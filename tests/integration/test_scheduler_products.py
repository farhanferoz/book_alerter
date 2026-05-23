"""End-to-end scheduler tests for the product iteration path.

Mirrors the books-side `test_scheduler_runs_wob_end_to_end` shape but
with a stub product source so the test runs without Playwright. Asserts:

- Configuring `SourceConfig.item_kinds = [PRODUCT]` makes the scheduler
  iterate products (not books) for that source.
- ProductObservation rows land (not PriceObservation).
- The product alert pipeline receives the affected product ids.
- The SourceRun row records the per-source totals.
- A source whose `Source.item_kinds` doesn't intersect the config emits a
  no-op SourceRun.
"""

from __future__ import annotations

import pytest
from sqlmodel import Session, select

from book_alerter.config import Config, SourceConfig
from book_alerter.db import models
from book_alerter.enums import Condition, ItemKind
from book_alerter.scheduler import Scheduler
from book_alerter.sources.base import ObservationCandidate, Source


class _StubProductSource(Source):
    """Returns canned observations without touching Amazon."""

    name = "amazon_uk_product"
    item_kinds = frozenset({ItemKind.PRODUCT})

    def __init__(self, *, region: str = "UK", name: str = "amazon_uk_product") -> None:
        self.region = region
        self.name = name

    async def fetch(self, item) -> list[ObservationCandidate]:
        return [
            ObservationCandidate(
                seller="Amazon",
                condition=Condition.NEW,
                price_minor=1499,
                shipping_minor=0,
                currency="GBP",
                url=f"https://www.amazon.co.uk/dp/{item.asin}",
            ),
        ]


@pytest.mark.asyncio
async def test_scheduler_runs_product_source_end_to_end(
    sqlite_engine, make_product,
) -> None:
    with Session(sqlite_engine) as s:
        product = make_product(s, asin="B07STUB0001")
        seeded_id = product.id

    cfg = Config(
        sources={
            "amazon_uk_product": SourceConfig(
                enabled=True,
                region="UK",
                per_book_delay_seconds=(0, 0),
                concurrency=1,
                item_kinds=[ItemKind.PRODUCT],
            ),
        },
    )

    alert_calls_by_kind: dict[ItemKind, list[list[int]]] = {
        ItemKind.BOOK: [],
        ItemKind.PRODUCT: [],
    }

    async def book_pipeline(ids: list[int]) -> None:
        alert_calls_by_kind[ItemKind.BOOK].append(list(ids))

    async def product_pipeline(ids: list[int]) -> None:
        alert_calls_by_kind[ItemKind.PRODUCT].append(list(ids))

    scheduler = Scheduler(
        config=cfg,
        sources={"amazon_uk_product": _StubProductSource()},
        session_factory=lambda: Session(sqlite_engine),
        alert_pipelines={
            ItemKind.BOOK: book_pipeline,
            ItemKind.PRODUCT: product_pipeline,
        },
    )

    run_id = await scheduler.trigger_now("amazon_uk_product")
    assert run_id > 0

    # ProductObservation rows landed for the product, NOT PriceObservation.
    with Session(sqlite_engine) as s:
        obs = s.exec(
            select(models.ProductObservation).where(
                models.ProductObservation.product_id == seeded_id,
            ),
        ).all()
        assert len(obs) == 1
        assert obs[0].price_minor == 1499
        assert obs[0].currency == "GBP"
        assert obs[0].source == "amazon_uk_product"

        # And no book-side observations leaked.
        book_obs = s.exec(select(models.PriceObservation)).all()
        assert len(book_obs) == 0

    # Scrape health updated on the product row.
    with Session(sqlite_engine) as s:
        product = s.get(models.Product, seeded_id)
        assert product.last_scrape_attempt_at is not None
        assert product.last_scrape_error is None

    # SourceRun records the run.
    with Session(sqlite_engine) as s:
        run = s.exec(select(models.SourceRun).where(models.SourceRun.id == run_id)).one()
        assert run.status == "success"
        assert run.books_attempted == 1
        assert run.books_succeeded == 1

    # Product pipeline received the id; book pipeline received nothing.
    assert alert_calls_by_kind[ItemKind.PRODUCT] == [[seeded_id]]
    assert alert_calls_by_kind[ItemKind.BOOK] == []


@pytest.mark.asyncio
async def test_scheduler_skips_kinds_outside_source_capability(
    sqlite_engine, make_product,
) -> None:
    """A SourceConfig that requests `[BOOK]` against a PRODUCT-only source
    must run a no-op and not crash. Audit row still lands so the operator
    can see the misconfiguration."""
    with Session(sqlite_engine) as s:
        make_product(s, asin="B07STUB0002")

    cfg = Config(
        sources={
            "amazon_uk_product": SourceConfig(
                enabled=True,
                region="UK",
                per_book_delay_seconds=(0, 0),
                concurrency=1,
                item_kinds=[ItemKind.BOOK],  # intersection with src.item_kinds is empty
            ),
        },
    )

    async def _np(ids: list[int]) -> None:
        pass

    scheduler = Scheduler(
        config=cfg,
        sources={"amazon_uk_product": _StubProductSource()},
        session_factory=lambda: Session(sqlite_engine),
        alert_pipelines={
            ItemKind.BOOK: _np,
            ItemKind.PRODUCT: _np,
        },
    )

    run_id = await scheduler.trigger_now("amazon_uk_product")
    assert run_id > 0

    # No observation rows at all.
    with Session(sqlite_engine) as s:
        assert s.exec(select(models.ProductObservation)).all() == []
        assert s.exec(select(models.PriceObservation)).all() == []
        run = s.exec(select(models.SourceRun).where(models.SourceRun.id == run_id)).one()
        assert run.status == "success"
        assert run.books_attempted == 0


