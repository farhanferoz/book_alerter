from __future__ import annotations

import pytest

from book_alerter.config import Config, SourceConfig
from book_alerter.sources.bookfinder import BookfinderInlineSource
from book_alerter.sources.registry import build_sources
from book_alerter.sources.wob import WobInlineSource


def test_wob_source_built() -> None:
    cfg = Config(sources={"wob": SourceConfig(region="UK")})
    sources = build_sources(cfg)
    assert "wob" in sources
    src = sources["wob"]
    assert isinstance(src, WobInlineSource)
    assert src.name == "wob"
    assert src.region == "UK"


def test_bookfinder_source_built() -> None:
    cfg = Config(sources={"bookfinder": SourceConfig(region="UK")})
    sources = build_sources(cfg)
    assert "bookfinder" in sources
    assert isinstance(sources["bookfinder"], BookfinderInlineSource)


def test_disabled_source_skipped() -> None:
    cfg = Config(sources={"wob": SourceConfig(enabled=False)})
    assert build_sources(cfg) == {}


def test_unknown_source_raises() -> None:
    cfg = Config(sources={"fictional": SourceConfig()})
    with pytest.raises(ValueError, match="no implementation"):
        build_sources(cfg)
