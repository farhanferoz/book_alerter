// Add-book modal — Phase 11.1 (ISBN + Search tabs).
//
// Two flows, both end in `POST /api/books` with the same `BookCreate` body:
//
//   1. ISBN tab (Phase 10.2 — unchanged behaviour): paste ISBN → blur fires
//      `/api/metadata/lookup` → preview card → confirm.
//   2. Search tab (Phase 11.1): debounced free-text query → `/api/metadata/search`
//      → click a hit to create.
//
// The two panel components live in this file; the create-book mutation is
// hoisted into a tiny `useCreateBook` hook so both panels share invalidation +
// error semantics. State design is the same as Phase 10.2 — the form panels
// mount/unmount with the dialog so state resets on close automatically (no
// effect-driven reset). The Tabs primitive is uncontrolled — switching tabs
// preserves each panel's local state for the lifetime of one open session,
// which is what users expect (typed text doesn't vanish when you peek at the
// other tab).
//
// 409 behaviour: backend returns the detail string only (no book id), so we
// show "Already tracked." inline. Wiring a link to the existing row needs a
// backend change (return id on conflict, or look it up via `/api/books`); both
// are out of scope for 11.1.

import { useEffect, useMemo, useState } from "react";
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
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

type BookMetadata = components["schemas"]["BookMetadata"];
type BookMetadataWithIsbn = components["schemas"]["BookMetadataWithIsbn"];
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

// Tiny inline debounce hook — one call site (the search panel), so the cost
// of adding a dep (`use-debounce` etc.) outweighs the 8-line hook.
function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(t);
  }, [value, delayMs]);
  return debounced;
}

// Shared create-book mutation. Both panels invalidate `["books"]` and close
// the dialog on success. The 409 / 422 surfacing is left to the caller via
// the returned `error` (with `ApiError.status`) — the messages differ per
// panel only slightly today, but extracting them would obscure intent.
function useCreateBook(onSuccess: () => void) {
  const qc = useQueryClient();
  return useMutation<BookOut, ApiError, BookCreate>({
    mutationFn: async (body) =>
      (await apiPost("/api/books", body)) as BookOut,
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ["books"] });
      onSuccess();
    },
  });
}

export function AddBookModal({ open, onOpenChange }: AddBookModalProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Add a book</DialogTitle>
          <DialogDescription>
            Paste an ISBN or search by title / author. We&apos;ll fetch cover
            art and metadata from OpenLibrary / Google Books.
          </DialogDescription>
        </DialogHeader>

        {/* Mount the tabs only when open so each panel's state resets on
            close — no effect-driven reset required. */}
        {open && (
          <Tabs defaultValue="isbn" className="gap-4">
            <TabsList className="grid w-full grid-cols-2">
              <TabsTrigger value="isbn">ISBN</TabsTrigger>
              <TabsTrigger value="search">Search</TabsTrigger>
            </TabsList>
            <TabsContent value="isbn">
              <AddBookByIsbn onDone={() => onOpenChange(false)} />
            </TabsContent>
            <TabsContent value="search">
              <AddBookBySearch onDone={() => onOpenChange(false)} />
            </TabsContent>
          </Tabs>
        )}
      </DialogContent>
    </Dialog>
  );
}

