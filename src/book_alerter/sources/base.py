from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import ClassVar, Protocol, runtime_checkable

from pydantic import BaseModel

from book_alerter.db.models import Condition
from book_alerter.enums import ItemKind

__all__ = [
    "Condition",
    "ItemKind",
    "ObservationCandidate",
    "Source",
    "SourceError",
    "TrackedItem",
]


@runtime_checkable
class TrackedItem(Protocol):
    """The minimum surface a Source needs to fetch observations.

    Both `Book` and `Product` satisfy this protocol — they expose the same
    fields the scheduler and sources read (`id`, `region`, `currency`,
    `last_scrape_*`) plus a kind-specific `identifier` property (ISBN-13
    for books, ASIN for products).

    `track_used` defaults False — book sources always track every condition
    Amazon publishes, so the attribute is irrelevant for them and they can
    simply not implement it. Sources that branch on this attribute use
    `getattr(item, "track_used", True)` so books fall through to their
    historical "all conditions" behaviour.
    """

    id: int | None
    region: str
    currency: str
    last_scrape_attempt_at: datetime | None
    last_scrape_error: str | None

    @property
    def identifier(self) -> str: ...


class ObservationCandidate(BaseModel):
    seller: str | None = None
    condition: Condition
    price_minor: int
    shipping_minor: int | None = None
    currency: str
    url: str


class SourceError(Exception):
    def __init__(self, source_name: str, message: str) -> None:
        super().__init__(f"[{source_name}] {message}")
        self.source_name = source_name
        self.message = message


class Source(ABC):
    name: str
    # Which TrackedItem kinds this Source can handle. Scheduler skips sources
    # whose `item_kinds` doesn't intersect the per-source config `item_kinds`.
    # Default to BOOK for back-compat with the pre-products book sources;
    # `AmazonProductSource` overrides to {PRODUCT}.
    item_kinds: ClassVar[frozenset[ItemKind]] = frozenset({ItemKind.BOOK})

    @abstractmethod
    async def fetch(self, item: TrackedItem) -> list[ObservationCandidate]: ...

    async def healthcheck(self) -> bool:
        return True
