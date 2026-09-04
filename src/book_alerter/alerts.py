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

    # D34: compare the same metric `compute_signal` uses for TARGET_HIT —
    # `current_effective_total_minor`, never `current_best_total_minor`.
    # `_persist` stores the raw total as `price + (shipping or 0)`, so an
    # unknown-shipping row's raw total silently folds to the bare price and
    # can sit below the target even though the cascade-estimated delivered
    # cost does not. In practice `effective` is None only when
    # `current_best_total_minor` also is (see `_stats_for_one_item`), which
    # the early return above already rules out — the explicit `is not None`
    # check here is defence in depth for any other `BookStats` construction,
    # mirroring the same guard `NEW_LOW` already uses below.
    effective = stats.current_effective_total_minor
    if (
        item.target_price_minor is not None
        and effective is not None
        and effective <= item.target_price_minor
        and prev_signal != Signal.TARGET_HIT
    ):
        out.append(AlertKindEnum.TARGET_HIT)

    if cur_signal == Signal.BUY and prev_signal != Signal.BUY:
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
