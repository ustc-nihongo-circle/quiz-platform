import json
import re

import pytest

from quiz.models import ActivityEdition, ActivityStatus


def test_participant_page_renders_the_public_quiz_shell(client):
    response = client.get("/participant/")

    assert response.status_code == 200
    assert b'lang="zh-CN"' in response.content
    assert b'id="participantApp"' in response.content


def test_site_root_redirects_to_the_participant_page(client):
    response = client.get("/")

    assert response.status_code == 302
    assert response.headers["Location"] == "/participant/"


def test_participant_page_uses_production_assets_without_mock_review_copy(client):
    response = client.get("/participant/")
    page = response.content.decode()

    assert '/static/quiz/participant.css' in page
    assert '/static/quiz/participant.js' in page
    assert "MOCK 后端场景" not in page
    assert "mock 响应" not in page


@pytest.mark.django_db
def test_participant_page_bootstraps_only_non_sensitive_session_identity(client):
    ActivityEdition.objects.create(
        slug="frontend-page-test",
        title="前端页面测试",
        status=ActivityStatus.OPEN,
        is_participant_entry=True,
    )
    client.post(
        "/api/v1/participant-session",
        data=json.dumps(
            {
                "display_name": "页面测试昵称",
                "identifier": "PB24009999",
                "contact": "13800139999",
            }
        ),
        content_type="application/json",
    )

    page = client.get("/participant/").content.decode()
    match = re.search(
        r'<script id="initialParticipant" type="application/json">(.*?)</script>',
        page,
    )

    assert match is not None
    assert json.loads(match.group(1)) == {
        "id": str(client.session["quiz_participant_id"]),
        "display_name": "页面测试昵称",
    }
    assert "PB24009999" not in page
    assert "13800139999" not in page
