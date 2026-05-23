"""SQLModel table definitions.

Books were introduced in Phase 1; the product stack was added 2026-05-23.
Books and products are deliberately separate tables — see
`docs/superpowers/plans/2026-05-23-products-implementation.md` for the
"separate parallel tables + polymorphic NotificationDelivery" decision.

Typing notes:
- All string-enum-typed fields use the StrEnums in `book_alerter.enums`.
  The SQL column stays `Column(String, nullable=False)` (not
  `Column(Enum(...))`) so the stored value is the enum's `.value` (e.g.
  `"new"`), not its `.name` (e.g. `"NEW"`). This keeps wire format identical
  to the original `Literal[...]` era and dodges the SQLAlchemy `Enum`
  default-stores-name pitfall.
- `Literal[...]` SQLModel fields would still need the same `sa_column`
  workaround because of SQLModel 0.0.22's `issubclass(Literal, Enum)`
  `TypeError`. StrEnum is a real Enum subclass so it does NOT need the
  workaround in principle — we keep `Column(String)` anyway for the wire-
  format reason above.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, CheckConstraint, Column, ForeignKey, Index, Integer, String
from sqlmodel import Field, SQLModel

from book_alerter.enums import (
    AlertKind,
    BookFormat,
    Condition,
    ItemStatus,
    NotificationDeliveryStatus,
    SourceRunStatus,
)

# Re-export Condition for back-compat with existing imports
# (`from book_alerter.db.models import Condition`).
__all__ = [
    "Alert",
    "AlertKind",
    "Book",
    "BookSignalState",
    "Condition",
    "NotificationDelivery",
    "PriceObservation",
    "Product",
    "ProductAlert",
    "ProductObservation",
    "ProductSignalState",
    "SourceRun",
]


# ===== Book stack =====


class Book(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    isbn13: str = Field(unique=True, index=True)
    title: str
    author: str
    cover_url: str | None = None
    format: BookFormat = Field(default=BookFormat.ANY, sa_column=Column(String, nullable=False))
    region: str = "UK"
    currency: str = "GBP"
    target_price_minor: int | None = None
    percentile_threshold: int | None = None
    percentile_window_days: int | None = None
    status: ItemStatus = Field(default=ItemStatus.ACTIVE, sa_column=Column(String, nullable=False))
    bought_price_minor: int | None = None
    notes: str | None = None
    alert_kinds_disabled: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    muted_until: datetime | None = None
    # Per-book scrape health. Last-write-wins across sources — the FE only
    # signals "broken now".
    last_scrape_attempt_at: datetime | None = None
    last_scrape_error: str | None = None
    created_at: datetime
    updated_at: datetime

    @property
    def identifier(self) -> str:
        """TrackedItemProtocol contract — the natural-key string for this
        item (ISBN-13 for books, ASIN for products)."""
        return self.isbn13


class PriceObservation(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    # ondelete=CASCADE so dropping a Book takes its observation history with
    # it. Enforced once PRAGMA foreign_keys=ON (set per-connection in
    # `db/session.py`); see migration 0013.
    book_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("book.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
    )
    source: str
    seller: str | None = None
    condition: Condition = Field(sa_column=Column(String, nullable=False))
    price_minor: int
    currency: str
    shipping_minor: int | None = None
    total_minor: int
    url: str
    observed_at: datetime = Field(index=True)
    raw: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    is_duplicate_of: int | None = Field(default=None, foreign_key="priceobservation.id")

    __table_args__ = (
        Index("ix_obs_book_observed", "book_id", "observed_at"),
        Index("ix_obs_book_source_observed", "book_id", "source", "observed_at"),
    )


class SourceRun(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    source: str
    started_at: datetime
    finished_at: datetime | None = None
    status: SourceRunStatus = Field(sa_column=Column(String, nullable=False))
    books_attempted: int = 0
    books_succeeded: int = 0
    error_message: str | None = None
    error_traceback: str | None = None


class Alert(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    book_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("book.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
    )
    kind: AlertKind = Field(sa_column=Column(String, nullable=False))
    price_minor: int
    currency: str
    source: str
    condition: str
    message: str
    fired_at: datetime = Field(index=True)
    dismissed_at: datetime | None = None
    delivered_via: list[str] = Field(default_factory=list, sa_column=Column(JSON))


class NotificationDelivery(SQLModel, table=True):
    """One row per (alert, channel) send attempt.

    Polymorphic over book and product alerts: exactly one of `alert_id` and
    `product_alert_id` is set per row, enforced by a CHECK constraint added
    in migration 0015. Code that reads delivery rows uses
    `row.alert_id or row.product_alert_id` and `row.kind` discrimination via
    the source side.
    """

    id: int | None = Field(default=None, primary_key=True)
    alert_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("alert.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
    )
    product_alert_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("productalert.id", ondelete="CASCADE"),
            nullable=True,
            index=True,
        ),
    )
    channel: str
    sent_at: datetime
    status: NotificationDeliveryStatus = Field(sa_column=Column(String, nullable=False))
    error_message: str | None = None

    __table_args__ = (
        # Must match the constraint name in migration 0015 exactly — the
        # downgrade looks it up by this string. CHECK enforces that exactly
        # one of the two FKs is set per row.
        CheckConstraint(
            "(alert_id IS NULL) <> (product_alert_id IS NULL)",
            name="ck_notificationdelivery_alert_xor_product",
        ),
    )


class BookSignalState(SQLModel, table=True):
    """Persists the last-evaluated signal + all-time-min per book so the alert
    pipeline can detect transitions without expensive recomputation."""
    book_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("book.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    last_signal: str | None = None
    last_all_time_min_total_minor: int | None = None
    last_evaluated_at: datetime | None = None


# ===== Product stack (added 2026-05-23) =====


class Product(SQLModel, table=True):
    """Non-book Amazon product, ASIN-keyed.

    Mirrors `Book` field-for-field where the semantics carry over, with two
    product-specific additions: `track_used` (per-product opt-in for used
    market scraping; default off because most non-book products only have
    new offers worth tracking) and `brand` (taking the role book.author
    plays in the dashboard subtitle line).
    """

    id: int | None = Field(default=None, primary_key=True)
    asin: str = Field(unique=True, index=True)
    title: str
    image_url: str | None = None
    brand: str | None = None
    region: str = "UK"
    currency: str = "GBP"
    target_price_minor: int | None = None
    percentile_threshold: int | None = None
    percentile_window_days: int | None = None
    status: ItemStatus = Field(default=ItemStatus.ACTIVE, sa_column=Column(String, nullable=False))
    bought_price_minor: int | None = None
    notes: str | None = None
    alert_kinds_disabled: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    muted_until: datetime | None = None
    # Per-product opt-in for tracking the used market. Default off because
    # typical non-book products (electronics, household, beauty) have no
    # meaningful used market on Amazon UK; collectibles / games / cameras
    # do, so the flag is per-row rather than a global config knob.
    track_used: bool = False
    # Per-product scrape health, same semantics as Book.
    last_scrape_attempt_at: datetime | None = None
    last_scrape_error: str | None = None
    created_at: datetime
    updated_at: datetime

    @property
    def identifier(self) -> str:
        """TrackedItemProtocol contract — the natural-key string for this
        item (ISBN-13 for books, ASIN for products)."""
        return self.asin


class ProductObservation(SQLModel, table=True):
    """One price observation for a product, from one source, at one time.

    Field-for-field mirror of `PriceObservation` with `product_id` swapped
    in for `book_id`. Kept as a separate table per the "separate parallel
    tables" decision so the existing book pipeline is untouched.
    """

    id: int | None = Field(default=None, primary_key=True)
    product_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("product.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
    )
    source: str
    seller: str | None = None
    condition: Condition = Field(sa_column=Column(String, nullable=False))
    price_minor: int
    currency: str
    shipping_minor: int | None = None
    total_minor: int
    url: str
    observed_at: datetime = Field(index=True)
    raw: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    is_duplicate_of: int | None = Field(default=None, foreign_key="productobservation.id")

    __table_args__ = (
        Index("ix_pobs_product_observed", "product_id", "observed_at"),
        Index("ix_pobs_product_source_observed", "product_id", "source", "observed_at"),
    )


class ProductAlert(SQLModel, table=True):
    """Mirror of `Alert` for products. Same `kind` taxonomy."""

    id: int | None = Field(default=None, primary_key=True)
    product_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("product.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
    )
    kind: AlertKind = Field(sa_column=Column(String, nullable=False))
    price_minor: int
    currency: str
    source: str
    condition: str
    message: str
    fired_at: datetime = Field(index=True)
    dismissed_at: datetime | None = None
    delivered_via: list[str] = Field(default_factory=list, sa_column=Column(JSON))


class ProductSignalState(SQLModel, table=True):
    """Persists the last-evaluated signal + all-time-min per product so the
    alert pipeline can detect transitions without expensive recomputation.
    Mirror of `BookSignalState`."""

    product_id: int = Field(
        sa_column=Column(
            Integer,
            ForeignKey("product.id", ondelete="CASCADE"),
            primary_key=True,
        ),
    )
    last_signal: str | None = None
    last_all_time_min_total_minor: int | None = None
    last_evaluated_at: datetime | None = None
