// Snapshot card — current best total + source + condition + age.

import type { Item } from "@/lib/item";
import {
  displaySourceLabel,
  formatCondition,
  formatMoneyMinor,
  formatRelativeTime,
} from "@/lib/format";

export function SnapshotCard({ item }: { item: Item }) {
  const s = item.stats;
  const hasObs = s.current_best_total_minor != null;
  // T2.3: `prime_applied` is a hard rule, not a guess (`stats.
  // effective_shipping` — free Amazon-fulfilled delivery under Prime, "not
  // an estimate"), unlike the cascade estimate case below. So when it
  // applies, the raw `current_best_total_minor` (which can still carry an
  // observed paid-shipping figure the Prime rule overrides) is stale —
  // headline `current_effective_total_minor` instead, the number the
  // backend actually ranked on and the buyer actually pays. Verified
  // against a production copy: book 3's Amazon offer carries an observed
  // 280p shipping charge; with Prime on, `current_best_total_minor` stays
  // 1980p while `current_effective_total_minor` correctly drops to 1700p —
  // showing the raw figure here would headline "£19.80" right next to a
  // "Prime (free delivery)" caption, a direct on-screen contradiction.
  // The cascade-estimate case (`shipping_is_estimate`) deliberately keeps
  // showing the item-only price instead of jumping to the guessed total —
  // that number is uncertain, so it isn't presented as a headline fact.
  const displayTotal = s.prime_applied
    ? s.current_effective_total_minor
    : s.current_best_total_minor;
  return (
    <div className="rounded-md border border-border bg-card p-4">
      <h2 className="text-xs font-medium uppercase text-muted-foreground">
        Current best
      </h2>
      {hasObs ? (
        <>
          <p className="mt-1 text-2xl font-semibold">
            {formatMoneyMinor(displayTotal, item.currency)}
            <span className="ml-2 align-middle text-xs font-normal text-muted-foreground">
              {/* Disambiguates the figure: when shipping is null, the displayed
                  "total" is actually item-only. Labelling it as such removes
                  the user's need to mentally cross-reference SourceBreakdown
                  and the chart's effective-total. `prime_applied` means the
                  backend already knows delivery is free even when the raw
                  observed figure is null or non-zero (T2.3 — the flag is
                  authoritative, we don't re-derive it here per D10). */}
              {s.current_best_shipping_minor != null || s.prime_applied
                ? "total"
                : "item only"}
            </span>
          </p>
          <p className="mt-1 text-xs text-muted-foreground">
            {displaySourceLabel(s.current_best_source, s.current_best_seller)}
            {" · "}
            {formatCondition(s.current_best_condition ?? "unknown")}
            {s.prime_applied
              ? " · Prime (free delivery)"
              : s.current_best_shipping_minor === 0
                ? " · free shipping"
                : s.current_best_shipping_minor != null
                  ? ` · incl. ${formatMoneyMinor(s.current_best_shipping_minor, item.currency)} shipping`
                  : s.shipping_is_estimate && s.shipping_estimate_minor != null
                    ? ` · shipping not listed (ranked using ~${formatMoneyMinor(s.shipping_estimate_minor, item.currency)} estimate)`
                    : " · shipping not listed"}
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
