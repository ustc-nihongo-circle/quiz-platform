import json
import re
import uuid

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from quiz.identity import IdentityProtector, register_or_resume
from quiz.models import (
    ActivityBankActivation,
    ActivityCategoryConfig,
    ActivityEdition,
    ActivityStatus,
    AdminAuditLog,
    BankQuestion,
    QuestionBankVersion,
    QuestionIdentity,
    QuestionType,
    ReviewStatus,
    RewardRule,
    SamplingRule,
)
from quiz.services import (
    ActivityMustBePaused,
    ActivityNotOpen,
    AttemptExpired,
    activate_question_bank,
    invalidate_attempt,
    normalize_blank_answer,
    start_attempt,
    submit_attempt,
)


def create_open_quiz():
    activity = ActivityEdition.objects.create(
        slug="autumn-2026",
        title="2026 秋季游园会",
        status=ActivityStatus.OPEN,
        default_question_count=2,
    )
    ActivityCategoryConfig.objects.create(
        activity=activity,
        category_key="language",
        title="语言",
    )
    bank = QuestionBankVersion.objects.create(
        version_code="2026-autumn-v1",
        title="示例题库",
        source_label="test",
        content_sha256="a" * 64,
    )
    for number, answer in ((1, "A"), (2, "B")):
        identity = QuestionIdentity.objects.create(stable_code=f"TEST-{number:04d}")
        BankQuestion.objects.create(
            bank=bank,
            identity=identity,
            category_key="language",
            pool_key="default",
            question_type=QuestionType.SINGLE_CHOICE,
            prompt=f"脱敏示例题 {number}",
            option_a="选项 A",
            option_b="选项 B",
            option_c="选项 C",
            option_d="选项 D",
            correct_option=answer,
            origin="test_fixture",
            review_status=ReviewStatus.VERIFIED,
            private_event_allowed=True,
            public_release_allowed=True,
            source_reference="test fixture",
            source_license="CC0-1.0",
        )
    SamplingRule.objects.create(
        bank=bank,
        category_key="language",
        pool_key="default",
        default_quota=2,
    )
    bank.finalized_at = timezone.now()
    bank.save(update_fields=("finalized_at",))
    activation = ActivityBankActivation(
        activity=activity,
        bank=bank,
        reason="test activation",
    )
    activation.full_clean()
    activation.save()
    return activity


def register(client):
    return client.post(
        "/api/v1/participant-session",
        data=json.dumps(
            {
                "display_name": "示例昵称",
                "identifier": "PB24000001",
                "contact": "13800138000",
            }
        ),
        content_type="application/json",
    )


@pytest.mark.django_db
def test_participant_starts_and_submits_an_attempt_without_receiving_answers(client):
    create_open_quiz()
    assert register(client).status_code == 201

    started = client.post(
        "/api/v1/attempts",
        data=json.dumps({"category_code": "language"}),
        content_type="application/json",
    )

    assert started.status_code == 201
    started_body = started.json()
    assert len(started_body["attempt"]["questions"]) == 2
    assert "correct_option" not in json.dumps(started_body)
    answers = []
    for item in started_body["attempt"]["questions"]:
        answer = "A" if item["prompt"].endswith("1") else "A"
        answers.append({"item_id": item["id"], "answer": answer})

    submitted = client.put(
        f"/api/v1/attempts/{started_body['attempt']['id']}/submission",
        data=json.dumps({"answers": answers}),
        content_type="application/json",
    )

    assert submitted.status_code == 200
    result = submitted.json()["attempt"]
    assert result["status"] == "submitted"
    assert result["score"] == 1
    assert result["category_high_score"] == 1
    correctness = {question["prompt"]: question["correct"] for question in result["questions"]}
    assert correctness == {"脱敏示例题 1": True, "脱敏示例题 2": False}
    assert "correct_option" not in json.dumps(result)


