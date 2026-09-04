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
from datetime import UTC, datetime

from sqlmodel import Session, select

from book_alerter import keepa, keepa_chart
from book_alerter.db import models
from book_alerter.enums import ItemStatus
from book_alerter.logging_setup import get_logger
from book_alerter.sources.normalizers import amazon_uk_dp_url, amazon_uk_product_dp_url

log = get_logger(__name__)


@dataclass(frozen=True)
class KeepaBackfillSchema:
    """Per-kind plumbing for `backfill_blocking`.

    `fetch_png(identifier)` returns the chart PNG bytes (or None when Keepa
    has no data for the identifier). `dp_url_for(identifier)` returns the
    Amazon UK dp URL stored on each PriceObservation/ProductObservation row.
    """

    item_model: type[models.Book] | type[models.Product]
    observation_model: type[models.PriceObservation] | type[models.ProductObservation]
    fk_attr: str  # "book_id" | "product_id"
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
    refresh: bool = False,
) -> int:
    """Fetch Keepa PNG, extract observations, persist. Returns rows inserted.

    Idempotent in both modes, but by different means.

    Default (`refresh=False`): skips entirely if ANY source='keepa' row
    already exists for the item. That is the first-backfill guard.

    `refresh=True` (T6.3's weekly job): skips that guard and instead drops
    individual extractions whose (seller, condition, observed_at) already
    exists, so a re-run adds only genuinely new chart points. **The plan
    assumed this per-date dedup already existed; it did not.** Before this,
    the coarse guard was the only protection, so a periodic re-run would
    either do nothing at all (guard intact) or duplicate the entire history
    on every run (guard removed) -- which is why the refresh mode had to
    bring the dedup with it.
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
        if existing is not None and not refresh:
            return 0

    png = schema.fetch_png(identifier)
    if png is None:
        return 0

    extractions = keepa_chart.extract_observations(png)
    if not extractions:
        return 0

    # Defence in depth against `_DateCalib.__call__`'s clamp: even if a
    # future change to the chart calibration reintroduces a rounding
    # artefact that pushes a date past today, no future-dated row reaches
    # the DB. Dropped rows are logged (not silently discarded) so a
    # regression here is visible without a DB invariant check catching it.
    today = datetime.now(UTC).date()
    future = [ext for ext in extractions if ext.observed_at > today]
    if future:
        log.warning(
            "keepa_backfill.future_dated_dropped",
            item_id=item_id,
            identifier=identifier,
            count=len(future),
        )
        extractions = [ext for ext in extractions if ext.observed_at <= today]
    if not extractions:
        return 0

    url = schema.dp_url_for(identifier)

    inserted = 0
    with session_factory() as session:
        seen: set[tuple[str | None, str, object]] = set()
        if refresh:
            # One query, not one per extraction: a chart carries ~400 points
            # and this job runs over every active item.
            #
            # Keyed on the DATE, not the datetime. A Keepa chart carries one
            # point per day, and the two sides are not directly comparable
            # anyway: SQLite hands back a naive datetime while
            # `observed_at_to_datetime` produces a UTC-aware one, so a tuple
            # comparison silently never matches and every run re-inserts the
            # whole history. Caught by the dedup test.
            seen = {
                (row.seller, str(row.condition), row.observed_at.date())
                for row in session.exec(
                    select(schema.observation_model).where(
                        fk_col == item_id,
                        schema.observation_model.source == "keepa",
                    )
                ).all()
            }
        for ext in extractions:
            seller, condition = keepa_chart.SERIES_TO_SELLER_CONDITION[ext.series]
            # A Keepa row reconstructs a single historical price point from the
            # chart; it is never re-scraped, so the offer's first and last
            # sighting are the same instant. Backdating `last_seen_at` rather
            # than stamping "now" is load-bearing for the history-summary view,
            # whose `last_polled_at` is MAX(last_seen_at) over every row of the
            # table, keepa included: stamping now would make a book with only
            # backfilled history claim it had just been polled.
            when = keepa_chart.observed_at_to_datetime(ext.observed_at)
            if refresh and (seller, str(condition), when.date()) in seen:
                continue
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
                    observed_at=when,
                    last_seen_at=when,
                    raw={"series": ext.series, "from": "keepa_chart_png"},
                )
            )
            inserted += 1
        session.commit()
    return inserted


def keepa_refresh_tick(
    session_factory: Callable[[], Session],
    *,
    enabled: bool,
) -> int:
    """T6.3: re-run the Keepa backfill for every ACTIVE item, adding only
    chart points we don't already have. Returns rows inserted.

    Ships **default-off** (`keepa.refresh_enabled`), and deliberately so:
    whether Keepa's PNG endpoint tolerates one request per tracked item per
    week is **not something this codebase has measured**, and it is not a
    fact worth guessing about someone else's service. Turning it on is an
    explicit choice by whoever is willing to find out. The plan says the same.

    Never raises: a failing refresh must not take the scheduler down, and a
    single item's failure must not abandon the rest. Mirrors `janitor_tick`.
    """
    if not enabled:
        log.info("keepa_refresh.disabled")
        return 0

    total = 0
    for schema, id_attr in ((BOOK_SCHEMA, "isbn13"), (PRODUCT_SCHEMA, "asin")):
        try:
            with session_factory() as session:
                items = [
                    (row.id, getattr(row, id_attr))
                    for row in session.exec(
                        select(schema.item_model).where(
                            schema.item_model.status == ItemStatus.ACTIVE
                        )
                    ).all()
                ]
        except Exception as e:
            log.error("keepa_refresh.query_failed", error=str(e))
            continue
        for item_id, identifier in items:
            if item_id is None:
                continue
            try:
                total += backfill_blocking(
                    item_id, identifier, session_factory,
                    schema=schema, refresh=True,
                )
            except Exception as e:
                # One bad chart (Keepa 404, OCR failure, a rate-limit page)
                # must not cost the remaining items their refresh.
                log.warning(
                    "keepa_refresh.item_failed",
                    identifier=identifier,
                    error=str(e),
                )
    log.info("keepa_refresh.finished", rows_inserted=total)
    return total
