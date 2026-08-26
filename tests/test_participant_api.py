import json

import pytest
from django.test import Client

from quiz.models import ActivityEdition, ActivityStatus


@pytest.mark.django_db
def test_participant_session_registers_and_resumes_without_exposing_identity(client):
    ActivityEdition.objects.create(
        slug="autumn-2026",
        title="2026 秋季游园会",
        status=ActivityStatus.OPEN,
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
def test_participant_session_contact_mismatch_returns_generic_recovery_error(client):
    ActivityEdition.objects.create(
        slug="autumn-2026",
        title="2026 秋季游园会",
        status=ActivityStatus.OPEN,
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
