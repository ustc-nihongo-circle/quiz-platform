from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

import pytest
from django.db import connection, connections
from django.utils import timezone

from quiz.identity import IdentityProtector, register_or_resume
from quiz.models import (
    ActivityBankActivation,
    ActivityCategoryConfig,
    ActivityEdition,
    ActivityStatus,
    BankQuestion,
    CategoryHighScore,
    Participant,
    QuestionBankVersion,
    QuestionIdentity,
    QuestionType,
    QuizAttempt,
    ReviewStatus,
    SamplingRule,
)
from quiz.services import start_attempt, submit_attempt, update_category_high_score

pytestmark = [pytest.mark.postgres, pytest.mark.django_db(transaction=True)]


@pytest.fixture(autouse=True)
def require_postgresql():
    assert connection.vendor == "postgresql", (
        "PostgreSQL tests are a required gate. Start compose.yaml and run with "
        "DJANGO_USE_SQLITE=0."
    )


@pytest.fixture
def protector():
    return IdentityProtector(
        keys={"v1": b"k" * 32},
        active_key_id="v1",
        hmac_key=b"h" * 32,
    )


def build_quiz():
    activity = ActivityEdition.objects.create(
        slug="concurrency-2026",
        title="并发测试活动",
        status=ActivityStatus.OPEN,
        default_question_count=2,
    )
    category = ActivityCategoryConfig.objects.create(
        activity=activity,
        category_key="demo",
        title="并发测试板块",
    )
    bank = QuestionBankVersion.objects.create(
        version_code="concurrency-v1",
        title="并发测试题库",
        source_label="synthetic test",
        content_sha256="c" * 64,
    )
    for number in (1, 2):
        BankQuestion.objects.create(
            bank=bank,
            identity=QuestionIdentity.objects.create(stable_code=f"CONCURRENCY-{number}"),
            category_key="demo",
            pool_key="default",
            question_type=QuestionType.SINGLE_CHOICE,
            prompt=f"虚构并发题 {number}",
            option_a="甲",
            option_b="乙",
            option_c="丙",
            option_d="丁",
            correct_option="A",
            origin="synthetic_test",
            review_status=ReviewStatus.VERIFIED,
            private_event_allowed=True,
            public_release_allowed=True,
            source_reference="generated test",
            source_license="CC0-1.0",
        )
    SamplingRule.objects.create(
        bank=bank,
        category_key="demo",
        pool_key="default",
        default_quota=2,
    )
    bank.finalized_at = timezone.now()
    bank.save(update_fields=("finalized_at",))
    ActivityBankActivation.objects.create(
        activity=activity,
        bank=bank,
        reason="concurrency test",
    )
    return activity, category, bank


def _thread_call(callback):
    connections.close_all()
    try:
        return callback()
    finally:
        connections.close_all()


def test_fifty_distinct_participants_submit_without_lost_records(protector):
    activity, _, _ = build_quiz()
    attempt_ids = []
    for number in range(50):
        participant = register_or_resume(
            activity=activity,
            display_name=f"测试参与者 {number}",
            identifier=f"PB{number:08d}",
            contact=f"138{number:08d}",
            protector=protector,
        ).participant
        attempt = start_attempt(participant=participant, category_code="demo").attempt
        answers = {str(item.pk): "A" for item in attempt.items.all()}
        attempt_ids.append((attempt.pk, participant.pk, answers))

    def submit(data):
        attempt_id, participant_id, answers = data
        return _thread_call(
            lambda: submit_attempt(
                attempt=QuizAttempt.objects.get(pk=attempt_id),
                participant=Participant.objects.get(pk=participant_id),
                answers=answers,
            ).score
        )

    with ThreadPoolExecutor(max_workers=50) as executor:
        scores = list(executor.map(submit, attempt_ids))

    assert scores == [2] * 50
    assert QuizAttempt.objects.filter(status="submitted").count() == 50
    assert CategoryHighScore.objects.count() == 50


def test_fifty_retries_of_one_submission_produce_one_result(protector):
    activity, _, _ = build_quiz()
    participant = register_or_resume(
        activity=activity,
        display_name="测试参与者",
        identifier="PB24000001",
        contact="13800138000",
        protector=protector,
    ).participant
    attempt = start_attempt(participant=participant, category_code="demo").attempt
    answers = {str(item.pk): "A" for item in attempt.items.all()}

    def submit(_):
        return _thread_call(
            lambda: submit_attempt(
                attempt=QuizAttempt.objects.get(pk=attempt.pk),
                participant=Participant.objects.get(pk=participant.pk),
                answers=answers,
            ).score
        )

    with ThreadPoolExecutor(max_workers=50) as executor:
        scores = list(executor.map(submit, range(50)))

    assert scores == [2] * 50
    assert CategoryHighScore.objects.filter(participant=participant).count() == 1


def test_fifty_registrations_with_one_identifier_create_one_participant(protector):
    activity, _, _ = build_quiz()

    def register(_):
        return _thread_call(
            lambda: register_or_resume(
                activity=activity,
                display_name="测试参与者",
                identifier="PB24000001",
                contact="13800138000",
                protector=protector,
            ).participant.pk
        )

    with ThreadPoolExecutor(max_workers=50) as executor:
        participant_ids = list(executor.map(register, range(50)))

    assert len(set(participant_ids)) == 1
    assert Participant.objects.filter(activity=activity).count() == 1


def test_fifty_start_requests_create_one_in_progress_attempt(protector):
    activity, _, _ = build_quiz()
    participant = register_or_resume(
        activity=activity,
        display_name="测试参与者",
        identifier="PB24000001",
        contact="13800138000",
        protector=protector,
    ).participant

    def start(_):
        return _thread_call(
            lambda: start_attempt(
                participant=Participant.objects.get(pk=participant.pk),
                category_code="demo",
            ).attempt.pk
        )

    with ThreadPoolExecutor(max_workers=50) as executor:
        attempt_ids = list(executor.map(start, range(50)))

    assert len(set(attempt_ids)) == 1
    assert QuizAttempt.objects.filter(participant=participant, status="in_progress").count() == 1


def test_concurrent_high_score_updates_are_monotonic(protector):
    activity, category, bank = build_quiz()
    participant = register_or_resume(
        activity=activity,
        display_name="测试参与者",
        identifier="PB24000001",
        contact="13800138000",
        protector=protector,
    ).participant
    now = timezone.now()
    attempts = [
        QuizAttempt.objects.create(
            activity=activity,
            participant=participant,
            category_config=category,
            bank=bank,
            status="submitted",
            started_at=now,
            deadline_at=now + timedelta(minutes=5),
            submitted_at=now + timedelta(seconds=score),
            question_count=50,
            score=score,
        )
        for score in range(50)
    ]

    def update(attempt_id):
        return _thread_call(
            lambda: update_category_high_score(QuizAttempt.objects.get(pk=attempt_id)).score
        )

    with ThreadPoolExecutor(max_workers=50) as executor:
        list(executor.map(update, [attempt.pk for attempt in attempts]))

    high_score = CategoryHighScore.objects.get(
        participant=participant,
        category_config=category,
    )
    assert high_score.score == 49
