// SourceCard — one card per configured source (name, enabled toggle, schedule,
// concurrency, jitter, per-book delay min/max), with "Run now" and expandable
// "Recent runs" history.
//
// Draft state lives locally per card; `Save` opens a generic
// `<DiffPreviewDialog>` listing changed fields, then on confirm PATCHes only
// the delta. Unchanged fields are omitted from the payload — `SourcePatch`
// uses `None`-means-don't-change semantics on the backend.
//
// 409 on "Run now" surfaces as "Source is in backoff — try again later" (the
// scheduler returns 0 when the backoff gate is active). Other errors surface
// generically. Success / failure banners auto-clear after RUN_FLASH_MS.

import { useEffect, useState } from "react";

import { ApiError } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  DiffPreviewDialog,
  type DiffRow,
} from "@/components/settings/DiffPreviewDialog";
import { SourceRunsTable } from "@/components/settings/SourceRunsTable";
import {
  useRunSource,
  useUpdateSource,
  type SourcePatch,
  type SourceStatus,
} from "@/hooks/useSources";
import { formatRelativeTime } from "@/lib/format";
import { formatErrorMessage } from "@/lib/utils";

const RUN_FLASH_MS = 3000;

type Draft = {
  enabled: boolean;
  schedule: string;
  concurrency: number;
  jitter_seconds: number;
  per_book_delay_min: number;
  per_book_delay_max: number;
};

function draftFromStatus(s: SourceStatus): Draft {
  return {
    enabled: s.config.enabled,
    schedule: s.config.schedule,
    concurrency: s.config.concurrency,
    jitter_seconds: s.config.jitter_seconds,
    per_book_delay_min: s.config.per_book_delay_seconds[0],
    per_book_delay_max: s.config.per_book_delay_seconds[1],
  };
}

function draftsEqual(a: Draft, b: Draft): boolean {
  return (
    a.enabled === b.enabled &&
    a.schedule === b.schedule &&
    a.concurrency === b.concurrency &&
    a.jitter_seconds === b.jitter_seconds &&
    a.per_book_delay_min === b.per_book_delay_min &&
    a.per_book_delay_max === b.per_book_delay_max
  );
}

function computeDiff(server: Draft, draft: Draft): DiffRow[] {
  const rows: DiffRow[] = [];
  if (server.enabled !== draft.enabled) {
    rows.push({
      field: "enabled",
      oldValue: String(server.enabled),
      newValue: String(draft.enabled),
    });
  }
  if (server.schedule !== draft.schedule) {
    rows.push({
      field: "schedule",
      oldValue: server.schedule,
      newValue: draft.schedule,
    });
  }
  if (server.concurrency !== draft.concurrency) {
    rows.push({
      field: "concurrency",
      oldValue: String(server.concurrency),
      newValue: String(draft.concurrency),
    });
  }
  if (server.jitter_seconds !== draft.jitter_seconds) {
    rows.push({
      field: "jitter_seconds",
      oldValue: String(server.jitter_seconds),
      newValue: String(draft.jitter_seconds),
    });
  }
  if (
    server.per_book_delay_min !== draft.per_book_delay_min ||
    server.per_book_delay_max !== draft.per_book_delay_max
  ) {
    rows.push({
      field: "per_book_delay_seconds",
      oldValue: `[${server.per_book_delay_min}, ${server.per_book_delay_max}]`,
      newValue: `[${draft.per_book_delay_min}, ${draft.per_book_delay_max}]`,
    });
  }
  return rows;
}

function buildPatch(server: Draft, draft: Draft): SourcePatch {
  const patch: SourcePatch = {};
  if (server.enabled !== draft.enabled) patch.enabled = draft.enabled;
  if (server.schedule !== draft.schedule) patch.schedule = draft.schedule;
  if (server.concurrency !== draft.concurrency) {
    patch.concurrency = draft.concurrency;
  }
  if (server.jitter_seconds !== draft.jitter_seconds) {
    patch.jitter_seconds = draft.jitter_seconds;
  }
  if (
    server.per_book_delay_min !== draft.per_book_delay_min ||
    server.per_book_delay_max !== draft.per_book_delay_max
  ) {
    patch.per_book_delay_seconds = [
      draft.per_book_delay_min,
      draft.per_book_delay_max,
    ];
  }
  return patch;
}

export type SourceCardProps = {
  source: SourceStatus;
};

