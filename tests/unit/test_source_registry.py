from __future__ import annotations

import pytest

from book_alerter.config import Config, SourceConfig
from book_alerter.sources.registry import build_sources
from book_alerter.sources.subprocess_source import SubprocessSource
from book_alerter.sources.wob import WobInlineSource


def test_inline_wob_source_built() -> None:
    cfg = Config(sources={"wob": SourceConfig(type="inline", region="UK")})
    sources = build_sources(cfg)
    assert "wob" in sources
    src = sources["wob"]
    assert isinstance(src, WobInlineSource)
    assert src.name == "wob"
    assert src.region == "UK"


def test_subprocess_source_built_with_binary() -> None:
    cfg = Config(
        sources={
            "bookfinder": SourceConfig(
                type="subprocess",
                binary="/usr/local/bin/bookfinder-pp-cli",
                timeout_seconds=45,
            )
        }
    )
    sources = build_sources(cfg)
    assert "bookfinder" in sources
    src = sources["bookfinder"]
    assert isinstance(src, SubprocessSource)
    assert src.binary == "/usr/local/bin/bookfinder-pp-cli"
    assert src.timeout_s == 45


def test_disabled_source_skipped() -> None:
    cfg = Config(sources={"wob": SourceConfig(type="inline", enabled=False)})
    assert build_sources(cfg) == {}


def test_unknown_inline_source_raises() -> None:
    cfg = Config(sources={"fictional": SourceConfig(type="inline")})
    with pytest.raises(ValueError, match="no inline implementation"):
        build_sources(cfg)


def test_subprocess_source_without_binary_raises() -> None:
    cfg = Config(sources={"bookfinder": SourceConfig(type="subprocess", binary=None)})
    with pytest.raises(ValueError, match="binary"):
        build_sources(cfg)
