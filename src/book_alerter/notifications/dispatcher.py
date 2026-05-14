"""Alert pipeline. Given a list of affected book_ids, recompute stats, detect
alert kinds, apply global/per-book/mute/dedup/quiet-hours filters, persist
Alert + NotificationDelivery rows, and update BookSignalState for the next
eval.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
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
)
from book_alerter.notifications.base import Notifier
from book_alerter.stats import BookStats, Signal, compute_book_stats


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


class AlertPipeline:
    def __init__(
        self,
        cfg: Config,
        session_factory: Callable[[], Session],
        notifiers: list[Notifier],
    ) -> None:
        self.cfg = cfg
        self.session_factory = session_factory
        self.notifiers = notifiers

    async def run(self, book_ids: list[int]) -> None:
        for bid in book_ids:
            with self.session_factory() as session:
                await self._run_one(session, bid)

    async def _run_one(self, session: Session, bid: int) -> None:
        book = session.get(Book, bid)
        if book is None:
            return

        # Per-book mute — skip the entire evaluation before any view reads.
        # Keeping the pre-mute prev_signal / prev_all_time_min lets a price
        # drop during the mute still fire new_low when the mute lifts.
        if (
            book.muted_until is not None
            and datetime.now(UTC) < book.muted_until.replace(tzinfo=UTC)
        ):
            return

        stats = compute_book_stats(bid, session)

        # Prior state — absent on first eval (no transition fires yet).
        prev = session.exec(
            select(BookSignalState).where(BookSignalState.book_id == bid)
        ).one_or_none()
        prev_signal = prev.last_signal if prev else None
        prev_all_time_min = prev.last_all_time_min_total_minor if prev else None

        kinds, cur_signal = detect_alert_kinds(
            book, stats, prev_signal, prev_all_time_min, self.cfg.recommendation,
        )
        kinds = [
            k for k in kinds
            if k in self.cfg.notifications.alert_kinds_enabled
            and k not in book.alert_kinds_disabled
        ]
        kinds = self._filter_dedup(book, kinds, session)

        for k in kinds:
            # At this point detect_alert_kinds guarantees current_best is non-None.
            assert stats.current_best_total_minor is not None
            alert = Alert(
                book_id=book.id,
                kind=k,
                price_minor=stats.current_best_total_minor,
                currency=book.currency,
                source=stats.current_best_source or "",
                condition=stats.current_best_condition or "",
                message=self._format_message(book, k, stats),
                fired_at=datetime.now(UTC),
                delivered_via=[],
            )
            session.add(alert)
            session.commit()
            session.refresh(alert)
            await self._deliver(alert, book, session)

        self._persist_state(session, prev, bid, cur_signal, stats)

    def _persist_state(
        self,
        session: Session,
        prev: BookSignalState | None,
        bid: int,
        cur_signal: Signal,
        stats: BookStats,
    ) -> None:
        state = prev
        if state is None:
            state = BookSignalState(book_id=bid)
            session.add(state)
        state.last_signal = cur_signal
        state.last_all_time_min_total_minor = stats.all_time_min_total_minor
        state.last_evaluated_at = datetime.now(UTC)
        session.commit()

    def _filter_dedup(
        self, book: Book, kinds: list[AlertKind], session: Session,
    ) -> list[AlertKind]:
        # Dedup window anchors on real wall clock (Alert.fired_at), not on
        # observed_at — "same alert kind fired N hours ago" rather than "same
        # alert from observations N hours apart." This matches user intent
        # (don't re-page on the same buy condition) and means back-to-back
        # pipeline runs in tests share the same dedup state regardless of
        # synthetic observed_at gaps; use freezegun to span the window.
        cutoff = datetime.now(UTC) - timedelta(
            hours=self.cfg.recommendation.alert_dedup_window_hours
        )
        out: list[AlertKind] = []
        for k in kinds:
            existing = session.exec(
                select(Alert).where(
                    Alert.book_id == book.id,
                    Alert.kind == k,
                    Alert.fired_at >= cutoff,
                )
            ).first()
            if existing is None:
                out.append(k)
        return out

    def _format_message(
        self, book: Book, kind: AlertKind, stats: BookStats,
    ) -> str:
        assert stats.current_best_total_minor is not None
        current = stats.current_best_total_minor
        delta = ""
        if stats.p50_total_minor is not None:
            pct = 100 * (stats.p50_total_minor - current) / stats.p50_total_minor
            delta = (
                f" (was median {stats.p50_total_minor / 100:.2f},"
                f" {pct:+.0f}%)"
            )
        return (
            f"[{kind.upper()}] {book.title} —"
            f" {current / 100:.2f} {book.currency}{delta}"
        )

    async def _deliver(
        self, alert: Alert, book: Book, session: Session,
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
            *[n.send(alert, book) for n in active_notifiers],
            return_exceptions=True,
        )
        delivered: list[str] = []
        for n, r in zip(active_notifiers, results, strict=True):
            if isinstance(r, Exception):
                session.add(NotificationDelivery(
                    alert_id=alert.id,
                    channel=n.name,
                    sent_at=datetime.now(UTC),
                    status="error",
                    error_message=str(r),
                ))
            else:
                session.add(NotificationDelivery(
                    alert_id=alert.id,
                    channel=n.name,
                    sent_at=datetime.now(UTC),
                    status=r["status"],
                    error_message=r.get("error_message"),
                ))
                if r["status"] == "sent":
                    delivered.append(n.name)
        alert.delivered_via = delivered
        session.commit()