@pytest.mark.asyncio
async def test_scheduler_isolates_per_item_unexpected_exception(
    sqlite_engine, make_book, make_product,
) -> None:
    """Regression test for per-item exception isolation.

    If a source raises a non-SourceError (e.g. RuntimeError, a Playwright
    assert) on one item, the iteration must charge that single item via
    `_record_item_failure` and continue scraping the rest. Before the fix
    `_one` only caught `(TimeoutError, SourceError)` and a stray exception
    propagated through `asyncio.gather` and aborted the entire kind.

    Setup: a source that serves both kinds; the book succeeds, the product
    raises RuntimeError. Asserts that:
    - book observation lands
    - product is charged with `last_scrape_error`
    - SourceRun is `partial` (1 of 2 attempted succeeded)
    - book alert pipeline STILL fires for the seeded book id
    """
    with Session(sqlite_engine) as s:
        book = make_book(s, isbn13="9780000000123")
        product = make_product(s, asin="B07KFAIL001")
        seeded_book_id = book.id
        seeded_product_id = product.id

    class _MultiKindCrasher(Source):
        name = "multi"
        item_kinds = frozenset({ItemKind.BOOK, ItemKind.PRODUCT})

        async def fetch(self, item) -> list[ObservationCandidate]:
            if isinstance(item, models.Product):
                raise RuntimeError("unexpected boom on the product path")
            return [
                ObservationCandidate(
                    seller="Amazon",
                    condition=Condition.NEW,
                    price_minor=799,
                    shipping_minor=0,
                    currency="GBP",
                    url=f"https://www.amazon.co.uk/dp/{item.isbn13}",
                ),
            ]

    cfg = Config(
        sources={
            "multi": SourceConfig(
                enabled=True, region="UK",
                per_book_delay_seconds=(0, 0), concurrency=1,
                item_kinds=[ItemKind.BOOK, ItemKind.PRODUCT],
            ),
        },
    )

    alert_calls: dict[ItemKind, list[list[int]]] = {
        ItemKind.BOOK: [],
        ItemKind.PRODUCT: [],
    }

    async def book_pipeline(ids: list[int]) -> None:
        alert_calls[ItemKind.BOOK].append(list(ids))

    async def product_pipeline(ids: list[int]) -> None:
        alert_calls[ItemKind.PRODUCT].append(list(ids))

    scheduler = Scheduler(
        config=cfg,
        sources={"multi": _MultiKindCrasher()},
        session_factory=lambda: Session(sqlite_engine),
        alert_pipelines={
            ItemKind.BOOK: book_pipeline,
            ItemKind.PRODUCT: product_pipeline,
        },
    )

    run_id = await scheduler.trigger_now("multi")
    assert run_id > 0

    with Session(sqlite_engine) as s:
        # Book observation landed.
        book_obs = s.exec(select(models.PriceObservation)).all()
        assert len(book_obs) == 1, (
            "book observation must land — sibling kind item failure shouldn't abort"
        )
        # No product observations — the item raised before _persist.
        product_obs = s.exec(select(models.ProductObservation)).all()
        assert len(product_obs) == 0
        # Product row charged with last_scrape_error.
        product_after = s.get(models.Product, seeded_product_id)
        assert product_after.last_scrape_error is not None
        assert "unexpected boom" in product_after.last_scrape_error
        # SourceRun partial — 1/2 succeeded.
        run = s.exec(select(models.SourceRun).where(models.SourceRun.id == run_id)).one()
        assert run.status == "partial"
        assert run.books_attempted == 2
        assert run.books_succeeded == 1

    # The fix: book alert pipeline STILL received its affected id, even
    # though the product item crashed.
    assert alert_calls[ItemKind.BOOK] == [[seeded_book_id]]
    assert alert_calls[ItemKind.PRODUCT] == []


@pytest.mark.asyncio
async def test_scheduler_marks_product_failure_on_source_error(
    sqlite_engine, make_product,
) -> None:
    """A product source raising SourceError marks last_scrape_error on the
    product row, identical to the books-side behaviour."""
    from book_alerter.sources.base import SourceError

    with Session(sqlite_engine) as s:
        product = make_product(s, asin="B07STUB0003")
        seeded_id = product.id

    class _FailingSource(Source):
        name = "amazon_uk_product"
        item_kinds = frozenset({ItemKind.PRODUCT})

        async def fetch(self, item) -> list[ObservationCandidate]:
            raise SourceError(self.name, "synthetic failure")

    cfg = Config(
        sources={
            "amazon_uk_product": SourceConfig(
                enabled=True, region="UK",
                per_book_delay_seconds=(0, 0), concurrency=1,
                item_kinds=[ItemKind.PRODUCT],
            ),
        },
    )

    async def _np(ids: list[int]) -> None:
        pass

    scheduler = Scheduler(
        config=cfg,
        sources={"amazon_uk_product": _FailingSource()},
        session_factory=lambda: Session(sqlite_engine),
        alert_pipelines={
            ItemKind.BOOK: _np,
            ItemKind.PRODUCT: _np,
        },
    )

    await scheduler.trigger_now("amazon_uk_product")

    with Session(sqlite_engine) as s:
        product = s.get(models.Product, seeded_id)
        assert product.last_scrape_attempt_at is not None
        assert product.last_scrape_error is not None
        assert "synthetic failure" in product.last_scrape_error
