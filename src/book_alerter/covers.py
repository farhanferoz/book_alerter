"""Cover-image disk cache.

External cover hosts (Amazon CDN, OpenLibrary) are commonly blocked by
browser shields when used as third-party image sources. The fix is to
serve covers from the same origin as the SPA: this module fetches each
book's cover once on first request and writes the bytes under
`data/covers/<isbn13>`. The `/api/covers/{isbn13}` endpoint then streams
the cached file.

Cache is keyed on ISBN13 (unique per `Book` row) and validated by magic
bytes — we refuse to write non-image responses (a 200 with an HTML error
body, a tracker pixel, etc.) so a transient upstream regression doesn't
poison the cache forever. Failed/non-image fetches return `None` and the
route 404s; subsequent requests retry.

Concurrent first-fetches for the same ISBN are serialized with an
asyncio.Lock keyed by isbn13 — without it, N simultaneous misses would
each call upstream and write the file in parallel, wasting bandwidth and
risking torn writes.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import httpx

COVER_DIR = Path(os.environ.get("BOOK_ALERTER_COVER_DIR", "data/covers"))

_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"GIF87a", "image/gif"),
    (b"GIF89a", "image/gif"),
)

_locks: dict[str, asyncio.Lock] = {}


def sniff_mime(data: bytes) -> str:
    """Detect content-type from magic bytes. Returns
    `application/octet-stream` for anything we don't recognize as an
    image — callers should treat that as "not an image."
    """
    for sig, mime in _MAGIC:
        if data.startswith(sig):
            return mime
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def cover_path(isbn13: str) -> Path:
    return COVER_DIR / isbn13


async def fetch_and_cache(
    isbn13: str,
    url: str,
    *,
    http: httpx.AsyncClient | None = None,
) -> Path | None:
    """Fetch `url` and write the response body to the on-disk cache.

    Returns the path on success, `None` on any error (network failure,
    non-200, empty body, or non-image bytes).

    Concurrent calls for the same `isbn13` are serialized so we only
    fetch upstream once even if many requests miss the cache at the
    same instant. After acquiring the lock we re-check `path.exists()`
    — a previous waiter may have already populated the cache.

    `http` is the lifespan-scoped shared client; when None we build a
    fresh client per call (back-compat for tests and CLI use).
    """
    lock = _locks.setdefault(isbn13, asyncio.Lock())
    async with lock:
        path = cover_path(isbn13)
        if path.exists():
            return path
        try:
            if http is not None:
                r = await http.get(url, timeout=15)
            else:
                async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
                    r = await client.get(url)
        except httpx.HTTPError:
            return None
        if r.status_code != 200 or not r.content:
            return None
        # Refuse to cache anything that isn't a recognized image — an HTML
        # error page or a 1×1 tracker pixel returns 200 too, and would
        # otherwise stick around forever causing broken-image renders.
        if sniff_mime(r.content) == "application/octet-stream":
            return None
        COVER_DIR.mkdir(parents=True, exist_ok=True)
        # Write to a temp file in the same directory and rename — atomic on
        # POSIX so two racing writers can't tear each other's content.
        tmp = path.with_suffix(".tmp")
        tmp.write_bytes(r.content)
        tmp.replace(path)
        return path
