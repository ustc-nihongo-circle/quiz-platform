from django.db import DatabaseError, connection
from django.http import JsonResponse
from django.shortcuts import render
from django.views.decorators.cache import never_cache


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
def participant_page(request):
    """Render the participant shell. The browser restores state through the API."""
    return render(request, "quiz/participant.html")
