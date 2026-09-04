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

`test_write_containment_during_scheduler_run` (T6.8) is a second, separate
container boot: it snapshots the container filesystem before and after a
real scheduler run and asserts every newly-created path lives under
/app/data -- the plan §8 write-containment standard, enforced rather than
just documented.
"""
from __future__ import annotations

import subprocess
import time
from http import HTTPStatus
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
COMPOSE_FILE = REPO_ROOT / "tests" / "e2e" / "docker-compose.test.yml"
BASE_URL = "http://127.0.0.1:18000"
CONTAINER = "book_alerter_e2e"

# Synthetic price (pence) for the step-6 PriceObservation -- named once and
# reused so the insert and the round-trip assertions can't drift apart.
_SYNTHETIC_TOTAL_MINOR = 899


def _compose(
    *args: str, check: bool = True, capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    cmd = ["docker", "compose", "-f", str(COMPOSE_FILE), *args]
    return subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        check=check,
        text=True,
        capture_output=capture,
    )


# T6.8: write-containment. Every path the runtime creates or modifies during
# a scheduler run must live under /app/data (the tmpfs volume) -- anything
# else is invisible disk growth in a real deployment (the mounted volume is
# the only place meant to hold state; see docker-compose.test.yml's own
# comment on why /app/data is tmpfs). Excluded from the check because the
# runtime legitimately churns them regardless of what any source does:
#   /proc, /sys, /dev - kernel-exposed pseudo-filesystems, not real disk;
#     every running process (including `find` itself) creates and destroys
#     entries under /proc/<pid>/ constantly.
#   /tmp               - container-wide scratch space Python, uvicorn and
#     Playwright are all free to use transiently (e.g. Chromium's own
#     --user-data-dir default before an explicit one is set); not backed by
#     the data volume and not expected to be.
#   __pycache__ / *.pyc - the interpreter recompiles bytecode caches next to
#     already-installed .py files on first import of a module that hasn't
#     been imported since the image was built; this happens on ANY request,
#     not because of anything a source wrote, and PYTHONDONTWRITEBYTECODE
#     isn't set for the app process (only relevant at image-build time).
#   /home/pwuser/{.cache,.config,.local} - discovered empirically, not
#     assumed: the FIRST Chromium launch in a container's life seeds
#     ~37 paths here (fontconfig's cache, the crashpad handler's "Crash
#     Reports" directory skeleton, the NSS cert9.db/key4.db/pkcs11.txt
#     trust store) that Chromium always writes under $HOME regardless of
#     --user-data-dir. Measured directly: triggering a second scheduler
#     run in the SAME container added zero further paths here (see commit
#     body) -- library/runtime housekeeping seeded once per container
#     lifetime, the same shape as the __pycache__ exclusion above, not
#     growth tied to scrape volume. The browser PROFILE itself
#     (data/browser-profiles/<source>/) is NOT part of this exclusion --
#     that one must (and does) land under /app/data.
_FIND_PRUNE_ARGS = (
    "-path", "/proc", "-o",
    "-path", "/sys", "-o",
    "-path", "/dev", "-o",
    "-path", "/tmp", "-o",
    "-name", "__pycache__", "-o",
    "-name", "*.pyc", "-o",
    "-path", "/home/pwuser/.cache", "-o",
    "-path", "/home/pwuser/.config", "-o",
    "-path", "/home/pwuser/.local",
)


def _snapshot_container_paths() -> set[str]:
    """Every filesystem path in the running container, minus the exclusions
    above. Used before/after a scheduler run to compute exactly which paths
    were newly created.

    `check=False`: the app runs as the unprivileged `pwuser` (Dockerfile
    `USER pwuser`), so `find /` exits 1 on every root-owned directory it
    can't descend into (`/etc/ssl/private`, `/var/cache/ldconfig`, ...) —
    noisy but harmless here, since a location `pwuser` can't *read* is
    also one it can't *write*, i.e. not a location a source running as the
    same user could have leaked a file into either. stdout still carries
    every path it *could* read, which is the complete set this check needs.
    """
    result = subprocess.run(
        [
            "docker", "exec", CONTAINER,
            "find", "/", "(", *_FIND_PRUNE_ARGS, ")", "-prune", "-o", "-print",
        ],
        check=False,
        text=True,
        capture_output=True,
    )
    return {line for line in result.stdout.splitlines() if line}


def _wait_for_health(timeout_s: float = 90.0) -> None:
    """Poll `/api/health` until 200 or timeout."""
    deadline = time.monotonic() + timeout_s
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        try:
            r = httpx.get(f"{BASE_URL}/api/health", timeout=2.0)
            if r.status_code == HTTPStatus.OK:
                return
        except Exception as e:
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
        assert r.status_code == HTTPStatus.OK, r.text

        # 2. POST a book.
        payload = {
            "isbn": "9780099490548",
            "title": "Captain Corelli",
            "author": "de Bernieres",
        }
        r = httpx.post(f"{BASE_URL}/api/books", json=payload, timeout=10.0)
        assert r.status_code == HTTPStatus.CREATED, r.text
        book = r.json()
        book_id = book["id"]
        assert book["isbn13"] == payload["isbn"]
        assert book["title"] == payload["title"]

        # 3. GET the book back.
        r = httpx.get(f"{BASE_URL}/api/books/{book_id}", timeout=5.0)
        assert r.status_code == HTTPStatus.OK, r.text
        assert r.json()["isbn13"] == payload["isbn"]

        # 4. Sources endpoint returns a list (may be empty on default config).
        r = httpx.get(f"{BASE_URL}/api/sources", timeout=5.0)
        assert r.status_code == HTTPStatus.OK, r.text
        assert isinstance(r.json(), list)

        # 5. SPA shell at /.
        r = httpx.get(f"{BASE_URL}/", timeout=5.0)
        assert r.status_code == HTTPStatus.OK, r.text
        assert "<title>Book Alerter</title>" in r.text

        # 6. Non-network observation check: insert a synthetic
        # PriceObservation via `docker exec`, then assert the API exposes
        # it. Proves SQLite + the `book_stats` view + the observations
        # endpoint are wired correctly end-to-end.
        inject = f"""
