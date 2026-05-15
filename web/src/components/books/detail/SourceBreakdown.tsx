// Source breakdown — latest observation per `(source, condition)` group.
//
// Observations come back newest-first; we walk once, keeping the first row
// we see per group key. That's the "latest per group" the spec calls for
// without re-sorting.

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
  const seen = new Map<string, PriceObservation>();
  for (const o of observations) {
    if (o.source === "keepa") continue;
    const key = `${o.source}::${o.condition}`;
    if (!seen.has(key)) seen.set(key, o);
  }
  return [...seen.values()].sort((a, b) => a.source.localeCompare(b.source));
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
          Latest observation per source × condition.
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
