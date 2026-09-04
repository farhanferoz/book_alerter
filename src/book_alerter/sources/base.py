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
    # T1.5 diagnostic capture: the raw delivery/shipping text a source read
    # `shipping_minor` from (Amazon: the dp delivery block or an AOD row's
    # `.aod-delivery-promise` text; Bookfinder: the card's shipping-label
    # text). Persisted inside `raw` via the scheduler's existing
    # `c.model_dump()` — no schema change. `None` when a source doesn't
    # capture it or found no delivery text at all.
    delivery_text: str | None = None
    currency: str
    url: str
    # T4.1: title/image scraped incidentally off the SAME page a source
    # already rendered to find a price. Only Amazon's `parse_dp` populates
    # these (the buy-box page renders `#productTitle`/a cover image
    # unconditionally; an AOD/offer-listing row never does) — `None` for
    # every other candidate. `scheduler._persist` uses whichever candidate
    # carries a non-None `item_title` to resolve a PENDING product's
    # metadata without waiting on the `metadata_refresh` job. Not
    # persisted as observation columns — read once by `_persist`, then
    # dropped like every other candidate field that isn't part of the
    # offer itself.
    item_title: str | None = None
    item_image_url: str | None = None


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

    async def prepare(self) -> None:
        """Called once by the scheduler before it iterates this source's
        items for a run. Default no-op — httpx-based sources (WoB) have
        nothing to set up. Browser-backed sources override this to open a
        `BrowserSession` and stash the resulting context for `fetch()`."""
        return None

    async def cleanup(self) -> None:
        """Called once by the scheduler in a `finally` after a run,
        whether or not it raised. Default no-op; browser-backed sources
        override this to close their `BrowserSession`."""
        return None
