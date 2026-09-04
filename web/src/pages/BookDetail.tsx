// Book detail (`/books/:id`).
//
// Two queries run in parallel:
//   - `useItem("book", id)`               → book + computed stats, as `Item`
//   - `useItemObservations("book", id)`   → up to 500 most-recent price observations
//
// The page is a stack of cards (header, snapshot, signal, history chart,
// source breakdown, settings, actions) — each card consumes its slice of the
// `Item`/`ItemObservation[]` data and is shared with `ProductDetail.tsx`.
// SettingsPanel and ActionBar own their own mutations and invalidate the
// `["book", id]` cache via TanStack so changes round-trip without a full
// reload — see `@/lib/item`'s `itemDetailQueryKey`/`itemListQueryKey`.
//
// Re-pointed at the shared `Item` hooks/components (T5.3) — `useItem("book",
// id)` used the identical query key and 404-no-retry behaviour as the old
// `useBook`/`useBookObservations` (`@/hooks/useBook`), so this was a
// like-for-like swap, not a behaviour change. Those old hooks are gone now
// (D40 / F7): they'd gone fully unused once `Dashboard.tsx` was re-pointed
// at `useItems` too, so the fix was to delete the whole per-kind family
// rather than leave two hook families producing two different cached
// shapes for the same query key.

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
import { useItem, useItemObservations } from "@/hooks/useItems";
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

  const bookQuery = useItem("book", validId);
  const obsQuery = useItemObservations("book", validId);

  if (validId == null) return <NotFound />;
  if (bookQuery.isLoading) return <PageSkeleton />;
  if (bookQuery.error?.status === 404) return <NotFound />;
  if (bookQuery.isError) return <ErrorBox error={bookQuery.error} />;
  if (!bookQuery.data) return <PageSkeleton />;

  const item = bookQuery.data;
  const observations = obsQuery.data?.items ?? [];

  return (
    <section className="space-y-4">
      <Link to="/" className="text-xs text-muted-foreground hover:text-foreground">
        ← Dashboard
      </Link>

      <HeaderCard item={item} />

      <ActionBar item={item} />

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <SnapshotCard item={item} />
        <SignalCard item={item} />
      </div>

      <PercentileChart item={item} />

      <HistoryChart observations={observations} isLoading={obsQuery.isLoading} />

      <KeepaChart item={item} />

      <SourceBreakdown item={item} observations={observations} />

      {/* Remount Settings whenever the book's updated_at changes so the form
          resets to the saved state without an effect. */}
      <SettingsPanel key={item.updated_at} item={item} />
    </section>
  );
}
