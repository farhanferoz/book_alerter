"""API integration tests for `/api/products/*`.

Mirrors the test_books_api.py pattern: builds a minimal router-only FastAPI
test app via the `api_client` fixture, persists rows via SQLModel directly
where helpful. Covers:

- list / create / get / patch / delete (soft + hard)
- 409 on duplicate ASIN; 422 on garbage ASIN input; 404 on missing id
- ASIN normalization (URL → bare ASIN)
- observations pagination
- refetch fan-out (stub scheduler)
- stats endpoint
- track_used PATCH toggle
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlmodel import Session

from book_alerter.db import models


def _seed_product(
    engine,
    *,
    asin: str = "B07TEST001",
    title: str = "USB-C Adapter",
    track_used: bool = False,
) -> int:
    now = datetime.now(UTC)
    with Session(engine) as s:
        p = models.Product(
            asin=asin, title=title,
            track_used=track_used,
            created_at=now, updated_at=now,
        )
        s.add(p)
        s.commit()
        s.refresh(p)
        return p.id


# --- create ---


def test_post_products_normalises_url_to_asin(api_client) -> None:
    r = api_client.post(
        "/api/products",
        json={
            "asin_or_url": "https://www.amazon.co.uk/dp/B07TEST002",
            "title": "Anker Power Bank",
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["asin"] == "B07TEST002"
    assert body["title"] == "Anker Power Bank"
    assert body["track_used"] is False
    # Stats shape mirrors books — counts 0 / windows present.
    assert body["stats"]["observation_count"] == 0
    assert set(body["stats"]["windows"].keys()) == {"1m", "3m", "12m"}


def test_post_products_with_track_used_true(api_client) -> None:
    r = api_client.post(
        "/api/products",
        json={
            "asin_or_url": "B07TEST003",
            "title": "Vintage Camera",
            "track_used": True,
        },
    )
    assert r.status_code == 201
    assert r.json()["track_used"] is True


def test_post_products_rejects_garbage_asin(api_client) -> None:
    r = api_client.post(
        "/api/products",
        json={"asin_or_url": "not-an-asin", "title": "x"},
    )
    assert r.status_code == 422
    assert "could not extract ASIN" in r.json()["detail"]


def test_post_products_409_on_duplicate_asin(api_client) -> None:
    _seed_product(api_client.app.state.engine, asin="B07TEST004")
    r = api_client.post(
        "/api/products",
        json={"asin_or_url": "B07TEST004", "title": "duplicate"},
    )
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["asin"] == "B07TEST004"
    assert isinstance(detail["product_id"], int)


# --- list / get ---


def test_get_products_excludes_archived_by_default(api_client) -> None:
    eng = api_client.app.state.engine
    _seed_product(eng, asin="B07ACTIVE0")
    archived_id = _seed_product(eng, asin="B07ARCHV00")
    with Session(eng) as s:
        p = s.get(models.Product, archived_id)
        p.status = "archived"
        s.add(p)
        s.commit()

    r = api_client.get("/api/products")
    assert r.status_code == 200
    asins = {p["asin"] for p in r.json()}
    assert "B07ACTIVE0" in asins
    assert "B07ARCHV00" not in asins

    r2 = api_client.get("/api/products?include_archived=true")
    asins2 = {p["asin"] for p in r2.json()}
    assert "B07ARCHV00" in asins2


def test_get_product_by_id(api_client) -> None:
    pid = _seed_product(api_client.app.state.engine, asin="B07GETTES1")
    r = api_client.get(f"/api/products/{pid}")
    assert r.status_code == 200
    assert r.json()["asin"] == "B07GETTES1"


def test_get_product_404_on_missing(api_client) -> None:
    r = api_client.get("/api/products/9999")
    assert r.status_code == 404


# --- patch ---


def test_patch_product_target_price_and_track_used(api_client) -> None:
    pid = _seed_product(api_client.app.state.engine, asin="B07PATCH01")
    r = api_client.patch(
        f"/api/products/{pid}",
        json={"target_price_minor": 1500, "track_used": True},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["target_price_minor"] == 1500
    assert body["track_used"] is True


def test_patch_product_status_archived(api_client) -> None:
    pid = _seed_product(api_client.app.state.engine, asin="B07PATCH02")
    r = api_client.patch(f"/api/products/{pid}", json={"status": "archived"})
    assert r.status_code == 200
    assert r.json()["status"] == "archived"


def test_patch_product_404_on_missing(api_client) -> None:
    r = api_client.patch("/api/products/9999", json={"target_price_minor": 100})
    assert r.status_code == 404


def test_patch_product_empty_body_is_noop(api_client) -> None:
    pid = _seed_product(api_client.app.state.engine, asin="B07PATCH03")
    r = api_client.patch(f"/api/products/{pid}", json={})
    assert r.status_code == 200


# --- delete ---


def test_delete_product_soft_marks_archived(api_client) -> None:
    pid = _seed_product(api_client.app.state.engine, asin="B07DEL0001")
    r = api_client.delete(f"/api/products/{pid}")
    assert r.status_code == 200
    assert r.json()["status"] == "archived"
    # Row still exists.
    assert api_client.get(f"/api/products/{pid}").status_code == 200


def test_delete_product_hard_removes_row(api_client) -> None:
    pid = _seed_product(api_client.app.state.engine, asin="B07DEL0002")
    r = api_client.delete(f"/api/products/{pid}?hard=true")
    assert r.status_code == 200
    # Row gone — 404 on subsequent fetch.
    assert api_client.get(f"/api/products/{pid}").status_code == 404


# --- observations ---


def test_get_product_observations_reports_last_seen_from_same_row(api_client) -> None:
    """A row re-seen later carries its own updated `last_seen_at` (migration
    0021, T3.2 heartbeat compaction — `scheduler._persist` updates the
    existing row in place instead of inserting an `is_duplicate_of`-pointing
    duplicate), so the observations endpoint returns exactly one item for
    one offer regardless of how many times it was re-confirmed."""
    eng = api_client.app.state.engine
    pid = _seed_product(eng, asin="B07OBS0001")
    now = datetime.now(UTC)
    with Session(eng) as s:
        obs = models.ProductObservation(
            product_id=pid, source="amazon_uk_product", condition="new",
            price_minor=999, currency="GBP", shipping_minor=0,
            total_minor=999, url="https://amazon.co.uk/x", observed_at=now,
            last_seen_at=now + timedelta(minutes=1),
        )
        s.add(obs)
        s.commit()

    r = api_client.get(f"/api/products/{pid}/observations")
    assert r.status_code == 200
    body = r.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["price_minor"] == 999


def test_get_product_observations_404_on_missing_product(api_client) -> None:
    r = api_client.get("/api/products/9999/observations")
    assert r.status_code == 404


# --- refetch ---


def test_refetch_product_fans_out_to_enabled_sources(api_client) -> None:
    pid = _seed_product(api_client.app.state.engine, asin="B07REF0001")
    # Configure sources so the fan-out has something to dispatch to.
    cfg = api_client.app.state.config
    from book_alerter.config import SourceConfig
    from book_alerter.enums import ItemKind

    cfg.sources["amazon_uk_product"] = SourceConfig(
        enabled=True, item_kinds=[ItemKind.PRODUCT],
    )
    cfg.sources["disabled_one"] = SourceConfig(
        enabled=False, item_kinds=[ItemKind.PRODUCT],
    )
    # Book-only source should NOT be fired by a product refetch (filtered
    # out as kind_unsupported per the refetch-scoping fix).
    cfg.sources["wob"] = SourceConfig(
        enabled=True, item_kinds=[ItemKind.BOOK],
    )

    r = api_client.post(f"/api/products/{pid}/refetch")
    assert r.status_code == 200
    body = r.json()
    triggered_names = {t["source"] for t in body["triggered"]}
    skipped = {s["source"]: s["reason"] for s in body["skipped"]}
    assert "amazon_uk_product" in triggered_names
    assert "wob" not in triggered_names, (
        "book-only source must NOT be triggered by a product refetch"
    )
    assert skipped.get("disabled_one") == "disabled"
    assert skipped.get("wob") == "kind_unsupported"


def test_refetch_product_404_on_missing(api_client) -> None:
    r = api_client.post("/api/products/9999/refetch")
    assert r.status_code == 404


# --- stats ---


def test_get_product_stats_shape(api_client) -> None:
    pid = _seed_product(api_client.app.state.engine, asin="B07STA0001")
    r = api_client.get(f"/api/products/{pid}/stats")
    assert r.status_code == 200
    body = r.json()
    assert body["book_id"] == pid  # field name reused — documented in plan doc
    assert set(body["windows"].keys()) == {"1m", "3m", "12m"}
    assert body["observation_count"] == 0


def test_get_product_stats_404_on_missing(api_client) -> None:
    r = api_client.get("/api/products/9999/stats")
    assert r.status_code == 404
