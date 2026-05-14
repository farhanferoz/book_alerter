"""In-app notifier — delivery is the Alert row itself. `send` is a no-op that
reports "sent" so the dispatcher records a NotificationDelivery for symmetry
with external channels (ntfy etc.)."""
from __future__ import annotations

from book_alerter.db.models import Alert, Book
from book_alerter.notifications.base import NotificationResult, Notifier


class InAppNotifier(Notifier):
    name = "inapp"
    bypasses_quiet_hours = True

    async def send(self, alert: Alert, book: Book) -> NotificationResult:
        return {"status": "sent"}
