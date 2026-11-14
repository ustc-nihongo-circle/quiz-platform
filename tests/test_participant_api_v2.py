import json
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from django.test import Client
from django.utils import timezone
from test_attempt_api import create_open_quiz, register

from quiz.identity import deidentify_participant
from quiz.models import ActivityEdition, ActivityStatus, AttemptStatus, Participant, QuizAttempt
from quiz.services import invalidate_attempt, start_attempt

pytestmark = pytest.mark.django_db
SESSION_URL = "/api/v2/participant-session"
HISTORY_URL = "/api/v2/attempts/history"


@pytest.fixture
def quiz(client):
    activity = create_open_quiz()
    assert register(client).status_code == 201
    participant = Participant.objects.get(activity=activity)
    return activity, participant


@pytest.fixture
def attempt(quiz):
    return start_attempt(participant=quiz[1], category_code="language").attempt


def detail_url(attempt):
    return f"/api/v2/attempts/{attempt.pk}"


def assert_error(response, status, code):
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert response["Cache-Control"] == "private, no-store"


def test_anonymous_session_sets_csrf_cookie_and_authenticates_writes_in_order():
    client = Client(enforce_csrf_checks=True)
    response = client.get(SESSION_URL)
    assert response.status_code == 200
    assert response["Cache-Control"] == "private, no-store"
    assert response.json()["session"] is None
    cookie = response.cookies["csrftoken"].value
    url = f"/api/v2/attempts/{uuid4()}/submission"
    assert_error(client.put(url), 403, "csrf_failed")
    assert_error(client.put(url, HTTP_X_CSRFTOKEN=cookie), 401, "participant_session_required")


def test_v2_submission_uses_cookie_and_csrf_from_real_session(client, attempt):
    browser = Client(enforce_csrf_checks=True)
    browser.cookies.update(client.cookies)
    csrf = browser.get(SESSION_URL).cookies["csrftoken"].value
    url = f"{detail_url(attempt)}/submission"
    payload = {"answers": []}
    assert_error(browser.put(url, payload, content_type="application/json"), 403, "csrf_failed")
    response = browser.put(url, payload, content_type="application/json", HTTP_X_CSRFTOKEN=csrf)
    assert response.status_code == 200
    assert response.json()["attempt"]["score"] == 0


def test_session_recovers_across_entry_change_and_detects_expiry(client, quiz, attempt):
    activity, participant = quiz
    activity.is_participant_entry = False
    activity.status = ActivityStatus.CLOSED
    activity.save(update_fields=["is_participant_entry", "status"])
    ActivityEdition.objects.create(
        slug="next-demo", title="Next Demo", status=ActivityStatus.OPEN, is_participant_entry=True
    )
    response = client.get(SESSION_URL)
    assert response.status_code == 200
    assert response["Cache-Control"] == "private, no-store"
    body = response.json()
    reference = {"id": str(attempt.pk), "status": AttemptStatus.IN_PROGRESS}
    assert body["session"] == {
        "participant": {
            "id": str(participant.pk), "display_name": participant.identity.display_name
        },
        "activity_code": activity.slug,
        "current_attempt": reference,
        "latest_attempt": reference,
    }
    assert datetime.fromisoformat(body["server_time"]).utcoffset() is not None
    deadline = timezone.now() - timedelta(seconds=4)
    QuizAttempt.objects.filter(pk=attempt.pk).update(
        started_at=deadline - timedelta(minutes=5), deadline_at=deadline
    )
    recovered = client.get(SESSION_URL).json()["session"]
    assert recovered["current_attempt"] is None
    assert recovered["latest_attempt"] == {"id": str(attempt.pk), "status": "timed_out"}
    attempt.refresh_from_db()
    assert attempt.status == AttemptStatus.TIMED_OUT
    assert attempt.deadline_at == deadline
    detail = client.get(detail_url(attempt)).json()["attempt"]
    assert set(detail) == {
        "id", "status", "category", "started_at", "deadline_at", "submitted_at", "question_count"
    }
    assert_error(
        client.put(f"{detail_url(attempt)}/submission", {"answers": []},
                   content_type="application/json"),
        409, "attempt_expired",
    )


def test_deidentification_revokes_existing_v2_session(client, quiz, attempt):
    deidentify_participant(participant=quiz[1], reason="test")
    assert client.get(SESSION_URL).json()["session"] is None
    assert_error(client.get(HISTORY_URL), 401, "participant_session_required")
    assert_error(client.get(detail_url(attempt)), 401, "participant_session_required")
    assert client.get("/api/v1/attempts/history").status_code == 401
    assert_error(
        client.put(f"{detail_url(attempt)}/submission", {"answers": []},
                   content_type="application/json"),
        401, "participant_session_required",
    )


