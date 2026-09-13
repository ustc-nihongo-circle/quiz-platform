from django.db import DatabaseError, connection
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import ensure_csrf_cookie

from .models import AttemptStatus, Participant, QuizAttempt
from .services import refresh_attempt_timeout, serialize_attempt


def health(request):
    """Return a process-level health response without exposing configuration."""
    return JsonResponse({"status": "ok"})


def readiness(request):
    """Return readiness only after a database round trip succeeds."""
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except DatabaseError:
        return JsonResponse({"status": "unavailable", "database": "error"}, status=503)
    return JsonResponse({"status": "ok", "database": "ok"})


@never_cache
@ensure_csrf_cookie
def participant_page(request):
    """Render the same-origin participant application shell."""
    participant_id = request.session.get("quiz_participant_id")
    activity_id = request.session.get("quiz_activity_id")
    participant = None
    if participant_id and activity_id:
        participant = (
            Participant.objects.select_related("identity")
            .filter(pk=participant_id, activity_id=activity_id)
            .first()
        )
    initial_participant = None
    initial_attempt = None
    if participant is not None:
        identity = getattr(participant, "identity", None)
        initial_participant = {
            "id": str(participant.pk),
            "display_name": identity.display_name if identity is not None else "参与者",
        }
        attempt = (
            QuizAttempt.objects.filter(participant=participant)
            .order_by("-created_at")
            .first()
        )
        if attempt is not None:
            if attempt.status == AttemptStatus.IN_PROGRESS:
                attempt = refresh_attempt_timeout(attempt)
            initial_attempt = serialize_attempt(attempt)
    return render(
        request,
        "quiz/participant.html",
        {
            "initial_attempt": initial_attempt,
            "initial_participant": initial_participant,
        },
    )
