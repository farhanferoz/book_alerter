from fastapi.testclient import TestClient
from book_alerter.app import create_app


def test_health_returns_ok():
    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