from datetime import datetime, UTC
from book_alerter.db.session import session_scope, get_engine
from book_alerter.db.models import PriceObservation
now = datetime.now(UTC)
with session_scope(get_engine()) as s:
    s.add(PriceObservation(
        book_id={book_id},
        source='manual',
        seller='e2e',
        condition='new',
        price_minor={_SYNTHETIC_TOTAL_MINOR},
        currency='GBP',
        shipping_minor=0,
        total_minor={_SYNTHETIC_TOTAL_MINOR},
        url='https://example.invalid/e2e',
        observed_at=now,
        # Required since migration 0021 (T3.2). A first sighting is also
        # its own last sighting, so both timestamps are the same instant.
        last_seen_at=now,
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
        assert r.status_code == HTTPStatus.OK, r.text
        page = r.json()
        items = page["items"]
        assert len(items) >= 1
        assert any(
            o["total_minor"] == _SYNTHETIC_TOTAL_MINOR and o["source"] == "manual"
            for o in items
        )

        # 6b. book_stats view reflects the observation.
        r = httpx.get(f"{BASE_URL}/api/books/{book_id}/stats", timeout=5.0)
        assert r.status_code == HTTPStatus.OK, r.text
        stats = r.json()
        assert stats["observation_count"] >= 1
        assert stats["current_best_total_minor"] == _SYNTHETIC_TOTAL_MINOR
    finally:
        # Always tear down — even on failure — so containers and the
        # tmpfs don't linger between runs.
        _compose("down", "-v", "--remove-orphans", check=False, capture=True)


@pytest.mark.e2e
def test_write_containment_during_scheduler_run() -> None:
    """T6.8: everything a source's browser-session lifecycle
    (prepare()/cleanup(), see src/book_alerter/sources/browser.py) creates
    on disk during a real scheduler run must land under /app/data.

    Own container (rather than extending test_docker_smoke) so the DB is
    guaranteed empty: `POST /api/sources/{name}/run` awaits the run
    in-request (Scheduler.trigger_now), and with zero ACTIVE books
    `_run_kind_for_source` fetches nothing, so this stays exactly as
    network-flake-resistant as the rest of this file while still exercising
    the real prepare()/cleanup() browser-session path -- the mechanism this
    test exists to contain. "amazon" is Playwright-backed (unlike "wob"),
    so its BrowserSessionMixin.prepare() actually creates a profile
    directory (data/browser-profiles/amazon/) -- the concrete case this
    task calls out.
    """
    _compose("down", "-v", "--remove-orphans", check=False, capture=True)

    try:
        _compose("up", "-d", "--no-build")
        _wait_for_health()

        before = _snapshot_container_paths()

        r = httpx.post(f"{BASE_URL}/api/sources/amazon/run", timeout=60.0)
        assert r.status_code == HTTPStatus.OK, r.text

        after = _snapshot_container_paths()

        new_paths = sorted(after - before)
        outside_data = [p for p in new_paths if not p.startswith("/app/data")]
        assert not outside_data, (
            "scheduler run created path(s) outside /app/data — this leaks "
            "onto the read-only image layer / container disk instead of "
            "the mounted data volume:\n"
            + "\n".join(outside_data)
            + f"\n\nall new paths: {new_paths}"
        )
        # The check must be able to fail, not just always pass — assert it
        # actually observed *something* new (the browser profile directory
        # at minimum), so a broken snapshot (e.g. `docker exec` erroring
        # silently into two empty sets) can't masquerade as containment.
        assert new_paths, "expected at least one new path (browser profile); saw none"
    finally:
        _compose("down", "-v", "--remove-orphans", check=False, capture=True)
