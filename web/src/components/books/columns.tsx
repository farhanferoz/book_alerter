/* eslint-disable react-refresh/only-export-components */
// Column definitions for the dashboard table.
//   cover · title+subtitle · best price · shipping · signal · percentile
//   mini-bars (1m/3m/12m, sort key = 3m rank) · days · last seen · actions
// Per-row actions (refetch/archive/delete) are wired via ItemRowMenu;
// mute remains detail-page-only.
//
// `buildColumnsFromItem` defines every column exactly once against `Item`
// (see `@/lib/item`) and is driven by both entry points below:
//   - `buildBookColumns()` keeps its original `(): ColumnDef<Book>[]`
//     signature unchanged — `pages/Dashboard.tsx` still passes it `Book[]`
//     data and must not change behaviour — converting each row to an
//     `Item` internally via `bookToItem` before rendering.
//   - `buildItemColumns()` is new: the products dashboard's data is
//     already `Item[]` (via `useItems("product")`), so no conversion is
//     needed there.

import type { ColumnDef } from "@tanstack/react-table";
import { BookIcon, PackageIcon } from "lucide-react";
import { Link } from "react-router-dom";

import {
  displaySourceLabel,
  formatCondition,
  formatMoneyMinor,
  formatRelativeTime,
  formatShippingMinorWithEstimate,
  isBookfinderSourcedLabel,
} from "@/lib/format";
import {
  bookToItem,
  itemHref,
  sortableTotalMinor,
  type Book,
  type Item,
} from "@/lib/item";
import { rank3mOrInf } from "@/lib/windows";
import { ItemRowMenu } from "./ItemRowMenu";
import { CoverImage } from "./CoverImage";
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

// The signal pill reads `item.stats.signal` directly — the backend
// computes it once with the live `RecommendationConfig`, so the FE never
// re-derives and can't drift from what the alert dispatcher will fire.
function buildColumnsFromItem<TRow>(toItem: (row: TRow) => Item): ColumnDef<TRow>[] {
  return [
    {
      id: "cover",
      header: "",
      cell: ({ row }) => {
        const item = toItem(row.original);
        return (
          <CoverImage
            src={item.imageUrl}
            className="h-10 w-7 rounded-sm"
            fallbackIcon={item.kind === "product" ? PackageIcon : BookIcon}
          />
        );
      },
      enableSorting: false,
    },
    {
      id: "title",
      accessorFn: (row) => toItem(row).title,
      header: "Title",
      cell: ({ row }) => {
        const item = toItem(row.original);
        return (
          <div className="min-w-[12rem]">
            <div className="flex items-center gap-1.5">
              <Link
                to={itemHref(item)}
                className="font-medium text-foreground hover:underline"
              >
                {item.title}
              </Link>
              {item.kind === "product" && item.metadata_status === "pending" && (
                <span
                  className="inline-flex items-center rounded-sm bg-muted px-1 py-px text-[9px] font-medium uppercase text-muted-foreground"
                  title="Title/image not confirmed yet — filled in by the next successful scrape or metadata retry"
                >
                  Pending
                </span>
              )}
              {item.last_scrape_error && (
                <span
                  role="img"
                  aria-label={`Scrape error: ${item.last_scrape_error}`}
                  title={`Last scrape error: ${item.last_scrape_error}`}
                  className="inline-block h-2 w-2 rounded-full bg-red-500"
                />
              )}
            </div>
            <div className="text-xs text-muted-foreground">
              {item.subtitle ?? (
                <em>no {item.kind === "product" ? "brand" : "author"}</em>
              )}
            </div>
          </div>
        );
      },
    },
    {
      id: "best_price",
      accessorFn: (row) => sortableTotalMinor(toItem(row).stats),
      header: "Best price",
      cell: ({ row }) => {
        const item = toItem(row.original);
        return (
          <div>
            <span className="font-medium">
              {formatMoneyMinor(item.stats.current_best_total_minor, item.currency)}
            </span>
            <div className="mt-0.5 flex items-center">
              <SourceBadge
                source={item.stats.current_best_source}
                seller={item.stats.current_best_seller}
              />
              <ConditionPill condition={item.stats.current_best_condition} />
            </div>
          </div>
        );
      },
    },
    {
      id: "shipping",
      accessorFn: (row) => toItem(row).stats.current_best_shipping_minor ?? -1,
      header: "Shipping",
      cell: ({ row }) => {
        const item = toItem(row.original);
        const imputed =
          item.stats.current_best_shipping_minor == null &&
          item.stats.shipping_estimate_minor != null;
        return (
          <span
            className="tabular-nums text-muted-foreground"
            title={
              imputed
                ? `Shipping unknown for this listing; using observed median ${formatMoneyMinor(
                    item.stats.shipping_estimate_minor,
                    item.currency,
                  )} for signal & percentile (* marks the estimate).`
                : undefined
            }
          >
            {formatShippingMinorWithEstimate(
              item.stats.current_best_shipping_minor,
              item.stats.shipping_estimate_minor,
              item.currency,
            )}
          </span>
        );
      },
    },
    {
      id: "signal",
      accessorFn: (row) => bookSignal(toItem(row)),
      header: "Signal",
      cell: ({ getValue }) => <SignalPill signal={getValue<Signal>()} />,
    },
    {
      id: "percentile",
      accessorFn: (row) => rank3mOrInf(toItem(row)),
      header: "Percentile",
      cell: ({ row }) => <MiniBars item={toItem(row.original)} />,
      sortingFn: (a, b) => {
        const av = a.getValue<number>("percentile");
        const bv = b.getValue<number>("percentile");
        return av - bv;
      },
    },
    {
      id: "days_of_history",
      accessorFn: (row) => toItem(row).stats.days_of_history,
      header: "Days",
      cell: ({ getValue }) => (
        <span className="text-muted-foreground">{getValue<number>()}</span>
      ),
    },
    {
      id: "last_seen",
      accessorFn: (row) => toItem(row).stats.last_polled_at ?? "",
      header: "Last seen",
      cell: ({ row }) => (
        <span className="text-muted-foreground">
          {formatRelativeTime(toItem(row.original).stats.last_polled_at)}
        </span>
      ),
    },
    {
      id: "actions",
      header: "",
      cell: ({ row }) => (
        <div className="flex justify-end">
          <ItemRowMenu item={toItem(row.original)} />
        </div>
      ),
      enableSorting: false,
    },
  ];
}

export function buildBookColumns(): ColumnDef<Book>[] {
  return buildColumnsFromItem<Book>(bookToItem);
}

export function buildItemColumns(): ColumnDef<Item>[] {
  return buildColumnsFromItem<Item>((item) => item);
}
