// Header card — cover thumbnail, title, author, format, status badge, ISBN.
//
// Read-only in Phase 10.3; the spec's "inline-editable" header is deferred to
// a follow-up so this task ships in a bounded slice. The Settings panel below
// covers every editable field that matters for alerting.

import type { Book } from "@/hooks/useBook";

const STATUS_LABEL: Record<Book["status"], string> = {
  active: "Active",
  bought: "Bought",
  archived: "Archived",
};

const STATUS_CLASS: Record<Book["status"], string> = {
  active:
    "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300",
  bought:
    "bg-emerald-200 text-emerald-900 dark:bg-emerald-900/60 dark:text-emerald-200",
  archived:
    "bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-400",
};

const FORMAT_LABEL: Record<Book["format"], string> = {
  paperback: "Paperback",
  hardcover: "Hardcover",
  any: "Any format",
};

export function HeaderCard({ book }: { book: Book }) {
  return (
    <div className="flex gap-4 rounded-md border border-border bg-card p-4">
      {book.cover_url ? (
        <img
          src={book.cover_url}
          alt=""
          className="h-28 w-20 rounded object-cover"
          loading="lazy"
        />
      ) : (
        <div className="h-28 w-20 rounded bg-muted" aria-hidden />
      )}
      <div className="flex flex-1 flex-col gap-2">
        <div className="flex items-center gap-2">
          <span
            className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_CLASS[book.status]}`}
          >
            {STATUS_LABEL[book.status]}
          </span>
          <span className="text-xs text-muted-foreground">
            {FORMAT_LABEL[book.format]}
          </span>
          <span className="text-xs text-muted-foreground">·</span>
          <span className="text-xs text-muted-foreground">
            {book.region} · {book.currency}
          </span>
        </div>
        <h1 className="text-xl font-semibold leading-tight">{book.title}</h1>
        <p className="text-sm text-muted-foreground">{book.author}</p>
        <p className="text-xs font-mono text-muted-foreground">
          ISBN-13: {book.isbn13}
        </p>
      </div>
    </div>
  );
}
