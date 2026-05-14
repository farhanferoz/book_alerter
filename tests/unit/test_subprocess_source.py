from __future__ import annotations

import asyncio
import sys

import pytest

from book_alerter.db.models import Book
from book_alerter.sources.base import SourceError
from book_alerter.sources.subprocess_source import SubprocessSource


class _PythonSource(SubprocessSource):
    """Drive the asyncio subprocess via `python -c` for hermetic tests."""

    def __init__(self, payload_script: str, **kw) -> None:
        super().__init__(name="pytest", binary=sys.executable, **kw)
        self._payload_script = payload_script

    def build_command(self, book: Book) -> list[str]:
        return [self.binary, "-c", self._payload_script]


def test_subprocess_source_parses_offers(transient_book) -> None:
    doc = {
        "isbn13": "9780000000000",
        "queried_at": "2026-05-14T00:00:00Z",
        "region": "UK",
        "currency": "GBP",
        "offers": [
            {
                "seller": "AAA",
                "condition": "new",
                "price_minor": 1234,
                "shipping_minor": 0,
                "currency": "GBP",
                "url": "https://x",
            }
        ],
    }
    src = _PythonSource(f"import json; print(json.dumps({doc!r}))")
    result = asyncio.run(src.fetch(transient_book()))
    assert len(result) == 1
    assert result[0].seller == "AAA"
    assert result[0].condition == "new"
    assert result[0].price_minor == 1234
    assert result[0].shipping_minor == 0
    assert result[0].currency == "GBP"
    assert result[0].url == "https://x"


def test_subprocess_source_raises_on_non_zero_exit(transient_book) -> None:
    class _BoomSource(SubprocessSource):
        def build_command(self, book: Book) -> list[str]:
            return [
                sys.executable,
                "-c",
                "import sys; sys.stderr.write('boom'); sys.exit(2)",
            ]

    src = _BoomSource(name="boom", binary=sys.executable)
    with pytest.raises(SourceError) as exc_info:
        asyncio.run(src.fetch(transient_book()))
    assert "boom" in str(exc_info.value)


def test_subprocess_source_raises_on_missing_binary(transient_book) -> None:
    src = SubprocessSource(
        name="missing",
        binary="/nonexistent/binary/that/does/not/exist",
    )
    with pytest.raises(SourceError) as exc_info:
        asyncio.run(src.fetch(transient_book()))
    msg = str(exc_info.value)
    assert "binary not found" in msg or "/nonexistent/binary/that/does/not/exist" in msg


def test_subprocess_source_wraps_malformed_json_in_source_error(transient_book) -> None:
    src = _PythonSource('print("this is not json {{{")')
    with pytest.raises(SourceError) as exc_info:
        asyncio.run(src.fetch(transient_book()))
    assert "parse failed" in str(exc_info.value)


def test_subprocess_source_raises_on_timeout(transient_book) -> None:
    class _SleepySource(SubprocessSource):
        def build_command(self, book: Book) -> list[str]:
            return [sys.executable, "-c", "import time; time.sleep(2)"]

    src = _SleepySource(name="sleepy", binary=sys.executable, timeout_s=0)
    with pytest.raises(SourceError) as exc_info:
        asyncio.run(src.fetch(transient_book()))
    assert "timeout" in str(exc_info.value).lower()
