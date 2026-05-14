// Shared constants for the alert `kind` enum (labels + Tailwind pill classes).
//
// Lives in `lib/` rather than alongside `AlertKindBadge` because React's
// `react-refresh/only-export-components` rule forbids non-component exports
// from component modules (same constraint that pushed `signal.ts` out of
// `columns.tsx`). Palette chosen to be distinguishable in light + dark mode
// without colliding with the signal pills on the dashboard.

import type { components } from "@/api/schema";

export type AlertKind = components["schemas"]["AlertOut"]["kind"];

export const ALERT_KIND_LABEL: Record<AlertKind, string> = {
  target_hit: "TARGET HIT",
  percentile_cross: "PERCENTILE",
  new_low: "NEW LOW",
};

export const ALERT_KIND_PILL_CLASS: Record<AlertKind, string> = {
  target_hit: "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300",
  percentile_cross: "bg-blue-100 text-blue-800 dark:bg-blue-900/40 dark:text-blue-300",
  new_low: "bg-purple-100 text-purple-800 dark:bg-purple-900/40 dark:text-purple-300",
};
