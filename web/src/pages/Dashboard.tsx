// Dashboard — tracked-book table with filter bar + empty state.
//
// Server-side surface on `GET /api/books` is currently just `include_archived`;
// signal/status/sort all happen client-side via `applyItemFilters`
// (`@/lib/item`, shared with `ProductsDashboard.tsx`). When the catalog
// grows past a single fetch we'll add the matching query params to the
// backend handler and the hook.
//
// D40 (frontend review F7): this page used to fetch `Book[]` through the
// private `useBooks` hook with its own private `applyFilters`/`SIGNAL_ORDER`
// copies. `useBooks` and `useItems("book", …)` shared the exact same query
// key (`["books", params]`, deliberately — see `useItems.ts`) but produced
// two different cached SHAPES for it, which was the actual landmine: a
// `Book`-shaped cache entry from one family read through the other's
// `Item`-shaped accessor would silently paint a first frame with no cover,
// author or signal. `useBooks` had exactly one remaining caller (this
// page); re-pointing it onto `useItems("book", …)` — the same hook
// `ProductsDashboard.tsx` already uses — and deleting `useBooks.ts` (along
// with the already-fully-dead `useBook`, `useBookObservations`,
// `useProduct`, `useProductObservations`, `useProducts`) closes the
// collision by removing the second shape rather than splitting the key,
// which would have forced every mutation site invalidating `["books"]` to
// also invalidate a second key family (the same defect this fix's sibling,
// F6, was about — multiplied across every mutation).

import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { AddBookModal } from "@/components/books/AddBookModal";
import { BookFilters, DEFAULT_FILTERS, type BookFiltersValue } from "@/components/books/BookFilters";
import { DataTable } from "@/components/books/BookTable";
import { buildItemColumns } from "@/components/books/columns";
import { ScrapeHealthBanner } from "@/components/books/ScrapeHealthBanner";
import { useItems } from "@/hooks/useItems";
import { applyItemFilters } from "@/lib/item";
import { formatErrorMessage } from "@/lib/utils";

function SkeletonRows() {
  return (
    <div className="space-y-2">
      {[0, 1, 2, 3].map((i) => (
        <Skeleton key={i} className="h-12" tone="muted" aria-hidden />
      ))}
    </div>
  );
}

function EmptyState({ onAddBook }: { onAddBook: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-md border border-dashed border-border p-10 text-center">
      <p className="text-sm font-medium">No books yet</p>
      <p className="mt-1 text-xs text-muted-foreground">
        Paste an ISBN to start tracking prices.
      </p>
      <Button className="mt-4" onClick={onAddBook}>
        Add your first book
      </Button>
    </div>
  );
}

function ErrorCard({ error }: { error: unknown }) {
  return (
    <div className="rounded-md border border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive">
      Failed to load books: {formatErrorMessage(error)}
    </div>
  );
}

export function Dashboard() {
  const [filters, setFilters] = useState<BookFiltersValue>(DEFAULT_FILTERS);
  const [addBookOpen, setAddBookOpen] = useState(false);
  const includeArchived = filters.status === "archived" || filters.status === "all";
  const { data, isLoading, isError, error } = useItems("book", {
    include_archived: includeArchived,
  });
  const columns = useMemo(() => buildItemColumns(), []);
  const filtered = useMemo(
    () => (data ? applyItemFilters(data, filters) : []),
    [data, filters],
  );

  const onAddBook = () => setAddBookOpen(true);

  return (
    <section className="space-y-4">
      <header className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">Dashboard</h1>
          <p className="text-xs text-muted-foreground">
            Tracked books, latest prices, and recommendation signals.
          </p>
        </div>
        <Button onClick={onAddBook}>Add book</Button>
      </header>

      <ScrapeHealthBanner scope="book" />

      <BookFilters value={filters} onChange={setFilters} />

      {isLoading ? (
        <SkeletonRows />
      ) : isError ? (
        <ErrorCard error={error} />
      ) : !data || data.length === 0 ? (
        <EmptyState onAddBook={onAddBook} />
      ) : (
        <DataTable
          columns={columns}
          data={filtered}
          emptyMessage="No books match the current filters."
        />
      )}

      <AddBookModal open={addBookOpen} onOpenChange={setAddBookOpen} />
    </section>
  );
}
