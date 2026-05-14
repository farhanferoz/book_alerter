"""Metadata lookup + search endpoints (Phase 7 Task 7.6, plan line 2544-2547).

Two endpoints under `/api/metadata`:

- `GET /api/metadata/lookup?isbn=<raw>` — normalize via `to_isbn13` (422 on
  invalid) then race OpenLibrary + Google Books via `lookup_isbn`. 404 when
  both providers return empty (i.e. `LookupError`).
- `GET /api/metadata/search?q=<query>&limit=<n>` — free-text Google Books
  search via `search_books`. Returns `list[BookMetadataWithIsbn]` so the
  add-book UI can render click-to-add candidates. `limit` defaults to 10,
  caps at 40 (matches Google Books' `maxResults` ceiling).

Both handlers are thin wrappers around `book_alerter.metadata`; the heavy
lifting (HTTP race, payload extraction, ISBN-13 promotion) lives there.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status

from book_alerter.metadata import (
    BookMetadata,
    BookMetadataWithIsbn,
    lookup_isbn,
    search_books,
)
from book_alerter.sources.normalizers import to_isbn13

router = APIRouter(prefix="/api/metadata", tags=["metadata"])


@router.get("/lookup")
async def get_metadata_lookup(
    isbn: str = Query(..., description="Raw ISBN-10 or ISBN-13"),
) -> BookMetadata:
    """Normalize the input ISBN and race providers for metadata.

    Returns 422 when the input can't be parsed as an ISBN, 404 when both
    providers return no usable data.
    """
    try:
        normalized = to_isbn13(isbn)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    try:
        return await lookup_isbn(normalized)
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.get("/search")
async def get_metadata_search(
    q: str = Query(..., min_length=1, description="Free-text title/author query"),
    limit: int = Query(10, ge=1, le=40, description="Max results (Google Books cap is 40)"),
) -> list[BookMetadataWithIsbn]:
    """Free-text search via Google Books. Empty list when no matches."""
    return await search_books(q, limit=limit)
