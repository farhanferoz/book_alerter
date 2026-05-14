"""Book metadata lookup. Races OpenLibrary and Google Books in parallel
and returns the first response with at least title + author. The other
in-flight request is cancelled. If both fail or return invalid data,
raises `LookupError`.

The race itself is the resilience strategy — no retries; a short network
timeout (~5s) is enough since the loser usually fills the gap.
"""
from __future__ import annotations

import asyncio
from typing import Any

import httpx
from pydantic import BaseModel

_OPENLIBRARY_URL = "https://openlibrary.org/api/books"
_GOOGLEBOOKS_URL = "https://www.googleapis.com/books/v1/volumes"
_TIMEOUT = httpx.Timeout(5.0)


class BookMetadata(BaseModel):
    title: str
    author: str
    cover_url: str | None = None


async def _fetch_openlibrary(
    isbn13: str, client: httpx.AsyncClient
) -> BookMetadata | None:
    """Query the OpenLibrary `bibkeys` endpoint. Returns `None` for the
    "found but missing required fields" / empty-response case so the race
    waits for the other provider; raises on HTTP errors."""
    params = {"bibkeys": f"ISBN:{isbn13}", "format": "json", "jscmd": "data"}
    resp = await client.get(_OPENLIBRARY_URL, params=params)
    resp.raise_for_status()
    payload: dict[str, Any] = resp.json()
    entry = payload.get(f"ISBN:{isbn13}")
    if not entry:
        return None
    title = entry.get("title")
    authors = entry.get("authors") or []
    author = authors[0].get("name") if authors else None
    if not title or not author:
        return None
    cover = entry.get("cover") or {}
    cover_url = cover.get("medium") or cover.get("large") or cover.get("small")
    return BookMetadata(title=title, author=author, cover_url=cover_url)


async def _fetch_googlebooks(
    isbn13: str, client: httpx.AsyncClient
) -> BookMetadata | None:
    """Query the Google Books `volumes` endpoint. Returns `None` when
    `totalItems == 0` or required fields are missing."""
    params = {"q": f"isbn:{isbn13}"}
    resp = await client.get(_GOOGLEBOOKS_URL, params=params)
    resp.raise_for_status()
    payload: dict[str, Any] = resp.json()
    if not payload.get("totalItems"):
        return None
    items = payload.get("items") or []
    if not items:
        return None
    info = items[0].get("volumeInfo") or {}
    title = info.get("title")
    authors = info.get("authors") or []
    author = authors[0] if authors else None
    if not title or not author:
        return None
    image_links = info.get("imageLinks") or {}
    cover_url = image_links.get("thumbnail") or image_links.get("smallThumbnail")
    return BookMetadata(title=title, author=author, cover_url=cover_url)


async def lookup_isbn(isbn13: str) -> BookMetadata:
    """Race OpenLibrary and Google Books in parallel and return the first
    valid `BookMetadata`. Cancels the losing task and awaits its
    cancellation so no warnings leak. Raises `LookupError` if neither
    provider returns usable data."""
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        tasks: set[asyncio.Task[BookMetadata | None]] = {
            asyncio.create_task(_fetch_openlibrary(isbn13, client), name="ol"),
            asyncio.create_task(_fetch_googlebooks(isbn13, client), name="gb"),
        }
        pending = tasks
        while pending:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED
            )
            for t in done:
                try:
                    result = t.result()
                except Exception:
                    continue  # this provider failed; let the other one finish
                if result is not None:
                    for p in pending:
                        p.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    return result
        raise LookupError(f"no metadata found for ISBN {isbn13!r}")