export function SourceCard({ source }: SourceCardProps) {
  const server = draftFromStatus(source);
  const [draft, setDraft] = useState<Draft>(server);
  const [expanded, setExpanded] = useState(false);
  const [diffOpen, setDiffOpen] = useState(false);
  const [runFlash, setRunFlash] = useState<
    | { kind: "success"; message: string }
    | { kind: "error"; message: string }
    | null
  >(null);

  const update = useUpdateSource();
  const run = useRunSource();

  // Draft realignment after a successful PATCH happens via a `key` on this
  // component in `<Sources>` (server snapshot becomes the mount key) — avoids
  // an effect-driven `setDraft(draftFromStatus(source))` and the
  // `react-hooks/set-state-in-effect` lint that catches it.

  useEffect(() => {
    if (runFlash === null) return;
    const t = setTimeout(() => setRunFlash(null), RUN_FLASH_MS);
    return () => clearTimeout(t);
  }, [runFlash]);

  const dirty = !draftsEqual(server, draft);
  const diff = computeDiff(server, draft);

  const onRunNow = () => {
    run.mutate(source.name, {
      onSuccess: (data) => {
        setRunFlash({
          kind: "success",
          message: `Run triggered, id=${data.run_id}`,
        });
      },
      onError: (err) => {
        const message =
          err instanceof ApiError && err.status === 409
            ? "Source is in backoff — try again later"
            : `Run failed (${formatErrorMessage(err)})`;
        setRunFlash({ kind: "error", message });
      },
    });
  };

  const onSaveClicked = () => {
    update.reset();
    setDiffOpen(true);
  };

  const onConfirmSave = () => {
    const patch = buildPatch(server, draft);
    update.mutate(
      { name: source.name, patch },
      {
        onSuccess: () => {
          setDiffOpen(false);
        },
      },
    );
  };

  const updateErrorMessage = update.error
    ? `Save failed (${formatErrorMessage(update.error)})`
    : null;

  return (
    <article className="space-y-3 rounded-md border border-border bg-card p-4">
      <header className="flex items-center justify-between gap-3">
        <div className="space-y-0.5">
          <h2 className="text-sm font-semibold">{source.name}</h2>
          <p className="text-xs text-muted-foreground">
            Region {source.config.region} · timeout{" "}
            {source.config.timeout_seconds}s · max consecutive errors{" "}
            {source.config.max_consecutive_errors}
          </p>
        </div>
        <label className="flex items-center gap-2 text-xs">
          <span className="text-muted-foreground">
            {draft.enabled ? "Enabled" : "Disabled"}
          </span>
          <Switch
            checked={draft.enabled}
            onCheckedChange={(checked) =>
              setDraft((d) => ({ ...d, enabled: checked }))
            }
            aria-label={`Toggle ${source.name}`}
          />
        </label>
      </header>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor={`schedule-${source.name}`}>Schedule (cron)</Label>
          <Input
            id={`schedule-${source.name}`}
            value={draft.schedule}
            onChange={(e) =>
              setDraft((d) => ({ ...d, schedule: e.target.value }))
            }
            placeholder="0 */6 * * *"
            className="font-mono text-xs"
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={`concurrency-${source.name}`}>Concurrency</Label>
          <Input
            id={`concurrency-${source.name}`}
            type="number"
            min={1}
            value={draft.concurrency}
            onChange={(e) =>
              setDraft((d) => ({
                ...d,
                concurrency: Number(e.target.value) || 0,
              }))
            }
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor={`jitter-${source.name}`}>Jitter (seconds)</Label>
          <Input
            id={`jitter-${source.name}`}
            type="number"
            min={0}
            value={draft.jitter_seconds}
            onChange={(e) =>
              setDraft((d) => ({
                ...d,
                jitter_seconds: Number(e.target.value) || 0,
              }))
            }
          />
        </div>
        <div className="space-y-1.5">
          <Label>Per-book delay (min–max seconds)</Label>
          <div className="flex items-center gap-2">
            <Input
              type="number"
              min={0}
              value={draft.per_book_delay_min}
              onChange={(e) =>
                setDraft((d) => ({
                  ...d,
                  per_book_delay_min: Number(e.target.value) || 0,
                }))
              }
              aria-label="Per-book delay min seconds"
            />
            <span className="text-xs text-muted-foreground">–</span>
            <Input
              type="number"
              min={0}
              value={draft.per_book_delay_max}
              onChange={(e) =>
                setDraft((d) => ({
                  ...d,
                  per_book_delay_max: Number(e.target.value) || 0,
                }))
              }
              aria-label="Per-book delay max seconds"
            />
          </div>
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={onRunNow}
          disabled={run.isPending}
        >
          {run.isPending ? "Running…" : "Run now"}
        </Button>
        <LastRunBadge source={source} />
        <div className="ml-auto flex items-center gap-2">
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={() => setDraft(server)}
            disabled={!dirty || update.isPending}
          >
            Discard
          </Button>
          <Button
            type="button"
            size="sm"
            onClick={onSaveClicked}
            disabled={!dirty || update.isPending}
          >
            Save…
          </Button>
        </div>
      </div>

      {runFlash && (
        <p
          className={`text-xs ${
            runFlash.kind === "success"
              ? "text-emerald-600 dark:text-emerald-400"
              : "text-destructive"
          }`}
        >
          {runFlash.message}
        </p>
      )}

      <details
        open={expanded}
        onToggle={(e) => setExpanded(e.currentTarget.open)}
        className="rounded-md border border-border bg-background/40 p-2"
      >
        <summary className="cursor-pointer text-xs font-medium text-muted-foreground">
          Recent runs (last 10)
        </summary>
        <div className="pt-2">
          <SourceRunsTable name={source.name} enabled={expanded} />
        </div>
      </details>

      <DiffPreviewDialog
        open={diffOpen}
        onOpenChange={(open) => {
          if (!update.isPending) setDiffOpen(open);
        }}
        title={`Save changes to ${source.name}`}
        description="Review the changed fields before writing config.yaml."
        diff={diff}
        onConfirm={onConfirmSave}
        isPending={update.isPending}
        errorMessage={updateErrorMessage}
      />
    </article>
  );
}

function LastRunBadge({ source }: { source: SourceStatus }) {
  const last = source.last_run;
  if (!last) {
    return (
      <span className="text-xs text-muted-foreground">No runs yet</span>
    );
  }
  const tone =
    last.status === "success"
      ? "text-emerald-600 dark:text-emerald-400"
      : last.status === "error"
        ? "text-destructive"
        : last.status === "partial"
          ? "text-amber-600 dark:text-amber-400"
          : "text-muted-foreground";
  return (
    <span className={`text-xs ${tone}`}>
      Last run: {last.status} · {formatRelativeTime(last.started_at)}
    </span>
  );
}
