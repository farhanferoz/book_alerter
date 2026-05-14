// Action bar — refetch · mark bought · archive · delete.
//
// Refetch: `POST /api/books/{id}/refetch`. Surfaces the `triggered` and
// `skipped` lists inline below the button bar (no toast infra yet).
//
// Status flips (`bought`, `archived`) and the destructive delete each have
// a confirm dialog. Delete navigates back to `/` on success.

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
import type { Book } from "@/hooks/useBook";

type RefetchResult = components["schemas"]["RefetchResult"];

type ConfirmKind = "bought" | "archived" | "delete" | null;

const CONFIRM_COPY: Record<
  Exclude<ConfirmKind, null>,
  { title: string; body: string; cta: string; ctaVariant: "default" | "destructive" }
> = {
  bought: {
    title: "Mark as bought?",
    body: "Stops scrapes and signals for this book. You can re-activate it later via Settings.",
    cta: "Mark bought",
    ctaVariant: "default",
  },
  archived: {
    title: "Archive this book?",
    body: "Hides the book from the active dashboard and pauses scrapes. The price history is preserved.",
    cta: "Archive",
    ctaVariant: "default",
  },
  delete: {
    title: "Delete this book?",
    body: "Permanently removes the book and all of its observations. This cannot be undone.",
    cta: "Delete",
    ctaVariant: "destructive",
  },
};

export function ActionBar({ book }: { book: Book }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const [confirm, setConfirm] = useState<ConfirmKind>(null);
  const [refetchSummary, setRefetchSummary] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const onMutationError = (err: ApiError) =>
    setActionError(`Action failed (${err.status}) — ${err.message}`);

  const refetch = useMutation<RefetchResult, ApiError>({
    mutationFn: async () => {
      const path =
        `/api/books/${book.id}/refetch` as "/api/books/{book_id}/refetch";
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
      void qc.invalidateQueries({ queryKey: ["book", book.id] });
    },
    onError: onMutationError,
  });

  const patchStatus = useMutation<Book, ApiError, Book["status"]>({
    mutationFn: async (status) => {
      const path = `/api/books/${book.id}` as "/api/books/{book_id}";
      return (await apiPatch(path, { status })) as Book;
    },
    onSuccess: () => {
      setActionError(null);
      setConfirm(null);
      void qc.invalidateQueries({ queryKey: ["book", book.id] });
      void qc.invalidateQueries({ queryKey: ["books"] });
    },
    onError: onMutationError,
  });

  const deleteBook = useMutation<Book, ApiError>({
    mutationFn: async () => {
      // `?hard=true` is the actual destructive delete — without it the
      // backend soft-deletes (status → archived) which we already expose via
      // the Archive button. Confirm-dialog copy matches hard semantics.
      const path =
        `/api/books/${book.id}?hard=true` as "/api/books/{book_id}";
      return (await apiDelete(path)) as Book;
    },
    onSuccess: () => {
      setActionError(null);
      setConfirm(null);
      void qc.invalidateQueries({ queryKey: ["books"] });
      navigate("/");
    },
    onError: onMutationError,
  });

  const onConfirm = () => {
    if (confirm === "bought") patchStatus.mutate("bought");
    else if (confirm === "archived") patchStatus.mutate("archived");
    else if (confirm === "delete") deleteBook.mutate();
  };

  const busy =
    refetch.isPending || patchStatus.isPending || deleteBook.isPending;

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
          disabled={busy || book.status === "bought"}
        >
          Mark bought
        </Button>
        <Button
          variant="outline"
          onClick={() => setConfirm("archived")}
          disabled={busy || book.status === "archived"}
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
        {confirm && (
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>{CONFIRM_COPY[confirm].title}</AlertDialogTitle>
              <AlertDialogDescription>
                {CONFIRM_COPY[confirm].body}
              </AlertDialogDescription>
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
                variant={CONFIRM_COPY[confirm].ctaVariant}
                onClick={onConfirm}
                disabled={busy}
              >
                {busy ? "Working…" : CONFIRM_COPY[confirm].cta}
              </Button>
            </AlertDialogFooter>
          </AlertDialogContent>
        )}
      </AlertDialog>
    </div>
  );
}
