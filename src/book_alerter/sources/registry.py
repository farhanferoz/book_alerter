from __future__ import annotations

import httpx

from book_alerter.config import Config
from book_alerter.sources.amazon import (
    AmazonUKInlineSource,
    AmazonUKProductInlineSource,
)
from book_alerter.sources.base import Source
from book_alerter.sources.bookfinder import BookfinderInlineSource
from book_alerter.sources.wob import WobInlineSource

_REGISTRY: dict[str, type[Source]] = {
    "wob": WobInlineSource,
    "bookfinder": BookfinderInlineSource,
    "amazon": AmazonUKInlineSource,
    "amazon_uk_product": AmazonUKProductInlineSource,
}


def build_sources(
    cfg: Config,
    *,
    http: httpx.AsyncClient | None = None,
) -> dict[str, Source]:
    """`http` is forwarded to httpx-based sources (currently only WOB) so
    the scheduler reuses the lifespan-scoped connection pool. Playwright-
    based sources (Amazon book + product, BookFinder) manage their own
    browser sessions and ignore the kwarg."""
    out: dict[str, Source] = {}
    for name, sc in cfg.sources.items():
        if not sc.enabled:
            continue
        cls = _REGISTRY.get(name)
        if cls is None:
            raise ValueError(f"no implementation for source '{name}'")
        kwargs: dict = {"name": name, "region": sc.region}
        if cls is WobInlineSource:
            kwargs["http"] = http
        out[name] = cls(**kwargs)
    return out
