"""ntfy.sh push notifier. POSTs alert message + priority + tags to
`<server>/<topic>`. HTTP errors are surfaced as
`{"status": "error", "error_message": str}` so the dispatcher records a failed
delivery rather than treating it as an unexpected exception."""
from __future__ import annotations

import base64
from collections.abc import Callable

import httpx

from book_alerter.config import NtfyChannelConfig
from book_alerter.http_client import shared_or_fresh
from book_alerter.notifications.base import (
    AlertLike,
    ItemLike,
    NotificationResult,
    Notifier,
)


def _encode_title(s: str) -> str:
    """Pass-through for ASCII; RFC 2047 base64 encoded-word for non-ASCII.

    httpx enforces ASCII on header values, but ntfy decodes encoded-word
    titles (`=?utf-8?b?...?=`) on the client side, so non-ASCII book titles
    round-trip cleanly.
    """
    if s.isascii():
        return s
    b64 = base64.b64encode(s.encode("utf-8")).decode("ascii")
    return f"=?utf-8?b?{b64}?="


class NtfyNotifier(Notifier):
    name = "ntfy"

    def __init__(
        self,
        cfg: NtfyChannelConfig,
        client_factory: Callable[[], httpx.AsyncClient] | None = None,
        *,
        http: httpx.AsyncClient | None = None,
    ) -> None:
        """`client_factory` is the legacy per-send constructor kept for unit-
        test injection; if a test passes one it wins over the shared `http`."""
        self._cfg = cfg
        self._http = http
        self._client_factory = client_factory

    async def send(self, alert: AlertLike, item: ItemLike) -> NotificationResult:
        if not self._cfg.enabled or not self._cfg.topic:
            return {"status": "error", "error_message": "ntfy disabled or topic missing"}
        url = f"{self._cfg.server.rstrip('/')}/{self._cfg.topic}"
        body = alert.message
        headers = {
            "Title": _encode_title(f"{alert.kind} - {item.title}"),
            "Priority": self._cfg.priority,
            "Tags": ",".join(self._cfg.tags),
        }
        try:
            if self._client_factory is not None:
                async with self._client_factory() as client:
                    resp = await client.post(
                        url, content=body.encode("utf-8"), headers=headers,
                    )
                    resp.raise_for_status()
            else:
                # Per-call 10s overrides the shared-client default so ntfy
                # stays the short-timeout channel the original design called for.
                async with shared_or_fresh(self._http) as client:
                    resp = await client.post(
                        url, content=body.encode("utf-8"), headers=headers,
                        timeout=10,
                    )
                    resp.raise_for_status()
        except httpx.HTTPError as e:
            return {"status": "error", "error_message": str(e)}
        return {"status": "sent"}
