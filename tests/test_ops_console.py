import csv
import io
import json
from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.management import call_command
from django.utils import timezone

from quiz.identity import register_or_resume
from quiz.models import (
    ActivityBankActivation,
    ActivityCategoryConfig,
    ActivityEdition,
    ActivityStatus,
    AdminAuditLog,
    CategoryHighScore,
    Participant,
    QuestionBankVersion,
    QuizAttempt,
    RewardRule,
)


def login_operator(client, username="operator"):
    operator = get_user_model().objects.create_user(
        username=username,
        password="test-password",
        is_staff=True,
    )
    operator.user_permissions.add(Permission.objects.get(codename="operate_quiz"))
    client.force_login(operator)
    return operator


@pytest.mark.django_db
def test_ops_console_requires_the_single_operator_permission(client):
    anonymous = client.get("/ops/")
    staff_without_permission = get_user_model().objects.create_user(
        username="staff-only",
        password="test-password",
        is_staff=True,
    )
    client.force_login(staff_without_permission)
    forbidden = client.get("/ops/")
    operator = get_user_model().objects.create_user(
        username="operator",
        password="test-password",
        is_staff=True,
    )
    operator.user_permissions.add(Permission.objects.get(codename="operate_quiz"))
    client.force_login(operator)
    allowed = client.get("/ops/")

    assert anonymous.status_code == 302
    assert anonymous.url.startswith("/ops/login/")
    assert forbidden.status_code == 403
    assert allowed.status_code == 200
    assert "现场管理控制台" in allowed.content.decode("utf-8")


@pytest.mark.django_db
def test_provision_quiz_operator_creates_a_non_superuser_with_one_console_permission():
    call_command("provision_quiz_operator", "field-operator")

    operator = get_user_model().objects.get(username="field-operator")
    assert operator.is_staff is True
    assert operator.is_superuser is False
    assert operator.has_usable_password() is False
    assert operator.has_perm("quiz.operate_quiz") is True


@pytest.mark.django_db
def test_ops_login_locks_one_username_and_client_after_five_failures(client):
    operator = get_user_model().objects.create_user(
        username="operator",
        password="correct-password",
        is_staff=True,
    )
    operator.user_permissions.add(Permission.objects.get(codename="operate_quiz"))

    responses = [
        client.post(
            "/ops/login/",
            {"username": "operator", "password": "wrong-password"},
        )
        for _ in range(5)
    ]
    locked = client.post(
        "/ops/login/",
        {"username": "operator", "password": "correct-password"},
    )

    assert all(response.status_code == 200 for response in responses[:4])
    assert responses[4].status_code == 429
    assert locked.status_code == 429


@pytest.mark.django_db
def test_ops_snapshot_reports_activity_counts_without_identity_data(client):
    login_operator(client)
    activity = ActivityEdition.objects.create(
        slug="autumn-2026",
        title="2026 秋季游园会",
        status=ActivityStatus.OPEN,
        is_participant_entry=True,
    )
    category = ActivityCategoryConfig.objects.create(
        activity=activity,
        category_key="language",
        title="语言",
    )
    bank = QuestionBankVersion.objects.create(
        version_code="ops-v1",
        title="现场题库",
        source_label="test",
        content_sha256="a" * 64,
        finalized_at=timezone.now(),
    )
    first = Participant.objects.create(activity=activity)
    second = Participant.objects.create(activity=activity)
    now = timezone.now()
    QuizAttempt.objects.create(
        activity=activity,
        participant=first,
        category_config=category,
        bank=bank,
        status="submitted",
        started_at=now,
        deadline_at=now + timedelta(minutes=5),
        submitted_at=now,
        question_count=2,
        score=1,
    )
    QuizAttempt.objects.create(
        activity=activity,
        participant=second,
        category_config=category,
        bank=bank,
        status="timed_out",
        started_at=now,
        deadline_at=now + timedelta(minutes=5),
        question_count=2,
    )

    response = client.get(f"/ops/activities/{activity.pk}/snapshot/")

    assert response.status_code == 200
    assert response.json() == {
        "activity_id": str(activity.pk),
        "status": "open",
        "participants": 2,
        "attempts": {
            "in_progress": 0,
            "submitted": 1,
            "timed_out": 1,
            "invalid": 0,
        },
        "last_submission_at": now.isoformat().replace("+00:00", "Z"),
    }
    assert "display_name" not in response.content.decode("utf-8")


