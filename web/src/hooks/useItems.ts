// Kind-agnostic wrappers over the books/products list and single-item
// endpoints:
//
//   useItems(kind, params)  → GET /api/books  or  /api/products, → Item[]
//   useItem(kind, id)       → GET /api/books/{id}  or  /api/products/{id}, → Item
//
// These do NOT call `useBooks`/`useProducts`/`useBook`/`useProduct`
// (`./useBooks.ts` etc.) directly. `kind === "book" ? useBooks(params) :
// useProducts(params)` — call one hook in one branch, another in the other
// — is exactly the shape `react-hooks/rules-of-hooks` forbids: a caller
// could in principle re-render with a different `kind`, and the hook-call
// sequence would then change between renders. Calling both unconditionally
// every render satisfies the rule but has no way to suppress the OTHER
// kind's fetch — neither `useBooks` nor `useProducts` takes an `enabled`
// override — so every `useItems("book", …)` call would also silently issue
// a background `GET /api/products`. Instead each hook here makes exactly
// ONE `useQuery` call, with `kind` used only as data to pick the endpoint
// and response mapper — matching `useBooks`/`useProducts`/`useBook`/
// `useProduct`'s own query keys and `apiGet` usage exactly, so a mutation
// elsewhere that invalidates `["books"]` / `["book", id]` still invalidates
// data read through `useItems`/`useItem`.

import { useQuery, type UseQueryResult } from "@tanstack/react-query";

import { ApiError, apiGet } from "@/api/client";
import {
  bookToItem,
  productToItem,
  type Book,
  type Item,
  type ItemKind,
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
    queryKey: [kind === "book" ? "books" : "products", params],
    queryFn: async () => {
      const search = new URLSearchParams();
      if (params.include_archived) search.set("include_archived", "true");
      const qs = search.toString();
      const base = kind === "book" ? "/api/books" : "/api/products";
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
    queryKey: [kind === "book" ? "book" : "product", id],
    queryFn: async () => {
      const base = kind === "book" ? "/api/books" : "/api/products";
      const path = `${base}/${id}` as
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
