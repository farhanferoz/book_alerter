"""Notification channel management endpoints (Phase 7 Task 7.7, plan line 2552).

Currently exposes `POST /api/notifications/{channel}/test` — synthesizes an
in-memory `Book` + `Alert` (NOT persisted) and dispatches it through the named
notifier so users can verify their channel config end-to-end without waiting
for a real alert to fire.

Design notes:

- The synthesized `Alert` has `id=None` / `book_id=0` and is never written to
  the DB. The notifier just reads fields off the model — for ntfy this is
  title/message/tags, for in-app it's the row insert (but in-app is in-app:
  the test endpoint is for *push* channels, and even if a user routes it to
  in-app, no `NotificationDelivery` row is created because there's no real
  Alert to attach to). Skip the DB write rather than persist a fake alert that
  would pollute the feed.
- Notifier lookup is by `notifier.name` via `app.state.notifiers` (a dict
  populated by `app.lifespan` + the `api_client` test fixture). 404 when no
  notifier matches the path param.
- Future Phase 11+ endpoints (mute, dedupe stats, delivery history) will land
  here too — keeping this router separate from `/api/alerts` since alerts are
  user-facing data while notifications are channel plumbing.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel

from book_alerter.api.alerts import AlertOut
from book_alerter.api.deps import NotifiersDep
from book_alerter.db import models
from book_alerter.enums import AlertKind
from book_alerter.notifications.dispatcher import BOOK_MODELS

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


# --- DTOs -------------------------------------------------------------------


class NotificationTestResult(BaseModel):
    """Result of `POST /api/notifications/{channel}/test`."""
    channel: str
    status: Literal["sent", "error"]
    error_message: str | None
    alert: AlertOut


# --- Handlers ---------------------------------------------------------------


@router.post("/{channel}/test", response_model=NotificationTestResult)
async def test_notification(
    channel: str,
    notifiers: NotifiersDep,
) -> NotificationTestResult:
    """Send a synthetic test alert through the named notifier channel.

    The alert is constructed in memory and is **not persisted**. 404 if no
    notifier with that name is configured. The notifier's `NotificationResult`
    (`sent` or `error` with optional `error_message`) is surfaced verbatim so
    the UI can show the actual failure mode (e.g. "ntfy returned 401").
    """
    notifier = notifiers.get(channel)
    if notifier is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"no notifier named {channel!r}",
        )
    now = datetime.now(UTC)
    book = models.Book(
        isbn13="9780000000007",
        title="Test Book",
        author="Book Alerter",
        created_at=now,
        updated_at=now,
    )
    alert = models.Alert(
        book_id=0,
        kind=AlertKind.TARGET_HIT,
        price_minor=1099,
        currency="GBP",
        source="test",
        condition="new",
        message="This is a test notification from Book Alerter.",
        fired_at=now,
    )
    result = await notifier.send(alert, book)
    return NotificationTestResult(
        channel=channel,
        status=result["status"],
        error_message=result.get("error_message"),
        # The synthetic alert is never persisted, so it has no real item to
        # resolve a title from — pass the stand-in book's own title.
        alert=AlertOut.from_alert(alert, BOOK_MODELS, book.title),
    )
