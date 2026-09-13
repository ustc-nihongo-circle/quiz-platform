from decimal import Decimal, InvalidOperation

from django.contrib.auth.decorators import login_required, permission_required
from django.contrib.auth.views import LoginView, LogoutView
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db.models import Count, Max, Q
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from .identity import deidentify_participant
from .models import (
    ActivityCategoryConfig,
    ActivityEdition,
    ActivityStatus,
    Participant,
    QuestionBankVersion,
    QuizAttempt,
    RewardRule,
)
from .ops_history import history_enabled
from .ops_services import (
    identity_export_rows,
    leaderboard_rows,
    reveal_participant_identities,
    search_participants,
    statistics_export_rows,
)
from .services import (
    ActivityMustBePaused,
    activate_question_bank,
    create_reward_rule,
    deactivate_reward_rule,
    invalidate_attempt,
    select_participant_entry,
    transition_activity,
    update_reward_rule,
)


class OpsLoginView(LoginView):
    template_name = "quiz/ops/login.html"
    next_page = "/ops/"
    redirect_authenticated_user = True


class OpsLogoutView(LogoutView):
    next_page = "/ops/login/"


@never_cache
@login_required(login_url="/ops/login/")
@permission_required("quiz.operate_quiz", raise_exception=True)
def dashboard(request):
    activities = ActivityEdition.objects.order_by("-created_at")
    return render(
        request,
        "quiz/ops/dashboard.html",
        {"activities": activities, "history_enabled": history_enabled()},
    )


@never_cache
@login_required(login_url="/ops/login/")
@permission_required("quiz.operate_quiz", raise_exception=True)
@require_GET
def activity_console(request, activity_id):
    activity = get_object_or_404(ActivityEdition, pk=activity_id)
    participant_result = search_participants(activity=activity)
    categories = list(activity.category_configs.all())
    current_activation = (
        activity.bank_activations.filter(is_current=True).select_related("bank").first()
    )
    status_actions = {
        ActivityStatus.DRAFT: [(ActivityStatus.OPEN, "开放活动")],
        ActivityStatus.OPEN: [
            (ActivityStatus.PAUSED, "暂停活动"),
            (ActivityStatus.CLOSED, "关闭活动"),
        ],
        ActivityStatus.PAUSED: [
            (ActivityStatus.OPEN, "恢复开放"),
            (ActivityStatus.CLOSED, "关闭活动"),
        ],
        ActivityStatus.CLOSED: [(ActivityStatus.ARCHIVED, "封存活动")],
        ActivityStatus.ARCHIVED: [],
    }[activity.status]
    return render(
        request,
        "quiz/ops/activity.html",
        {
            "activity": activity,
            "activities": ActivityEdition.objects.order_by("-created_at"),
            "categories": categories,
            "current_activation": current_activation,
            "available_banks": QuestionBankVersion.objects.filter(
                finalized_at__isnull=False
            ).order_by("-imported_at"),
            "participants": participant_result["participants"],
            "participant_pagination": participant_result["pagination"],
            "recent_attempts": activity.attempts.select_related(
                "category_config", "participant__identity"
            ).order_by("-created_at", "pk")[:30],
            "reward_rules": activity.reward_rules.select_related("category_config").order_by(
                "-is_active", "-priority", "pk"
            ),
            "audit_logs": activity.audit_logs.select_related("actor").all()[:100],
            "leaderboard": (
                leaderboard_rows(
                    activity=activity,
                    category_key=categories[0].category_key,
                )
                if categories
                else []
            ),
            "status_actions": status_actions,
        },
    )


@never_cache
@login_required(login_url="/ops/login/")
@permission_required("quiz.operate_quiz", raise_exception=True)
@require_GET
def activity_snapshot(request, activity_id):
    activity = get_object_or_404(ActivityEdition, pk=activity_id)
    attempt_summary = activity.attempts.aggregate(
        in_progress=Count("pk", filter=Q(status="in_progress")),
        submitted=Count("pk", filter=Q(status="submitted")),
        timed_out=Count("pk", filter=Q(status="timed_out")),
        invalid=Count("pk", filter=Q(status="invalid")),
        last_submission_at=Max("submitted_at"),
    )
    last_submission_at = attempt_summary.pop("last_submission_at")
    response = JsonResponse(
        {
            "activity_id": str(activity.pk),
            "status": activity.status,
            "participants": activity.participants.count(),
            "attempts": attempt_summary,
            "last_submission_at": (
                last_submission_at.isoformat().replace("+00:00", "Z")
                if last_submission_at
                else None
            ),
        }
    )
    response["Cache-Control"] = "private, no-store"
    return response


