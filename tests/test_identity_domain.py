import base64
import json
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import IntegrityError
from django.utils import timezone

from quiz.identity import (
    IdentityDecryptionError,
    IdentityProtector,
    ParticipantRecoveryRequired,
    deidentify_participant,
    normalize_contact,
    normalize_identifier,
    register_or_resume,
)
from quiz.models import (
    ActivityBankActivation,
    ActivityCategoryConfig,
    ActivityEdition,
    ActivityStatus,
    AdminAuditLog,
    AttemptStatus,
    BankQuestion,
    ParticipantIdentity,
    PoolQuota,
    QuestionBankVersion,
    QuestionIdentity,
    QuestionType,
    QuizAttempt,
    ReviewStatus,
)
from quiz.services import select_participant_entry, transition_activity


@pytest.fixture
def protector():
    return IdentityProtector(
        keys={"test-v1": b"k" * 32},
        active_key_id="test-v1",
        hmac_key=b"h" * 32,
    )


@pytest.mark.django_db
def test_operator_can_select_one_participant_entry_with_an_audit_record():
    actor = get_user_model().objects.create_user(username="operator")
    previous = ActivityEdition.objects.create(
        slug="autumn-2026",
        title="2026 秋季游园会",
        status=ActivityStatus.CLOSED,
        is_participant_entry=True,
    )
    candidate = ActivityEdition.objects.create(
        slug="spring-2027",
        title="2027 春季游园会",
        status=ActivityStatus.DRAFT,
    )

    selected = select_participant_entry(
        activity=candidate,
        actor=actor,
        reason="筹备下一届入口",
    )

    previous.refresh_from_db()
    assert selected.is_participant_entry is True
    assert previous.is_participant_entry is False
    audit = AdminAuditLog.objects.get(action="participant_entry_selected")
    assert audit.actor == actor
    assert audit.activity == candidate
    assert audit.reason == "筹备下一届入口"


@pytest.mark.django_db
def test_only_the_selected_participant_entry_can_open():
    actor = get_user_model().objects.create_user(username="operator")
    activity = ActivityEdition.objects.create(
        slug="autumn-2026",
        title="2026 秋季游园会",
        status=ActivityStatus.DRAFT,
    )

    with pytest.raises(ValidationError, match="参与者入口"):
        transition_activity(
            activity=activity,
            next_status=ActivityStatus.OPEN,
            actor=actor,
            reason="开放现场入口",
        )


@pytest.mark.django_db
def test_archiving_the_participant_entry_clears_the_public_selection():
    actor = get_user_model().objects.create_user(username="operator")
    activity = ActivityEdition.objects.create(
        slug="autumn-2026",
        title="2026 秋季游园会",
        status=ActivityStatus.CLOSED,
        is_participant_entry=True,
    )

    archived = transition_activity(
        activity=activity,
        next_status=ActivityStatus.ARCHIVED,
        actor=actor,
        reason="活动资料已经封存",
    )

    assert archived.status == ActivityStatus.ARCHIVED
    assert archived.is_participant_entry is False


@pytest.mark.django_db
def test_participant_can_resume_with_normalized_identifier_and_contact(protector):
    activity = ActivityEdition.objects.create(
        slug="autumn-2026", title="2026 秋季游园会", status=ActivityStatus.OPEN
    )

    first = register_or_resume(
        activity=activity,
        display_name="Yuriko",
        identifier=" pb24000001 ",
        contact="138 0013 8000",
        protector=protector,
    )
    resumed = register_or_resume(
        activity=activity,
        display_name="新的显示名不会覆盖登记资料",
        identifier="PB24000001",
        contact="+86 138 0013 8000",
        protector=protector,
    )

    assert first.created is True
    assert resumed.created is False
    assert resumed.participant == first.participant
    assert resumed.participant.identity.display_name == "Yuriko"
    assert resumed.participant.identity.contact_type == "phone"


def test_identity_ciphertext_authenticates_its_context(protector):
    envelope = protector.encrypt("PB24000001", context="participant:one:identifier")

    assert protector.decrypt(envelope, context="participant:one:identifier") == "PB24000001"

    tampered = {**envelope, "ciphertext": envelope["ciphertext"][:-2] + "AA"}
    with pytest.raises(IdentityDecryptionError):
        protector.decrypt(tampered, context="participant:one:identifier")
    with pytest.raises(IdentityDecryptionError):
        protector.decrypt(envelope, context="participant:two:identifier")


