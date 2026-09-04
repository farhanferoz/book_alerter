"""Alerts feed across BOTH item kinds (finding F8, plan task T4.5).

Product alerts were being written to `productalert` and then never shown:
`api/alerts.py` had no product path at all, so a product could fire an alert
that no surface in the application would ever display. These tests pin the
union feed, the per-kind filter, and dismissal on both tables.

The product-alert factory lives here rather than in `conftest.py` so that this
file is self-contained; it shares one row-builder with the book case so the two
kinds cannot drift.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from http import HTTPStatus

import pytest
from sqlmodel import Session

from book_alerter.db import models
from book_alerter.enums import AlertKind, ItemKind

BASE = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _alert_row(model, fk_attr: str, item_id: int, **kw):
    """Build an Alert or ProductAlert; the two models are field-identical
    apart from the FK column name."""
    defaults = {
        "kind": AlertKind.TARGET_HIT,
        "price_minor": 500,
        "currency": "GBP",
        "source": "wob",
        "condition": "used_g",
        "message": "test alert",
        "fired_at": BASE,
        "dismissed_at": None,
        "delivered_via": [],
    }
    defaults.update(kw)
    return model(**{fk_attr: item_id}, **defaults)


@pytest.fixture
def make_any_alert():
    def _make(session: Session, *, item_kind: ItemKind, item_id: int, **kw):
        model, fk = (
            (models.Alert, "book_id")
            if item_kind is ItemKind.BOOK
            else (models.ProductAlert, "product_id")
        )
        row = _alert_row(model, fk, item_id, **kw)
        session.add(row)
        session.commit()
        session.refresh(row)
        return row

    return _make


def _seed_book(api_client, isbn: str = "9780241638194") -> int:
    return api_client.post(
        "/api/books", json={"isbn": isbn, "title": "A Book", "author": "A"}
    ).json()["id"]


def _seed_product(api_client, asin: str = "B09B96TG33") -> int:
    resp = api_client.post(
        "/api/products", json={"asin_or_url": asin, "title": "A Product"}
    )
    assert resp.status_code in (HTTPStatus.OK, HTTPStatus.CREATED), resp.text
    return resp.json()["id"]


def test_feed_merges_books_and_products_newest_first(
    api_client, engine_with_view, make_any_alert
):
    bid = _seed_book(api_client)
    pid = _seed_product(api_client)
    with Session(engine_with_view) as s:
        make_any_alert(s, item_kind=ItemKind.BOOK, item_id=bid, fired_at=BASE,
                       message="book-older")
        make_any_alert(s, item_kind=ItemKind.PRODUCT, item_id=pid,
                       fired_at=BASE + timedelta(hours=1), message="product-newer")

    body = api_client.get("/api/alerts").json()
    assert [i["message"] for i in body["items"]] == ["product-newer", "book-older"]
    kinds = [i["item_kind"] for i in body["items"]]
    assert kinds == [ItemKind.PRODUCT, ItemKind.BOOK]
    # every row identifies its item and carries a display title
    assert body["items"][0]["item_id"] == pid
    assert body["items"][0]["title"] == "A Product"
    assert body["items"][1]["item_id"] == bid
    assert body["items"][1]["title"] == "A Book"


def test_item_kind_filter_restricts_to_one_table(
    api_client, engine_with_view, make_any_alert
):
    bid = _seed_book(api_client)
    pid = _seed_product(api_client)
    with Session(engine_with_view) as s:
        make_any_alert(s, item_kind=ItemKind.BOOK, item_id=bid, fired_at=BASE)
        make_any_alert(s, item_kind=ItemKind.PRODUCT, item_id=pid, fired_at=BASE)

    only_products = api_client.get("/api/alerts?item_kind=product").json()
    assert [i["item_kind"] for i in only_products["items"]] == [ItemKind.PRODUCT]
    only_books = api_client.get("/api/alerts?item_kind=book").json()
    assert [i["item_kind"] for i in only_books["items"]] == [ItemKind.BOOK]


def test_dismiss_product_alert_is_idempotent(
    api_client, engine_with_view, make_any_alert
):
    pid = _seed_product(api_client)
    with Session(engine_with_view) as s:
        aid = make_any_alert(
            s, item_kind=ItemKind.PRODUCT, item_id=pid, fired_at=BASE
        ).id

    first = api_client.post(f"/api/alerts/product/{aid}/dismiss")
    assert first.status_code == HTTPStatus.OK, first.text
    stamp = first.json()["dismissed_at"]
    assert stamp is not None

    again = api_client.post(f"/api/alerts/product/{aid}/dismiss")
    assert again.status_code == HTTPStatus.OK
    assert again.json()["dismissed_at"] == stamp, "re-dismiss must not move the stamp"


def test_dismiss_uses_the_kind_not_just_the_id(
    api_client, engine_with_view, make_any_alert
):
    """Ids are unique only within their own table, so the same id can exist in
    both. Dismissing one kind must leave the other alone."""
    bid = _seed_book(api_client)
    pid = _seed_product(api_client)
    with Session(engine_with_view) as s:
        book_aid = make_any_alert(
            s, item_kind=ItemKind.BOOK, item_id=bid, fired_at=BASE
        ).id
        prod_aid = make_any_alert(
            s, item_kind=ItemKind.PRODUCT, item_id=pid, fired_at=BASE
        ).id
    assert book_aid == prod_aid, "precondition: colliding ids across the two tables"

    api_client.post(f"/api/alerts/product/{prod_aid}/dismiss")
    active = api_client.get("/api/alerts?dismissed=false").json()
    assert [i["item_kind"] for i in active["items"]] == [ItemKind.BOOK]


def test_dismiss_all_covers_both_tables(api_client, engine_with_view, make_any_alert):
    bid = _seed_book(api_client)
    pid = _seed_product(api_client)
    with Session(engine_with_view) as s:
        make_any_alert(s, item_kind=ItemKind.BOOK, item_id=bid, fired_at=BASE)
        make_any_alert(s, item_kind=ItemKind.PRODUCT, item_id=pid, fired_at=BASE)

    resp = api_client.post("/api/alerts/dismiss-all")
    assert resp.status_code == HTTPStatus.OK, resp.text
    expected_dismissed = 2  # one book alert + one product alert
    assert resp.json()["dismissed_count"] == expected_dismissed, (
        "must sweep the product table too"
    )
    assert api_client.get("/api/alerts?dismissed=false").json()["items"] == []


def test_missing_alert_is_404_per_kind(api_client, engine_with_view):
    assert api_client.post("/api/alerts/product/99999/dismiss").status_code == HTTPStatus.NOT_FOUND
    assert api_client.post("/api/alerts/book/99999/dismiss").status_code == HTTPStatus.NOT_FOUND
