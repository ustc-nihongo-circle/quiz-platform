import hashlib
import json
import random
import unicodedata
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection, transaction
from django.db.models import Q
from django.utils import timezone

from .models import (
    ActivityBankActivation,
    ActivityCategoryConfig,
    ActivityStatus,
    AdminAuditLog,
    AttemptItem,
    AttemptStatus,
    BankQuestion,
    CategoryHighScore,
    Participant,
    QuestionType,
    QuizAttempt,
    RewardRule,
)

SUBMISSION_GRACE_SECONDS = 3


class QuizStateError(ValueError):
    code = "quiz_state_error"


class ActivityNotOpen(QuizStateError):
    code = "activity_not_open"


class QuestionBankUnavailable(QuizStateError):
    code = "question_bank_unavailable"


class AttemptExpired(QuizStateError):
    code = "attempt_expired"


class AttemptInvalidated(QuizStateError):
    code = "attempt_invalidated"


class ActivityMustBePaused(QuizStateError):
    code = "activity_must_be_paused"


@dataclass(frozen=True)
class AttemptStartResult:
    attempt: QuizAttempt
    created: bool


def normalize_blank_answer(value: str) -> str:
    return unicodedata.normalize("NFKC", value).strip().casefold()


def _eligible_questions(activation: ActivityBankActivation, category_key: str, pool_key: str):
    questions = BankQuestion.objects.filter(
        bank=activation.bank,
        category_key=category_key,
        pool_key=pool_key,
        private_event_allowed=True,
    )
    if activation.legacy_exception:
        return questions.filter(origin="legacy_2024", prior_event_use=True)
    return questions.filter(review_status="verified")


def _pool_quotas(category: ActivityCategoryConfig, activation: ActivityBankActivation):
    overrides = list(category.pool_quotas.all())
    if overrides:
        quotas = [(quota.pool_key, quota.quota) for quota in overrides]
    else:
        quotas = list(
            activation.bank.sampling_rules.filter(category_key=category.category_key)
            .order_by("display_order", "pool_key")
            .values_list("pool_key", "default_quota")
        )
    if not quotas or sum(quota for _, quota in quotas) != category.effective_question_count:
        raise QuestionBankUnavailable("Sampling quotas do not match the configured question count.")
    return quotas


@transaction.atomic
def activate_question_bank(
    *,
    activity,
    bank,
    actor,
    reason: str,
    legacy_exception: bool = False,
) -> ActivityBankActivation:
    reason = reason.strip()
    if not reason:
        raise ValidationError("启用题库必须填写原因。")
    activity = type(activity).objects.select_for_update().get(pk=activity.pk)
    current = (
        ActivityBankActivation.objects.select_for_update()
        .filter(activity=activity, is_current=True)
        .first()
    )
    if current and activity.status != ActivityStatus.PAUSED:
        raise ActivityMustBePaused("An active question bank can only be switched while paused.")
    if not current and activity.status not in {ActivityStatus.DRAFT, ActivityStatus.PAUSED}:
        raise ActivityMustBePaused(
            "The first question bank must be activated in draft or paused state."
        )
    candidate = ActivityBankActivation(
        activity=activity,
        bank=bank,
        activated_by=actor,
        reason=reason,
        legacy_exception=legacy_exception,
    )
    if current:
        current.is_current = False
        current.save(update_fields=("is_current",))
    candidate.full_clean()
    for category in activity.category_configs.all():
        for pool_key, quota in _pool_quotas(category, candidate):
            if _eligible_questions(candidate, category.category_key, pool_key).count() < quota:
                raise ValidationError(
                    {
                        "bank": (
                            f"题库池 {category.category_key}/{pool_key} "
                            f"的可用题目少于配额 {quota}。"
                        )
                    }
                )
    candidate.save()
    AdminAuditLog.objects.create(
        actor=actor,
        activity=activity,
        action="question_bank_activated",
        reason=reason,
        metadata={
            "bank_version": bank.version_code,
            "legacy_exception": legacy_exception,
        },
    )
    return candidate


