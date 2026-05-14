// Settings → Notifications tab (Phase 11.4).
//
// Edits the full `NotificationsConfig` block:
//   - channels: { inapp, ntfy } — per-channel cards (`<ChannelCard>` family).
//   - alert_kinds_enabled: list[AlertKind] — `<AlertKindsEditor>`.
//   - quiet_hours: QuietHours | None — `<QuietHoursEditor>`.
//
// Save flow mirrors Phase 11.3's recommendation tab:
//   1. Local draft initialised from server snapshot (mount-key remount after
//      successful PUT re-initialises the form against the new server state).
//   2. "Save…" calls `PUT /api/config?dry_run=true` to fetch the server-
//      validated diff, then opens `<DiffPreviewDialog>`.
//   3. On confirm, second mutate with `dryRun: false` applies + closes the
//      dialog + flashes "Saved at HH:MM:SS" for 3s.
//   4. 422 surfaces inline as `Validation failed: <pydantic-error-strings>`.
//
// The "Send test" buttons on individual channels operate independently of the
// save flow — they always fire against the saved server config (see
// `<NtfyChannelCard>`).

import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { AlertKindsEditor } from "@/components/settings/AlertKindsEditor";
import {
  DiffPreviewDialog,
  type DiffRow,
} from "@/components/settings/DiffPreviewDialog";
import {
  InAppChannelCard,
  NtfyChannelCard,
} from "@/components/settings/ChannelCard";
import { QuietHoursEditor } from "@/components/settings/QuietHoursEditor";
import {
  useConfig,
  useUpdateConfig,
  type ConfigShape,
  type InAppChannelShape,
  type NotificationsConfigShape,
  type NtfyChannelShape,
  type QuietHoursShape,
  type AlertKind,
} from "@/hooks/useConfig";
import { useSavedFlash } from "@/hooks/useSavedFlash";
import { diffToRows, formatPutErrorMessage } from "@/lib/config-diff";
import { formatErrorMessage } from "@/lib/utils";

const EXPAND_KEYS = new Set(["notifications"]);

// --- Equality + diffing ----------------------------------------------------

function tagsEqual(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i += 1) {
    if (a[i] !== b[i]) return false;
  }
  return true;
}

function quietEqual(
  a: QuietHoursShape | null,
  b: QuietHoursShape | null,
): boolean {
  if (a === null && b === null) return true;
  if (a === null || b === null) return false;
  return a.start === b.start && a.end === b.end && a.tz === b.tz;
}

function inappEqual(a: InAppChannelShape, b: InAppChannelShape): boolean {
  return a.enabled === b.enabled;
}

function ntfyEqual(a: NtfyChannelShape, b: NtfyChannelShape): boolean {
  return (
    a.enabled === b.enabled &&
    a.server === b.server &&
    a.topic === b.topic &&
    a.priority === b.priority &&
    tagsEqual(a.tags, b.tags)
  );
}

function kindsEqual(a: AlertKind[], b: AlertKind[]): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i += 1) {
    if (a[i] !== b[i]) return false;
  }
  return true;
}

function notificationsEqual(
  a: NotificationsConfigShape,
  b: NotificationsConfigShape,
): boolean {
  return (
    kindsEqual(a.alert_kinds_enabled, b.alert_kinds_enabled) &&
    quietEqual(a.quiet_hours, b.quiet_hours) &&
    inappEqual(a.channels.inapp, b.channels.inapp) &&
    ntfyEqual(a.channels.ntfy, b.channels.ntfy)
  );
}

// Mount-key string — same shape pattern as `<SourceCard>` / Recommendation
// (server snapshot becomes the mount key; the form remounts after a
// successful PUT realigns server-side state).
function mountKey(n: NotificationsConfigShape): string {
  const inapp = n.channels.inapp.enabled ? "1" : "0";
  const ntfy = [
    n.channels.ntfy.enabled ? "1" : "0",
    n.channels.ntfy.server,
    n.channels.ntfy.topic,
    n.channels.ntfy.priority,
    n.channels.ntfy.tags.join(","),
  ].join(";");
  const quiet = n.quiet_hours
    ? `${n.quiet_hours.start}-${n.quiet_hours.end}@${n.quiet_hours.tz}`
    : "off";
  const kinds = [...n.alert_kinds_enabled].sort().join(",");
  return `${inapp}|${ntfy}|${quiet}|${kinds}`;
}

// --- Outer page ------------------------------------------------------------

