/* eslint-disable react-refresh/only-export-components */
// Column definitions for the dashboard table.
//   cover · title+subtitle · signal · best price · shipping · percentile
//   mini-bars (1m/3m/12m, sort key = 3m rank) · days · last seen · actions
// Signal sits right after the title on purpose: it is the one column the
// dashboard exists to show, and with the alerts rail open a laptop-width
// viewport only fits the first three or four columns before the table
// scrolls horizontally. The title cell is width-capped for the same reason.
// Per-row actions (refetch/archive/delete) are wired via ItemRowMenu;
// mute remains detail-page-only.
//
// `buildColumnsFromItem` defines every column exactly once against `Item`
// (see `@/lib/item`) and `buildItemColumns()` is the one entry point both
// dashboards use — both `pages/Dashboard.tsx` and `pages/ProductsDashboard.tsx`
// read `Item[]` (via `useItems("book" | "product", …)`), so no per-kind
// conversion is needed at this layer.
//
// D40: `buildBookColumns()` (a `ColumnDef<Book>[]` wrapper that converted
// each row via `bookToItem` before rendering) used to be the second entry
// point, for the era `Dashboard.tsx` still fetched `Book[]` through
// `useBooks`. It's gone now that `Dashboard.tsx` fetches `Item[]` directly
// through `useItems("book", …)` — same migration that deleted `useBooks.ts`
// (see that file's git history / `lib/item.ts`'s `SIGNAL_ORDER` export).

import type { ColumnDef } from "@tanstack/react-table";
import { BookIcon, PackageIcon } from "lucide-react";
import { Link } from "react-router-dom";

import {
  displaySourceLabel,
  formatCondition,
  formatMoneyMinor,
  formatRelativeTime,
  formatShippingMinor,
  formatShippingMinorWithEstimate,
  isBookfinderSourcedLabel,
} from "@/lib/format";
import {
  itemHref,
  sortableTotalMinor,
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
          <div className="min-w-[12rem] max-w-[24rem]">
            <div className="flex items-center gap-1.5">
              <Link
                to={itemHref(item)}
                className="line-clamp-2 font-medium text-foreground hover:underline"
                title={item.title}
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
              {/* F4: "failed" is the state that never self-heals (the
                  retry job and the scraper backfill both filter on
                  "pending", and there's no title field on the patch
                  endpoint to fix it by hand) -- so it's the one that most
                  needs a visible, distinct-from-"pending" indicator. The
                  placeholder title stays forever once this fires. */}
              {item.kind === "product" && item.metadata_status === "failed" && (
                <span
                  className="inline-flex items-center rounded-sm bg-destructive/10 px-1 py-px text-[9px] font-medium uppercase text-destructive"
                  title="Amazon title/image lookup gave up after repeated failures — this product keeps its placeholder title/image until it's re-added or fixed by hand"
                >
                  Failed
                </span>
              )}
              {/* The chip replaced a bare red dot, whose meaning was visible
                  only on hover and whose `aria-label` carried the error text.
                  The detail comes back as real `sr-only` text rather than an
                  `aria-label`: this span has no role, so it maps to
                  `generic`, where ARIA prohibits naming and the label may be
                  dropped. `title` stays for the sighted hover case. */}
              {item.last_scrape_error && (
                <span
                  className="inline-flex shrink-0 items-center rounded-sm bg-destructive/10 px-1 py-px text-[9px] font-medium uppercase text-destructive"
                  title={`Last scrape error: ${item.last_scrape_error}`}
                >
                  Scrape failed
                  <span className="sr-only">: {item.last_scrape_error}</span>
                </span>
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
      id: "signal",
      accessorFn: (row) => bookSignal(toItem(row)),
      header: "Signal",
      cell: ({ getValue }) => <SignalPill signal={getValue<Signal>()} />,
    },
    {
      id: "best_price",
      accessorFn: (row) => sortableTotalMinor(toItem(row).stats),
      header: "Best price",
      cell: ({ row }) => {
        const item = toItem(row.original);
        const s = item.stats;
        // F5: headline the effective total under Prime, same as
        // `SnapshotCard` on the detail page -- `current_best_total_minor`
        // can still carry an observed paid-shipping figure the Prime rule
        // overrides, so the raw total is stale (not just uncertain) in
        // that one case. The cascade-estimate case keeps the raw
        // (item-only) price for the same reason `SnapshotCard` does: that
        // number is a guess, not a fact, so it isn't headlined as a total.
        const displayTotal = s.prime_applied
          ? s.current_effective_total_minor
          : s.current_best_total_minor;
        return (
          <div>
            <span className="font-medium">
              {formatMoneyMinor(displayTotal, item.currency)}
            </span>
            <div className="mt-0.5 flex flex-wrap items-center gap-x-1">
              <SourceBadge
                source={s.current_best_source}
                seller={s.current_best_seller}
              />
              <ConditionPill condition={s.current_best_condition} />
              {s.prime_applied && (
                <span className="text-[9px] font-medium uppercase text-muted-foreground">
                  Prime
                </span>
              )}
            </div>
          </div>
        );
      },
    },
    {
      id: "shipping",
      // F3: unknown shipping must never sort as cheaper than free (D20/D34)
      // -- `?? -1` put it below £0.00. Fall back to the estimate pence
      // value when there's no observed figure, matching what the cell
      // actually displays (`~+£2.80*`), and only fall through to "sorts
      // last" for a row with no live offer at all (mirrors every other
      // comparator in this file via `sortableTotalMinor`/`rank3mOrInf`).
      accessorFn: (row) => {
        const s = toItem(row).stats;
        return (
          s.current_best_shipping_minor ??
          s.shipping_estimate_minor ??
          Number.MAX_SAFE_INTEGER
        );
      },
      header: "Shipping",
      cell: ({ row }) => {
        const item = toItem(row.original);
        const s = item.stats;
        // F5: read the backend's flags directly (D10) instead of
        // re-deriving "is this imputed" from the raw fields, and disclose
        // the Prime rule -- previously this cell showed a plain `+£2.80`
        // for a Prime-waived Amazon offer with no indication the charge
        // isn't actually being paid.
        if (s.prime_applied) {
          return (
            <span
              className="tabular-nums text-muted-foreground"
              title="Free under Amazon Prime — this offer's own shipping charge, if any, is waived"
            >
              {formatShippingMinor(0, item.currency)}
            </span>
          );
        }
        return (
          <span
            className="tabular-nums text-muted-foreground"
            title={
              s.shipping_is_estimate
                ? `Shipping unknown for this listing; using observed median ${formatMoneyMinor(
                    s.shipping_estimate_minor,
                    item.currency,
                  )} for signal & percentile (* marks the estimate).`
                : undefined
            }
          >
            {formatShippingMinorWithEstimate(
              s.current_best_shipping_minor,
              s.shipping_estimate_minor,
              item.currency,
            )}
          </span>
        );
      },
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

export function buildItemColumns(): ColumnDef<Item>[] {
  return buildColumnsFromItem<Item>((item) => item);
}
