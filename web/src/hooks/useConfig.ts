// React Query hooks over the `/api/config` endpoints.
//
//   useConfig()         → GET /api/config — current full Config as JSON.
//   useUpdateConfig()   → PUT /api/config — body {config, dry_run}; returns
//                         {diff, applied, errors}. A single mutation covers
//                         both the dry-run (preview) and apply flows; the
//                         caller passes `dryRun` per call.
//
// The wire schema for `Config` itself is not exposed by openapi-typescript —
// `GET /api/config` declares the response as `dict[str, Any]`. The hook
// therefore returns `ConfigShape` (a hand-typed mirror keyed against
// `RecommendationConfig` in `src/book_alerter/config.py`). When new fields
// land server-side, extend this type — the API call doesn't change.
//
// `applied=true` invalidates `["config"]` and `["books"]` (recommendation
// changes shift the dashboard's client-side signal computation).

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ApiError, apiGet, apiPut } from "@/api/client";
import type { components } from "@/api/schema";

// Hand-typed mirror of `RecommendationConfig` in `src/book_alerter/config.py`.
// Kept in sync manually — the wire `Config` is `dict[str, Any]`.
export type RecommendationConfigShape = {
  min_observations_for_signal: number;
  buy_percentile: number;
  watch_percentile: number;
  target_tolerance_pct: number;
  alert_dedup_window_hours: number;
};

export type ConfigShape = {
  config_version: number;
  recommendation: RecommendationConfigShape;
  // Notifications + sources are passed-through verbatim in PUT bodies; the
  // recommendation tab never edits them but must preserve them on save.
  notifications: Record<string, unknown>;
  sources: Record<string, unknown>;
  // Allow extra top-level keys (forward-compat with new config sections).
  [key: string]: unknown;
};

export const RECOMMENDATION_DEFAULTS: RecommendationConfigShape = {
  min_observations_for_signal: 14,
  buy_percentile: 25,
  watch_percentile: 50,
  target_tolerance_pct: 5,
  alert_dedup_window_hours: 24,
};

export type ConfigUpdateResult = components["schemas"]["ConfigUpdateResult"];

export function useConfig() {
  return useQuery<ConfigShape, ApiError>({
    queryKey: ["config"],
    queryFn: async () => {
      const body = await apiGet("/api/config");
      return body as ConfigShape;
    },
  });
}

export type UpdateConfigArgs = {
  config: ConfigShape;
  dryRun: boolean;
};

export function useUpdateConfig() {
  const qc = useQueryClient();
  return useMutation<ConfigUpdateResult, ApiError, UpdateConfigArgs>({
    mutationFn: async ({ config, dryRun }) => {
      const body = await apiPut("/api/config", {
        config: config as Record<string, unknown>,
        dry_run: dryRun,
      });
      return body as ConfigUpdateResult;
    },
    onSuccess: (data) => {
      if (data.applied) {
        void qc.invalidateQueries({ queryKey: ["config"] });
        // Recommendation tweaks shift the client-side signal computation.
        void qc.invalidateQueries({ queryKey: ["books"] });
      }
    },
  });
}
