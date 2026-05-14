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

from book_alerter.sources.normalizers import to_isbn13

_OPENLIBRARY_URL = "https://openlibrary.org/api/books"
_GOOGLEBOOKS_URL = "https://www.googleapis.com/books/v1/volumes"
_TIMEOUT = httpx.Timeout(5.0)


class BookMetadata(BaseModel):
    title: str
    author: str
    cover_url: str | None = None


class BookMetadataWithIsbn(BaseModel):
    """A search-result row that carries its own ISBN-13 — distinct from
    `BookMetadata` (used by `/api/metadata/lookup` where the caller already
    has the ISBN). The add-book UI uses this shape to render
    "click to add" candidates."""
    isbn13: str
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
    first = authors[0] if authors else None
    author = first.get("name") if isinstance(first, dict) else None
    if not title or not author:
        return None
    cover = entry.get("cover")
    cover_url = None
    if isinstance(cover, dict):
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
    provider returns usable data.

    Caller is responsible for passing a canonical ISBN-13. Pre-normalize
    raw user input via `book_alerter.sources.normalizers.to_isbn13`."""
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


def _extract_isbn13_from_industry_identifiers(
    identifiers: list[Any],
) -> str | None:
    """Pull an ISBN-13 out of a Google Books `industryIdentifiers` array.

    Prefers a native `ISBN_13` entry. Falls back to promoting an `ISBN_10`
    via `to_isbn13`. Returns `None` if neither is present or both are
    malformed. `isinstance(..., dict)` guards are applied because the JSON
    payload is untrusted (an array can contain unexpected scalars)."""
    isbn10: str | None = None
    for ident in identifiers:
        if not isinstance(ident, dict):
            continue
        kind = ident.get("type")
        value = ident.get("identifier")
        if not isinstance(value, str):
            continue
        if kind == "ISBN_13":
            return value
        if kind == "ISBN_10" and isbn10 is None:
            isbn10 = value
    if isbn10 is not None:
        try:
            return to_isbn13(isbn10)
        except ValueError:
            return None
    return None


async def search_books(
    query: str, limit: int = 10
) -> list[BookMetadataWithIsbn]:
    """Free-text title/author search against Google Books `volumes`.

    Returns `BookMetadataWithIsbn` rows so the add-book UI can present
    "click to add" candidates with the ISBN already resolved. Items
    missing title, author, or any ISBN are dropped — without an ISBN we
    can't add the book to the watchlist, so the row is useless.

    `limit` is forwarded as `maxResults` (Google Books caps at 40).
    Network: single AsyncClient, 5s timeout, no retries (matches
    `_fetch_googlebooks`)."""
    params = {"q": query, "maxResults": str(limit)}
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(_GOOGLEBOOKS_URL, params=params)
        resp.raise_for_status()
        payload: dict[str, Any] = resp.json()
    items = payload.get("items") or []
    out: list[BookMetadataWithIsbn] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        info = item.get("volumeInfo")
        if not isinstance(info, dict):
            continue
        title = info.get("title")
        authors = info.get("authors") or []
        author = authors[0] if authors and isinstance(authors, list) else None
        if not isinstance(title, str) or not isinstance(author, str):
            continue
        identifiers = info.get("industryIdentifiers") or []
        if not isinstance(identifiers, list):
            continue
        isbn13 = _extract_isbn13_from_industry_identifiers(identifiers)
        if isbn13 is None:
            continue
        image_links = info.get("imageLinks") or {}
        cover_url: str | None = None
        if isinstance(image_links, dict):
            cover_url = image_links.get("thumbnail") or image_links.get(
                "smallThumbnail"
            )
        out.append(
            BookMetadataWithIsbn(
                isbn13=isbn13,
                title=title,
                author=author,
                cover_url=cover_url,
            )
        )
    return out
