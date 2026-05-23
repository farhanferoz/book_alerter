"""Alert kind detection.

Pure logic that compares current `BookStats` against previous evaluation
state and returns the set of alert kinds that should fire, alongside the
current signal so callers can persist it without recomputing. Persistence
and deduplication are handled by the caller.
"""

from __future__ import annotations

from book_alerter.config import AlertKind, RecommendationConfig
from book_alerter.db.models import Book, Product
from book_alerter.enums import AlertKind as AlertKindEnum
from book_alerter.stats import BookStats, Signal, compute_signal

__all__ = ["AlertKind", "detect_alert_kinds"]


def detect_alert_kinds(
    item: Book | Product,
    stats: BookStats,
    prev_signal: Signal | None,
    prev_all_time_min: int | None,
    cfg: RecommendationConfig,
) -> tuple[list[AlertKind], Signal]:
    """Item-agnostic — works on any model exposing `target_price_minor`,
    `percentile_threshold`, etc. (the shared TrackedItem surface). Returns
    the alert kinds that should fire alongside the current signal so the
    caller can persist the signal without recomputing."""
    cur_signal = compute_signal(item, stats, cfg)
    out: list[AlertKind] = []
    if stats.current_best_total_minor is None:
        return out, cur_signal

    if (
        item.target_price_minor is not None
        and stats.current_best_total_minor <= item.target_price_minor
        and prev_signal != "TARGET_HIT"
    ):
        out.append(AlertKindEnum.TARGET_HIT)

    if cur_signal == "BUY" and prev_signal != "BUY":
        out.append(AlertKindEnum.PERCENTILE_CROSS)

    # `prev_all_time_min` is the cascade-imputed bound persisted by the
    # dispatcher. Compare on the same metric (`current_effective_total_minor`)
    # so a current row with NULL shipping doesn't falsely beat a historical
    # min that includes imputed shipping.
    if (
        prev_all_time_min is not None
        and stats.current_effective_total_minor is not None
        and stats.current_effective_total_minor < prev_all_time_min
    ):
        out.append(AlertKindEnum.NEW_LOW)

    return out, cur_signal
