"""Schema-parameterised Keepa backfill helper.

Books and products both backfill historical price observations from the
Keepa chart PNG. The persistence and idempotency logic is identical; only
the model class, FK column name, and identifier-to-PNG/URL functions differ
per kind. `backfill_blocking` is the single impl; `BOOK_SCHEMA` and
`PRODUCT_SCHEMA` are the two bundles that wire it up for each kind.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from sqlmodel import Session, select

from book_alerter import keepa, keepa_chart
from book_alerter.db import models
from book_alerter.sources.normalizers import amazon_uk_dp_url, amazon_uk_product_dp_url


@dataclass(frozen=True)
class KeepaBackfillSchema:
    """Per-kind plumbing for `backfill_blocking`.

    `fetch_png(identifier)` returns the chart PNG bytes (or None when Keepa
    has no data for the identifier). `dp_url_for(identifier)` returns the
    Amazon UK dp URL stored on each PriceObservation/ProductObservation row.
    """

    item_model: type[Any]              # models.Book | models.Product
    observation_model: type[Any]       # models.PriceObservation | models.ProductObservation
    fk_attr: str                       # "book_id" | "product_id"
    fetch_png: Callable[[str], bytes | None]
    dp_url_for: Callable[[str], str]


BOOK_SCHEMA = KeepaBackfillSchema(
    item_model=models.Book,
    observation_model=models.PriceObservation,
    fk_attr="book_id",
    fetch_png=keepa.fetch_chart_png,
    dp_url_for=amazon_uk_dp_url,
)


PRODUCT_SCHEMA = KeepaBackfillSchema(
    item_model=models.Product,
    observation_model=models.ProductObservation,
    fk_attr="product_id",
    fetch_png=keepa.fetch_chart_png_for_asin,
    dp_url_for=amazon_uk_product_dp_url,
)


def backfill_blocking(
    item_id: int,
    identifier: str,
    session_factory: Callable[[], Session],
    *,
    schema: KeepaBackfillSchema,
) -> int:
    """Fetch Keepa PNG, extract observations, persist. Returns rows inserted.

    Idempotent: skips if any source='keepa' row already exists for the item.
    Splits sessions around the OCR call so the DB connection isn't pinned
    across the ~3-5s extraction.

    Shipping is left NULL — Keepa's chart PNG only renders item prices and
    we will not fabricate a number. Downstream comparisons against current
    scraped totals (which DO include shipping when the page advertises it)
    are therefore unfair on the historical side; the UI surfaces NULL as
    em-dash so the gap is visible rather than papered over.
    """
    fk_col = getattr(schema.observation_model, schema.fk_attr)

    with session_factory() as session:
        # If the item was hard-deleted between the BackgroundTasks dispatch
        # and now, bail — otherwise we'd insert observation rows whose FK
        # points at nothing (the cascade-delete in delete_book/delete_product
        # has no way to wait for in-flight backfills).
        if session.get(schema.item_model, item_id) is None:
            return 0
        existing = session.exec(
            select(schema.observation_model)
            .where(
                fk_col == item_id,
                schema.observation_model.source == "keepa",
            )
            .limit(1)
        ).first()
        if existing is not None:
            return 0

    png = schema.fetch_png(identifier)
    if png is None:
        return 0

    extractions = keepa_chart.extract_observations(png)
    if not extractions:
        return 0

    url = schema.dp_url_for(identifier)

    inserted = 0
    with session_factory() as session:
        for ext in extractions:
            seller, condition = keepa_chart.SERIES_TO_SELLER_CONDITION[ext.series]
            session.add(
                schema.observation_model(
                    **{schema.fk_attr: item_id},
                    source="keepa",
                    seller=seller,
                    condition=condition,
                    price_minor=ext.price_minor,
                    currency="GBP",
                    shipping_minor=None,
                    total_minor=ext.price_minor,
                    url=url,
                    observed_at=keepa_chart.observed_at_to_datetime(ext.observed_at),
                    raw={"series": ext.series, "from": "keepa_chart_png"},
                )
            )
            inserted += 1
        session.commit()
    return inserted
