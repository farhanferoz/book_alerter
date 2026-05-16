// Single source of truth for the percentile window keys surfaced on the
// dashboard (mini-bars column) and the detail page (PercentileChart).
// Stay in sync with `book_alerter.stats.WINDOW_DAYS` on the backend.

import type { Book } from "@/hooks/useBooks";

export const WINDOW_KEYS = ["1m", "3m", "12m"] as const;
export type WindowKey = (typeof WINDOW_KEYS)[number];

export function rankIn(book: Book, key: WindowKey): number | null {
  return book.stats.windows?.[key]?.rank ?? null;
}

// Books with no 3m rank sink to the bottom of an ascending sort.
export function rank3mOrInf(book: Book): number {
  return rankIn(book, "3m") ?? Number.MAX_SAFE_INTEGER;
}
