// Dashboard — tracked-book table with filter bar + empty state.
//
// Server-side surface on `GET /api/books` is currently just `include_archived`;
// signal/status/sort all happen client-side (see `useBooks.ts` for the
// follow-up note). When the catalog grows past a single fetch we'll add the
// matching query params to the backend handler and the hook.

import { useMemo, useState } from "react";

import { ApiError } from "@/api/client";
import { Button } from "@/components/ui/button";
import { AddBookModal } from "@/components/books/AddBookModal";
import { BookFilters, DEFAULT_FILTERS, type BookFiltersValue } from "@/components/books/BookFilters";
import { DataTable } from "@/components/books/BookTable";
import { bookColumns } from "@/components/books/columns";
import { approximateSignal, type Signal } from "@/components/books/signal";
import { useBooks, type Book } from "@/hooks/useBooks";

const SIGNAL_ORDER: Record<Signal, number> = {
  TARGET_HIT: 0,
  BUY: 1,
  WATCH: 2,
  WAIT: 3,
  INSUFFICIENT_DATA: 4,
};

function applyFilters(books: Book[], filters: BookFiltersValue): Book[] {
  let result = books;
  if (filters.signal !== "ALL") {
    result = result.filter((b) => approximateSignal(b) === filters.signal);
  }
  if (filters.status !== "all") {
    result = result.filter((b) => b.status === filters.status);
  }
  const sorted = [...result];
  switch (filters.sort) {
    case "signal":
      sorted.sort(
        (a, b) => SIGNAL_ORDER[approximateSignal(a)] - SIGNAL_ORDER[approximateSignal(b)],
      );
      break;
    case "best_price":
      sorted.sort((a, b) => {
        const av = a.stats.current_best_total_minor ?? Number.MAX_SAFE_INTEGER;
        const bv = b.stats.current_best_total_minor ?? Number.MAX_SAFE_INTEGER;
        return av - bv;
      });
      break;
    case "pct_vs_median":
      sorted.sort((a, b) => pctOrInf(a) - pctOrInf(b));
      break;
    case "last_seen":
      sorted.sort((a, b) => {
        const av = a.stats.last_observed_at ?? "";
        const bv = b.stats.last_observed_at ?? "";
        return bv.localeCompare(av); // newest first
      });
      break;
    case "title":
      sorted.sort((a, b) => a.title.localeCompare(b.title));
      break;
  }
  return sorted;
}

function pctOrInf(book: Book): number {
  const current = book.stats.current_best_total_minor;
  const median = book.stats.p50_total_minor;
  if (current == null || median == null || median === 0) return Number.MAX_SAFE_INTEGER;
  return (current - median) / median;
}

function SkeletonRows() {
  return (
    <div className="space-y-2">
      {[0, 1, 2, 3].map((i) => (
        <div
          key={i}
          className="h-12 animate-pulse rounded-md bg-muted/60"
          aria-hidden
        />
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
  const message =
    error instanceof ApiError
      ? `${error.status} — ${error.message}`
      : error instanceof Error
        ? error.message
        : String(error);
  return (
    <div className="rounded-md border border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive">
      Failed to load books: {message}
    </div>
  );
}

export function Dashboard() {
  const [filters, setFilters] = useState<BookFiltersValue>(DEFAULT_FILTERS);
  const [addBookOpen, setAddBookOpen] = useState(false);
  const includeArchived = filters.status === "archived" || filters.status === "all";
  const { data, isLoading, isError, error } = useBooks({
    include_archived: includeArchived,
  });

  const filtered = useMemo(
    () => (data ? applyFilters(data, filters) : []),
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

      <BookFilters value={filters} onChange={setFilters} />

      {isLoading ? (
        <SkeletonRows />
      ) : isError ? (
        <ErrorCard error={error} />
      ) : !data || data.length === 0 ? (
        <EmptyState onAddBook={onAddBook} />
      ) : (
        <DataTable
          columns={bookColumns}
          data={filtered}
          emptyMessage="No books match the current filters."
        />
      )}

      <AddBookModal open={addBookOpen} onOpenChange={setAddBookOpen} />
    </section>
  );
}
