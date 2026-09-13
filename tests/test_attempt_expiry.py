from datetime import timedelta
from io import StringIO

import pytest
from django.core.management import call_command
from django.utils import timezone
from test_attempt_api import create_open_quiz

from quiz.models import AdminAuditLog, CategoryHighScore, Participant, QuizAttempt
from quiz.services import (
    AttemptExpired,
    expire_stale_attempts,
    refresh_attempt_timeout,
    start_attempt,
    submit_attempt,
)

pytestmark = pytest.mark.django_db


@pytest.fixture
def quiz():
    activity = create_open_quiz()
    return activity, timezone.now()


def new_attempt(activity, started_at):
    participant = Participant.objects.create(activity=activity)
    return start_attempt(participant=participant, category_code="language", now=started_at).attempt


def test_cleanup_exact_boundary_preserves_newer_and_final_records(quiz):
    activity, now = quiz
    boundary = new_attempt(activity, now - timedelta(minutes=30))
    newer = new_attempt(activity, now - timedelta(minutes=30) + timedelta(microseconds=1))
    submitted = new_attempt(activity, now - timedelta(hours=1))
    submit_attempt(
        attempt=submitted,
        participant=submitted.participant,
        answers={},
        now=submitted.started_at + timedelta(seconds=1),
    )
    invalid = new_attempt(activity, now - timedelta(hours=1))
    QuizAttempt.objects.filter(pk=invalid.pk).update(status="invalid")
    timed_out = new_attempt(activity, now - timedelta(hours=1))
    QuizAttempt.objects.filter(pk=timed_out.pk).update(status="timed_out")
    activity.status = "closed"
    activity.save(update_fields=("status",))

    assert expire_stale_attempts(now=now, dry_run=True) == 1
    boundary.refresh_from_db()
    assert boundary.status == "in_progress"
    assert not AdminAuditLog.objects.filter(action="stale_attempts_expired").exists()
    assert expire_stale_attempts(now=now) == 1
    assert expire_stale_attempts(now=now) == 0
    for attempt, expected in [
        (boundary, "timed_out"),
        (newer, "in_progress"),
        (submitted, "submitted"),
        (invalid, "invalid"),
        (timed_out, "timed_out"),
    ]:
        attempt.refresh_from_db()
        assert attempt.status == expected
    assert CategoryHighScore.objects.filter(source_attempt=submitted).exists()
    audit = AdminAuditLog.objects.get(action="stale_attempts_expired")
    assert audit.metadata == {"count": 1, "max_age_seconds": 1800}


def test_cleanup_command_dry_run_and_activity_scope(quiz):
    activity, now = quiz
    attempt = new_attempt(activity, now - timedelta(hours=1))
    output = StringIO()
    call_command("expire_stale_attempts", "--dry-run", "--activity", activity.slug, stdout=output)
    assert "eligible=1" in output.getvalue()
    attempt.refresh_from_db()
    assert attempt.status == "in_progress"
    output = StringIO()
    call_command("expire_stale_attempts", "--activity", activity.slug, stdout=output)
    assert "expired=1" in output.getvalue()
    attempt.refresh_from_db()
    assert attempt.status == "timed_out"


@pytest.mark.parametrize("action", ["submit", "refresh", "restart"])
def test_legacy_long_deadline_cannot_bypass_hard_expiry(quiz, action):
    activity, now = quiz
    attempt = new_attempt(activity, now - timedelta(minutes=30))
    QuizAttempt.objects.filter(pk=attempt.pk).update(deadline_at=now + timedelta(hours=1))
    attempt.refresh_from_db()
    if action == "submit":
        with pytest.raises(AttemptExpired):
            submit_attempt(attempt=attempt, participant=attempt.participant, answers={}, now=now)
    elif action == "refresh":
        refresh_attempt_timeout(attempt, now=now)
    else:
        replacement = start_attempt(
            participant=attempt.participant,
            category_code="language",
            now=now,
        )
        assert replacement.created and replacement.attempt.pk != attempt.pk
    attempt.refresh_from_db()
    assert attempt.status == "timed_out"
    assert not CategoryHighScore.objects.exists()


def test_new_attempt_deadline_is_capped_without_extending_shorter_limit(quiz):
    activity, now = quiz
    short = new_attempt(activity, now)
    assert short.deadline_at == now + timedelta(minutes=5)
    activity.default_time_limit_seconds = 3600
    activity.save(update_fields=("default_time_limit_seconds",))
    long = new_attempt(activity, now)
    assert long.deadline_at == now + timedelta(minutes=30)
