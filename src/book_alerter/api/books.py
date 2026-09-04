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
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from book_alerter import keepa, keepa_backfill
from book_alerter.api._serializers import UtcDateTime, to_z_iso
from book_alerter.api.deps import ConfigDep, SchedulerDep, SessionDep
from book_alerter.config import RecommendationConfig
from book_alerter.db import models
from book_alerter.enums import ItemStatus
from book_alerter.sources.normalizers import to_isbn13
from book_alerter.stats import (
    _BOOK_SCHEMA,
    WINDOW_DAYS,
    BookStats,
    Signal,
    WindowStats,
    compute_book_stats,
    compute_signal,
    compute_stats_for_items,
)

router = APIRouter(prefix="/api/books", tags=["books"])


def _effective_window_days(book: models.Book, cfg) -> int:
    return book.percentile_window_days or cfg.recommendation.percentile_window_days


def _stats_for(book: models.Book, session: Session, cfg) -> BookStats:
    """compute_book_stats wired with this book's window and the cascade
    parameters from RecommendationConfig. Used by every single-book endpoint;
    `list_books` calls compute_book_stats directly so it can share the global
    medians across all books in one request."""
    return compute_book_stats(
        book.id or 0,
        session,
        _effective_window_days(book, cfg),
        default_shipping_minor=cfg.recommendation.default_shipping_minor,
        min_global_median_observations=cfg.recommendation.min_global_median_observations,
    )


# --- DTOs -------------------------------------------------------------------


class BookCreate(BaseModel):
    isbn: str
    title: str
    author: str
    cover_url: str | None = None
    format: Literal["paperback", "hardcover", "any"] = "any"
    target_price_minor: int | None = None
    percentile_threshold: int | None = None
    percentile_window_days: int | None = None
    notes: str | None = None


class BookPatch(BaseModel):
    target_price_minor: int | None = None
    percentile_threshold: int | None = None
    percentile_window_days: int | None = None
    status: Literal["active", "archived", "bought"] | None = None
    muted_until: datetime | None = None
    notes: str | None = None
    alert_kinds_disabled: list[str] | None = None


class WindowStatsOut(BaseModel):
    """Wire mirror of `book_alerter.stats.WindowStats` — per-window
    distribution summary surfaced for the dashboard mini-bars column and
    the detail-page box-plot. `count` lets the UI dim a window whose
    sample size is too small to trust."""
    count: int
    rank: int | None
    p5: int | None
    p25: int | None
    p50: int | None
    p75: int | None
    p95: int | None

    @classmethod
    def from_dataclass(cls, w: WindowStats) -> WindowStatsOut:
        return cls(
            count=w.count, rank=w.rank,
            p5=w.p5, p25=w.p25, p50=w.p50, p75=w.p75, p95=w.p95,
        )


class BookStatsOut(BaseModel):
    """Wire mirror of `book_alerter.stats.BookStats`.

    Excludes `sorted_totals` (internal percentile cache).

    `book_id` and `item_id` carry the same value (the underlying dataclass
    field is named `book_id` for historical reasons). New product callers
    should read `item_id`; existing book callers keep reading `book_id`.
    """
    book_id: int
    item_id: int
    current_best_total_minor: int | None
    current_best_price_minor: int | None
    current_best_shipping_minor: int | None
    current_best_source: str | None
    current_best_seller: str | None
    current_best_condition: str | None
    current_best_url: str | None
    all_time_min_total_minor: int | None
    all_time_max_total_minor: int | None
    observation_count: int
    days_of_history: int
    last_observed_at: UtcDateTime | None
    last_polled_at: UtcDateTime | None
    percentile_window_days: int
    current_percentile_rank: int | None
    current_effective_total_minor: int | None
    shipping_estimate_minor: int | None
    # Authoritative signal computed once on the backend with the live
    # `RecommendationConfig`. The FE renders this directly — no
    # client-side re-derivation, so the dashboard pill can't drift from
    # what the alert dispatcher will fire.
    signal: Signal | None
    # Per-window distribution summaries — keys: "1m", "3m", "12m". The
    # dashboard `MiniBars` column reads each window's `rank`; the detail-
    # page box-plot reads p5/p25/p50/p75/p95.
    windows: dict[str, WindowStatsOut]

    @classmethod
    def from_dataclass(
        cls,
        s: BookStats,
        book: models.Book | models.Product | None = None,
        reco: RecommendationConfig | None = None,
    ) -> BookStatsOut:
        signal = (
            compute_signal(book, s, reco)
            if book is not None and reco is not None
            else None
        )
        return cls(
            book_id=s.book_id,
            item_id=s.item_id,
            current_best_total_minor=s.current_best_total_minor,
            current_best_price_minor=s.current_best_price_minor,
            current_best_shipping_minor=s.current_best_shipping_minor,
            current_best_source=s.current_best_source,
            current_best_seller=s.current_best_seller,
            current_best_condition=s.current_best_condition,
            current_best_url=s.current_best_url,
            all_time_min_total_minor=s.all_time_min_total_minor,
            all_time_max_total_minor=s.all_time_max_total_minor,
            observation_count=s.observation_count,
            days_of_history=s.days_of_history,
            last_observed_at=s.last_observed_at,
            last_polled_at=s.last_polled_at,
            percentile_window_days=s.percentile_window_days,
            current_percentile_rank=s.current_percentile_rank,
            current_effective_total_minor=s.current_effective_total_minor,
            shipping_estimate_minor=s.shipping_estimate_minor,
            signal=signal,
            # Emit all canonical keys (filling missing with empties) so the
            # FE layout stays stable across books with sparse history.
            windows={
                k: WindowStatsOut.from_dataclass(s.windows.get(k, WindowStats()))
                for k in WINDOW_DAYS
            },
        )


