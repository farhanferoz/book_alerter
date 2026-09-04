// React Query hooks over the `/api/alerts` endpoints.
//
//   useAlerts({ dismissed, kind, limit, before })   → GET  /api/alerts
//   useDismissAlert()                               → POST /api/alerts/{item_kind}/{id}/dismiss
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
export type ItemKind = Alert["item_kind"];

/** Alert ids are unique only within their own table, so a row is addressed by
 *  the (item_kind, id) pair. */
export type AlertRef = { item_kind: ItemKind; id: number };

export function alertRef(alert: Alert): AlertRef {
  return { item_kind: alert.item_kind, id: alert.id };
}

export function sameAlertRef(a: AlertRef | undefined, b: AlertRef): boolean {
  return a !== undefined && a.item_kind === b.item_kind && a.id === b.id;
}

export type AlertsQueryParams = {
  dismissed?: boolean;
  kind?: AlertKind;
  item_kind?: ItemKind;
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
      if (params.item_kind) search.set("item_kind", params.item_kind);
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
      if (params.item_kind) search.set("item_kind", params.item_kind);
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
  return useMutation<Alert, ApiError, AlertRef>({
    mutationFn: async ({ item_kind, id }: AlertRef) => {
      const path =
        `/api/alerts/${item_kind}/${id}/dismiss` as "/api/alerts/{item_kind}/{alert_id}/dismiss";
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
