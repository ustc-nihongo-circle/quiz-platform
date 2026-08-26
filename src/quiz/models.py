# ruff: noqa: DJ012

import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import F, Q


class ActivityStatus(models.TextChoices):
    DRAFT = "draft", "Draft"
    OPEN = "open", "Open"
    PAUSED = "paused", "Paused"
    CLOSED = "closed", "Closed"
    ARCHIVED = "archived", "Archived"


class ActivityEdition(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slug = models.SlugField(max_length=80, unique=True)
    title = models.CharField(max_length=200)
    status = models.CharField(
        max_length=16,
        choices=ActivityStatus.choices,
        default=ActivityStatus.DRAFT,
    )
    default_question_count = models.PositiveSmallIntegerField(default=15)
    default_time_limit_seconds = models.PositiveIntegerField(default=300)
    is_participant_entry = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        permissions = [("operate_quiz", "Can operate the on-site quiz console")]
        constraints = [
            models.CheckConstraint(
                condition=Q(default_question_count__gt=0),
                name="activity_default_question_count_positive",
            ),
            models.CheckConstraint(
                condition=Q(default_time_limit_seconds__gt=0),
                name="activity_default_time_limit_positive",
            ),
            models.UniqueConstraint(
                fields=("is_participant_entry",),
                condition=Q(is_participant_entry=True),
                name="one_participant_entry_activity",
            ),
        ]

    def __str__(self) -> str:
        return self.title

    def transition_to(self, next_status: str) -> None:
        allowed = {
            ActivityStatus.DRAFT: {ActivityStatus.OPEN, ActivityStatus.ARCHIVED},
            ActivityStatus.OPEN: {ActivityStatus.PAUSED, ActivityStatus.CLOSED},
            ActivityStatus.PAUSED: {ActivityStatus.OPEN, ActivityStatus.CLOSED},
            ActivityStatus.CLOSED: {ActivityStatus.ARCHIVED},
            ActivityStatus.ARCHIVED: set(),
        }
        if next_status not in allowed[ActivityStatus(self.status)]:
            raise ValidationError({"status": "This activity status transition is not allowed."})
        self.status = next_status
        self.save(update_fields=("status", "updated_at"))


class Participant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    activity = models.ForeignKey(
        ActivityEdition,
        on_delete=models.PROTECT,
        related_name="participants",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    anonymized_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=("activity", "created_at"), name="participant_activity_created"),
        ]

    def __str__(self) -> str:
        return str(self.pk)


class ContactType(models.TextChoices):
    PHONE = "phone", "Phone"
    EMAIL = "email", "Email"


class ParticipantIdentity(models.Model):
    participant = models.OneToOneField(
        Participant,
        primary_key=True,
        on_delete=models.CASCADE,
        related_name="identity",
    )
    activity = models.ForeignKey(
        ActivityEdition,
        on_delete=models.PROTECT,
        related_name="participant_identities",
    )
    display_name = models.CharField(max_length=100)
    contact_type = models.CharField(max_length=8, choices=ContactType.choices)
    identifier_envelope = models.JSONField()
    contact_envelope = models.JSONField()
    identifier_digest = models.CharField(max_length=64)
    contact_digest = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=("activity", "display_name"), name="identity_activity_display"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=("activity", "identifier_digest"),
                name="identity_identifier_unique_per_activity",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        if self.participant_id and self.activity_id != self.participant.activity_id:
            raise ValidationError({"activity": "Identity and participant must share an activity."})

    def __str__(self) -> str:
        return self.display_name