class PriceObservationOut(BaseModel):
    """Wire mirror of `book_alerter.db.models.PriceObservation`. Excludes the
    internal `raw` source payload.

    `observed_at` is the FIRST time this offer was seen — the right x for a
    price timeline; it never changes. `last_seen` is the most recent scrape
    that re-confirmed the offer and `url` is that scrape's link (a
    parser-corrected link supersedes a stale one an older parser recorded)
    — both live directly on the row (`PriceObservation.last_seen_at` /
    `.url`), updated in place by `scheduler._persist` on every re-confirming
    scrape since migration 0021 (T3.2, heartbeat compaction) replaced
    duplicate-row heartbeats with this update-in-place model.
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
    observed_at: UtcDateTime
    last_seen: UtcDateTime

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
            last_seen=obs.last_seen_at,
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
    percentile_window_days: int | None
    status: Literal["active", "archived", "bought"]
    bought_price_minor: int | None
    notes: str | None
    alert_kinds_disabled: list[str] = Field(default_factory=list)
    muted_until: UtcDateTime | None
    # Per-book scrape health. last_scrape_error is populated by the scheduler
    # when a source fails for this book and cleared on the next success.
    last_scrape_attempt_at: UtcDateTime | None = None
    last_scrape_error: str | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime
    stats: BookStatsOut

    @classmethod
    def from_book(
        cls,
        book: models.Book,
        stats: BookStats,
        reco: RecommendationConfig | None = None,
    ) -> BookOut:
        return cls(
            id=book.id or 0,
            isbn13=book.isbn13,
            title=book.title,
            author=book.author,
            # Same-origin proxy URL so browser shields don't block third-party
            # CDN requests. Bytes are served by `api/covers.py`, lazily fetched
            # from `book.cover_url` (kept upstream in the DB row) on first hit.
            cover_url=(f"/api/covers/{book.isbn13}" if book.cover_url else None),
            format=book.format,
            region=book.region,
            currency=book.currency,
            target_price_minor=book.target_price_minor,
            percentile_threshold=book.percentile_threshold,
            percentile_window_days=book.percentile_window_days,
            status=book.status,
            bought_price_minor=book.bought_price_minor,
            notes=book.notes,
            alert_kinds_disabled=list(book.alert_kinds_disabled or []),
            muted_until=book.muted_until,
            last_scrape_attempt_at=book.last_scrape_attempt_at,
            last_scrape_error=book.last_scrape_error,
            created_at=book.created_at,
            updated_at=book.updated_at,
            stats=BookStatsOut.from_dataclass(stats, book=book, reco=reco),
        )


# --- Handlers ---------------------------------------------------------------


@router.get("", response_model=list[BookOut])
def list_books(
    request: Request,
    session: SessionDep,
    cfg: ConfigDep,
    include_archived: bool = False,
) -> list[BookOut]:
    stmt = select(models.Book)
    if not include_archived:
        stmt = stmt.where(models.Book.status != ItemStatus.ARCHIVED)
    books = session.exec(stmt).all()
    medians = request.app.state.medians_cache.get_or_compute(
        session,
        schema=_BOOK_SCHEMA,
        min_observations=cfg.recommendation.min_global_median_observations,
    )
    ids = [b.id or 0 for b in books]
    window_days = {iid: _effective_window_days(b, cfg) for iid, b in zip(ids, books, strict=True)}
    stats_by_id = compute_stats_for_items(
        ids,
        session,
        schema=_BOOK_SCHEMA,
        cfg=cfg.recommendation,
        window_days=window_days,
        medians=medians,
    )
    return [
        BookOut.from_book(b, stats_by_id[b.id or 0], reco=cfg.recommendation)
        for b in books
    ]


@router.post("", response_model=BookOut, status_code=status.HTTP_201_CREATED)
def create_book(
    payload: BookCreate,
    session: SessionDep,
    cfg: ConfigDep,
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
            detail={
                "message": f"book with ISBN-13 {isbn13} already exists",
                "book_id": existing.id,
                "isbn13": isbn13,
            },
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
        percentile_window_days=payload.percentile_window_days,
        notes=payload.notes,
        created_at=now,
        updated_at=now,
    )
    session.add(book)
    session.commit()
    session.refresh(book)
    assert book.id is not None  # set by session.refresh

    # Fire-and-forget Keepa backfill so the days-of-history gate clears
    # immediately for books Amazon UK indexes. No-op if Keepa has no data.
    engine = session.get_bind()
    background_tasks.add_task(
        _keepa_backfill_blocking,
        book.id,
        book.isbn13,
        lambda: Session(engine),
    )

    stats = _stats_for(book, session, cfg)
    return BookOut.from_book(book, stats, reco=cfg.recommendation)


@router.get("/{book_id}", response_model=BookOut)
def get_book(
    book_id: int,
    session: SessionDep,
    cfg: ConfigDep,
) -> BookOut:
    book = session.get(models.Book, book_id)
    if book is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="book not found")
    stats = _stats_for(book, session, cfg)
    return BookOut.from_book(book, stats, reco=cfg.recommendation)


@router.patch("/{book_id}", response_model=BookOut)
def patch_book(
    book_id: int,
    payload: BookPatch,
    session: SessionDep,
    cfg: ConfigDep,
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

    stats = _stats_for(book, session, cfg)
    return BookOut.from_book(book, stats, reco=cfg.recommendation)


@router.delete("/{book_id}", response_model=BookOut)
def delete_book(
    book_id: int,
    session: SessionDep,
    cfg: ConfigDep,
    hard: bool = False,
) -> BookOut | dict[str, object]:
    book = session.get(models.Book, book_id)
    if book is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="book not found")

    if hard:
        stats = _stats_for(book, session, cfg)
        out = BookOut.from_book(book, stats, reco=cfg.recommendation)
        # Cascade is now schema-enforced (migration 0013 + foreign_keys
        # pragma in db/session.py). The book delete fans out automatically
        # to PriceObservation, Alert, NotificationDelivery, and
        # BookSignalState — no hand-cascade needed.
        session.delete(book)
        session.commit()
        return out

    book.status = ItemStatus.ARCHIVED
    book.updated_at = datetime.now(UTC)
    session.add(book)
    session.commit()
    session.refresh(book)
    stats = _stats_for(book, session, cfg)
    return BookOut.from_book(book, stats, reco=cfg.recommendation)


@router.get("/{book_id}/observations", response_model=ObservationsPage)
def list_observations(
    book_id: int,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    before: datetime | None = None,
    source: str | None = None,
) -> ObservationsPage:
    """Paginated price history for a book (newest-first).

    Cursor via `before` (ISO 8601 `observed_at`); response includes
    `next_before` for the next page. One row per distinct offer since
    migration 0021 (T3.2) — no more `is_duplicate_of` rows to filter out.
    """
    if session.get(models.Book, book_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="book not found")

    stmt = select(models.PriceObservation).where(models.PriceObservation.book_id == book_id)
    if source is not None:
        stmt = stmt.where(models.PriceObservation.source == source)
    if before is not None:
        stmt = stmt.where(models.PriceObservation.observed_at < before)
    stmt = stmt.order_by(models.PriceObservation.observed_at.desc()).limit(limit)  # type: ignore[attr-defined]

    rows = session.exec(stmt).all()
    items = [PriceObservationOut.from_obs(r) for r in rows]
    next_before = (
        to_z_iso(rows[-1].observed_at) if len(rows) == limit and rows else None
    )
    return ObservationsPage(items=items, next_before=next_before)


class RefetchTriggered(BaseModel):
    source: str
    run_id: int


class RefetchSkipped(BaseModel):
    source: str
    reason: Literal["disabled", "backoff_active", "kind_unsupported"]


class RefetchResult(BaseModel):
    """Result of `POST /api/{kind}/{id}/refetch` (both books and products use
    this shape via the shared `_run_refetch` helper below).

    `triggered` lists sources whose `scheduler.trigger_now` returned a real
    `run_id`. `skipped` records sources that were intentionally not triggered:
    `reason="disabled"` for sources with `enabled=False`; `reason="backoff_active"`
    when the scheduler returned `0`; `reason="kind_unsupported"` when the
    source's configured `item_kinds` doesn't include the kind being refetched.
    """
    triggered: list[RefetchTriggered]
    skipped: list[RefetchSkipped]


async def _run_refetch(cfg, scheduler, *, kind) -> RefetchResult:
    """Shared fan-out: for each configured source, decide triggered vs. skipped
    by (enabled, kind support, backoff gate). Re-used by both book and product
    refetch endpoints.

    `kind` is an `ItemKind`. Sources whose `SourceConfig.item_kinds` doesn't
    contain `kind` are marked `kind_unsupported` so a product-only source
    isn't fired when the user clicked refetch on a book (and vice versa).
    """
    triggered: list[RefetchTriggered] = []
    skipped: list[RefetchSkipped] = []
    eligible: list[str] = []
    for n, sc in cfg.sources.items():
        if not sc.enabled:
            skipped.append(RefetchSkipped(source=n, reason="disabled"))
            continue
        if kind not in set(sc.item_kinds):
            skipped.append(RefetchSkipped(source=n, reason="kind_unsupported"))
            continue
        eligible.append(n)
    # Fire eligible sources concurrently — each `trigger_now` is async and may
    # block on network I/O / scheduler queue. `gather` preserves input order
    # so `triggered`/`skipped` follow `cfg.sources` iteration order.
    run_ids = await asyncio.gather(
        *(scheduler.trigger_now(n) for n in eligible),
    )
    for n, run_id in zip(eligible, run_ids, strict=True):
        if run_id == 0:
            skipped.append(RefetchSkipped(source=n, reason="backoff_active"))
        else:
            triggered.append(RefetchTriggered(source=n, run_id=run_id))
    return RefetchResult(triggered=triggered, skipped=skipped)


@router.post("/{book_id}/refetch", response_model=RefetchResult)
async def refetch_book(
    book_id: int,
    session: SessionDep,
    cfg: ConfigDep,
    scheduler: SchedulerDep,
) -> RefetchResult:
    """Trigger an immediate scrape across every enabled BOOK-serving source.

    The refetch button is "ask all enabled book-serving sources about this
    book again." Sources whose `item_kinds` doesn't include BOOK are skipped
    with `reason="kind_unsupported"` so a product-only source doesn't fire
    a no-op cycle. Disabled sources surface in `skipped` with
    `reason="disabled"`; backoff-gated sources with `reason="backoff_active"`.
    404 if the book id is not found.
    """
    from book_alerter.enums import ItemKind

    if session.get(models.Book, book_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="book not found"
        )
    return await _run_refetch(cfg, scheduler, kind=ItemKind.BOOK)


@router.get("/{book_id}/stats", response_model=BookStatsOut)
def get_book_stats(
    book_id: int,
    session: SessionDep,
    cfg: ConfigDep,
) -> BookStatsOut:
    """Return the full `BookStats` for a book (zero-obs case included)."""
    book = session.get(models.Book, book_id)
    if book is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="book not found")
    return BookStatsOut.from_dataclass(
        _stats_for(book, session, cfg),
        book=book,
        reco=cfg.recommendation,
    )


def _keepa_backfill_blocking(
    book_id: int,
    isbn13: str,
    session_factory,
) -> int:
    """Thin wrapper around `keepa_backfill.backfill_blocking` with the book
    schema bound in. Kept as a named symbol so existing `background_tasks.
    add_task(...)` call sites and tests that monkeypatch this don't break.
    """
    return keepa_backfill.backfill_blocking(
        book_id, isbn13, session_factory, schema=keepa_backfill.BOOK_SCHEMA,
    )


@router.post("/{book_id}/keepa-backfill")
async def trigger_keepa_backfill(
    book_id: int,
    session: SessionDep,
) -> dict:
    """Trigger a one-shot Keepa backfill. Idempotent.

    Returns inserted=0 if a previous backfill already populated this book.
    Synchronous (awaits the extraction) so the caller gets a real count;
    the book-creation auto-backfill is fire-and-forget via BackgroundTasks.
    """
    book = session.get(models.Book, book_id)
    if book is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="book not found")
    engine = session.get_bind()
    inserted = await asyncio.to_thread(
        _keepa_backfill_blocking,
        book_id,
        book.isbn13,
        lambda: Session(engine),
    )
    return {"inserted": inserted, "book_id": book_id}


@router.get("/{book_id}/keepa-chart.png")
async def get_keepa_chart(book_id: int, session: SessionDep) -> Response:
    """Proxy the Keepa price-history PNG with a 24h server-side cache.

    Hides the user's IP from Keepa and absorbs repeated FE loads.
    """
    book = session.get(models.Book, book_id)
    if book is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="book not found")
    png = await asyncio.to_thread(keepa.fetch_chart_png, book.isbn13)
    if png is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="keepa has no chart for this ISBN",
        )
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )
