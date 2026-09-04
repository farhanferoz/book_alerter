// Action bar — refetch · mark bought · archive · delete.
//
// Refetch: `POST /api/books/{id}/refetch` or `/api/products/{id}/refetch`.
// Surfaces the `triggered` and `skipped` lists inline below the button bar
// (no toast infra yet).
//
// Status flips (`bought`, `archived`) and the destructive delete each have
// a confirm dialog. Delete navigates back to the dashboard on success.
//
// `?hard=true` on delete is the actual destructive delete — without it the
// backend soft-deletes (status → archived), which the Archive button
// already exposes. Confirm-dialog copy matches hard semantics. Preserved
// exactly from the pre-generalisation ActionBar/ProductDetail — omitting
// it once made the products delete button silently archive while its
// confirm dialog promised a cascading delete.

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";

import { ApiError, apiDelete, apiPatch, apiPost } from "@/api/client";
import type { components } from "@/api/schema";
import {
  AlertDialog,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import {
  itemApiBase,
  itemDetailQueryKey,
  itemListHref,
  itemListQueryKey,
  type Item,
} from "@/lib/item";

type RefetchResult = components["schemas"]["RefetchResult"];

type ConfirmKind = "bought" | "archived" | "delete" | null;

// Exact wording preserved for books ("no behaviour change for books").
function confirmCopy(kind: Item["kind"], confirm: Exclude<ConfirmKind, null>) {
  const noun = kind === "product" ? "product" : "book";
  switch (confirm) {
    case "bought":
      return {
        title: "Mark as bought?",
        body: `Stops scrapes and signals for this ${noun}. You can re-activate it later via Settings.`,
        cta: "Mark bought",
        ctaVariant: "default" as const,
      };
    case "archived":
      return {
        title: `Archive this ${noun}?`,
        body: `Hides the ${noun} from the active dashboard and pauses scrapes. The price history is preserved.`,
        cta: "Archive",
        ctaVariant: "default" as const,
      };
    case "delete":
      return {
        title: `Delete this ${noun}?`,
        body: `Permanently removes the ${noun} and all of its observations. This cannot be undone.`,
        cta: "Delete",
        ctaVariant: "destructive" as const,
      };
  }
}

export function ActionBar({ item }: { item: Item }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [confirm, setConfirm] = useState<ConfirmKind>(null);
  const [refetchSummary, setRefetchSummary] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const base = itemApiBase(item.kind);
  const detailKey = itemDetailQueryKey(item.kind);
  const listKey = itemListQueryKey(item.kind);

  const onMutationError = (err: ApiError) =>
    setActionError(`Action failed (${err.status}) — ${err.message}`);

  const refetch = useMutation<RefetchResult, ApiError>({
    mutationFn: async () => {
      const path = `${base}/${item.id}/refetch` as
        | "/api/books/{book_id}/refetch"
        | "/api/products/{product_id}/refetch";
      return (await apiPost(path)) as RefetchResult;
    },
    onSuccess: (result) => {
      setActionError(null);
      const triggered = result.triggered.map((t) => t.source);
      const skipped = result.skipped.map(
        (s) => `${s.source} (${s.reason.replace("_", " ")})`,
      );
      const parts: string[] = [];
      parts.push(
        triggered.length
          ? `Triggered: ${triggered.join(", ")}`
          : "No sources triggered.",
      );
      if (skipped.length) parts.push(`Skipped: ${skipped.join(", ")}`);
      setRefetchSummary(parts.join(" · "));
      // Refetched scrapes land asynchronously; invalidate so the page picks
      // up the new observations on the next poll/visit.
      void qc.invalidateQueries({ queryKey: [detailKey, item.id] });
    },
    onError: onMutationError,
  });

  const patchStatus = useMutation<Item, ApiError, Item["status"]>({
    mutationFn: async (status) => {
      const path = `${base}/${item.id}` as
        | "/api/books/{book_id}"
        | "/api/products/{product_id}";
      return (await apiPatch(path, { status })) as Item;
    },
    onSuccess: () => {
      setActionError(null);
      setConfirm(null);
      void qc.invalidateQueries({ queryKey: [detailKey, item.id] });
      void qc.invalidateQueries({ queryKey: [listKey] });
    },
    onError: onMutationError,
  });

  const deleteItem = useMutation<Item, ApiError>({
    mutationFn: async () => {
      const path = `${base}/${item.id}?hard=true` as
        | "/api/books/{book_id}"
        | "/api/products/{product_id}";
      return (await apiDelete(path)) as Item;
    },
    onSuccess: () => {
      setActionError(null);
      setConfirm(null);
      void qc.invalidateQueries({ queryKey: [listKey] });
      navigate(itemListHref(item.kind));
    },
    onError: onMutationError,
  });

  const onConfirm = () => {
    if (confirm === "bought") patchStatus.mutate("bought");
    else if (confirm === "archived") patchStatus.mutate("archived");
    else if (confirm === "delete") deleteItem.mutate();
  };

  const busy =
    refetch.isPending || patchStatus.isPending || deleteItem.isPending;

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-2">
        <Button
          variant="outline"
          onClick={() => refetch.mutate()}
          disabled={busy}
        >
          {refetch.isPending ? "Refetching…" : "Refetch now"}
        </Button>
        <Button
          variant="outline"
          onClick={() => setConfirm("bought")}
          disabled={busy || item.status === "bought"}
        >
          Mark bought
        </Button>
        <Button
          variant="outline"
          onClick={() => setConfirm("archived")}
          disabled={busy || item.status === "archived"}
        >
          Archive
        </Button>
        <Button
          variant="destructive"
          onClick={() => setConfirm("delete")}
          disabled={busy}
        >
          Delete
        </Button>
      </div>

      {refetchSummary && (
        <p className="text-xs text-muted-foreground">{refetchSummary}</p>
      )}
      {actionError && <p className="text-xs text-destructive">{actionError}</p>}

      <AlertDialog
        open={confirm !== null}
        onOpenChange={(open) => {
          if (!open) setConfirm(null);
        }}
      >
        {confirm && (() => {
          const copy = confirmCopy(item.kind, confirm);
          return (
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>{copy.title}</AlertDialogTitle>
                <AlertDialogDescription>{copy.body}</AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <Button
                  variant="outline"
                  onClick={() => setConfirm(null)}
                  disabled={busy}
                >
                  Cancel
                </Button>
                <Button
                  variant={copy.ctaVariant}
                  onClick={onConfirm}
                  disabled={busy}
                >
                  {busy ? "Working…" : copy.cta}
                </Button>
              </AlertDialogFooter>
            </AlertDialogContent>
          );
        })()}
      </AlertDialog>
    </div>
  );
}
