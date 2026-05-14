"""SQLModel table definitions. Tables added in Phase 1."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from sqlalchemy import Column, Index, JSON, String
from sqlmodel import Field, SQLModel


class Book(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    isbn13: str = Field(unique=True, index=True)
    title: str
    author: str
    cover_url: str | None = None
    format: Literal["paperback", "hardcover", "any"] = Field(default="any", sa_column=Column(String, nullable=False))
    region: str = "UK"
    currency: str = "GBP"
    target_price_minor: int | None = None
    percentile_threshold: int | None = None
    status: Literal["active", "archived", "bought"] = Field(default="active", sa_column=Column(String, nullable=False))
    bought_price_minor: int | None = None
    notes: str | None = None
    alert_kinds_disabled: list[str] = Field(default_factory=list, sa_column=Column(JSON))
    muted_until: datetime | None = None
    created_at: datetime
    updated_at: datetime


class PriceObservation(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    book_id: int = Field(foreign_key="book.id", index=True)
    source: str
    seller: str | None = None
    condition: Literal["new", "used_vg", "used_g", "used_acceptable", "unknown"] = Field(
        sa_column=Column(String, nullable=False)
    )
    price_minor: int
    currency: str
    shipping_minor: int | None = None
    total_minor: int
    url: str
    observed_at: datetime = Field(index=True)
    raw: dict = Field(default_factory=dict, sa_column=Column(JSON))
    is_duplicate_of: int | None = Field(default=None, foreign_key="priceobservation.id")

    __table_args__ = (
        Index("ix_obs_book_observed", "book_id", "observed_at"),
        Index("ix_obs_book_source_observed", "book_id", "source", "observed_at"),
    )
