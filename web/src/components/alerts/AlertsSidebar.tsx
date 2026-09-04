// Right-rail alerts feed, mounted inside `AppShell` on every route.
//
// Pulls the 20 newest undismissed alerts via `useAlerts({ dismissed: false,
// limit: 20 })` and renders ONE card per item — the newest alert for it,
// with a "+N older" note when more are pending — using the shared
// `AlertItem`. Ungrouped, a book whose price keeps drifting fills the rail
// with six near-identical cards and crowds every other item out; the full
// list is one click away on /alerts. The feed spans books and products;
// each row carries its own title and item identity from the backend, so no
// client-side title lookup is needed.

import { useMemo } from "react";
import { Link } from "react-router-dom";

import { Skeleton } from "@/components/ui/skeleton";
import {
  alertRef,
  sameAlertRef,
  useAlerts,
  useDismissAlert,
  type Alert,
} from "@/hooks/useAlerts";

import { AlertItem } from "./AlertItem";

export function AlertsSidebar() {
  const alertsQuery = useAlerts({ dismissed: false, limit: 20 });
  const dismiss = useDismissAlert();

  const page = alertsQuery.data;
  const items = page?.items ?? [];

  // Newest-first from the API, so the first alert seen for an item is the
  // one to show; the rest just count. Keyed on `page` (stable per fetch)
  // rather than the `?? []` fallback, which would be a new array each render.
  const groups = useMemo(() => {
    const byItem = new Map<string, { newest: Alert; older: number }>();
    for (const alert of page?.items ?? []) {
      const key = `${alert.item_kind}-${alert.item_id}`;
      const group = byItem.get(key);
      if (group) group.older += 1;
      else byItem.set(key, { newest: alert, older: 0 });
    }
    return [...byItem.values()];
  }, [page]);

  return (
    <div className="flex h-full flex-col gap-3">
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">Active alerts</h2>
        <Link
          to="/alerts"
          className="text-xs text-muted-foreground hover:underline"
        >
          View all
        </Link>
      </div>

      <div className="flex-1 min-h-0 overflow-auto space-y-2 pr-1">
        {alertsQuery.isLoading && (
          <div className="space-y-2">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-16" />
            ))}
          </div>
        )}

        {alertsQuery.isError && (
          <p className="text-xs text-destructive">
            Failed to load alerts: {alertsQuery.error.message}
          </p>
        )}

        {!alertsQuery.isLoading &&
          !alertsQuery.isError &&
          items.length === 0 && (
            <p className="text-xs text-muted-foreground">No active alerts.</p>
          )}

        {groups.map(({ newest: alert, older }) => (
          <div key={`${alert.item_kind}-${alert.id}`} className="space-y-1">
            <AlertItem
              alert={alert}
              onDismiss={(ref) => dismiss.mutate(ref)}
              dismissing={dismiss.isPending && sameAlertRef(dismiss.variables, alertRef(alert))}
              compact
            />
            {older > 0 && (
              <Link
                to="/alerts"
                className="block px-2 text-[11px] text-muted-foreground hover:underline"
              >
                +{older} older alert{older === 1 ? "" : "s"} for this item
              </Link>
            )}
          </div>
        ))}
      </div>

      <p className="text-[11px] text-muted-foreground border-t border-border pt-2">
        {items.length}
        {items.length === 20 ? "+" : ""} active across {groups.length} item
        {groups.length === 1 ? "" : "s"}
      </p>
    </div>
  );
}
