// Add-book modal — Phase 10.2 (ISBN tab only).
//
// The spec calls for two tabs (paste-ISBN + search) — Phase 11.1 ships the
// search tab. For now we render the ISBN form directly so the diff stays
// small; the second tab can slot in here via shadcn `tabs` when 11.1 lands.
// See the "TAB STRIP HERE" comment below for the insertion point.
//
// Flow:
//   1. User types an ISBN. We strip whitespace/hyphens and validate against
//      a permissive regex (9 digits + check char OR 13 digits). Backend
//      does the authoritative `to_isbn13` parse on create.
//   2. On blur — once the local format check passes — we fire
//      `/api/metadata/lookup?isbn=...` via TanStack Query. While pending we
//      show a skeleton; on 404 we surface "not found" but still allow the
//      user to confirm (the backend create handler does NOT require metadata
//      — it stores whatever fields we send). On 422 (bad ISBN per backend)
//      we surface the backend's detail message.
//   3. Confirm POSTs `/api/books`. On 409 (duplicate ISBN) we show an inline
//      "Already tracked" message — the backend currently returns the
//      detail string only (no book id), so we don't link out yet.
//   4. On success: invalidate `["books"]`, close.
//
// State design: the form is split into its own `<AddBookForm>` component so
// it mounts/unmounts with the dialog — that gives us automatic reset on
// close, no `useEffect` chasing the `open` prop. Title/author use a
// "user-edited" sentinel pattern (`null` = take from metadata, otherwise
// take from user) instead of an effect that copies the lookup data into
// state — that pattern trips `react-hooks/set-state-in-effect` and is also
// genuinely harder to reason about.

import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { apiGet, apiPost, ApiError } from "@/api/client";
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

type BookMetadata = components["schemas"]["BookMetadata"];
type BookOut = components["schemas"]["BookOut"];
type BookCreate = components["schemas"]["BookCreate"];

export type AddBookModalProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

// Strip everything except digits and the ISBN-10 check char `X`. The backend
// canonical form is 13 digits, but ISBN-10 is accepted on input (we let
// `to_isbn13` upgrade it server-side).
function normalizeIsbn(raw: string): string {
  return raw.replace(/[\s-]/g, "").toUpperCase();
}

// Permissive regex matching ISBN-10 (9 digits + check digit/`X`) or
// ISBN-13 (13 digits). This is a syntactic gate only — the backend runs
// the real checksum via `isbnlib.to_isbn13`.
const ISBN_RE = /^(?:\d{9}[\dX]|\d{13})$/;

function isSyntacticallyValid(raw: string): boolean {
  return ISBN_RE.test(normalizeIsbn(raw));
}

export function AddBookModal({ open, onOpenChange }: AddBookModalProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Add a book</DialogTitle>
          <DialogDescription>
            Paste an ISBN-10 or ISBN-13. We&apos;ll fetch the cover and title
            from OpenLibrary / Google Books.
          </DialogDescription>
        </DialogHeader>

        {/* TAB STRIP HERE — Phase 11.1 inserts <Tabs> wrapping the form below
            plus a second TabPanel for the search flow. */}

        {/* Mount the form only when open so state resets automatically on
            close — no effect-driven reset required. */}
        {open && <AddBookForm onDone={() => onOpenChange(false)} />}
      </DialogContent>
    </Dialog>
  );
}

