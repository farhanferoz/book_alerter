// Settings → Sources tab (Phase 11.2).
//
// Lists every configured source as a `<SourceCard>`. Editing/run-now flows
// are encapsulated in the card; this page only handles the list-level
// loading / error / empty states.

import { SourceCard } from "@/components/settings/SourceCard";
import { Skeleton } from "@/components/ui/skeleton";
import { useSources, type SourceStatus } from "@/hooks/useSources";
import { formatErrorMessage } from "@/lib/utils";

export function SettingsSources() {
  const sources = useSources();

  if (sources.isPending) {
    return (
      <div className="space-y-3">
        {Array.from({ length: 3 }).map((_, i) => (
          <Skeleton key={i} className="h-40 w-full" />
        ))}
      </div>
    );
  }

  if (sources.error) {
    return (
      <p className="text-sm text-destructive">
        Failed to load sources: {formatErrorMessage(sources.error)}
      </p>
    );
  }

  const rows = sources.data ?? [];
  if (rows.length === 0) {
    return (
      <p className="text-sm text-muted-foreground">
        No sources configured. Add entries under <code>sources:</code> in{" "}
        <code>config.yaml</code> (or via the Advanced tab).
      </p>
    );
  }

  return (
    <div className="space-y-3">
      {rows.map((source) => (
        // Composite key — when a PATCH succeeds and the server values change,
        // the card remounts and `useState(server)` re-initialises the draft.
        // Avoids an effect-driven `setDraft` (caught by `set-state-in-effect`).
        <SourceCard key={sourceMountKey(source)} source={source} />
      ))}
    </div>
  );
}

function sourceMountKey(source: SourceStatus): string {
  const c = source.config;
  return [
    source.name,
    c.enabled,
    c.schedule,
    c.concurrency,
    c.jitter_seconds,
    c.per_book_delay_seconds[0],
    c.per_book_delay_seconds[1],
  ].join("|");
}
