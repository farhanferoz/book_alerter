/* eslint-disable react-refresh/only-export-components */
// Client-side approximation of `book_alerter.stats.compute_signal`.
//
// The exact backend computation needs `RecommendationConfig` (buy_percentile,
// min_observations_for_signal, target_tolerance_pct) plus the full
// `sorted_totals` array carried inside `BookStats`. The wire mirror
// (`BookStatsOut`) deliberately excludes `sorted_totals`, so we approximate
// using the materialised p25/p50/p75 fields.
//
// Approximation rules (mirrors backend semantics where data allows):
//   - observation_count < config.min_observations_for_signal: INSUFFICIENT_DATA
//   - current_best_total_minor is None: INSUFFICIENT_DATA
//   - target set + current <= target: TARGET_HIT
//   - current <= p25_total_minor: BUY
//   - current <= p50_total_minor: WATCH
//   - else: WAIT
//
// Phase 11.3 lifts the `min_observations_for_signal` constant out of this
// module — callers pass the live `RecommendationConfig` slice from
// `useConfig()`. When config is loading/errored, callers should fall through
// to `RECOMMENDATION_DEFAULTS` (or pass it explicitly) so the dashboard
// degrades to spec defaults rather than crashing. The Signal column is
// presentation only — alert dispatch uses the server-side computation.

import type { Book } from "@/hooks/useBooks";
import type { RecommendationConfigShape } from "@/hooks/useConfig";

export type Signal =
  | "BUY"
  | "WATCH"
  | "WAIT"
  | "TARGET_HIT"
  | "INSUFFICIENT_DATA";

export function approximateSignal(
  book: Book,
  config: RecommendationConfigShape,
): Signal {
  const s = book.stats;
  if (s.observation_count < config.min_observations_for_signal) {
    return "INSUFFICIENT_DATA";
  }
  if (s.current_best_total_minor == null) return "INSUFFICIENT_DATA";

  if (
    book.target_price_minor != null &&
    s.current_best_total_minor <= book.target_price_minor
  ) {
    return "TARGET_HIT";
  }
  if (s.p25_total_minor != null && s.current_best_total_minor <= s.p25_total_minor) {
    return "BUY";
  }
  if (s.p50_total_minor != null && s.current_best_total_minor <= s.p50_total_minor) {
    return "WATCH";
  }
  return "WAIT";
}

export const SIGNAL_LABEL: Record<Signal, string> = {
  BUY: "BUY",
  WATCH: "WATCH",
  WAIT: "WAIT",
  TARGET_HIT: "TARGET HIT",
  INSUFFICIENT_DATA: "NO DATA",
};

// Tailwind class strings keyed by signal. Palette follows the design spec:
//   BUY (green), TARGET_HIT (emerald, prominent), WATCH (amber),
//   WAIT (slate), INSUFFICIENT_DATA (neutral).
export const SIGNAL_PILL_CLASS: Record<Signal, string> = {
  BUY: "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300",
  TARGET_HIT:
    "bg-emerald-200 text-emerald-900 font-semibold dark:bg-emerald-900/60 dark:text-emerald-200",
  WATCH: "bg-amber-100 text-amber-800 dark:bg-amber-900/40 dark:text-amber-300",
  WAIT: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
  INSUFFICIENT_DATA:
    "bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-400",
};

// Shared pill renderer used by the dashboard column and the book-detail
// Signal card. Both call sites previously inlined the identical span; the
// component keeps the wire-up in one place.
export function SignalPill({ signal }: { signal: Signal }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${SIGNAL_PILL_CLASS[signal]}`}
    >
      {SIGNAL_LABEL[signal]}
    </span>
  );
}
