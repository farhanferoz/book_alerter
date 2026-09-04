// Row-level kebab menu for the dashboard tables: refetch · archive · delete.
// Shared between the books `Dashboard` and the products dashboard via
// `Item` — the only book/product difference is which endpoint prefix and
// list query key to use, both plain data keyed off `item.kind`, not a
// branch between two otherwise-different implementations.
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
import {
  itemApiBase,
  itemListQueryKey,
  type Book,
  type Item,
  type Product,
} from "@/lib/item";

type RefetchResult = components["schemas"]["RefetchResult"];
type ConfirmKind = "archive" | "delete" | null;

// Exact wording preserved per kind (`book` matches what BookRowMenu said
// before this generalisation — "no behaviour change for books").
function confirmCopy(kind: Item["kind"], confirm: Exclude<ConfirmKind, null>) {
  const noun = kind === "product" ? "product" : "book";
  return confirm === "archive"
    ? {
        title: `Archive this ${noun}?`,
        body: `Hides the ${noun} from the active dashboard and pauses scrapes. Price history is preserved.`,
        cta: "Archive",
        ctaVariant: "default" as const,
      }
    : {
        title: `Delete this ${noun}?`,
        body: `Permanently removes the ${noun} and all of its observations. This cannot be undone.`,
        cta: "Delete",
        ctaVariant: "destructive" as const,
      };
}

export function ItemRowMenu({ item }: { item: Item }) {
  const qc = useQueryClient();
  const [confirm, setConfirm] = useState<ConfirmKind>(null);
  const base = itemApiBase(item.kind);
  const listKey = itemListQueryKey(item.kind);

  const onError = (label: string) => (err: ApiError) =>
    window.alert(`${label} failed (${err.status}) — ${err.message}`);

  const refetch = useMutation<RefetchResult, ApiError>({
    mutationFn: async () => {
      const path = `${base}/${item.id}/refetch` as
        | "/api/books/{book_id}/refetch"
        | "/api/products/{product_id}/refetch";
      return (await apiPost(path)) as RefetchResult;
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: [listKey] });
    },
    onError: onError("Refetch"),
  });

  const archive = useMutation<Book | Product, ApiError>({
    mutationFn: async () => {
      const path = `${base}/${item.id}` as
        | "/api/books/{book_id}"
        | "/api/products/{product_id}";
      return (await apiPatch(path, { status: "archived" })) as Book | Product;
    },
    onSuccess: () => {
      setConfirm(null);
      void qc.invalidateQueries({ queryKey: [listKey] });
    },
    onError: (err) => {
      setConfirm(null);
      onError("Archive")(err);
    },
  });

  const remove = useMutation<Book | Product, ApiError>({
    mutationFn: async () => {
      const path = `${base}/${item.id}?hard=true` as
        | "/api/books/{book_id}"
        | "/api/products/{product_id}";
      return (await apiDelete(path)) as Book | Product;
    },
    onSuccess: () => {
      setConfirm(null);
      void qc.invalidateQueries({ queryKey: [listKey] });
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
                disabled={busy || item.status === "archived"}
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
    </>
  );
}
