// Add-product modal — single tab (paste ASIN or full Amazon URL).
//
// Mirrors `AddBookModal` in spirit but trimmer: products don't have a free-
// text search analogue (Amazon doesn't expose a public search API and we'd
// have to scrape SERPs, which is out of scope). Paste-and-preview is the
// only flow.
//
// Flow: user pastes input → debounced → POST /api/metadata/asin-lookup
// fills title / image / brand → user confirms → POST /api/products.
//
// T4.1: Confirm is enabled as soon as the input looks like a valid ASIN/URL
// (`looksLikeAsinOrUrl`), not gated on the lookup succeeding — the lookup
// launches a browser and can 502 on a bot challenge, and there is no reason
// add-product should block on Amazon being scrapeable right now (F7). When
// `title` is omitted, `POST /api/products` creates the row with a
// placeholder title and `metadata_status: "pending"`; a background job and
// the product scraper's own next successful scrape both race to fill in
// the real title/image later (see the pending badge on the product row,
// `components/books/columns.tsx`).

import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
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

// Pull the duplicate product's id out of a 409 ApiError body. Backend returns
// `{detail: {message, product_id, asin}}`; narrow defensively. Symmetric with
// `duplicateBookIdFromError` in `components/books/AddBookModal.tsx` — kept
// inline here rather than extracted because the books / products surfaces
// don't share a common error file yet and the dedup target would be one
// helper.
function duplicateProductIdFromError(err: ApiError | null | undefined): number | null {
  if (!err || err.status !== 409) return null;
  const body = err.body;
  if (!body || typeof body !== "object") return null;
  const detail = (body as { detail?: unknown }).detail;
  if (!detail || typeof detail !== "object") return null;
  const id = (detail as { product_id?: unknown }).product_id;
  return typeof id === "number" ? id : null;
}

function CreateProductError({
  error,
  onDone,
}: {
  error: ApiError | null | undefined;
  onDone: () => void;
}) {
  if (!error) return null;
  if (error.status === 409) {
    const id = duplicateProductIdFromError(error);
    return (
      <p className="text-xs text-destructive">
        Already tracked.{" "}
        {id !== null && (
          <Link to={`/products/${id}`} onClick={onDone} className="underline">
            View product
          </Link>
        )}
      </p>
    );
  }
  return (
    <p className="text-xs text-destructive">
      Failed to add product: {formatErrorMessage(error)}
    </p>
  );
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
  // Confirm-enablement reads the LIVE input, matching the header comment's
  // stated intent ("enabled as soon as the input looks like a valid
  // ASIN/URL") -- the debounce exists to throttle the Playwright-backed
  // lookup query below, not to gate the button.
  const enabled = looksLikeAsinOrUrl(input);

  const lookup = useQuery<ProductMetadata, ApiError>({
    queryKey: ["product-asin-lookup", debounced],
    queryFn: async () => {
      const res = await apiPost("/api/metadata/asin-lookup", {
        input: debounced,
      });
      return res as ProductMetadata;
    },
    enabled: looksLikeAsinOrUrl(debounced),
    retry: false,
  });

  // `lookup.data` describes `debounced`, which lags up to 450ms behind
  // `input`. Editing the field again before the debounce catches up (or
  // just after a paste) leaves `lookup.data` describing a DIFFERENT
  // ASIN/URL than what `input` now holds. Gating on `debounced === input`
  // -- i.e. the debounce has genuinely caught up -- is what stops a stale
  // preview from ever being attached to a submission for a different
  // product: found in review as a real, unrecoverable bug (F1) where
  // editing ASIN A -> B and confirming inside the debounce window created
  // product B carrying A's title/image/brand, with no repair path once
  // the backend accepts a title and marks metadata_status "ok".
  const metadataIsCurrent = debounced === input;
  const preview = metadataIsCurrent ? lookup.data : undefined;

  const create = useCreateProduct(onDone);

  const onConfirm = () => {
    if (!enabled) return;
    create.mutate({
      // Raw input, not lookup.data.asin -- the lookup may not have
      // resolved yet. The backend's `to_asin` does the same normalisation
      // (bare ASIN or any Amazon URL shape) that the lookup endpoint uses.
      asin_or_url: input.trim(),
      // `preview`, not `lookup.data` -- omitted (not empty-string) both
      // when the lookup hasn't landed yet AND when it landed for a since-
      // edited input, so the backend generates the placeholder title and
      // metadata_status "pending" (self-healing) rather than persisting a
      // stale or blank title as "ok" (permanent, per F1).
      title: preview?.title ?? null,
      image_url: preview?.image_url ?? null,
      brand: preview?.brand ?? null,
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

        {enabled && !lookup.isLoading && preview && (
          <div className="flex gap-3 rounded-md border border-border p-3">
            {preview.image_url ? (
              <img
                src={preview.image_url}
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
                {preview.title}
              </div>
              <div className="text-xs text-muted-foreground">
                {preview.brand ?? <em>no brand</em>} · ASIN{" "}
                {preview.asin}
              </div>
            </div>
          </div>
        )}

        {/* T4.1: no preview yet (lookup still failing/not-yet-resolved, or
            stale for a since-edited input -- see `preview` above) is not a
            reason to block adding the product -- Confirm is already enabled
            below. Covers the lookup.isError case too: whichever reason the
            preview isn't here, the product still gets added with a
            placeholder title that fills in later. */}
        {enabled && !lookup.isLoading && !preview && (
          <p className="text-xs text-muted-foreground">
            Details will be filled in after the first scrape.
          </p>
        )}

        <CreateProductError error={create.error} onDone={onDone} />
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
          disabled={!enabled || create.isPending}
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
