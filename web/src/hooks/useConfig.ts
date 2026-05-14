// React Query hooks over the `/api/config` endpoints.
//
//   useConfig()         → GET /api/config — current full Config as JSON.
//   useConfigSchema()   → GET /api/config/schema — Pydantic JSON Schema for
//                         the Config model. Consumer: Phase 11.5 Monaco
//                         editor (read-only schema panel; live validation
//                         happens via dry-run PUT, not Ajv).
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

// Hand-typed mirror of `NotificationsConfig` (Phase 11.4). Keys match the
// Pydantic models in `src/book_alerter/config.py` 1:1.
export type AlertKind = "target_hit" | "percentile_cross" | "new_low";

export const ALERT_KINDS: readonly AlertKind[] = [
  "target_hit",
  "percentile_cross",
  "new_low",
];

export type QuietHoursShape = {
  start: string;
  end: string;
  tz: string;
};

export type InAppChannelShape = {
  enabled: boolean;
};

export type NtfyChannelShape = {
  enabled: boolean;
  server: string;
  topic: string;
  priority: string;
  tags: string[];
};

export type NotificationChannelsShape = {
  inapp: InAppChannelShape;
  ntfy: NtfyChannelShape;
};

export type NotificationsConfigShape = {
  alert_kinds_enabled: AlertKind[];
  quiet_hours: QuietHoursShape | null;
  channels: NotificationChannelsShape;
};

export type ConfigShape = {
  config_version: number;
  recommendation: RecommendationConfigShape;
  notifications: NotificationsConfigShape;
  // Sources are passed-through verbatim in PUT bodies (edited via PATCH
  // /api/sources on the Sources tab).
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

// Pydantic-generated JSON Schema for the `Config` model. The shape is the
// standard JSON-Schema dict with `properties`, `$defs`, etc. We keep the
// return type as `Record<string, unknown>` since the consumer (Advanced
// settings) only walks it for display — no typed access required.
export function useConfigSchema() {
  return useQuery<Record<string, unknown>, ApiError>({
    queryKey: ["config", "schema"],
    queryFn: async () => {
      const body = await apiGet("/api/config/schema");
      return body as Record<string, unknown>;
    },
    // Schema is keyed on the running server build — refetching on focus is
    // wasteful. Treat as effectively static for the session.
    staleTime: Infinity,
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
        // Recommendation tweaks shift the client-side signal computation;
        // alert-kind / quiet-hours / channel changes affect future dispatch
        // (the alerts feed itself doesn't refresh, but invalidating here is
        // cheap and keeps the next paint coherent if a dispatch fires).
        void qc.invalidateQueries({ queryKey: ["books"] });
        void qc.invalidateQueries({ queryKey: ["alerts"] });
      }
    },
  });
}
