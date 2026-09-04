/* eslint-disable react-refresh/only-export-components */
// Signal accessor — reads the authoritative signal computed by
// `book_alerter.stats.compute_signal` and shipped on the wire as
// `BookStatsOut.signal`. Falls back to INSUFFICIENT_DATA if the field is
// somehow missing (older API payload, error state). The dashboard pill
// matches exactly what the alert dispatcher will fire.
//
// `bookSignal` takes anything with a `.stats` of the shared `ItemStats`
// shape rather than specifically a `Book` — `BookOut.stats` and
// `ProductOut.stats` are the same wire type (see `@/lib/item`), so this
// reads a product's signal identically without a second function. `Signal`
// itself is re-exported from `@/lib/item`, where it's derived from the
// generated schema rather than hand-written here.

import type { ItemStats, Signal } from "@/lib/item";

export type { Signal };

export function bookSignal(item: { stats: ItemStats }): Signal {
  return item.stats.signal ?? "INSUFFICIENT_DATA";
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
