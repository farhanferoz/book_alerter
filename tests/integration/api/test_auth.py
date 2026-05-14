"""Tests for optional HTTP Basic auth (Task 7.8).

Auth state is fixed at `create_app()` time, so each test builds a fresh app
with controlled env vars. `BOOK_ALERTER_DATABASE_URL` + `BOOK_ALERTER_CONFIG_PATH`
point at tmp_path so the lifespan doesn't touch `data/book_alerter.db` or
`data/config.yaml`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from book_alerter.app import create_app


@pytest.fixture
def isolated_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    """Point DB + config at tmp_path so lifespan never touches real data/."""
    monkeypatch.setenv("BOOK_ALERTER_DATABASE_URL", f"sqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("BOOK_ALERTER_CONFIG_PATH", str(tmp_path / "config.yaml"))
    return tmp_path


def test_auth_disabled_allows_unauthenticated_request(
    monkeypatch: pytest.MonkeyPatch, isolated_env: Path
) -> None:
    monkeypatch.delenv("APP_BASIC_AUTH_USER", raising=False)
    monkeypatch.delenv("APP_BASIC_AUTH_PASS", raising=False)
    with TestClient(create_app()) as client:
        resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_auth_enabled_no_creds_returns_401_with_www_authenticate(
    monkeypatch: pytest.MonkeyPatch, isolated_env: Path
) -> None:
    monkeypatch.setenv("APP_BASIC_AUTH_USER", "alice")
    monkeypatch.setenv("APP_BASIC_AUTH_PASS", "secret")
    with TestClient(create_app()) as client:
        resp = client.get("/api/health")
    assert resp.status_code == 401
    assert resp.headers.get("WWW-Authenticate", "").lower().startswith("basic")


def test_auth_enabled_wrong_user_returns_401(
    monkeypatch: pytest.MonkeyPatch, isolated_env: Path
) -> None:
    monkeypatch.setenv("APP_BASIC_AUTH_USER", "alice")
    monkeypatch.setenv("APP_BASIC_AUTH_PASS", "secret")
    with TestClient(create_app()) as client:
        resp = client.get("/api/health", auth=("bob", "secret"))
    assert resp.status_code == 401


def test_auth_enabled_wrong_password_returns_401(
    monkeypatch: pytest.MonkeyPatch, isolated_env: Path
) -> None:
    monkeypatch.setenv("APP_BASIC_AUTH_USER", "alice")
    monkeypatch.setenv("APP_BASIC_AUTH_PASS", "secret")
    with TestClient(create_app()) as client:
        resp = client.get("/api/health", auth=("alice", "wrong"))
    assert resp.status_code == 401


def test_auth_enabled_correct_creds_returns_200(
    monkeypatch: pytest.MonkeyPatch, isolated_env: Path
) -> None:
    monkeypatch.setenv("APP_BASIC_AUTH_USER", "alice")
    monkeypatch.setenv("APP_BASIC_AUTH_PASS", "secret")
    with TestClient(create_app()) as client:
        resp = client.get("/api/health", auth=("alice", "secret"))
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_partial_env_vars_keep_auth_disabled(
    monkeypatch: pytest.MonkeyPatch, isolated_env: Path
) -> None:
    monkeypatch.setenv("APP_BASIC_AUTH_USER", "alice")
    monkeypatch.delenv("APP_BASIC_AUTH_PASS", raising=False)
    with TestClient(create_app()) as client:
        resp = client.get("/api/health")
    assert resp.status_code == 200