def test_identity_digest_isolated_between_activities(protector):
    first = protector.digest("PB24000001", activity_id="activity-one", field="identifier")
    second = protector.digest("PB24000001", activity_id="activity-two", field="identifier")

    assert first != second


@pytest.mark.django_db
def test_identity_envelopes_can_be_rotated_without_changing_plaintext(protector):
    activity = ActivityEdition.objects.create(
        slug="autumn-2026", title="2026 秋季游园会", status=ActivityStatus.OPEN
    )
    participant = register_or_resume(
        activity=activity,
        display_name="Yuriko",
        identifier="PB24000001",
        contact="13800138000",
        protector=protector,
    ).participant
    rotated = IdentityProtector(
        keys={"test-v1": b"k" * 32, "test-v2": b"n" * 32},
        active_key_id="test-v2",
        hmac_key=b"h" * 32,
    )

    assert rotated.rotate_identity(participant.identity) is True

    participant.identity.refresh_from_db()
    assert participant.identity.identifier_envelope["key_id"] == "test-v2"
    assert (
        rotated.decrypt(
            participant.identity.identifier_envelope,
            context=f"participant:{participant.pk}:identifier",
        )
        == "PB24000001"
    )


@pytest.mark.django_db
def test_rotate_identity_keys_command_uses_the_active_environment_key(protector, monkeypatch):
    activity = ActivityEdition.objects.create(
        slug="autumn-2026", title="2026 秋季游园会", status=ActivityStatus.OPEN
    )
    identity = register_or_resume(
        activity=activity,
        display_name="Yuriko",
        identifier="PB24000001",
        contact="13800138000",
        protector=protector,
    ).participant.identity
    monkeypatch.setenv(
        "QUIZ_IDENTITY_KEYS",
        json.dumps(
            {
                "test-v1": base64.b64encode(b"k" * 32).decode("ascii"),
                "test-v2": base64.b64encode(b"n" * 32).decode("ascii"),
            }
        ),
    )
    monkeypatch.setenv("QUIZ_IDENTITY_ACTIVE_KEY_ID", "test-v2")
    monkeypatch.setenv(
        "QUIZ_IDENTITY_HMAC_KEY",
        base64.b64encode(b"h" * 32).decode("ascii"),
    )

    call_command("rotate_identity_keys")

    identity.refresh_from_db()
    assert identity.identifier_envelope["key_id"] == "test-v2"
    assert identity.contact_envelope["key_id"] == "test-v2"


def test_contact_and_identifier_normalization():
    assert normalize_identifier(" ｐｂ２４０００００１ ") == "PB24000001"
    assert normalize_contact("138 0013 8000") == "+8613800138000"
    assert normalize_contact(" Person@Example.COM ") == "Person@example.com"

    with pytest.raises(ValidationError):
        normalize_identifier("PB 24000001")


@pytest.mark.django_db
def test_existing_identifier_with_different_contact_requires_recovery(protector):
    activity = ActivityEdition.objects.create(
        slug="autumn-2026", title="2026 秋季游园会", status=ActivityStatus.OPEN
    )
    register_or_resume(
        activity=activity,
        display_name="Yuriko",
        identifier="PB24000001",
        contact="13800138000",
        protector=protector,
    )

    with pytest.raises(ParticipantRecoveryRequired):
        register_or_resume(
            activity=activity,
            display_name="Yuriko",
            identifier="PB24000001",
            contact="13800138001",
            protector=protector,
        )


@pytest.mark.django_db
def test_deidentification_removes_identity_but_keeps_participant_and_audit_fact(protector):
    activity = ActivityEdition.objects.create(
        slug="autumn-2026", title="2026 秋季游园会", status=ActivityStatus.OPEN
    )
    registration = register_or_resume(
        activity=activity,
        display_name="Yuriko",
        identifier="PB24000001",
        contact="13800138000",
        protector=protector,
    )

    assert deidentify_participant(
        participant=registration.participant,
        reason="参与者请求删除身份资料",
    )

    registration.participant.refresh_from_db()
    assert registration.participant.anonymized_at is not None
    assert not ParticipantIdentity.objects.filter(participant=registration.participant).exists()
    audit = AdminAuditLog.objects.get(participant=registration.participant)
    assert audit.action == "participant_deidentified"
    assert audit.reason == "参与者请求删除身份资料"


@pytest.mark.django_db
def test_activity_status_transitions_reject_reopening_a_closed_activity():
    activity = ActivityEdition.objects.create(slug="autumn-2026", title="2026 秋季游园会")

    activity.transition_to(ActivityStatus.OPEN)
    activity.transition_to(ActivityStatus.CLOSED)

    with pytest.raises(ValidationError):
        activity.transition_to(ActivityStatus.OPEN)


