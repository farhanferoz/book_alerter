// Settings → Sources tab (Phase 11.2).
//
// Lists every configured source as a `<SourceCard>`. Editing/run-now flows
// are encapsulated in the card; this page handles the list-level loading /
// error / empty states plus the "Run all enabled" fan-out.

import { useState } from "react";
import { useQueryClient } from "@tanstack/react-query";

import { ApiError, apiPost } from "@/api/client";
import { SourceCard } from "@/components/settings/SourceCard";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { useSources, type SourceStatus } from "@/hooks/useSources";
import { formatErrorMessage } from "@/lib/utils";

type RunAllResult = {
  triggered: string[];
  inBackoff: string[];
  failed: { name: string; message: string }[];
};

export function SettingsSources() {
  const sources = useSources();
  const qc = useQueryClient();
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<RunAllResult | null>(null);

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

  const enabledNames = rows.filter((r) => r.config.enabled).map((r) => r.name);

  const onRunAll = async () => {
    setRunning(true);
    setResult(null);
    const outcomes = await Promise.allSettled(
      enabledNames.map((name) =>
        apiPost(`/api/sources/${encodeURIComponent(name)}/run` as
          "/api/sources/{name}/run"),
      ),
    );
    const triggered: string[] = [];
    const inBackoff: string[] = [];
    const failed: { name: string; message: string }[] = [];
    outcomes.forEach((o, i) => {
      const name = enabledNames[i];
      if (o.status === "fulfilled") {
        triggered.push(name);
      } else if (o.reason instanceof ApiError && o.reason.status === 409) {
        inBackoff.push(name);
      } else {
        failed.push({ name, message: formatErrorMessage(o.reason) });
      }
    });
    setResult({ triggered, inBackoff, failed });
    setRunning(false);
    void qc.invalidateQueries({ queryKey: ["sources"] });
    enabledNames.forEach((n) =>
      qc.invalidateQueries({ queryKey: ["source-runs", n] }),
    );
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between">
        <p className="text-xs text-muted-foreground">
          {enabledNames.length} of {rows.length} source
          {rows.length === 1 ? "" : "s"} enabled
        </p>
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={onRunAll}
          disabled={running || enabledNames.length === 0}
        >
          {running ? "Running…" : "Run all enabled"}
        </Button>
      </div>

      {result && <RunAllSummary result={result} />}

      {rows.map((source) => (
        // Composite key — when a PATCH succeeds and the server values change,
        // the card remounts and `useState(server)` re-initialises the draft.
        // Avoids an effect-driven `setDraft` (caught by `set-state-in-effect`).
        <SourceCard key={sourceMountKey(source)} source={source} />
      ))}
    </div>
  );
}

function RunAllSummary({ result }: { result: RunAllResult }) {
  const parts: string[] = [];
  if (result.triggered.length > 0) {
    parts.push(`Triggered: ${result.triggered.join(", ")}`);
  }
  if (result.inBackoff.length > 0) {
    parts.push(`In backoff: ${result.inBackoff.join(", ")}`);
  }
  if (result.failed.length > 0) {
    parts.push(
      `Failed: ${result.failed.map((f) => `${f.name} (${f.message})`).join(", ")}`,
    );
  }
  const tone =
    result.failed.length > 0
      ? "text-destructive"
      : result.inBackoff.length > 0
        ? "text-amber-600 dark:text-amber-400"
        : "text-emerald-600 dark:text-emerald-400";
  return <p className={`text-xs ${tone}`}>{parts.join(" · ")}</p>;
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
