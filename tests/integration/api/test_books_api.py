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
    existing_id = r1.json()["id"]
    r2 = api_client.post("/api/books", json=payload)
    assert r2.status_code == 409
    detail = r2.json()["detail"]
    # FE consumes book_id to render a "View book" link in the Add-book modal
    # error state, so pin the contract here.
    assert detail["book_id"] == existing_id
    assert detail["isbn13"] == "9780241638194"


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


def test_get_books_list_reuses_medians_cache_within_ttl(api_client, engine_with_view):
    """GET /api/books must read `app.state.medians_cache` (T3.4) rather than
    recomputing `source_seller_global_shipping_medians` on every render:
    a shipping-median shift after the first call must NOT show up in the
    second call's cascade estimate while the cache is still fresh, and MUST
    show up immediately after `invalidate()`.

    The cascade's tier-1 (`book_source_medians`, this book's own
    observations for the source) is deliberately NOT cached — it's cheap
    (already fetched by `compute_stats_for_items`'s query 2) and must
    always reflect this book's latest data. Only tier-2
    (`source_seller_global_shipping_medians`, cross-book) is cached, so
    the shipping observations driving the median shift must live on a
    *different* book (`donor_bid`) than the one whose stats we read
    (`bid`) — otherwise the shift would show up via tier-1 regardless of
    caching and the test would prove nothing about the cache.
    """
    bid = _seed_book(api_client)
    donor_bid = api_client.post(
        "/api/books",
        json={"isbn": "9780099490548", "title": "Donor", "author": "A"},
    ).json()["id"]
    now = datetime.now(UTC)

    def _amazon_obs(shipping: int, i: int) -> models.PriceObservation:
        return models.PriceObservation(
            book_id=donor_bid, source="amazon", seller="Amazon", condition="new",
            price_minor=1000, currency="GBP", shipping_minor=shipping,
            total_minor=1000 + shipping, url=f"https://amazon/{i}",
            observed_at=now - timedelta(days=i), last_seen_at=now - timedelta(days=i),
            raw={},
        )

    with Session(engine_with_view) as s:
        # 5 rows on the DONOR book clear the default
        # min_global_median_observations=5 threshold; all at shipping=100
        # -> global median = 100. `bid` itself gets none, so its own
        # book_source_medians (tier 1) is empty and it must fall through
        # to tier 2.
        for i in range(5):
            s.add(_amazon_obs(100, i + 1))
        # The current live offer on `bid`: same (source, seller) bucket,
        # shipping UNKNOWN -> its effective total depends on the cascade's
        # tier-2 (source, seller_class) global median.
        s.add(models.PriceObservation(
            book_id=bid, source="amazon", seller="Amazon", condition="new",
            price_minor=2000, currency="GBP", shipping_minor=None,
            total_minor=2000, url="https://amazon/live",
            observed_at=now, last_seen_at=now, raw={},
        ))
        s.commit()

    def _stats_for(bid: int) -> dict:
        body = api_client.get("/api/books").json()
        return next(b["stats"] for b in body if b["id"] == bid)

    first = _stats_for(bid)
    assert first["shipping_estimate_minor"] == 100

    with Session(engine_with_view) as s:
        # 5 more rows on the DONOR book at shipping=900 would shift the
        # global median to 500 if recomputed now (ten values: five 100s,
        # five 900s) -- `bid`'s own data is untouched.
        for i in range(5):
            s.add(_amazon_obs(900, i + 10))
        s.commit()

    second = _stats_for(bid)
    assert second["shipping_estimate_minor"] == 100, (
        "medians cache should still be serving the pre-TTL-expiry value"
    )

    api_client.app.state.medians_cache.invalidate()
    third = _stats_for(bid)
    assert third["shipping_estimate_minor"] == 500, (
        "invalidate() must force a recompute reflecting the new rows"
    )


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


def test_delete_book_hard_cascades_to_child_tables(api_client, engine_with_view):
    """A hard-delete must remove the book's PriceObservation / Alert /
    NotificationDelivery / BookSignalState rows in the same transaction.
    The cascade is now enforced by the schema (migration 0013 + the
    `PRAGMA foreign_keys=ON` set in `db/session.py`), replacing the
    hand-cascade that used to live in `delete_book`. Without this
    enforcement, orphan child rows would have the dashboard render
    "missing-book" entries.
    """
    created = api_client.post(
        "/api/books",
        json={"isbn": "9780241638194", "title": "T", "author": "A"},
    ).json()
    bid = created["id"]

    # Seed each child table directly with at least one row pointing at this
    # book / its alerts, exercising every cascade path declared in 0013.
    now = datetime.now(UTC)
    with Session(engine_with_view) as s:
        obs = models.PriceObservation(
            book_id=bid, source="amazon", condition="new",
            price_minor=1000, currency="GBP", total_minor=1000,
            url="https://x", observed_at=now, last_seen_at=now, raw={},
        )
        alert = models.Alert(
            book_id=bid, kind="target_hit", price_minor=1000, currency="GBP",
            source="amazon", condition="new", message="m", fired_at=now,
            delivered_via=[],
        )
        state = models.BookSignalState(book_id=bid, last_signal="BUY")
        s.add_all([obs, alert, state])
        s.commit()
        s.refresh(alert)
        delivery = models.NotificationDelivery(
            alert_id=alert.id, channel="ntfy", sent_at=now, status="sent",
        )
        s.add(delivery)
        s.commit()
        obs_id, alert_id, delivery_id = obs.id, alert.id, delivery.id

    resp = api_client.delete(f"/api/books/{bid}?hard=true")
    assert resp.status_code == 200

    with Session(engine_with_view) as s:
        assert s.get(models.Book, bid) is None
        assert s.get(models.PriceObservation, obs_id) is None
        assert s.get(models.Alert, alert_id) is None
        assert s.get(models.NotificationDelivery, delivery_id) is None
        assert s.get(models.BookSignalState, bid) is None


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


def test_get_observations_surfaces_latest_sighting_url_and_last_seen(
    api_client, engine_with_view, make_observation
):
    """A row re-seen later must report the LATEST sighting's url + last_seen,
    while observed_at stays the first sighting.

    Mirrors the production bug migration 0019 fixed (a frozen stale link/
    timestamp): the chart needs first-seen (observed_at) for the timeline,
    the breakdown needs last-seen + the fresh link. Since migration 0021
    (T3.2, heartbeat compaction), a re-sighting updates `last_seen_at`/`url`
    on the SAME row (`scheduler._persist`) instead of inserting a duplicate
    row — modelled here directly rather than via two `make_observation` calls.
    """
    bid = _seed_book(api_client)
    base = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    with Session(engine_with_view) as s:
        canonical = make_observation(
            s, book_id=bid, observed_at=base,
            url="https://example.com/fresh-latest",
            last_seen_at=base + timedelta(days=3),
        )
        canonical_id = canonical.id

    body = api_client.get(f"/api/books/{bid}/observations").json()
    assert len(body["items"]) == 1
    item = body["items"][0]
    assert item["id"] == canonical_id
    assert item["observed_at"].startswith("2026-01-01T12:00")  # first-seen kept
    assert item["last_seen"].startswith("2026-01-04T12:00")  # latest sighting
    assert item["url"] == "https://example.com/fresh-latest"  # fresh link, not stale


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
