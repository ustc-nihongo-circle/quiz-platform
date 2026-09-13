from functools import wraps
from unicodedata import normalize

from django.conf import settings
from django.contrib.auth.decorators import login_required, permission_required
from django.core.paginator import Paginator
from django.db import DatabaseError
from django.db.models import Count, Max, Q
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, render
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_http_methods

from .models import ActivityEdition, Participant, QuizAttempt
from .ops_services import PARTICIPANT_SORTS


def history_enabled():
    return "history" in settings.DATABASES


def history_objects(model):
    return model.objects.using("history")


def history_access(view):
    @never_cache
    @login_required(login_url="/ops/login/")
    @permission_required("quiz.operate_quiz", raise_exception=True)
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        if not history_enabled():
            raise Http404
        try:
            return view(request, *args, **kwargs)
        except DatabaseError:
            return render(request, "quiz/ops/history_unavailable.html", status=503)

    return wrapped


@history_access
@require_GET
def history_index(request):
    activities = (
        history_objects(ActivityEdition)
        .annotate(
            participant_count=Count("participants", distinct=True),
            attempt_count=Count("attempts", distinct=True),
        )
        .filter(attempt_count__gt=0)
        .order_by("-created_at")
    )
    return render(request, "quiz/ops/history_index.html", {"activities": activities})


@history_access
@require_http_methods(["GET", "POST"])
def history_activity(request, activity_id):
    activity = get_object_or_404(history_objects(ActivityEdition), pk=activity_id)
    # Names remain in a CSRF-protected POST body, never in URLs or access logs.
    query = request.POST.get("display_name", "") if request.method == "POST" else ""
    if len(query) > 400:
        return HttpResponseBadRequest("名称搜索内容过长。")
    normalized_query = normalize("NFKC", query).strip()
    sort = request.POST.get("sort", "recent") if request.method == "POST" else "recent"
    sort = sort if sort in PARTICIPANT_SORTS else "recent"
    participants = (
        history_objects(Participant)
        .filter(activity_id=activity_id)
        .select_related("identity")
        .only(
            "id", "activity_id", "created_at", "identity__participant_id", "identity__display_name"
        )
        .annotate(
            attempt_count=Count("attempts", filter=Q(attempts__activity_id=activity_id)),
            last_attempt_at=Max(
                "attempts__started_at", filter=Q(attempts__activity_id=activity_id)
            ),
        )
        .order_by(*PARTICIPANT_SORTS[sort])
    )
    if normalized_query:
        participants = participants.filter(identity__display_name__icontains=normalized_query)
    page = Paginator(participants, 50).get_page(request.POST.get("page", "1"))
    return render(
        request,
        "quiz/ops/history_activity.html",
        {"activity": activity, "participant_page": page, "query": query, "sort": sort},
    )


@history_access
@require_GET
def history_participant(request, activity_id, participant_id):
    participant = get_object_or_404(
        history_objects(Participant)
        .select_related("activity", "identity")
        .only("id", "activity", "identity__participant_id", "identity__display_name"),
        pk=participant_id,
        activity_id=activity_id,
    )
    identity = getattr(participant, "identity", None)
    attempts = (
        history_objects(QuizAttempt)
        .filter(participant_id=participant_id, activity_id=activity_id)
        .select_related("category_config")
        .order_by("-started_at", "pk")
    )
    page = Paginator(attempts, 25).get_page(request.GET.get("page", "1"))
    page.object_list = page.object_list.prefetch_related("items")
    return render(
        request,
        "quiz/ops/participant_detail.html",
        {
            "activity": participant.activity,
            "participant": participant,
            "display_name": identity.display_name if identity else "已去身份化",
            "attempt_page": page,
            "history_mode": True,
        },
    )
