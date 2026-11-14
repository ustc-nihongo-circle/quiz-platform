import re
from functools import wraps

from django.core.exceptions import RequestDataTooBig, ValidationError
from django.http import Http404, JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods

from .api import (
    activity_detail,
    api_error,
    parse_json_object,
    participant_session,
    question_image_response,
    rate_limit_error,
    session_participant,
    validation_error_response,
)
from .models import AttemptItem, AttemptStatus, QuizAttempt
from .participant_history import history_response, refresh_current_attempt
from .rate_limits import RateLimited, RateLimitUnavailable
from .services import (
    ActivityNotOpen,
    AttemptExpired,
    AttemptInvalidated,
    ParticipantSessionRevoked,
    QuestionBankUnavailable,
    refresh_attempt_timeout,
    serialize_attempt,
    start_attempt,
    submit_attempt,
)

SUBMISSION_BODY_LIMIT = 1024 * 1024
ITEM_ID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")


def private_response(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        response = view(request, *args, **kwargs)
        response["Cache-Control"] = "private, no-store"
        return response

    return wrapped


def session_required():
    return api_error("participant_session_required", "请先登记或再次进入。", status=401)


def attempt_not_found():
    return api_error("attempt_not_found", "未找到答题记录。", status=404)


def participant_attempts(participant):
    return QuizAttempt.objects.filter(participant=participant, activity_id=participant.activity_id)


def attempt_reference(attempt):
    return {"id": str(attempt.pk), "status": attempt.status} if attempt is not None else None


@private_response
def activity(request):
    return activity_detail(request)


@private_response
@require_http_methods(["GET", "POST", "DELETE"])
def session(request):
    if request.method == "GET":
        return session_snapshot(request)
    return participant_session(request)


@ensure_csrf_cookie
def session_snapshot(request):
    participant = session_participant(request, require_identity=True)
    now = timezone.now()
    session = None
    if participant is not None:
        attempts = participant_attempts(participant)
        current = refresh_current_attempt(attempts, now=now)
        latest = attempts.order_by("-started_at", "-pk").first()
        session = {
            "participant": {
                "id": str(participant.pk),
                "display_name": participant.identity.display_name,
            },
            "activity_code": participant.activity.slug,
            "current_attempt": attempt_reference(current),
            "latest_attempt": attempt_reference(latest),
        }
    return JsonResponse({"server_time": now.isoformat(), "session": session})


@private_response
@require_http_methods(["POST"])
def attempt_start(request):
    participant = session_participant(request, require_identity=True)
    if participant is None:
        return session_required()
    if request.content_type != "application/json":
        return api_error("unsupported_media_type", "请使用 JSON 开卷。", status=415)
    try:
        payload = parse_json_object(request, max_bytes=SUBMISSION_BODY_LIMIT)
        category = payload.get("category_code")
        if (
            set(payload) != {"category_code"} or not isinstance(category, str)
            or not category or "\x00" in category
            or any("\ud800" <= char <= "\udfff" for char in category)
        ):
            raise ValidationError({"category_code": "请填写板块代码。"})
        result = start_attempt(
            participant=participant, category_code=category, require_entry=True
        )
    except RequestDataTooBig:
        return api_error("request_too_large", "请求超过大小限制。", status=413)
    except ValidationError as error:
        return validation_error_response(error)
    except ParticipantSessionRevoked:
        return session_required()
    except (RateLimited, RateLimitUnavailable) as error:
        return rate_limit_error(error)
    except ActivityNotOpen:
        return api_error("activity_not_open", "当前活动不接受新答题。", status=409)
    except QuestionBankUnavailable:
        return api_error(
            "question_bank_unavailable", "该板块暂时无法开始，请联系活动管理员。",
            status=409, retryable=True,
        )
    return JsonResponse(
        {"attempt": serialize_attempt(result.attempt, api_version=2)},
        status=201 if result.created else 200,
    )


@private_response
@require_GET
def attempt_history(request):
    participant = session_participant(request, require_identity=True)
    if participant is None:
        return session_required()
    return history_response(request, participant_attempts(participant))


@private_response
@require_GET
def attempt_detail(request, attempt_id):
    participant = session_participant(request, require_identity=True)
    if participant is None:
        return session_required()
    attempt = participant_attempts(participant).select_related("category_config").filter(
        pk=attempt_id
    ).first()
    if attempt is None:
        return attempt_not_found()
    attempt = refresh_attempt_timeout(attempt)
    return JsonResponse({"attempt": serialize_attempt(attempt, api_version=2)})


@private_response
@require_GET
def attempt_item_image(request, attempt_id, item_id):
    participant = session_participant(request, require_identity=True)
    if participant is None:
        return attempt_not_found()
    attempts = participant_attempts(participant).filter(
        pk=attempt_id, status__in=[AttemptStatus.IN_PROGRESS, AttemptStatus.SUBMITTED]
    )
    attempt = attempts.first()
    if attempt is None or refresh_attempt_timeout(attempt).status not in {
        AttemptStatus.IN_PROGRESS, AttemptStatus.SUBMITTED
    }:
        return attempt_not_found()
    try:
        return question_image_response(AttemptItem.objects.filter(pk=item_id, attempt__in=attempts))
    except Http404:
        return attempt_not_found()


def parse_answers(request):
    payload = parse_json_object(request, max_bytes=SUBMISSION_BODY_LIMIT)
    if set(payload) != {"answers"} or not isinstance(payload["answers"], list):
        raise ValidationError({"answers": "请求必须包含 answers 数组。"})
    answers = {}
    for entry in payload["answers"]:
        if not isinstance(entry, dict) or set(entry) != {"item_id", "answer"}:
            raise ValidationError({"answers": "每项答案必须包含 item_id 和 answer。"})
        item_id = entry["item_id"]
        if not isinstance(item_id, str) or not ITEM_ID.fullmatch(item_id):
            raise ValidationError({"answers": "item_id 必须是小写 UUID。"})
        if item_id in answers:
            raise ValidationError({"answers": "同一题不能提交两次。"})
        value = entry["answer"]
        if value is not None and (
            not isinstance(value, str) or len(value) > 500 or "\x00" in value
            or any("\ud800" <= char <= "\udfff" for char in value)
        ):
            raise ValidationError({item_id: "答案必须为空或为 500 字以内的有效文本。"})
        answers[item_id] = value
    return answers


@private_response
@require_http_methods(["PUT"])
def attempt_submission(request, attempt_id):
    participant = session_participant(request, require_identity=True)
    if participant is None:
        return session_required()
    attempt = participant_attempts(participant).filter(pk=attempt_id).first()
    if attempt is None:
        return attempt_not_found()
    if request.content_type != "application/json":
        return api_error("unsupported_media_type", "请使用 JSON 交卷。", status=415)
    try:
        answers = parse_answers(request)
        attempt = submit_attempt(attempt=attempt, participant=participant, answers=answers)
    except RequestDataTooBig:
        return api_error("request_too_large", "请求超过大小限制。", status=413)
    except ValidationError as error:
        return validation_error_response(error)
    except ParticipantSessionRevoked:
        return session_required()
    except AttemptExpired as error:
        return api_error(error.code, "本次答题已经超时。", status=409)
    except AttemptInvalidated as error:
        return api_error(error.code, "本次答题已被管理员作废。", status=409)
    return JsonResponse({"attempt": serialize_attempt(attempt, api_version=2)})
