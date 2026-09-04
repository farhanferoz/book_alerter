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
 * `imageUrl` (`cover_url` for books, `image_url` for products) and a
 * hoisted `signal` (`stats.signal`) alongside every other field of the
 * underlying response. `kind` narrows the union the usual TS way:
 * `item.kind === "book"` gives back `isbn13`, `format`, … for free;
 * `"product"` gives back `asin`, `brand`, `track_used`, ….
 */
export type Item =
  | (Book & { kind: "book"; imageUrl: string | null; signal: ItemStats["signal"] })
  | (Product & { kind: "product"; imageUrl: string | null; signal: ItemStats["signal"] });

export function bookToItem(book: Book): Item {
  return { ...book, kind: "book", imageUrl: book.cover_url, signal: book.stats.signal };
}

export function productToItem(product: Product): Item {
  return {
    ...product,
    kind: "product",
    imageUrl: product.image_url,
    signal: product.stats.signal,
  };
}

/** Detail route for an item — mirrors `alertItemHref` in `AlertItem.tsx`. */
export function itemHref(item: Pick<Item, "kind" | "id">): string {
  const segment = item.kind === "product" ? "products" : "books";
  return `/${segment}/${item.id}`;
}
