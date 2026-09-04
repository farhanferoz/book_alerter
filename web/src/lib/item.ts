// `Item` is the shared shape T5.2–T5.4 render dashboard/detail components
// against, instead of growing a second (product) copy of every book
// component — the whole point is that a component written against `Item`
// renders either kind without knowing which it has.
//
// It's a book or a product tagged with `kind`, intersected with the FULL
// underlying `BookOut`/`ProductOut` shape (not a hand-picked subset) so an
// additive backend field on either response appears on `Item` automatically
// — this file only needs to change for a genuinely new cross-kind concept,
// not because the backend added a field to one side.
//
// Read before touching this: `web/src/hooks/useAlerts.ts` (kind derived
// from the generated schema, the `AlertRef`/`alertRef()` pattern) and
// `web/src/components/alerts/AlertItem.tsx` (one row renders a book or
// product alert identically off `item_kind`/`item_id`/`title` — the shape
// of abstraction this file generalises to books/products themselves).

import type { components } from "@/api/schema";
import { bookSignal } from "@/components/books/signal";
import { rank3mOrInf } from "@/lib/windows";

export type Book = components["schemas"]["BookOut"];
export type Product = components["schemas"]["ProductOut"];

// Both `BookOut.stats` and `ProductOut.stats` are already the same wire
// shape (one backend dataclass serves both kinds — see `BookStatsOut`'s own
// docstring: "book_id and item_id carry the same value... New product
// callers should read item_id"). Re-exported under a kind-neutral name.
export type ItemStats = components["schemas"]["BookStatsOut"];
export type Signal = NonNullable<ItemStats["signal"]>;

// Derived from the generated schema, not hand-written, so it can't drift
// from the backend's `ItemKind` enum — the same derivation `useAlerts.ts`
// uses for its own `ItemKind` (`Alert["item_kind"]`, which resolves to this
// same schema entry).
export type ItemKind = components["schemas"]["ItemKind"];

/**
 * A book or a product, tagged with `kind` and carrying a normalised
 * `imageUrl` (`cover_url` for books, `image_url` for products), `subtitle`
 * (`author` for books — always present; `brand` for products — may be
 * null), and a hoisted `signal` (`stats.signal`) alongside every other
 * field of the underlying response. `kind` narrows the union the usual TS
 * way: `item.kind === "book"` gives back `isbn13`, `format`, … for free;
 * `"product"` gives back `asin`, `brand`, `track_used`, ….
 */
export type Item =
  | (Book & {
      kind: "book";
      imageUrl: string | null;
      subtitle: string;
      signal: ItemStats["signal"];
    })
  | (Product & {
      kind: "product";
      imageUrl: string | null;
      subtitle: string | null;
      signal: ItemStats["signal"];
    });

export function bookToItem(book: Book): Item {
  return {
    ...book,
    kind: "book",
    imageUrl: book.cover_url,
    subtitle: book.author,
    signal: book.stats.signal,
  };
}

export function productToItem(product: Product): Item {
  return {
    ...product,
    kind: "product",
    imageUrl: product.image_url,
    subtitle: product.brand,
    signal: product.stats.signal,
  };
}

/** Detail route for an item — mirrors `alertItemHref` in `AlertItem.tsx`. */
export function itemHref(item: Pick<Item, "kind" | "id">): string {
  const segment = item.kind === "product" ? "products" : "books";
  return `/${segment}/${item.id}`;
}

/** Dashboard/list route for an item's kind. Not simply `itemHref` minus the
 * id — the books dashboard is mounted at `/` (see `App.tsx`), not `/books`
 * (there is no `/books` route), while the products dashboard is at
 * `/products`. Used after a delete to navigate back to the right list. */
export function itemListHref(kind: ItemKind): string {
  return kind === "product" ? "/products" : "/";
}

// `/api/books/{book_id}/...` and `/api/products/{product_id}/...` are the
// only two endpoint families an item can belong to; the base path and the
// list/detail query keys all key off `kind` as plain data. Shared by
// `useItems.ts`'s hooks, `ItemRowMenu`, `ActionBar`, `SettingsPanel` and
// `KeepaChart` so there's one definition of "books" vs "products", not one
// per component.
export function itemApiBase(kind: ItemKind): "/api/books" | "/api/products" {
  return kind === "product" ? "/api/products" : "/api/books";
}

export function itemListQueryKey(kind: ItemKind): "books" | "products" {
  return kind === "product" ? "products" : "books";
}

export function itemDetailQueryKey(kind: ItemKind): "book" | "product" {
  return kind === "product" ? "product" : "book";
}