@pytest.mark.django_db
def test_ops_participant_search_returns_masked_identity_by_default(client):
    login_operator(client)
    activity = ActivityEdition.objects.create(
        slug="autumn-2026",
        title="2026 秋季游园会",
        status=ActivityStatus.OPEN,
        is_participant_entry=True,
    )
    registration = register_or_resume(
        activity=activity,
        display_name="示例昵称",
        identifier="PB24000001",
        contact="13800138000",
    )

    response = client.post(
        f"/ops/activities/{activity.pk}/participants/search/",
        {"display_name": "示例"},
    )

    assert response.status_code == 200
    assert response.json()["participants"] == [
        {
            "id": str(registration.participant.pk),
            "display_name": "示例昵称",
            "identifier": "PB******01",
            "contact": "+86*******8000",
            "contact_type": "phone",
            "anonymized": False,
            "attempt_count": 0,
            "last_attempt_at": None,
        }
    ]
    body = response.content.decode("utf-8")
    assert "PB24000001" not in body
    assert "+8613800138000" not in body


@pytest.mark.django_db
def test_ops_participant_search_matches_exact_normalized_identifier_without_logging_plaintext(
    client,
):
    login_operator(client)
    activity = ActivityEdition.objects.create(
        slug="autumn-2026",
        title="2026 秋季游园会",
        status=ActivityStatus.OPEN,
        is_participant_entry=True,
    )
    expected = register_or_resume(
        activity=activity,
        display_name="目标参与者",
        identifier="PB24000001",
        contact="13800138000",
    )
    register_or_resume(
        activity=activity,
        display_name="其他参与者",
        identifier="PB24000002",
        contact="13800138001",
    )

    response = client.post(
        f"/ops/activities/{activity.pk}/participants/search/",
        {"identifier": " pb24000001 "},
    )

    assert response.status_code == 200
    assert [row["id"] for row in response.json()["participants"]] == [
        str(expected.participant.pk)
    ]
    assert "PB24000001" not in response.content.decode("utf-8")


@pytest.mark.django_db
def test_ops_participant_search_matches_normalized_contact_or_internal_id(client):
    login_operator(client)
    activity = ActivityEdition.objects.create(
        slug="autumn-2026-contact-search",
        title="2026 秋季游园会",
        status=ActivityStatus.OPEN,
        is_participant_entry=True,
    )
    expected = register_or_resume(
        activity=activity,
        display_name="联系方式目标",
        identifier="PB24000001",
        contact="13800138000",
    )
    register_or_resume(
        activity=activity,
        display_name="其他参与者",
        identifier="PB24000002",
        contact="13800138001",
    )

    by_contact = client.post(
        f"/ops/activities/{activity.pk}/participants/search/",
        {"contact": "+86 138 0013 8000"},
    )
    by_internal_id = client.post(
        f"/ops/activities/{activity.pk}/participants/search/",
        {"participant_id": str(expected.participant.pk)},
    )

    expected_ids = [str(expected.participant.pk)]
    assert [row["id"] for row in by_contact.json()["participants"]] == expected_ids
    assert [row["id"] for row in by_internal_id.json()["participants"]] == expected_ids


@pytest.mark.django_db
def test_ops_full_identity_reveal_is_page_scoped_and_audited_without_plaintext(client):
    actor = login_operator(client)
    activity = ActivityEdition.objects.create(
        slug="autumn-2026",
        title="2026 秋季游园会",
        status=ActivityStatus.OPEN,
        is_participant_entry=True,
    )
    registration = register_or_resume(
        activity=activity,
        display_name="示例昵称",
        identifier="PB24000001",
        contact="13800138000",
    )

    response = client.post(
        f"/ops/activities/{activity.pk}/participants/reveal/",
        {
            "participant_ids": [str(registration.participant.pk)],
            "fields": ["identifier", "contact"],
        },
    )

    assert response.status_code == 200
    assert response.json()["participants"] == {
        str(registration.participant.pk): {
            "identifier": "PB24000001",
            "contact": "+8613800138000",
        }
    }
    assert "no-store" in response["Cache-Control"]
    audit = AdminAuditLog.objects.get(action="participant_identity_revealed")
    assert audit.actor == actor
    assert audit.activity == activity
    assert audit.metadata == {"fields": ["contact", "identifier"], "row_count": 1}
    serialized_audit = json.dumps(audit.metadata, ensure_ascii=False)
    assert "PB24000001" not in serialized_audit
    assert "13800138000" not in serialized_audit


