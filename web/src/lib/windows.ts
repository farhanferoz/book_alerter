// Single source of truth for the percentile window keys surfaced on the
// dashboard (mini-bars column) and the detail page (PercentileChart).
// Stay in sync with `book_alerter.stats.WINDOW_DAYS` on the backend.

import type { ItemStats } from "@/lib/item";

export const WINDOW_KEYS = ["1m", "3m", "12m"] as const;
export type WindowKey = (typeof WINDOW_KEYS)[number];

// Days-per-key mirror of WINDOW_DAYS in src/book_alerter/stats.py. Used to
// map `stats.percentile_window_days` (e.g. 90) back to the key ("3m") so
// per-window stats (p50, rank) for the configured window can be read without
// passing labels around.
const WINDOW_DAYS: Record<WindowKey, number> = { "1m": 30, "3m": 90, "12m": 365 };

export function keyForDays(days: number | null | undefined): WindowKey | null {
  for (const k of WINDOW_KEYS) {
    if (WINDOW_DAYS[k] === days) return k;
  }
  return null;
}

// `rankIn`/`rank3mOrInf` take anything with a `.stats` of the shared
// `ItemStats` shape — not specifically a `Book` — since `BookOut.stats` and
// `ProductOut.stats` are the same wire type (see `@/lib/item`).
export function rankIn(item: { stats: ItemStats }, key: WindowKey): number | null {
  return item.stats.windows?.[key]?.rank ?? null;
}

// Items with no 3m rank sink to the bottom of an ascending sort.
export function rank3mOrInf(item: { stats: ItemStats }): number {
  return rankIn(item, "3m") ?? Number.MAX_SAFE_INTEGER;
}
