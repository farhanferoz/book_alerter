"""Static-file mount tests.

The Dockerfile copies the built Vite SPA into `/app/web/dist` and FastAPI is
expected to serve it from `/`. `create_app()` mounts `BOOK_ALERTER_WEB_DIST`
(default `web/dist`) as a `StaticFiles(html=True)` route — mounted last so the
`/api/*` routers still match first.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from book_alerter.app import create_app


def test_static_ui_not_mounted_when_dist_missing(monkeypatch, tmp_path):
    """In dev (no FE build), `/` should not be served by the API."""
    monkeypatch.setenv("BOOK_ALERTER_WEB_DIST", str(tmp_path / "missing"))
    app = create_app()
    client = TestClient(app)
    resp = client.get("/")
    # No SPA mount + no `/` route on any router → 404
    assert resp.status_code == 404


def test_static_ui_served_when_dist_present(monkeypatch, tmp_path):
    """When the dist directory exists, `/` returns its `index.html`."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html><body>book-alerter-spa</body></html>")
    monkeypatch.setenv("BOOK_ALERTER_WEB_DIST", str(dist))

    app = create_app()
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 200
    assert "book-alerter-spa" in resp.text


def test_spa_fallback_returns_index_for_unknown_path(monkeypatch, tmp_path):
    """Deep-link reloads (e.g. /books/123) fall through to index.html so the
    SPA's client-side router can take over."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html><body>spa-shell</body></html>")
    monkeypatch.setenv("BOOK_ALERTER_WEB_DIST", str(dist))

    app = create_app()
    client = TestClient(app)
    resp = client.get("/books/123")
    assert resp.status_code == 200
    assert "spa-shell" in resp.text


def test_static_files_served_from_assets_subdir(monkeypatch, tmp_path):
    """Vite emits hashed assets under `assets/`; they should be served as-is."""
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<html></html>")
    (dist / "assets" / "app-abc123.js").write_text("console.log('hello');")
    monkeypatch.setenv("BOOK_ALERTER_WEB_DIST", str(dist))

    app = create_app()
    client = TestClient(app)
    resp = client.get("/assets/app-abc123.js")
    assert resp.status_code == 200
    assert "console.log" in resp.text


def test_api_routes_still_match_with_static_mount(monkeypatch, tmp_path):
    """The static mount sits below the API routers in the route table."""
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text("<html><body>spa</body></html>")
    monkeypatch.setenv("BOOK_ALERTER_WEB_DIST", str(dist))

    app = create_app()
    client = TestClient(app)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
