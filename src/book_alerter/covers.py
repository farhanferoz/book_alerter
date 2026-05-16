"""Cover-image disk cache.

External cover hosts (Amazon CDN, OpenLibrary) are commonly blocked by
browser shields (Brave Shields, uBlock-with-strict-list) when used as
third-party image sources. The fix is to serve covers from the same origin
as the SPA: this module fetches each book's cover once on first request and
writes the bytes under `data/covers/<isbn13>`. The `/api/covers/{isbn13}`
endpoint then streams the cached file, so the browser only ever sees
same-origin image requests.

Cache is keyed on ISBN13 (unique per `Book` row). No TTL — covers are
effectively immutable per edition; if a book's `cover_url` is updated, the
admin can delete the cached file to force a refetch.
"""
from __future__ import annotations

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


def sniff_mime(data: bytes) -> str:
    """Detect content-type from magic bytes. Browsers sniff anyway, but we
    set the right Content-Type so caching and downloads behave."""
    for sig, mime in _MAGIC:
        if data.startswith(sig):
            return mime
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "application/octet-stream"


def cover_path(isbn13: str) -> Path:
    return COVER_DIR / isbn13


async def fetch_and_cache(isbn13: str, url: str) -> Path | None:
    """Fetch `url` and write the response body to the on-disk cache.

    Returns the path on success, `None` on any error (network failure,
    non-200, empty body). Caller is expected to surface a 404 / fallback
    in the latter case.
    """
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(url)
    except httpx.HTTPError:
        return None
    if r.status_code != 200 or not r.content:
        return None
    COVER_DIR.mkdir(parents=True, exist_ok=True)
    path = cover_path(isbn13)
    path.write_bytes(r.content)
    return path
