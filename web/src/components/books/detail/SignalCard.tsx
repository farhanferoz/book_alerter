// Signal card — pill + target distance + percentile context.
//
// Reuses `approximateSignal` from Phase 10.1 (same client-side approximation
// as the dashboard column). The percentile rank shown here is a coarse bucket
// derived from the three materialised quantiles in `BookStats` — see
// `bucketPercentile` below. The real `BookStats.percentile_at()` lives on the
// backend and isn't on the wire (Phase 11.3 lifts the real recommendation
// config + percentile data to the client).

import type { Book } from "@/hooks/useBook";
import { formatMoneyMinor } from "@/lib/format";
import { SignalPill, approximateSignal } from "@/components/books/signal";
import { useConfig, RECOMMENDATION_DEFAULTS } from "@/hooks/useConfig";

// Coarse percentile bucket from p25/p50/p75. Returns a human-readable string.
// The backend's `percentile_at()` does proper linear interpolation against
// `sorted_totals`; we approximate with the three exposed quantiles and a
// "between X and Y" range. INSUFFICIENT_DATA branches are caller-handled.
function bucketPercentile(book: Book): string | null {
  const s = book.stats;
  const current = s.current_best_total_minor;
  if (current == null) return null;
  if (s.p25_total_minor != null && current <= s.p25_total_minor) {
    return "≤ P25 (cheap quartile)";
  }
  if (s.p50_total_minor != null && current <= s.p50_total_minor) {
    return "P25–P50 (below median)";
  }
  if (s.p75_total_minor != null && current <= s.p75_total_minor) {
    return "P50–P75 (above median)";
  }
  return "> P75 (top quartile)";
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
  // Config drives `min_observations_for_signal`; fall through to spec
  // defaults while /api/config is unavailable (Phase 11.3 lift).
  const cfg = useConfig();
  const recommendation = cfg.data?.recommendation ?? RECOMMENDATION_DEFAULTS;
  const signal = approximateSignal(book, recommendation);
  const bucket = bucketPercentile(book);
  const distance = targetDistance(book);
  const s = book.stats;

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
          {bucket && (
            <p className="text-xs text-muted-foreground">
              Current price sits {bucket} of {s.observation_count} obs over{" "}
              {s.days_of_history} days.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
