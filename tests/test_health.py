import pytest


def test_health_endpoint(client):
    response = client.get("/health/")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.django_db
def test_liveness_and_database_readiness_endpoints(client):
    live = client.get("/health/live/")
    ready = client.get("/health/ready/")

    assert live.status_code == 200
    assert live.json() == {"status": "ok"}
    assert ready.status_code == 200
    assert ready.json() == {"status": "ok", "database": "ok"}
