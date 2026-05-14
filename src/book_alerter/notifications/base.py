"""Notifier ABC. Channel implementations return a dict suitable for
NotificationDelivery: {"status": "sent"|"error", "error_message"?: str}.
"""
from __future__ import annotations

from abc import ABC, abstractmethod

from book_alerter.db.models import Alert, Book


class Notifier(ABC):
    name: str

    @abstractmethod
    async def send(self, alert: Alert, book: Book) -> dict:
        """Returns a dict suitable for NotificationDelivery: {status, error_message?}."""
