"""ntfy.sh push notifier. POSTs alert message + priority + tags to
`<server>/<topic>`. HTTP errors are surfaced as
`{"status": "error", "error_message": str}` so the dispatcher records a failed
delivery rather than treating it as an unexpected exception."""
from __future__ import annotations

import base64
from collections.abc import Callable

import httpx

from book_alerter.config import NtfyChannelConfig
from book_alerter.db.models import Alert, Book
from book_alerter.notifications.base import Notifier


def _encode_title(s: str) -> str:
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
    ) -> None:
        self._cfg = cfg
        self._client_factory = client_factory or (lambda: httpx.AsyncClient(timeout=10))

    async def send(self, alert: Alert, book: Book) -> dict:
        if not self._cfg.enabled or not self._cfg.topic:
            return {"status": "error", "error_message": "ntfy disabled or topic missing"}
        url = f"{self._cfg.server.rstrip('/')}/{self._cfg.topic}"
        body = alert.message
        # httpx enforces ASCII header values; ntfy decodes RFC 2047 encoded-word
        # titles (`=?utf-8?b?...?=`), so non-ASCII book titles round-trip cleanly.
        headers = {
            "Title": _encode_title(f"{alert.kind} - {book.title}"),
            "Priority": self._cfg.priority,
            "Tags": ",".join(self._cfg.tags or []),
        }
        async with self._client_factory() as client:
            try:
                resp = await client.post(
                    url, content=body.encode("utf-8"), headers=headers
                )
                resp.raise_for_status()
            except httpx.HTTPError as e:
                return {"status": "error", "error_message": str(e)}
        return {"status": "sent"}
