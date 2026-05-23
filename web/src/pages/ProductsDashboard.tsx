// Products dashboard — minimal table of tracked Amazon products.
//
// Deliberately leaner than the books `Dashboard` (no signal/sort filter
// bar, no DataTable wrapper) — products are a thinner surface at MVP and
// the user can extend if it grows. Keeps the diff small so the books
// path stays untouched.

import { useState } from "react";
import { Link } from "react-router-dom";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useProducts, type Product } from "@/hooks/useProducts";
import { formatErrorMessage } from "@/lib/utils";

import { AddProductModal } from "@/components/products/AddProductModal";

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

function priceCell(minor: number | null | undefined, currency: string): string {
  if (minor == null) return "—";
  return `${currency} ${(minor / 100).toFixed(2)}`;
}

function ProductRow({ product }: { product: Product }) {
  return (
    <tr className="border-b border-border last:border-b-0 hover:bg-accent/30">
      <td className="p-3 align-middle">
        {product.image_url ? (
          <img
            src={product.image_url}
            alt=""
            className="h-12 w-12 rounded object-cover"
            loading="lazy"
          />
        ) : (
          <div
            className="h-12 w-12 rounded bg-muted text-muted-foreground flex items-center justify-center text-xs"
            aria-hidden
          >
            —
          </div>
        )}
      </td>
      <td className="p-3 align-middle">
        <Link
          to={`/products/${product.id}`}
          className="font-medium hover:underline"
        >
          {product.title}
        </Link>
        <div className="text-xs text-muted-foreground">
          {product.brand ?? <em>no brand</em>} · ASIN {product.asin}
        </div>
      </td>
      <td className="p-3 align-middle text-right tabular-nums">
        {priceCell(
          product.stats.current_best_total_minor,
          product.currency,
        )}
      </td>
      <td className="p-3 align-middle text-right text-xs text-muted-foreground">
        {product.stats.observation_count}
      </td>
      <td className="p-3 align-middle text-xs">
        {product.track_used ? "new + used" : "new only"}
      </td>
      <td className="p-3 align-middle">
        {product.last_scrape_error ? (
          <span
            className="inline-block h-2 w-2 rounded-full bg-destructive"
            title={product.last_scrape_error}
            aria-label="scrape error"
          />
        ) : (
          <span className="text-xs text-muted-foreground">ok</span>
        )}
      </td>
    </tr>
  );
}

export function ProductsDashboard() {
  const [addOpen, setAddOpen] = useState(false);
  const { data, isLoading, isError, error } = useProducts();

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

      {isLoading ? (
        <SkeletonRows />
      ) : isError ? (
        <ErrorCard error={error} />
      ) : !data || data.length === 0 ? (
        <EmptyState onAddProduct={onAddProduct} />
      ) : (
        <div className="overflow-x-auto rounded-md border border-border">
          <table className="w-full text-sm">
            <thead className="bg-muted/50 text-left text-xs uppercase tracking-wider text-muted-foreground">
              <tr>
                <th className="p-3">Image</th>
                <th className="p-3">Title</th>
                <th className="p-3 text-right">Best price</th>
                <th className="p-3 text-right">Obs.</th>
                <th className="p-3">Scope</th>
                <th className="p-3">Health</th>
              </tr>
            </thead>
            <tbody>
              {data.map((p) => (
                <ProductRow key={p.id} product={p} />
              ))}
            </tbody>
          </table>
        </div>
      )}

      <AddProductModal open={addOpen} onOpenChange={setAddOpen} />
    </section>
  );
}
