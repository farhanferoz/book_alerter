"""Metadata lookup + search endpoints.

- `GET /api/metadata/lookup?isbn=<raw>` — normalize via `to_isbn13` (422 on
  invalid) then race OpenLibrary + Google Books. Falls back to an Amazon UK
  Playwright scrape iff `config.metadata.amazon_uk_fallback`. 404 when every
  path comes up empty.
- `GET /api/metadata/search?q=<query>&limit=<n>` — free-text Google Books
  search. Returns `[]` on upstream failure (429 quota, network) so the FE
  shows a clean "no results" rather than a 500.

Both providers honour `config.metadata.google_books_api_key` to bypass the
shared anonymous quota.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel

from book_alerter.api.deps import ConfigDep, HttpDep
from book_alerter.metadata import (
    BookMetadata,
    BookMetadataWithIsbn,
    ProductMetadata,
    fetch_amazon_uk_product_metadata,
    lookup_isbn,
    search_books,
)
from book_alerter.sources.normalizers import to_asin, to_isbn13

router = APIRouter(prefix="/api/metadata", tags=["metadata"])


@router.get("/lookup")
async def get_metadata_lookup(
    cfg: ConfigDep,
    http: HttpDep,
    isbn: str = Query(..., description="Raw ISBN-10 or ISBN-13"),
) -> BookMetadata:
    """Normalize the input ISBN and race providers for metadata.

    Returns 422 when the input can't be parsed as an ISBN, 404 when every
    provider (including the optional Amazon UK fallback) comes up empty.
    """
    try:
        normalized = to_isbn13(isbn)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc
    try:
        return await lookup_isbn(
            normalized,
            google_api_key=cfg.metadata.google_books_api_key,
            allow_amazon_fallback=cfg.metadata.amazon_uk_fallback,
            http=http,
        )
    except LookupError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc


@router.get("/search")
async def get_metadata_search(
    cfg: ConfigDep,
    http: HttpDep,
    q: str = Query(..., min_length=1, description="Free-text title/author query"),
    limit: int = Query(10, ge=1, le=40, description="Max results (Google Books cap is 40)"),
) -> list[BookMetadataWithIsbn]:
    """Free-text search via Google Books. Empty list when no matches OR when
    Google returns 429/5xx (the FE handles empty as "no results"; surfacing
    the underlying error would just be misleading noise)."""
    return await search_books(
        q,
        limit=limit,
        google_api_key=cfg.metadata.google_books_api_key,
        http=http,
    )


class AsinLookupRequest(BaseModel):
    input: str


@router.post("/asin-lookup")
async def post_asin_lookup(payload: AsinLookupRequest) -> ProductMetadata:
    """Normalise input → ASIN → Playwright dp scrape → product metadata.

    Used by the Add-Product dialog to pre-fill title + image + brand. 422
    on garbage input, 502 on render failure (so the FE can surface a
    user-actionable error instead of a generic 500).
    """
    try:
        asin = to_asin(payload.input)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=str(exc),
        ) from exc

    result = await fetch_amazon_uk_product_metadata(asin)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=(
                f"could not fetch Amazon UK metadata for ASIN {asin}; the dp "
                "page may be bot-blocked or the ASIN may not exist"
            ),
        )
    return result
