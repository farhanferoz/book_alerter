"""Alerts feed + dismiss endpoints, across both tracked item kinds.

Implements `GET /api/alerts` (cursor-paginated feed; optional `kind`,
`item_kind` and `dismissed` filters), `POST /api/alerts/{item_kind}/{id}/dismiss`
(idempotent), and `POST /api/alerts/dismiss-all` (bulk).

Design notes:

- Books and products keep separate alert tables (`alert`, `productalert`) per
  the project's parallel-tables decision. Rather than duplicate the query,
  filter and dismiss logic per kind, every handler here iterates the existing
  `_AlertModels` registry from `notifications.dispatcher` — the same bundle the
  alert pipeline is parameterised on. Adding a third item kind means adding a
  registry entry, not editing this module.
- Alert ids are only unique *within* a table, so an alert is addressed by the
  pair (`item_kind`, `id`). That is why the dismiss route carries the kind.
- Alerts are dismissed manually only; there is no auto-dismiss anywhere in the
  system. Re-dismissing is a no-op (200, preserves the original
  `dismissed_at`) so retries are safe.
- `GET /api/alerts` mirrors the cursor-pagination shape used by
  `/api/books/{id}/observations` (cursor field is `fired_at`, strict `<`,
  newest-first, `next_before` emitted only when the page is full). Merging two
  tables is safe under that contract: each table is asked for the same
  `before`-bounded newest-`limit` rows, so the merged, re-sorted, truncated
  result is exactly the true newest `limit` rows across both.
- `dismiss-all` issues one UPDATE per table — never row-by-row — and returns
  the summed count.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import update
from sqlmodel import Session, select

from book_alerter.api._serializers import UtcDateTime, to_z_iso
from book_alerter.api.deps import SessionDep
from book_alerter.db import models
from book_alerter.enums import AlertKind, ItemKind
from book_alerter.notifications.dispatcher import (
    BOOK_MODELS,
    PRODUCT_MODELS,
    _AlertModels,
)

router = APIRouter(prefix="/api/alerts", tags=["alerts"])

# The registry is the single source of truth for per-kind classes and column
# names; see the module docstring.
_MODELS_BY_KIND: dict[ItemKind, _AlertModels] = {
    BOOK_MODELS.kind: BOOK_MODELS,
    PRODUCT_MODELS.kind: PRODUCT_MODELS,
}

# Placeholder shown when an alert outlives the item it fired for. The FKs are
# ON DELETE CASCADE, so this should be unreachable; it exists so a feed never
# 500s on unexpected data.
_UNKNOWN_ITEM_TITLE = "(deleted item)"


# --- DTOs -------------------------------------------------------------------


class AlertOut(BaseModel):
    """Wire mirror of an alert row, kind-agnostic.

    `item_kind` + `item_id` identify what the alert is about; together with
    `id` they address the row for dismissal.
    """

    id: int
    item_kind: ItemKind
    item_id: int
    title: str
    kind: AlertKind
    price_minor: int
    currency: str
    source: str
    condition: str
    message: str
    fired_at: UtcDateTime
    dismissed_at: UtcDateTime | None
    delivered_via: list[str]

    @classmethod
    def from_alert(
        cls,
        alert: models.Alert | models.ProductAlert,
        bundle: _AlertModels,
        title: str,
    ) -> AlertOut:
        return cls(
            id=alert.id or 0,
            item_kind=bundle.kind,
            item_id=getattr(alert, bundle.alert_item_id_attr),
            title=title,
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


# --- Helpers ----------------------------------------------------------------


def _titles_for(
    session: Session, bundle: _AlertModels, item_ids: set[int]
) -> dict[int, str]:
    """Batch-load item titles for one kind. One query, never N+1."""
    if not item_ids:
        return {}
    item_model = bundle.item_model
    rows = session.exec(
        select(item_model.id, item_model.title).where(item_model.id.in_(item_ids))  # type: ignore[union-attr, attr-defined]
    ).all()
    return {row[0]: row[1] for row in rows}


def _page_for_kind(
    session: Session,
    bundle: _AlertModels,
    *,
    limit: int,
    before: datetime | None,
    kind: AlertKind | None,
    dismissed: bool | None,
) -> list[AlertOut]:
    """Newest-`limit` alerts of one kind, already serialised."""
    alert_model = bundle.alert_model
    stmt = select(alert_model)
    if kind is not None:
        stmt = stmt.where(alert_model.kind == kind)
    if dismissed is False:
        stmt = stmt.where(alert_model.dismissed_at.is_(None))  # type: ignore[union-attr]
    elif dismissed is True:
        stmt = stmt.where(alert_model.dismissed_at.is_not(None))  # type: ignore[union-attr]
    if before is not None:
        stmt = stmt.where(alert_model.fired_at < before)
    stmt = stmt.order_by(alert_model.fired_at.desc()).limit(limit)  # type: ignore[attr-defined]

    rows = list(session.exec(stmt).all())
    titles = _titles_for(
        session, bundle, {getattr(r, bundle.alert_item_id_attr) for r in rows}
    )
    return [
        AlertOut.from_alert(
            r,
            bundle,
            titles.get(getattr(r, bundle.alert_item_id_attr), _UNKNOWN_ITEM_TITLE),
        )
        for r in rows
    ]


# --- Handlers ---------------------------------------------------------------


@router.get("", response_model=AlertsPage)
def list_alerts(
    session: SessionDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
    before: datetime | None = None,
    kind: AlertKind | None = None,
    item_kind: ItemKind | None = None,
    dismissed: bool | None = None,
) -> AlertsPage:
    """Cursor-paginated alerts feed across books and products (newest-first).

    - `kind` filters by alert kind; `item_kind` restricts to one item kind.
    - `dismissed=false` → only undismissed; `dismissed=true` → only dismissed;
      omitted → both.
    - `before` (ISO 8601 `fired_at`) is strict `<`; pass the previous page's
      `next_before` to fetch the next page.
    """
    bundles = (
        [_MODELS_BY_KIND[item_kind]] if item_kind is not None else list(_MODELS_BY_KIND.values())
    )
    merged: list[AlertOut] = []
    for bundle in bundles:
        merged.extend(
            _page_for_kind(
                session,
                bundle,
                limit=limit,
                before=before,
                kind=kind,
                dismissed=dismissed,
            )
        )
    # Newest-first across both tables, then truncate to the page size. Ties on
    # `fired_at` are broken by kind then id so paging is deterministic.
    merged.sort(key=lambda a: (a.fired_at, a.item_kind, a.id), reverse=True)
    items = merged[:limit]
    next_before = to_z_iso(items[-1].fired_at) if len(items) == limit and items else None
    return AlertsPage(items=items, next_before=next_before)


@router.post("/{item_kind}/{alert_id}/dismiss", response_model=AlertOut)
def dismiss_alert(
    item_kind: ItemKind,
    alert_id: int,
    session: SessionDep,
) -> AlertOut:
    """Dismiss one alert. Idempotent: re-dismissing preserves the original
    `dismissed_at` and still returns 200.

    The kind is part of the path because alert ids are unique only within
    their own table.
    """
    bundle = _MODELS_BY_KIND[item_kind]
    alert = session.get(bundle.alert_model, alert_id)
    if alert is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="alert not found")
    if alert.dismissed_at is None:
        alert.dismissed_at = datetime.now(UTC)
        session.add(alert)
        session.commit()
        session.refresh(alert)
    item = session.get(bundle.item_model, getattr(alert, bundle.alert_item_id_attr))
    title = item.title if item is not None else _UNKNOWN_ITEM_TITLE
    return AlertOut.from_alert(alert, bundle, title)


@router.post("/dismiss-all", response_model=DismissAllResult)
def dismiss_all_alerts(session: SessionDep) -> DismissAllResult:
    """Bulk-dismiss every undismissed alert of every kind.

    One UPDATE per table — never iterates row-by-row. Returns the total number
    of rows updated; previously-dismissed alerts retain their timestamp.
    """
    now = datetime.now(UTC)
    dismissed = 0
    for bundle in _MODELS_BY_KIND.values():
        alert_model = bundle.alert_model
        result = session.exec(  # type: ignore[call-overload]
            update(alert_model)
            .where(alert_model.dismissed_at.is_(None))  # type: ignore[union-attr]
            .values(dismissed_at=now)
        )
        dismissed += result.rowcount or 0
    session.commit()
    return DismissAllResult(dismissed_count=dismissed)
