import json

import pytest
from test_attempt_api import create_open_quiz

from quiz.models import Participant, ParticipantIdentity

pytestmark = pytest.mark.django_db


def test_overlong_display_name_is_a_field_error_without_partial_registration(client):
    create_open_quiz()
    response = client.post(
        "/api/v1/participant-session",
        data=json.dumps(
            {
                "display_name": "名" * 101,
                "identifier": "SYNTHETIC-SECURITY",
                "contact": "audit@example.com",
            }
        ),
        content_type="application/json",
    )
    assert response.status_code == 400
    assert "display_name" in response.json()["error"]["field_errors"]
    assert not Participant.objects.exists()
    assert not ParticipantIdentity.objects.exists()


def test_same_identifier_recovery_failures_are_limited_across_browsers():
    from django.test import Client

    create_open_quiz()
    payload = {"display_name": "合成身份", "identifier": "SECURITY-ID", "contact": "a@example.com"}
    assert (
        Client()
        .post(
            "/api/v1/participant-session", data=json.dumps(payload), content_type="application/json"
        )
        .status_code
        == 201
    )
    payload["contact"] = "mismatch@example.com"
    for number in range(5):
        response = Client().post(
            "/api/v1/participant-session",
            data=json.dumps(payload),
            content_type="application/json",
            REMOTE_ADDR=f"192.0.2.{number + 1}",
        )
        assert response.status_code == 409
    response = Client().post(
        "/api/v1/participant-session",
        data=json.dumps(payload),
        content_type="application/json",
        REMOTE_ADDR="192.0.2.100",
    )
    assert response.status_code == 429
    assert response.json()["error"]["code"] == "rate_limited"
    assert int(response["Retry-After"]) > 0
    assert Participant.objects.count() == 1


@pytest.mark.parametrize(
    "field,value",
    [
        ("display_name", None),
        ("display_name", 123),
        ("display_name", []),
        ("identifier", True),
        ("contact", {}),
        ("display_name", "名" * 401),
        ("identifier", "A" * 65),
        ("identifier", "A" * 129),
        ("contact", "x" * 513),
        ("display_name", "㍿" * 26),
        ("display_name", "\x00"),
        ("display_name", "\ud800"),
    ],
)
def test_invalid_registration_fields_are_rejected_without_echo(client, field, value):
    create_open_quiz()
    payload = {"display_name": "合成用户", "identifier": "SYNTHETIC", "contact": "a@example.com"}
    payload[field] = value
    response = client.post(
        "/api/v1/participant-session", data=json.dumps(payload), content_type="application/json"
    )
    assert response.status_code == 400
    assert field in response.json()["error"]["field_errors"]
    assert not Participant.objects.exists()


def test_normalized_boundaries_and_existing_length_compatibility(client):
    create_open_quiz()
    payload = {
        "display_name": "😀" * 100,
        "identifier": "a" * 64,
        "contact": "synthetic-recovery-compatibility-000000001@example.com",
    }
    response = client.post(
        "/api/v1/participant-session",
        data=json.dumps(payload),
        content_type="application/json; charset=utf-8",
    )
    assert response.status_code == 201
    assert response.json()["participant"]["display_name"] == payload["display_name"]
    payload["display_name"] = "不能覆盖首次昵称"
    response = client.post(
        "/api/v1/participant-session", data=json.dumps(payload), content_type="application/json"
    )
    assert response.status_code == 200
    assert response.json()["participant"]["display_name"] == "😀" * 100


@pytest.mark.parametrize(
    "body,content_type,status",
    [
        ("{}", "text/plain", 415),
        (" " * 8193, "application/json", 413),
        ("{", "application/json", 400),
        ("null", "application/json", 400),
        ("[" * 2000 + "]" * 2000, "application/json", 400),
    ],
)
def test_registration_request_contract(client, body, content_type, status):
    create_open_quiz()
    response = client.post("/api/v1/participant-session", data=body, content_type=content_type)
    assert response.status_code == status
    assert not Participant.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_rate_database_failure_is_retryable_and_creates_nothing(client, monkeypatch):
    from django.db import DatabaseError, connection

    create_open_quiz()

    def unavailable(execute, sql, params, many, context):
        if "quiz_ratelimitbucket" in sql:
            raise DatabaseError("synthetic outage")
        return execute(sql, params, many, context)

    with connection.execute_wrapper(unavailable):
        response = client.post(
            "/api/v1/participant-session",
            data=json.dumps(
                {
                    "display_name": "合成",
                    "identifier": "SYNTHETIC",
                    "contact": "a@example.com",
                }
            ),
            content_type="application/json",
        )
    assert response.status_code == 503
    assert response.json()["error"]["retryable"] is True
    assert not Participant.objects.exists()