@transaction.atomic
def transition_activity(*, activity, next_status: str, actor, reason: str):
    reason = reason.strip()
    if not reason:
        raise ValidationError("切换活动状态必须填写原因。")
    activity = type(activity).objects.select_for_update().get(pk=activity.pk)
    previous_status = activity.status
    activity.transition_to(next_status)
    AdminAuditLog.objects.create(
        actor=actor,
        activity=activity,
        action="activity_status_changed",
        reason=reason,
        metadata={"from": previous_status, "to": next_status},
    )
    return activity


@transaction.atomic
def start_attempt(
    *,
    participant: Participant,
    category_code: str,
    now=None,
    rng: random.Random | random.SystemRandom | None = None,
) -> AttemptStartResult:
    now = now or timezone.now()
    rng = rng or random.SystemRandom()
    participant = Participant.objects.select_for_update().select_related("activity").get(
        pk=participant.pk
    )
    activity = participant.activity
    existing = (
        QuizAttempt.objects.select_for_update()
        .filter(participant=participant, status=AttemptStatus.IN_PROGRESS)
        .first()
    )
    if existing:
        if now <= existing.deadline_at + timedelta(seconds=SUBMISSION_GRACE_SECONDS):
            return AttemptStartResult(existing, False)
        existing.status = AttemptStatus.TIMED_OUT
        existing.save(update_fields=("status", "updated_at"))
    if activity.status != ActivityStatus.OPEN:
        raise ActivityNotOpen("The activity is not open for new attempts.")
    try:
        category = activity.category_configs.get(category_key=category_code)
        activation = ActivityBankActivation.objects.select_related("bank").get(
            activity=activity,
            is_current=True,
        )
    except (ActivityCategoryConfig.DoesNotExist, ActivityBankActivation.DoesNotExist) as error:
        raise QuestionBankUnavailable(
            "The category or active question bank is unavailable."
        ) from error

    selected: list[BankQuestion] = []
    for pool_key, quota in _pool_quotas(category, activation):
        candidates = list(_eligible_questions(activation, category.category_key, pool_key))
        if len(candidates) < quota:
            raise QuestionBankUnavailable(
                f"Pool {category.category_key}/{pool_key} has fewer questions than its quota."
            )
        selected.extend(rng.sample(candidates, quota))
    rng.shuffle(selected)
    deadline = now + timedelta(seconds=category.effective_time_limit_seconds)
    try:
        attempt = QuizAttempt.objects.create(
            activity=activity,
            participant=participant,
            category_config=category,
            bank=activation.bank,
            status=AttemptStatus.IN_PROGRESS,
            started_at=now,
            deadline_at=deadline,
            question_count=len(selected),
        )
    except IntegrityError:
        attempt = QuizAttempt.objects.get(
            participant=participant,
            status=AttemptStatus.IN_PROGRESS,
        )
        return AttemptStartResult(attempt, False)
    AttemptItem.objects.bulk_create(
        [
            AttemptItem(
                attempt=attempt,
                question=question,
                display_order=index,
                option_order=["A", "B", "C", "D"]
                if question.question_type == QuestionType.SINGLE_CHOICE
                else [],
            )
            for index, question in enumerate(selected, start=1)
        ]
    )
    return AttemptStartResult(attempt, True)


