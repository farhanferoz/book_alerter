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
from typing import Any

import httpx
from pydantic import BaseModel

from book_alerter.http_client import shared_or_fresh
from book_alerter.logging_setup import get_logger
from book_alerter.sources.amazon import BOT_MARKERS
from book_alerter.sources.normalizers import (
    amazon_uk_dp_url,
    amazon_uk_product_dp_url,
    to_isbn13,
)

log = get_logger(__name__)

_OPENLIBRARY_URL = "https://openlibrary.org/api/books"
_GOOGLEBOOKS_URL = "https://www.googleapis.com/books/v1/volumes"
_TIMEOUT = httpx.Timeout(5.0)

# Both Amazon fallbacks below are on the interactive path (the add-book
# dialog and the ASIN-lookup endpoint block on them), sharing the
# `amazon_uk_product` BrowserSession profile with the scheduled product
# source (T1.1 D24). A scheduled run can legitimately hold that profile for
# minutes; a human should not wait anywhere near that long for a spinner.
# 10s is long enough to absorb the common case (the previous holder is
# already finishing up — a single fetch cycle, not a whole run) while
# staying well inside ordinary UI/HTTP-client patience; past that, telling
# the user "try again shortly" beats leaving them staring at a spinner with
# no explanation for however long a scheduled run has left to run.
_METADATA_BROWSER_ACQUIRE_TIMEOUT_S = 10.0


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


class ProductMetadata(BaseModel):
    """ASIN-keyed product metadata pulled from the Amazon UK dp page.

    The product side of the metadata flow — used by `/api/metadata/asin-lookup`
    so the Add-Product dialog can pre-fill title/image before the user clicks
    save. Mirrors `BookMetadata` in spirit; the fields differ because product
    pages don't carry an author byline and the title is the only required
    field for the FE to render a usable preview.
    """
    asin: str
    title: str
    image_url: str | None = None
    brand: str | None = None


async def _fetch_openlibrary(
    isbn13: str, client: httpx.AsyncClient
) -> BookMetadata | None:
    """Query the OpenLibrary `bibkeys` endpoint. Returns `None` for the
    "found but missing required fields" / empty-response case so the race
    waits for the other provider; raises on HTTP errors."""
    params = {"bibkeys": f"ISBN:{isbn13}", "format": "json", "jscmd": "data"}
    # Per-call 5s timeout overrides whatever the (shared) client carries —
    # metadata race relies on a short timeout so the loser doesn't dominate
    # the wait when one provider is slow.
    resp = await client.get(_OPENLIBRARY_URL, params=params, timeout=_TIMEOUT)
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
    resp = await client.get(_GOOGLEBOOKS_URL, params=params, timeout=_TIMEOUT)
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
    raw user input via `book_alerter.sources.normalizers.to_isbn13`."""
    async with shared_or_fresh(http) as client:
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
        async with shared_or_fresh(http) as client:
            resp = await client.get(_GOOGLEBOOKS_URL, params=params, timeout=_TIMEOUT)
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
    static fields) — caller treats that as "no metadata available" —
    EXCEPT `BrowserSessionBusy`, which propagates: the profile is shared
    with the scheduled `amazon_uk_product` source, which can hold it for
    minutes, so `_METADATA_BROWSER_ACQUIRE_TIMEOUT_S` bounds this
    interactive path's wait rather than blocking indefinitely.

    Uses the `amazon_uk_product` `BrowserSession` profile (not a book-only
    one) so this fallback benefits from the same returning-visitor cookie
    jar as `AmazonUKProductInlineSource` — see `BrowserSession`'s docstring
    for why a persistent profile matters beyond just bot-evasion.
    """
    from playwright.async_api import (
        TimeoutError as PlaywrightTimeoutError,
    )

    from book_alerter.enums import BrowserProfile
    from book_alerter.sources.browser import (  # local import — heavy
        BrowserSession,
        BrowserSessionBusy,
    )

    url = amazon_uk_dp_url(isbn13)
    try:
        async with BrowserSession(
            BrowserProfile.AMAZON_UK_PRODUCT,
            acquire_timeout=_METADATA_BROWSER_ACQUIRE_TIMEOUT_S,
        ) as context:
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
    except BrowserSessionBusy:
        # Distinct from every other failure below: this isn't "no metadata
        # available", it's "the browser profile is busy right now" — let it
        # propagate so an interactive caller can answer with that specific
        # reason (e.g. a 503) instead of a silent None.
        raise
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


# --- Amazon UK product metadata --------------------------------------------


