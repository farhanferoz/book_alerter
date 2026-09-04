// Dashboard — tracked-book table with filter bar + empty state.
//
// Server-side surface on `GET /api/books` is currently just `include_archived`;
// signal/status/sort all happen client-side (see `useBooks.ts` for the
// follow-up note). When the catalog grows past a single fetch we'll add the
// matching query params to the backend handler and the hook.

import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { AddBookModal } from "@/components/books/AddBookModal";
import { BookFilters, DEFAULT_FILTERS, type BookFiltersValue } from "@/components/books/BookFilters";
import { DataTable } from "@/components/books/BookTable";
import { buildBookColumns } from "@/components/books/columns";
import { bookSignal, type Signal } from "@/components/books/signal";
import { useBooks, type Book } from "@/hooks/useBooks";
import { useSources } from "@/hooks/useSources";
import { rank3mOrInf } from "@/lib/windows";
import { sourceDisplayName } from "@/lib/format";
import { formatErrorMessage } from "@/lib/utils";

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
    result = result.filter((b) => bookSignal(b) === filters.signal);
  }
  if (filters.status !== "all") {
    result = result.filter((b) => b.status === filters.status);
  }
  const sorted = [...result];
  switch (filters.sort) {
    case "signal":
      sorted.sort(
        (a, b) => SIGNAL_ORDER[bookSignal(a)] - SIGNAL_ORDER[bookSignal(b)],
      );
      break;
    case "best_price":
      sorted.sort((a, b) => {
        const av = a.stats.current_best_total_minor ?? Number.MAX_SAFE_INTEGER;
        const bv = b.stats.current_best_total_minor ?? Number.MAX_SAFE_INTEGER;
        return av - bv;
      });
      break;
    case "percentile":
      sorted.sort((a, b) => rank3mOrInf(a) - rank3mOrInf(b));
      break;
    case "last_seen":
      sorted.sort((a, b) => {
        const av = a.stats.last_polled_at ?? "";
        const bv = b.stats.last_polled_at ?? "";
        return bv.localeCompare(av); // newest first
      });
      break;
    case "title":
      sorted.sort((a, b) => a.title.localeCompare(b.title));
      break;
  }
  return sorted;
}

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

// Scrape-health banner (T6.1). `last_24h.challenged` is a rolled-up proxy —
// see `SourceHealthOut` on the backend — so it's worded as "in the last 24
// hours" (the actual window the number covers, possibly several runs) rather
// than "in the last run", and the caveat line makes clear it isn't an exact
// bot-block count. Fails silently (renders nothing) while loading or on
// error — this is a supplementary heads-up, not the page's primary data.
function ScrapeHealthBanner() {
  const { data } = useSources();
  const challenged = (data ?? []).filter((s) => s.last_24h.challenged > 0);
  if (challenged.length === 0) return null;

  return (
    <div className="rounded-md border border-amber-500/40 bg-amber-50 p-3 text-sm text-amber-800 dark:bg-amber-900/20 dark:text-amber-300">
      {challenged.map((s) => (
        <p key={s.name}>
          {sourceDisplayName(s.name)} blocked {s.last_24h.challenged} of{" "}
          {s.last_24h.attempted} items in the last 24 hours.
        </p>
      ))}
      <p className="mt-1 text-xs opacity-80">
        Counts every failed scrape attempt in the window, not confirmed bot
        blocks alone.
      </p>
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
  const columns = useMemo(() => buildBookColumns(), []);
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

      <ScrapeHealthBanner />

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
