"""Integration tests for the notifications-test endpoint (Task 7.7).

Covers `POST /api/notifications/{channel}/test` — the synthetic-alert dispatch
that lets users verify their channel config end-to-end without firing a real
alert. The `_StubNotifier` attached by `api_client` under name `"stub"` is the
test-side counterpart of `_StubScheduler` for the refetch endpoint.
"""

from __future__ import annotations

from sqlmodel import Session, select

from book_alerter.db import models


def test_test_notification_happy_path_calls_stub_with_synthetic_book(api_client):
    resp = api_client.post("/api/notifications/stub/test")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["channel"] == "stub"
    assert body["status"] == "sent"
    assert body["error_message"] is None
    # Synthetic alert is surfaced on the wire (not persisted — see separate test).
    assert body["alert"]["kind"] == "target_hit"
    assert body["alert"]["message"] == "This is a test notification from Book Alerter."

    stub = api_client.app.state.notifiers["stub"]
    assert len(stub.calls) == 1
    alert, book = stub.calls[0]
    assert book.isbn13 == "9780000000007"
    assert book.title == "Test Book"
    assert alert.kind == "target_hit"
    assert alert.message == "This is a test notification from Book Alerter."


def test_test_notification_surfaces_error_result(api_client):
    api_client.app.state.notifiers["stub"].next_result = {
        "status": "error",
        "error_message": "boom",
    }
    resp = api_client.post("/api/notifications/stub/test")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "error"
    assert body["error_message"] == "boom"


def test_test_notification_unknown_channel_returns_404(api_client):
    resp = api_client.post("/api/notifications/unknown/test")
    assert resp.status_code == 404
    assert "unknown" in resp.json()["detail"]


def test_test_notification_does_not_persist_alert(api_client, engine_with_view):
    resp = api_client.post("/api/notifications/stub/test")
    assert resp.status_code == 200
    # Synthetic alert is in-memory only — Alert table stays empty.
    with Session(engine_with_view) as s:
        rows = s.exec(select(models.Alert)).all()
        assert rows == []
