"""Keepa price-history chart integration (free, no-API-key path).

Keepa exposes a public, no-auth PNG endpoint at
`https://graph.keepa.com/pricehistory.png` that returns a rendered chart for
any ASIN + domain. This module:

  - Builds the right URL for an ISBN (ISBN-13 → ISBN-10 conversion).
  - Fetches the PNG from Keepa with a polite User-Agent + 10s timeout.
  - Caches the bytes on disk (`data/keepa-cache/<asin>-<range>.png`) with a
    24-hour TTL so we don't hammer Keepa when the FE asks for the chart
    multiple times.

It does NOT extract numeric data from the PNG — that lives in
`keepa_chart.py`. This module is for the visual-embed flow only.
"""
from __future__ import annotations

import time
from pathlib import Path

import httpx

from book_alerter.logging_setup import get_logger
from book_alerter.sources.normalizers import asin_for_amazon_uk

log = get_logger(__name__)

# Keepa graph endpoint. Verified live May 2026. No auth required;
# CORS-allowing; Keepa edge-caches the PNG 90 min per requestor.
# `domain=2` is amazon.co.uk; other regions documented in Keepa's API docs.
_KEEPA_GRAPH_URL = "https://graph.keepa.com/pricehistory.png"
_KEEPA_DOMAIN_UK = 2

# Default chart shape. Width 1200 gives ~3 px/day on a 365-day chart, which
# is enough resolution for the numeric extractor to read step changes.
_DEFAULT_WIDTH = 1200
_DEFAULT_HEIGHT = 400
_DEFAULT_RANGE_DAYS = 365

# How long a cached PNG stays fresh. Keepa updates the underlying chart
# roughly every few hours; 24h is a sensible balance between freshness and
# load on their edge. Caller can short-circuit by deleting the file.
_CACHE_TTL_SECONDS = 24 * 3600

# Polite UA — declares we're a hobby tracker, not anonymous traffic.
_USER_AGENT = "book_alerter/0.0 (+https://github.com/local/book_alerter)"


def cache_path(cache_dir: Path, isbn13: str, range_days: int) -> Path:
    """Return the on-disk cache path for an ISBN-range combo."""
    asin = asin_for_amazon_uk(isbn13)
    return cache_dir / f"{asin}-r{range_days}.png"


def is_fresh(path: Path, ttl_seconds: int = _CACHE_TTL_SECONDS) -> bool:
    """True if `path` exists and was modified within the TTL window."""
    if not path.exists():
        return False
    age = time.time() - path.stat().st_mtime
    return age < ttl_seconds


async def fetch_chart_png(
    isbn13: str,
    cache_dir: Path,
    *,
    range_days: int = _DEFAULT_RANGE_DAYS,
    width: int = _DEFAULT_WIDTH,
    height: int = _DEFAULT_HEIGHT,
    ttl_seconds: int = _CACHE_TTL_SECONDS,
) -> bytes | None:
    """Return the PNG bytes for `isbn13`'s Amazon UK chart, fetching if needed.

    Cache layout: `<cache_dir>/<asin>-r<range>.png`. Stale cache (older
    than `ttl_seconds`) is replaced on next call. Returns None if Keepa
    has no chart for this ASIN (404 / non-image response).

    Note: Keepa returns 200 with a "no data" PNG for ASINs it doesn't track
    rather than 404. We treat any sub-1KB response as "no data" — real
    charts are ~20KB+.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_path(cache_dir, isbn13, range_days)
    if is_fresh(path, ttl_seconds):
        return path.read_bytes()

    asin = asin_for_amazon_uk(isbn13)
    params = {
        "asin": asin,
        "domain": _KEEPA_DOMAIN_UK,
        "new": 1,
        "used": 1,
        "amazon": 1,
        "range": range_days,
        "width": width,
        "height": height,
    }
    log.info("keepa.fetch.start", asin=asin, range_days=range_days)
    try:
        async with httpx.AsyncClient(
            timeout=10.0, headers={"User-Agent": _USER_AGENT}
        ) as client:
            resp = await client.get(_KEEPA_GRAPH_URL, params=params)
    except httpx.HTTPError as exc:
        log.warning("keepa.fetch.error", asin=asin, error=str(exc))
        return None

    if resp.status_code != 200:
        log.warning("keepa.fetch.non200", asin=asin, status=resp.status_code)
        return None
    if not resp.headers.get("content-type", "").startswith("image/"):
        log.warning("keepa.fetch.notimg", asin=asin, ct=resp.headers.get("content-type"))
        return None
    if len(resp.content) < 1024:
        # Keepa's "no data" placeholder PNG is tiny.
        log.info("keepa.fetch.nodata", asin=asin, size=len(resp.content))
        return None

    path.write_bytes(resp.content)
    log.info("keepa.fetch.ok", asin=asin, size=len(resp.content), cached=str(path))
    return resp.content
