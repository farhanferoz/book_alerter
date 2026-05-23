"""Notifier ABC + shared result type + tracked-item / alert protocols.

Channel implementations return a `NotificationResult` that the dispatcher
persists as a NotificationDelivery row. The `AlertLike` / `ItemLike`
protocols mean a Notifier doesn't need to know whether it's handling a
book or a product — both stacks expose the same fields a notifier reads
(`alert.message`, `alert.kind`, `item.title`).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal, NotRequired, Protocol, TypedDict, runtime_checkable


class NotificationResult(TypedDict):
    status: Literal["sent", "error"]
    error_message: NotRequired[str]


@runtime_checkable
class AlertLike(Protocol):
    """The minimum surface a Notifier reads off an alert. Both `Alert` (book)
    and `ProductAlert` satisfy this — `kind`, `message`, `currency` etc.
    have identical semantics across both stacks."""

    id: int | None
    kind: str
    message: str
    price_minor: int
    currency: str
    source: str
    condition: str


@runtime_checkable
class ItemLike(Protocol):
    """The minimum surface a Notifier reads off the tracked item. Both
    `Book` and `Product` expose `title` and `currency`; the `identifier`
    property gives ISBN-13 for books and ASIN for products (used by some
    channels for the "view details" link)."""

    id: int | None
    title: str
    currency: str

    @property
    def identifier(self) -> str: ...


class Notifier(ABC):
    name: str
    # True for channels that should still deliver during quiet hours (e.g. the
    # in-app feed, which only writes a DB row and never paging the user).
    bypasses_quiet_hours: bool = False

    @abstractmethod
    async def send(self, alert: AlertLike, item: ItemLike) -> NotificationResult: ...
