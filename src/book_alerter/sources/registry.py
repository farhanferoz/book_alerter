from __future__ import annotations

from book_alerter.config import Config
from book_alerter.sources.base import Source
from book_alerter.sources.subprocess_source import SubprocessSource
from book_alerter.sources.wob import WobInlineSource


_INLINE_REGISTRY: dict[str, type[Source]] = {
    "wob": WobInlineSource,
}


def build_sources(cfg: Config) -> dict[str, Source]:
    out: dict[str, Source] = {}
    for name, sc in cfg.sources.items():
        if not sc.enabled:
            continue
        if sc.type == "inline":
            cls = _INLINE_REGISTRY.get(name)
            if cls is None:
                raise ValueError(f"no inline implementation for source '{name}'")
            out[name] = cls(name=name, region=sc.region)
        elif sc.type == "subprocess":
            if not sc.binary:
                raise ValueError(f"source '{name}' is subprocess but has no binary")
            out[name] = SubprocessSource(
                name=name, binary=sc.binary, region=sc.region,
                timeout_s=sc.timeout_seconds,
            )
        else:
            raise ValueError(f"unknown source type: {sc.type}")
    return out
