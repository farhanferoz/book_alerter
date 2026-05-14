// Full alerts page (`/alerts`).
//
// Reuses `useInfiniteAlerts` (cursor pagination via the backend's
// `next_before` / `before` contract) and `useDismissAlert` /
// `useDismissAllAlerts` from `hooks/useAlerts.ts`. The dashboard chrome
// already mounts the sidebar on every route via `AppShell`, so this page is
// just the main-area content.
//
// Filter shape: { dismissed, kind, book }. `dismissed=all` → omit the param;
// `kind=all` → omit the param. Book-title filter is applied client-side
// because the backend has no title-search endpoint.

import { useMemo, useState } from "react";

import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { AlertFilters, type Filters } from "@/components/alerts/AlertFilters";
import { AlertItem } from "@/components/alerts/AlertItem";
import {
  useDismissAlert,
  useDismissAllAlerts,
  useInfiniteAlerts,
} from "@/hooks/useAlerts";
import { useBooks } from "@/hooks/useBooks";

const PAGE_SIZE = 50;

function filtersToParams(filters: Filters) {
  return {
    dismissed:
      filters.dismissed === "active"
        ? false
        : filters.dismissed === "dismissed"
          ? true
          : undefined,
    kind: filters.kind === "all" ? undefined : filters.kind,
    limit: PAGE_SIZE,
  };
}

export function Alerts() {
  const [filters, setFilters] = useState<Filters>({
    dismissed: "active",
    kind: "all",
    book: "",
  });
  const [confirmDismissAll, setConfirmDismissAll] = useState(false);

  const alertsQuery = useInfiniteAlerts(filtersToParams(filters));
  const booksQuery = useBooks({ include_archived: true });
  const dismiss = useDismissAlert();
  const dismissAll = useDismissAllAlerts();

  const titleById = useMemo(
    () => new Map((booksQuery.data ?? []).map((b) => [b.id, b.title])),
    [booksQuery.data],
  );

  const allItems = useMemo(
    () => alertsQuery.data?.pages.flatMap((p) => p.items) ?? [],
    [alertsQuery.data],
  );

  // Client-side book-title filter.
  const items = useMemo(() => {
    const needle = filters.book.trim().toLowerCase();
    if (!needle) return allItems;
    return allItems.filter((a) =>
      (titleById.get(a.book_id) ?? "").toLowerCase().includes(needle),
    );
  }, [allItems, filters.book, titleById]);

  const hasActive = allItems.some((a) => a.dismissed_at === null);
  const showDismissAll = filters.dismissed !== "dismissed" && hasActive;

  return (
    <section className="space-y-4">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="text-xl font-semibold">Alerts</h1>
          <p className="text-sm text-muted-foreground">
            Full alert log with filters and dismiss controls.
          </p>
        </div>
        {showDismissAll && (
          <Button
            variant="outline"
            size="sm"
            disabled={dismissAll.isPending}
            onClick={() => setConfirmDismissAll(true)}
          >
            {dismissAll.isPending ? "Dismissing…" : "Dismiss all"}
          </Button>
        )}
      </header>

      <AlertFilters value={filters} onChange={setFilters} />

      {alertsQuery.isLoading && (
        <div className="space-y-2">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-20" />
          ))}
        </div>
      )}

      {alertsQuery.isError && (
        <p className="text-sm text-destructive">
          Failed to load alerts: {alertsQuery.error.message}
        </p>
      )}

      {!alertsQuery.isLoading && !alertsQuery.isError && items.length === 0 && (
        <p className="text-sm text-muted-foreground">
          No alerts match the current filters.
        </p>
      )}

      <div className="space-y-2">
        {items.map((alert) => (
          <AlertItem
            key={alert.id}
            alert={alert}
            bookTitle={titleById.get(alert.book_id)}
            onDismiss={
              alert.dismissed_at === null
                ? (id) => dismiss.mutate(id)
                : undefined
            }
            dismissing={dismiss.isPending && dismiss.variables === alert.id}
          />
        ))}
      </div>

      {alertsQuery.hasNextPage && (
        <div className="flex justify-center pt-2">
          <Button
            variant="outline"
            size="sm"
            onClick={() => void alertsQuery.fetchNextPage()}
            disabled={alertsQuery.isFetchingNextPage}
          >
            {alertsQuery.isFetchingNextPage ? "Loading…" : "Load more"}
          </Button>
        </div>
      )}

      <AlertDialog
        open={confirmDismissAll}
        onOpenChange={(open) => {
          if (!open) setConfirmDismissAll(false);
        }}
      >
        {confirmDismissAll && (
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>Dismiss all active alerts?</AlertDialogTitle>
              <AlertDialogDescription>
                This dismisses every alert with no current dismissal
                timestamp, across all books and kinds. Idempotent —
                previously-dismissed alerts keep their original timestamp.
              </AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <Button
                variant="outline"
                onClick={() => setConfirmDismissAll(false)}
                disabled={dismissAll.isPending}
              >
                Cancel
              </Button>
              <Button
                variant="default"
                onClick={() =>
                  dismissAll.mutate(undefined, {
                    onSettled: () => setConfirmDismissAll(false),
                  })
                }
                disabled={dismissAll.isPending}
              >
                {dismissAll.isPending ? "Working…" : "Dismiss all"}
              </Button>
            </AlertDialogFooter>
          </AlertDialogContent>
        )}
      </AlertDialog>
    </section>
  );
}
