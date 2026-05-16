// Snapshot card — current best total + source + condition + age.

import type { Book } from "@/hooks/useBook";
import {
  displaySourceLabel,
  formatCondition,
  formatMoneyMinor,
  formatRelativeTime,
} from "@/lib/format";

export function SnapshotCard({ book }: { book: Book }) {
  const s = book.stats;
  const hasObs = s.current_best_total_minor != null;
  return (
    <div className="rounded-md border border-border bg-card p-4">
      <h2 className="text-xs font-medium uppercase text-muted-foreground">
        Current best
      </h2>
      {hasObs ? (
        <>
          <p className="mt-1 text-2xl font-semibold">
            {formatMoneyMinor(s.current_best_total_minor, book.currency)}
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            {displaySourceLabel(s.current_best_source, s.current_best_seller)}
            {" · "}
            {formatCondition(s.current_best_condition ?? "unknown")}
            {s.current_best_shipping_minor === 0
              ? " · free shipping"
              : s.current_best_shipping_minor != null
                ? ` · incl. ${formatMoneyMinor(s.current_best_shipping_minor, book.currency)} shipping`
                : " · shipping unknown"}
          </p>
          <p className="mt-2 text-xs text-muted-foreground">
            Last polled {formatRelativeTime(s.last_polled_at)}
            {s.last_observed_at &&
              s.last_observed_at !== s.last_polled_at &&
              ` · price last changed ${formatRelativeTime(s.last_observed_at)}`}
          </p>
          {s.current_best_url && (
            <a
              href={s.current_best_url}
              target="_blank"
              rel="noreferrer noopener"
              className="mt-2 inline-block text-xs text-primary hover:underline"
            >
              Open offer ↗
            </a>
          )}
        </>
      ) : (
        <p className="mt-1 text-sm text-muted-foreground">No observations yet.</p>
      )}
    </div>
  );
}
