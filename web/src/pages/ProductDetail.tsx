// Product detail — minimum viable view: image, title, current best, history table,
// refetch button, mute / target-price PATCH.
//
// Lean compared with BookDetail: no chart, no signal pill, no condition
// dropdown. Functional and consistent with the books layout language.

import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { apiDelete, apiPatch, apiPost, ApiError } from "@/api/client";
import type { components } from "@/api/schema";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import {
  useProduct,
  useProductObservations,
  type Product,
  type ProductObservation,
} from "@/hooks/useProduct";
import { formatDateTime, formatMoneyMinor } from "@/lib/format";
import { formatErrorMessage } from "@/lib/utils";

type ProductPatch = components["schemas"]["ProductPatch"];

function ObservationRow({ obs }: { obs: ProductObservation }) {
  return (
    <tr className="border-b border-border last:border-b-0">
      <td className="p-2 text-xs">{formatDateTime(obs.observed_at)}</td>
      <td className="p-2 text-xs">{obs.source}</td>
      <td className="p-2 text-xs">{obs.seller ?? "—"}</td>
      <td className="p-2 text-xs">{obs.condition}</td>
      <td className="p-2 text-xs text-right tabular-nums">
        {formatMoneyMinor(obs.price_minor, obs.currency)}
      </td>
      <td className="p-2 text-xs text-right tabular-nums">
        {formatMoneyMinor(obs.shipping_minor, obs.currency)}
      </td>
      <td className="p-2 text-xs text-right tabular-nums font-medium">
        {formatMoneyMinor(obs.total_minor, obs.currency)}
      </td>
    </tr>
  );
}

function useRefetchProduct(productId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      const path =
        `/api/products/${productId}/refetch` as "/api/products/{product_id}/refetch";
      return await apiPost(path);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["product", productId] });
      qc.invalidateQueries({ queryKey: ["products"] });
    },
  });
}

function usePatchProduct(productId: number) {
  const qc = useQueryClient();
  return useMutation<Product, ApiError, ProductPatch>({
    mutationFn: async (patch) => {
      const res = await apiPatch(
        `/api/products/${productId}` as "/api/products/{product_id}",
        patch,
      );
      return res as Product;
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["product", productId] });
      qc.invalidateQueries({ queryKey: ["products"] });
    },
  });
}

function useDeleteProduct(productId: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async () => {
      // `?hard=true` is the actual destructive delete — without it the backend
      // soft-deletes (status → archived), which the Archive button already
      // does. Omitting it made this button silently archive while its confirm
      // dialog promised a cascading delete.
      const path =
        `/api/products/${productId}?hard=true` as "/api/products/{product_id}";
      return await apiDelete(path);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["products"] });
    },
  });
}