@never_cache
@login_required(login_url="/ops/login/")
@permission_required("quiz.operate_quiz", raise_exception=True)
@require_POST
def participant_search(request, activity_id):
    activity = get_object_or_404(ActivityEdition, pk=activity_id)
    response = JsonResponse(
        search_participants(
            activity=activity,
            display_name=request.POST.get("display_name", ""),
            identifier=request.POST.get("identifier", ""),
            contact=request.POST.get("contact", ""),
            participant_id=request.POST.get("participant_id", ""),
            sort=request.POST.get("sort", "recent"),
            page=request.POST.get("page", "1"),
        )
    )
    response["Cache-Control"] = "private, no-store"
    return response


@never_cache
@login_required(login_url="/ops/login/")
@permission_required("quiz.operate_quiz", raise_exception=True)
@require_GET
def participant_detail(request, activity_id, participant_id):
    participant = get_object_or_404(
        Participant.objects.select_related("activity", "identity"),
        pk=participant_id,
        activity_id=activity_id,
    )
    identity = getattr(participant, "identity", None)
    attempts = (
        participant.attempts.filter(activity_id=activity_id)
        .select_related("category_config")
        .order_by("-started_at", "pk")
    )
    attempt_page = Paginator(attempts, 25).get_page(request.GET.get("page", "1"))
    # Only fetch submitted item values for this page, never answer keys or identity secrets.
    attempt_page.object_list = attempt_page.object_list.prefetch_related("items")
    return render(
        request,
        "quiz/ops/participant_detail.html",
        {
            "activity": participant.activity,
            "participant": participant,
            "display_name": identity.display_name if identity else "已去身份化",
            "attempt_page": attempt_page,
        },
    )


@never_cache
@login_required(login_url="/ops/login/")
@permission_required("quiz.operate_quiz", raise_exception=True)
@require_POST
def participant_reveal(request, activity_id):
    activity = get_object_or_404(ActivityEdition, pk=activity_id)
    response = JsonResponse(
        {
            "participants": reveal_participant_identities(
                activity=activity,
                participant_ids=request.POST.getlist("participant_ids"),
                fields=request.POST.getlist("fields"),
                actor=request.user,
            )
        }
    )
    response["Cache-Control"] = "private, no-store"
    return response