@pytest.mark.django_db
def test_duplicate_submission_keeps_the_first_result_and_records_payload_conflict(client):
    create_open_quiz()
    register(client)
    started = client.post(
        "/api/v1/attempts",
        data=json.dumps({"category_code": "language"}),
        content_type="application/json",
    ).json()["attempt"]
    first_answers = [
        {"item_id": item["id"], "answer": "A"} for item in started["questions"]
    ]
    first = client.put(
        f"/api/v1/attempts/{started['id']}/submission",
        data=json.dumps({"answers": first_answers}),
        content_type="application/json",
    )
    changed_answers = [
        {"item_id": item["id"], "answer": "D"} for item in started["questions"]
    ]

    repeated = client.put(
        f"/api/v1/attempts/{started['id']}/submission",
        data=json.dumps({"answers": changed_answers}),
        content_type="application/json",
    )

    assert repeated.status_code == 200
    assert repeated.json()["attempt"]["score"] == first.json()["attempt"]["score"]
    assert AdminAuditLog.objects.filter(action="attempt_submission_conflict").count() == 1


@pytest.mark.django_db
def test_submission_at_grace_boundary_is_accepted_and_later_submission_times_out():
    activity = create_open_quiz()
    protector = IdentityProtector(keys={"v1": b"k" * 32}, active_key_id="v1", hmac_key=b"h" * 32)
    participant = register_or_resume(
        activity=activity,
        display_name="示例昵称",
        identifier="PB24000001",
        contact="13800138000",
        protector=protector,
    ).participant
    start_time = timezone.now()
    first = start_attempt(
        participant=participant,
        category_code="language",
        now=start_time,
    ).attempt

    submit_attempt(
        attempt=first,
        participant=participant,
        answers={},
        now=first.deadline_at + timezone.timedelta(seconds=3),
    )
    second = start_attempt(
        participant=participant,
        category_code="language",
        now=first.deadline_at + timezone.timedelta(seconds=4),
    ).attempt

    with pytest.raises(AttemptExpired):
        submit_attempt(
            attempt=second,
            participant=participant,
            answers={},
            now=second.deadline_at + timezone.timedelta(seconds=3, milliseconds=1),
        )

    second.refresh_from_db()
    assert second.status == "timed_out"


@pytest.mark.django_db
@pytest.mark.parametrize("closed_status", [ActivityStatus.PAUSED, ActivityStatus.CLOSED])
def test_pausing_or_closing_blocks_new_attempts_but_allows_existing_submission(closed_status):
    activity = create_open_quiz()
    protector = IdentityProtector(keys={"v1": b"k" * 32}, active_key_id="v1", hmac_key=b"h" * 32)
    first = register_or_resume(
        activity=activity,
        display_name="示例昵称一",
        identifier="PB24000001",
        contact="13800138000",
        protector=protector,
    ).participant
    second = register_or_resume(
        activity=activity,
        display_name="示例昵称二",
        identifier="PB24000002",
        contact="13800138001",
        protector=protector,
    ).participant
    attempt = start_attempt(participant=first, category_code="language").attempt
    activity.status = closed_status
    activity.save(update_fields=("status", "updated_at"))

    submit_attempt(attempt=attempt, participant=first, answers={})
    with pytest.raises(ActivityNotOpen):
        start_attempt(participant=second, category_code="language")


@pytest.mark.django_db
def test_question_bank_switch_requires_pause_and_keeps_an_audit_record():
    activity = create_open_quiz()
    replacement = QuestionBankVersion.objects.create(
        version_code="2026-autumn-v2",
        title="替换题库",
        source_label="test",
        content_sha256="b" * 64,
    )
    for number in (1, 2):
        BankQuestion.objects.create(
            bank=replacement,
            identity=QuestionIdentity.objects.create(stable_code=f"TEST-V2-{number:04d}"),
            category_key="language",
            pool_key="default",
            question_type=QuestionType.SINGLE_CHOICE,
            prompt=f"替换示例题 {number}",
            option_a="选项 A",
            option_b="选项 B",
            option_c="选项 C",
            option_d="选项 D",
            correct_option="A",
            origin="test_fixture",
            review_status=ReviewStatus.VERIFIED,
            private_event_allowed=True,
            public_release_allowed=True,
            source_reference="test fixture",
            source_license="CC0-1.0",
        )
    SamplingRule.objects.create(
        bank=replacement,
        category_key="language",
        pool_key="default",
        default_quota=2,
    )
    replacement.finalized_at = timezone.now()
    replacement.save(update_fields=("finalized_at",))
    actor = get_user_model().objects.create_user(username="operator")

    with pytest.raises(ActivityMustBePaused):
        activate_question_bank(
            activity=activity,
            bank=replacement,
            actor=actor,
            reason="现场纠正错题",
        )
    activity.status = ActivityStatus.PAUSED
    activity.save(update_fields=("status", "updated_at"))

    activation = activate_question_bank(
        activity=activity,
        bank=replacement,
        actor=actor,
        reason="现场纠正错题",
    )

    assert activation.is_current is True
    assert activity.bank_activations.filter(is_current=True).get().bank == replacement
    assert AdminAuditLog.objects.filter(action="question_bank_activated").count() == 1


