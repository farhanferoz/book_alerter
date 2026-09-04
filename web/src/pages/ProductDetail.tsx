// Product detail (`/products/:id`) — full parity with the books detail
// page (T5.3), built from the exact same cards via the shared `Item`
// abstraction rather than a second, leaner implementation. See
// `BookDetail.tsx`, which this mirrors card-for-card.

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
      <h1 className="text-xl font-semibold">Product not found</h1>
      <p className="text-sm text-muted-foreground">
        This product may have been deleted, or the URL is wrong.
      </p>
      <Link to="/products" className="text-sm text-primary hover:underline">
        ← Back to products
      </Link>
    </section>
  );
}

function ErrorBox({ error }: { error: unknown }) {
  return (
    <div className="rounded-md border border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive">
      Failed to load product: {formatErrorMessage(error)}
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

export function ProductDetail() {
  const { id } = useParams<{ id: string }>();
  const numericId = id != null ? Number(id) : null;
  const validId =
    numericId != null && Number.isFinite(numericId) && numericId > 0
      ? numericId
      : null;

  const productQuery = useItem("product", validId);
  const obsQuery = useItemObservations("product", validId);

  if (validId == null) return <NotFound />;
  if (productQuery.isLoading) return <PageSkeleton />;
  if (productQuery.error?.status === 404) return <NotFound />;
  if (productQuery.isError) return <ErrorBox error={productQuery.error} />;
  if (!productQuery.data) return <PageSkeleton />;

  const item = productQuery.data;
  const observations = obsQuery.data?.items ?? [];

  return (
    <section className="space-y-4">
      <Link
        to="/products"
        className="text-xs text-muted-foreground hover:text-foreground"
      >
        ← Products
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

      {/* Remount Settings whenever the product's updated_at changes so the
          form resets to the saved state without an effect. */}
      <SettingsPanel key={item.updated_at} item={item} />
    </section>
  );
}