# Product-side brand selectors. `#bylineInfo` carries "Visit the X Store" or
# "Brand: X" on most non-book product pages.
_AMAZON_PRODUCT_BRAND_SELECTORS = (
    "#bylineInfo",
    "#brand",
    ".po-brand .po-break-word",
)


def _parse_amazon_product_metadata(html: str, *, asin: str) -> ProductMetadata | None:
    """Extract title/image/brand from an Amazon UK product dp page.

    Title uses the same `#productTitle` selector as the book path. Image
    falls back across the same cover selector cascade. Brand is product-
    specific — bylineInfo or the side-panel brand row.

    Returns None when the page didn't render a usable title (bot
    interstitial, dp redirect, or unfamiliar template). Brand and image
    are best-effort.
    """
    from selectolax.parser import HTMLParser

    tree = HTMLParser(html)
    title: str | None = None
    for sel in _AMAZON_TITLE_SELECTORS:
        node = tree.css_first(sel)
        if node is not None:
            t = node.text(strip=True)
            if t:
                title = t
                break
    if not title:
        return None

    image_url: str | None = None
    for sel in _AMAZON_COVER_SELECTORS:
        node = tree.css_first(sel)
        if node is not None:
            src = node.attributes.get("src") or node.attributes.get("data-old-hires")
            if src:
                image_url = src
                break

    brand: str | None = None
    for sel in _AMAZON_PRODUCT_BRAND_SELECTORS:
        node = tree.css_first(sel)
        if node is not None:
            b = node.text(strip=True)
            # Strip leading template prefixes Amazon uses for the byline.
            b = re.sub(r"^(Visit the\s+|Brand:\s*)", "", b)
            # Strip trailing "Store" Amazon appends to the brand link.
            b = re.sub(r"\s+Store$", "", b).strip()
            if b and b.lower() != "amazon":
                brand = b
                break

    return ProductMetadata(asin=asin, title=title, image_url=image_url, brand=brand)


async def fetch_amazon_uk_product_metadata(asin: str) -> ProductMetadata | None:
    """Playwright-rendered Amazon UK product dp scrape. Returns None on any
    failure (bot challenge, navigation timeout, no title selector) EXCEPT
    `BrowserSessionBusy`, which propagates — see `_fetch_amazon_uk_metadata`
    for why. Cost is ~10-20s per call — caller should not invoke this in
    tight loops.

    Uses the `amazon_uk_product` `BrowserSession` profile — the same one
    `AmazonUKProductInlineSource` scrapes with — so a returning visitor
    profile benefits this one-shot lookup too.
    """
    from playwright.async_api import (
        TimeoutError as PlaywrightTimeoutError,
    )

    from book_alerter.enums import BrowserProfile
    from book_alerter.sources.browser import (  # local import — heavy
        BrowserSession,
        BrowserSessionBusy,
    )

    url = amazon_uk_product_dp_url(asin)
    try:
        async with BrowserSession(
            BrowserProfile.AMAZON_UK_PRODUCT,
            acquire_timeout=_METADATA_BROWSER_ACQUIRE_TIMEOUT_S,
        ) as context:
            page = await context.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=20_000)
            except PlaywrightTimeoutError:
                log.warning("metadata.asin_lookup.nav_timeout", url=url)
                return None
            try:
                await page.wait_for_selector(
                    "#productTitle", timeout=8_000, state="attached"
                )
            except PlaywrightTimeoutError:
                # T4.1: this function is now also called in a loop
                # (metadata_refresh, up to 6 attempts x every PENDING
                # product) rather than only from the one-shot interactive
                # asin-lookup endpoint. A missing selector after 8s is
                # usually just a slow render, not a terminal failure (the
                # code still falls through to try parsing the page below)
                # -- plan §8 rules out per-item progress logging at INFO,
                # so this one drops to DEBUG. nav_timeout/error (genuine
                # failures) and bot_blocked (a real outcome worth keeping
                # visible even under retry) are unchanged.
                log.debug("metadata.asin_lookup.no_title_selector", url=url)
            html = await page.content()
    except BrowserSessionBusy:
        # Distinct from every other failure below — see
        # _fetch_amazon_uk_metadata's identical handling for why.
        raise
    except Exception as exc:
        log.warning("metadata.asin_lookup.error", url=url, error=str(exc))
        return None

    if any(m in html for m in BOT_MARKERS):
        log.info("metadata.asin_lookup.bot_blocked", url=url)
        return None
    return _parse_amazon_product_metadata(html, asin=asin)
