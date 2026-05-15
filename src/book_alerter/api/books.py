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

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Response, status
from pydantic import BaseModel, Field
from sqlmodel import select

from book_alerter import keepa, keepa_chart
from book_alerter.api.deps import ConfigDep, SchedulerDep, SessionDep
from book_alerter.db import models
from book_alerter.sources.normalizers import to_isbn13
from book_alerter.stats import BookStats, compute_book_stats

router = APIRouter(prefix="/api/books", tags=["books"])


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


class PriceObservationOut(BaseModel):
    """Wire mirror of `book_alerter.db.models.PriceObservation`.

    Excludes the internal `raw` source payload and the `is_duplicate_of`
    dedup pointer — the observations endpoint filters duplicates out before
    serializing, so the pointer is irrelevant to callers.
    """
    id: int
    book_id: int
    source: str
    seller: str | None
    condition: str
    price_minor: int
    currency: str
    shipping_minor: int | None
    total_minor: int
    url: str
    observed_at: datetime

    @classmethod
    def from_obs(cls, obs: models.PriceObservation) -> PriceObservationOut:
        return cls(
            id=obs.id or 0,
            book_id=obs.book_id,
            source=obs.source,
            seller=obs.seller,
            condition=obs.condition,
            price_minor=obs.price_minor,
            currency=obs.currency,
            shipping_minor=obs.shipping_minor,
            total_minor=obs.total_minor,
            url=obs.url,
            observed_at=obs.observed_at,
        )


class ObservationsPage(BaseModel):
    """Cursor-paginated page of price observations (newest-first).

    `next_before` is the `observed_at` (ISO 8601) of the last row in `items`;
    pass it as the `before` query param to fetch the next page. `None` when
    `len(items) < limit` (i.e., the page is not full → no more rows).
    """
    items: list[PriceObservationOut]
    next_before: str | None


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
    background_tasks: BackgroundTasks,
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

    # Fire-and-forget Keepa backfill so the days-of-history gate clears
    # immediately for books Amazon UK indexes. No-op if Keepa has no data.
    engine = session.get_bind()

    def _factory():
        from sqlmodel import Session
        return Session(engine)

    background_tasks.add_task(
        _keepa_backfill_blocking,
        book.id or 0,
        book.isbn13,
        _factory,
        Path("data/keepa-cache"),
    )

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


@router.get("/{book_id}/observations", response_model=ObservationsPage)
def list_observations(
    book_id: int,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    before: datetime | None = None,
    source: str | None = None,
) -> ObservationsPage:
    """Paginated price history for a book (newest-first).

    Excludes deduplicated rows (`is_duplicate_of IS NOT NULL`). Cursor via
    `before` (ISO 8601 `observed_at`); response includes `next_before` for the
    next page.
    """
    if session.get(models.Book, book_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="book not found")

    stmt = (
        select(models.PriceObservation)
        .where(models.PriceObservation.book_id == book_id)
        .where(models.PriceObservation.is_duplicate_of.is_(None))  # type: ignore[union-attr]
    )
    if source is not None:
        stmt = stmt.where(models.PriceObservation.source == source)
    if before is not None:
        stmt = stmt.where(models.PriceObservation.observed_at < before)
    stmt = stmt.order_by(models.PriceObservation.observed_at.desc()).limit(limit)  # type: ignore[attr-defined]

    rows = session.exec(stmt).all()
    items = [PriceObservationOut.from_obs(r) for r in rows]
    next_before = (
        rows[-1].observed_at.isoformat() if len(rows) == limit and rows else None
    )
    return ObservationsPage(items=items, next_before=next_before)


class RefetchTriggered(BaseModel):
    source: str
    run_id: int


class RefetchSkipped(BaseModel):
    source: str
    reason: Literal["disabled", "backoff_active"]


class RefetchResult(BaseModel):
    """Result of `POST /api/books/{id}/refetch`.

    Fans out across every configured source. `triggered` lists sources whose
    `scheduler.trigger_now` returned a real `run_id`. `skipped` records sources
    that were intentionally not triggered: `reason="disabled"` for sources with
    `enabled=False` in config, `reason="backoff_active"` when the scheduler
    returned `0` (backoff gate). Empty `cfg.sources` yields two empty lists.
    """
    triggered: list[RefetchTriggered]
    skipped: list[RefetchSkipped]


