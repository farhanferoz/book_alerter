// Single alert row, reused by the sidebar and the full Alerts page.
//
// Layout: kind pill · book title (linked to /books/:id) · message · relative
// timestamp · dismiss "X" button. The book title comes from the optional
// `bookTitle` prop — callers look it up against `useBooks()` (cached). When
// the title is unknown we fall back to "Book #<id>" so the row is still
// useful.
//
// `compact` shrinks the row for the sidebar (tighter padding, smaller text);
// the page passes `compact={false}` for a roomier list.

import { Link } from "react-router-dom";
import { XIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { formatMoneyMinor, formatRelativeTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Alert } from "@/hooks/useAlerts";

import { AlertKindBadge } from "./AlertKindBadge";

type Props = {
  alert: Alert;
  bookTitle?: string;
  onDismiss?: (id: number) => void;
  dismissing?: boolean;
  compact?: boolean;
};

export function AlertItem({
  alert,
  bookTitle,
  onDismiss,
  dismissing = false,
  compact = false,
}: Props) {
  const isDismissed = alert.dismissed_at !== null;
  const title = bookTitle ?? `Book #${alert.book_id}`;
  return (
    <article
      className={cn(
        "flex items-start gap-2 rounded-md border border-border bg-card text-card-foreground",
        compact ? "p-2 text-xs" : "p-3 text-sm",
        isDismissed && "opacity-60",
      )}
    >
      <div className="flex-1 min-w-0 space-y-1">
        <div className="flex items-center gap-2 flex-wrap">
          <AlertKindBadge kind={alert.kind} />
          <Link
            to={`/books/${alert.book_id}`}
            className="font-medium truncate hover:underline"
            title={title}
          >
            {title}
          </Link>
        </div>
        <p className={cn("text-muted-foreground", compact && "line-clamp-2")}>
          {alert.message}
        </p>
        <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
          <span>{formatMoneyMinor(alert.price_minor, alert.currency)}</span>
          <span aria-hidden>·</span>
          <span className="uppercase">{alert.source}</span>
          <span aria-hidden>·</span>
          <span className="uppercase">{alert.condition.replace(/_/g, " ")}</span>
          <span aria-hidden>·</span>
          <span title={alert.fired_at}>{formatRelativeTime(alert.fired_at)}</span>
        </div>
      </div>
      {onDismiss && !isDismissed && (
        <Button
          variant="ghost"
          size="sm"
          className="h-6 w-6 p-0 shrink-0"
          onClick={() => onDismiss(alert.id)}
          disabled={dismissing}
          aria-label="Dismiss alert"
          title="Dismiss"
        >
          <XIcon className="size-3.5" />
        </Button>
      )}
    </article>
  );
}
