// Recent runs table — lazy-loaded when the parent `<SourceCard>`'s "Recent
// runs" accordion is expanded. Fields mirror `SourceRunOut` on the wire:
// status / started / finished / attempted / succeeded / error.
//
// `error_traceback` is intentionally omitted from the API; the `error_message`
// column surfaces the short string only — full tracebacks live in structured
// logs (`source.run.exception`).

import { useSourceRuns } from "@/hooks/useSources";
import { formatDateTime, formatRelativeTime } from "@/lib/format";
import { formatErrorMessage } from "@/lib/utils";
import { Skeleton } from "@/components/ui/skeleton";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";

export type SourceRunsTableProps = {
  name: string;
  enabled: boolean;
};

const STATUS_TONE: Record<string, string> = {
  running: "text-blue-600 dark:text-blue-400",
  success: "text-emerald-600 dark:text-emerald-400",
  partial: "text-amber-600 dark:text-amber-400",
  error: "text-destructive",
};

export function SourceRunsTable({ name, enabled }: SourceRunsTableProps) {
  const runs = useSourceRuns(name, { enabled });

  if (!enabled) return null;

  if (runs.isPending) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-6 w-full" tone="muted" />
        ))}
      </div>
    );
  }

  if (runs.error) {
    return (
      <p className="text-xs text-destructive">
        Failed to load runs: {formatErrorMessage(runs.error)}
      </p>
    );
  }

  const rows = runs.data ?? [];
  if (rows.length === 0) {
    return <p className="text-xs text-muted-foreground">No runs yet</p>;
  }

  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead>Status</TableHead>
          <TableHead>Started</TableHead>
          <TableHead>Finished</TableHead>
          <TableHead className="text-right">Attempted</TableHead>
          <TableHead className="text-right">Succeeded</TableHead>
          <TableHead>Error</TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {rows.map((run) => (
          <TableRow key={run.id}>
            <TableCell>
              <span
                className={`text-xs font-medium ${
                  STATUS_TONE[run.status] ?? "text-muted-foreground"
                }`}
              >
                {run.status}
              </span>
            </TableCell>
            <TableCell title={formatDateTime(run.started_at)}>
              <span className="text-xs">
                {formatRelativeTime(run.started_at)}
              </span>
            </TableCell>
            <TableCell
              title={run.finished_at ? formatDateTime(run.finished_at) : ""}
            >
              <span className="text-xs">
                {run.finished_at
                  ? formatRelativeTime(run.finished_at)
                  : "—"}
              </span>
            </TableCell>
            <TableCell className="text-right text-xs">
              {run.books_attempted}
            </TableCell>
            <TableCell className="text-right text-xs">
              {run.books_succeeded}
            </TableCell>
            <TableCell className="max-w-[18rem] truncate text-xs text-muted-foreground">
              {run.error_message ?? "—"}
            </TableCell>
          </TableRow>
        ))}
      </TableBody>
    </Table>
  );
}
