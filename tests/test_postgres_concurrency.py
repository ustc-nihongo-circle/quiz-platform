from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest
from django.db import connection, connections
from django.utils import timezone

from quiz.identity import IdentityProtector, register_or_resume
from quiz.models import (
    ActivityBankActivation,
    ActivityCategoryConfig,
    ActivityEdition,
    ActivityStatus,
    AdminAuditLog,
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
from quiz.services import (
    ActivityNotOpen,
    AttemptExpired,
    AttemptInvalidated,
    expire_stale_attempts,
    invalidate_attempt,
    select_participant_entry,
    start_attempt,
    submit_attempt,
    transition_activity,
    update_category_high_score,
)

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
        is_participant_entry=True,
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


def test_concurrent_participant_entry_selection_keeps_exactly_one_activity():
    activities = [
        ActivityEdition.objects.create(
            slug=f"entry-{number}",
            title=f"入口候选 {number}",
            status=ActivityStatus.DRAFT,
        )
        for number in range(20)
    ]

    def select(activity_id):
        return _thread_call(
            lambda: select_participant_entry(
                activity=ActivityEdition.objects.get(pk=activity_id),
                actor=None,
                reason="并发入口选择测试",
            ).pk
        )

    with ThreadPoolExecutor(max_workers=20) as executor:
        list(executor.map(select, [activity.pk for activity in activities]))

    assert ActivityEdition.objects.filter(is_participant_entry=True).count() == 1
    assert AdminAuditLog.objects.filter(action="participant_entry_selected").count() == 20


def test_pause_and_attempt_starts_never_insert_an_attempt_after_pause_commit(protector):
    activity, _, _ = build_quiz()
    participants = [
        register_or_resume(
            activity=activity,
            display_name=f"暂停竞态参与者 {number}",
            identifier=f"PAUSE{number:06d}",
            contact=f"139{number:08d}",
            protector=protector,
        ).participant
        for number in range(20)
    ]
    barrier = Barrier(len(participants) + 1)

    def start(participant_id):
        def callback():
            barrier.wait()
            try:
                return start_attempt(
                    participant=Participant.objects.get(pk=participant_id),
                    category_code="demo",
                ).attempt.pk
            except ActivityNotOpen:
                return None

        return _thread_call(callback)

    def pause():
        def callback():
            barrier.wait()
            return transition_activity(
                activity=ActivityEdition.objects.get(pk=activity.pk),
                next_status=ActivityStatus.PAUSED,
                actor=None,
                reason="并发暂停测试",
            ).pk

        return _thread_call(callback)

    with ThreadPoolExecutor(max_workers=len(participants) + 1) as executor:
        start_futures = [executor.submit(start, participant.pk) for participant in participants]
        pause_future = executor.submit(pause)
        started = [future.result() for future in start_futures]
        pause_future.result()

    pause_recorded_at = AdminAuditLog.objects.get(action="activity_status_changed").created_at
    assert activity.attempts.filter(created_at__gt=pause_recorded_at).count() == 0
    assert len([attempt_id for attempt_id in started if attempt_id]) == activity.attempts.count()


def test_submission_and_invalidation_converge_on_invalid_without_a_high_score(protector):
    activity, _, _ = build_quiz()
    participant = register_or_resume(
        activity=activity,
        display_name="提交作废竞态参与者",
        identifier="PB24000001",
        contact="13800138000",
        protector=protector,
    ).participant
    attempt = start_attempt(participant=participant, category_code="demo").attempt
    answers = {str(item.pk): "A" for item in attempt.items.all()}
    barrier = Barrier(2)

    def submit():
        def callback():
            barrier.wait()
            try:
                submit_attempt(
                    attempt=QuizAttempt.objects.get(pk=attempt.pk),
                    participant=Participant.objects.get(pk=participant.pk),
                    answers=answers,
                )
                return "submitted"
            except AttemptInvalidated:
                return "invalidated-first"

        return _thread_call(callback)

    def invalidate():
        def callback():
            barrier.wait()
            invalidate_attempt(
                attempt=QuizAttempt.objects.get(pk=attempt.pk),
                actor=None,
                reason="并发作废测试",
            )
            return "invalidated"

        return _thread_call(callback)

    with ThreadPoolExecutor(max_workers=2) as executor:
        submit_future = executor.submit(submit)
        invalidate_future = executor.submit(invalidate)
        assert submit_future.result() in {"submitted", "invalidated-first"}
        assert invalidate_future.result() == "invalidated"

    attempt.refresh_from_db()
    assert attempt.status == "invalid"
    assert not CategoryHighScore.objects.filter(participant=participant).exists()
    assert AdminAuditLog.objects.filter(action="attempt_invalidated").count() == 1


def test_parallel_expiry_is_idempotent_and_late_submission_cannot_score():
    activity, _, _ = build_quiz()
    participant = Participant.objects.create(activity=activity)
    now = timezone.now()
    attempt = start_attempt(
        participant=participant,
        category_code="demo",
        now=now - timedelta(minutes=31),
    ).attempt
    barrier = Barrier(3)

    def expire():
        def callback():
            barrier.wait()
            return expire_stale_attempts(now=now)

        return _thread_call(callback)

    def submit():
        def callback():
            barrier.wait()
            with pytest.raises(AttemptExpired):
                submit_attempt(attempt=attempt, participant=participant, answers={}, now=now)

        return _thread_call(callback)

    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = [executor.submit(expire), executor.submit(expire), executor.submit(submit)]
        counts = [future.result() for future in futures]
    assert sum(counts[:2]) <= 1
    attempt.refresh_from_db()
    assert attempt.status == "timed_out"
    assert not CategoryHighScore.objects.exists()
    assert AdminAuditLog.objects.filter(action="stale_attempts_expired").count() <= 1


def test_cleanup_waiting_for_accepted_submission_preserves_result():
    from threading import Event

    from django.db import transaction

    activity, _, _ = build_quiz()
    participant = Participant.objects.create(activity=activity)
    now = timezone.now()
    attempt = start_attempt(participant=participant, category_code="demo", now=now).attempt
    submission_locked = Event()
    cleanup_started = Event()

    def submit():
        def callback():
            with transaction.atomic():
                QuizAttempt.objects.select_for_update().get(pk=attempt.pk)
                submit_attempt(attempt=attempt, participant=participant, answers={}, now=now)
                submission_locked.set()
                assert cleanup_started.wait(timeout=10)

        return _thread_call(callback)

    def expire():
        def callback():
            assert submission_locked.wait(timeout=10)
            cleanup_started.set()
            return expire_stale_attempts(now=now + timedelta(minutes=31))

        return _thread_call(callback)

    with ThreadPoolExecutor(max_workers=2) as executor:
        submitted = executor.submit(submit)
        expired = executor.submit(expire)
        submitted.result()
        assert expired.result() == 0
    attempt.refresh_from_db()
    assert attempt.status == "submitted"
    assert CategoryHighScore.objects.filter(source_attempt=attempt).exists()


def test_parallel_workers_cannot_overspend_the_last_tokens():
    from quiz.rate_limits import Policy, RateLimited, consume_limits

    now = timezone.now()
    barrier = Barrier(20)
    policy = Policy("parallel_security", 7, 1, 3600)

    def attempt(_):
        def callback():
            barrier.wait()
            try:
                consume_limits([(policy, "synthetic", "shared")], now=now)
                return True
            except RateLimited:
                return False
        return _thread_call(callback)

    with ThreadPoolExecutor(max_workers=20) as executor:
        assert sum(executor.map(attempt, range(20))) == 7
