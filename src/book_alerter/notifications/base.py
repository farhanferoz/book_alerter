"""Notifier ABC + shared result type. Channel implementations return a
`NotificationResult` that the dispatcher persists as a NotificationDelivery row.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal, NotRequired, TypedDict

from book_alerter.db.models import Alert, Book


class NotificationResult(TypedDict):
    status: Literal["sent", "error"]
    error_message: NotRequired[str]


class Notifier(ABC):
    name: str
    # True for channels that should still deliver during quiet hours (e.g. the
    # in-app feed, which only writes a DB row and never paging the user).
    bypasses_quiet_hours: bool = False

    @abstractmethod
    async def send(self, alert: Alert, book: Book) -> NotificationResult: ...
