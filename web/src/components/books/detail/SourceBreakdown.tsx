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
import { formatMoneyMinor, formatRelativeTime } from "@/lib/format";

function latestPerGroup(observations: PriceObservation[]): PriceObservation[] {
  const seen = new Map<string, PriceObservation>();
  for (const o of observations) {
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
              <TableHead>Total</TableHead>
              <TableHead>Observed</TableHead>
              <TableHead>Link</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {rows.map((o) => (
              <TableRow key={o.id}>
                <TableCell className="font-medium uppercase">
                  {o.source}
                </TableCell>
                <TableCell className="text-muted-foreground">
                  {o.condition.replace(/_/g, " ")}
                </TableCell>
                <TableCell>{formatMoneyMinor(o.total_minor, o.currency)}</TableCell>
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