class QuestionIdentity(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    stable_code = models.CharField(max_length=80, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.stable_code


class ReviewStatus(models.TextChoices):
    PENDING = "pending", "Pending"
    VERIFIED = "verified", "Verified"
    REJECTED = "rejected", "Rejected"


class QuestionBankVersion(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    version_code = models.CharField(max_length=100, unique=True)
    title = models.CharField(max_length=200)
    format_version = models.PositiveSmallIntegerField(default=1)
    source_label = models.CharField(max_length=200)
    content_sha256 = models.CharField(max_length=64)
    finalized_at = models.DateTimeField(null=True, blank=True)
    imported_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("version_code", "content_sha256"),
                name="bank_version_content_unique",
            ),
        ]

    def save(self, *args, **kwargs) -> None:
        if self.pk:
            original = type(self).objects.filter(pk=self.pk).first()
            if original and original.finalized_at:
                immutable_fields = (
                    "version_code",
                    "title",
                    "format_version",
                    "source_label",
                    "content_sha256",
                    "finalized_at",
                )
                changed = any(
                    getattr(self, field) != getattr(original, field)
                    for field in immutable_fields
                )
                if changed:
                    raise ValidationError("A finalized question bank version is immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self.finalized_at:
            raise ValidationError("A finalized question bank version cannot be deleted.")
        return super().delete(*args, **kwargs)

    def __str__(self) -> str:
        return self.version_code

    @property
    def is_finalized(self) -> bool:
        return self.finalized_at is not None


class QuestionType(models.TextChoices):
    SINGLE_CHOICE = "single_choice", "Single choice"
    FILL_BLANK = "fill_blank", "Fill blank"


class FrozenBankContentModel(models.Model):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs) -> None:
        if self._bank_is_finalized():
            raise ValidationError("Content in a finalized question bank is immutable.")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        if self._bank_is_finalized():
            raise ValidationError("Content in a finalized question bank is immutable.")
        return super().delete(*args, **kwargs)

    def _bank_is_finalized(self) -> bool:
        return bool(self.bank_id and self.bank.finalized_at)


class BankQuestion(FrozenBankContentModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    bank = models.ForeignKey(
        QuestionBankVersion,
        on_delete=models.CASCADE,
        related_name="questions",
    )
    identity = models.ForeignKey(
        QuestionIdentity,
        on_delete=models.PROTECT,
        related_name="versions",
    )
    category_key = models.SlugField(max_length=80)
    pool_key = models.SlugField(max_length=80)
    question_type = models.CharField(max_length=20, choices=QuestionType.choices)
    prompt = models.TextField()
    option_a = models.TextField(blank=True)
    option_b = models.TextField(blank=True)
    option_c = models.TextField(blank=True)
    option_d = models.TextField(blank=True)
    correct_option = models.CharField(max_length=1, blank=True)
    acceptable_answers = models.JSONField(default=list, blank=True)
    origin = models.CharField(max_length=100)
    prior_event_use = models.BooleanField(default=False)
    review_status = models.CharField(
        max_length=16,
        choices=ReviewStatus.choices,
        default=ReviewStatus.PENDING,
    )
    private_event_allowed = models.BooleanField(default=True)
    public_release_allowed = models.BooleanField(default=False)
    source_reference = models.TextField()
    source_license = models.TextField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("bank", "identity"),
                name="bank_question_identity_unique",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        if self.question_type == QuestionType.SINGLE_CHOICE:
            if self.correct_option not in {"A", "B", "C", "D"}:
                raise ValidationError(
                    {"correct_option": "Single-choice answer must be A, B, C or D."}
                )
            if self.acceptable_answers:
                raise ValidationError(
                    {"acceptable_answers": "Single-choice questions cannot use text answers."}
                )
            if not all((self.option_a, self.option_b, self.option_c, self.option_d)):
                raise ValidationError("Single-choice questions require options A through D.")
        elif self.question_type == QuestionType.FILL_BLANK:
            if self.correct_option:
                raise ValidationError(
                    {"correct_option": "Fill-blank questions cannot use a choice answer."}
                )
            if not self.acceptable_answers:
                raise ValidationError(
                    {"acceptable_answers": "Fill-blank questions require accepted answers."}
                )

    def __str__(self) -> str:
        return f"{self.bank.version_code}:{self.identity.stable_code}"


class QuestionAsset(FrozenBankContentModel):
    bank = models.ForeignKey(
        QuestionBankVersion,
        on_delete=models.CASCADE,
        related_name="assets",
    )
    question = models.ForeignKey(
        BankQuestion,
        on_delete=models.CASCADE,
        related_name="assets",
    )
    relative_path = models.CharField(max_length=500)
    content_sha256 = models.CharField(max_length=64)
    mime_type = models.CharField(max_length=100)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("question", "relative_path"),
                name="question_asset_path_unique",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        if self.question_id and self.bank_id != self.question.bank_id:
            raise ValidationError({"bank": "Asset and question must share a bank version."})

    def __str__(self) -> str:
        return self.relative_path


class SamplingRule(FrozenBankContentModel):
    bank = models.ForeignKey(
        QuestionBankVersion,
        on_delete=models.CASCADE,
        related_name="sampling_rules",
    )
    category_key = models.SlugField(max_length=80)
    pool_key = models.SlugField(max_length=80)
    default_quota = models.PositiveSmallIntegerField()
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("bank", "category_key", "pool_key"),
                name="sampling_rule_pool_unique",
            ),
            models.CheckConstraint(
                condition=Q(default_quota__gt=0),
                name="sampling_rule_quota_positive",
            ),
        ]
        ordering = ("category_key", "display_order", "pool_key")

    def __str__(self) -> str:
        return f"{self.category_key}:{self.pool_key}={self.default_quota}"


