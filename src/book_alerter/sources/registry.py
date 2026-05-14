from __future__ import annotations

from book_alerter.config import Config
from book_alerter.sources.base import Source
from book_alerter.sources.bookfinder import BookfinderInlineSource
from book_alerter.sources.wob import WobInlineSource

_REGISTRY: dict[str, type[Source]] = {
    "wob": WobInlineSource,
    "bookfinder": BookfinderInlineSource,
}


def build_sources(cfg: Config) -> dict[str, Source]:
    out: dict[str, Source] = {}
    for name, sc in cfg.sources.items():
        if not sc.enabled:
            continue
        cls = _REGISTRY.get(name)
        if cls is None:
            raise ValueError(f"no implementation for source '{name}'")
        out[name] = cls(name=name, region=sc.region)
    return out
