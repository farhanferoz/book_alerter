"""Integration tests for the Metadata endpoints (Task 7.6).

Provider behavior (HTTP race, payload extraction) is covered separately in
`tests/integration/test_metadata.py` against hand-written cassettes. These
tests assert the *handler* contract — ISBN normalization, error mapping
(422 / 404), search query/limit validation — so we monkeypatch
`lookup_isbn` and `search_books` at the import site
(`book_alerter.api.metadata.{lookup_isbn,search_books}`) rather than the
source module. Patching the source would leave the handler's already-bound
reference pointing at the original function.
"""

from __future__ import annotations

import pytest

from book_alerter.metadata import BookMetadata, BookMetadataWithIsbn

# --- GET /api/metadata/lookup -----------------------------------------------


def test_lookup_normalizes_isbn10_before_calling_lookup(api_client, monkeypatch):
    """Raw ISBN-10 → handler calls `lookup_isbn` with the normalized
    ISBN-13, and the canned `BookMetadata` is serialized back."""
    seen: dict[str, str] = {}

    async def fake_lookup(isbn13: str, **kwargs) -> BookMetadata:
        seen["isbn13"] = isbn13
        return BookMetadata(title="T", author="A", cover_url="https://x/c.jpg")

    monkeypatch.setattr(
        "book_alerter.api.metadata.lookup_isbn", fake_lookup
    )
    # 0241638194 is a valid ISBN-10 → normalizes to 9780241638194.
    resp = api_client.get("/api/metadata/lookup", params={"isbn": "0241638194"})
    assert resp.status_code == 200, resp.text
    assert seen["isbn13"] == "9780241638194"
    body = resp.json()
    assert body == {"title": "T", "author": "A", "cover_url": "https://x/c.jpg"}


def test_lookup_invalid_isbn_returns_422(api_client, monkeypatch):
    """Garbage input → `to_isbn13` raises ValueError → handler returns 422."""

    async def fake_lookup(isbn13: str, **kwargs) -> BookMetadata:  # pragma: no cover
        raise AssertionError("should not be called when ISBN is invalid")

    monkeypatch.setattr(
        "book_alerter.api.metadata.lookup_isbn", fake_lookup
    )
    resp = api_client.get("/api/metadata/lookup", params={"isbn": "not-an-isbn"})
    assert resp.status_code == 422, resp.text


def test_lookup_both_providers_empty_returns_404(api_client, monkeypatch):
    """When `lookup_isbn` raises `LookupError`, the handler maps it to 404."""

    async def fake_lookup(isbn13: str, **kwargs) -> BookMetadata:
        raise LookupError(f"no metadata found for ISBN {isbn13!r}")

    monkeypatch.setattr(
        "book_alerter.api.metadata.lookup_isbn", fake_lookup
    )
    resp = api_client.get(
        "/api/metadata/lookup", params={"isbn": "9780241638194"}
    )
    assert resp.status_code == 404, resp.text


# --- GET /api/metadata/search -----------------------------------------------


def test_search_returns_list_of_candidates(api_client, monkeypatch):
    """Happy path — handler forwards `q` to `search_books` and serializes
    the resulting `BookMetadataWithIsbn` list."""
    seen: dict[str, object] = {}

    async def fake_search(query: str, limit: int = 10, **kwargs):
        seen["query"] = query
        seen["limit"] = limit
        return [
            BookMetadataWithIsbn(
                isbn13="9780241638194",
                title="Apollo Remastered",
                author="Andy Saunders",
                cover_url="https://x/c.jpg",
            ),
            BookMetadataWithIsbn(
                isbn13="9780000000001",
                title="Another",
                author="Someone",
                cover_url=None,
            ),
        ]

    monkeypatch.setattr(
        "book_alerter.api.metadata.search_books", fake_search
    )
    resp = api_client.get(
        "/api/metadata/search", params={"q": "apollo remastered"}
    )
    assert resp.status_code == 200, resp.text
    assert seen == {"query": "apollo remastered", "limit": 10}
    body = resp.json()
    assert len(body) == 2
    assert body[0]["isbn13"] == "9780241638194"
    assert body[0]["title"] == "Apollo Remastered"
    assert body[1]["cover_url"] is None


def test_search_missing_query_returns_422(api_client):
    """`q` is required — FastAPI returns 422 when omitted."""
    resp = api_client.get("/api/metadata/search")
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("limit", [0, 50])
def test_search_limit_out_of_range_returns_422(api_client, limit):
    """`limit` must be in [1, 40] — Google Books caps `maxResults` at 40."""
    resp = api_client.get(
        "/api/metadata/search", params={"q": "x", "limit": limit}
    )
    assert resp.status_code == 422, resp.text


def test_search_empty_results_returns_empty_list(api_client, monkeypatch):
    """No matches → 200 with `[]` (not 404 — search-no-results is a valid
    answer, distinct from lookup-by-known-ISBN-not-found)."""

    async def fake_search(query: str, limit: int = 10, **kwargs):
        return []

    monkeypatch.setattr(
        "book_alerter.api.metadata.search_books", fake_search
    )
    resp = api_client.get(
        "/api/metadata/search", params={"q": "asdfqwerzxcv"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == []
