// Kind-agnostic wrappers over the books/products list and single-item
// endpoints:
//
//   useItems(kind, params)  → GET /api/books  or  /api/products, → Item[]
//   useItem(kind, id)       → GET /api/books/{id}  or  /api/products/{id}, → Item
//
// Each hook here makes exactly ONE `useQuery` call, with `kind` used only
// as data to pick the endpoint and response mapper inside `queryFn`.
// `kind === "book" ? useSomeBookHook() : useSomeProductHook()` — call a
// DIFFERENT hook depending on a runtime value — is exactly the shape
// `react-hooks/rules-of-hooks` forbids: a caller could re-render with a
// different `kind`, and the hook-call sequence would then change between
// renders.
//
// D40 (frontend review F7): these are now the ONLY hooks over these query
// keys. A separate per-kind family (`useBook`, `useBooks`, `useProduct`,
// `useProducts`, plus their `*Observations` siblings) used to exist
// alongside these, deliberately sharing the exact same query keys
// (`["books", params]`, `["book", id]`, …) so a mutation elsewhere that
// invalidates `["books"]` / `["book", id]` still invalidates data read
// through `useItems`/`useItem` — that key-sharing contract still holds.
// But two hook families producing two different cached SHAPES for one key
// is a landmine, not a safety net: a `Book`-shaped cache entry read through
// `useItem`'s `Item`-shaped accessor would render a first frame with
// `kind`/`imageUrl`/`subtitle`/`signal` all `undefined`. Once every page
// (`BookDetail.tsx`, `ProductDetail.tsx`, `Dashboard.tsx`,
// `ProductsDashboard.tsx`) was re-pointed at `useItem`/`useItems`, the
// per-kind family had zero remaining callers, so the fix was to delete it
// rather than split the keys — splitting would have forced every mutation
// site invalidating `["books"]` to also invalidate a second key family,
// multiplying the exact defect F6 (missing `["products"]` invalidation on
// config save) was about.

import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import { ApiError, apiGet } from "@/api/client";
import {
  bookToItem,
  itemApiBase,
  itemDetailQueryKey,
  itemListQueryKey,
  productToItem,
  type Book,
  type Item,
  type ItemKind,
  type ItemObservation,
  type Product,
} from "@/lib/item";

// Mirrors `BooksQueryParams`/`ProductsQueryParams` (`./useBooks.ts`,
// `./useProducts.ts`), which are identical today — the backend only
// supports `include_archived` server-side on either list endpoint.
export type ItemsQueryParams = {
  include_archived?: boolean;
};

export function useItems(
  kind: ItemKind,
  params: ItemsQueryParams = {},
): UseQueryResult<Item[], ApiError> {
  return useQuery<Item[], ApiError>({
    queryKey: [itemListQueryKey(kind), params],
    queryFn: async () => {
      const search = new URLSearchParams();
      if (params.include_archived) search.set("include_archived", "true");
      const qs = search.toString();
      const base = itemApiBase(kind);
      const path = (qs ? `${base}?${qs}` : base) as "/api/books" | "/api/products";
      const body = await apiGet(path);
      return kind === "book"
        ? (body as Book[]).map(bookToItem)
        : (body as Product[]).map(productToItem);
    },
  });
}

export function useItem(kind: ItemKind, id: number | null): UseQueryResult<Item, ApiError> {
  return useQuery<Item, ApiError>({
    queryKey: [itemDetailQueryKey(kind), id],
    queryFn: async () => {
      const path = `${itemApiBase(kind)}/${id}` as
        | "/api/books/{book_id}"
        | "/api/products/{product_id}";
      const body = await apiGet(path);
      return kind === "book" ? bookToItem(body as Book) : productToItem(body as Product);
    },
    enabled: id != null,
    retry: (count, err) => {
      // Don't retry on 404 — item is gone, surface immediately. Same
      // predicate as `useBook`/`useProduct`.
      if (err instanceof ApiError && err.status === 404) return false;
      return count < 2;
    },
  });
}

export type ItemObservationsPage = {
  items: ItemObservation[];
  next_before: string | null;
};

/** Same query keys the deleted `useBookObservations`/`useProductObservations`
 * used (`["book"|"product", id, "observations", limit]`, D40 above), same
 * default limit — one `useQuery` call with `kind` picking the endpoint, for
 * the same rules-of-hooks reason `useItems`/`useItem` above do. */
export function useItemObservations(
  kind: ItemKind,
  id: number | null,
  limit = 500,
): UseQueryResult<ItemObservationsPage, ApiError> {
  return useQuery<ItemObservationsPage, ApiError>({
    queryKey: [itemDetailQueryKey(kind), id, "observations", limit],
    queryFn: async () => {
      const path = `${itemApiBase(kind)}/${id}/observations?limit=${limit}` as
        | "/api/books/{book_id}/observations"
        | "/api/products/{product_id}/observations";
      const body = await apiGet(path);
      return body as ItemObservationsPage;
    },
    enabled: id != null,
  });
}
