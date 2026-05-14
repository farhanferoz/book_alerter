"""End-to-end Docker smoke test (Phase 13.1).

Boots the `book_alerter:dev` image via `docker compose -f
tests/e2e/docker-compose.test.yml up -d`, then exercises the live API
surface to prove the full container — FastAPI + SQLite + migrations + the
`book_stats` view + SPA static-file mount — works as a single unit.

Network-flake-resistant variant: rather than hitting Amazon/WoB/Bookfinder
(which would require network reachability and selectors that survive any
upstream HTML change), the test inserts a `PriceObservation` directly into
the in-container SQLite via `docker exec` and then asserts the API
surfaces it. This decouples the smoke test from network conditions while
still proving the full stack is wired correctly end-to-end.

Marked `@pytest.mark.e2e` and skipped by default (see `addopts` in
pyproject.toml). Opt in with `uv run pytest -m e2e -q tests/e2e/`.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "tests" / "e2e" / "docker-compose.test.yml"
BASE_URL = "http://127.0.0.1:18000"
CONTAINER = "book_alerter_e2e"


def _compose(*args: str, check: bool = True, capture: bool = False) -> subprocess.CompletedProcess[str]:
    cmd = ["docker", "compose", "-f", str(COMPOSE_FILE), *args]
    return subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        check=check,
        text=True,
        capture_output=capture,
    )


def _wait_for_health(timeout_s: float = 90.0) -> None:
    """Poll `/api/health` until 200 or timeout."""
    deadline = time.monotonic() + timeout_s
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{BASE_URL}/api/health", timeout=2.0)
            if r.status_code == 200:
                return
        except Exception as e:  # noqa: BLE001 — health check is best-effort
            last_exc = e
        time.sleep(1.0)
    # Health never came up — dump logs to aid debugging then fail.
    logs = _compose("logs", "--no-color", check=False, capture=True).stdout
    raise AssertionError(
        f"Container did not become healthy within {timeout_s}s "
        f"(last error: {last_exc!r}).\n--- compose logs ---\n{logs}"
    )


@pytest.mark.e2e
def test_docker_smoke() -> None:
    # Pre-clean: if a previous failed run left the container, nuke it.
    _compose("down", "-v", "--remove-orphans", check=False, capture=True)

    try:
        _compose("up", "-d", "--no-build")
        _wait_for_health()

        # 1. Health endpoint still 200 after warmup.
        r = httpx.get(f"{BASE_URL}/api/health", timeout=5.0)
        assert r.status_code == 200, r.text

        # 2. POST a book.
        payload = {
            "isbn": "9780099490548",
            "title": "Captain Corelli",
            "author": "de Bernieres",
        }
        r = httpx.post(f"{BASE_URL}/api/books", json=payload, timeout=10.0)
        assert r.status_code == 201, r.text
        book = r.json()
        book_id = book["id"]
        assert book["isbn13"] == payload["isbn"]
        assert book["title"] == payload["title"]

        # 3. GET the book back.
        r = httpx.get(f"{BASE_URL}/api/books/{book_id}", timeout=5.0)
        assert r.status_code == 200, r.text
        assert r.json()["isbn13"] == payload["isbn"]

        # 4. Sources endpoint returns a list (may be empty on default config).
        r = httpx.get(f"{BASE_URL}/api/sources", timeout=5.0)
        assert r.status_code == 200, r.text
        assert isinstance(r.json(), list)

        # 5. SPA shell at /.
        r = httpx.get(f"{BASE_URL}/", timeout=5.0)
        assert r.status_code == 200, r.text
        assert "<title>Book Alerter</title>" in r.text

        # 6. Non-network observation check: insert a synthetic
        # PriceObservation via `docker exec`, then assert the API exposes
        # it. Proves SQLite + the `book_stats` view + the observations
        # endpoint are wired correctly end-to-end.
        inject = f"""
from datetime import datetime, UTC
from book_alerter.db.session import session_scope, get_engine
from book_alerter.db.models import PriceObservation
with session_scope(get_engine()) as s:
    s.add(PriceObservation(
        book_id={book_id},
        source='manual',
        seller='e2e',
        condition='new',
        price_minor=899,
        currency='GBP',
        shipping_minor=0,
        total_minor=899,
        url='https://example.invalid/e2e',
        observed_at=datetime.now(UTC),
        raw={{}},
    ))
print('inserted')
"""
        exec_res = subprocess.run(
            ["docker", "exec", CONTAINER, "python", "-c", inject],
            check=True,
            text=True,
            capture_output=True,
        )
        assert "inserted" in exec_res.stdout, exec_res.stdout + exec_res.stderr

        # 6a. Observation visible via API. Endpoint returns a paginated
        # envelope: {items: [...], next_before: str | None}.
        r = httpx.get(f"{BASE_URL}/api/books/{book_id}/observations", timeout=5.0)
        assert r.status_code == 200, r.text
        page = r.json()
        items = page["items"]
        assert len(items) >= 1
        assert any(o["total_minor"] == 899 and o["source"] == "manual" for o in items)

        # 6b. book_stats view reflects the observation.
        r = httpx.get(f"{BASE_URL}/api/books/{book_id}/stats", timeout=5.0)
        assert r.status_code == 200, r.text
        stats = r.json()
        assert stats["observation_count"] >= 1
        assert stats["current_best_total_minor"] == 899
    finally:
        # Always tear down — even on failure — so containers and the
        # tmpfs don't linger between runs.
        _compose("down", "-v", "--remove-orphans", check=False, capture=True)
