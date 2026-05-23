// Add-product modal — single tab (paste ASIN or full Amazon URL).
//
// Mirrors `AddBookModal` in spirit but trimmer: products don't have a free-
// text search analogue (Amazon doesn't expose a public search API and we'd
// have to scrape SERPs, which is out of scope). Paste-and-preview is the
// only flow.
//
// Flow: user pastes input → debounced → POST /api/metadata/asin-lookup
// fills title / image / brand → user confirms → POST /api/products.

import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiPost, ApiError } from "@/api/client";
import type { components } from "@/api/schema";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { formatErrorMessage } from "@/lib/utils";

type ProductMetadata = components["schemas"]["ProductMetadata"];
type ProductOut = components["schemas"]["ProductOut"];
type ProductCreate = components["schemas"]["ProductCreate"];

export type AddProductModalProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(t);
  }, [value, delayMs]);
  return debounced;
}

// Cheap pre-flight check so we don't fire a Playwright scrape on every
// keystroke. We accept anything that COULD be a URL or a 10-char ASIN; the
// server runs the real validation in `to_asin`.
function looksLikeAsinOrUrl(raw: string): boolean {
  const s = raw.trim();
  if (!s) return false;
  if (/^[A-Z0-9]{10}$/i.test(s)) return true;
  return /amazon|\bdp\b|gp\/product/i.test(s);
}

function useCreateProduct(onSuccess: (p: ProductOut) => void) {
  const qc = useQueryClient();
  return useMutation<ProductOut, ApiError, ProductCreate>({
    mutationFn: async (body) => {
      const res = await apiPost("/api/products", body);
      return res as ProductOut;
    },
    onSuccess: (product) => {
      qc.invalidateQueries({ queryKey: ["products"] });
      onSuccess(product);
    },
  });
}

/**
 * Inner panel — mounted/unmounted with the dialog so React naturally resets
 * input + mutation state when the modal closes. Mirrors the AddBookModal
 * pattern documented in RESUME.md ("mount-key remount" section).
 */
function AddProductBody({ onDone }: { onDone: () => void }) {
  const [input, setInput] = useState("");
  const debounced = useDebouncedValue(input, 450);
  const enabled = looksLikeAsinOrUrl(debounced);

  const lookup = useQuery<ProductMetadata, ApiError>({
    queryKey: ["product-asin-lookup", debounced],
    queryFn: async () => {
      const res = await apiPost("/api/metadata/asin-lookup", {
        input: debounced,
      });
      return res as ProductMetadata;
    },
    enabled,
    retry: false,
  });

  const create = useCreateProduct(onDone);

  const onConfirm = () => {
    if (!lookup.data) return;
    create.mutate({
      asin_or_url: lookup.data.asin,
      title: lookup.data.title,
      image_url: lookup.data.image_url ?? null,
      brand: lookup.data.brand ?? null,
      // OpenAPI emits track_used as required (Pydantic field has a default
      // but no Optional). Default-off is the product-side convention; user
      // flips it later on the detail page.
      track_used: false,
    });
  };

  return (
    <>
      <div className="space-y-3">
        <div className="space-y-2">
          <Label htmlFor="add-product-input">ASIN or URL</Label>
          <Input
            id="add-product-input"
            autoFocus
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="B07XYZ1234 or https://www.amazon.co.uk/dp/..."
          />
        </div>

        {enabled && lookup.isLoading && (
          <div className="space-y-2">
            <Skeleton className="h-4 w-2/3" tone="muted" />
            <Skeleton className="h-3 w-1/2" tone="muted" />
          </div>
        )}

        {lookup.isError && (
          <p className="text-xs text-destructive">
            Couldn't fetch metadata: {formatErrorMessage(lookup.error)}
          </p>
        )}

        {lookup.data && (
          <div className="flex gap-3 rounded-md border border-border p-3">
            {lookup.data.image_url ? (
              <img
                src={lookup.data.image_url}
                alt=""
                className="h-20 w-20 rounded object-cover"
              />
            ) : (
              <div
                className="h-20 w-20 rounded bg-muted text-muted-foreground flex items-center justify-center text-xs"
                aria-hidden
              >
                —
              </div>
            )}
            <div className="min-w-0 flex-1">
              <div className="text-sm font-medium truncate">
                {lookup.data.title}
              </div>
              <div className="text-xs text-muted-foreground">
                {lookup.data.brand ?? <em>no brand</em>} · ASIN{" "}
                {lookup.data.asin}
              </div>
            </div>
          </div>
        )}

        {create.isError && (
          <p className="text-xs text-destructive">
            {create.error.status === 409
              ? "Already tracked."
              : `Failed to add product: ${formatErrorMessage(create.error)}`}
          </p>
        )}
      </div>

      <DialogFooter>
        <Button
          variant="outline"
          onClick={onDone}
          disabled={create.isPending}
        >
          Cancel
        </Button>
        <Button
          onClick={onConfirm}
          disabled={!lookup.data || create.isPending}
        >
          {create.isPending ? "Adding..." : "Add product"}
        </Button>
      </DialogFooter>
    </>
  );
}

export function AddProductModal({ open, onOpenChange }: AddProductModalProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Add product</DialogTitle>
          <DialogDescription>
            Paste an Amazon UK ASIN (10 chars, e.g.{" "}
            <code className="text-xs">B07XYZ1234</code>) or the full dp URL.
          </DialogDescription>
        </DialogHeader>

        {open && <AddProductBody onDone={() => onOpenChange(false)} />}
      </DialogContent>
    </Dialog>
  );
}
