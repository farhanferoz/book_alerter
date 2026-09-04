// Signal card — pill + target distance + percentile context.
//
// Signal is read from `item.stats.signal` (computed once on the backend
// with the live recommendation config), so the pill matches exactly what
// the alert dispatcher will fire.

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
  const current = item.stats.current_best_total_minor;
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

  return (
    <div className="rounded-md border border-border bg-card p-4">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-xs font-medium uppercase text-muted-foreground">
          Signal
        </h2>
        <SignalPill signal={signal} />
      </div>

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
