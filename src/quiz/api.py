import json
from collections.abc import Mapping

from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.csrf import csrf_failure as default_csrf_failure
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods

from .identity import ParticipantRecoveryRequired, register_or_resume
from .models import ActivityEdition, ActivityStatus, Participant, QuizAttempt
from .services import (
    ActivityNotOpen,
    AttemptExpired,
    AttemptInvalidated,
    QuestionBankUnavailable,
    refresh_attempt_timeout,
    serialize_attempt,
    start_attempt,
    submit_attempt,
)


def api_error(
    code: str,
    message: str,
    *,
    status: int,
    field_errors: Mapping[str, object] | None = None,
    retryable: bool = False,
) -> JsonResponse:
    return JsonResponse(
        {
            "error": {
                "code": code,
                "message": message,
                "field_errors": dict(field_errors or {}),
                "retryable": retryable,
            }
        },
        status=status,
    )


def csrf_failure(request: HttpRequest, reason=""):
    if request.path.startswith("/api/"):
        return api_error(
            "csrf_failed",
            "请求来源校验失败，请刷新页面后重试。",
            status=403,
            retryable=True,
        )
    return default_csrf_failure(request, reason=reason)


def current_activity() -> ActivityEdition | None:
    return (
        ActivityEdition.objects.exclude(status=ActivityStatus.ARCHIVED)
        .order_by("-updated_at", "-created_at")
        .first()
    )


def parse_json_object(request: HttpRequest) -> dict[str, object]:
    try:
        value = json.loads(request.body or b"{}")
    except (TypeError, ValueError, UnicodeDecodeError) as error:
        raise ValidationError("请求正文必须是 JSON 对象。") from error
    if not isinstance(value, dict):
        raise ValidationError("请求正文必须是 JSON 对象。")
    return value


def validation_error_response(error: ValidationError) -> JsonResponse:
    if hasattr(error, "message_dict"):
        fields = error.message_dict
        message = "请检查提交字段。"
    else:
        fields = {}
        message = "; ".join(error.messages)
    return api_error("validation_error", message, status=400, field_errors=fields)


def session_participant(request: HttpRequest) -> Participant | None:
    participant_id = request.session.get("quiz_participant_id")
    activity_id = request.session.get("quiz_activity_id")
    if not participant_id or not activity_id:
        return None
    return Participant.objects.filter(pk=participant_id, activity_id=activity_id).first()


@require_GET
@ensure_csrf_cookie
def activity_detail(request: HttpRequest) -> JsonResponse:
    activity = current_activity()
    if activity is None:
        return api_error("activity_unavailable", "当前没有可用活动。", status=404)
    categories = [
        {
            "code": category.category_key,
            "title": category.title,
            "question_count": category.effective_question_count,
            "time_limit_seconds": category.effective_time_limit_seconds,
        }
        for category in activity.category_configs.all()
    ]
    return JsonResponse(
        {
            "activity": {
                "code": activity.slug,
                "title": activity.title,
                "status": activity.status,
                "categories": categories,
            }
        }
    )


@require_http_methods(["POST", "DELETE"])
def participant_session(request: HttpRequest) -> JsonResponse:
    if request.method == "DELETE":
        request.session.pop("quiz_participant_id", None)
        request.session.pop("quiz_activity_id", None)
        return HttpResponse(status=204)

    activity = current_activity()
    if activity is None or activity.status not in {ActivityStatus.OPEN, ActivityStatus.PAUSED}:
        return api_error("activity_unavailable", "当前活动不接受参与者进入。", status=409)
    try:
        payload = parse_json_object(request)
        result = register_or_resume(
            activity=activity,
            display_name=str(payload.get("display_name", "")),
            identifier=str(payload.get("identifier", "")),
            contact=str(payload.get("contact", "")),
        )
    except ParticipantRecoveryRequired:
        return api_error(
            "participant_recovery_required",
            "登记信息无法匹配，请联系活动管理员处理。",
            status=409,
        )
    except ValidationError as error:
        return validation_error_response(error)

    request.session.cycle_key()
    request.session["quiz_participant_id"] = str(result.participant.pk)
    request.session["quiz_activity_id"] = str(activity.pk)
    return JsonResponse(
        {
            "participant": {
                "id": str(result.participant.pk),
                "display_name": result.participant.identity.display_name,
            },
            "created": result.created,
        },
        status=201 if result.created else 200,
    )


@require_http_methods(["POST"])
def attempts_collection(request: HttpRequest) -> JsonResponse:
    participant = session_participant(request)
    if participant is None:
        return api_error("participant_session_required", "请先登记或再次进入。", status=401)
    try:
        payload = parse_json_object(request)
        result = start_attempt(
            participant=participant,
            category_code=str(payload.get("category_code", "")),
        )
    except ActivityNotOpen:
        return api_error("activity_not_open", "当前活动不接受新答题。", status=409)
    except QuestionBankUnavailable:
        return api_error(
            "question_bank_unavailable",
            "该板块暂时无法开始，请联系活动管理员。",
            status=409,
            retryable=True,
        )
    return JsonResponse(
        {"attempt": serialize_attempt(result.attempt)},
        status=201 if result.created else 200,
    )


@require_GET
def current_attempt(request: HttpRequest) -> JsonResponse:
    participant = session_participant(request)
    if participant is None:
        return api_error("participant_session_required", "请先登记或再次进入。", status=401)
    attempt = (
        QuizAttempt.objects.filter(participant=participant, status="in_progress")
        .order_by("-created_at")
        .first()
    )
    if attempt is None:
        return api_error("attempt_not_found", "当前没有进行中的答题。", status=404)
    attempt = refresh_attempt_timeout(attempt)
    return JsonResponse({"attempt": serialize_attempt(attempt)})


@require_GET
def attempt_detail(request: HttpRequest, attempt_id) -> JsonResponse:
    participant = session_participant(request)
    attempt = QuizAttempt.objects.filter(pk=attempt_id, participant=participant).first()
    if participant is None or attempt is None:
        return api_error("attempt_not_found", "未找到答题记录。", status=404)
    attempt = refresh_attempt_timeout(attempt)
    return JsonResponse({"attempt": serialize_attempt(attempt)})


@require_http_methods(["PUT"])
def attempt_submission(request: HttpRequest, attempt_id) -> JsonResponse:
    participant = session_participant(request)
    attempt = QuizAttempt.objects.filter(pk=attempt_id, participant=participant).first()
    if participant is None or attempt is None:
        return api_error("attempt_not_found", "未找到答题记录。", status=404)
    try:
        payload = parse_json_object(request)
        raw_answers = payload.get("answers", [])
        if not isinstance(raw_answers, list):
            raise ValidationError({"answers": "answers 必须是数组。"})
        answers = {}
        for answer in raw_answers:
            if not isinstance(answer, dict) or "item_id" not in answer:
                raise ValidationError({"answers": "每项答案必须包含 item_id。"})
            item_id = str(answer["item_id"])
            if item_id in answers:
                raise ValidationError({"answers": "同一题不能提交两次。"})
            answers[item_id] = answer.get("answer")
        attempt = submit_attempt(
            attempt=attempt,
            participant=participant,
            answers=answers,
        )
    except ValidationError as error:
        return validation_error_response(error)
    except AttemptExpired:
        return api_error("attempt_expired", "本次答题已经超时。", status=409)
    except AttemptInvalidated:
        return api_error("attempt_invalidated", "本次答题已被管理员作废。", status=409)
    return JsonResponse({"attempt": serialize_attempt(attempt)})
