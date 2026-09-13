from datetime import timedelta

import pytest
from django.utils import timezone
from test_attempt_api import create_open_quiz

from quiz.models import Participant
from quiz.rate_limits import RateLimited
from quiz.services import start_attempt, submit_attempt

pytestmark = pytest.mark.django_db


def test_new_attempt_quota_does_not_count_retries_or_block_submission():
    activity = create_open_quiz()
    participant = Participant.objects.create(activity=activity)
    now = timezone.now()
    for _ in range(10):
        first = start_attempt(participant=participant, category_code="language", now=now)
        retry = start_attempt(participant=participant, category_code="language", now=now)
        assert first.created and not retry.created and first.attempt.pk == retry.attempt.pk
        submit_attempt(attempt=first.attempt, participant=participant, answers={}, now=now)
    with pytest.raises(RateLimited):
        start_attempt(participant=participant, category_code="language", now=now)
    assert (
        submit_attempt(attempt=first.attempt, participant=participant, answers={}, now=now).status
        == "submitted"
    )
    assert start_attempt(
        participant=participant, category_code="language", now=now + timedelta(minutes=1)
    ).created


def test_refill_clock_rollback_multi_bucket_atomicity_and_pruning():
    from quiz.models import RateLimitBucket
    from quiz.rate_limits import Policy, consume_limits, prune_buckets

    now = timezone.now()
    first = (Policy("synthetic_first", 1, 1, 10), "scope", "synthetic-first")
    second = (Policy("synthetic_second", 2, 1, 10), "scope", "synthetic-second")
    consume_limits([first], now=now)
    with pytest.raises(RateLimited) as exc:
        consume_limits([first, second], now=now - timedelta(seconds=30))
    assert exc.value.retry_after_seconds == 10
    assert RateLimitBucket.objects.get(namespace="synthetic_second").tokens == 2
    with pytest.raises(RateLimited):
        consume_limits([first], now=now + timedelta(seconds=9))
    consume_limits([first], now=now + timedelta(seconds=10))
    assert prune_buckets(now=now + timedelta(minutes=59)) == 0
    assert prune_buckets(now=now + timedelta(hours=2), batch_size=1) == 2


def test_untrusted_forwarded_headers_do_not_change_address(settings):
    from django.test import RequestFactory

    from quiz.rate_limits import client_ip

    settings.QUIZ_TRUSTED_PROXY_CIDRS = ["172.30.0.1/32"]
    request = RequestFactory().get(
        "/",
        REMOTE_ADDR="192.0.2.1",
        HTTP_X_REAL_IP="198.51.100.2",
        HTTP_X_FORWARDED_FOR="198.51.100.3",
    )
    assert client_ip(request) == "192.0.2.1"
    request.META["REMOTE_ADDR"] = "172.30.0.1"
    assert client_ip(request) == "198.51.100.2"


def test_failed_new_identity_write_rolls_back_only_creation_quotas(monkeypatch):
    from django.db import IntegrityError

    from quiz.identity import register_or_resume
    from quiz.models import ParticipantIdentity, RateLimitBucket

    activity = create_open_quiz()

    def fail(*args, **kwargs):
        raise IntegrityError("synthetic failure")

    monkeypatch.setattr(ParticipantIdentity.objects, "create", fail)
    with pytest.raises(ParticipantIdentity.DoesNotExist):
        register_or_resume(
            activity=activity,
            display_name="合成",
            identifier="SYNTHETIC",
            contact="a@example.com",
            source_ip="192.0.2.1",
        )
    assert not Participant.objects.exists()
    assert not RateLimitBucket.objects.exists()
