// Source breakdown — every distinct *price point* in the most-recent scrape
// per `(source, condition)`. A single marketplace seller (e.g. World of
// Books) can list multiple physical copies at different prices under a
// single product URL, so URL-based dedup collapses them; keying on
// `total_minor` surfaces all alive prices and lets the table reconcile
// with the current-best card when the cheapest copy isn't the first row
// in the response.

import { useMemo } from "react";

import type { PriceObservation } from "@/hooks/useBook";
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
function latestPerGroup(observations: PriceObservation[]): PriceObservation[] {
  // Pass 1: find the most recent observed_at per (source, condition). ISO
  // 8601 strings sort correctly with `>`.
  const latestTs = new Map<string, string>();
  for (const o of observations) {
    if (o.source === "keepa") continue;
    const key = `${o.source}::${o.condition}`;
    const cur = latestTs.get(key);
    if (cur === undefined || o.observed_at > cur) {
      latestTs.set(key, o.observed_at);
    }
  }
  // Pass 2: keep every price point in the latest snapshot for its
  // (source, condition). Two copies at identical prices collapse (same
  // offer scraped twice); copies at different prices each get a row.
  // Drops stale historical listings whose observed_at doesn't match.
  const seen = new Map<string, PriceObservation>();
  for (const o of observations) {
    if (o.source === "keepa") continue;
    const groupKey = `${o.source}::${o.condition}`;
    if (o.observed_at !== latestTs.get(groupKey)) continue;
    const rowKey = `${groupKey}::${o.total_minor}`;
    if (!seen.has(rowKey)) seen.set(rowKey, o);
  }
  // Cheapest first within each source group so the row that matches the
  // current-best card sits on top.
  return [...seen.values()].sort((a, b) => {
    const s = a.source.localeCompare(b.source);
    if (s !== 0) return s;
    return a.total_minor - b.total_minor;
  });
}

export function SourceBreakdown({
  observations,
}: {
  observations: PriceObservation[];
}) {
  const rows = useMemo(() => latestPerGroup(observations), [observations]);

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
            {rows.map((o) => (
              <TableRow key={o.id}>
                <TableCell className="font-medium">
                  <span className="uppercase">
                    {displaySourceLabel(o.source, o.seller)}
                  </span>
                  {isBookfinderSourcedLabel(o.source) && (
                    <div className="text-[10px] font-normal text-muted-foreground/70">
                      via bookfinder
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
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {formatRelativeTime(o.observed_at)}
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
            ))}
          </TableBody>
        </Table>
      )}
    </div>
  );
}
