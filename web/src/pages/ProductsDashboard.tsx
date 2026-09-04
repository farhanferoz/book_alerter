// Products dashboard — reuses the books dashboard's table, columns,
// filters, mini-bars, signal pill and row menu via the shared `Item`
// abstraction (`@/lib/item`) instead of growing a second copy of each of
// them. `BookFilters`/`DataTable` are kind-neutral despite their names
// (see their own files) and are used here unchanged; `buildItemColumns`
// (`components/books/columns.tsx`) is the same column set as the books
// `Dashboard`, driven off `Item` instead of `Book`.

import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { AddProductModal } from "@/components/products/AddProductModal";
import {
  BookFilters,
  DEFAULT_FILTERS,
  type BookFiltersValue,
} from "@/components/books/BookFilters";
import { DataTable } from "@/components/books/BookTable";
import { buildItemColumns } from "@/components/books/columns";
import { ScrapeHealthBanner } from "@/components/books/ScrapeHealthBanner";
import { useItems } from "@/hooks/useItems";
import { applyItemFilters } from "@/lib/item";
import { formatErrorMessage } from "@/lib/utils";

function SkeletonRows() {
  return (
    <div className="space-y-2">
      {[0, 1, 2].map((i) => (
        <Skeleton key={i} className="h-12" tone="muted" aria-hidden />
      ))}
    </div>
  );
}

function EmptyState({ onAddProduct }: { onAddProduct: () => void }) {
  return (
    <div className="flex flex-col items-center justify-center rounded-md border border-dashed border-border p-10 text-center">
      <p className="text-sm font-medium">No products yet</p>
      <p className="mt-1 text-xs text-muted-foreground">
        Paste an Amazon UK ASIN or product URL to start tracking prices.
      </p>
      <Button className="mt-4" onClick={onAddProduct}>
        Add your first product
      </Button>
    </div>
  );
}

function ErrorCard({ error }: { error: unknown }) {
  return (
    <div className="rounded-md border border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive">
      Failed to load products: {formatErrorMessage(error)}
    </div>
  );
}

export function ProductsDashboard() {
  const [filters, setFilters] = useState<BookFiltersValue>(DEFAULT_FILTERS);
  const [addOpen, setAddOpen] = useState(false);
  const includeArchived = filters.status === "archived" || filters.status === "all";
  const { data, isLoading, isError, error } = useItems("product", {
    include_archived: includeArchived,
  });
  const columns = useMemo(() => buildItemColumns(), []);
  const filtered = useMemo(
    () => (data ? applyItemFilters(data, filters) : []),
    [data, filters],
  );

  const onAddProduct = () => setAddOpen(true);

  return (
    <section className="space-y-4">
      <header className="flex items-center justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold">Products</h1>
          <p className="text-xs text-muted-foreground">
            Tracked non-book Amazon products. Live prices via Amazon UK +
            historical backfill via Keepa.
          </p>
        </div>
        <Button onClick={onAddProduct}>Add product</Button>
      </header>

      <ScrapeHealthBanner scope="product" />

      <BookFilters value={filters} onChange={setFilters} />

      {isLoading ? (
        <SkeletonRows />
      ) : isError ? (
        <ErrorCard error={error} />
      ) : !data || data.length === 0 ? (
        <EmptyState onAddProduct={onAddProduct} />
      ) : (
        <DataTable
          columns={columns}
          data={filtered}
          emptyMessage="No products match the current filters."
        />
      )}

      <AddProductModal open={addOpen} onOpenChange={setAddOpen} />
    </section>
  );
}
