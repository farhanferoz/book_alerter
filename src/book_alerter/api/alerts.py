"""Alerts feed + dismiss endpoints.

Implements `GET /api/alerts` (cursor-paginated feed; optional `kind` and
`dismissed` filters), `POST /api/alerts/{id}/dismiss` (idempotent),
and `POST /api/alerts/dismiss-all` (bulk via a single UPDATE) — Phase 7 Task 7.3.

Design notes:

- Alerts are dismissed manually only (spec line 40); there is no
  auto-dismiss anywhere in the system. Re-dismissing a dismissed alert is a
  no-op (200, returns the existing `dismissed_at`) so retries are safe.
- `GET /api/alerts` mirrors the cursor-pagination shape used by
  `/api/books/{id}/observations` (cursor field is `fired_at`, strict `<`,
  newest-first, `next_before` emitted only when the page is full).
- `dismiss-all` issues a single `UPDATE alert SET dismissed_at = now() WHERE
  dismissed_at IS NULL`; the response carries `result.rowcount` rather than
  iterating row-by-row.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import update
from sqlmodel import select

from book_alerter.api.deps import SessionDep
from book_alerter.db import models

router = APIRouter(prefix="/api/alerts", tags=["alerts"])

AlertKind = Literal["new_low", "target_hit", "percentile_cross"]


# --- DTOs -------------------------------------------------------------------


class AlertOut(BaseModel):
    """Wire mirror of `book_alerter.db.models.Alert`."""
    id: int
    book_id: int
    kind: AlertKind
    price_minor: int
    currency: str
    source: str
    condition: str
    message: str
    fired_at: datetime
    dismissed_at: datetime | None
    delivered_via: list[str]

    @classmethod
    def from_alert(cls, alert: models.Alert) -> AlertOut:
        return cls(
            id=alert.id or 0,
            book_id=alert.book_id,
            kind=alert.kind,
            price_minor=alert.price_minor,
            currency=alert.currency,
            source=alert.source,
            condition=alert.condition,
            message=alert.message,
            fired_at=alert.fired_at,
            dismissed_at=alert.dismissed_at,
            delivered_via=list(alert.delivered_via or []),
        )


class AlertsPage(BaseModel):
    """Cursor-paginated page of alerts (newest-first by `fired_at`).

    `next_before` is the `fired_at` (ISO 8601) of the last row in `items`;
    pass it as `before` to fetch the next page. `None` when `len(items) <
    limit` (page not full → no more rows).
    """
    items: list[AlertOut]
    next_before: str | None


class DismissAllResult(BaseModel):
    dismissed_count: int


# --- Handlers ---------------------------------------------------------------


@router.get("", response_model=AlertsPage)
def list_alerts(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    before: datetime | None = None,
    kind: AlertKind | None = None,
    dismissed: bool | None = None,
) -> AlertsPage:
    """Cursor-paginated alerts feed (newest-first).

    - `kind` filters by alert kind.
    - `dismissed=false` → only undismissed; `dismissed=true` → only dismissed;
      omitted → both.
    - `before` (ISO 8601 `fired_at`) is strict `<`; pass the previous page's
      `next_before` to fetch the next page.
    """
    stmt = select(models.Alert)
    if kind is not None:
        stmt = stmt.where(models.Alert.kind == kind)
    if dismissed is False:
        stmt = stmt.where(models.Alert.dismissed_at.is_(None))  # type: ignore[union-attr]
    elif dismissed is True:
        stmt = stmt.where(models.Alert.dismissed_at.is_not(None))  # type: ignore[union-attr]
    if before is not None:
        stmt = stmt.where(models.Alert.fired_at < before)
    stmt = stmt.order_by(models.Alert.fired_at.desc()).limit(limit)  # type: ignore[attr-defined]

    rows = session.exec(stmt).all()
    items = [AlertOut.from_alert(a) for a in rows]
    next_before = (
        rows[-1].fired_at.isoformat() if len(rows) == limit and rows else None
    )
    return AlertsPage(items=items, next_before=next_before)


@router.post("/{alert_id}/dismiss", response_model=AlertOut)
def dismiss_alert(
    alert_id: int,
    session: SessionDep,
) -> AlertOut:
    """Dismiss an alert. Idempotent: re-dismissing preserves the original
    `dismissed_at` and still returns 200."""
    alert = session.get(models.Alert, alert_id)
    if alert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="alert not found")
    if alert.dismissed_at is None:
        alert.dismissed_at = datetime.now(UTC)
        session.add(alert)
        session.commit()
        session.refresh(alert)
    return AlertOut.from_alert(alert)


@router.post("/dismiss-all", response_model=DismissAllResult)
def dismiss_all_alerts(session: SessionDep) -> DismissAllResult:
    """Bulk-dismiss every alert with `dismissed_at IS NULL`.

    Single UPDATE — never iterates row-by-row. Returns the number of rows
    updated; previously-dismissed alerts retain their original timestamp.
    """
    now = datetime.now(UTC)
    result = session.exec(  # type: ignore[call-overload]
        update(models.Alert)
        .where(models.Alert.dismissed_at.is_(None))  # type: ignore[union-attr]
        .values(dismissed_at=now)
    )
    session.commit()
    return DismissAllResult(dismissed_count=result.rowcount or 0)
