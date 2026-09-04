// Single alert row, reused by the sidebar and the full Alerts page.
//
// Layout: kind pill · item title (linked to the item's detail page) · message ·
// relative timestamp · dismiss "X" button. The title and the item identity come
// from the alert itself — the backend resolves them — so this row renders a
// book alert and a product alert identically and needs no lookup table.
//
// `compact` shrinks the row for the sidebar (tighter padding, smaller text);
// the page passes `compact={false}` for a roomier list.

import { Link } from "react-router-dom";
import { XIcon } from "lucide-react";

import { Button } from "@/components/ui/button";
import { formatMoneyMinor, formatRelativeTime } from "@/lib/format";
import { cn } from "@/lib/utils";
import { alertRef, type Alert, type AlertRef } from "@/hooks/useAlerts";

import { AlertKindBadge } from "./AlertKindBadge";

type Props = {
  alert: Alert;
  onDismiss?: (ref: AlertRef) => void;
  dismissing?: boolean;
  compact?: boolean;
};

/** Detail route for the item an alert fired for. */
function alertItemHref(alert: Alert): string {
  const segment = alert.item_kind === "product" ? "products" : "books";
  return `/${segment}/${alert.item_id}`;
}

export function AlertItem({
  alert,
  onDismiss,
  dismissing = false,
  compact = false,
}: Props) {
  const isDismissed = alert.dismissed_at !== null;
  const title = alert.title;
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
            to={alertItemHref(alert)}
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
          onClick={() => onDismiss(alertRef(alert))}
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
