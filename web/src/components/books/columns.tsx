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
  formatShippingMinorWithEstimate,
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
    cell: ({ row }) => {
      const b = row.original;
      return (
        <div className="min-w-[12rem]">
          <div className="flex items-center gap-1.5">
            <Link
              to={`/books/${b.id}`}
              className="font-medium text-foreground hover:underline"
            >
              {b.title}
            </Link>
            {b.last_scrape_error && (
              // Inline red dot with the truncated error in the native tooltip.
              // Hover for the message; clicking the row navigates to the
              // detail page where the full error and last-attempt timestamp
              // can be surfaced more richly later.
              <span
                role="img"
                aria-label={`Scrape error: ${b.last_scrape_error}`}
                title={`Last scrape error: ${b.last_scrape_error}`}
                className="inline-block h-2 w-2 rounded-full bg-red-500"
              />
            )}
          </div>
          <div className="text-xs text-muted-foreground">{b.author}</div>
        </div>
      );
    },
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
      const imputed =
        b.stats.current_best_shipping_minor == null &&
        b.stats.shipping_estimate_minor != null;
      return (
        <span
          className="tabular-nums text-muted-foreground"
          title={
            imputed
              ? `Shipping unknown for this listing; using observed median ${formatMoneyMinor(
                  b.stats.shipping_estimate_minor,
                  b.currency,
                )} for signal & percentile (* marks the estimate).`
              : undefined
          }
        >
          {formatShippingMinorWithEstimate(
            b.stats.current_best_shipping_minor,
            b.stats.shipping_estimate_minor,
            b.currency,
          )}
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
