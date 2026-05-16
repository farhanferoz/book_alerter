"""Scenario 6 — UI surface against real (scenario-seeded) data.

We don't spin up the dev frontend (that requires Node + a running uvicorn).
Instead we instantiate a router-only FastAPI app — same pattern as
`tests/integration/api/conftest.py` — and curl through TestClient. The
DB is the same SQLite file the earlier scenarios populated, so the API
sees real books + observations + alerts + signal state.

We sanity-check:
- `GET /api/books` (excluding archived).
- `GET /api/books/{id}` shape.
- `GET /api/books/{id}/observations` returns chronological history.
- `GET /api/alerts?dismissed=false` returns the undismissed feed.
- `GET /api/sources` works.
- `GET /api/config` returns the canonical config shape.
- `GET /api/health` returns 200.

UI route checks (curl HEAD on `/`, `/books/<id>`, etc.) are NOT done here:
they require a running uvicorn + the built web bundle, which is beyond a
fast scenario script. The router-only TestClient suffices to catch any
API-contract regressions.
"""
from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fastapi import FastAPI
from fastapi.testclient import TestClient
from freezegun import freeze_time

from book_alerter.api import alerts as alerts_routes
from book_alerter.api import books as books_routes
from book_alerter.api import config as config_routes
from book_alerter.api import health as health_routes
from book_alerter.api import sources as sources_routes
from book_alerter.config import Config, NotificationsConfig, RecommendationConfig
from book_alerter.db import models
from book_alerter.notifications.dispatcher import AlertPipeline
from book_alerter.notifications.inapp import InAppNotifier
from helpers import (  # noqa: E402
    add_observation,
    fresh_engine,
    make_book,
    make_recorder,
    run_pipeline,
    session_factory_for,
    session_for,
)
from sqlmodel import select


class _StubScheduler:
    async def trigger_now(self, name: str) -> int:
        return 1


def _seed_realistic_data(engine) -> dict[str, int]:
    """Build a small realistic DB: two books, a few alerts, history."""
    cfg = Config(
        recommendation=RecommendationConfig(min_observations_for_signal=14),
        notifications=NotificationsConfig(quiet_hours=None),
    )
    pipeline = AlertPipeline(
        cfg=cfg,
        session_factory=session_factory_for(engine),
        notifiers=[InAppNotifier()],
    )

    with session_for(engine) as s:
        book_a = make_book(
            s,
            isbn13="9780099490548",
            title="Captain Corelli's Mandolin",
            author="Louis de Bernieres",
            target_price_minor=400,
            percentile_threshold=25,
        )
        book_b = make_book(
            s,
            isbn13="9780571197361",
            title="Birthday Letters",
            author="Ted Hughes",
            target_price_minor=600,
        )
        book_a_id = book_a.id
        book_b_id = book_b.id

    base = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    # Book A — drive into TARGET_HIT eventually.
    for i, total in enumerate([1500 + 25 * i for i in range(14)]):
        with session_for(engine) as s:
            add_observation(
                s, book_id=book_a_id, total_minor=total, source=f"a_{i}",
                observed_at=base + timedelta(days=i),
            )
    run_pipeline(pipeline, [book_a_id])
    with session_for(engine) as s:
        add_observation(
            s, book_id=book_a_id, total_minor=350, source="a_drop",
            observed_at=base + timedelta(days=15),
        )
    run_pipeline(pipeline, [book_a_id])

    # Book B — just history, no special transitions.
    for i, total in enumerate([800 + i * 30 for i in range(8)]):
        with session_for(engine) as s:
            add_observation(
                s, book_id=book_b_id, total_minor=total, source=f"b_{i}",
                observed_at=base + timedelta(days=i),
            )
    run_pipeline(pipeline, [book_b_id])

    return {"book_a": book_a_id, "book_b": book_b_id}


def _build_app(engine, cfg_path: Path) -> FastAPI:
    app = FastAPI()
    app.state.engine = engine
    app.state.config = Config(
        recommendation=RecommendationConfig(min_observations_for_signal=14),
        notifications=NotificationsConfig(quiet_hours=None),
    )
    app.state.config_path = cfg_path
    app.state.scheduler = _StubScheduler()
    app.state.notifiers = {"inapp": InAppNotifier()}
    for r in (
        health_routes.router,
        books_routes.router,
        alerts_routes.router,
        sources_routes.router,
        config_routes.router,
    ):
        app.include_router(r)
    return app


