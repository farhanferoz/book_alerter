"""Books CRUD endpoints.

Implements `GET /api/books`, `POST /api/books`, `GET /api/books/{id}`,
`PATCH /api/books/{id}`, and `DELETE /api/books/{id}` (Phase 7 Task 7.1).

Design notes:

- `POST /api/books` accepts `{isbn, title, author, ...}` and stores values
  as-given (after `to_isbn13` normalization on the ISBN). It deliberately does
  NOT call `metadata.lookup_isbn` — that's a separate Phase 7.6 endpoint
  (`/api/metadata/lookup`) so the create handler stays offline-friendly and
  network-failure-free.
- `BookStats` is a dataclass (`stats.py`), so the response exposes it via a
  small Pydantic mirror DTO (`BookStatsOut`) rather than enabling
  `arbitrary_types_allowed`. Mirroring keeps the OpenAPI schema clean and
  decouples the wire format from the internal dataclass.
- `DELETE /api/books/{id}` defaults to soft-delete (`status="archived"`).
  Pass `?hard=true` to actually remove the row.
- `GET /api/books` excludes archived books by default. Pass
  `?include_archived=true` to include them.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from book_alerter.api.deps import get_session
from book_alerter.db import models
from book_alerter.sources.normalizers import to_isbn13
from book_alerter.stats import BookStats, compute_book_stats

router = APIRouter(prefix="/api/books", tags=["books"])

SessionDep = Annotated[Session, Depends(get_session)]


# --- DTOs -------------------------------------------------------------------


class BookCreate(BaseModel):
    isbn: str
    title: str
    author: str
    cover_url: str | None = None
    format: Literal["paperback", "hardcover", "any"] = "any"
    target_price_minor: int | None = None
    percentile_threshold: int | None = None
    notes: str | None = None


class BookPatch(BaseModel):
    target_price_minor: int | None = None
    percentile_threshold: int | None = None
    status: Literal["active", "archived", "bought"] | None = None
    muted_until: datetime | None = None
    notes: str | None = None
    alert_kinds_disabled: list[str] | None = None


class BookStatsOut(BaseModel):
    """Wire mirror of `book_alerter.stats.BookStats`.

    Excludes `sorted_totals` (internal percentile cache).
    """
    book_id: int
    current_best_total_minor: int | None
    current_best_source: str | None
    current_best_seller: str | None
    current_best_condition: str | None
    current_best_url: str | None
    p25_total_minor: int | None
    p50_total_minor: int | None
    p75_total_minor: int | None
    all_time_min_total_minor: int | None
    all_time_max_total_minor: int | None
    observation_count: int
    days_of_history: int
    last_observed_at: datetime | None

    @classmethod
    def from_dataclass(cls, s: BookStats) -> BookStatsOut:
        return cls(
            book_id=s.book_id,
            current_best_total_minor=s.current_best_total_minor,
            current_best_source=s.current_best_source,
            current_best_seller=s.current_best_seller,
            current_best_condition=s.current_best_condition,
            current_best_url=s.current_best_url,
            p25_total_minor=s.p25_total_minor,
            p50_total_minor=s.p50_total_minor,
            p75_total_minor=s.p75_total_minor,
            all_time_min_total_minor=s.all_time_min_total_minor,
            all_time_max_total_minor=s.all_time_max_total_minor,
            observation_count=s.observation_count,
            days_of_history=s.days_of_history,
            last_observed_at=s.last_observed_at,
        )


class BookOut(BaseModel):
    id: int
    isbn13: str
    title: str
    author: str
    cover_url: str | None
    format: Literal["paperback", "hardcover", "any"]
    region: str
    currency: str
    target_price_minor: int | None
    percentile_threshold: int | None
    status: Literal["active", "archived", "bought"]
    bought_price_minor: int | None
    notes: str | None
    alert_kinds_disabled: list[str] = Field(default_factory=list)
    muted_until: datetime | None
    created_at: datetime
    updated_at: datetime
    stats: BookStatsOut

    @classmethod
    def from_book(cls, book: models.Book, stats: BookStats) -> BookOut:
        return cls(
            id=book.id or 0,
            isbn13=book.isbn13,
            title=book.title,
            author=book.author,
            cover_url=book.cover_url,
            format=book.format,
            region=book.region,
            currency=book.currency,
            target_price_minor=book.target_price_minor,
            percentile_threshold=book.percentile_threshold,
            status=book.status,
            bought_price_minor=book.bought_price_minor,
            notes=book.notes,
            alert_kinds_disabled=list(book.alert_kinds_disabled or []),
            muted_until=book.muted_until,
            created_at=book.created_at,
            updated_at=book.updated_at,
            stats=BookStatsOut.from_dataclass(stats),
        )


# --- Handlers ---------------------------------------------------------------


@router.get("", response_model=list[BookOut])
def list_books(
    session: SessionDep,
    include_archived: bool = False,
) -> list[BookOut]:
    stmt = select(models.Book)
    if not include_archived:
        stmt = stmt.where(models.Book.status != "archived")
    books = session.exec(stmt).all()
    return [
        BookOut.from_book(b, compute_book_stats(b.id or 0, session))
        for b in books
    ]


@router.post("", response_model=BookOut, status_code=status.HTTP_201_CREATED)
def create_book(
    payload: BookCreate,
    session: SessionDep,
) -> BookOut:
    try:
        isbn13 = to_isbn13(payload.isbn)
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail=str(exc)
        ) from exc

    existing = session.exec(
        select(models.Book).where(models.Book.isbn13 == isbn13)
    ).first()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"book with ISBN-13 {isbn13} already exists",
        )

    now = datetime.now(UTC)
    book = models.Book(
        isbn13=isbn13,
        title=payload.title,
        author=payload.author,
        cover_url=payload.cover_url,
        format=payload.format,
        target_price_minor=payload.target_price_minor,
        percentile_threshold=payload.percentile_threshold,
        notes=payload.notes,
        created_at=now,
        updated_at=now,
    )
    session.add(book)
    session.commit()
    session.refresh(book)

    stats = compute_book_stats(book.id or 0, session)
    return BookOut.from_book(book, stats)


@router.get("/{book_id}", response_model=BookOut)
def get_book(
    book_id: int,
    session: SessionDep,
) -> BookOut:
    book = session.get(models.Book, book_id)
    if book is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="book not found")
    stats = compute_book_stats(book_id, session)
    return BookOut.from_book(book, stats)


@router.patch("/{book_id}", response_model=BookOut)
def patch_book(
    book_id: int,
    payload: BookPatch,
    session: SessionDep,
) -> BookOut:
    book = session.get(models.Book, book_id)
    if book is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="book not found")

    data = payload.model_dump(exclude_unset=True)
    for field_name, value in data.items():
        setattr(book, field_name, value)
    if data:
        book.updated_at = datetime.now(UTC)
        session.add(book)
        session.commit()
        session.refresh(book)

    stats = compute_book_stats(book_id, session)
    return BookOut.from_book(book, stats)


@router.delete("/{book_id}", response_model=BookOut)
def delete_book(
    book_id: int,
    session: SessionDep,
    hard: bool = False,
) -> BookOut | dict[str, object]:
    book = session.get(models.Book, book_id)
    if book is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="book not found")

    if hard:
        # Snapshot a response before deletion so callers see what was removed.
        stats = compute_book_stats(book_id, session)
        out = BookOut.from_book(book, stats)
        session.delete(book)
        session.commit()
        return out

    book.status = "archived"
    book.updated_at = datetime.now(UTC)
    session.add(book)
    session.commit()
    session.refresh(book)
    stats = compute_book_stats(book_id, session)
    return BookOut.from_book(book, stats)
