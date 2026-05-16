from fastapi.testclient import TestClient
from book_alerter.app import create_app


def test_health_returns_ok():
    app = create_app()
    # `with TestClient(...)` enters the FastAPI lifespan so engine + scheduler
    # are initialized — the deep healthcheck (db SELECT 1 + scheduler.running)
    # would otherwise 503.
    with TestClient(app) as client:
        resp = client.get("/api/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"


def test_health_includes_config_version_when_loaded(monkeypatch, tmp_path):
    monkeypatch.setenv("BOOK_ALERTER_CONFIG_PATH", str(tmp_path / "config.yaml"))
    (tmp_path / "config.yaml").write_text("config_version: 1\n")
    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json()["config_version"] == 1


def test_first_boot_seeds_default_config(monkeypatch, tmp_path):
    """If config.yaml is absent, the lifespan writes the defaults to disk so
    the user has something to inspect and edit."""
    cfg_path = tmp_path / "config.yaml"
    monkeypatch.setenv("BOOK_ALERTER_CONFIG_PATH", str(cfg_path))
    assert not cfg_path.exists()
    app = create_app()
    with TestClient(app) as client:
        resp = client.get("/api/health")
        assert resp.status_code == 200
    assert cfg_path.exists(), "lifespan should seed defaults when config is missing"
    # Sanity: it's valid YAML and has the schema root keys.
    import yaml as _yaml
    parsed = _yaml.safe_load(cfg_path.read_text())
    assert "sources" in parsed
    assert "recommendation" in parsed
