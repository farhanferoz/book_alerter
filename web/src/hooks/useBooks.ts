// React Query hook over `GET /api/books`.
//
// The backend handler (see `src/book_alerter/api/books.py::list_books`) only
// supports `include_archived` server-side at the moment. Sort/signal/status
// filters are applied client-side in the Dashboard — follow-up to extend
// server-side filtering when the list grows beyond what fits in one fetch.

import { useQuery } from "@tanstack/react-query";

import { apiGet } from "@/api/client";
import type { components } from "@/api/schema";

export type Book = components["schemas"]["BookOut"];

export type BooksQueryParams = {
  include_archived?: boolean;
};

export function useBooks(params: BooksQueryParams = {}) {
  return useQuery<Book[]>({
    queryKey: ["books", params],
    queryFn: async () => {
      const search = new URLSearchParams();
      if (params.include_archived) {
        search.set("include_archived", "true");
      }
      const qs = search.toString();
      const path = qs ? `/api/books?${qs}` : "/api/books";
      // `apiGet` returns the union of all documented response shapes (incl.
      // 422 HTTPValidationError). The fetch wrapper throws on non-2xx, so the
      // resolved value here is the 200 body — narrow it explicitly.
      const body = await apiGet(path as "/api/books");
      return body as Book[];
    },
  });
}
