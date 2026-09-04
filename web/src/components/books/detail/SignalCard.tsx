// Signal card — pill + target distance + percentile context.
//
// Signal is read from `item.stats.signal` (computed once on the backend
// with the live recommendation config), so the pill matches exactly what
// the alert dispatcher will fire.
//
// T4.4: `live_observation_count === 0` (with `observation_count > 0` —
// mirrors the backend guard in `notifications/dispatcher.py`, though the
// backend's own condition already makes this unreachable with zero
// history) means every price in the window came from Keepa backfill, none
// from a live scrape. The signal itself is still legitimate (D16 — Keepa
// history is valid history, `compute_signal` fires on it deliberately) —
// this is provenance context, not an error, so it renders in the same
// muted tone as the other secondary lines below, never styled like a
// warning. Wording matches the dispatcher's alert-message suffix verbatim
// (adapted from a mid-sentence clause to a standalone sentence) so the
// dashboard, detail page and alert text never drift into three different
// phrasings of the same fact.

import type { Item } from "@/lib/item";
import { formatMoneyMinor, ordinalSuffix } from "@/lib/format";
import { SignalPill, bookSignal } from "@/components/books/signal";
import { useConfig, RECOMMENDATION_DEFAULTS } from "@/hooks/useConfig";

function percentileSummary(item: Item): string | null {
  // Read the rank for the configured window (canonical OR custom) directly
  // from the backend-computed `current_percentile_rank` field. The backend
  // handles non-canonical windows by computing rank against the bounded
  // slice on the fly.
  const s = item.stats;
  const rank = s.current_percentile_rank;
  if (rank == null) return null;
  return `At the ${rank}${ordinalSuffix(rank)} percentile of ${s.percentile_window_days}-day history`;
}

function targetDistance(item: Item): string | null {
  if (item.target_price_minor == null) return null;
  // `current_effective_total_minor`, not `current_best_total_minor` (F2 /
  // D34: "every user-facing price comparison reads the effective total,
  // never the raw one" -- `stats.py`'s `compute_signal` derives the pill
  // above from this same field, so a raw-total comparison here can show
  // the opposite of the pill on the same screen: e.g. £9.50 raw with an
  // unlisted-shipping estimate landing effective at £12.30 against a
  // £10.00 target reads "Target met" beside a WAIT/WATCH pill).
  const current = item.stats.current_effective_total_minor;
  if (current == null) return null;
  const delta = current - item.target_price_minor;
  const pct = Math.round((delta / item.target_price_minor) * 100);
  if (delta <= 0) {
    return `Target met — ${formatMoneyMinor(-delta, item.currency)} below target.`;
  }
  return `${formatMoneyMinor(delta, item.currency)} above target (${pct > 0 ? "+" : ""}${pct}%).`;
}

export function SignalCard({ item }: { item: Item }) {
  // `min_observations_for_signal` is shown in the INSUFFICIENT_DATA hint
  // so the user knows the threshold; signal itself comes from the backend.
  const cfg = useConfig();
  const recommendation = cfg.data?.recommendation ?? RECOMMENDATION_DEFAULTS;
  const signal = bookSignal(item);
  const summary = percentileSummary(item);
  const distance = targetDistance(item);
  const s = item.stats;
  const shippingNote =
    s.current_best_shipping_minor == null && s.shipping_estimate_minor != null
      ? `Shipping for current row unknown; using observed median ${formatMoneyMinor(s.shipping_estimate_minor, item.currency)} for ranking.`
      : null;
  const keepaOnly = s.observation_count > 0 && s.live_observation_count === 0;

  return (
    <div className="rounded-md border border-border bg-card p-4">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-xs font-medium uppercase text-muted-foreground">
          Signal
        </h2>
        <SignalPill signal={signal} />
      </div>

      {keepaOnly && (
        <p className="mt-2 text-xs text-muted-foreground">
          Based on Keepa history only — no live offer yet.
        </p>
      )}

      {signal === "INSUFFICIENT_DATA" ? (
        <p className="mt-2 text-sm text-muted-foreground">
          Need at least {recommendation.min_observations_for_signal} observations
          to compute a signal. Currently {s.observation_count}.
        </p>
      ) : (
        <div className="mt-2 space-y-1.5 text-sm">
          {item.target_price_minor != null ? (
            <p>
              <span className="text-muted-foreground">Target:</span>{" "}
              <span className="font-medium">
                {formatMoneyMinor(item.target_price_minor, item.currency)}
              </span>
              {distance && (
                <span className="ml-1 text-xs text-muted-foreground">
                  — {distance}
                </span>
              )}
            </p>
          ) : (
            <p className="text-xs text-muted-foreground">
              No target set. Add one in Settings below to enable target alerts.
            </p>
          )}
          {summary && (
            <p className="text-xs text-muted-foreground">
              {summary} ({s.observation_count} obs over {s.days_of_history} days).
            </p>
          )}
          {shippingNote && (
            <p className="text-xs text-muted-foreground/80">{shippingNote}</p>
          )}
        </div>
      )}
    </div>
  );
}