@pytest.mark.django_db
def test_identity_export_requires_a_reason_streams_safe_csv_and_records_only_metadata(client):
    actor = login_operator(client)
    activity = ActivityEdition.objects.create(
        slug="autumn-2026",
        title="2026 秋季游园会",
        status=ActivityStatus.OPEN,
        is_participant_entry=True,
    )
    registration = register_or_resume(
        activity=activity,
        display_name="=示例公式",
        identifier="+PB24000001",
        contact="person@example.com",
    )

    rejected = client.post(f"/ops/activities/{activity.pk}/exports/identities/")
    exported = client.post(
        f"/ops/activities/{activity.pk}/exports/identities/",
        {"reason": "现场核对登记信息"},
    )

    assert rejected.status_code == 400
    assert exported.status_code == 200
    assert exported.streaming is True
    assert "no-store" in exported["Cache-Control"]
    content = b"".join(exported.streaming_content).decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(content)))
    assert rows == [
        {
            "participant_id": str(registration.participant.pk),
            "display_name": "'=示例公式",
            "identifier": "'+PB24000001",
            "contact_type": "email",
            "contact": "person@example.com",
        }
    ]
    assert "identifier_envelope" not in content
    assert "identifier_digest" not in content
    audit = AdminAuditLog.objects.get(action="participant_identity_exported")
    assert audit.actor == actor
    assert audit.reason == "现场核对登记信息"
    assert audit.metadata == {"row_count": 1}
    assert "+PB24000001" not in json.dumps(audit.metadata)


@pytest.mark.django_db
def test_statistics_export_uses_internal_ids_and_never_contains_identity_fields(client):
    actor = login_operator(client)
    activity = ActivityEdition.objects.create(
        slug="autumn-2026",
        title="2026 秋季游园会",
        status=ActivityStatus.OPEN,
        is_participant_entry=True,
    )
    category = ActivityCategoryConfig.objects.create(
        activity=activity,
        category_key="language",
        title="语言",
    )
    bank = QuestionBankVersion.objects.create(
        version_code="ops-v1",
        title="现场题库",
        source_label="test",
        content_sha256="a" * 64,
        finalized_at=timezone.now(),
    )
    registration = register_or_resume(
        activity=activity,
        display_name="不可导出的昵称",
        identifier="PB24000001",
        contact="13800138000",
    )
    now = timezone.now()
    attempt = QuizAttempt.objects.create(
        activity=activity,
        participant=registration.participant,
        category_config=category,
        bank=bank,
        status="submitted",
        started_at=now,
        deadline_at=now + timedelta(minutes=5),
        submitted_at=now,
        question_count=2,
        score=1,
    )
    CategoryHighScore.objects.create(
        activity=activity,
        participant=registration.participant,
        category_config=category,
        score=1,
        achieved_at=now,
        source_attempt=attempt,
    )

    response = client.post(f"/ops/activities/{activity.pk}/exports/statistics/")

    assert response.status_code == 200
    content = b"".join(response.streaming_content).decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(content)))
    assert rows[0]["participant_id"] == str(registration.participant.pk)
    assert rows[0]["attempt_id"] == str(attempt.pk)
    assert rows[0]["category_high_score"] == "1"
    assert "不可导出的昵称" not in content
    assert "PB24000001" not in content
    assert "13800138000" not in content
    audit = AdminAuditLog.objects.get(action="participant_statistics_exported")
    assert audit.actor == actor
    assert audit.metadata == {"row_count": 1}


