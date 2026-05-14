"""Alert pipeline. Given a list of affected book_ids, recompute stats, detect
alert kinds, apply global/per-book/mute/dedup filters, persist Alert +
NotificationDelivery rows, and update BookSignalState for the next eval.

Wired into Scheduler via `Scheduler(..., alert_pipeline=AlertPipeline.run)`
in `book_alerter.app.lifespan`.
"""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlmodel import Session, select

from book_alerter.alerts import AlertKind, detect_alert_kinds
from book_alerter.config import Config
from book_alerter.db.models import (
    Alert,
    Book,
    BookSignalState,
    NotificationDelivery,
)
from book_alerter.notifications.base import Notifier
from book_alerter.stats import compute_book_stats, compute_signal


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
                book = session.exec(
                    select(Book).where(Book.id == bid)
                ).one_or_none()
                if book is None:
                    continue
                stats = compute_book_stats(bid, session)

                # Prior state — absent on first eval (no transition fires yet).
                prev = session.exec(
                    select(BookSignalState).where(BookSignalState.book_id == bid)
                ).one_or_none()
                prev_signal = prev.last_signal if prev else None
                prev_all_time_min = (
                    prev.last_all_time_min_total_minor if prev else None
                )

                kinds: list[AlertKind] = detect_alert_kinds(
                    book, stats, prev_signal, prev_all_time_min,
                    self.cfg.recommendation,
                )
                # Global per-kind toggle + per-book disabled list.
                kinds = [
                    k for k in kinds
                    if k in self.cfg.notifications.alert_kinds_enabled
                    and k not in book.alert_kinds_disabled
                ]
                # Per-book mute — gate AFTER filters; skip alerts entirely but
                # continue to refresh BookSignalState so a future unmute has
                # an up-to-date prev_signal/prev_all_time_min baseline.
                muted = (
                    book.muted_until is not None
                    and datetime.now(UTC) < book.muted_until.replace(tzinfo=UTC)
                )
                if muted:
                    kinds = []
                kinds = self._filter_dedup(book, kinds, session)

                for k in kinds:
                    alert = Alert(
                        book_id=book.id,
                        kind=k,
                        price_minor=stats.current_best_total_minor or 0,
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

                self._persist_state(session, bid, book, stats)

    def _persist_state(
        self, session: Session, bid: int, book: Book, stats,
    ) -> None:
        # NOTE: compute_signal is also invoked inside detect_alert_kinds — once
        # per eval. Acceptable double-call for Phase 4; follow-up could thread
        # cur_signal through detect_alert_kinds to dedupe.
        cur_signal = compute_signal(book, stats, self.cfg.recommendation)
        state = session.exec(
            select(BookSignalState).where(BookSignalState.book_id == bid)
        ).one_or_none()
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

    def _format_message(self, book: Book, kind: str, stats) -> str:
        delta = ""
        if stats.p50_total_minor:
            pct = (
                100
                * (stats.p50_total_minor - (stats.current_best_total_minor or 0))
                / stats.p50_total_minor
            )
            delta = (
                f" (was median {stats.p50_total_minor / 100:.2f},"
                f" {pct:+.0f}%)"
            )
        return (
            f"[{kind.upper()}] {book.title} —"
            f" {(stats.current_best_total_minor or 0)/100:.2f} {book.currency}{delta}"
        )

    async def _deliver(
        self, alert: Alert, book: Book, session: Session,
    ) -> None:
        results = await asyncio.gather(
            *[n.send(alert, book) for n in self.notifiers],
            return_exceptions=True,
        )
        delivered: list[str] = []
        for n, r in zip(self.notifiers, results):
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
