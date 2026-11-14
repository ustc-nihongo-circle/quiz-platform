from django.urls import path
from django.views.generic import RedirectView

from . import participant_api_v2 as v2
from .api import (
    activity_detail,
    attempt_detail,
    attempt_item_image,
    attempt_submission,
    attempts_collection,
    current_attempt,
    participant_session,
)
from .participant_history import participant_history
from .views import health, participant_page, readiness

urlpatterns = [
    path("", RedirectView.as_view(pattern_name="participant-page", permanent=False)),
    path("health/", health, name="health"),
    path("health/live/", health, name="health-live"),
    path("health/ready/", readiness, name="health-ready"),
    path("participant/", participant_page, name="participant-page"),
    path("api/v1/activity", activity_detail, name="api-activity"),
    path("api/v1/participant-session", participant_session, name="api-participant-session"),
    path("api/v1/attempts", attempts_collection, name="api-attempts"),
    path("api/v1/attempts/current", current_attempt, name="api-current-attempt"),
    path("api/v1/attempts/history", participant_history, name="api-participant-history"),
    path("api/v1/attempts/<uuid:attempt_id>", attempt_detail, name="api-attempt-detail"),
    path(
        "api/v1/attempts/<uuid:attempt_id>/items/<uuid:item_id>/image",
        attempt_item_image,
        name="api-attempt-item-image",
    ),
    path(
        "api/v1/attempts/<uuid:attempt_id>/submission",
        attempt_submission,
        name="api-attempt-submission",
    ),
    path("api/v2/activity", v2.activity, name="api-v2-activity"),
    path("api/v2/participant-session", v2.session, name="api-v2-participant-session"),
    path("api/v2/attempts", v2.attempt_start, name="api-v2-attempt-start"),
    path("api/v2/attempts/history", v2.attempt_history, name="api-v2-attempt-history"),
    path("api/v2/attempts/<uuid:attempt_id>", v2.attempt_detail, name="api-v2-attempt-detail"),
    path(
        "api/v2/attempts/<uuid:attempt_id>/submission",
        v2.attempt_submission,
        name="api-v2-attempt-submission",
    ),
    path(
        "api/v2/attempts/<uuid:attempt_id>/items/<uuid:item_id>/image",
        v2.attempt_item_image,
        name="api-v2-attempt-item-image",
    ),
]
