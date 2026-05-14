"""Integration tests for `book_alerter.metadata.lookup_isbn` — the
OpenLibrary + Google Books parallel race.

Cassettes were hand-written under `cassettes/metadata/` because the live
Google Books API daily quota was exhausted at record time (a global
unauthenticated-quota that no key would fix in-session). Each cassette
contains both the OL and GB interactions for one scenario. Replay uses
`record_mode="none"` so missing fixtures fail loud rather than silently
hitting the network.
"""
from __future__ import annotations

import asyncio
import warnings

import pytest

from book_alerter.metadata import BookMetadata, lookup_isbn


def test_ol_wins_when_gb_invalid(metadata_vcr):
    """Both providers respond; GB returns `totalItems: 0` so OL must win.
    Asserts the title/author/cover come from the OL payload."""
    with metadata_vcr("none").use_cassette("case1_ol_wins.yaml"):
        result = asyncio.run(lookup_isbn("9780241479414"))
    assert isinstance(result, BookMetadata)
    assert result.title == "1929"
    assert result.author == "Andrew Ross Sorkin"
    assert result.cover_url == "https://covers.openlibrary.org/b/id/15161975-M.jpg"


def test_gb_wins_when_ol_503(metadata_vcr):
    """OL returns transport-level 503; GB returns a valid record. The
    race must wait past the OL failure and surface GB's data."""
    with metadata_vcr("none").use_cassette("case2_gb_wins_ol_503.yaml"):
        result = asyncio.run(lookup_isbn("9780241479414"))
    assert result.title == "1929 GB"
    assert result.author == "Andrew Ross Sorkin"
    assert result.cover_url == "https://books.google.com/thumb.jpg"


def test_gb_wins_when_ol_empty(metadata_vcr):
    """OL returns `{}` (found-but-empty — distinct from a transport
    failure) so `_fetch_openlibrary` returns None; the race waits for GB."""
    with metadata_vcr("none").use_cassette("case3_gb_wins_ol_empty.yaml"):
        result = asyncio.run(lookup_isbn("9780753560686"))
    assert result.title == "Mystery Volume"
    assert result.author == "Famous Author"
    assert result.cover_url == "https://books.google.com/thumb-mv.jpg"


def test_both_fail_raises_lookup_error(metadata_vcr):
    """OL 503 + GB `totalItems: 0` → no provider has data; expect a
    clear `LookupError` rather than a silent None."""
    with metadata_vcr("none").use_cassette("case4_both_fail.yaml"):
        with pytest.raises(LookupError, match="9780241479414"):
            asyncio.run(lookup_isbn("9780241479414"))


def test_ol_wins_without_cover(metadata_vcr):
    """OL has title + author but no `cover` field; metadata still wins
    and `cover_url` is None. GB returns empty so the race is unambiguous."""
    with metadata_vcr("none").use_cassette("case5_ol_no_cover.yaml"):
        result = asyncio.run(lookup_isbn("9780241638194"))
    assert result.title == "Gemini and Mercury Remastered"
    assert result.author == "Andy Saunders"
    assert result.cover_url is None


def test_cancellation_hygiene_no_warnings(metadata_vcr):
    """When the winner returns, the loser is cancelled and awaited; no
    'coroutine was never awaited' / 'task was destroyed' warnings."""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with metadata_vcr("none").use_cassette("case1_ol_wins.yaml"):
            asyncio.run(lookup_isbn("9780241479414"))
    leaked = [
        w for w in caught
        if "never awaited" in str(w.message) or "was destroyed" in str(w.message)
    ]
    assert not leaked, f"unexpected asyncio warnings: {[str(w.message) for w in leaked]}"