def test_v2_history_paginates_and_scopes_to_the_session(client, quiz, attempt):
    activity, participant = quiz
    other = Participant.objects.create(activity=activity)
    foreign = start_attempt(participant=other, category_code="language").attempt
    QuizAttempt.objects.bulk_create([
        QuizAttempt(
            activity=activity, participant=participant, category_config=attempt.category_config,
            bank=attempt.bank, status=AttemptStatus.INVALID, started_at=attempt.started_at,
            deadline_at=attempt.deadline_at, question_count=attempt.question_count, score=1,
        )
        for _ in range(20)
    ])
    first = client.get(HISTORY_URL, {"participant_id": other.pk}).json()
    last = client.get(HISTORY_URL, {"page": "000003"}).json()
    assert first["current_attempt_id"] == str(attempt.pk)
    assert len(first["attempts"]) == 20
    assert last["pagination"] == {"page": 2, "pages": 2, "total": 21, "page_size": 20}
    rows = first["attempts"] + last["attempts"]
    assert [row["id"] for row in rows] == sorted((row["id"] for row in rows), reverse=True)
    assert all(row["score"] is None and "questions" not in row for row in rows)
    assert_error(client.get(HISTORY_URL, {"page": "０"}), 400, "validation_error")
    assert_error(client.get(detail_url(foreign)), 404, "attempt_not_found")
    assert_error(
        client.put(f"{detail_url(foreign)}/submission", {"answers": []},
                   content_type="application/json"),
        404, "attempt_not_found",
    )


def test_v2_submission_retains_first_result_and_hides_invalid_answers(client, quiz, attempt):
    quiz[0].status = ActivityStatus.PAUSED
    quiz[0].save(update_fields=["status"])
    url = f"{detail_url(attempt)}/submission"
    item = attempt.items.select_related("question").first()
    answer = item.question.correct_option.lower()
    payload = {"answers": [{"item_id": str(item.pk), "answer": answer}]}
    first = client.put(url, payload, content_type="application/json")
    assert first.status_code == 200
    assert first["Cache-Control"] == "private, no-store"
    result = first.json()["attempt"]
    assert result["score"] == result["category_high_score"] == 1
    assert result["questions"][0]["answer"] == answer
    assert result["questions"][0]["correct"] is True
    assert "correct_option" not in result["questions"][0]
    assert client.put(url, {"answers": []}, content_type="application/json").json() == first.json()
    assert client.get(detail_url(attempt)).json() == first.json()
    assert client.get(HISTORY_URL).json()["attempts"][0]["score"] == 1

    invalidate_attempt(attempt=attempt, actor=None, reason="test")
    assert_error(
        client.put(url, payload, content_type="application/json"), 409, "attempt_invalidated"
    )
    invalid = client.get(detail_url(attempt)).json()["attempt"]
    assert invalid["status"] == "invalid"
    assert {"questions", "score", "reward_phrase"}.isdisjoint(invalid)
    assert invalid["submitted_at"] == result["submitted_at"]


def test_v2_submission_validates_transport_shape_and_answers(client, attempt):
    url = f"{detail_url(attempt)}/submission"
    item = {"item_id": str(attempt.items.first().pk), "answer": "A"}
    assert_error(client.put(url, "{}"), 415, "unsupported_media_type")
    assert_error(
        client.put(url, b" " * (1024 * 1024 + 1), content_type="application/json"),
        413, "request_too_large",
    )
    for payload in (
        {}, {"answers": [], "extra": True}, {"answers": [item, item]},
        {"answers": [dict(item, answer=123)]}, {"answers": [dict(item, answer="X")]},
        {"answers": [dict(item, answer="a" * 501)]}, {"answers": [dict(item, answer="\ud800")]},
        {"answers": [dict(item, answer="\x00")]}, {"answers": [dict(item, item_id=str(uuid4()))]},
        {"answers": [{"item_id": item["item_id"]}]},
    ):
        assert_error(
            client.put(url, json.dumps(payload), content_type="application/json"),
            400, "validation_error",
        )
    attempt.refresh_from_db()
    assert attempt.status == AttemptStatus.IN_PROGRESS


def test_v2_attempt_images_use_the_same_session_boundary(client, quiz, attempt, settings, tmp_path):
    settings.MEDIA_ROOT = tmp_path
    relative = Path("assets/synthetic-diagram.png")
    target = tmp_path / "question-banks" / attempt.bank.version_code / relative
    target.parent.mkdir(parents=True)
    target.write_bytes((Path("content/examples/question-bank-v1") / relative).read_bytes())
    response = client.get(detail_url(attempt))
    assert response.status_code == 200
    image = next(q for q in response.json()["attempt"]["questions"] if q["image_url"])
    assert "answer" not in image
    url = image["image_url"]
    assert url.startswith(f"{detail_url(attempt)}/items/")
    response = client.get(url)
    assert response.status_code == 200
    assert response["Content-Type"] == "image/png"
    assert b"".join(response.streaming_content) == target.read_bytes()
    assert_error(Client().get(url), 404, "attempt_not_found")
    invalidate_attempt(attempt=attempt, actor=None, reason="test")
    assert_error(client.get(url), 404, "attempt_not_found")
