"""Fetch + on-disk cache for the public Keepa price-history PNG endpoint.

Numeric extraction from the PNG lives in `keepa_chart.py`; this module is
purely the network + cache layer.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import httpx

from book_alerter.logging_setup import get_logger
from book_alerter.sources.normalizers import asin_for_amazon_uk

log = get_logger(__name__)

_KEEPA_GRAPH_URL = "https://graph.keepa.com/pricehistory.png"
_KEEPA_DOMAIN_UK = 2  # amazon.co.uk

# Width 1200 gives ~3 px/day on a 365-day chart — enough resolution for the
# extractor to read step changes.
_DEFAULT_WIDTH = 1200
_DEFAULT_HEIGHT = 400
_DEFAULT_RANGE_DAYS = 365

_CACHE_TTL_SECONDS = 24 * 3600
_USER_AGENT = "book_alerter/0.0 (+https://github.com/local/book_alerter)"

DEFAULT_CACHE_DIR = Path("data/keepa-cache")


def cache_path(cache_dir: Path, isbn13: str, range_days: int) -> Path:
    asin = asin_for_amazon_uk(isbn13)
    return cache_dir / f"{asin}-r{range_days}.png"


def is_fresh(path: Path, ttl_seconds: int = _CACHE_TTL_SECONDS) -> bool:
    if not path.exists():
        return False
    age = time.time() - path.stat().st_mtime
    return age < ttl_seconds


def fetch_chart_png(
    isbn13: str,
    cache_dir: Path = DEFAULT_CACHE_DIR,
    *,
    range_days: int = _DEFAULT_RANGE_DAYS,
    width: int = _DEFAULT_WIDTH,
    height: int = _DEFAULT_HEIGHT,
    ttl_seconds: int = _CACHE_TTL_SECONDS,
) -> bytes | None:
    """Return the PNG bytes for `isbn13`'s Amazon UK chart, fetching if needed.

    Returns None for ASINs Keepa doesn't track — Keepa serves a sub-1KB
    placeholder for those rather than 404, so we treat any sub-1KB image as
    "no data". Cache writes are atomic (tmp + os.replace) so concurrent
    callers can race without corrupting the file.
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
        with httpx.Client(
            timeout=10.0, headers={"User-Agent": _USER_AGENT}
        ) as client:
            resp = client.get(_KEEPA_GRAPH_URL, params=params)
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
        log.info("keepa.fetch.nodata", asin=asin, size=len(resp.content))
        return None

    tmp = path.with_suffix(f"{path.suffix}.{os.getpid()}.tmp")
    tmp.write_bytes(resp.content)
    os.replace(tmp, path)
    log.info("keepa.fetch.ok", asin=asin, size=len(resp.content), cached=str(path))
    return resp.content