function AddBookForm({ onDone }: { onDone: () => void }) {
  const qc = useQueryClient();
  const [isbn, setIsbn] = useState("");
  // `null` means "use the metadata value when available" — diverges to a
  // string the moment the user types in the field. Sentinel pattern avoids
  // copying lookup.data into state via an effect.
  const [titleEdit, setTitleEdit] = useState<string | null>(null);
  const [authorEdit, setAuthorEdit] = useState<string | null>(null);
  // `lookupKey` is the ISBN we've committed to looking up (set on blur);
  // typing alone doesn't refetch.
  const [lookupKey, setLookupKey] = useState<string | null>(null);

  const normalized = useMemo(() => normalizeIsbn(isbn), [isbn]);
  const valid = useMemo(() => isSyntacticallyValid(isbn), [isbn]);

  const lookup = useQuery<BookMetadata, ApiError>({
    queryKey: ["metadata-lookup", lookupKey],
    queryFn: async () => {
      const url = `/api/metadata/lookup?isbn=${encodeURIComponent(lookupKey ?? "")}`;
      return (await apiGet(url as "/api/metadata/lookup")) as BookMetadata;
    },
    enabled: Boolean(lookupKey),
    retry: false,
    staleTime: 5 * 60 * 1000,
  });

  // Derived display values — metadata wins until the user edits the field.
  const titleValue = titleEdit ?? lookup.data?.title ?? "";
  const authorValue = authorEdit ?? lookup.data?.author ?? "";
  const coverUrl = lookup.data?.cover_url ?? null;

  const create = useMutation<BookOut, ApiError, BookCreate>({
    mutationFn: async (body) =>
      (await apiPost("/api/books", body)) as BookOut,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["books"] });
      onDone();
    },
  });

  const onBlurIsbn = () => {
    if (valid) setLookupKey(normalized);
  };

  const onSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const trimmedTitle = titleValue.trim();
    const trimmedAuthor = authorValue.trim();
    if (!valid || !trimmedTitle || !trimmedAuthor) return;
    create.mutate({
      isbn: normalized,
      title: trimmedTitle,
      author: trimmedAuthor,
      cover_url: coverUrl,
      format: "any",
    });
  };

  const lookupErrorMessage = (): string | null => {
    if (!lookup.error) return null;
    if (lookup.error.status === 404) {
      return "Not found in metadata sources. Fill in title and author manually.";
    }
    if (lookup.error.status === 422) {
      return "Backend rejected this ISBN. Double-check the digits.";
    }
    return "Lookup failed — you can still confirm manually.";
  };

  const createErrorMessage = (): string | null => {
    if (!create.error) return null;
    if (create.error.status === 409) return "Already tracked.";
    if (create.error.status === 422) {
      return "Invalid book details. Check ISBN, title, and author.";
    }
    return `Save failed (${create.error.status}).`;
  };

  const submitDisabled =
    !valid ||
    !titleValue.trim() ||
    !authorValue.trim() ||
    create.isPending;

  return (
    <form className="space-y-4" onSubmit={onSubmit}>
      <div className="space-y-1.5">
        <Label htmlFor="add-book-isbn">ISBN</Label>
        <Input
          id="add-book-isbn"
          autoFocus
          value={isbn}
          onChange={(e) => setIsbn(e.target.value)}
          onBlur={onBlurIsbn}
          placeholder="9780099490548"
          aria-invalid={isbn !== "" && !valid}
        />
        <p className="text-xs text-muted-foreground">
          ISBN-10 (10 chars) or ISBN-13 (13 digits). Hyphens and spaces OK.
        </p>
      </div>

      {/* Metadata preview / status */}
      {lookup.isPending && lookupKey !== null && (
        <div className="flex gap-3 rounded-md border border-border p-3">
          <Skeleton className="h-16 w-12 rounded" tone="muted" />
          <div className="flex flex-1 flex-col gap-2">
            <Skeleton className="h-4 w-3/4 rounded" tone="muted" />
            <Skeleton className="h-3 w-1/2 rounded" tone="muted" />
          </div>
        </div>
      )}

      {lookup.data && !lookup.isPending && (
        <div className="flex gap-3 rounded-md border border-border p-3">
          {coverUrl ? (
            <img
              src={coverUrl}
              alt=""
              className="h-16 w-12 rounded object-cover"
            />
          ) : (
            <div className="h-16 w-12 rounded bg-muted/40" />
          )}
          <div className="flex flex-1 flex-col gap-1 text-sm">
            <span className="font-medium leading-tight">{lookup.data.title}</span>
            <span className="text-xs text-muted-foreground">
              {lookup.data.author}
            </span>
          </div>
        </div>
      )}

      {lookup.error && (
        <p className="text-xs text-amber-600 dark:text-amber-400">
          {lookupErrorMessage()}
        </p>
      )}

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="add-book-title">Title</Label>
          <Input
            id="add-book-title"
            value={titleValue}
            onChange={(e) => setTitleEdit(e.target.value)}
            required
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="add-book-author">Author</Label>
          <Input
            id="add-book-author"
            value={authorValue}
            onChange={(e) => setAuthorEdit(e.target.value)}
            required
          />
        </div>
      </div>

      {createErrorMessage() && (
        <p className="text-xs text-destructive">{createErrorMessage()}</p>
      )}

      <DialogFooter>
        <Button
          type="button"
          variant="outline"
          onClick={onDone}
          disabled={create.isPending}
        >
          Cancel
        </Button>
        <Button type="submit" disabled={submitDisabled}>
          {create.isPending ? "Adding…" : "Add book"}
        </Button>
      </DialogFooter>
    </form>
  );
}