def main() -> int:
    # Same time-rot guard as scenario_01: freeze "now" just past the latest
    # seeded observation so windowed stats include the full series even when
    # the real clock has moved on. See scenario_01_signal_transitions.py for
    # the failure mode this prevents.
    with freeze_time("2026-01-17 12:00:00"):
        return _run_scenario()


def _run_scenario() -> int:
    r = make_recorder("scenario_06_ui_surface")
    engine = fresh_engine()
    ids = _seed_realistic_data(engine)

    cfg_path = Path(__file__).parent / ".scenario_config.yaml"
    if cfg_path.exists():
        cfg_path.unlink()
    app = _build_app(engine, cfg_path)
    client = TestClient(app)

    # --- /api/health
    resp = client.get("/api/health")
    r.expect(resp.status_code == 200, f"GET /api/health == 200 (got {resp.status_code})")
    r.expect("status" in resp.json(), "/api/health body contains 'status'")

    # --- /api/books
    resp = client.get("/api/books")
    r.expect(resp.status_code == 200, f"GET /api/books == 200 (got {resp.status_code})")
    books = resp.json()
    r.expect(len(books) == 2, f"two books returned (got {len(books)})")
    r.expect(
        all("stats" in b for b in books),
        "every BookOut has nested 'stats' (BookStatsOut)",
    )
    isbn_titles = {b["isbn13"]: b["title"] for b in books}
    r.expect(
        "9780099490548" in isbn_titles, f"Corelli ISBN present (got {list(isbn_titles)})",
    )

    # --- /api/books/{id}
    resp = client.get(f"/api/books/{ids['book_a']}")
    r.expect(resp.status_code == 200, "GET /api/books/{id} == 200")
    body = resp.json()
    r.expect(body["isbn13"] == "9780099490548", "book detail has correct ISBN")
    r.expect(
        body["stats"]["current_best_total_minor"] == 350,
        f"current_best_total_minor reflects all observations "
        f"(got {body['stats']['current_best_total_minor']})",
    )

    # --- /api/books/{id}/observations
    resp = client.get(f"/api/books/{ids['book_a']}/observations?limit=500")
    r.expect(resp.status_code == 200, "GET /api/books/{id}/observations == 200")
    obs_body = resp.json()
    r.expect(
        "items" in obs_body and "next_before" in obs_body,
        "observations page has items + next_before",
    )
    # Should have 15 observations on book_a (14 warmup + 1 drop).
    r.expect(
        len(obs_body["items"]) == 15,
        f"observation count == 15 (got {len(obs_body['items'])})",
    )
    # Items should be newest-first.
    ts = [o["observed_at"] for o in obs_body["items"]]
    r.expect(ts == sorted(ts, reverse=True), "observations returned newest-first")

    # --- /api/alerts?dismissed=false
    resp = client.get("/api/alerts?dismissed=false")
    r.expect(resp.status_code == 200, "GET /api/alerts == 200")
    alerts_body = resp.json()
    r.expect("items" in alerts_body, "alerts feed has items")
    r.expect(
        len(alerts_body["items"]) >= 1,
        f"at least one undismissed alert (got {len(alerts_body['items'])})",
    )

    # --- /api/sources
    resp = client.get("/api/sources")
    r.expect(resp.status_code == 200, "GET /api/sources == 200")

    # --- /api/config
    resp = client.get("/api/config")
    r.expect(resp.status_code == 200, "GET /api/config == 200")
    cfg = resp.json()
    r.expect(
        "recommendation" in cfg and "notifications" in cfg,
        "config has recommendation + notifications keys",
    )

    # --- dismiss an alert, verify list shrinks
    with session_for(engine) as s:
        active = s.exec(
            select(models.Alert).where(models.Alert.dismissed_at.is_(None))
        ).all()
    if active:
        aid = active[0].id
        resp = client.post(f"/api/alerts/{aid}/dismiss")
        r.expect(resp.status_code == 200, f"POST /api/alerts/{{id}}/dismiss == 200")
        resp2 = client.get("/api/alerts?dismissed=false")
        new_count = len(resp2.json()["items"])
        r.expect(
            new_count == len(alerts_body["items"]) - 1,
            f"undismissed count decreased by 1 (was {len(alerts_body['items'])} now {new_count})",
        )

    # cleanup
    if cfg_path.exists():
        cfg_path.unlink()

    return r.finish()


if __name__ == "__main__":
    sys.exit(main())
