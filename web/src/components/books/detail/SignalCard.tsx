// Signal card — pill + target distance + percentile context.
//
// Signal is read from `book.stats.signal` (computed once on the backend
// with the live recommendation config), so the pill matches exactly what
// the alert dispatcher will fire.

import type { Book } from "@/hooks/useBook";
import { formatMoneyMinor, ordinalSuffix } from "@/lib/format";
import { SignalPill, bookSignal } from "@/components/books/signal";
import { useConfig, RECOMMENDATION_DEFAULTS } from "@/hooks/useConfig";

function percentileSummary(book: Book): string | null {
  const s = book.stats;
  const rank = s.current_percentile_rank;
  if (rank == null) return null;
  return `At the ${rank}${ordinalSuffix(rank)} percentile of ${s.percentile_window_days}-day history`;
}

function targetDistance(book: Book): string | null {
  if (book.target_price_minor == null) return null;
  const current = book.stats.current_best_total_minor;
  if (current == null) return null;
  const delta = current - book.target_price_minor;
  const pct = Math.round((delta / book.target_price_minor) * 100);
  if (delta <= 0) {
    return `Target met — ${formatMoneyMinor(-delta, book.currency)} below target.`;
  }
  return `${formatMoneyMinor(delta, book.currency)} above target (${pct > 0 ? "+" : ""}${pct}%).`;
}

export function SignalCard({ book }: { book: Book }) {
  // `min_observations_for_signal` is shown in the INSUFFICIENT_DATA hint
  // so the user knows the threshold; signal itself comes from the backend.
  const cfg = useConfig();
  const recommendation = cfg.data?.recommendation ?? RECOMMENDATION_DEFAULTS;
  const signal = bookSignal(book);
  const summary = percentileSummary(book);
  const distance = targetDistance(book);
  const s = book.stats;
  const shippingNote =
    s.current_best_shipping_minor == null && s.shipping_estimate_minor != null
      ? `Shipping for current row unknown; using observed median ${formatMoneyMinor(s.shipping_estimate_minor, book.currency)} for ranking.`
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
          {book.target_price_minor != null ? (
            <p>
              <span className="text-muted-foreground">Target:</span>{" "}
              <span className="font-medium">
                {formatMoneyMinor(book.target_price_minor, book.currency)}
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