@pytest.mark.django_db
def test_category_override_requires_complete_pool_quotas():
    activity = ActivityEdition.objects.create(slug="autumn-2026", title="2026 秋季游园会")
    category = ActivityCategoryConfig.objects.create(
        activity=activity,
        category_key="language",
        title="语言",
        question_count=5,
    )

    with pytest.raises(ValidationError):
        category.validate_pool_quotas()

    PoolQuota.objects.create(category_config=category, pool_key="words", quota=2)
    PoolQuota.objects.create(category_config=category, pool_key="grammar", quota=3)
    category.validate_pool_quotas()


@pytest.mark.django_db
def test_finalized_question_bank_and_its_questions_are_immutable():
    bank = QuestionBankVersion.objects.create(
        version_code="2026-autumn-v1",
        title="2026 秋季题库",
        source_label="internal",
        content_sha256="a" * 64,
    )
    identity = QuestionIdentity.objects.create(stable_code="Q-0001")
    question = BankQuestion.objects.create(
        bank=bank,
        identity=identity,
        category_key="language",
        pool_key="words",
        question_type=QuestionType.SINGLE_CHOICE,
        prompt="问题",
        option_a="A",
        option_b="B",
        option_c="C",
        option_d="D",
        correct_option="A",
        origin="original",
        source_reference="internal:Q-0001",
    )
    bank.finalized_at = timezone.now()
    bank.save(update_fields=("finalized_at",))

    bank.title = "被修改"
    with pytest.raises(ValidationError):
        bank.save()
    question.prompt = "被修改"
    with pytest.raises(ValidationError):
        question.save()


@pytest.mark.django_db(transaction=True)
def test_participant_has_at_most_one_in_progress_attempt(protector):
    activity = ActivityEdition.objects.create(
        slug="autumn-2026", title="2026 秋季游园会", status=ActivityStatus.OPEN
    )
    category = ActivityCategoryConfig.objects.create(
        activity=activity,
        category_key="language",
        title="语言",
    )
    bank = QuestionBankVersion.objects.create(
        version_code="2026-autumn-v1",
        title="2026 秋季题库",
        source_label="internal",
        content_sha256="a" * 64,
    )
    participant = register_or_resume(
        activity=activity,
        display_name="Yuriko",
        identifier="PB24000001",
        contact="13800138000",
        protector=protector,
    ).participant
    started_at = timezone.now()
    attempt_data = {
        "activity": activity,
        "participant": participant,
        "category_config": category,
        "bank": bank,
        "status": AttemptStatus.IN_PROGRESS,
        "started_at": started_at,
        "deadline_at": started_at + timedelta(minutes=5),
        "question_count": 15,
    }
    QuizAttempt.objects.create(**attempt_data)

    with pytest.raises(IntegrityError):
        QuizAttempt.objects.create(**attempt_data)


@pytest.mark.django_db
def test_pending_legacy_bank_requires_explicit_private_exception():
    activity = ActivityEdition.objects.create(slug="autumn-2026", title="2026 秋季游园会")
    bank = QuestionBankVersion.objects.create(
        version_code="legacy-2024",
        title="2024 往届题库",
        source_label="legacy",
        content_sha256="a" * 64,
    )
    identity = QuestionIdentity.objects.create(stable_code="LEGACY2024-0001")
    BankQuestion.objects.create(
        bank=bank,
        identity=identity,
        category_key="language",
        pool_key="words",
        question_type=QuestionType.SINGLE_CHOICE,
        prompt="问题",
        option_a="A",
        option_b="B",
        option_c="C",
        option_d="D",
        correct_option="A",
        origin="legacy_2024",
        prior_event_use=True,
        review_status=ReviewStatus.PENDING,
        private_event_allowed=True,
        public_release_allowed=False,
        source_reference="legacy row 2",
        source_license="unreviewed",
    )
    bank.finalized_at = timezone.now()
    bank.save(update_fields=("finalized_at",))

    ordinary = ActivityBankActivation(
        activity=activity,
        bank=bank,
        reason="普通启用",
    )
    with pytest.raises(ValidationError):
        ordinary.full_clean()

    approver = get_user_model().objects.create_user(username="approver")
    exception = ActivityBankActivation(
        activity=activity,
        bank=bank,
        activated_by=approver,
        reason="已人工批准仅用于本次私密活动",
        legacy_exception=True,
    )
    exception.full_clean()
