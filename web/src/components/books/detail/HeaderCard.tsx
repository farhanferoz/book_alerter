// Header card — cover thumbnail, title, subtitle (author/brand), format
// (books only), status badge, ISBN/ASIN.
//
// Read-only in Phase 10.3; the spec's "inline-editable" header is deferred to
// a follow-up so this task ships in a bounded slice. The Settings panel below
// covers every editable field that matters for alerting.

import type { Book, Item } from "@/lib/item";

import { CoverImage } from "@/components/books/CoverImage";

const STATUS_LABEL: Record<Item["status"], string> = {
  active: "Active",
  bought: "Bought",
  archived: "Archived",
};

const STATUS_CLASS: Record<Item["status"], string> = {
  active:
    "bg-green-100 text-green-800 dark:bg-green-900/40 dark:text-green-300",
  bought:
    "bg-emerald-200 text-emerald-900 dark:bg-emerald-900/60 dark:text-emerald-200",
  archived:
    "bg-neutral-100 text-neutral-600 dark:bg-neutral-800 dark:text-neutral-400",
};

// Book-only — a product has no format concept.
const BOOK_FORMAT_LABEL: Record<Book["format"], string> = {
  paperback: "Paperback",
  hardcover: "Hardcover",
  any: "Any format",
};

export function HeaderCard({ item }: { item: Item }) {
  return (
    <div className="flex gap-4 rounded-md border border-border bg-card p-4">
      <CoverImage src={item.imageUrl} className="h-28 w-20 rounded" />
      <div className="flex flex-1 flex-col gap-2">
        <div className="flex items-center gap-2">
          <span
            className={`inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${STATUS_CLASS[item.status]}`}
          >
            {STATUS_LABEL[item.status]}
          </span>
          {item.kind === "book" && (
            <span className="text-xs text-muted-foreground">
              {BOOK_FORMAT_LABEL[item.format]}
            </span>
          )}
          <span className="text-xs text-muted-foreground">·</span>
          <span className="text-xs text-muted-foreground">
            {item.region} · {item.currency}
          </span>
        </div>
        <h1 className="text-xl font-semibold leading-tight">{item.title}</h1>
        <p className="text-sm text-muted-foreground">
          {item.subtitle ?? (
            <em>no {item.kind === "product" ? "brand" : "author"}</em>
          )}
        </p>
        <p className="text-xs font-mono text-muted-foreground">
          {item.kind === "product"
            ? `ASIN: ${item.asin}`
            : `ISBN-13: ${item.isbn13}`}
        </p>
      </div>
    </div>
  );
}
