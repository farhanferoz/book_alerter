"""ntfy.sh push notifier. POSTs alert message + priority + tags to
`<server>/<topic>`. HTTP errors are surfaced as
`{"status": "error", "error_message": str}` so the dispatcher records a failed
delivery rather than treating it as an unexpected exception."""
from __future__ import annotations

import base64
from collections.abc import Callable
from contextlib import asynccontextmanager

import httpx

from book_alerter.config import NtfyChannelConfig
from book_alerter.db.models import Alert, Book
from book_alerter.notifications.base import NotificationResult, Notifier


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
        """`http` is the lifespan-scoped shared client; `client_factory` is
        the legacy per-send constructor kept for unit-test injection. When
        both are None we create a fresh client per send (back-compat with
        ad-hoc CLI / script use). `http` wins when both are provided."""
        self._cfg = cfg
        self._http = http
        self._client_factory = client_factory or (lambda: httpx.AsyncClient(timeout=10))

    @asynccontextmanager
    async def _client(self):
        if self._http is not None:
            # Don't close the shared client — caller owns its lifecycle.
            yield self._http
        else:
            async with self._client_factory() as c:
                yield c

    async def send(self, alert: Alert, book: Book) -> NotificationResult:
        if not self._cfg.enabled or not self._cfg.topic:
            return {"status": "error", "error_message": "ntfy disabled or topic missing"}
        url = f"{self._cfg.server.rstrip('/')}/{self._cfg.topic}"
        body = alert.message
        headers = {
            "Title": _encode_title(f"{alert.kind} - {book.title}"),
            "Priority": self._cfg.priority,
            "Tags": ",".join(self._cfg.tags),
        }
        async with self._client() as client:
            try:
                resp = await client.post(
                    url, content=body.encode("utf-8"), headers=headers
                )
                resp.raise_for_status()
            except httpx.HTTPError as e:
                return {"status": "error", "error_message": str(e)}
        return {"status": "sent"}
