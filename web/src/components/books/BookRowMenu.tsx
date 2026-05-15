// Row-level kebab menu for the Dashboard table: refetch · archive · delete.
//
// Mirrors the mutation patterns in `detail/ActionBar.tsx` but scoped to a
// single row, with shorter copy and no inline result rendering — TanStack
// Query invalidation drives the visual update (archived rows disappear
// from the default view; deleted rows are gone). Failures surface via
// window.alert since there's no toast infra yet (matches the precedent
// noted in ActionBar's header comment).

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Menu } from "@base-ui/react/menu";
import { MoreVerticalIcon } from "lucide-react";

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
import type { Book } from "@/hooks/useBooks";

type RefetchResult = components["schemas"]["RefetchResult"];
type ConfirmKind = "archive" | "delete" | null;

const CONFIRM_COPY: Record<
  Exclude<ConfirmKind, null>,
  { title: string; body: string; cta: string; ctaVariant: "default" | "destructive" }
> = {
  archive: {
    title: "Archive this book?",
    body: "Hides the book from the active dashboard and pauses scrapes. Price history is preserved.",
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

export function BookRowMenu({ book }: { book: Book }) {
  const qc = useQueryClient();
  const [confirm, setConfirm] = useState<ConfirmKind>(null);

  const onError = (label: string) => (err: ApiError) =>
    window.alert(`${label} failed (${err.status}) — ${err.message}`);

  const refetch = useMutation<RefetchResult, ApiError>({
    mutationFn: async () => {
      const path =
        `/api/books/${book.id}/refetch` as "/api/books/{book_id}/refetch";
      return (await apiPost(path)) as RefetchResult;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["books"] });
    },
    onError: onError("Refetch"),
  });

  const archive = useMutation<Book, ApiError>({
    mutationFn: async () => {
      const path = `/api/books/${book.id}` as "/api/books/{book_id}";
      return (await apiPatch(path, { status: "archived" })) as Book;
    },
    onSuccess: () => {
      setConfirm(null);
      void qc.invalidateQueries({ queryKey: ["books"] });
    },
    onError: (err) => {
      setConfirm(null);
      onError("Archive")(err);
    },
  });

  const remove = useMutation<Book, ApiError>({
    mutationFn: async () => {
      const path =
        `/api/books/${book.id}?hard=true` as "/api/books/{book_id}";
      return (await apiDelete(path)) as Book;
    },
    onSuccess: () => {
      setConfirm(null);
      void qc.invalidateQueries({ queryKey: ["books"] });
    },
    onError: (err) => {
      setConfirm(null);
      onError("Delete")(err);
    },
  });

  const busy = refetch.isPending || archive.isPending || remove.isPending;

  const onConfirm = () => {
    if (confirm === "archive") archive.mutate();
    else if (confirm === "delete") remove.mutate();
  };

  return (
    <>
      <Menu.Root>
        <Menu.Trigger
          render={
            <Button variant="ghost" size="icon-sm" aria-label="Row actions" />
          }
        >
          <MoreVerticalIcon className="size-4" />
        </Menu.Trigger>
        <Menu.Portal>
          <Menu.Positioner side="bottom" align="end" sideOffset={4}>
            <Menu.Popup className="z-50 min-w-[8rem] rounded-md border border-border bg-popover p-1 text-sm text-popover-foreground shadow-md outline-none">
              <Menu.Item
                disabled={busy}
                onClick={() => refetch.mutate()}
                className="flex cursor-default items-center rounded-sm px-2 py-1.5 outline-none data-highlighted:bg-accent data-highlighted:text-accent-foreground data-disabled:pointer-events-none data-disabled:opacity-50"
              >
                {refetch.isPending ? "Refetching…" : "Refetch"}
              </Menu.Item>
              <Menu.Item
                disabled={busy || book.status === "archived"}
                onClick={() => setConfirm("archive")}
                className="flex cursor-default items-center rounded-sm px-2 py-1.5 outline-none data-highlighted:bg-accent data-highlighted:text-accent-foreground data-disabled:pointer-events-none data-disabled:opacity-50"
              >
                Archive
              </Menu.Item>
              <Menu.Item
                disabled={busy}
                onClick={() => setConfirm("delete")}
                className="flex cursor-default items-center rounded-sm px-2 py-1.5 text-destructive outline-none data-highlighted:bg-destructive/10 data-disabled:pointer-events-none data-disabled:opacity-50"
              >
                Delete
              </Menu.Item>
            </Menu.Popup>
          </Menu.Positioner>
        </Menu.Portal>
      </Menu.Root>

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
    </>
  );
}
