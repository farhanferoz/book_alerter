// Book detail (`/books/:id`).
//
// Two queries run in parallel:
//   - `useBook(id)`              → book + computed stats
//   - `useBookObservations(id)`  → up to 500 most-recent price observations
//
// The page is a stack of cards (header, snapshot, signal, history chart,
// source breakdown, settings, actions) — each card consumes its slice of the
// data. SettingsPanel and ActionBar own their own mutations and invalidate
// the `["book", id]` cache via TanStack so changes round-trip without a
// full reload.

import { Link, useParams } from "react-router-dom";

import { Skeleton } from "@/components/ui/skeleton";
import { ActionBar } from "@/components/books/detail/ActionBar";
import { HeaderCard } from "@/components/books/detail/HeaderCard";
import { HistoryChart } from "@/components/books/detail/HistoryChart";
import { KeepaChart } from "@/components/books/detail/KeepaChart";
import { PercentileChart } from "@/components/books/detail/PercentileChart";
import { SettingsPanel } from "@/components/books/detail/SettingsPanel";
import { SignalCard } from "@/components/books/detail/SignalCard";
import { SnapshotCard } from "@/components/books/detail/SnapshotCard";
import { SourceBreakdown } from "@/components/books/detail/SourceBreakdown";
import { useBook, useBookObservations } from "@/hooks/useBook";
import { formatErrorMessage } from "@/lib/utils";

function NotFound() {
  return (
    <section className="space-y-3">
      <h1 className="text-xl font-semibold">Book not found</h1>
      <p className="text-sm text-muted-foreground">
        This book may have been deleted, or the URL is wrong.
      </p>
      <Link to="/" className="text-sm text-primary hover:underline">
        ← Back to dashboard
      </Link>
    </section>
  );
}

function ErrorBox({ error }: { error: unknown }) {
  return (
    <div className="rounded-md border border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive">
      Failed to load book: {formatErrorMessage(error)}
    </div>
  );
}

function PageSkeleton() {
  return (
    <div className="space-y-4">
      <Skeleton className="h-32" />
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <Skeleton className="h-32" />
        <Skeleton className="h-32" />
      </div>
      <Skeleton className="h-72" />
    </div>
  );
}

export function BookDetail() {
  const { id } = useParams<{ id: string }>();
  const numericId = id != null ? Number(id) : null;
  const validId =
    numericId != null && Number.isFinite(numericId) && numericId > 0
      ? numericId
      : null;

  const bookQuery = useBook(validId);
  const obsQuery = useBookObservations(validId);

  if (validId == null) return <NotFound />;
  if (bookQuery.isLoading) return <PageSkeleton />;
  if (bookQuery.error?.status === 404) return <NotFound />;
  if (bookQuery.isError) return <ErrorBox error={bookQuery.error} />;
  if (!bookQuery.data) return <PageSkeleton />;

  const book = bookQuery.data;
  const observations = obsQuery.data?.items ?? [];

  return (
    <section className="space-y-4">
      <Link to="/" className="text-xs text-muted-foreground hover:text-foreground">
        ← Dashboard
      </Link>

      <HeaderCard book={book} />

      <ActionBar book={book} />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <SnapshotCard book={book} />
        <SignalCard book={book} />
      </div>

      <PercentileChart book={book} />

      <HistoryChart observations={observations} isLoading={obsQuery.isLoading} />

      <KeepaChart bookId={book.id} />

      <SourceBreakdown book={book} observations={observations} />

      {/* Remount Settings whenever the book's updated_at changes so the form
          resets to the saved state without an effect. */}
      <SettingsPanel key={book.updated_at} book={book} />
    </section>
  );
}
