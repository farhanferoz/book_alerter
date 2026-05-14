// React Query hooks for the single-book detail view.
//
// `useBook(id)`           → GET /api/books/{id}            (book + stats)
// `useBookObservations`   → GET /api/books/{id}/observations (price history)
//
// Both are keyed off `["book", id]` / `["book", id, "observations"]` so the
// detail page can invalidate precisely after a PATCH/refetch without nuking
// the dashboard list. The list cache key is `["books"]` (plural) and stays
// independent — but we DO invalidate it from the detail page after mutations
// that change the book's projected `stats`/`status` so the dashboard reflects
// the new state on next visit.

import { useQuery } from "@tanstack/react-query";

import { ApiError, apiGet } from "@/api/client";
import type { components } from "@/api/schema";

export type Book = components["schemas"]["BookOut"];
export type ObservationsPage = components["schemas"]["ObservationsPage"];
export type PriceObservation = components["schemas"]["PriceObservationOut"];

export function useBook(id: number | null) {
  return useQuery<Book, ApiError>({
    queryKey: ["book", id],
    queryFn: async () => {
      // openapi-typescript types the dynamic path with `{book_id}` literal;
      // the runtime fetch wraps a real URL, so we cast the path.
      const path = `/api/books/${id}` as "/api/books/{book_id}";
      const body = await apiGet(path);
      return body as Book;
    },
    enabled: id != null,
    retry: (count, err) => {
      // Don't retry on 404 — book is gone, surface immediately.
      if (err instanceof ApiError && err.status === 404) return false;
      return count < 2;
    },
  });
}

export function useBookObservations(id: number | null, limit = 500) {
  return useQuery<ObservationsPage, ApiError>({
    queryKey: ["book", id, "observations", limit],
    queryFn: async () => {
      const path = `/api/books/${id}/observations?limit=${limit}`;
      const body = await apiGet(path as "/api/books/{book_id}/observations");
      return body as ObservationsPage;
    },
    enabled: id != null,
  });
}