def _canonical_submission_digest(answers: dict[str, Any]) -> str:
    canonical = json.dumps(answers, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def refresh_attempt_timeout(attempt: QuizAttempt, *, now=None) -> QuizAttempt:
    now = now or timezone.now()
    if (
        attempt.status == AttemptStatus.IN_PROGRESS
        and now > attempt.deadline_at + timedelta(seconds=SUBMISSION_GRACE_SECONDS)
    ):
        QuizAttempt.objects.filter(
            pk=attempt.pk,
            status=AttemptStatus.IN_PROGRESS,
        ).update(status=AttemptStatus.TIMED_OUT, updated_at=now)
        attempt.refresh_from_db()
    return attempt


def _answer_is_correct(item: AttemptItem, value: Any) -> bool:
    if value is None or value == "":
        return False
    if item.question.question_type == QuestionType.SINGLE_CHOICE:
        if not isinstance(value, str) or value.upper() not in {"A", "B", "C", "D"}:
            raise ValidationError({str(item.pk): "选择题答案必须是 A、B、C 或 D。"})
        return value.upper() == item.question.correct_option
    if not isinstance(value, str) or len(value) > 500:
        raise ValidationError({str(item.pk): "填空答案必须是 500 字以内的文本。"})
    submitted = normalize_blank_answer(value)
    accepted = {normalize_blank_answer(answer) for answer in item.question.acceptable_answers}
    return submitted in accepted


def _reward_text(attempt: QuizAttempt) -> str:
    rate = Decimal(attempt.score) / Decimal(attempt.question_count)
    rule = (
        RewardRule.objects.filter(
            activity=attempt.activity,
            is_active=True,
            min_score_rate__lte=rate,
            max_score_rate__gte=rate,
        )
        .filter(Q(category_config=attempt.category_config) | Q(category_config__isnull=True))
        .order_by("-priority", "-category_config_id", "pk")
        .first()
    )
    return rule.text if rule else ""


def update_category_high_score(attempt: QuizAttempt) -> CategoryHighScore:
    if connection.vendor == "postgresql":
        table = CategoryHighScore._meta.db_table
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO {table}
                    (activity_id, participant_id, category_config_id, score,
                     achieved_at, source_attempt_id, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (participant_id, category_config_id)
                DO UPDATE SET
                    score = EXCLUDED.score,
                    achieved_at = EXCLUDED.achieved_at,
                    source_attempt_id = EXCLUDED.source_attempt_id,
                    updated_at = EXCLUDED.updated_at
                WHERE EXCLUDED.score > {table}.score
                """,
                [
                    attempt.activity_id,
                    attempt.participant_id,
                    attempt.category_config_id,
                    attempt.score,
                    attempt.submitted_at,
                    attempt.pk,
                    attempt.submitted_at,
                ],
            )
        return CategoryHighScore.objects.get(
            participant=attempt.participant,
            category_config=attempt.category_config,
        )
    high_score, created = CategoryHighScore.objects.select_for_update().get_or_create(
        participant=attempt.participant,
        category_config=attempt.category_config,
        defaults={
            "activity": attempt.activity,
            "score": attempt.score,
            "achieved_at": attempt.submitted_at,
            "source_attempt": attempt,
        },
    )
    if not created and attempt.score > high_score.score:
        high_score.score = attempt.score
        high_score.achieved_at = attempt.submitted_at
        high_score.source_attempt = attempt
        high_score.save(update_fields=("score", "achieved_at", "source_attempt", "updated_at"))
    return high_score


@transaction.atomic
def _submit_attempt_atomic(
    *,
    attempt: QuizAttempt,
    participant: Participant,
    answers: dict[str, Any],
    now=None,
) -> QuizAttempt | None:
    now = now or timezone.now()
    attempt = (
        QuizAttempt.objects.select_for_update()
        .select_related("activity", "participant", "category_config")
        .get(pk=attempt.pk, participant=participant)
    )
    digest = _canonical_submission_digest(answers)
    if attempt.status == AttemptStatus.SUBMITTED:
        if attempt.submission_digest != digest:
            AdminAuditLog.objects.create(
                activity=attempt.activity,
                participant=participant,
                action="attempt_submission_conflict",
                reason="A later submission carried a different payload; the first result was kept.",
                metadata={"attempt_id": str(attempt.pk)},
            )
        return attempt
    if attempt.status == AttemptStatus.INVALID:
        raise AttemptInvalidated("The attempt has been invalidated.")
    if attempt.status == AttemptStatus.TIMED_OUT:
        raise AttemptExpired("The attempt deadline has passed.")
    if now > attempt.deadline_at + timedelta(seconds=SUBMISSION_GRACE_SECONDS):
        attempt.status = AttemptStatus.TIMED_OUT
        attempt.save(update_fields=("status", "updated_at"))
        return None

    items = list(attempt.items.select_related("question").all())
    item_map = {str(item.pk): item for item in items}
    unknown = set(answers) - set(item_map)
    if unknown:
        raise ValidationError({"answers": "提交包含不属于本次答题的题目。"})
    score = 0
    for item in items:
        value = answers.get(str(item.pk))
        item.submitted_answer = value
        item.is_correct = _answer_is_correct(item, value)
        score += int(item.is_correct)
    AttemptItem.objects.bulk_update(items, ("submitted_answer", "is_correct"))
    attempt.status = AttemptStatus.SUBMITTED
    attempt.submitted_at = now
    attempt.score = score
    attempt.submission_digest = digest
    attempt.reward_text = _reward_text(attempt)
    attempt.save(
        update_fields=(
            "status",
            "submitted_at",
            "score",
            "submission_digest",
            "reward_text",
            "updated_at",
        )
    )
    update_category_high_score(attempt)
    return attempt


def submit_attempt(
    *,
    attempt: QuizAttempt,
    participant: Participant,
    answers: dict[str, Any],
    now=None,
) -> QuizAttempt:
    result = _submit_attempt_atomic(
        attempt=attempt,
        participant=participant,
        answers=answers,
        now=now,
    )
    if result is None:
        raise AttemptExpired("The attempt deadline has passed.")
    return result


@transaction.atomic
def invalidate_attempt(*, attempt: QuizAttempt, actor, reason: str) -> QuizAttempt:
    reason = reason.strip()
    if not reason:
        raise ValidationError("作废答题记录必须填写原因。")
    attempt = QuizAttempt.objects.select_for_update().get(pk=attempt.pk)
    attempt.status = AttemptStatus.INVALID
    attempt.invalidated_at = timezone.now()
    attempt.invalidation_reason = reason
    attempt.save(
        update_fields=("status", "invalidated_at", "invalidation_reason", "updated_at")
    )
    replacement = (
        QuizAttempt.objects.filter(
            participant=attempt.participant,
            category_config=attempt.category_config,
            status=AttemptStatus.SUBMITTED,
        )
        .order_by("-score", "submitted_at", "pk")
        .first()
    )
    if replacement:
        CategoryHighScore.objects.update_or_create(
            participant=attempt.participant,
            category_config=attempt.category_config,
            defaults={
                "activity": attempt.activity,
                "score": replacement.score,
                "achieved_at": replacement.submitted_at,
                "source_attempt": replacement,
            },
        )
    else:
        CategoryHighScore.objects.filter(
            participant=attempt.participant,
            category_config=attempt.category_config,
        ).delete()
    AdminAuditLog.objects.create(
        actor=actor,
        activity=attempt.activity,
        participant=attempt.participant,
        action="attempt_invalidated",
        reason=reason,
        metadata={"attempt_id": str(attempt.pk)},
    )
    return attempt


def serialize_attempt(attempt: QuizAttempt) -> dict[str, Any]:
    attempt = QuizAttempt.objects.select_related("category_config", "bank").prefetch_related(
        "items__question__assets"
    ).get(pk=attempt.pk)
    questions = []
    is_result = attempt.status in {AttemptStatus.SUBMITTED, AttemptStatus.INVALID}
    for item in attempt.items.all():
        question = item.question
        asset = question.assets.first()
        entry: dict[str, Any] = {
            "id": str(item.pk),
            "position": item.display_order,
            "prompt": question.prompt,
            "type": question.question_type,
            "image_url": (
                f"/media/question-banks/{attempt.bank.version_code}/{asset.relative_path}"
                if asset
                else None
            ),
            "options": (
                [
                    {"id": "A", "text": question.option_a},
                    {"id": "B", "text": question.option_b},
                    {"id": "C", "text": question.option_c},
                    {"id": "D", "text": question.option_d},
                ]
                if question.question_type == QuestionType.SINGLE_CHOICE
                else []
            ),
        }
        if is_result:
            entry["answer"] = item.submitted_answer
            entry["correct"] = item.is_correct
        questions.append(entry)
    data: dict[str, Any] = {
        "id": str(attempt.pk),
        "status": attempt.status,
        "category": {
            "code": attempt.category_config.category_key,
            "title": attempt.category_config.title,
        },
        "started_at": attempt.started_at.isoformat(),
        "deadline_at": attempt.deadline_at.isoformat(),
        "question_count": attempt.question_count,
        "questions": questions,
    }
    if is_result:
        high_score = CategoryHighScore.objects.filter(
            participant=attempt.participant,
            category_config=attempt.category_config,
        ).first()
        data.update(
            {
                "score": attempt.score,
                "score_rate": attempt.score / attempt.question_count,
                "category_high_score": high_score.score if high_score else None,
                "reward_phrase": attempt.reward_text or None,
            }
        )
    return data
