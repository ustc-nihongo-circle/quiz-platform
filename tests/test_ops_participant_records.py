from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from quiz.identity import register_or_resume
from quiz.models import (
    ActivityCategoryConfig,
    ActivityEdition,
    AttemptItem,
    BankQuestion,
    Participant,
    QuestionBankVersion,
    QuestionIdentity,
    QuizAttempt,
)


@pytest.fixture
def records(client, db):
    user = get_user_model().objects.create_user(username="records-operator")
    user.user_permissions.add(Permission.objects.get(codename="operate_quiz"))
    client.force_login(user)
    activity = ActivityEdition.objects.create(slug="records", title="合成记录活动", status="open")
    category = ActivityCategoryConfig.objects.create(
        activity=activity, category_key="language", title="语言"
    )
    bank = QuestionBankVersion.objects.create(
        version_code="records", title="合成题库", source_label="test", content_sha256="e" * 64
    )
    return activity, category, bank


def participant(activity, number, name="同名样例"):
    return register_or_resume(
        activity=activity,
        display_name=name,
        identifier=f"SYNTHETIC{number:04}",
        contact=f"synthetic-{number}@example.invalid",
    ).participant


def attempt(records, person, minutes=0, status="submitted"):
    activity, category, bank = records
    start = timezone.now() - timedelta(minutes=minutes)
    return QuizAttempt.objects.create(
        activity=activity,
        category_config=category,
        bank=bank,
        participant=person,
        status=status,
        started_at=start,
        deadline_at=start + timedelta(minutes=5),
        submitted_at=None if status == "in_progress" else start + timedelta(seconds=10),
        question_count=1,
        score=0,
    )


def search(client, activity, **data):
    response = client.post(f"/ops/activities/{activity.pk}/participants/search/", data)
    assert response.status_code == 200
    return response.json()


@pytest.mark.django_db
def test_sort_entire_result_before_paging_and_keep_filters(client, records):
    activity = records[0]
    people = [participant(activity, i, f"样例{i:02}") for i in range(52)]
    recent = attempt(records, people[0])
    attempt(records, people[1], minutes=10)
    first = search(client, activity)
    second = search(client, activity, page=2)
    assert first["sort"] == "recent"
    assert first["pagination"] == {"page": 1, "pages": 2, "total": 52}
    assert [row["id"] for row in first["participants"][:2]] == [str(p.pk) for p in people[:2]]
    ids = [row["id"] for result in [first, second] for row in result["participants"]]
    assert len(ids) == len(set(ids)) == 52
    assert first["participants"][0]["attempt_count"] == 1
    assert first["participants"][0]["last_attempt_at"].startswith(
        recent.started_at.isoformat()[:19]
    )
    filtered = search(client, activity, display_name="样例00", sort="oldest", page=99)
    assert filtered["pagination"]["total"] == 1
    assert filtered["participants"][0]["id"] == str(people[0].pk)
    assert search(client, activity, participant_id="bad-id")["pagination"]["total"] == 0


@pytest.mark.django_db
@pytest.mark.parametrize("sort", ["newest", "oldest", "name", "attempts", "unknown"])
def test_allowed_sorts_use_stable_ties_and_unknown_falls_back(client, records, sort):
    activity = records[0]
    first = participant(activity, 1, "B")
    second = participant(activity, 2, "A")
    third = participant(activity, 3, "C")
    now = timezone.now()
    Participant.objects.filter(pk=first.pk).update(created_at=now - timedelta(days=3))
    Participant.objects.filter(pk=second.pk).update(created_at=now - timedelta(days=2))
    Participant.objects.filter(pk=third.pk).update(created_at=now - timedelta(days=1))
    attempt(records, first, minutes=10)
    attempt(records, first, minutes=20, status="invalid")
    attempt(records, second)
    expected = {
        "newest": [third, second, first],
        "oldest": [first, second, third],
        "name": [second, first, third],
        "attempts": [first, second, third],
        "unknown": [second, first, third],
    }
    result = search(client, activity, sort=sort)
    assert [r["id"] for r in result["participants"]] == [str(p.pk) for p in expected[sort]]
    assert result["sort"] == ("recent" if sort == "unknown" else sort)


@pytest.mark.django_db
def test_details_include_all_statuses_across_pages_without_other_people(client, records):
    activity = records[0]
    person = participant(activity, 1)
    other = participant(activity, 2)
    expected = [
        attempt(records, person, i, ["submitted", "timed_out", "invalid"][i % 3]) for i in range(27)
    ]
    expected.insert(0, attempt(records, person, -1, "in_progress"))
    excluded = attempt(records, other, -2)
    url = f"/ops/activities/{activity.pk}/participants/{person.pk}/"
    with CaptureQueriesContext(connection) as queries:
        response = client.get(url)
        page_one = list(response.context["attempt_page"])
    assert len(queries) <= 10  # 25 records must not cause 25 item/identity lookups.
    page_two = list(client.get(url, {"page": 2}).context["attempt_page"])
    assert [a.pk for a in page_one + page_two] == [a.pk for a in expected]
    assert str(excluded.pk) not in response.content.decode()
    assert "no-store" in response["Cache-Control"]
    assert response.context["attempt_page"].paginator.count == 28


@pytest.mark.django_db
def test_details_enforce_operator_permission_and_activity_scope(client, records):
    activity = records[0]
    person = participant(activity, 1)
    url = f"/ops/activities/{activity.pk}/participants/{person.pk}/"
    other = ActivityEdition.objects.create(slug="other", title="其他活动")
    assert client.get(f"/ops/activities/{other.pk}/participants/{person.pk}/").status_code == 404
    assert client.get(url).status_code == 200
    assert "尚无答题记录" in client.get(url).content.decode()
    client.logout()
    assert client.get(url).status_code == 302
    user = get_user_model().objects.create_user(username="no-operator-permission", is_staff=True)
    client.force_login(user)
    assert client.get(url).status_code == 403


@pytest.mark.django_db
def test_recent_names_and_submitted_values_are_escaped_and_no_identity_secrets(client, records):
    activity, category, bank = records
    person = participant(activity, 1, '<img src=x onerror="alert(1)">')
    record = attempt(records, person)
    question = BankQuestion.objects.create(
        bank=bank,
        identity=QuestionIdentity.objects.create(stable_code="safe-detail"),
        category_key=category.category_key,
        pool_key="one",
        question_type="fill_blank",
        prompt="私密题干",
        acceptable_answers=["SECRET_KEY_NOT_FOR_DETAIL"],
        source_reference="synthetic",
        source_license="synthetic",
        origin="test",
    )
    AttemptItem.objects.create(
        attempt=record,
        question=question,
        display_order=1,
        submitted_answer="<script>alert(1)</script>",
        is_correct=False,
    )
    url = f"/ops/activities/{activity.pk}/participants/{person.pk}/"
    overview = client.get(f"/ops/activities/{activity.pk}/").content.decode()
    assert url in overview and "&lt;img" in overview
    body = client.get(url).content.decode()
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in body
    for secret in [
        "<script>",
        "<img",
        "SECRET_KEY_NOT_FOR_DETAIL",
        "SYNTHETIC0001",
        "synthetic-1@example.invalid",
    ]:
        assert secret not in body
    person.identity.delete()
    body = client.get(url).content.decode()
    assert "已去身份化" in body and str(record.pk) in body
