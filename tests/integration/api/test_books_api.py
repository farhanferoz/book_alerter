"""Integration tests for the Books CRUD endpoints (Task 7.1 + 7.2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlmodel import Session

from book_alerter.config import Config, SourceConfig
from book_alerter.db import models


def _install_sources(client, **sources: SourceConfig) -> None:
    """Replace `app.state.config.sources` with the given mapping."""
    cfg: Config = client.app.state.config
    client.app.state.config = cfg.model_copy(update={"sources": sources})


def test_post_books_happy_path_normalizes_isbn(api_client):
    resp = api_client.post(
        "/api/books",
        json={
            "isbn": "0241638194",  # ISBN-10 — should normalize to 13
            "title": "T",
            "author": "A",
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["isbn13"] == "9780241638194"
    assert body["title"] == "T"
    assert body["author"] == "A"
    assert isinstance(body["id"], int) and body["id"] > 0
    assert body["status"] == "active"
    # stats is present (zero-observation case)
    assert body["stats"]["observation_count"] == 0
    assert body["stats"]["current_best_total_minor"] is None


def test_post_books_duplicate_isbn_returns_409(api_client):
    payload = {"isbn": "9780241638194", "title": "T", "author": "A"}
    r1 = api_client.post("/api/books", json=payload)
    assert r1.status_code == 201
    r2 = api_client.post("/api/books", json=payload)
    assert r2.status_code == 409


def test_post_books_invalid_isbn_returns_422(api_client):
    resp = api_client.post(
        "/api/books",
        json={"isbn": "not-an-isbn", "title": "T", "author": "A"},
    )
    assert resp.status_code == 422


def test_get_books_list_returns_books_with_stats(api_client):
    api_client.post(
        "/api/books",
        json={"isbn": "9780241638194", "title": "T", "author": "A"},
    )
    resp = api_client.get("/api/books")
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body, list)
    assert len(body) == 1
    assert body[0]["isbn13"] == "9780241638194"
    assert "stats" in body[0]
    assert body[0]["stats"]["observation_count"] == 0


def test_get_book_by_id_happy_path(api_client):
    created = api_client.post(
        "/api/books",
        json={"isbn": "9780241638194", "title": "T", "author": "A"},
    ).json()
    resp = api_client.get(f"/api/books/{created['id']}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == created["id"]
    assert "stats" in body


def test_get_book_unknown_id_returns_404(api_client):
    resp = api_client.get("/api/books/99999")
    assert resp.status_code == 404


def test_patch_book_updates_fields(api_client, engine_with_view):
    created = api_client.post(
        "/api/books",
        json={"isbn": "9780241638194", "title": "T", "author": "A"},
    ).json()
    bid = created["id"]
    resp = api_client.patch(
        f"/api/books/{bid}",
        json={"target_price_minor": 500, "status": "bought"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["target_price_minor"] == 500
    assert body["status"] == "bought"

    # Verify the DB row reflects it.
    with Session(engine_with_view) as s:
        db_book = s.get(models.Book, bid)
        assert db_book is not None
        assert db_book.target_price_minor == 500
        assert db_book.status == "bought"


def test_patch_book_unknown_id_returns_404(api_client):
    resp = api_client.patch("/api/books/99999", json={"target_price_minor": 1})
    assert resp.status_code == 404


def test_patch_book_empty_body_is_noop(api_client):
    created = api_client.post(
        "/api/books",
        json={"isbn": "9780241638194", "title": "T", "author": "A"},
    ).json()
    resp = api_client.patch(f"/api/books/{created['id']}", json={})
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == created["title"]
    assert body["author"] == created["author"]
    assert body["status"] == created["status"]


def test_delete_book_soft_delete_default(api_client, engine_with_view):
    created = api_client.post(
        "/api/books",
        json={"isbn": "9780241638194", "title": "T", "author": "A"},
    ).json()
    bid = created["id"]
    resp = api_client.delete(f"/api/books/{bid}")
    assert resp.status_code == 200
    assert resp.json()["status"] == "archived"

    # Row still exists.
    with Session(engine_with_view) as s:
        db_book = s.get(models.Book, bid)
        assert db_book is not None
        assert db_book.status == "archived"


def test_delete_book_hard_removes_row(api_client, engine_with_view):
    created = api_client.post(
        "/api/books",
        json={"isbn": "9780241638194", "title": "T", "author": "A"},
    ).json()
    bid = created["id"]
    resp = api_client.delete(f"/api/books/{bid}?hard=true")
    assert resp.status_code == 200

    # Row gone — GET returns 404.
    resp2 = api_client.get(f"/api/books/{bid}")
    assert resp2.status_code == 404

    with Session(engine_with_view) as s:
        assert s.get(models.Book, bid) is None


def test_get_books_excludes_archived_by_default(api_client):
    created = api_client.post(
        "/api/books",
        json={"isbn": "9780241638194", "title": "T", "author": "A"},
    ).json()
    api_client.delete(f"/api/books/{created['id']}")  # soft-delete

    resp = api_client.get("/api/books")
    assert resp.status_code == 200
    assert resp.json() == []

    resp_all = api_client.get("/api/books?include_archived=true")
    assert resp_all.status_code == 200
    assert len(resp_all.json()) == 1


# --- Task 7.2: observations + stats endpoints --------------------------------


def _seed_book(api_client) -> int:
    return api_client.post(
        "/api/books",
        json={"isbn": "9780241638194", "title": "T", "author": "A"},
    ).json()["id"]


def test_get_observations_happy_path_newest_first(api_client, engine_with_view, make_observation):
    bid = _seed_book(api_client)
    base = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    with Session(engine_with_view) as s:
        for i in range(3):
            make_observation(
                s, book_id=bid,
                observed_at=base + timedelta(hours=i),
                price_minor=500 + i * 10,
            )

    resp = api_client.get(f"/api/books/{bid}/observations")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 3
    # Newest first → totals descend by insertion order index.
    totals = [item["total_minor"] for item in body["items"]]
    assert totals == [520, 510, 500]
    # Page not full (len(items) < default 100) → no cursor.
    assert body["next_before"] is None


def test_get_observations_limit_emits_next_before(api_client, engine_with_view, make_observation):
    bid = _seed_book(api_client)
    base = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    with Session(engine_with_view) as s:
        for i in range(3):
            make_observation(s, book_id=bid, observed_at=base + timedelta(hours=i))

    resp = api_client.get(f"/api/books/{bid}/observations?limit=2")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 2
    # next_before = observed_at of the 2nd (last) row in the page.
    assert body["next_before"] == body["items"][-1]["observed_at"]


def test_get_observations_before_filters(api_client, engine_with_view, make_observation):
    bid = _seed_book(api_client)
    base = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    with Session(engine_with_view) as s:
        for i in range(3):
            make_observation(s, book_id=bid, observed_at=base + timedelta(hours=i))

    cutoff = (base + timedelta(hours=1)).isoformat()
    resp = api_client.get(
        f"/api/books/{bid}/observations",
        params={"before": cutoff},
    )
    assert resp.status_code == 200
    body = resp.json()
    # Strict <: only the row at base+0h is included.
    assert len(body["items"]) == 1
    assert body["items"][0]["observed_at"].startswith("2026-01-01T12:00")


def test_get_observations_source_filter(api_client, engine_with_view, make_observation):
    bid = _seed_book(api_client)
    base = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    with Session(engine_with_view) as s:
        make_observation(s, book_id=bid, observed_at=base, source="wob")
        make_observation(s, book_id=bid, observed_at=base + timedelta(hours=1), source="abebooks")

    resp = api_client.get(f"/api/books/{bid}/observations?source=wob")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["source"] == "wob"


def test_get_observations_excludes_duplicates(api_client, engine_with_view, make_observation):
    bid = _seed_book(api_client)
    base = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    with Session(engine_with_view) as s:
        canonical = make_observation(s, book_id=bid, observed_at=base)
        canonical_id = canonical.id
        make_observation(
            s, book_id=bid,
            observed_at=base + timedelta(hours=1),
            is_duplicate_of=canonical_id,
        )

    resp = api_client.get(f"/api/books/{bid}/observations")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["id"] == canonical_id


def test_get_observations_unknown_book_returns_404(api_client):
    resp = api_client.get("/api/books/99999/observations")
    assert resp.status_code == 404


def test_get_observations_limit_zero_returns_422(api_client):
    bid = _seed_book(api_client)
    resp = api_client.get(f"/api/books/{bid}/observations?limit=0")
    assert resp.status_code == 422


def test_get_observations_limit_too_large_returns_422(api_client):
    bid = _seed_book(api_client)
    resp = api_client.get(f"/api/books/{bid}/observations?limit=10000")
    assert resp.status_code == 422


def test_get_stats_happy_path(api_client, engine_with_view, make_observation):
    bid = _seed_book(api_client)
    base = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    with Session(engine_with_view) as s:
        # Insert in time order; the latest observation (price=500) defines
        # `current_best_total_minor` per the book_stats view semantics.
        for i, price in enumerate([700, 600, 500]):
            make_observation(
                s, book_id=bid,
                observed_at=base + timedelta(hours=i),
                price_minor=price,
            )

    resp = api_client.get(f"/api/books/{bid}/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["book_id"] == bid
    assert body["current_best_total_minor"] == 500
    assert body["all_time_min_total_minor"] == 500
    assert body["all_time_max_total_minor"] == 700
    assert body["observation_count"] == 3
    # sorted_totals is internal — excluded from the wire DTO.
    assert "sorted_totals" not in body


def test_get_stats_zero_observations(api_client):
    bid = _seed_book(api_client)
    resp = api_client.get(f"/api/books/{bid}/stats")
    assert resp.status_code == 200
    body = resp.json()
    assert body["book_id"] == bid
    assert body["current_best_total_minor"] is None
    assert body["observation_count"] == 0


def test_get_stats_unknown_book_returns_404(api_client):
    resp = api_client.get("/api/books/99999/stats")
    assert resp.status_code == 404


# --- Task 7.7: POST /api/books/{id}/refetch ---------------------------------


def test_refetch_triggers_all_enabled_sources(api_client):
    bid = _seed_book(api_client)
    _install_sources(
        api_client,
        wob=SourceConfig(),
        amazon=SourceConfig(),
    )
    resp = api_client.post(f"/api/books/{bid}/refetch")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    sources_triggered = sorted(t["source"] for t in body["triggered"])
    assert sources_triggered == ["amazon", "wob"]
    assert all(t["run_id"] == 42 for t in body["triggered"])
    assert body["skipped"] == []
    # Scheduler was called once per enabled source.
    assert sorted(api_client.app.state.scheduler.calls) == ["amazon", "wob"]


def test_refetch_skips_disabled_sources(api_client):
    bid = _seed_book(api_client)
    _install_sources(
        api_client,
        wob=SourceConfig(),
        amazon=SourceConfig(enabled=False),
    )
    resp = api_client.post(f"/api/books/{bid}/refetch")
    assert resp.status_code == 200
    body = resp.json()
    assert [t["source"] for t in body["triggered"]] == ["wob"]
    assert body["skipped"] == [{"source": "amazon", "reason": "disabled"}]
    # Disabled sources do NOT hit the scheduler.
    assert api_client.app.state.scheduler.calls == ["wob"]


def test_refetch_surfaces_backoff_as_skipped(api_client):
    bid = _seed_book(api_client)
    _install_sources(
        api_client,
        wob=SourceConfig(),
        amazon=SourceConfig(),
    )
    api_client.app.state.scheduler.return_zero_for = {"wob"}
    resp = api_client.post(f"/api/books/{bid}/refetch")
    assert resp.status_code == 200
    body = resp.json()
    assert [t["source"] for t in body["triggered"]] == ["amazon"]
    assert body["skipped"] == [{"source": "wob", "reason": "backoff_active"}]


def test_refetch_unknown_book_returns_404(api_client):
    _install_sources(api_client, wob=SourceConfig())
    resp = api_client.post("/api/books/99999/refetch")
    assert resp.status_code == 404


def test_refetch_no_sources_configured_returns_empty(api_client):
    bid = _seed_book(api_client)
    # `_install_sources()` without kwargs leaves `cfg.sources` empty.
    _install_sources(api_client)
    resp = api_client.post(f"/api/books/{bid}/refetch")
    assert resp.status_code == 200
    assert resp.json() == {"triggered": [], "skipped": []}
    assert api_client.app.state.scheduler.calls == []
