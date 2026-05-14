"""Alert kind detection.

Pure logic that compares current `BookStats` against previous evaluation
state and returns the set of alert kinds that should fire, alongside the
current signal so callers can persist it without recomputing. Persistence
and deduplication are handled by the caller.
"""

from __future__ import annotations

from book_alerter.config import AlertKind, RecommendationConfig
from book_alerter.db.models import Book
from book_alerter.stats import BookStats, Signal, compute_signal


__all__ = ["AlertKind", "detect_alert_kinds"]


def detect_alert_kinds(
    book: Book,
    stats: BookStats,
    prev_signal: Signal | None,
    prev_all_time_min: int | None,
    cfg: RecommendationConfig,
) -> tuple[list[AlertKind], Signal]:
    cur_signal = compute_signal(book, stats, cfg)
    out: list[AlertKind] = []
    if stats.current_best_total_minor is None:
        return out, cur_signal

    if (
        book.target_price_minor is not None
        and stats.current_best_total_minor <= book.target_price_minor
        and prev_signal != "TARGET_HIT"
    ):
        out.append("target_hit")

    if cur_signal == "BUY" and prev_signal != "BUY":
        out.append("percentile_cross")

    if (
        prev_all_time_min is not None
        and stats.current_best_total_minor < prev_all_time_min
    ):
        out.append("new_low")

    return out, cur_signal
