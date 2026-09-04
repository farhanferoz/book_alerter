"""Integration tests for the Sources endpoints (Task 7.4)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import yaml
from sqlalchemy import event
from sqlmodel import Session

from book_alerter.config import Config, SourceConfig


def _install_sources(client, **sources: SourceConfig) -> None:
    """Replace `app.state.config.sources` with the given mapping.

    Uses `model_copy(update=...)` so the existing config (including its
    persisted path) is preserved.
    """
    cfg: Config = client.app.state.config
    client.app.state.config = cfg.model_copy(update={"sources": sources})


# --- GET /api/sources --------------------------------------------------------


def test_get_sources_sorted_alphabetically_with_last_run(
    api_client, engine_with_view, make_source_run
):
    _install_sources(
        api_client,
        wob=SourceConfig(schedule="0 */6 * * *"),
        amazon=SourceConfig(schedule="0 */12 * * *", enabled=False),
    )
    started = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    with Session(engine_with_view) as s:
        make_source_run(
            s,
            source="wob",
            started_at=started,
            finished_at=started + timedelta(minutes=1),
            status="success",
            books_attempted=3,
            books_succeeded=3,
        )

    resp = api_client.get("/api/sources")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert [s["name"] for s in body] == ["amazon", "wob"]
    assert body[0]["last_run"] is None
    assert body[0]["config"]["enabled"] is False
    assert body[1]["last_run"]["status"] == "success"
    assert body[1]["last_run"]["books_succeeded"] == 3
    assert body[1]["config"]["schedule"] == "0 */6 * * *"


def test_get_sources_last_run_is_most_recent(
    api_client, engine_with_view, make_source_run
):
    _install_sources(api_client, wob=SourceConfig())
    base = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    with Session(engine_with_view) as s:
        make_source_run(s, source="wob", started_at=base, status="error")
        make_source_run(
            s, source="wob", started_at=base + timedelta(hours=2), status="partial"
        )
        make_source_run(
            s, source="wob", started_at=base + timedelta(hours=1), status="success"
        )

    resp = api_client.get("/api/sources")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["last_run"]["status"] == "partial"


def test_get_sources_last_run_is_one_query_regardless_of_source_count(
    api_client, engine_with_view, make_source_run
):
    """T3.3: the last-run-per-source lookup is one windowed query, not one
    `LIMIT 1` query per configured source. Verified with a real
    `before_cursor_execute` counter, not assumed from reading the code."""
    names = ("wob", "amazon", "bookfinder", "amazon_uk_product")
    sources = {name: SourceConfig() for name in names}
    _install_sources(api_client, **sources)
    base = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    with Session(engine_with_view) as s:
        for name in sources:
            make_source_run(s, source=name, started_at=base, status="success")
            make_source_run(
                s, source=name, started_at=base + timedelta(hours=1), status="error"
            )

    select_count = 0

    def _on_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        nonlocal select_count
        if "sourcerun" in statement.lower() and statement.strip().upper().startswith("SELECT"):
            select_count += 1

    engine = api_client.app.state.engine
    event.listen(engine, "before_cursor_execute", _on_cursor_execute)
    try:
        resp = api_client.get("/api/sources")
    finally:
        event.remove(engine, "before_cursor_execute", _on_cursor_execute)

    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == len(sources)
    assert all(s["last_run"]["status"] == "error" for s in body)  # latest wins
    # The property that matters is that the query count does not scale with the
    # number of sources, not that it is literally one: the endpoint issues a
    # fixed pair (newest run per source, plus the 24h health roll-up), and a
    # reintroduced per-source loop would show up as growth. Asserting a
    # constant AND a hard ceiling catches both regressions.
    four_source_count = select_count

    select_count = 0
    _install_sources(api_client, **{f"extra_{i}": SourceConfig() for i in range(8)},
                     **sources)
    event.listen(engine, "before_cursor_execute", _on_cursor_execute)
    try:
        resp2 = api_client.get("/api/sources")
    finally:
        event.remove(engine, "before_cursor_execute", _on_cursor_execute)
    assert resp2.status_code == 200
    assert len(resp2.json()) == len(sources) + 8

    assert select_count == four_source_count, (
        f"sourcerun SELECTs grew with source count: {four_source_count} for 4 "
        f"sources, {select_count} for 12 — a per-source loop has crept back in"
    )
    assert four_source_count <= 2, (
        f"expected at most 2 sourcerun SELECTs, got {four_source_count}"
    )


# --- POST /api/sources/{name}/run -------------------------------------------


def test_trigger_run_returns_run_id_and_calls_scheduler(api_client):
    _install_sources(api_client, wob=SourceConfig())
    stub = api_client.app.state.scheduler

    resp = api_client.post("/api/sources/wob/run")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"run_id": 42}
    assert stub.calls == ["wob"]


def test_trigger_run_returns_409_when_backoff_active(api_client):
    _install_sources(api_client, wob=SourceConfig())
    api_client.app.state.scheduler.return_zero_for.add("wob")

    resp = api_client.post("/api/sources/wob/run")
    assert resp.status_code == 409
    assert "backoff" in resp.json()["detail"]


def test_trigger_run_returns_404_for_unknown_source(api_client):
    _install_sources(api_client, wob=SourceConfig())

    resp = api_client.post("/api/sources/unknown/run")
    assert resp.status_code == 404


# --- PATCH /api/sources/{name} ----------------------------------------------


def test_patch_source_enabled_round_trips_to_yaml(api_client):
    _install_sources(api_client, wob=SourceConfig(enabled=True))
    cfg_path: Path = api_client.app.state.config_path

    resp = api_client.patch("/api/sources/wob", json={"enabled": False})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["name"] == "wob"
    assert body["config"]["enabled"] is False

    # Subsequent GET reflects the change.
    get_resp = api_client.get("/api/sources")
    assert get_resp.json()[0]["config"]["enabled"] is False

    # YAML on disk reflects the change.
    assert cfg_path.exists()
    on_disk = yaml.safe_load(cfg_path.read_text())
    assert on_disk["sources"]["wob"]["enabled"] is False


def test_patch_source_multi_field_update_preserves_others(api_client):
    _install_sources(
        api_client, wob=SourceConfig(schedule="0 */6 * * *", concurrency=1, region="UK")
    )

    resp = api_client.patch(
        "/api/sources/wob",
        json={"concurrency": 3, "schedule": "*/5 * * * *"},
    )
    assert resp.status_code == 200, resp.text
    cfg = resp.json()["config"]
    assert cfg["concurrency"] == 3
    assert cfg["schedule"] == "*/5 * * * *"
    # Untouched fields preserved.
    assert cfg["region"] == "UK"
    assert cfg["enabled"] is True


def test_patch_source_invalid_concurrency_returns_422(api_client):
    _install_sources(api_client, wob=SourceConfig())

    resp = api_client.patch("/api/sources/wob", json={"concurrency": 99})
    assert resp.status_code == 422


def test_patch_source_unknown_returns_404(api_client):
    _install_sources(api_client, wob=SourceConfig())

    resp = api_client.patch("/api/sources/unknown", json={"enabled": False})
    assert resp.status_code == 404


def test_patch_source_updates_jitter(api_client):
    _install_sources(api_client, wob=SourceConfig(jitter_seconds=600))
    cfg_path: Path = api_client.app.state.config_path

    resp = api_client.patch("/api/sources/wob", json={"jitter_seconds": 42})
    assert resp.status_code == 200, resp.text
    assert resp.json()["config"]["jitter_seconds"] == 42

    # In-memory config swapped.
    new_cfg: Config = api_client.app.state.config
    assert new_cfg.sources["wob"].jitter_seconds == 42

    # YAML on disk reflects the change.
    on_disk = yaml.safe_load(cfg_path.read_text())
    assert on_disk["sources"]["wob"]["jitter_seconds"] == 42


def test_patch_source_updates_per_book_delay(api_client):
    _install_sources(
        api_client, wob=SourceConfig(per_book_delay_seconds=(5, 15))
    )
    cfg_path: Path = api_client.app.state.config_path

    resp = api_client.patch(
        "/api/sources/wob", json={"per_book_delay_seconds": [10, 30]}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["config"]["per_book_delay_seconds"] == [10, 30]

    new_cfg: Config = api_client.app.state.config
    assert new_cfg.sources["wob"].per_book_delay_seconds == (10, 30)

    on_disk = yaml.safe_load(cfg_path.read_text())
    assert on_disk["sources"]["wob"]["per_book_delay_seconds"] == [10, 30]


# --- GET /api/sources/{name}/runs -------------------------------------------


def test_get_source_runs_returns_history(
    api_client, engine_with_view, make_source_run
):
    _install_sources(api_client, wob=SourceConfig())
    base = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    with Session(engine_with_view) as s:
        make_source_run(s, source="wob", started_at=base, status="error")
        make_source_run(
            s,
            source="wob",
            started_at=base + timedelta(hours=2),
            status="success",
        )
        make_source_run(
            s,
            source="wob",
            started_at=base + timedelta(hours=1),
            status="partial",
        )
        # Different source — must be filtered out.
        make_source_run(
            s,
            source="amazon",
            started_at=base + timedelta(hours=3),
            status="success",
        )

    resp = api_client.get("/api/sources/wob/runs")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 3
    assert [r["status"] for r in body] == ["success", "partial", "error"]
    # No traceback leakage.
    assert "error_traceback" not in body[0]


def test_get_source_runs_respects_limit(
    api_client, engine_with_view, make_source_run
):
    _install_sources(api_client, wob=SourceConfig())
    base = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    with Session(engine_with_view) as s:
        for i in range(5):
            make_source_run(
                s,
                source="wob",
                started_at=base + timedelta(hours=i),
                status="success",
            )

    resp = api_client.get("/api/sources/wob/runs?limit=2")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert len(body) == 2
    # Newest two.
    ts = [r["started_at"] for r in body]
    assert ts == sorted(ts, reverse=True)


def test_get_source_runs_404_on_unknown(api_client):
    _install_sources(api_client, wob=SourceConfig())

    resp = api_client.get("/api/sources/nonexistent/runs")
    assert resp.status_code == 404


def test_patch_source_empty_body_is_idempotent_noop(api_client):
    _install_sources(api_client, wob=SourceConfig(schedule="0 */6 * * *"))
    cfg_path: Path = api_client.app.state.config_path

    resp = api_client.patch("/api/sources/wob", json={})
    assert resp.status_code == 200, resp.text
    cfg = resp.json()["config"]
    assert cfg["schedule"] == "0 */6 * * *"
    assert cfg["enabled"] is True
    # Empty body → no save → file is not touched.
    assert not cfg_path.exists()


# --- GET /api/sources: last_24h health roll-up (T6.1) ------------------------


def _health(api_client, name: str) -> dict:
    body = api_client.get("/api/sources").json()
    return next(s["last_24h"] for s in body if s["name"] == name)


def test_last_24h_sums_only_runs_inside_the_window(
    api_client, engine_with_view, make_source_run
):
    """The window boundary is the part that rots silently, so pin both sides."""
    _install_sources(api_client, amazon=SourceConfig())
    now = datetime.now(UTC)
    with Session(engine_with_view) as s:
        # inside the window
        make_source_run(
            s, source="amazon", started_at=now - timedelta(hours=1),
            status="partial", books_attempted=10, books_succeeded=4,
            items_challenged=3,
        )
        # comfortably outside it — must not be counted
        make_source_run(
            s, source="amazon", started_at=now - timedelta(hours=30),
            status="error", books_attempted=99, books_succeeded=0,
            items_challenged=99,
        )

    h = _health(api_client, "amazon")
    assert h["attempted"] == 10, "a run older than 24h must not be summed"
    assert h["succeeded"] == 4
    # Exact since T1.3, not `attempted - succeeded`: of the 6 failures only 3
    # were bot challenges, and the distinction is the whole point of the
    # column. The out-of-window run's 99 must not leak in either.
    assert h["challenged"] == 3


def test_last_24h_accumulates_across_several_runs(
    api_client, engine_with_view, make_source_run
):
    _install_sources(api_client, amazon=SourceConfig())
    now = datetime.now(UTC)
    with Session(engine_with_view) as s:
        runs = ((1, 5, 5, 0), (3, 7, 2, 4), (6, 1, 0, 1))
        for hours, attempted, succeeded, challenged in runs:
            make_source_run(
                s, source="amazon", started_at=now - timedelta(hours=hours),
                status="partial", books_attempted=attempted, books_succeeded=succeeded,
                items_challenged=challenged,
            )

    h = _health(api_client, "amazon")
    # challenged sums the column (0+4+1), not attempted-succeeded (which
    # would be 6) — one of the 7 failures in run two was an ordinary error.
    assert (h["attempted"], h["succeeded"], h["challenged"]) == (13, 7, 5)


def test_last_24h_is_zeroed_for_a_source_that_never_ran(api_client, engine_with_view):
    _install_sources(api_client, amazon=SourceConfig())
    assert _health(api_client, "amazon") == {
        "attempted": 0,
        "succeeded": 0,
        "challenged": 0,
    }


def test_last_24h_never_reports_negative_challenged(
    api_client, engine_with_view, make_source_run
):
    """Defensive: succeeded should never exceed attempted, but a clamped zero
    is a better answer than a negative count leaking to the dashboard."""
    _install_sources(api_client, amazon=SourceConfig())
    with Session(engine_with_view) as s:
        make_source_run(
            s, source="amazon", started_at=datetime.now(UTC) - timedelta(minutes=5),
            status="success", books_attempted=2, books_succeeded=5,
        )
    assert _health(api_client, "amazon")["challenged"] == 0