class ActivityCategoryConfig(models.Model):
    activity = models.ForeignKey(
        ActivityEdition,
        on_delete=models.CASCADE,
        related_name="category_configs",
    )
    category_key = models.SlugField(max_length=80)
    title = models.CharField(max_length=100)
    question_count = models.PositiveSmallIntegerField(null=True, blank=True)
    time_limit_seconds = models.PositiveIntegerField(null=True, blank=True)
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("activity", "category_key"),
                name="activity_category_unique",
            ),
            models.CheckConstraint(
                condition=Q(question_count__isnull=True) | Q(question_count__gt=0),
                name="category_question_count_positive",
            ),
            models.CheckConstraint(
                condition=Q(time_limit_seconds__isnull=True) | Q(time_limit_seconds__gt=0),
                name="category_time_limit_positive",
            ),
        ]
        ordering = ("display_order", "category_key")

    @property
    def effective_question_count(self) -> int:
        return self.question_count or self.activity.default_question_count

    @property
    def effective_time_limit_seconds(self) -> int:
        return self.time_limit_seconds or self.activity.default_time_limit_seconds

    def validate_pool_quotas(self) -> None:
        quotas = list(self.pool_quotas.all())
        if self.question_count is not None and not quotas:
            raise ValidationError("A question-count override requires complete pool quotas.")
        if quotas and sum(quota.quota for quota in quotas) != self.effective_question_count:
            raise ValidationError("Pool quotas must total the category question count.")

    def __str__(self) -> str:
        return f"{self.activity.slug}:{self.category_key}"


class PoolQuota(models.Model):
    category_config = models.ForeignKey(
        ActivityCategoryConfig,
        on_delete=models.CASCADE,
        related_name="pool_quotas",
    )
    pool_key = models.SlugField(max_length=80)
    quota = models.PositiveSmallIntegerField()
    display_order = models.PositiveSmallIntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("category_config", "pool_key"),
                name="category_pool_quota_unique",
            ),
            models.CheckConstraint(
                condition=Q(quota__gt=0),
                name="category_pool_quota_positive",
            ),
        ]
        ordering = ("display_order", "pool_key")

    def __str__(self) -> str:
        return f"{self.category_config}:{self.pool_key}={self.quota}"