@never_cache
@login_required(login_url="/ops/login/")
@permission_required("quiz.operate_quiz", raise_exception=True)
@require_POST
def identity_export(request, activity_id):
    activity = get_object_or_404(ActivityEdition, pk=activity_id)
    try:
        rows = identity_export_rows(
            activity=activity,
            actor=request.user,
            reason=request.POST.get("reason", ""),
        )
    except ValidationError as error:
        return HttpResponse("; ".join(error.messages), status=400)
    response = StreamingHttpResponse(rows, content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = (
        f'attachment; filename="{activity.slug}-participant-identities.csv"'
    )
    response["Cache-Control"] = "private, no-store"
    return response


@never_cache
@login_required(login_url="/ops/login/")
@permission_required("quiz.operate_quiz", raise_exception=True)
@require_POST
def statistics_export(request, activity_id):
    activity = get_object_or_404(ActivityEdition, pk=activity_id)
    response = StreamingHttpResponse(
        statistics_export_rows(activity=activity, actor=request.user),
        content_type="text/csv; charset=utf-8",
    )
    response["Content-Disposition"] = (
        f'attachment; filename="{activity.slug}-attempt-statistics.csv"'
    )
    response["Cache-Control"] = "private, no-store"
    return response


@never_cache
@login_required(login_url="/ops/login/")
@permission_required("quiz.operate_quiz", raise_exception=True)
@require_POST
def participant_entry_select(request, activity_id):
    activity = get_object_or_404(ActivityEdition, pk=activity_id)
    try:
        selected = select_participant_entry(
            activity=activity,
            actor=request.user,
            reason=request.POST.get("reason", ""),
        )
    except ValidationError as error:
        return JsonResponse({"errors": error.messages}, status=400)
    return JsonResponse(
        {
            "activity_id": str(selected.pk),
            "is_participant_entry": selected.is_participant_entry,
        }
    )


@never_cache
@login_required(login_url="/ops/login/")
@permission_required("quiz.operate_quiz", raise_exception=True)
@require_POST
def activity_status_transition(request, activity_id):
    activity = get_object_or_404(ActivityEdition, pk=activity_id)
    next_status = request.POST.get("status", "")
    if next_status not in ActivityStatus.values:
        return JsonResponse({"errors": ["未知的活动状态。"]}, status=400)
    try:
        activity = transition_activity(
            activity=activity,
            next_status=next_status,
            actor=request.user,
            reason=request.POST.get("reason", ""),
        )
    except ValidationError as error:
        return JsonResponse({"errors": error.messages}, status=400)
    return JsonResponse({"activity_id": str(activity.pk), "status": activity.status})


@never_cache
@login_required(login_url="/ops/login/")
@permission_required("quiz.operate_quiz", raise_exception=True)
@require_POST
def question_bank_switch(request, activity_id):
    activity = get_object_or_404(ActivityEdition, pk=activity_id)
    bank = get_object_or_404(
        QuestionBankVersion,
        version_code=request.POST.get("bank_version", ""),
        finalized_at__isnull=False,
    )
    legacy_exception = request.POST.get("legacy_exception") in {"1", "true", "on"}
    try:
        activation = activate_question_bank(
            activity=activity,
            bank=bank,
            actor=request.user,
            reason=request.POST.get("reason", ""),
            legacy_exception=legacy_exception,
        )
    except (ActivityMustBePaused, ValidationError) as error:
        messages = error.messages if isinstance(error, ValidationError) else [str(error)]
        return JsonResponse({"errors": messages}, status=400)
    return JsonResponse(
        {
            "activity_id": str(activity.pk),
            "bank_version": activation.bank.version_code,
            "legacy_exception": activation.legacy_exception,
        }
    )


@never_cache
@login_required(login_url="/ops/login/")
@permission_required("quiz.operate_quiz", raise_exception=True)
@require_POST
def attempt_invalidate(request, attempt_id):
    attempt = get_object_or_404(QuizAttempt, pk=attempt_id)
    try:
        attempt = invalidate_attempt(
            attempt=attempt,
            actor=request.user,
            reason=request.POST.get("reason", ""),
        )
    except ValidationError as error:
        return JsonResponse({"errors": error.messages}, status=400)
    return JsonResponse({"attempt_id": str(attempt.pk), "status": attempt.status})


@never_cache
@login_required(login_url="/ops/login/")
@permission_required("quiz.operate_quiz", raise_exception=True)
@require_GET
def attempt_detail(request, attempt_id):
    attempt = get_object_or_404(
        QuizAttempt.objects.select_related("category_config"),
        pk=attempt_id,
    )
    response = JsonResponse(
        {
            "attempt": {
                "id": str(attempt.pk),
                "participant_id": str(attempt.participant_id),
                "category": attempt.category_config.category_key,
                "status": attempt.status,
                "score": attempt.score,
                "question_count": attempt.question_count,
                "started_at": attempt.started_at.isoformat().replace("+00:00", "Z"),
                "deadline_at": attempt.deadline_at.isoformat().replace("+00:00", "Z"),
                "submitted_at": (
                    attempt.submitted_at.isoformat().replace("+00:00", "Z")
                    if attempt.submitted_at
                    else None
                ),
                "items": [
                    {
                        "id": str(item.pk),
                        "position": item.display_order,
                        "answer": item.submitted_answer,
                        "correct": item.is_correct,
                    }
                    for item in attempt.items.all()
                ],
            }
        }
    )
    response["Cache-Control"] = "private, no-store"
    return response


@never_cache
@login_required(login_url="/ops/login/")
@permission_required("quiz.operate_quiz", raise_exception=True)
@require_POST
def participant_deidentify(request, participant_id):
    participant = get_object_or_404(Participant, pk=participant_id)
    try:
        deidentified = deidentify_participant(
            participant=participant,
            actor=request.user,
            reason=request.POST.get("reason", ""),
        )
    except ValidationError as error:
        return JsonResponse({"errors": error.messages}, status=400)
    return JsonResponse({"participant_id": str(participant.pk), "deidentified": deidentified})


@never_cache
@login_required(login_url="/ops/login/")
@permission_required("quiz.operate_quiz", raise_exception=True)
@require_POST
def reward_rule_create(request, activity_id):
    activity = get_object_or_404(ActivityEdition, pk=activity_id)
    category_key = request.POST.get("category_key", "").strip()
    category = None
    if category_key:
        category = get_object_or_404(
            ActivityCategoryConfig,
            activity=activity,
            category_key=category_key,
        )
    try:
        rule = create_reward_rule(
            activity=activity,
            category=category,
            min_score_rate=Decimal(request.POST.get("min_score_rate", "")),
            max_score_rate=Decimal(request.POST.get("max_score_rate", "")),
            priority=int(request.POST.get("priority", "")),
            text=request.POST.get("text", ""),
            actor=request.user,
            reason=request.POST.get("reason", ""),
        )
    except (InvalidOperation, ValueError):
        return JsonResponse({"errors": ["分数区间或优先级格式无效。"]}, status=400)
    except ValidationError as error:
        messages = error.messages if hasattr(error, "messages") else [str(error)]
        return JsonResponse({"errors": messages}, status=400)
    return JsonResponse(
        {"reward_rule_id": rule.pk, "active": rule.is_active},
        status=201,
    )


@never_cache
@login_required(login_url="/ops/login/")
@permission_required("quiz.operate_quiz", raise_exception=True)
@require_POST
def reward_rule_update(request, rule_id):
    rule = get_object_or_404(RewardRule, pk=rule_id)
    action = request.POST.get("action")
    if action == "deactivate":
        try:
            rule = deactivate_reward_rule(
                rule=rule,
                actor=request.user,
                reason=request.POST.get("reason", ""),
            )
        except ValidationError as error:
            return JsonResponse({"errors": error.messages}, status=400)
        return JsonResponse({"reward_rule_id": rule.pk, "active": rule.is_active})
    if action != "update":
        return JsonResponse({"errors": ["未知的兑奖词操作。"]}, status=400)
    category = rule.category_config
    if "category_key" in request.POST:
        category_key = request.POST.get("category_key", "").strip()
        category = (
            get_object_or_404(
                ActivityCategoryConfig,
                activity=rule.activity,
                category_key=category_key,
            )
            if category_key
            else None
        )
    try:
        rule = update_reward_rule(
            rule=rule,
            category=category,
            min_score_rate=Decimal(request.POST.get("min_score_rate", "")),
            max_score_rate=Decimal(request.POST.get("max_score_rate", "")),
            priority=int(request.POST.get("priority", "")),
            text=request.POST.get("text", ""),
            actor=request.user,
            reason=request.POST.get("reason", ""),
        )
    except (InvalidOperation, ValueError):
        return JsonResponse({"errors": ["分数区间或优先级格式无效。"]}, status=400)
    except ValidationError as error:
        return JsonResponse({"errors": error.messages}, status=400)
    return JsonResponse({"reward_rule_id": rule.pk, "active": rule.is_active})


@never_cache
@login_required(login_url="/ops/login/")
@permission_required("quiz.operate_quiz", raise_exception=True)
@require_GET
def leaderboard(request, activity_id):
    activity = get_object_or_404(ActivityEdition, pk=activity_id)
    category_key = request.GET.get("category", "").strip()
    get_object_or_404(
        ActivityCategoryConfig,
        activity=activity,
        category_key=category_key,
    )
    response = JsonResponse(
        {
            "category": category_key,
            "rows": leaderboard_rows(activity=activity, category_key=category_key),
        }
    )
    response["Cache-Control"] = "private, no-store"
    return response
