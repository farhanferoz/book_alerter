// Source breakdown — every distinct *price point* in the most-recent scrape
// per `(source, condition)`. A single marketplace seller (e.g. World of
// Books) can list multiple physical copies at different prices under a
// single product URL, so URL-based dedup collapses them; keying on
// `total_minor` surfaces all alive prices and lets the table reconcile
// with the current-best card when the cheapest copy isn't the first row
// in the response.

import { useMemo } from "react";

import type { Item, ItemObservation } from "@/lib/item";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  displaySourceLabel,
  formatCondition,
  formatMoneyMinor,
  formatRelativeTime,
  formatShippingMinor,
  isBookfinderSourcedLabel,
} from "@/lib/format";

// Keepa is a historical price archive, not a vendor — there's no Keepa
// page to click "buy" on. Filter it out of the per-source breakdown so the
// table only lists offers the user could actually transact on. Keepa still
// drives the chart and percentile distribution via its own code paths.
function latestPerGroup(observations: ItemObservation[]): ItemObservation[] {
  // Key off `last_seen` (the most recent scrape that re-confirmed each offer),
  // NOT `observed_at` (first sighting). A stable-but-live offer is deduped on
  // every scrape, so its canonical observed_at is frozen at the first sighting
  // — keying on that would show the offer as days stale, or drop it in favour
  // of a more-recently-CHANGED (but not necessarily cheaper) offer. last_seen
  // reflects the genuine latest scrape. ISO 8601 strings sort correctly with `>`.
  const latestTs = new Map<string, string>();
  for (const o of observations) {
    if (o.source === "keepa") continue;
    const key = `${o.source}::${o.condition}`;
    const cur = latestTs.get(key);
    if (cur === undefined || o.last_seen > cur) {
      latestTs.set(key, o.last_seen);
    }
  }
  // Pass 2: keep every price point present in the latest scrape for its
  // (source, condition). Two copies at identical prices collapse (same
  // offer scraped twice); copies at different prices each get a row.
  // Drops listings no longer in the latest scrape (last_seen doesn't match).
  const seen = new Map<string, ItemObservation>();
  for (const o of observations) {
    if (o.source === "keepa") continue;
    const groupKey = `${o.source}::${o.condition}`;
    if (o.last_seen !== latestTs.get(groupKey)) continue;
    const rowKey = `${groupKey}::${o.total_minor}`;
    if (!seen.has(rowKey)) seen.set(rowKey, o);
  }
  // Cheapest first within each source group so the row that matches the
  // current-best card sits on top.
  return [...seen.values()].sort((a, b) => {
    const s = a.source.localeCompare(b.source);
    if (s !== 0) return s;
    // F12 (D20/D34): an unknown-shipping row's `total_minor` folds to bare
    // price (the "(item only)" cell below), so ranking on it directly can
    // put an unknown-shipping row above a genuinely cheaper fully-delivered
    // one. There's no per-row cascade estimate at this layer to rank on
    // instead -- only the backend-computed current-best OFFER carries one
    // (`item.stats.shipping_estimate_minor`), not every raw observation --
    // so unknown-shipping rows sort after every known-shipping row in the
    // group instead of competing with them on price alone.
    const aUnknown = a.shipping_minor == null;
    const bUnknown = b.shipping_minor == null;
    if (aUnknown !== bUnknown) return aUnknown ? 1 : -1;
    return a.total_minor - b.total_minor;
  });
}

// Pin the row that backs the SnapshotCard's "Current best" — without this,
// the latest-per-group filter can drop it when a newer scrape returned a
// different cheapest offer, leaving the user staring at a snapshot price
// they can't find anywhere in the table below.
//
// Match the full (url, source, condition, seller, total_minor) tuple. A
// single seller (e.g. World of Books on its own dot-com) sometimes lists
// multiple copies at different prices under one product URL — without
// `total_minor` the match is ambiguous and we'd pin the wrong row.
// `book_stats` and `priceobservation` are both read from the same fresh
// view/table on each render, so the tuple should always resolve to
// exactly one observation; if it doesn't, returning null + falling back
// to no-pin is strictly better than pinning a near-miss.
function findCurrentBestObservation(
  item: Item,
  observations: ItemObservation[],
): ItemObservation | null {
  const s = item.stats;
  if (
    s.current_best_url == null ||
    s.current_best_source == null ||
    s.current_best_total_minor == null
  ) {
    return null;
  }
  for (const o of observations) {
    if (
      o.url === s.current_best_url &&
      o.source === s.current_best_source &&
      o.condition === s.current_best_condition &&
      o.seller === s.current_best_seller &&
      o.total_minor === s.current_best_total_minor
    ) {
      return o;
    }
  }
  return null;
}

