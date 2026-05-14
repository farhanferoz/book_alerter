// React Query hooks over the `/api/alerts` endpoints.
//
//   useAlerts({ dismissed, kind, limit, before })   → GET  /api/alerts
//   useDismissAlert()                               → POST /api/alerts/{id}/dismiss
//   useDismissAllAlerts()                           → POST /api/alerts/dismiss-all
//
// Backend handler at `src/book_alerter/api/alerts.py`. Cursor pagination is
// available via `before` but the sidebar + page currently fetch a single
// generous limit; "Load more" can hook into `next_before` when needed.
//
// Both mutations invalidate `["alerts"]` so the sidebar + page re-fetch
// together. We do NOT invalidate `["books"]` — the dashboard columns don't
// surface an alert count (verified against `components/books/columns.tsx`).

import {
  useInfiniteQuery,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";

import { ApiError, apiGet, apiPost } from "@/api/client";
import type { components } from "@/api/schema";

export type Alert = components["schemas"]["AlertOut"];
export type AlertsPage = components["schemas"]["AlertsPage"];
export type AlertKind = Alert["kind"];

export type AlertsQueryParams = {
  dismissed?: boolean;
  kind?: AlertKind;
  limit?: number;
  before?: string;
};

export function useAlerts(params: AlertsQueryParams = {}) {
  return useQuery<AlertsPage, ApiError>({
    queryKey: ["alerts", params],
    queryFn: async () => {
      const search = new URLSearchParams();
      if (params.dismissed !== undefined) {
        search.set("dismissed", String(params.dismissed));
      }
      if (params.kind) search.set("kind", params.kind);
      if (params.limit !== undefined) search.set("limit", String(params.limit));
      if (params.before) search.set("before", params.before);
      const qs = search.toString();
      const path = qs ? `/api/alerts?${qs}` : "/api/alerts";
      const body = await apiGet(path as "/api/alerts");
      return body as AlertsPage;
    },
  });
}

/**
 * Cursor-paginated variant for the full Alerts page.
 *
 * Each page is the backend's `AlertsPage`; `getNextPageParam` reads
 * `next_before` (null when the last fetched page wasn't full, signalling
 * "no more rows"). The flat item list is `data.pages.flatMap(p => p.items)`.
 */
export function useInfiniteAlerts(
  params: Omit<AlertsQueryParams, "before"> = {},
) {
  const limit = params.limit ?? 50;
  return useInfiniteQuery<AlertsPage, ApiError>({
    queryKey: ["alerts", "infinite", { ...params, limit }],
    initialPageParam: undefined,
    queryFn: async ({ pageParam }) => {
      const search = new URLSearchParams();
      if (params.dismissed !== undefined) {
        search.set("dismissed", String(params.dismissed));
      }
      if (params.kind) search.set("kind", params.kind);
      search.set("limit", String(limit));
      if (typeof pageParam === "string") search.set("before", pageParam);
      const path = `/api/alerts?${search.toString()}`;
      const body = await apiGet(path as "/api/alerts");
      return body as AlertsPage;
    },
    getNextPageParam: (last) => last.next_before ?? undefined,
  });
}

export function useDismissAlert() {
  const qc = useQueryClient();
  return useMutation<Alert, ApiError, number>({
    mutationFn: async (id: number) => {
      const path = `/api/alerts/${id}/dismiss` as "/api/alerts/{alert_id}/dismiss";
      const body = await apiPost(path);
      return body as Alert;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["alerts"] });
    },
  });
}

export type DismissAllResult = components["schemas"]["DismissAllResult"];

export function useDismissAllAlerts() {
  const qc = useQueryClient();
  return useMutation<DismissAllResult, ApiError, void>({
    mutationFn: async () => {
      const body = await apiPost("/api/alerts/dismiss-all");
      return body as DismissAllResult;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["alerts"] });
    },
  });
}
