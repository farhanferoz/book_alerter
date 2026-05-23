"""Alert pipeline. Given a list of affected item ids, recompute stats, detect
alert kinds, apply global/per-item/mute/dedup/quiet-hours filters, persist
Alert + NotificationDelivery rows, and update SignalState for the next eval.

One `AlertPipeline` instance is parameterised on a `_AlertModels` bundle that
selects book vs product tables. The app holds two pipelines (one per kind)
and routes each scheduler run to the matching one. All cross-kind code paths
(dedup window, quiet hours, mute, notifier dispatch) are shared.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlmodel import Session, select

from book_alerter.alerts import AlertKind, detect_alert_kinds
from book_alerter.config import Config, QuietHours
from book_alerter.db.models import (
    Alert,
    Book,
    BookSignalState,
    NotificationDelivery,
    Product,
    ProductAlert,
    ProductSignalState,
)
from book_alerter.enums import ItemKind
from book_alerter.notifications.base import Notifier
from book_alerter.stats import (
    _BOOK_SCHEMA,
    _PRODUCT_SCHEMA,
    BookStats,
    SellerClass,
    Signal,
    _ItemSchema,
    compute_book_stats,
    compute_product_stats,
    label_for_days,
    source_seller_global_shipping_medians,
)


def _in_quiet_hours(now_local: datetime, qh: QuietHours | None) -> bool:
    """Return True if `now_local` (in the user's tz) falls inside the configured
    quiet-hours window. `start` is inclusive, `end` is exclusive. Supports both
    normal (start < end) and wrapping (start > end, e.g. 22:00–08:00) windows."""
    if qh is None:
        return False
    start_h, start_m = map(int, qh.start.split(":"))
    end_h, end_m = map(int, qh.end.split(":"))
    cur = now_local.hour * 60 + now_local.minute
    s = start_h * 60 + start_m
    e = end_h * 60 + end_m
    return (s <= cur or cur < e) if s > e else (s <= cur < e)


@dataclass(frozen=True)
class _AlertModels:
    """Bundles every kind-specific class + helper the pipeline needs so the
    same `AlertPipeline` implementation handles books and products by swapping
    a single argument. Two module-level instances at the bottom of this file
    (`BOOK_MODELS`, `PRODUCT_MODELS`) cover the two callers."""

    kind: ItemKind
    item_model: type[Book | Product]
    alert_model: type[Alert | ProductAlert]
    signal_state_model: type[BookSignalState | ProductSignalState]
    stats_fn: Callable[..., BookStats]
    schema: _ItemSchema
    # Column name on the alert model that holds the FK to the item, e.g.
    # `book_id` / `product_id`. Used by `_filter_dedup` to scope by item.
    alert_item_id_attr: str
    # Column name on the signal-state model that holds the FK to the item.
    state_item_id_attr: str
    # Column name on `NotificationDelivery` for this alert kind's FK. Books
    # use `alert_id`; products use `product_alert_id`. Exactly one of the
    # two is non-NULL per delivery row (enforced by the CHECK constraint).
    delivery_fk_attr: str


BOOK_MODELS = _AlertModels(
    kind=ItemKind.BOOK,
    item_model=Book,
    alert_model=Alert,
    signal_state_model=BookSignalState,
    stats_fn=compute_book_stats,
    schema=_BOOK_SCHEMA,
    alert_item_id_attr="book_id",
    state_item_id_attr="book_id",
    delivery_fk_attr="alert_id",
)

PRODUCT_MODELS = _AlertModels(
    kind=ItemKind.PRODUCT,
    item_model=Product,
    alert_model=ProductAlert,
    signal_state_model=ProductSignalState,
    stats_fn=compute_product_stats,
    schema=_PRODUCT_SCHEMA,
    alert_item_id_attr="product_id",
    state_item_id_attr="product_id",
    delivery_fk_attr="product_alert_id",
)


class AlertPipeline:
    def __init__(
        self,
        cfg: Config,
        session_factory: Callable[[], Session],
        notifiers: list[Notifier],
        models: _AlertModels = BOOK_MODELS,
    ) -> None:
        self.cfg = cfg
        self.session_factory = session_factory
        self.notifiers = notifiers
        self.models = models
        # Per-item lock so two source runs finishing within the dedup window
        # cannot both evaluate the same item, race past `_filter_dedup`, and
        # commit duplicate Alert rows. Pipelines on DISTINCT items still run
        # in parallel; only same-item overlap serializes.
        self._item_locks: dict[int, asyncio.Lock] = {}

    def _lock_for(self, iid: int) -> asyncio.Lock:
        lock = self._item_locks.get(iid)
        if lock is None:
            lock = self._item_locks[iid] = asyncio.Lock()
        return lock

    async def run(self, item_ids: list[int]) -> None:
        # Compute the global shipping medians once per pipeline call.
        # `compute_*_stats` otherwise re-runs this full-table scan for every
        # item — with ~3 sources × hourly cron that's a lot of wasted SQLite
        # reads on the locked DB. The medians are read-only across the
        # cycle, so a single snapshot is correct.
        with self.session_factory() as session:
            medians = source_seller_global_shipping_medians(
                session,
                min_observations=self.cfg.recommendation.min_global_median_observations,
                schema=self.models.schema,
            )
        for iid in item_ids:
            lock = self._lock_for(iid)
            async with lock:
                with self.session_factory() as session:
                    await self._run_one(session, iid, medians=medians)

    async def _run_one(
        self,
        session: Session,
        iid: int,
        *,
        medians: dict[tuple[str, SellerClass], int] | None = None,
    ) -> None:
        item = session.get(self.models.item_model, iid)
        if item is None:
            return

        # Per-item mute — skip the entire evaluation before any view reads.
        # Keeping the pre-mute prev_signal / prev_all_time_min lets a price
        # drop during the mute still fire new_low when the mute lifts.
        if (
            item.muted_until is not None
            and datetime.now(UTC) < item.muted_until.replace(tzinfo=UTC)
        ):
            return

        window = item.percentile_window_days or self.cfg.recommendation.percentile_window_days
        stats = self.models.stats_fn(
            iid,
            session,
            window,
            source_seller_global_medians=medians,
            default_shipping_minor=self.cfg.recommendation.default_shipping_minor,
            min_global_median_observations=self.cfg.recommendation.min_global_median_observations,
        )

        # Prior state — absent on first eval (no transition fires yet).
        state_model = self.models.signal_state_model
        state_fk = getattr(state_model, self.models.state_item_id_attr)
        prev = session.exec(
            select(state_model).where(state_fk == iid)
        ).one_or_none()
        prev_signal = prev.last_signal if prev else None
        prev_all_time_min = prev.last_all_time_min_total_minor if prev else None

        kinds, cur_signal = detect_alert_kinds(
            item, stats, prev_signal, prev_all_time_min, self.cfg.recommendation,
        )
        kinds = [
            k for k in kinds
            if k in self.cfg.notifications.alert_kinds_enabled
            and k not in item.alert_kinds_disabled
        ]
        kinds = self._filter_dedup(item, kinds, session)

        for k in kinds:
            # At this point detect_alert_kinds guarantees current_best is non-None.
            assert stats.current_best_total_minor is not None
            alert = self.models.alert_model(
                **{self.models.alert_item_id_attr: item.id},
                kind=k,
                price_minor=stats.current_best_total_minor,
                currency=item.currency,
                source=stats.current_best_source or "",
                condition=stats.current_best_condition or "",
                message=self._format_message(item, k, stats),
                fired_at=datetime.now(UTC),
                delivered_via=[],
            )
            session.add(alert)
            session.commit()
            session.refresh(alert)
            await self._deliver(alert, item, session)

        self._persist_state(session, prev, iid, cur_signal, stats)

    def _persist_state(
        self,
        session: Session,
        prev: BookSignalState | ProductSignalState | None,
        iid: int,
        cur_signal: Signal,
        stats: BookStats,
    ) -> None:
        state = prev
        if state is None:
            state = self.models.signal_state_model(
                **{self.models.state_item_id_attr: iid}
            )
            session.add(state)
        state.last_signal = cur_signal
        state.last_all_time_min_total_minor = stats.all_time_min_total_minor
        state.last_evaluated_at = datetime.now(UTC)
        session.commit()

    def _filter_dedup(
        self,
        item: Book | Product,
        kinds: list[AlertKind],
        session: Session,
    ) -> list[AlertKind]:
        # Dedup window anchors on real wall clock (alert.fired_at), not on
        # observed_at — "same alert kind fired N hours ago" rather than "same
        # alert from observations N hours apart." This matches user intent
        # (don't re-page on the same buy condition) and means back-to-back
        # pipeline runs in tests share the same dedup state regardless of
        # synthetic observed_at gaps; use freezegun to span the window.
        cutoff = datetime.now(UTC) - timedelta(
            hours=self.cfg.recommendation.alert_dedup_window_hours
        )
        alert_model = self.models.alert_model
        item_fk = getattr(alert_model, self.models.alert_item_id_attr)
        out: list[AlertKind] = []
        for k in kinds:
            existing = session.exec(
                select(alert_model).where(
                    item_fk == item.id,
                    alert_model.kind == k,
                    alert_model.fired_at >= cutoff,
                )
            ).first()
            if existing is None:
                out.append(k)
        return out

    def _format_message(
        self,
        item: Book | Product,
        kind: AlertKind,
        stats: BookStats,
    ) -> str:
        """Render the alert message.

        Uses prose ("below"/"above"/"at median") rather than a signed
        percentage — the prior `(was median X, +P%)` format made
        "below median" read as a positive number, which most readers
        parsed as ABOVE median. See commit `0051dea`.
        """
        assert stats.current_best_total_minor is not None
        current = stats.current_best_total_minor
        item_minor = stats.current_best_price_minor
        ship_minor = stats.current_best_shipping_minor
        ccy = item.currency
        if item_minor is not None and ship_minor is not None:
            if ship_minor == 0:
                breakdown = f" (item {item_minor / 100:.2f}, free ship)"
            else:
                breakdown = (
                    f" (item {item_minor / 100:.2f} + "
                    f"{ship_minor / 100:.2f} ship)"
                )
        else:
            # current_best_* came from a non-buyable row (shipping unknown);
            # alerts shouldn't normally fire on these but stay defensive.
            breakdown = ""

        cfg_label = label_for_days(stats.percentile_window_days)
        delta = ""
        p50 = (
            stats.windows[cfg_label].p50
            if cfg_label is not None and cfg_label in stats.windows
            else None
        )
        # `p50 > 0` guards a real degenerate case: when every historical
        # observation totals £0 (free copies, parser quirk), p50 is 0 and
        # the `(p50 - current) / p50` divisor blows up. The alert row has
        # already been committed at this point, so a raise here would mean
        # the user gets the DB row but no notification — silently broken.
        if p50 is not None and p50 > 0:
            pct = 100 * (p50 - current) / p50
            # Round to the rendered precision FIRST, then compare. A naive
            # `abs(pct) < 0.5` lets `pct == 0.5` fall through to the
            # else-branch, where `f"{0.5:.0f}"` formats as "0" → the
            # message reads "0% below median X" — the exact contradiction
            # the threshold is supposed to prevent.
            display_pct = round(abs(pct))
            window_str = f"{stats.percentile_window_days}d"
            if display_pct == 0:
                delta = f", at {window_str} median {p50 / 100:.2f}"
            else:
                direction = "below" if pct > 0 else "above"
                delta = (
                    f", {display_pct}% {direction} "
                    f"{window_str} median {p50 / 100:.2f}"
                )
        return (
            f"[{kind.upper()}] {item.title} — "
            f"total {current / 100:.2f} {ccy}{breakdown}{delta}"
        )

    async def _deliver(
        self,
        alert: Alert | ProductAlert,
        item: Book | Product,
        session: Session,
    ) -> None:
        # Quiet-hours gate: skip notifiers that don't opt into bypass while
        # we're inside the configured window. The Alert row (already written
        # above) and the in-app NotificationDelivery still land — the user can
        # see it in the feed; we just don't page them. Re-firing after the
        # window relies on the buy condition still holding + the dedup window
        # having passed (plan-documented MVP simplification).
        qh = self.cfg.notifications.quiet_hours
        in_quiet = qh is not None and _in_quiet_hours(
            datetime.now(ZoneInfo(qh.tz)), qh,
        )
        active_notifiers = [
            n for n in self.notifiers if n.bypasses_quiet_hours or not in_quiet
        ]

        results = await asyncio.gather(
            *[n.send(alert, item) for n in active_notifiers],
            return_exceptions=True,
        )
        delivered: list[str] = []
        for n, r in zip(active_notifiers, results, strict=True):
            base_kwargs = {
                "channel": n.name,
                "sent_at": datetime.now(UTC),
                self.models.delivery_fk_attr: alert.id,
            }
            if isinstance(r, Exception):
                session.add(NotificationDelivery(
                    **base_kwargs,
                    status="error",
                    error_message=str(r),
                ))
            else:
                session.add(NotificationDelivery(
                    **base_kwargs,
                    status=r["status"],
                    error_message=r.get("error_message"),
                ))
                if r["status"] == "sent":
                    delivered.append(n.name)
        alert.delivered_via = delivered
        session.commit()