export function SourceBreakdown({
  item,
  observations,
}: {
  item: Item;
  observations: ItemObservation[];
}) {
  // Single memoized pass: derive both `rows` and `currentBestId` together
  // so they cannot disagree about which observation is "Current best"
  // across re-renders. The current-best row ALWAYS sits at position 0
  // — whether it was already in `latest` or had to be prepended — so
  // the highlighted row never jumps between the top and the middle of
  // the table between scrapes.
  const { rows, currentBestId } = useMemo(() => {
    const latest = latestPerGroup(observations);
    const currentBest = findCurrentBestObservation(item, observations);
    if (currentBest == null) {
      return { rows: latest, currentBestId: null };
    }
    const rest = latest.filter((o) => o.id !== currentBest.id);
    return {
      rows: [currentBest, ...rest],
      currentBestId: currentBest.id,
    };
  }, [item, observations]);

  return (
    <div className="rounded-md border border-border bg-card">
      <div className="border-b border-border p-4">
        <h2 className="text-xs font-medium uppercase text-muted-foreground">
          Source breakdown
        </h2>
        <p className="mt-1 text-xs text-muted-foreground">
          Every distinct price point from the latest scrape per source × condition.
        </p>
      </div>
      {rows.length === 0 ? (
        <p className="p-4 text-sm text-muted-foreground">
          No observations yet.
        </p>
      ) : (
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Source</TableHead>
              <TableHead>Condition</TableHead>
              <TableHead className="text-right">Item</TableHead>
              <TableHead className="text-right">Shipping</TableHead>
              <TableHead className="text-right">Total</TableHead>
              <TableHead>Observed</TableHead>
              <TableHead>Link</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((o) => {
              const isCurrentBest = o.id === currentBestId;
              return (
                <TableRow
                  key={o.id}
                  className={isCurrentBest ? "bg-primary/5" : undefined}
                >
                  <TableCell className="font-medium">
                    <span className="uppercase">
                      {displaySourceLabel(o.source, o.seller)}
                    </span>
                    {isBookfinderSourcedLabel(o.source) && (
                      <div className="text-[10px] font-normal text-muted-foreground/70">
                        via bookfinder
                      </div>
                    )}
                    {isCurrentBest && (
                      <div className="text-[10px] font-medium text-primary">
                        Current best
                        {/* T2.3: these two flags live on `item.stats` (the
                            current-best offer only), not on each raw
                            observation — so they can only annotate this one
                            pinned row. `prime_applied` and
                            `shipping_is_estimate` are mutually exclusive
                            (see `stats.effective_shipping`). Consumed as-is
                            per D10 — never re-derived from the row's raw
                            shipping figure. */}
                        {item.stats.prime_applied && (
                          <span className="ml-1 font-normal text-muted-foreground">
                            · Prime
                          </span>
                        )}
                        {item.stats.shipping_is_estimate && (
                          <span className="ml-1 font-normal text-muted-foreground">
                            · est.
                          </span>
                        )}
                      </div>
                    )}
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {formatCondition(o.condition)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums">
                    {formatMoneyMinor(o.price_minor, o.currency)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums text-muted-foreground">
                    {formatShippingMinor(o.shipping_minor, o.currency)}
                  </TableCell>
                  <TableCell className="text-right tabular-nums font-medium">
                    {formatMoneyMinor(o.total_minor, o.currency)}
                    {o.shipping_minor == null && (
                      <span className="ml-1 align-middle text-[10px] font-normal text-muted-foreground">
                        (item only)
                      </span>
                    )}
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {formatRelativeTime(o.last_seen)}
                  </TableCell>
                  <TableCell>
                    <a
                      href={o.url}
                      target="_blank"
                      rel="noreferrer noopener"
                      className="text-primary hover:underline"
                    >
                      Open ↗
                    </a>
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
