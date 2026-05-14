"""In-app notifier — delivery is the Alert row itself. `send` is a no-op that
reports "sent" so the dispatcher records a NotificationDelivery for symmetry
with external channels (ntfy etc., added later)."""
from __future__ import annotations

from book_alerter.notifications.base import Notifier


class InAppNotifier(Notifier):
    name = "inapp"

    async def send(self, alert, book):  # type: ignore[override]
        return {"status": "sent"}
