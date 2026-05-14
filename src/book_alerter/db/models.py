"""SQLModel table definitions. Tables added in Phase 1."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from sqlalchemy import Column, JSON, String
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
