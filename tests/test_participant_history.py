import json
from datetime import timedelta

import pytest
from django.utils import timezone
from test_attempt_api import create_open_quiz

from quiz.models import ActivityEdition, Participant, QuizAttempt
from quiz.services import serialize_attempt, start_attempt, submit_attempt

pytestmark = pytest.mark.django_db
URL = "/api/v1/attempts/history"


@pytest.fixture
def history(client):
    activity = create_open_quiz()
    person = Participant.objects.create(activity=activity)
    session = client.session
    session["quiz_participant_id"] = str(person.pk)
    session["quiz_activity_id"] = str(activity.pk)
    session.save()
    return activity, person


def record(history, *, status="submitted", minutes=0):
    activity, person = history
    start = timezone.now() - timedelta(minutes=minutes)
    return QuizAttempt.objects.create(
        activity=activity, participant=person,
        category_config=activity.category_configs.first(),
        bank=activity.bank_activations.get(is_current=True).bank,
        status=status, started_at=start, deadline_at=start + timedelta(minutes=5),
        submitted_at=start + timedelta(seconds=20) if status == "submitted" else None,
        question_count=2, score=1,
    )


def test_history_requires_session_and_never_caches(client):
    response = client.get(URL)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "participant_session_required"
    assert "no-store" in response.headers["Cache-Control"]


def test_history_scopes_to_session_even_with_foreign_parameters(client, history):
    own = record(history)
    other_person = Participant.objects.create(activity=history[0])
    foreign = record((history[0], other_person))
    response = client.get(URL, {"participant_id": str(other_person.pk)})
    assert response.status_code == 200
    assert [item["id"] for item in response.json()["attempts"]] == [str(own.pk)]
    assert client.get(f"/api/v1/attempts/{foreign.pk}").status_code == 404
    assert "private" in response.headers["Cache-Control"]
    assert "no-store" in response.headers["Cache-Control"]


def test_history_rejects_mismatched_activity_session(client, history):
    record(history)
    unrelated = ActivityEdition.objects.create(slug="other", title="Other")
    session = client.session
    session["quiz_activity_id"] = str(unrelated.pk)
    session.save()
    assert client.get(URL).status_code == 401


def test_history_empty_and_pagination_cover_all_records(client, history):
    empty = client.get(URL).json()
    assert empty["attempts"] == []
    assert empty["pagination"] == {"page": 1, "pages": 1, "total": 0, "page_size": 20}
    attempts = [record(history, minutes=i) for i in range(25)]
    first = client.get(URL).json()
    second = client.get(URL, {"page": 2}).json()
    assert len(first["attempts"]) == 20
    assert len(second["attempts"]) == 5
    assert [a["id"] for a in first["attempts"] + second["attempts"]] == [
        str(a.pk) for a in attempts
    ]
    assert client.get(URL, {"page": 999}).json()["pagination"]["page"] == 2


@pytest.mark.parametrize("page", ["0", "-1", "a", "1.2", "١", "1" * 100])
def test_history_rejects_invalid_pages(client, history, page):
    assert client.get(URL, {"page": page}).status_code == 400


def test_history_omits_answers_identity_and_invalid_scores(client, history):
    records = [record(history, status=status, minutes=i) for i, status in enumerate(
        ["submitted", "timed_out", "invalid", "in_progress"]
    )]
    rows = client.get(URL).json()["attempts"]
    assert [row["status"] for row in rows] == [attempt.status for attempt in records]
    assert rows[0]["score"] == 1
    assert all(row["score"] is None for row in rows[1:])
    allowed = {"id", "category", "status", "started_at", "deadline_at", "submitted_at",
               "score", "question_count"}
    assert all(set(row) == allowed for row in rows)
    assert QuizAttempt.objects.count() == 4


def test_history_uses_existing_expiry_without_changing_deadline(client, history):
    old = record(history, status="in_progress", minutes=31)
    response = client.get(URL)
    old.refresh_from_db()
    assert response.json()["attempts"][0]["status"] == old.status == "timed_out"
    assert response.json()["current_attempt_id"] is None
    assert old.deadline_at == old.started_at + timedelta(minutes=5)
    assert QuizAttempt.objects.count() == 1


def test_result_timestamp_addition_preserves_submission_idempotency(client, history):
    attempt = start_attempt(participant=history[1], category_code="language").attempt
    submitted = submit_attempt(attempt=attempt, participant=history[1], answers={})
    first = serialize_attempt(submitted)
    again = submit_attempt(attempt=attempt, participant=history[1], answers={})
    assert serialize_attempt(again) == first
    assert first["submitted_at"] == submitted.submitted_at.isoformat()
    assert all("correct_option" not in item for item in first["questions"])
    assert client.get(URL).json()["attempts"][0]["submitted_at"] == first["submitted_at"]


@pytest.mark.parametrize("status", ["open", "paused", "closed"])
def test_reentering_recovers_own_history_without_overwriting_name(client, status):
    activity = create_open_quiz()
    payload = {"display_name": "合成记录", "identifier": "HISTORY-RECOVERY",
               "contact": "history@example.invalid"}
    registered = client.post("/api/v1/participant-session", json.dumps(payload),
                             content_type="application/json")
    person = Participant.objects.get(pk=registered.json()["participant"]["id"])
    existing = record((activity, person))
    assert client.delete("/api/v1/participant-session").status_code == 204
    assert client.get(URL).status_code == 401
    activity.status = status
    activity.save(update_fields=["status"])
    payload["display_name"] = "另一个输入名"
    resumed = client.post("/api/v1/participant-session", json.dumps(payload),
                          content_type="application/json")
    assert resumed.status_code == 200
    assert resumed.json()["participant"]["display_name"] == "合成记录"
    assert client.get(URL).json()["attempts"][0]["id"] == str(existing.pk)
    assert Participant.objects.count() == QuizAttempt.objects.count() == 1
