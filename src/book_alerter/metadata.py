"""Book metadata lookup. Races OpenLibrary and Google Books in parallel
and returns the first response with at least title + author. The other
in-flight request is cancelled. If both fail or return invalid data,
optionally falls back to an Amazon UK dp-page scrape, then raises
`LookupError` if nothing landed.

The race is the resilience strategy — no retries; a short network timeout
(~5s) is enough since the loser usually fills the gap. The Amazon fallback
is *sequential* (not in the race) because launching Playwright is heavy and
we don't want to pay for it on every lookup.
"""
from __future__ import annotations

import asyncio
import re
from contextlib import asynccontextmanager
from typing import Any

import httpx
from pydantic import BaseModel

from book_alerter.logging_setup import get_logger
from book_alerter.sources.amazon import BOT_MARKERS
from book_alerter.sources.normalizers import amazon_uk_dp_url, to_isbn13

log = get_logger(__name__)

_OPENLIBRARY_URL = "https://openlibrary.org/api/books"
_GOOGLEBOOKS_URL = "https://www.googleapis.com/books/v1/volumes"
_TIMEOUT = httpx.Timeout(5.0)


@asynccontextmanager
async def _maybe_client(http: httpx.AsyncClient | None):
    """Yield `http` when provided (lifespan-scoped); else open a fresh
    short-lived client. Either way the caller can `async with` the result.
    The shared client is not closed on exit."""
    if http is not None:
        yield http
    else:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            yield client


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
    isbn13: str,
    client: httpx.AsyncClient,
    *,
    api_key: str = "",
) -> BookMetadata | None:
    """Query the Google Books `volumes` endpoint. Returns `None` when
    `totalItems == 0` or required fields are missing. Passing a non-empty
    `api_key` lifts the anonymous-IP daily quota (free tier is 1000/day per
    key)."""
    params: dict[str, str] = {"q": f"isbn:{isbn13}"}
    if api_key:
        params["key"] = api_key
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


async def lookup_isbn(
    isbn13: str,
    *,
    google_api_key: str = "",
    allow_amazon_fallback: bool = False,
    http: httpx.AsyncClient | None = None,
) -> BookMetadata:
    """Race OpenLibrary and Google Books in parallel and return the first
    valid `BookMetadata`. Cancels the losing task. Falls back to a
    Playwright Amazon UK scrape iff `allow_amazon_fallback=True` and both
    providers return no usable data. Raises `LookupError` if every path
    fails.

    Caller is responsible for passing a canonical ISBN-13. Pre-normalize
    raw user input via `book_alerter.sources.normalizers.to_isbn13`.

    `http` is the lifespan-scoped shared client; when None we open a
    fresh client (back-compat for tests and CLI use)."""
    async with _maybe_client(http) as client:
        tasks: set[asyncio.Task[BookMetadata | None]] = {
            asyncio.create_task(_fetch_openlibrary(isbn13, client), name="ol"),
            asyncio.create_task(
                _fetch_googlebooks(isbn13, client, api_key=google_api_key),
                name="gb",
            ),
        }
        pending = tasks
        while pending:
            done, pending = await asyncio.wait(
                pending, return_when=asyncio.FIRST_COMPLETED
            )
            for t in done:
                try:
                    result = t.result()
                except Exception as exc:
                    log.info("metadata.provider.failed", provider=t.get_name(), error=str(exc))
                    continue  # this provider failed; let the other one finish
                if result is not None:
                    for p in pending:
                        p.cancel()
                    await asyncio.gather(*pending, return_exceptions=True)
                    # OpenLibrary often returns title+author but no cover
                    # (Gemini/Mercury, Sparta, et al.). When the race winner
                    # is missing a cover, enrich with Amazon so the UI has
                    # something to show.
                    if result.cover_url is None and allow_amazon_fallback:
                        log.info("metadata.amazon_fallback.enrich", isbn13=isbn13)
                        amazon = await _fetch_amazon_uk_metadata(isbn13)
                        if amazon is not None and amazon.cover_url:
                            return BookMetadata(
                                title=result.title,
                                author=result.author,
                                cover_url=amazon.cover_url,
                            )
                    return result

    if allow_amazon_fallback:
        log.info("metadata.amazon_fallback.start", isbn13=isbn13)
        amazon = await _fetch_amazon_uk_metadata(isbn13)
        if amazon is not None:
            return amazon

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
    query: str,
    limit: int = 10,
    *,
    google_api_key: str = "",
    http: httpx.AsyncClient | None = None,
) -> list[BookMetadataWithIsbn]:
    """Free-text title/author search against Google Books `volumes`.

    Returns `BookMetadataWithIsbn` rows so the add-book UI can present
    "click to add" candidates with the ISBN already resolved. Items
    missing title, author, or any ISBN are dropped — without an ISBN we
    can't add the book to the watchlist, so the row is useless.

    Returns `[]` on any upstream failure (5xx, 429 quota exhaustion,
    network error). The FE handles an empty list as "no results"; surfacing
    the underlying HTTP error as a 500 would be misleading.

    `limit` is forwarded as `maxResults` (Google Books caps at 40).
    Network: single AsyncClient, 5s timeout, no retries."""
    params: dict[str, str] = {"q": query, "maxResults": str(limit)}
    if google_api_key:
        params["key"] = google_api_key
    try:
        async with _maybe_client(http) as client:
            resp = await client.get(_GOOGLEBOOKS_URL, params=params)
            resp.raise_for_status()
            payload: dict[str, Any] = resp.json()
    except httpx.HTTPStatusError as exc:
        log.warning(
            "metadata.search.http_error",
            status=exc.response.status_code,
            query=query,
        )
        return []
    except httpx.HTTPError as exc:
        log.warning("metadata.search.transport_error", error=str(exc), query=query)
        return []
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


