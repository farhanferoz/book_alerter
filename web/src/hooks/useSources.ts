// React Query hooks over the `/api/sources` endpoints.
//
//   useSources()                     → GET   /api/sources
//   useSourceRuns(name, {enabled})   → GET   /api/sources/{name}/runs?limit=10
//   useRunSource()                   → POST  /api/sources/{name}/run
//   useUpdateSource()                → PATCH /api/sources/{name}
//
// `useSourceRuns` is gated on `enabled` so the "Recent runs" accordion fetches
// lazily — the list is only useful once the user expands the panel. All
// mutations invalidate the `["sources"]` prefix so the parent list refreshes
// with fresh `last_run` data; `useRunSource` also bumps `["source-runs", name]`
// so an open accordion shows the new run.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError, apiGet, apiPatch, apiPost } from "@/api/client";
import type { components } from "@/api/schema";

export type SourceStatus = components["schemas"]["SourceStatusOut"];
export type SourceConfig = components["schemas"]["SourceConfigOut"];
export type SourceRun = components["schemas"]["SourceRunOut"];
export type SourcePatch = components["schemas"]["SourcePatch"];
export type TriggerRunResult = components["schemas"]["TriggerRunResult"];

export function useSources() {
  return useQuery<SourceStatus[], ApiError>({
    queryKey: ["sources"],
    queryFn: async () => {
      const body = await apiGet("/api/sources");
      return body as SourceStatus[];
    },
  });
}

export function useSourceRuns(
  name: string,
  options: { enabled: boolean; limit?: number } = { enabled: false },
) {
  const limit = options.limit ?? 10;
  return useQuery<SourceRun[], ApiError>({
    queryKey: ["source-runs", name, { limit }],
    queryFn: async () => {
      // Path-templated GET — the typed client doesn't yet plumb `params.path`,
      // so we interpolate the name and cast to the templated path literal.
      const path =
        `/api/sources/${encodeURIComponent(name)}/runs?limit=${limit}` as
        "/api/sources/{name}/runs";
      const body = await apiGet(path);
      return body as SourceRun[];
    },
    enabled: options.enabled,
  });
}

export function useRunSource() {
  const qc = useQueryClient();
  return useMutation<TriggerRunResult, ApiError, string>({
    mutationFn: async (name) => {
      const path = `/api/sources/${encodeURIComponent(name)}/run` as
        "/api/sources/{name}/run";
      const body = await apiPost(path);
      return body as TriggerRunResult;
    },
    onSuccess: (_data, name) => {
      void qc.invalidateQueries({ queryKey: ["sources"] });
      void qc.invalidateQueries({ queryKey: ["source-runs", name] });
    },
  });
}

export function useUpdateSource() {
  const qc = useQueryClient();
  return useMutation<
    SourceStatus,
    ApiError,
    { name: string; patch: SourcePatch }
  >({
    mutationFn: async ({ name, patch }) => {
      const path = `/api/sources/${encodeURIComponent(name)}` as
        "/api/sources/{name}";
      const body = await apiPatch(path, patch);
      return body as SourceStatus;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["sources"] });
    },
  });
}
