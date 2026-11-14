from django.core.paginator import Paginator
from django.http import JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET

from .api import api_error, session_participant
from .models import AttemptStatus, QuizAttempt
from .services import refresh_attempt_timeout, serialize_attempt_summary


def refresh_current_attempt(attempts, *, now=None):
    current = attempts.filter(status=AttemptStatus.IN_PROGRESS).first()
    if current is None:
        return None
    current = refresh_attempt_timeout(current, now=now)
    return current if current.status == AttemptStatus.IN_PROGRESS else None


@never_cache
@require_GET
def participant_history(request):
    participant = session_participant(request)
    if participant is None:
        return api_error("participant_session_required", "请先登记或再次进入。", status=401)
    attempts = QuizAttempt.objects.filter(
        participant=participant, activity_id=participant.activity_id
    )
    return history_response(request, attempts)


def history_response(request, attempts):
    raw_page = request.GET.get("page", "1")
    if not raw_page.isascii() or not raw_page.isdecimal() or len(raw_page) > 8:
        return api_error("validation_error", "页码必须是正整数。", status=400)
    page_number = int(raw_page)
    if page_number < 1:
        return api_error("validation_error", "页码必须是正整数。", status=400)

    # Reuse the existing expiry rule; reading history never starts a new attempt.
    current = refresh_current_attempt(attempts)
    page = Paginator(
        attempts.select_related("category_config").order_by("-started_at", "-pk"), 20
    ).get_page(page_number)
    return JsonResponse({
        "current_attempt_id": str(current.pk) if current is not None else None,
        "attempts": [
            {
                **serialize_attempt_summary(attempt),
                "score": attempt.score if attempt.status == AttemptStatus.SUBMITTED else None,
            }
            for attempt in page
        ],
        "pagination": {
            "page": page.number, "pages": page.paginator.num_pages,
            "total": page.paginator.count, "page_size": 20,
        },
    })