function AddBookByIsbn({ onDone }: { onDone: () => void }) {
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

  const create = useCreateBook(onDone);

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

// Backend `q` is `Query(..., min_length=1)`; we use ≥ 2 client-side to avoid
// noisy one-char queries (Google Books returns garbage for those anyway). The
// `enabled` gate is the source of truth — react-query won't dispatch until the
// debounced value crosses the threshold.
const MIN_QUERY_LEN = 2;
const DEBOUNCE_MS = 300;
const SEARCH_LIMIT = 10;

function AddBookBySearch({ onDone }: { onDone: () => void }) {
  const [query, setQuery] = useState("");
  const debounced = useDebouncedValue(query.trim(), DEBOUNCE_MS);
  const [pendingIsbn, setPendingIsbn] = useState<string | null>(null);

  const search = useQuery<BookMetadataWithIsbn[], ApiError>({
    queryKey: ["metadata-search", debounced],
    queryFn: async () => {
      const url = `/api/metadata/search?q=${encodeURIComponent(
        debounced,
      )}&limit=${SEARCH_LIMIT}`;
      return (await apiGet(
        url as "/api/metadata/search",
      )) as BookMetadataWithIsbn[];
    },
    enabled: debounced.length >= MIN_QUERY_LEN,
    retry: false,
    staleTime: 5 * 60 * 1000,
  });

  const create = useCreateBook(onDone);

  const onPickHit = (hit: BookMetadataWithIsbn) => {
    if (create.isPending) return;
    setPendingIsbn(hit.isbn13);
    create.mutate({
      isbn: hit.isbn13,
      title: hit.title,
      author: hit.author,
      cover_url: hit.cover_url ?? null,
      format: "any",
    });
  };

  const createErrorMessage = (): string | null => {
    if (!create.error) return null;
    if (create.error.status === 409) return "Already tracked.";
    if (create.error.status === 422) {
      return "Invalid book details (backend rejected).";
    }
    return `Save failed (${create.error.status}).`;
  };

  // `search_books` server-side already drops items missing isbn/title/author,
  // so we trust the shape and only deal with the empty-array case here.
  const results = search.data ?? [];
  const shouldShowResults = debounced.length >= MIN_QUERY_LEN;

  return (
    <div className="space-y-4">
      <div className="space-y-1.5">
        <Label htmlFor="add-book-search">Search</Label>
        <Input
          id="add-book-search"
          autoFocus
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Search by title or author"
        />
        <p className="text-xs text-muted-foreground">
          Free-text search via Google Books. We only show results with a usable
          ISBN.
        </p>
      </div>

      {shouldShowResults && search.isPending && (
        <ul className="space-y-2">
          {Array.from({ length: 3 }).map((_, i) => (
            <li
              key={i}
              className="flex gap-3 rounded-md border border-border p-3"
            >
              <Skeleton className="h-16 w-12 rounded" tone="muted" />
              <div className="flex flex-1 flex-col gap-2">
                <Skeleton className="h-4 w-3/4 rounded" tone="muted" />
                <Skeleton className="h-3 w-1/2 rounded" tone="muted" />
              </div>
            </li>
          ))}
        </ul>
      )}

      {shouldShowResults && search.error && (
        <p className="text-xs text-amber-600 dark:text-amber-400">
          Search failed ({search.error.status}). Try again or use the ISBN tab.
        </p>
      )}

      {shouldShowResults &&
        !search.isPending &&
        !search.error &&
        results.length === 0 && (
          <p className="text-xs text-muted-foreground">
            No matches. Try a different query or use the ISBN tab.
          </p>
        )}

      {shouldShowResults && !search.isPending && results.length > 0 && (
        <ul className="max-h-80 space-y-2 overflow-y-auto">
          {results.map((hit) => {
            const isPickPending =
              create.isPending && pendingIsbn === hit.isbn13;
            return (
              <li key={hit.isbn13}>
                <button
                  type="button"
                  onClick={() => onPickHit(hit)}
                  disabled={create.isPending}
                  className="flex w-full gap-3 rounded-md border border-border p-3 text-left transition-colors hover:bg-accent hover:text-accent-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {hit.cover_url ? (
                    <img
                      src={hit.cover_url}
                      alt=""
                      className="h-16 w-12 rounded object-cover"
                    />
                  ) : (
                    <div className="h-16 w-12 rounded bg-muted/40" />
                  )}
                  <div className="flex flex-1 flex-col gap-1 text-sm">
                    <span className="font-medium leading-tight">
                      {hit.title}
                    </span>
                    <span className="text-xs text-muted-foreground">
                      {hit.author}
                    </span>
                    <span className="font-mono text-[10px] text-muted-foreground">
                      ISBN {hit.isbn13}
                    </span>
                  </div>
                  <span className="self-center text-xs font-medium text-muted-foreground">
                    {isPickPending ? "Adding…" : "Track"}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}

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
      </DialogFooter>
    </div>
  );
}