export function ProductDetail() {
  const params = useParams<{ id: string }>();
  const id = params.id ? Number(params.id) : null;
  const navigate = useNavigate();
  const product = useProduct(id);
  const observations = useProductObservations(id);

  const refetch = useRefetchProduct(id ?? 0);
  const patch = usePatchProduct(id ?? 0);
  const remove = useDeleteProduct(id ?? 0);

  const [targetPriceMinor, setTargetPriceMinor] = useState<string>("");

  if (product.isLoading) {
    return <Skeleton className="h-40" />;
  }
  if (product.isError) {
    return (
      <div className="rounded-md border border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive">
        Failed to load product: {formatErrorMessage(product.error)}
      </div>
    );
  }
  const p = product.data!;

  const onTargetSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const minor = Number(targetPriceMinor);
    if (!Number.isFinite(minor) || minor < 0) return;
    patch.mutate({ target_price_minor: minor });
    setTargetPriceMinor("");
  };

  const onTrackUsedToggle = (checked: boolean) => {
    patch.mutate({ track_used: checked });
  };

  const onArchive = () => {
    if (
      confirm(
        `Archive ${p.title}?\n\nIt stops being tracked but keeps its ` +
          `price history, and can be restored later.`,
      )
    ) {
      patch.mutate({ status: "archived" });
    }
  };

  const onDelete = () => {
    if (
      confirm(
        `Permanently delete ${p.title}?\n\nThis removes the product and ` +
          `cascades through its observations and alerts. It cannot be undone — ` +
          `use Archive instead to keep the history.`,
      )
    ) {
      remove.mutate(undefined, {
        onSuccess: () => navigate("/products"),
      });
    }
  };

  return (
    <section className="space-y-4">
      <header className="flex items-start gap-4">
        {p.image_url ? (
          <img
            src={p.image_url}
            alt=""
            className="h-32 w-32 rounded object-cover"
          />
        ) : (
          <div
            className="h-32 w-32 rounded bg-muted text-muted-foreground flex items-center justify-center text-xs"
            aria-hidden
          >
            —
          </div>
        )}
        <div className="flex-1 min-w-0">
          <h1 className="text-xl font-semibold">{p.title}</h1>
          <p className="text-xs text-muted-foreground">
            {p.brand ?? <em>no brand</em>} · ASIN {p.asin} · status {p.status}
          </p>
          <p className="mt-2 text-2xl font-medium tabular-nums">
            {formatMoneyMinor(p.stats.current_best_total_minor, p.currency)}
          </p>
          <p className="text-xs text-muted-foreground">
            Best across {p.stats.observation_count} observations · last seen{" "}
            {formatDateTime(p.stats.last_polled_at)}
          </p>
        </div>
        <div className="space-y-2">
          <Button
            onClick={() => refetch.mutate()}
            disabled={refetch.isPending}
            className="w-full"
          >
            {refetch.isPending ? "Refetching..." : "Refetch now"}
          </Button>
          <Button
            variant="outline"
            onClick={onArchive}
            disabled={patch.isPending}
            className="w-full"
          >
            Archive
          </Button>
          <Button
            variant="destructive"
            onClick={onDelete}
            disabled={remove.isPending}
            className="w-full"
          >
            Delete permanently
          </Button>
        </div>
      </header>

      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <form
          onSubmit={onTargetSubmit}
          className="rounded-md border border-border p-3 space-y-2"
        >
          <Label htmlFor="target-price">Target price (pence)</Label>
          <div className="flex gap-2">
            <Input
              id="target-price"
              type="number"
              min={0}
              step={1}
              placeholder={p.target_price_minor?.toString() ?? "e.g. 2500"}
              value={targetPriceMinor}
              onChange={(e) => setTargetPriceMinor(e.target.value)}
            />
            <Button type="submit" disabled={patch.isPending}>
              Save
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            Current: {formatMoneyMinor(p.target_price_minor, p.currency)}
          </p>
        </form>

        <div className="rounded-md border border-border p-3 flex items-center justify-between">
          <div>
            <Label htmlFor="track-used">Track used market</Label>
            <p className="text-xs text-muted-foreground">
              When on, also tracks used grades from the Amazon offer-listing
              page. Default off — most non-book products have no meaningful
              used market.
            </p>
          </div>
          <Switch
            id="track-used"
            checked={p.track_used}
            onCheckedChange={onTrackUsedToggle}
            disabled={patch.isPending}
          />
        </div>
      </div>

      <div>
        <h2 className="text-sm font-semibold mb-2">Recent observations</h2>
        {observations.isLoading ? (
          <Skeleton className="h-32" />
        ) : observations.data && observations.data.items.length > 0 ? (
          <div className="overflow-x-auto rounded-md border border-border">
            <table className="w-full text-sm">
              <thead className="bg-muted/50 text-left text-xs uppercase tracking-wider text-muted-foreground">
                <tr>
                  <th className="p-2">When</th>
                  <th className="p-2">Source</th>
                  <th className="p-2">Seller</th>
                  <th className="p-2">Condition</th>
                  <th className="p-2 text-right">Price</th>
                  <th className="p-2 text-right">Shipping</th>
                  <th className="p-2 text-right">Total</th>
                </tr>
              </thead>
              <tbody>
                {observations.data.items.map((o) => (
                  <ObservationRow key={o.id} obs={o} />
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="rounded-md border border-dashed border-border p-6 text-center text-xs text-muted-foreground">
            No observations yet. Try "Refetch now" or wait for the next
            scheduled scrape.
          </div>
        )}
      </div>
    </section>
  );
}