@router.post("/{book_id}/refetch", response_model=RefetchResult)
async def refetch_book(
    book_id: int,
    session: SessionDep,
    cfg: ConfigDep,
    scheduler: SchedulerDep,
) -> RefetchResult:
    """Trigger an immediate scrape across every enabled source for this book.

    The refetch button is "ask all sources about this book again" — it does
    **not** pre-filter by which sources have observed the book. Disabled
    sources surface in `skipped` with `reason="disabled"`; sources whose
    backoff gate is active (scheduler returns 0) surface with
    `reason="backoff_active"`. 404 if the book id is not found.
    """
    if session.get(models.Book, book_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="book not found"
        )
    triggered: list[RefetchTriggered] = []
    skipped: list[RefetchSkipped] = []
    enabled_names = [n for n, sc in cfg.sources.items() if sc.enabled]
    for n, sc in cfg.sources.items():
        if not sc.enabled:
            skipped.append(RefetchSkipped(source=n, reason="disabled"))
    # Fire enabled sources concurrently — each `trigger_now` is async and may
    # block on network I/O / scheduler queue. `gather` preserves input order
    # so `triggered`/`skipped` follow `cfg.sources` iteration order.
    run_ids = await asyncio.gather(
        *(scheduler.trigger_now(n) for n in enabled_names)
    )
    for n, run_id in zip(enabled_names, run_ids, strict=True):
        if run_id == 0:
            skipped.append(RefetchSkipped(source=n, reason="backoff_active"))
        else:
            triggered.append(RefetchTriggered(source=n, run_id=run_id))
    return RefetchResult(triggered=triggered, skipped=skipped)


@router.get("/{book_id}/stats", response_model=BookStatsOut)
def get_book_stats(
    book_id: int,
    session: SessionDep,
) -> BookStatsOut:
    """Return the full `BookStats` for a book (zero-obs case included)."""
    if session.get(models.Book, book_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="book not found")
    return BookStatsOut.from_dataclass(compute_book_stats(book_id, session))


def _keepa_backfill_blocking(
    book_id: int,
    isbn13: str,
    session_factory,
    cache_dir: Path,
) -> int:
    """Background worker — fetch Keepa PNG, extract, persist, return row count.

    Idempotent: skips if any source='keepa' row already exists for the book.
    Synchronous SQL inside an async-friendly wrapper so FastAPI's
    BackgroundTasks can hand it off without blocking the request thread.
    """
    with session_factory() as session:
        existing = session.exec(
            select(models.PriceObservation)
            .where(
                models.PriceObservation.book_id == book_id,
                models.PriceObservation.source == "keepa",
            )
            .limit(1)
        ).first()
        if existing is not None:
            return 0

    # asyncio.run because we're called from a thread pool worker.
    png = asyncio.run(keepa.fetch_chart_png(isbn13, cache_dir))
    if png is None:
        return 0

    extractions = keepa_chart.extract_observations(png)
    if not extractions:
        return 0

    from book_alerter.sources.normalizers import asin_for_amazon_uk
    asin = asin_for_amazon_uk(isbn13)
    amazon_url = f"https://www.amazon.co.uk/dp/{asin}"

    inserted = 0
    with session_factory() as session:
        for ext in extractions:
            seller, condition = keepa_chart.SERIES_TO_SELLER_CONDITION[ext.series]
            session.add(
                models.PriceObservation(
                    book_id=book_id,
                    source="keepa",
                    seller=seller,
                    condition=condition,
                    price_minor=ext.price_minor,
                    currency="GBP",
                    shipping_minor=0,
                    total_minor=ext.price_minor,
                    url=amazon_url,
                    observed_at=datetime.combine(ext.observed_at, datetime.min.time()).replace(tzinfo=UTC),
                    raw={"series": ext.series, "from": "keepa_chart_png"},
                )
            )
            inserted += 1
        session.commit()
    return inserted


@router.post("/{book_id}/keepa-backfill")
async def keepa_backfill(
    book_id: int,
    session: SessionDep,
) -> dict:
    """Trigger a one-shot Keepa backfill for `book_id`.

    Fetches the public Keepa PNG, runs the OCR-based numeric extractor, and
    inserts the extracted (date, series, price) tuples as historical
    PriceObservation rows. Idempotent: returns inserted=0 if backfill ran
    previously for this book. Synchronous (waits for the extraction) to
    return a clear count to the caller; book-creation auto-backfill is
    fire-and-forget via BackgroundTasks instead.
    """
    book = session.get(models.Book, book_id)
    if book is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="book not found")
    engine = session.get_bind()

    def _factory():
        from sqlmodel import Session
        return Session(engine)

    inserted = await asyncio.to_thread(
        _keepa_backfill_blocking,
        book_id,
        book.isbn13,
        _factory,
        Path("data/keepa-cache"),
    )
    return {"inserted": inserted, "book_id": book_id}


@router.get("/{book_id}/keepa-chart.png")
async def get_keepa_chart(book_id: int, session: SessionDep) -> Response:
    """Serve the Keepa Amazon UK price-history PNG for a book.

    Server-side proxy + 24h disk cache so we don't hammer Keepa's edge and so
    the FE doesn't expose the user's IP to Keepa. Returns 404 if Keepa has
    no chart for this ISBN (book too niche, just published, etc.).
    """
    book = session.get(models.Book, book_id)
    if book is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="book not found")
    cache_dir = Path("data/keepa-cache")
    png = await keepa.fetch_chart_png(book.isbn13, cache_dir)
    if png is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="keepa has no chart for this ISBN",
        )
    # Cache for an hour on the browser side. Keepa's data refreshes
    # roughly every few hours; matching 60min keeps the FE responsive
    # without re-fetching constantly.
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )
