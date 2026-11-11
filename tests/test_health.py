from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app, create_app

client = TestClient(app)

def test_health():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readiness_checks_dependencies(tmp_path):
    ready_app = create_app(
        Settings(qdrant_path=str(tmp_path / "q"), database_path=str(tmp_path / "db"))
    )
    with TestClient(ready_app) as ready_client:
        response = ready_client.get("/ready")
        assert response.status_code == 200
        assert response.json() == {
            "status": "ready",
            "checks": {"sqlite": "ok", "qdrant": "ok"},
        }


def test_readiness_returns_structured_failure(tmp_path, monkeypatch):
    ready_app = create_app(
        Settings(qdrant_path=str(tmp_path / "q"), database_path=str(tmp_path / "db"))
    )
    monkeypatch.setattr(
        ready_app.state.repository,
        "check_ready",
        lambda: (_ for _ in ()).throw(OSError("down")),
    )
    with TestClient(ready_app) as ready_client:
        response = ready_client.get("/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "not_ready"
    assert response.json()["checks"]["sqlite"] == "failed"

