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

import httpx
import pytest

from book_alerter.metadata import BookMetadata, lookup_isbn, search_books


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


def test_search_books_extracts_isbn13_and_promotes_isbn10(monkeypatch):
    """Three-item Google Books payload exercises every extraction path:

    - item 0 has both ISBN_13 and ISBN_10 → prefer the 13 verbatim.
    - item 1 has only ISBN_10 → promote via `to_isbn13`.
    - item 2 has no industry identifiers → filtered out (an unaddable row
      is useless to the add-book UI).

    Uses `httpx.MockTransport` (same pattern as `test_ntfy_notifier`) by
    monkeypatching the `httpx.AsyncClient` constructor in the metadata
    module namespace — `search_books` builds its own client internally, so
    transport injection has to happen there. Cleaner than recording a real
    cassette for a 3-row controlled payload.
    """
    payload = {
        "items": [
            {
                "volumeInfo": {
                    "title": "Both",
                    "authors": ["A1"],
                    "industryIdentifiers": [
                        {"type": "ISBN_10", "identifier": "0241638194"},
                        {"type": "ISBN_13", "identifier": "9780241638194"},
                    ],
                    "imageLinks": {"thumbnail": "https://x/both.jpg"},
                }
            },
            {
                "volumeInfo": {
                    "title": "Ten only",
                    "authors": ["A2"],
                    # 0140449132 is a valid ISBN-10 → promotes cleanly.
                    "industryIdentifiers": [
                        {"type": "ISBN_10", "identifier": "0140449132"},
                    ],
                }
            },
            {
                "volumeInfo": {
                    "title": "No ISBN",
                    "authors": ["A3"],
                    "industryIdentifiers": [
                        {"type": "OTHER", "identifier": "xyz"},
                    ],
                }
            },
        ]
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/books/v1/volumes"
        assert request.url.params.get("q") == "apollo"
        assert request.url.params.get("maxResults") == "5"
        return httpx.Response(200, json=payload)

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def fake_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr("book_alerter.metadata.httpx.AsyncClient", fake_client)

    results = asyncio.run(search_books("apollo", limit=5))
    assert len(results) == 2
    assert results[0].isbn13 == "9780241638194"
    assert results[0].title == "Both"
    assert results[0].cover_url == "https://x/both.jpg"
    assert results[1].isbn13 == "9780140449136"  # promoted from ISBN_10
    assert results[1].title == "Ten only"
    assert results[1].cover_url is None


def test_search_books_returns_empty_on_429_instead_of_raising(monkeypatch):
    """Google Books returns 429 when the anonymous-IP daily quota is
    exhausted. `search_books` must swallow that and return `[]` so the FE
    can show "no results" — surfacing a 500 to the user would be misleading
    (the query isn't malformed; the upstream is rate-limited)."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={"error": {"code": 429, "message": "Quota exceeded"}},
        )

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def fake_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr("book_alerter.metadata.httpx.AsyncClient", fake_client)
    results = asyncio.run(search_books("anything", limit=5))
    assert results == []


def test_search_books_forwards_google_api_key(monkeypatch):
    """A non-empty `google_api_key` must be forwarded as `&key=...` so the
    request bypasses the anonymous-IP quota. Empty key must NOT add the
    param (Google rejects an empty key value)."""
    seen_params: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen_params.append(dict(request.url.params))
        return httpx.Response(200, json={"items": []})

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient

    def fake_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr("book_alerter.metadata.httpx.AsyncClient", fake_client)
    asyncio.run(search_books("apollo", limit=3, google_api_key="AIza-test"))
    asyncio.run(search_books("apollo", limit=3))
    assert seen_params[0].get("key") == "AIza-test"
    assert "key" not in seen_params[1]


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
