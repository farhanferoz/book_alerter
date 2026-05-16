"""Cover-image proxy endpoint.

`GET /api/covers/{isbn13}` serves the disk-cached cover for the given book.
On a cache miss, the upstream `Book.cover_url` is fetched once and written
to `data/covers/<isbn13>`; subsequent requests stream from disk. See
`book_alerter.covers` for the cache primitives.

The route exists so the SPA can render covers as same-origin images,
bypassing browser shield blocklists that fire on third-party CDN hosts
(Amazon, OpenLibrary). The DB still holds the upstream URL on `Book.cover_url`.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Response, status
from sqlmodel import select

from book_alerter.api.deps import SessionDep
from book_alerter.covers import cover_path, fetch_and_cache, sniff_mime
from book_alerter.db import models

router = APIRouter(prefix="/api/covers", tags=["covers"])

_CACHE_CONTROL = "public, max-age=86400"


@router.get("/{isbn13}", include_in_schema=False)
async def get_cover(isbn13: str, session: SessionDep) -> Response:
    path = cover_path(isbn13)
    if not path.exists():
        book = session.exec(
            select(models.Book).where(models.Book.isbn13 == isbn13)
        ).first()
        if book is None or not book.cover_url:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
        if await fetch_and_cache(isbn13, book.cover_url) is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    data = path.read_bytes()
    return Response(
        content=data,
        media_type=sniff_mime(data),
        headers={"Cache-Control": _CACHE_CONTROL},
    )
