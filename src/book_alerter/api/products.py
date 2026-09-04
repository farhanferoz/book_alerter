"""Products CRUD endpoints.

Mirror of `api/books.py` for the product side. The underlying machinery
(stats engine, alert pipeline, source ABC) is shared via the model-
parameterised helpers in `stats.py` / `notifications/dispatcher.py` — the
duplication here is intentionally only on the FastAPI / Pydantic layer,
where each endpoint validates and shapes a distinct wire payload.

Design notes that diverge from `api/books.py`:
- `POST /api/products` accepts `{asin_or_url, title, ...}`. `to_asin`
  normalises the input (full Amazon URLs across TLDs collapse to the
  raw 10-char ASIN). 409 on duplicate.
- `PATCH /api/products/{id}` supports the per-product `track_used` toggle
  (default False = NEW offers only). No analogue on the book side.
- `DELETE /api/products/{id}` defaults to soft-delete (`status="archived"`).
  Pass `?hard=true` to remove the row + cascade child observations/alerts.
- `GET /api/products` excludes archived by default. Pass `?include_archived=true`.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, Field
from sqlmodel import Session, select

from book_alerter import keepa, keepa_backfill
from book_alerter.api._serializers import UtcDateTime, to_z_iso
from book_alerter.api.books import (
    BookStatsOut,  # shape is item-agnostic
    _run_refetch,
)
from book_alerter.api.books import (
    RefetchResult as BookRefetchResult,
)
from book_alerter.api.deps import ConfigDep, HttpDep, SchedulerDep, SessionDep
from book_alerter.config import RecommendationConfig
from book_alerter.covers import fetch_and_cache_url, sniff_mime
from book_alerter.db import models
from book_alerter.enums import ItemKind, ItemStatus
from book_alerter.sources.normalizers import to_asin
from book_alerter.stats import (
    _PRODUCT_SCHEMA,
    BookStats,
    compute_product_stats,
    compute_stats_for_items,
)

PRODUCT_IMAGE_CACHE_DIR = Path("data/product-images")

router = APIRouter(prefix="/api/products", tags=["products"])


def _effective_window_days(product: models.Product, cfg) -> int:
    return product.percentile_window_days or cfg.recommendation.percentile_window_days


def _stats_for(product: models.Product, session: Session, cfg) -> BookStats:
    """compute_product_stats wired with this product's window and the cascade
    parameters from RecommendationConfig."""
    return compute_product_stats(
        product.id or 0,
        session,
        _effective_window_days(product, cfg),
        default_shipping_minor=cfg.recommendation.default_shipping_minor,
        min_global_median_observations=cfg.recommendation.min_global_median_observations,
        prime=cfg.recommendation.amazon_prime,
    )


# --- DTOs -------------------------------------------------------------------


class ProductCreate(BaseModel):
    asin_or_url: str
    title: str
    image_url: str | None = None
    brand: str | None = None
    target_price_minor: int | None = None
    percentile_threshold: int | None = None
    percentile_window_days: int | None = None
    notes: str | None = None
    track_used: bool = False


class ProductPatch(BaseModel):
    target_price_minor: int | None = None
    percentile_threshold: int | None = None
    percentile_window_days: int | None = None
    status: Literal["active", "archived", "bought"] | None = None
    muted_until: datetime | None = None
    notes: str | None = None
    alert_kinds_disabled: list[str] | None = None
    track_used: bool | None = None


class ProductOut(BaseModel):
    id: int
    asin: str
    title: str
    image_url: str | None
    brand: str | None
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
    track_used: bool
    last_scrape_attempt_at: UtcDateTime | None = None
    last_scrape_error: str | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime
    stats: BookStatsOut

    @classmethod
    def from_product(
        cls,
        product: models.Product,
        stats: BookStats,
        reco: RecommendationConfig | None = None,
    ) -> ProductOut:
        return cls(
            id=product.id or 0,
            asin=product.asin,
            title=product.title,
            # Same-origin proxy URL so browser shields don't block third-party
            # CDN requests. Bytes are served by the image-proxy endpoint below.
            image_url=(f"/api/products/{product.id}/image" if product.image_url else None),
            brand=product.brand,
            region=product.region,
            currency=product.currency,
            target_price_minor=product.target_price_minor,
            percentile_threshold=product.percentile_threshold,
            percentile_window_days=product.percentile_window_days,
            status=product.status,
            bought_price_minor=product.bought_price_minor,
            notes=product.notes,
            alert_kinds_disabled=list(product.alert_kinds_disabled or []),
            muted_until=product.muted_until,
            track_used=product.track_used,
            last_scrape_attempt_at=product.last_scrape_attempt_at,
            last_scrape_error=product.last_scrape_error,
            created_at=product.created_at,
            updated_at=product.updated_at,
            stats=BookStatsOut.from_dataclass(stats, book=product, reco=reco),
        )


class ProductObservationOut(BaseModel):
    # Mirror of PriceObservationOut: `observed_at` is the offer's FIRST
    # sighting (never changes), `last_seen`/`url` are the row's own
    # `last_seen_at`/`url`, updated in place on every re-confirming scrape.
    # See that class for the full rationale.
    id: int
    product_id: int
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
    def from_obs(cls, obs: models.ProductObservation) -> ProductObservationOut:
        return cls(
            id=obs.id or 0,
            product_id=obs.product_id,
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


class ProductObservationsPage(BaseModel):
    items: list[ProductObservationOut]
    next_before: str | None


# --- Handlers ---------------------------------------------------------------


@router.get("", response_model=list[ProductOut])
def list_products(
    request: Request,
    session: SessionDep,
    cfg: ConfigDep,
    include_archived: bool = False,
) -> list[ProductOut]:
    stmt = select(models.Product)
    if not include_archived:
        stmt = stmt.where(models.Product.status != ItemStatus.ARCHIVED)
    products = session.exec(stmt).all()
    medians = request.app.state.medians_cache.get_or_compute(
        session,
        schema=_PRODUCT_SCHEMA,
        min_observations=cfg.recommendation.min_global_median_observations,
    )
    ids = [p.id or 0 for p in products]
    window_days = {
        iid: _effective_window_days(p, cfg) for iid, p in zip(ids, products, strict=True)
    }
    stats_by_id = compute_stats_for_items(
        ids,
        session,
        schema=_PRODUCT_SCHEMA,
        cfg=cfg.recommendation,
        window_days=window_days,
        medians=medians,
    )
    return [
        ProductOut.from_product(p, stats_by_id[p.id or 0], reco=cfg.recommendation)
        for p in products
    ]


@router.post("", response_model=ProductOut, status_code=status.HTTP_201_CREATED)
def create_product(
    payload: ProductCreate,
    session: SessionDep,
    cfg: ConfigDep,
    background_tasks: BackgroundTasks,
) -> ProductOut:
    try:
        asin = to_asin(payload.asin_or_url)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    existing = session.exec(
        select(models.Product).where(models.Product.asin == asin)
    ).first()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": f"product with ASIN {asin} already exists",
                "product_id": existing.id,
                "asin": asin,
            },
        )

    now = datetime.now(UTC)
    product = models.Product(
        asin=asin,
        title=payload.title,
        image_url=payload.image_url,
        brand=payload.brand,
        target_price_minor=payload.target_price_minor,
        percentile_threshold=payload.percentile_threshold,
        percentile_window_days=payload.percentile_window_days,
        notes=payload.notes,
        track_used=payload.track_used,
        created_at=now,
        updated_at=now,
    )
    session.add(product)
    session.commit()
    session.refresh(product)
    assert product.id is not None

    # Fire-and-forget Keepa backfill — same pattern as books.
    engine = session.get_bind()
    background_tasks.add_task(
        _keepa_backfill_blocking,
        product.id,
        product.asin,
        lambda: Session(engine),
    )

    stats = _stats_for(product, session, cfg)
    return ProductOut.from_product(product, stats, reco=cfg.recommendation)


@router.get("/{product_id}", response_model=ProductOut)
def get_product(
    product_id: int,
    session: SessionDep,
    cfg: ConfigDep,
) -> ProductOut:
    product = session.get(models.Product, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="product not found")
    stats = _stats_for(product, session, cfg)
    return ProductOut.from_product(product, stats, reco=cfg.recommendation)


@router.patch("/{product_id}", response_model=ProductOut)
def patch_product(
    product_id: int,
    payload: ProductPatch,
    session: SessionDep,
    cfg: ConfigDep,
) -> ProductOut:
    product = session.get(models.Product, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="product not found")

    data = payload.model_dump(exclude_unset=True)
    for field_name, value in data.items():
        setattr(product, field_name, value)
    if data:
        product.updated_at = datetime.now(UTC)
        session.add(product)
        session.commit()
        session.refresh(product)

    stats = _stats_for(product, session, cfg)
    return ProductOut.from_product(product, stats, reco=cfg.recommendation)


@router.delete("/{product_id}", response_model=ProductOut)
def delete_product(
    product_id: int,
    session: SessionDep,
    cfg: ConfigDep,
    hard: bool = False,
) -> ProductOut | dict[str, object]:
    product = session.get(models.Product, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="product not found")

    if hard:
        stats = _stats_for(product, session, cfg)
        out = ProductOut.from_product(product, stats, reco=cfg.recommendation)
        # Schema-enforced cascade via migration 0014's CASCADE FKs.
        session.delete(product)
        session.commit()
        return out

    product.status = ItemStatus.ARCHIVED
    product.updated_at = datetime.now(UTC)
    session.add(product)
    session.commit()
    session.refresh(product)
    stats = _stats_for(product, session, cfg)
    return ProductOut.from_product(product, stats, reco=cfg.recommendation)


@router.get("/{product_id}/observations", response_model=ProductObservationsPage)
def list_product_observations(
    product_id: int,
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    before: datetime | None = None,
    source: str | None = None,
) -> ProductObservationsPage:
    """Paginated price history for a product (newest-first). Mirror of the
    book observations endpoint — same cursor shape."""
    if session.get(models.Product, product_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="product not found")

    stmt = select(models.ProductObservation).where(
        models.ProductObservation.product_id == product_id
    )
    if source is not None:
        stmt = stmt.where(models.ProductObservation.source == source)
    if before is not None:
        stmt = stmt.where(models.ProductObservation.observed_at < before)
    stmt = stmt.order_by(models.ProductObservation.observed_at.desc()).limit(limit)  # type: ignore[attr-defined]

    rows = session.exec(stmt).all()
    items = [ProductObservationOut.from_obs(r) for r in rows]
    next_before = (
        to_z_iso(rows[-1].observed_at) if len(rows) == limit and rows else None
    )
    return ProductObservationsPage(items=items, next_before=next_before)


@router.post("/{product_id}/refetch", response_model=BookRefetchResult)
async def refetch_product(
    product_id: int,
    session: SessionDep,
    cfg: ConfigDep,
    scheduler: SchedulerDep,
) -> BookRefetchResult:
    """Trigger an immediate scrape across every PRODUCT-serving source.

    Shares the books-side `_run_refetch` helper — same triggered/skipped
    shape, filtered to sources whose `item_kinds` includes PRODUCT so a
    book-only source (wob, bookfinder) is correctly marked
    `kind_unsupported` rather than firing a no-op cycle.
    """
    if session.get(models.Product, product_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="product not found"
        )
    return await _run_refetch(cfg, scheduler, kind=ItemKind.PRODUCT)


@router.get("/{product_id}/stats", response_model=BookStatsOut)
def get_product_stats(
    product_id: int,
    session: SessionDep,
    cfg: ConfigDep,
) -> BookStatsOut:
    """Return the full stats bundle for a product (zero-obs case included)."""
    product = session.get(models.Product, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="product not found")
    return BookStatsOut.from_dataclass(
        _stats_for(product, session, cfg),
        book=product,
        reco=cfg.recommendation,
    )


def _keepa_backfill_blocking(
    product_id: int,
    asin: str,
    session_factory,
) -> int:
    """Thin wrapper around `keepa_backfill.backfill_blocking` with the product
    schema bound in. Kept as a named symbol so existing `background_tasks.
    add_task(...)` call sites and tests that monkeypatch this don't break.
    """
    return keepa_backfill.backfill_blocking(
        product_id, asin, session_factory, schema=keepa_backfill.PRODUCT_SCHEMA,
    )


@router.post("/{product_id}/keepa-backfill")
async def trigger_keepa_backfill(
    product_id: int,
    session: SessionDep,
) -> dict:
    """Trigger a one-shot Keepa backfill for this product. Idempotent —
    returns inserted=0 if a previous backfill already populated this product."""
    product = session.get(models.Product, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="product not found")
    engine = session.get_bind()
    inserted = await asyncio.to_thread(
        _keepa_backfill_blocking,
        product_id,
        product.asin,
        lambda: Session(engine),
    )
    return {"inserted": inserted, "product_id": product_id}


@router.get("/{product_id}/keepa-chart.png")
async def get_keepa_chart(product_id: int, session: SessionDep) -> Response:
    """Proxy the Keepa price-history PNG with a 24h server-side cache.
    Same flow as the books endpoint, ASIN-keyed."""
    product = session.get(models.Product, product_id)
    if product is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="product not found")
    png = await asyncio.to_thread(keepa.fetch_chart_png_for_asin, product.asin)
    if png is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="keepa has no chart for this ASIN",
        )
    return Response(
        content=png,
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@router.get("/{product_id}/image", include_in_schema=False)
async def get_product_image(
    product_id: int,
    session: SessionDep,
    http: HttpDep,
) -> Response:
    """Same-origin proxy for the upstream Amazon product image. Mirrors the
    cover-image endpoint for books but keyed on product_id rather than
    ISBN-13. The image bytes are cached on disk under
    `data/product-images/<asin>` and re-fetched lazily on cache miss.

    `product.image_url` is set during `create_product` from the user-paste
    flow OR the asin-lookup metadata. We require https:// + an Amazon CDN
    host to prevent SSRF — a freely-pasted internal URL ("http://169.254.169.254/...")
    would otherwise let the server fetch internal infrastructure on the user's
    behalf. Single-user NAS posture mitigates the impact, but the input is
    user-controllable so the validation belongs here regardless.
    """
    product = session.get(models.Product, product_id)
    if product is None or not product.image_url:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    if not _is_safe_image_url(product.image_url):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)
    path = _product_image_cache_path(product.asin)
    if not path.exists():
        result = await fetch_and_cache_url(path, product.image_url, http=http)
        if result is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    data = path.read_bytes()
    return Response(
        content=data,
        media_type=sniff_mime(data),
        headers={"Cache-Control": "public, max-age=86400"},
    )


def _product_image_cache_path(asin: str) -> Path:
    """Disk path for the cached product image. Lives under
    `data/product-images/<asin>` parallel to `data/covers/<isbn13>`.

    No side effects — `fetch_and_cache_url` creates the parent directory at
    write time, so this stays a pure path-builder.
    """
    return PRODUCT_IMAGE_CACHE_DIR / asin


_ALLOWED_IMAGE_HOSTS: tuple[str, ...] = (
    "m.media-amazon.com",
    "images-na.ssl-images-amazon.com",
    "images-eu.ssl-images-amazon.com",
    "images-amazon.com",
    "ssl-images-amazon.com",
)


def _is_safe_image_url(url: str) -> bool:
    """SSRF guard for the user-supplied `product.image_url`. Accepts https://
    URLs whose host is on Amazon's CDN allowlist. Anything else (cloud
    metadata IPs, internal services, http://, file:// — though httpx doesn't
    support file:// natively, defence in depth) is rejected."""
    from urllib.parse import urlparse

    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    return any(host == h or host.endswith("." + h) for h in _ALLOWED_IMAGE_HOSTS)
