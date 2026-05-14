/* eslint-disable react-refresh/only-export-components */
// Column definitions for the dashboard book table.
//
// Columns chosen from design spec (§ "Dashboard (/)" → main table row):
//   cover · title+author · best price (source badge + condition pill) ·
//   signal pill · % vs median (sparkline-coloured) · days of history ·
//   last seen · row actions
//
// Phase 10.1 ships everything except the inline sparkline (Recharts cell)
// and per-row action buttons (refetch, mute) — both land in 10.3+ when the
// surrounding interactions exist. The "% vs median" column is rendered as
// plain coloured text for now; sparkline conversion is a follow-up.

import type { ColumnDef } from "@tanstack/react-table";
import { Link } from "react-router-dom";

import { formatMoneyMinor, formatRelativeTime } from "@/lib/format";
import type { Book } from "@/hooks/useBooks";
import type { RecommendationConfigShape } from "@/hooks/useConfig";
import { SignalPill, approximateSignal, type Signal } from "./signal";

function ConditionPill({ condition }: { condition: string | null }) {
  if (!condition) return null;
  return (
    <span className="ml-1 inline-flex items-center rounded-sm border border-border px-1 py-px text-[10px] uppercase text-muted-foreground">
      {condition.replace(/_/g, " ")}
    </span>
  );
}

function SourceBadge({ source }: { source: string | null }) {
  if (!source) return null;
  return (
    <span className="mr-1 inline-flex items-center rounded-sm bg-muted px-1.5 py-px text-[10px] font-medium uppercase text-muted-foreground">
      {source}
    </span>
  );
}

function percentVsMedian(book: Book): number | null {
  const current = book.stats.current_best_total_minor;
  const median = book.stats.p50_total_minor;
  if (current == null || median == null || median === 0) return null;
  return Math.round(((current - median) / median) * 100);
}

function pctClass(pct: number | null): string {
  if (pct == null) return "text-muted-foreground";
  if (pct <= -15) return "text-green-700 dark:text-green-400 font-medium";
  if (pct <= 0) return "text-green-700 dark:text-green-400";
  if (pct <= 15) return "text-amber-700 dark:text-amber-400";
  return "text-rose-700 dark:text-rose-400";
}

// Built as a factory so `approximateSignal(b, config)` can close over the
// live `RecommendationConfig` (Phase 11.3 lift — was a hard-coded constant
// in `signal.tsx`).
export function buildBookColumns(
  config: RecommendationConfigShape,
): ColumnDef<Book>[] {
  return [
  {
    id: "cover",
    header: "",
    cell: ({ row }) =>
      row.original.cover_url ? (
        <img
          src={row.original.cover_url}
          alt=""
          className="h-10 w-7 rounded-sm object-cover"
          loading="lazy"
        />
      ) : (
        <div className="h-10 w-7 rounded-sm bg-muted" aria-hidden />
      ),
    enableSorting: false,
  },
  {
    id: "title",
    accessorFn: (b) => b.title,
    header: "Title",
    cell: ({ row }) => (
      <div className="min-w-[12rem]">
        <Link
          to={`/books/${row.original.id}`}
          className="font-medium text-foreground hover:underline"
        >
          {row.original.title}
        </Link>
        <div className="text-xs text-muted-foreground">{row.original.author}</div>
      </div>
    ),
  },
  {
    id: "best_price",
    accessorFn: (b) => b.stats.current_best_total_minor ?? Number.MAX_SAFE_INTEGER,
    header: "Best price",
    cell: ({ row }) => {
      const b = row.original;
      return (
        <div>
          <span className="font-medium">
            {formatMoneyMinor(b.stats.current_best_total_minor, b.currency)}
          </span>
          <div className="mt-0.5 flex items-center">
            <SourceBadge source={b.stats.current_best_source} />
            <ConditionPill condition={b.stats.current_best_condition} />
          </div>
        </div>
      );
    },
  },
  {
    id: "signal",
    accessorFn: (b) => approximateSignal(b, config),
    header: "Signal",
    cell: ({ getValue }) => <SignalPill signal={getValue<Signal>()} />,
  },
  {
    id: "pct_vs_median",
    accessorFn: percentVsMedian,
    header: "% vs median",
    cell: ({ getValue }) => {
      const pct = getValue<number | null>();
      return (
        <span className={pctClass(pct)}>
          {pct == null ? "—" : `${pct > 0 ? "+" : ""}${pct}%`}
        </span>
      );
    },
    sortingFn: (a, b) => {
      const av = a.getValue<number | null>("pct_vs_median");
      const bv = b.getValue<number | null>("pct_vs_median");
      if (av == null && bv == null) return 0;
      if (av == null) return 1;
      if (bv == null) return -1;
      return av - bv;
    },
  },
  {
    id: "days_of_history",
    accessorFn: (b) => b.stats.days_of_history,
    header: "Days",
    cell: ({ getValue }) => (
      <span className="text-muted-foreground">{getValue<number>()}</span>
    ),
  },
  {
    id: "last_seen",
    accessorFn: (b) => b.stats.last_observed_at ?? "",
    header: "Last seen",
    cell: ({ row }) => (
      <span className="text-muted-foreground">
        {formatRelativeTime(row.original.stats.last_observed_at)}
      </span>
    ),
  },
  ];
}
