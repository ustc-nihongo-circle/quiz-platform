import json

import pytest
from django.test import Client

from quiz.models import ActivityEdition, ActivityStatus


@pytest.mark.django_db
def test_activity_api_uses_the_explicit_participant_entry_instead_of_latest_activity(client):
    selected = ActivityEdition.objects.create(
        slug="autumn-2026",
        title="2026 秋季游园会",
        status=ActivityStatus.PAUSED,
        is_participant_entry=True,
    )
    ActivityEdition.objects.create(
        slug="spring-2027-draft",
        title="2027 春季筹备",
        status=ActivityStatus.DRAFT,
    )

    response = client.get("/api/v1/activity")

    assert response.status_code == 200
    assert response.json()["activity"]["code"] == selected.slug


@pytest.mark.django_db
def test_participant_session_registers_and_resumes_without_exposing_identity(client):
    ActivityEdition.objects.create(
        slug="autumn-2026",
        title="2026 秋季游园会",
        status=ActivityStatus.OPEN,
        is_participant_entry=True,
    )
    payload = {
        "display_name": "示例昵称",
        "identifier": "PB24000001",
        "contact": "13800138000",
    }

    created = client.post(
        "/api/v1/participant-session",
        data=json.dumps(payload),
        content_type="application/json",
    )
    resumed = client.post(
        "/api/v1/participant-session",
        data=json.dumps({**payload, "identifier": "pb24000001"}),
        content_type="application/json",
    )

    assert created.status_code == 201
    assert resumed.status_code == 200
    assert created.json()["participant"] == resumed.json()["participant"]
    body = json.dumps(created.json(), ensure_ascii=False)
    assert "PB24000001" not in body
    assert "13800138000" not in body


@pytest.mark.django_db
@pytest.mark.parametrize("inactive_status", [ActivityStatus.PAUSED, ActivityStatus.CLOSED])
def test_inactive_activity_allows_existing_participant_to_resume_but_rejects_new_identity(
    client,
    inactive_status,
):
    activity = ActivityEdition.objects.create(
        slug="autumn-2026",
        title="2026 秋季游园会",
        status=ActivityStatus.OPEN,
        is_participant_entry=True,
    )
    existing = {
        "display_name": "已登记昵称",
        "identifier": "PB24000001",
        "contact": "13800138000",
    }
    assert client.post(
        "/api/v1/participant-session",
        data=json.dumps(existing),
        content_type="application/json",
    ).status_code == 201
    activity.status = inactive_status
    activity.save(update_fields=("status", "updated_at"))

    resumed = Client().post(
        "/api/v1/participant-session",
        data=json.dumps(existing),
        content_type="application/json",
    )
    rejected = Client().post(
        "/api/v1/participant-session",
        data=json.dumps(
            {
                "display_name": "新参与者",
                "identifier": "PB24000002",
                "contact": "13800138001",
            }
        ),
        content_type="application/json",
    )

    assert resumed.status_code == 200
    assert resumed.json()["created"] is False
    assert rejected.status_code == 409
    assert rejected.json()["error"]["code"] == "participant_recovery_required"


@pytest.mark.django_db
def test_participant_session_contact_mismatch_returns_generic_recovery_error(client):
    ActivityEdition.objects.create(
        slug="autumn-2026",
        title="2026 秋季游园会",
        status=ActivityStatus.OPEN,
        is_participant_entry=True,
    )
    first = {
        "display_name": "示例昵称",
        "identifier": "PB24000001",
        "contact": "13800138000",
    }
    client.post(
        "/api/v1/participant-session",
        data=json.dumps(first),
        content_type="application/json",
    )

    response = client.post(
        "/api/v1/participant-session",
        data=json.dumps({**first, "contact": "13800138001"}),
        content_type="application/json",
    )

    assert response.status_code == 409
    assert response.json() == {
        "error": {
            "code": "participant_recovery_required",
            "message": "登记信息无法匹配，请联系活动管理员处理。",
            "field_errors": {},
            "retryable": False,
        }
    }


@pytest.mark.django_db
def test_session_mutations_require_same_origin_csrf_token():
    ActivityEdition.objects.create(
        slug="autumn-2026",
        title="2026 秋季游园会",
        status=ActivityStatus.OPEN,
        is_participant_entry=True,
    )
    csrf_client = Client(enforce_csrf_checks=True)
    activity = csrf_client.get("/api/v1/activity")
    token = activity.cookies["csrftoken"].value
    payload = json.dumps(
        {
            "display_name": "示例昵称",
            "identifier": "PB24000001",
            "contact": "13800138000",
        }
    )

    rejected = csrf_client.post(
        "/api/v1/participant-session",
        data=payload,
        content_type="application/json",
    )
    accepted = csrf_client.post(
        "/api/v1/participant-session",
        data=payload,
        content_type="application/json",
        HTTP_X_CSRFTOKEN=token,
    )

    assert rejected.status_code == 403
    assert rejected.json()["error"]["code"] == "csrf_failed"
    assert accepted.status_code == 201
