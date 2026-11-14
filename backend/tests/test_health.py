from fastapi.testclient import TestClient

from app.main import app


def test_health_endpoint_reports_simulated_only() -> None:
    with TestClient(app) as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["simulated_data_only"] is True