class ActivityBankActivation(models.Model):
    activity = models.ForeignKey(
        ActivityEdition,
        on_delete=models.PROTECT,
        related_name="bank_activations",
    )
    bank = models.ForeignKey(
        QuestionBankVersion,
        on_delete=models.PROTECT,
        related_name="activity_activations",
    )
    activated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="quiz_bank_activations",
    )
    activated_at = models.DateTimeField(auto_now_add=True)
    reason = models.TextField()
    legacy_exception = models.BooleanField(default=False)
    is_current = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("activity",),
                condition=Q(is_current=True),
                name="one_current_bank_per_activity",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        if not self.bank.finalized_at:
            raise ValidationError({"bank": "Only a finalized question bank can be activated."})
        questions = self.bank.questions.all()
        if questions.filter(private_event_allowed=False).exists():
            raise ValidationError({"bank": "Every active question must allow private-event use."})
        if self.legacy_exception:
            if not self.activated_by_id:
                raise ValidationError(
                    {"activated_by": "A legacy exception requires an approving administrator."}
                )
            disallowed = questions.exclude(
                origin="legacy_2024",
                prior_event_use=True,
                review_status=ReviewStatus.PENDING,
            ) | questions.filter(public_release_allowed=True)
            if disallowed.exists():
                raise ValidationError(
                    {
                        "legacy_exception": (
                            "Legacy exceptions only cover pending, private legacy questions."
                        )
                    }
                )
            if not self.reason.strip():
                raise ValidationError({"reason": "A legacy exception requires an approval reason."})
        elif questions.exclude(review_status=ReviewStatus.VERIFIED).exists():
            raise ValidationError({"bank": "Every active question must be verified."})
        for category in self.activity.category_configs.all():
            category.validate_pool_quotas()

    def __str__(self) -> str:
        return f"{self.activity.slug}:{self.bank.version_code}"


class AttemptStatus(models.TextChoices):
    IN_PROGRESS = "in_progress", "In progress"
    SUBMITTED = "submitted", "Submitted"
    TIMED_OUT = "timed_out", "Timed out"
    INVALID = "invalid", "Invalid"


