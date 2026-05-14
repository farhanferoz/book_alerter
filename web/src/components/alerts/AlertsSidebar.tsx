// Right-rail alerts feed, mounted inside `AppShell` on every route.
//
// Pulls the 20 newest undismissed alerts via `useAlerts({ dismissed: false,
// limit: 20 })` and renders each with the shared `AlertItem`. Book titles
// come from the already-cached `useBooks()` dashboard list (active books); we
// also request `include_archived` so alerts on archived/bought books still
// resolve to a real title rather than "Book #<id>".

import { Link } from "react-router-dom";

import { Skeleton } from "@/components/ui/skeleton";
import { useAlerts, useDismissAlert } from "@/hooks/useAlerts";
import { useBooks } from "@/hooks/useBooks";

import { AlertItem } from "./AlertItem";

export function AlertsSidebar() {
  const alertsQuery = useAlerts({ dismissed: false, limit: 20 });
  const booksQuery = useBooks({ include_archived: true });
  const dismiss = useDismissAlert();

  const titleById = new Map<number, string>(
    (booksQuery.data ?? []).map((b) => [b.id, b.title]),
  );
  const items = alertsQuery.data?.items ?? [];

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

        {items.map((alert) => (
          <AlertItem
            key={alert.id}
            alert={alert}
            bookTitle={titleById.get(alert.book_id)}
            onDismiss={(id) => dismiss.mutate(id)}
            dismissing={dismiss.isPending && dismiss.variables === alert.id}
            compact
          />
        ))}
      </div>

      <p className="text-[11px] text-muted-foreground border-t border-border pt-2">
        {items.length} active{items.length === 20 ? "+ shown" : ""}
      </p>
    </div>
  );
}