/**
 * Sort key for any "cheapest first" ordering.
 *
 * Ranks on `current_best_total_minor` and you rank on a number that folds
 * unknown shipping to zero, so a "£7.99 + unknown delivery" item sorts above
 * an "£8.50, delivered free" one even though it really costs £10.79. That is
 * the same defect class as D20/D34: an unpaid delivery charge treated as
 * free. `current_effective_total_minor` is the price+shipping figure with the
 * cascade estimate applied, which is what the buyer actually pays.
 *
 * A null effective total means we have no shipping signal at all for this
 * item, so it sorts last rather than cheap — an unknown must never
 * masquerade as the best price (D34).
 */
export function sortableTotalMinor(stats: ItemStats): number {
  return stats.current_effective_total_minor ?? Number.MAX_SAFE_INTEGER;
}

// --- Observations ---------------------------------------------------------

export type PriceObservation = components["schemas"]["PriceObservationOut"];
export type ProductObservation = components["schemas"]["ProductObservationOut"];

/**
 * Shared shape for the detail-page history/breakdown components
 * (`HistoryChart`, `SourceBreakdown`). `PriceObservationOut` and
 * `ProductObservationOut` are the same wire type except for their FK field
 * (`book_id` vs `product_id` — verified against `web/src/api/schema.ts`),
 * which neither of those components reads, so this omits it rather than
 * unioning the two FK-bearing types. A `PriceObservation`/`ProductObservation`
 * satisfies this structurally as-is — no mapping function needed.
 */
export type ItemObservation = {
  id: number;
  source: string;
  seller: string | null;
  condition: string;
  price_minor: number;
  currency: string;
  shipping_minor: number | null;
  total_minor: number;
  url: string;
  observed_at: string;
  last_seen: string;
};

// --- Dashboard filter/sort -----------------------------------------------
//
// Shared with the products dashboard (`ProductsDashboard.tsx`) so it gets
// the same signal/status/sort behaviour as the books `Dashboard` without a
// second copy of the switch below. `pages/Dashboard.tsx` keeps its own
// private `applyFilters` — it isn't exported, and that page's behaviour
// must not change in this task — so this is a parallel implementation
// against `Item`, not a literal extraction of that one; a later task that
// re-points `Dashboard.tsx` at `Item` can fold the two together.

/** Kept structurally identical to `@/components/books/BookFilters`'s
 * `BookFiltersValue` on purpose — a `BookFiltersValue` and an `ItemFilters`
 * satisfy each other via TS structural typing, so `BookFilters` (the form)
 * can drive this without a shared named type between `lib/` and
 * `components/`. */
export type ItemFilters = {
  signal: Signal | "ALL";
  status: "active" | "archived" | "bought" | "all";
  sort: "signal" | "best_price" | "percentile" | "last_seen" | "title";
};

const SIGNAL_ORDER: Record<Signal, number> = {
  TARGET_HIT: 0,
  BUY: 1,
  WATCH: 2,
  WAIT: 3,
  INSUFFICIENT_DATA: 4,
};

/** Mirrors `pages/Dashboard.tsx`'s private `applyFilters` — same filter
 * predicates, same sort keys/directions, reusing the same `bookSignal`/
 * `rank3mOrInf` accessors that back the dashboard columns — generalised to
 * `Item[]`. */
export function applyItemFilters(items: Item[], filters: ItemFilters): Item[] {
  let result = items;
  if (filters.signal !== "ALL") {
    result = result.filter((i) => bookSignal(i) === filters.signal);
  }
  if (filters.status !== "all") {
    result = result.filter((i) => i.status === filters.status);
  }
  const sorted = [...result];
  switch (filters.sort) {
    case "signal":
      sorted.sort((a, b) => SIGNAL_ORDER[bookSignal(a)] - SIGNAL_ORDER[bookSignal(b)]);
      break;
    case "best_price":
      sorted.sort((a, b) => {
        const av = a.stats.current_best_total_minor ?? Number.MAX_SAFE_INTEGER;
        const bv = b.stats.current_best_total_minor ?? Number.MAX_SAFE_INTEGER;
        return av - bv;
      });
      break;
    case "percentile":
      sorted.sort((a, b) => rank3mOrInf(a) - rank3mOrInf(b));
      break;
    case "last_seen":
      sorted.sort((a, b) => {
        const av = a.stats.last_polled_at ?? "";
        const bv = b.stats.last_polled_at ?? "";
        return bv.localeCompare(av); // newest first
      });
      break;
    case "title":
      sorted.sort((a, b) => a.title.localeCompare(b.title));
      break;
  }
  return sorted;
}