class QuizAttempt(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    activity = models.ForeignKey(
        ActivityEdition,
        on_delete=models.PROTECT,
        related_name="attempts",
    )
    participant = models.ForeignKey(
        Participant,
        on_delete=models.PROTECT,
        related_name="attempts",
    )
    category_config = models.ForeignKey(
        ActivityCategoryConfig,
        on_delete=models.PROTECT,
        related_name="attempts",
    )
    bank = models.ForeignKey(
        QuestionBankVersion,
        on_delete=models.PROTECT,
        related_name="attempts",
    )
    status = models.CharField(
        max_length=16,
        choices=AttemptStatus.choices,
        default=AttemptStatus.IN_PROGRESS,
    )
    started_at = models.DateTimeField()
    deadline_at = models.DateTimeField()
    submitted_at = models.DateTimeField(null=True, blank=True)
    question_count = models.PositiveSmallIntegerField()
    score = models.PositiveSmallIntegerField(default=0)
    submission_digest = models.CharField(max_length=64, blank=True)
    reward_text = models.TextField(blank=True)
    invalidated_at = models.DateTimeField(null=True, blank=True)
    invalidation_reason = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(
                fields=("activity", "status", "created_at"),
                name="attempt_act_status_created",
            ),
            models.Index(
                fields=("activity", "category_config", "status", "submitted_at"),
                name="attempt_act_cat_status",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=("participant",),
                condition=Q(status=AttemptStatus.IN_PROGRESS),
                name="one_in_progress_attempt_per_participant",
            ),
            models.CheckConstraint(
                condition=Q(question_count__gt=0),
                name="attempt_question_count_positive",
            ),
            models.CheckConstraint(
                condition=Q(score__gte=0) & Q(score__lte=F("question_count")),
                name="attempt_score_in_range",
            ),
            models.CheckConstraint(
                condition=Q(deadline_at__gt=F("started_at")),
                name="attempt_deadline_after_start",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        if self.participant_id and self.activity_id != self.participant.activity_id:
            raise ValidationError({"activity": "Attempt and participant must share an activity."})
        if self.category_config_id and self.activity_id != self.category_config.activity_id:
            raise ValidationError(
                {"category_config": "Attempt category belongs to another activity."}
            )

    def __str__(self) -> str:
        return str(self.pk)


class AttemptItem(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    attempt = models.ForeignKey(
        QuizAttempt,
        on_delete=models.CASCADE,
        related_name="items",
    )
    question = models.ForeignKey(
        BankQuestion,
        on_delete=models.PROTECT,
        related_name="attempt_items",
    )
    display_order = models.PositiveSmallIntegerField()
    option_order = models.JSONField(default=list, blank=True)
    submitted_answer = models.JSONField(null=True, blank=True)
    is_correct = models.BooleanField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=("attempt", "display_order"),
                name="attempt_item_display_order_unique",
            ),
            models.UniqueConstraint(
                fields=("attempt", "question"),
                name="attempt_question_unique",
            ),
        ]
        ordering = ("display_order",)

    def clean(self) -> None:
        super().clean()
        if self.attempt_id and self.question_id and self.attempt.bank_id != self.question.bank_id:
            raise ValidationError({"question": "Attempt item must use the attempt bank version."})

    def __str__(self) -> str:
        return str(self.pk)


class CategoryHighScore(models.Model):
    activity = models.ForeignKey(
        ActivityEdition,
        on_delete=models.CASCADE,
        related_name="category_high_scores",
    )
    participant = models.ForeignKey(
        Participant,
        on_delete=models.CASCADE,
        related_name="category_high_scores",
    )
    category_config = models.ForeignKey(
        ActivityCategoryConfig,
        on_delete=models.CASCADE,
        related_name="high_scores",
    )
    score = models.PositiveSmallIntegerField()
    achieved_at = models.DateTimeField()
    source_attempt = models.ForeignKey(
        QuizAttempt,
        on_delete=models.PROTECT,
        related_name="high_score_records",
    )
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(
                fields=("activity", "category_config", "-score", "achieved_at"),
                name="high_score_activity_category",
            ),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=("participant", "category_config"),
                name="participant_category_high_score_unique",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        if self.participant_id and self.activity_id != self.participant.activity_id:
            raise ValidationError(
                {"activity": "High score and participant must share an activity."}
            )
        if self.category_config_id and self.activity_id != self.category_config.activity_id:
            raise ValidationError(
                {"category_config": "High score category belongs to another activity."}
            )
        if self.source_attempt_id:
            if self.source_attempt.participant_id != self.participant_id:
                raise ValidationError(
                    {"source_attempt": "High score attempt belongs to another participant."}
                )
            if self.source_attempt.category_config_id != self.category_config_id:
                raise ValidationError(
                    {"source_attempt": "High score attempt belongs to another category."}
                )
            if self.score != self.source_attempt.score:
                raise ValidationError({"score": "High score must equal the source attempt score."})

    def __str__(self) -> str:
        return f"{self.participant_id}:{self.category_config_id}={self.score}"


class RewardRule(models.Model):
    activity = models.ForeignKey(
        ActivityEdition,
        on_delete=models.CASCADE,
        related_name="reward_rules",
    )
    category_config = models.ForeignKey(
        ActivityCategoryConfig,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="reward_rules",
    )
    min_score_rate = models.DecimalField(max_digits=5, decimal_places=4)
    max_score_rate = models.DecimalField(max_digits=5, decimal_places=4)
    priority = models.IntegerField(default=0)
    text = models.CharField(max_length=300)
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=Q(min_score_rate__gte=0) & Q(min_score_rate__lte=1),
                name="reward_min_rate_in_range",
            ),
            models.CheckConstraint(
                condition=Q(max_score_rate__gte=0) & Q(max_score_rate__lte=1),
                name="reward_max_rate_in_range",
            ),
            models.CheckConstraint(
                condition=Q(min_score_rate__lte=F("max_score_rate")),
                name="reward_rate_ordered",
            ),
            models.UniqueConstraint(
                fields=("activity", "category_config", "priority"),
                name="reward_rule_priority_unique",
            ),
        ]

    def clean(self) -> None:
        super().clean()
        if self.category_config_id and self.activity_id != self.category_config.activity_id:
            raise ValidationError(
                {"category_config": "Reward category belongs to another activity."}
            )

    def __str__(self) -> str:
        return self.text


class AdminAuditLog(models.Model):
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="quiz_audit_logs",
    )
    activity = models.ForeignKey(
        ActivityEdition,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    participant = models.ForeignKey(
        Participant,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="audit_logs",
    )
    action = models.CharField(max_length=100)
    reason = models.TextField()
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=("activity", "-created_at"), name="audit_activity_created"),
            models.Index(fields=("participant", "-created_at"), name="audit_participant_created"),
        ]
        ordering = ("-created_at", "-pk")

    def __str__(self) -> str:
        return self.action