export function SettingsNotifications() {
  const cfg = useConfig();

  if (cfg.isPending) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-32 w-full" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-24 w-full" />
      </div>
    );
  }

  if (cfg.error || !cfg.data) {
    return (
      <div className="rounded-md border border-destructive/40 bg-destructive/5 p-4 text-sm text-destructive">
        Failed to load config: {formatErrorMessage(cfg.error)}
      </div>
    );
  }

  return (
    <NotificationsForm
      key={mountKey(cfg.data.notifications)}
      config={cfg.data}
    />
  );
}

// --- Form ------------------------------------------------------------------

function NotificationsForm({ config }: { config: ConfigShape }) {
  const server = config.notifications;
  const [draft, setDraft] = useState<NotificationsConfigShape>(server);
  const [diffOpen, setDiffOpen] = useState(false);
  const [diffRows, setDiffRows] = useState<DiffRow[]>([]);
  const { savedAt, flash: flashSaved } = useSavedFlash();

  const update = useUpdateConfig();

  const dirty = !notificationsEqual(server, draft);

  // Client-side validation: when ntfy is enabled, topic must be non-empty.
  // (Mirrors the runtime guard in `book_alerter.notifications.ntfy.send`.)
  const validationError = useMemo<string | null>(() => {
    const n = draft.channels.ntfy;
    if (n.enabled && n.topic.trim() === "") {
      return "ntfy is enabled but no topic is set.";
    }
    return null;
  }, [draft]);

  const onPreview = () => {
    if (!dirty || validationError) return;
    update.reset();
    const candidate: ConfigShape = { ...config, notifications: draft };
    update.mutate(
      { config: candidate, dryRun: true },
      {
        onSuccess: (result) => {
          setDiffRows(diffToRows(result.diff, { expand: EXPAND_KEYS }));
          setDiffOpen(true);
        },
      },
    );
  };

  const onConfirmSave = () => {
    const candidate: ConfigShape = { ...config, notifications: draft };
    update.mutate(
      { config: candidate, dryRun: false },
      {
        onSuccess: (result) => {
          if (result.applied) {
            setDiffOpen(false);
            flashSaved();
          }
        },
      },
    );
  };

  const errorMessage = formatPutErrorMessage(update.error);

  // Sub-component setters — keep individual cards stateless.
  const setInApp = (next: InAppChannelShape) =>
    setDraft((d) => ({
      ...d,
      channels: { ...d.channels, inapp: next },
    }));
  const setNtfy = (next: NtfyChannelShape) =>
    setDraft((d) => ({
      ...d,
      channels: { ...d.channels, ntfy: next },
    }));
  const setKinds = (next: AlertKind[]) =>
    setDraft((d) => ({ ...d, alert_kinds_enabled: next }));
  const setQuiet = (next: QuietHoursShape | null) =>
    setDraft((d) => ({ ...d, quiet_hours: next }));

  return (
    <section className="space-y-4">
      <header>
        <h2 className="text-sm font-semibold">Notifications</h2>
        <p className="text-xs text-muted-foreground">
          Channels, alert kinds, and quiet-hours window. The in-app channel
          bypasses quiet hours; push channels respect them.
        </p>
      </header>

      <div className="space-y-3">
        <InAppChannelCard draft={draft.channels.inapp} onChange={setInApp} />
        <NtfyChannelCard
          draft={draft.channels.ntfy}
          server={server.channels.ntfy}
          onChange={setNtfy}
        />
      </div>

      <AlertKindsEditor
        value={draft.alert_kinds_enabled}
        onChange={setKinds}
      />

      <QuietHoursEditor value={draft.quiet_hours} onChange={setQuiet} />

      <div className="flex flex-wrap items-center gap-3">
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={() => setDraft(server)}
          disabled={!dirty || update.isPending}
        >
          Reset
        </Button>
        <Button
          type="button"
          size="sm"
          onClick={onPreview}
          disabled={!dirty || validationError !== null || update.isPending}
        >
          {update.isPending && !diffOpen ? "Computing diff…" : "Save…"}
        </Button>
        {validationError && (
          <span className="text-xs text-destructive">{validationError}</span>
        )}
        {savedAt && (
          <span className="text-xs text-emerald-600 dark:text-emerald-400">
            Saved at {savedAt}
          </span>
        )}
        {!diffOpen && errorMessage && (
          <span className="text-xs text-destructive">{errorMessage}</span>
        )}
      </div>

      <DiffPreviewDialog
        open={diffOpen}
        onOpenChange={(open) => {
          if (!update.isPending) setDiffOpen(open);
        }}
        title="Save notification changes"
        description="Review the changed fields before writing config.yaml."
        diff={diffRows}
        onConfirm={onConfirmSave}
        isPending={update.isPending}
        errorMessage={diffOpen ? errorMessage : null}
      />
    </section>
  );
}