def test_fill_blank_normalization_is_unicode_aware_but_not_fuzzy():
    assert normalize_blank_answer(" ＡｂＣ ") == "abc"
    assert normalize_blank_answer("一  七〇") != normalize_blank_answer("一七〇")


@pytest.mark.django_db
def test_submission_rejects_item_ids_that_were_not_drawn_for_the_attempt(client):
    create_open_quiz()
    register(client)
    started = client.post(
        "/api/v1/attempts",
        data=json.dumps({"category_code": "language"}),
        content_type="application/json",
    ).json()["attempt"]

    response = client.put(
        f"/api/v1/attempts/{started['id']}/submission",
        data=json.dumps({"answers": [{"item_id": str(uuid.uuid4()), "answer": "A"}]}),
        content_type="application/json",
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "validation_error"
    assert response.json()["error"]["field_errors"] == {
        "answers": ["提交包含不属于本次答题的题目。"]
    }


@pytest.mark.django_db
def test_reward_is_snapshotted_and_invalidation_recomputes_high_score():
    activity = create_open_quiz()
    protector = IdentityProtector(keys={"v1": b"k" * 32}, active_key_id="v1", hmac_key=b"h" * 32)
    participant = register_or_resume(
        activity=activity,
        display_name="示例昵称",
        identifier="PB24000001",
        contact="13800138000",
        protector=protector,
    ).participant
    category = activity.category_configs.get(category_key="language")
    RewardRule.objects.create(
        activity=activity,
        category_config=category,
        min_score_rate="1.0000",
        max_score_rate="1.0000",
        priority=10,
        text="满分兑奖词",
    )
    first = start_attempt(participant=participant, category_code="language").attempt
    perfect_answers = {str(item.pk): item.question.correct_option for item in first.items.all()}
    first = submit_attempt(
        attempt=first,
        participant=participant,
        answers=perfect_answers,
    )
    second = start_attempt(participant=participant, category_code="language").attempt
    one_correct = {}
    for index, item in enumerate(second.items.all()):
        one_correct[str(item.pk)] = item.question.correct_option if index == 0 else "D"
    second = submit_attempt(
        attempt=second,
        participant=participant,
        answers=one_correct,
    )

    assert first.reward_text == "满分兑奖词"
    assert second.reward_text == ""
    invalidate_attempt(attempt=first, actor=None, reason="测试作废")

    high_score = participant.category_high_scores.get(category_config=category)
    assert high_score.score == 1
    assert high_score.source_attempt == second
    assert AdminAuditLog.objects.filter(action="attempt_invalidated").count() == 1


@pytest.mark.django_db
def test_participant_page_bootstraps_the_current_attempt_without_answers(client):
    create_open_quiz()
    register(client)
    started = client.post(
        "/api/v1/attempts",
        data=json.dumps({"category_code": "language"}),
        content_type="application/json",
    ).json()["attempt"]

    page = client.get("/participant/").content.decode()
    match = re.search(
        r'<script id="initialAttempt" type="application/json">(.*?)</script>',
        page,
    )

    assert match is not None
    initial_attempt = json.loads(match.group(1))
    assert initial_attempt["id"] == started["id"]
    assert "correct_option" not in json.dumps(initial_attempt)