@pytest.mark.django_db
def test_ops_operator_can_select_the_participant_entry_with_a_reason(client):
    login_operator(client)
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

    response = client.post(
        f"/ops/activities/{candidate.pk}/participant-entry/",
        {"reason": "准备下一届参与者入口"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "activity_id": str(candidate.pk),
        "is_participant_entry": True,
    }
    previous.refresh_from_db()
    candidate.refresh_from_db()
    assert previous.is_participant_entry is False
    assert candidate.is_participant_entry is True


@pytest.mark.django_db
def test_ops_operator_can_apply_an_audited_activity_status_transition(client):
    actor = login_operator(client)
    activity = ActivityEdition.objects.create(
        slug="autumn-2026",
        title="2026 秋季游园会",
        status=ActivityStatus.DRAFT,
        is_participant_entry=True,
    )

    response = client.post(
        f"/ops/activities/{activity.pk}/status/",
        {"status": "open", "reason": "现场开始接待参与者"},
    )

    assert response.status_code == 200
    assert response.json() == {"activity_id": str(activity.pk), "status": "open"}
    activity.refresh_from_db()
    assert activity.status == ActivityStatus.OPEN
    audit = AdminAuditLog.objects.get(action="activity_status_changed")
    assert audit.actor == actor
    assert audit.reason == "现场开始接待参与者"


@pytest.mark.django_db
def test_ops_operator_can_switch_a_finalized_bank_only_while_paused(client):
    actor = login_operator(client)
    activity = ActivityEdition.objects.create(
        slug="autumn-2026",
        title="2026 秋季游园会",
        status=ActivityStatus.PAUSED,
        is_participant_entry=True,
    )
    previous = QuestionBankVersion.objects.create(
        version_code="ops-v1",
        title="第一版题库",
        source_label="test",
        content_sha256="a" * 64,
        finalized_at=timezone.now(),
    )
    replacement = QuestionBankVersion.objects.create(
        version_code="ops-v2",
        title="修正版题库",
        source_label="test",
        content_sha256="b" * 64,
        finalized_at=timezone.now(),
    )
    ActivityBankActivation.objects.create(
        activity=activity,
        bank=previous,
        activated_by=actor,
        reason="初始题库",
        is_current=True,
    )

    response = client.post(
        f"/ops/activities/{activity.pk}/question-bank/",
        {"bank_version": replacement.version_code, "reason": "现场纠正题库"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "activity_id": str(activity.pk),
        "bank_version": "ops-v2",
        "legacy_exception": False,
    }
    current_activation = ActivityBankActivation.objects.get(activity=activity, is_current=True)
    assert current_activation.bank == replacement
    audit = AdminAuditLog.objects.get(action="question_bank_activated")
    assert audit.actor == actor
    assert audit.reason == "现场纠正题库"


@pytest.mark.django_db
def test_ops_operator_can_invalidate_an_attempt_and_recompute_high_score(client):
    actor = login_operator(client)
    activity = ActivityEdition.objects.create(
        slug="autumn-2026",
        title="2026 秋季游园会",
        status=ActivityStatus.PAUSED,
        is_participant_entry=True,
    )
    category = ActivityCategoryConfig.objects.create(
        activity=activity,
        category_key="language",
        title="语言",
    )
    bank = QuestionBankVersion.objects.create(
        version_code="ops-v1",
        title="现场题库",
        source_label="test",
        content_sha256="a" * 64,
        finalized_at=timezone.now(),
    )
    participant = Participant.objects.create(activity=activity)
    now = timezone.now()
    attempt = QuizAttempt.objects.create(
        activity=activity,
        participant=participant,
        category_config=category,
        bank=bank,
        status="submitted",
        started_at=now,
        deadline_at=now + timedelta(minutes=5),
        submitted_at=now,
        question_count=2,
        score=2,
    )
    CategoryHighScore.objects.create(
        activity=activity,
        participant=participant,
        category_config=category,
        score=2,
        achieved_at=now,
        source_attempt=attempt,
    )

    response = client.post(
        f"/ops/attempts/{attempt.pk}/invalidate/",
        {"reason": "现场确认该记录无效"},
    )

    assert response.status_code == 200
    assert response.json() == {"attempt_id": str(attempt.pk), "status": "invalid"}
    attempt.refresh_from_db()
    assert attempt.status == "invalid"
    assert not CategoryHighScore.objects.filter(participant=participant).exists()
    audit = AdminAuditLog.objects.get(action="attempt_invalidated")
    assert audit.actor == actor


@pytest.mark.django_db
def test_ops_operator_can_deidentify_one_participant_with_a_reason(client):
    actor = login_operator(client)
    activity = ActivityEdition.objects.create(
        slug="autumn-2026",
        title="2026 秋季游园会",
        status=ActivityStatus.OPEN,
        is_participant_entry=True,
    )
    registration = register_or_resume(
        activity=activity,
        display_name="申请删除身份者",
        identifier="PB24000001",
        contact="13800138000",
    )
    activity.status = ActivityStatus.CLOSED
    activity.save(update_fields=("status", "updated_at"))

    response = client.post(
        f"/ops/participants/{registration.participant.pk}/deidentify/",
        {"reason": "参与者现场申请删除身份资料"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "participant_id": str(registration.participant.pk),
        "deidentified": True,
    }
    registration.participant.refresh_from_db()
    assert registration.participant.anonymized_at is not None
    assert not hasattr(registration.participant, "identity")
    audit = AdminAuditLog.objects.get(action="participant_deidentified")
    assert audit.actor == actor


@pytest.mark.django_db
def test_ops_operator_can_create_an_audited_reward_rule(client):
    actor = login_operator(client)
    activity = ActivityEdition.objects.create(
        slug="autumn-2026",
        title="2026 秋季游园会",
        status=ActivityStatus.DRAFT,
        is_participant_entry=True,
    )
    category = ActivityCategoryConfig.objects.create(
        activity=activity,
        category_key="language",
        title="语言",
    )

    response = client.post(
        f"/ops/activities/{activity.pk}/reward-rules/",
        {
            "category_key": category.category_key,
            "min_score_rate": "0.8000",
            "max_score_rate": "1.0000",
            "priority": "10",
            "text": "日协满分达人",
            "reason": "建立现场兑奖词",
        },
    )

    assert response.status_code == 201
    rule = RewardRule.objects.get(activity=activity)
    assert response.json() == {"reward_rule_id": rule.pk, "active": True}
    assert rule.category_config == category
    assert rule.text == "日协满分达人"
    audit = AdminAuditLog.objects.get(action="reward_rule_created")
    assert audit.actor == actor
    assert audit.reason == "建立现场兑奖词"


@pytest.mark.django_db
def test_ops_operator_deactivates_reward_rules_instead_of_deleting_them(client):
    actor = login_operator(client)
    activity = ActivityEdition.objects.create(
        slug="autumn-2026",
        title="2026 秋季游园会",
        status=ActivityStatus.PAUSED,
        is_participant_entry=True,
    )
    rule = RewardRule.objects.create(
        activity=activity,
        min_score_rate="0.8000",
        max_score_rate="1.0000",
        priority=10,
        text="日协满分达人",
        is_active=True,
    )

    response = client.post(
        f"/ops/reward-rules/{rule.pk}/",
        {"action": "deactivate", "reason": "现场停止使用该兑奖词"},
    )

    assert response.status_code == 200
    rule.refresh_from_db()
    assert rule.is_active is False
    assert RewardRule.objects.filter(pk=rule.pk).exists()
    audit = AdminAuditLog.objects.get(action="reward_rule_deactivated")
    assert audit.actor == actor
    assert audit.reason == "现场停止使用该兑奖词"


@pytest.mark.django_db
def test_ops_operator_can_update_an_existing_reward_rule_with_an_audit_record(client):
    actor = login_operator(client)
    activity = ActivityEdition.objects.create(
        slug="autumn-2026",
        title="2026 秋季游园会",
        status=ActivityStatus.PAUSED,
        is_participant_entry=True,
    )
    rule = RewardRule.objects.create(
        activity=activity,
        min_score_rate="0.8000",
        max_score_rate="1.0000",
        priority=10,
        text="旧兑奖词",
        is_active=True,
    )

    response = client.post(
        f"/ops/reward-rules/{rule.pk}/",
        {
            "action": "update",
            "min_score_rate": "0.9000",
            "max_score_rate": "1.0000",
            "priority": "20",
            "text": "新兑奖词",
            "reason": "现场调整展示条件",
        },
    )

    assert response.status_code == 200
    rule.refresh_from_db()
    assert str(rule.min_score_rate) == "0.9000"
    assert rule.priority == 20
    assert rule.text == "新兑奖词"
    audit = AdminAuditLog.objects.get(action="reward_rule_updated")
    assert audit.actor == actor
    assert audit.reason == "现场调整展示条件"


@pytest.mark.django_db
def test_ops_internal_leaderboard_uses_dense_ranks_and_first_achieved_order(client):
    login_operator(client)
    activity = ActivityEdition.objects.create(
        slug="autumn-2026",
        title="2026 秋季游园会",
        status=ActivityStatus.OPEN,
        is_participant_entry=True,
    )
    category = ActivityCategoryConfig.objects.create(
        activity=activity,
        category_key="language",
        title="语言",
    )
    bank = QuestionBankVersion.objects.create(
        version_code="ops-v1",
        title="现场题库",
        source_label="test",
        content_sha256="a" * 64,
        finalized_at=timezone.now(),
    )
    participants = [
        register_or_resume(
            activity=activity,
            display_name=name,
            identifier=f"PB2400000{index}",
            contact=f"1380013800{index}",
        ).participant
        for index, name in enumerate(("先得同分", "后得同分", "第二名"), start=1)
    ]
    activity.status = ActivityStatus.PAUSED
    activity.save(update_fields=("status", "updated_at"))
    base = timezone.now()
    scores = (2, 2, 1)
    achieved = (base, base + timedelta(seconds=1), base + timedelta(seconds=2))
    for participant, score, achieved_at in zip(participants, scores, achieved, strict=True):
        attempt = QuizAttempt.objects.create(
            activity=activity,
            participant=participant,
            category_config=category,
            bank=bank,
            status="submitted",
            started_at=achieved_at,
            deadline_at=achieved_at + timedelta(minutes=5),
            submitted_at=achieved_at,
            question_count=2,
            score=score,
        )
        CategoryHighScore.objects.create(
            activity=activity,
            participant=participant,
            category_config=category,
            score=score,
            achieved_at=achieved_at,
            source_attempt=attempt,
        )

    response = client.get(
        f"/ops/activities/{activity.pk}/leaderboard/",
        {"category": category.category_key},
    )

    assert response.status_code == 200
    rows = response.json()["rows"]
    assert [(row["rank"], row["display_name"], row["score"]) for row in rows] == [
        (1, "先得同分", 2),
        (1, "后得同分", 2),
        (2, "第二名", 1),
    ]
    serialized = response.content.decode("utf-8")
    assert "PB24000001" not in serialized
    assert "13800138001" not in serialized


@pytest.mark.django_db
def test_ops_activity_console_renders_controls_and_only_masked_identity(client):
    login_operator(client)
    activity = ActivityEdition.objects.create(
        slug="autumn-2026",
        title="2026 秋季游园会",
        status=ActivityStatus.OPEN,
        is_participant_entry=True,
    )
    ActivityCategoryConfig.objects.create(
        activity=activity,
        category_key="language",
        title="语言",
    )
    register_or_resume(
        activity=activity,
        display_name="现场参与者",
        identifier="PB24000001",
        contact="13800138000",
    )

    response = client.get(f"/ops/activities/{activity.pk}/")

    assert response.status_code == 200
    page = response.content.decode("utf-8")
    assert "2026 秋季游园会" in page
    assert "暂停活动" in page
    assert "参与者检索" in page
    assert "显示完整编号" in page
    assert "PB******01" in page
    assert "+86*******8000" in page
    assert "PB24000001" not in page
    assert "+8613800138000" not in page


@pytest.mark.django_db
def test_ops_attempt_detail_shows_submitted_values_without_correct_answers(client):
    login_operator(client)
    activity = ActivityEdition.objects.create(
        slug="autumn-2026-detail",
        title="2026 秋季游园会",
        status=ActivityStatus.OPEN,
        is_participant_entry=True,
    )
    category = ActivityCategoryConfig.objects.create(
        activity=activity,
        category_key="language",
        title="语言",
    )
    bank = QuestionBankVersion.objects.create(
        version_code="ops-detail-v1",
        title="现场题库",
        source_label="test",
        content_sha256="a" * 64,
        finalized_at=timezone.now(),
    )
    participant = Participant.objects.create(activity=activity)
    now = timezone.now()
    attempt = QuizAttempt.objects.create(
        activity=activity,
        participant=participant,
        category_config=category,
        bank=bank,
        status="submitted",
        started_at=now,
        deadline_at=now + timedelta(minutes=5),
        submitted_at=now,
        question_count=1,
        score=1,
    )

    response = client.get(f"/ops/attempts/{attempt.pk}/")

    assert response.status_code == 200
    data = response.json()["attempt"]
    assert data["id"] == str(attempt.pk)
    assert data["participant_id"] == str(participant.pk)
    assert data["status"] == "submitted"
    assert "correct_option" not in response.content.decode("utf-8")
    assert "acceptable_answers" not in response.content.decode("utf-8")
