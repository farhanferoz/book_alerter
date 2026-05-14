"""Integration tests for the Alerts endpoints (Task 7.3)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlmodel import Session


def _seed_book(api_client, isbn: str = "9780241638194") -> int:
    return api_client.post(
        "/api/books",
        json={"isbn": isbn, "title": "T", "author": "A"},
    ).json()["id"]


# --- GET /api/alerts ---------------------------------------------------------


def test_get_alerts_newest_first_across_books(api_client, engine_with_view, make_alert):
    b1 = _seed_book(api_client, "9780241638194")
    b2 = _seed_book(api_client, "9780140449266")
    base = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    with Session(engine_with_view) as s:
        make_alert(s, book_id=b1, fired_at=base, message="oldest")
        make_alert(s, book_id=b2, fired_at=base + timedelta(hours=1), message="middle")
        make_alert(s, book_id=b1, fired_at=base + timedelta(hours=2), message="newest")

    resp = api_client.get("/api/alerts")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    msgs = [item["message"] for item in body["items"]]
    assert msgs == ["newest", "middle", "oldest"]
    assert body["next_before"] is None


def test_get_alerts_kind_filter(api_client, engine_with_view, make_alert):
    bid = _seed_book(api_client)
    base = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    with Session(engine_with_view) as s:
        make_alert(s, book_id=bid, fired_at=base, kind="target_hit")
        make_alert(s, book_id=bid, fired_at=base + timedelta(hours=1), kind="new_low")
        make_alert(s, book_id=bid, fired_at=base + timedelta(hours=2), kind="target_hit")

    resp = api_client.get("/api/alerts?kind=target_hit")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 2
    assert all(item["kind"] == "target_hit" for item in body["items"])


def test_get_alerts_dismissed_false_returns_only_undismissed(
    api_client, engine_with_view, make_alert
):
    bid = _seed_book(api_client)
    base = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    with Session(engine_with_view) as s:
        make_alert(s, book_id=bid, fired_at=base, message="undismissed")
        make_alert(
            s, book_id=bid, fired_at=base + timedelta(hours=1),
            message="dismissed",
            dismissed_at=base + timedelta(hours=2),
        )

    resp = api_client.get("/api/alerts?dismissed=false")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["message"] == "undismissed"
    assert body["items"][0]["dismissed_at"] is None


def test_get_alerts_dismissed_true_returns_only_dismissed(
    api_client, engine_with_view, make_alert
):
    bid = _seed_book(api_client)
    base = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    with Session(engine_with_view) as s:
        make_alert(s, book_id=bid, fired_at=base, message="undismissed")
        make_alert(
            s, book_id=bid, fired_at=base + timedelta(hours=1),
            message="dismissed",
            dismissed_at=base + timedelta(hours=2),
        )

    resp = api_client.get("/api/alerts?dismissed=true")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["items"]) == 1
    assert body["items"][0]["message"] == "dismissed"
    assert body["items"][0]["dismissed_at"] is not None


def test_get_alerts_cursor_pagination(api_client, engine_with_view, make_alert):
    bid = _seed_book(api_client)
    base = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    with Session(engine_with_view) as s:
        for i in range(4):
            make_alert(s, book_id=bid, fired_at=base + timedelta(hours=i), message=f"a{i}")

    resp1 = api_client.get("/api/alerts?limit=2")
    assert resp1.status_code == 200
    page1 = resp1.json()
    assert len(page1["items"]) == 2
    # Newest first: a3, a2
    assert [it["message"] for it in page1["items"]] == ["a3", "a2"]
    assert page1["next_before"] == page1["items"][-1]["fired_at"]

    resp2 = api_client.get(f"/api/alerts?limit=2&before={page1['next_before']}")
    assert resp2.status_code == 200
    page2 = resp2.json()
    assert len(page2["items"]) == 2
    assert [it["message"] for it in page2["items"]] == ["a1", "a0"]

    # No overlap between page1 and page2.
    ids1 = {it["id"] for it in page1["items"]}
    ids2 = {it["id"] for it in page2["items"]}
    assert ids1.isdisjoint(ids2)


def test_get_alerts_empty(api_client):
    resp = api_client.get("/api/alerts")
    assert resp.status_code == 200
    assert resp.json() == {"items": [], "next_before": None}


# --- POST /api/alerts/{id}/dismiss ------------------------------------------


def test_post_dismiss_happy_path(api_client, engine_with_view, make_alert):
    bid = _seed_book(api_client)
    base = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    with Session(engine_with_view) as s:
        alert = make_alert(s, book_id=bid, fired_at=base)
        aid = alert.id

    resp = api_client.post(f"/api/alerts/{aid}/dismiss")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["id"] == aid
    assert body["dismissed_at"] is not None

    # Subsequent GET reflects the dismissal.
    feed = api_client.get("/api/alerts").json()
    assert feed["items"][0]["dismissed_at"] is not None


def test_post_dismiss_idempotent_preserves_original(
    api_client, engine_with_view, make_alert
):
    bid = _seed_book(api_client)
    base = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    pre_dismissed = datetime(2026, 1, 2, 9, 0, tzinfo=UTC)
    with Session(engine_with_view) as s:
        alert = make_alert(
            s, book_id=bid, fired_at=base, dismissed_at=pre_dismissed
        )
        aid = alert.id

    resp = api_client.post(f"/api/alerts/{aid}/dismiss")
    assert resp.status_code == 200
    body = resp.json()
    # Original timestamp preserved — not overwritten.
    assert body["dismissed_at"].startswith("2026-01-02T09:00")


def test_post_dismiss_unknown_id_returns_404(api_client):
    resp = api_client.post("/api/alerts/99999/dismiss")
    assert resp.status_code == 404


# --- POST /api/alerts/dismiss-all -------------------------------------------


def test_post_dismiss_all_bulk(api_client, engine_with_view, make_alert):
    bid = _seed_book(api_client)
    base = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    pre_dismissed = datetime(2026, 1, 2, 9, 0, tzinfo=UTC)
    with Session(engine_with_view) as s:
        make_alert(s, book_id=bid, fired_at=base, message="u1")
        make_alert(s, book_id=bid, fired_at=base + timedelta(hours=1), message="u2")
        already = make_alert(
            s,
            book_id=bid,
            fired_at=base + timedelta(hours=2),
            message="already",
            dismissed_at=pre_dismissed,
        )
        already_id = already.id

    resp = api_client.post("/api/alerts/dismiss-all")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"dismissed_count": 2}

    # Previously-dismissed alert retains original timestamp.
    feed = api_client.get("/api/alerts").json()
    for item in feed["items"]:
        if item["id"] == already_id:
            assert item["dismissed_at"].startswith("2026-01-02T09:00")
        else:
            assert item["dismissed_at"] is not None


def test_post_dismiss_all_no_undismissed(api_client, engine_with_view, make_alert):
    bid = _seed_book(api_client)
    base = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
    with Session(engine_with_view) as s:
        make_alert(
            s, book_id=bid, fired_at=base,
            dismissed_at=base + timedelta(hours=1),
        )

    resp = api_client.post("/api/alerts/dismiss-all")
    assert resp.status_code == 200
    assert resp.json() == {"dismissed_count": 0}
