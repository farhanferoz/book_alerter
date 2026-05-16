/* eslint-disable react-refresh/only-export-components */
// Column definitions for the dashboard book table.
//   cover · title+author · best price · shipping · signal · percentile
//   mini-bars (1m/3m/12m, sort key = 3m rank) · days · last seen · actions
// Per-row actions (refetch/archive/delete) are wired via BookRowMenu;
// mute remains detail-page-only.

import type { ColumnDef } from "@tanstack/react-table";
import { Link } from "react-router-dom";

import {
  displaySourceLabel,
  formatCondition,
  formatMoneyMinor,
  formatRelativeTime,
  formatShippingMinor,
  isBookfinderSourcedLabel,
} from "@/lib/format";
import type { Book } from "@/hooks/useBooks";
import { BookRowMenu } from "./BookRowMenu";
import { CoverImage } from "./CoverImage";
import { rank3mOrInf } from "@/lib/windows";
import { MiniBars } from "./MiniBars";
import { SignalPill, bookSignal, type Signal } from "./signal";

function ConditionPill({ condition }: { condition: string | null }) {
  if (!condition) return null;
  return (
    <span className="ml-1 inline-flex items-center rounded-sm border border-border px-1 py-px text-[10px] uppercase text-muted-foreground">
      {formatCondition(condition)}
    </span>
  );
}

function SourceBadge({
  source,
  seller,
}: {
  source: string | null;
  seller?: string | null;
}) {
  if (!source) return null;
  const label = displaySourceLabel(source, seller);
  return (
    <span className="mr-1 inline-flex items-center rounded-sm bg-muted px-1.5 py-px text-[10px] font-medium uppercase text-muted-foreground">
      {label}
      {isBookfinderSourcedLabel(source) && (
        <span className="ml-1 text-[9px] font-normal normal-case text-muted-foreground/70">
          via bookfinder
        </span>
      )}
    </span>
  );
}

// The signal pill reads `book.stats.signal` directly — the backend
// computes it once with the live `RecommendationConfig`, so the FE never
// re-derives and can't drift from what the alert dispatcher will fire.
export function buildBookColumns(): ColumnDef<Book>[] {
  return [
  {
    id: "cover",
    header: "",
    cell: ({ row }) => (
      <CoverImage
        src={row.original.cover_url}
        className="h-10 w-7 rounded-sm"
      />
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
            <SourceBadge
              source={b.stats.current_best_source}
              seller={b.stats.current_best_seller}
            />
            <ConditionPill condition={b.stats.current_best_condition} />
          </div>
        </div>
      );
    },
  },
  {
    id: "shipping",
    accessorFn: (b) => b.stats.current_best_shipping_minor ?? -1,
    header: "Shipping",
    cell: ({ row }) => {
      const b = row.original;
      return (
        <span className="tabular-nums text-muted-foreground">
          {formatShippingMinor(b.stats.current_best_shipping_minor, b.currency)}
        </span>
      );
    },
  },
  {
    id: "signal",
    accessorFn: (b) => bookSignal(b),
    header: "Signal",
    cell: ({ getValue }) => <SignalPill signal={getValue<Signal>()} />,
  },
  {
    id: "percentile",
    accessorFn: rank3mOrInf,
    header: "Percentile",
    cell: ({ row }) => <MiniBars book={row.original} />,
    sortingFn: (a, b) => {
      const av = a.getValue<number>("percentile");
      const bv = b.getValue<number>("percentile");
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
    accessorFn: (b) => b.stats.last_polled_at ?? "",
    header: "Last seen",
    cell: ({ row }) => (
      <span className="text-muted-foreground">
        {formatRelativeTime(row.original.stats.last_polled_at)}
      </span>
    ),
  },
  {
    id: "actions",
    header: "",
    cell: ({ row }) => (
      <div className="flex justify-end">
        <BookRowMenu book={row.original} />
      </div>
    ),
    enableSorting: false,
  },
  ];
}
