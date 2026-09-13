from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.core.exceptions import PermissionDenied
from django.db import DatabaseError, connection
from django.test import Client
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from quiz import ops_history
from quiz.history_router import HistoryRouter
from quiz.models import (
    ActivityCategoryConfig,
    ActivityEdition,
    AttemptItem,
    BankQuestion,
    Participant,
    ParticipantIdentity,
    QuestionBankVersion,
    QuestionIdentity,
    QuizAttempt,
)


@pytest.fixture
def history(client, db, monkeypatch):
    # Unit fixtures use the test database. Real cross-database grants are also
    # verified against PostgreSQL before deployment.
    monkeypatch.setattr(ops_history, "history_enabled", lambda: True)
    monkeypatch.setattr("quiz.ops.history_enabled", lambda: True)
    monkeypatch.setattr(ops_history, "history_objects", lambda model: model.objects.all())
    user = get_user_model().objects.create_user(username="history-test-operator")
    user.user_permissions.add(Permission.objects.get(codename="operate_quiz"))
    client.force_login(user)
    activity = ActivityEdition.objects.create(slug="history-test", title="历史合成活动")
    category = ActivityCategoryConfig.objects.create(
        activity=activity, category_key="sample", title="合成板块"
    )
    bank = QuestionBankVersion.objects.create(
        version_code="history-test", title="合成题库", source_label="test", content_sha256="a" * 64
    )
    question = BankQuestion.objects.create(
        bank=bank,
        identity=QuestionIdentity.objects.create(stable_code="HISTORY-SAMPLE"),
        category_key="sample",
        pool_key="sample",
        question_type="fill_blank",
        prompt="NOT_EXPOSED_QUESTION",
        acceptable_answers=["NOT_EXPOSED_ANSWER"],
    )
    return user, activity, category, bank, question


def person(history, number, name="历史合成样例"):
    participant = Participant.objects.create(activity=history[1])
    ParticipantIdentity.objects.create(
        participant=participant,
        activity=history[1],
        display_name=name,
        contact_type="email",
        identifier_envelope={"secret": "MUST_NOT_FETCH_IDENTIFIER"},
        contact_envelope={"secret": "MUST_NOT_FETCH_CONTACT"},
        identifier_digest=f"{number:064x}",
        contact_digest=f"{number:064x}",
    )
    return participant


def record(history, participant, minutes=0):
    now = timezone.now() - timedelta(minutes=minutes)
    attempt = QuizAttempt.objects.create(
        activity=history[1], participant=participant, category_config=history[2], bank=history[3],
        status="submitted", started_at=now, deadline_at=now + timedelta(minutes=5),
        submitted_at=now, question_count=1, score=0,
    )
    AttemptItem.objects.create(
        attempt=attempt, question=history[4], display_order=1,
        submitted_answer='<img src=x onerror="window.injected=true">', is_correct=False,
    )
    return attempt


def test_history_disabled_is_not_exposed(client, db):
    user = get_user_model().objects.create_user(username="no-history")
    user.user_permissions.add(Permission.objects.get(codename="operate_quiz"))
    client.force_login(user)
    assert client.get("/ops/history/").status_code == 404


def test_history_requires_existing_operator_permission(client, history):
    client.logout()
    assert client.get("/ops/history/").status_code == 302
    user = get_user_model().objects.create_user(username="ordinary-history-user")
    client.force_login(user)
    assert client.get("/ops/history/").status_code == 403


def test_history_lists_only_activities_with_answers_and_disables_caching(client, history):
    record(history, person(history, 1))
    ActivityEdition.objects.create(slug="empty-history", title="空草稿")
    response = client.get("/ops/history/")
    assert response.status_code == 200
    assert "no-store" in response["Cache-Control"]
    assert [item.pk for item in response.context["activities"]] == [history[1].pk]
    assert b'/ops/history/' in client.get("/ops/").content


def test_history_global_sort_pagination_and_post_search(client, history):
    people = [person(history, i, f"历史样例{i:02}") for i in range(52)]
    record(history, people[0])
    record(history, people[1], minutes=10)
    url = f"/ops/history/activities/{history[1].pk}/"
    first = client.get(url)
    assert len(first.context["participant_page"]) == 50
    assert first.context["participant_page"][0].pk == people[0].pk
    second = client.post(url, {"page": "2", "sort": "recent"})
    assert len(second.context["participant_page"]) == 2
    filtered = client.post(url, {"display_name": "历史样例51", "sort": "name"})
    assert filtered.context["participant_page"].paginator.count == 1
    assert filtered.context["participant_page"][0].pk == people[-1].pk
    assert b'method="post"' in first.content
    assert b'data-invalidate-url' not in first.content


def test_history_never_selects_identity_envelopes(client, history):
    participant = person(history, 1)
    record(history, participant)
    paths = [f"/ops/history/activities/{history[1].pk}/",
             f"/ops/history/activities/{history[1].pk}/participants/{participant.pk}/"]
    for path in paths:
        with CaptureQueriesContext(connection) as captured:
            response = client.get(path)
        assert response.status_code == 200
        sql = " ".join(item["sql"] for item in captured.captured_queries)
        assert "identifier_envelope" not in sql and "contact_envelope" not in sql
        assert b"MUST_NOT_FETCH" not in response.content


def test_history_detail_scope_pagination_and_escaping(client, history):
    participant = person(history, 1)
    for number in range(27):
        record(history, participant, minutes=number)
    path = f"/ops/history/activities/{history[1].pk}/participants/{participant.pk}/"
    response = client.get(path)
    assert len(response.context["attempt_page"]) == 25
    assert b"&lt;img" in response.content and b"<img src=x" not in response.content
    assert b"NOT_EXPOSED_ANSWER" not in response.content
    assert b"NOT_EXPOSED_QUESTION" not in response.content
    assert response.context["history_mode"] is True
    assert len(client.get(path + "?page=2").context["attempt_page"]) == 2
    other = ActivityEdition.objects.create(slug="other-history", title="其他活动")
    assert client.get(path.replace(str(history[1].pk), str(other.pk))).status_code == 404
    assert client.post(path).status_code == 405
    assert client.post("/ops/history/").status_code == 405


def test_history_search_requires_csrf_and_rejects_oversized_filter(history):
    client = Client(enforce_csrf_checks=True)
    client.force_login(history[0])
    path = f"/ops/history/activities/{history[1].pk}/"
    assert client.post(path, {"display_name": "合成"}).status_code == 403
    client.get(path)
    token = client.cookies["csrftoken"].value
    assert client.post(path, {"display_name": "x" * 401}, HTTP_X_CSRFTOKEN=token).status_code == 400


def test_history_connection_failure_does_not_break_formal_dashboard(client, history, monkeypatch):
    def unavailable(model):
        raise DatabaseError("private connection diagnostic")

    monkeypatch.setattr(ops_history, "history_objects", unavailable)
    response = client.get("/ops/history/")
    assert response.status_code == 503
    assert b"private connection diagnostic" not in response.content
    assert client.get("/ops/").status_code == 200


def test_history_router_prevents_migrations_and_instance_writes():
    router = HistoryRouter()
    assert router.allow_migrate("history", "quiz") is False
    assert router.allow_migrate("default", "quiz") is None
    historical = Participant()
    historical._state.db = "history"
    with pytest.raises(PermissionDenied):
        router.db_for_write(Participant, instance=historical)
    current = Participant()
    current._state.db = "default"
    assert router.allow_relation(historical, current) is False
