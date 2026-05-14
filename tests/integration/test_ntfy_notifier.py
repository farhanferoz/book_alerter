"""Integration tests for NtfyNotifier using httpx.MockTransport (no live HTTP)."""
from __future__ import annotations

from datetime import UTC, datetime

import httpx
import pytest

from book_alerter.config import NtfyChannelConfig
from book_alerter.db import models
from book_alerter.notifications.ntfy import NtfyNotifier


def _make_alert(**overrides) -> models.Alert:
    defaults = dict(
        book_id=1,
        kind="target_hit",
        price_minor=900,
        currency="GBP",
        source="amazon",
        condition="new",
        message="Target hit: GBP 9.00",
        fired_at=datetime.now(UTC),
    )
    defaults.update(overrides)
    return models.Alert(**defaults)


def _make_book(**overrides) -> models.Book:
    now = datetime.now(UTC)
    defaults = dict(
        isbn13="9780000000000",
        title="The Great Book",
        author="A. Author",
        created_at=now,
        updated_at=now,
    )
    defaults.update(overrides)
    return models.Book(**defaults)


def _client_factory_from_transport(transport: httpx.MockTransport):
    def _factory() -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=transport, timeout=10)
    return _factory


async def test_happy_path_posts_to_ntfy_with_correct_url_headers_and_body():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["method"] = request.method
        captured["headers"] = dict(request.headers)
        captured["content"] = request.content
        return httpx.Response(200, text="ok")

    cfg = NtfyChannelConfig(
        enabled=True,
        server="https://ntfy.sh",
        topic="my-topic",
        priority="high",
        tags=["book", "money", "uk"],
    )
    notifier = NtfyNotifier(
        cfg, client_factory=_client_factory_from_transport(httpx.MockTransport(handler))
    )

    alert = _make_alert(message="Target hit: GBP 9.00")
    book = _make_book(title="The Great Book")

    result = await notifier.send(alert, book)

    assert result == {"status": "sent"}
    assert captured["method"] == "POST"
    assert captured["url"] == "https://ntfy.sh/my-topic"
    assert "target_hit" in captured["headers"]["title"]
    assert "The Great Book" in captured["headers"]["title"]
    assert captured["headers"]["priority"] == "high"
    assert captured["headers"]["tags"] == "book,money,uk"
    assert captured["content"] == b"Target hit: GBP 9.00"


async def test_happy_path_strips_trailing_slash_from_server():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        return httpx.Response(200)

    cfg = NtfyChannelConfig(
        enabled=True, server="https://ntfy.sh/", topic="t", tags=["a"]
    )
    notifier = NtfyNotifier(
        cfg, client_factory=_client_factory_from_transport(httpx.MockTransport(handler))
    )
    result = await notifier.send(_make_alert(), _make_book())
    assert result == {"status": "sent"}
    assert captured["url"] == "https://ntfy.sh/t"


async def test_5xx_returns_error_status_with_message():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="service unavailable")

    cfg = NtfyChannelConfig(enabled=True, topic="t")
    notifier = NtfyNotifier(
        cfg, client_factory=_client_factory_from_transport(httpx.MockTransport(handler))
    )

    result = await notifier.send(_make_alert(), _make_book())

    assert result["status"] == "error"
    assert result["error_message"]
    assert isinstance(result["error_message"], str)


async def test_disabled_config_does_not_make_http_call():
    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("NtfyNotifier must not make an HTTP call when disabled")

    cfg = NtfyChannelConfig(enabled=False, topic="t")
    notifier = NtfyNotifier(
        cfg, client_factory=_client_factory_from_transport(httpx.MockTransport(handler))
    )
    result = await notifier.send(_make_alert(), _make_book())
    assert result["status"] == "error"
    assert result["error_message"]


async def test_empty_topic_does_not_make_http_call():
    def handler(request: httpx.Request) -> httpx.Response:
        pytest.fail("NtfyNotifier must not make an HTTP call when topic is empty")

    cfg = NtfyChannelConfig(enabled=True, topic="")
    notifier = NtfyNotifier(
        cfg, client_factory=_client_factory_from_transport(httpx.MockTransport(handler))
    )
    result = await notifier.send(_make_alert(), _make_book())
    assert result["status"] == "error"
    assert result["error_message"]


async def test_tags_joined_with_commas_when_multiple():
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["headers"] = dict(request.headers)
        return httpx.Response(200)

    cfg = NtfyChannelConfig(
        enabled=True, topic="t", tags=["book", "money", "uk", "alert"]
    )
    notifier = NtfyNotifier(
        cfg, client_factory=_client_factory_from_transport(httpx.MockTransport(handler))
    )
    result = await notifier.send(_make_alert(), _make_book())
    assert result == {"status": "sent"}
    assert captured["headers"]["tags"] == "book,money,uk,alert"