# --- Amazon UK fallback -----------------------------------------------------


# Amazon UK product-title element. The dp page renders #productTitle in the
# static HTML for non-bot-flagged sessions; selectolax can read it without
# waiting for hydration.
_AMAZON_TITLE_SELECTORS = ("#productTitle",)
# Author byline lives in `.author .a-link-normal` on most book dp pages;
# `#bylineInfo .author a` covers the older layout we still see on some titles.
_AMAZON_AUTHOR_SELECTORS = (
    ".author .a-link-normal",
    "#bylineInfo .author a",
    "#bylineInfo a.a-link-normal",
)
_AMAZON_COVER_SELECTORS = ("#landingImage", "#imgBlkFront", "#ebooksImgBlkFront")


def _parse_amazon_dp_metadata(html: str) -> BookMetadata | None:
    """Extract title/author/cover from an Amazon UK dp page HTML blob.

    Returns None if the page didn't render the static product fields (bot
    challenge persisted, dp redirected, etc.)."""
    from selectolax.parser import HTMLParser  # local import — heavy

    tree = HTMLParser(html)
    title: str | None = None
    for sel in _AMAZON_TITLE_SELECTORS:
        node = tree.css_first(sel)
        if node is not None:
            t = node.text(strip=True)
            if t:
                title = t
                break
    author: str | None = None
    for sel in _AMAZON_AUTHOR_SELECTORS:
        node = tree.css_first(sel)
        if node is not None:
            a = node.text(strip=True)
            # Skip the "(Author)" suffix Amazon appends, and prune any
            # bylineInfo separator dots.
            a = re.sub(r"\s*\(Author\)\s*$", "", a).strip()
            if a:
                author = a
                break
    if not title or not author:
        return None
    cover_url: str | None = None
    for sel in _AMAZON_COVER_SELECTORS:
        node = tree.css_first(sel)
        if node is not None:
            src = node.attributes.get("src") or node.attributes.get("data-old-hires")
            if src:
                cover_url = src
                break
    return BookMetadata(title=title, author=author, cover_url=cover_url)


async def _fetch_amazon_uk_metadata(isbn13: str) -> BookMetadata | None:
    """Playwright-rendered Amazon UK dp-page scrape for title/author.

    Used as a last-resort fallback when OpenLibrary and Google Books both
    miss. Cost is ~10-20s per call (browser launch + nav), so callers gate
    this behind a config flag and call it sequentially, not in the race.
    Returns None on any failure (bot challenge, navigation timeout, missing
    static fields) — caller treats that as "no metadata available"."""
    from playwright.async_api import (
        TimeoutError as PlaywrightTimeoutError,
        async_playwright,
    )

    url = amazon_uk_dp_url(isbn13)
    try:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled"],
            )
            try:
                context = await browser.new_context(
                    viewport={"width": 1366, "height": 768},
                    locale="en-GB",
                )
                page = await context.new_page()
                try:
                    await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
                except PlaywrightTimeoutError:
                    log.warning("metadata.amazon_fallback.nav_timeout", url=url)
                    return None
                try:
                    await page.wait_for_selector(
                        "#productTitle", timeout=8_000, state="attached"
                    )
                except PlaywrightTimeoutError:
                    log.info("metadata.amazon_fallback.no_title_selector", url=url)
                html = await page.content()
            finally:
                await browser.close()
    except Exception as exc:
        log.warning("metadata.amazon_fallback.error", url=url, error=str(exc))
        return None

    if any(m in html for m in BOT_MARKERS):
        log.info("metadata.amazon_fallback.bot_blocked", url=url)
        return None
    result = _parse_amazon_dp_metadata(html)
    if result is None:
        log.info("metadata.amazon_fallback.no_fields", url=url)
    else:
        log.info("metadata.amazon_fallback.ok", url=url, title=result.title)
    return result
