// ChannelCard — per-channel UI for `NotificationsConfig.channels`.
//
// MVP supports two channels:
//   - `inapp` (InAppChannelConfig: enabled-only, always-on transport, bypasses
//     quiet hours). No "Send test" button — the synthetic test alert built by
//     `POST /api/notifications/inapp/test` *does* dispatch through the in-app
//     notifier, but per Task 7.7 it doesn't persist a `NotificationDelivery`
//     row (no real Alert.id), so the test would silently no-op from a user's
//     perspective. We still expose the endpoint for parity in case future
//     channels add observable side-effects; for inapp specifically we render
//     the button for symmetry but label it accordingly.
//   - `ntfy` (NtfyChannelConfig: enabled, server, topic, priority, tags[]).
//     "Send test" → `POST /api/notifications/ntfy/test`; failures (network,
//     401, etc.) surface inline via the endpoint's `error_message` field.
//
// Telegram + Pushover slots are intentionally absent: the Pydantic model in
// `src/book_alerter/config.py` does not yet carry them (RESUME: "deferred —
// slots reserved" refers to the conceptual roadmap, not present types). When
// the model adds them, render a new card here.
//
// The "Send test" button always fires against the *currently-saved* server
// config — not the local draft. If the draft differs we surface a hint so
// the user knows to save first if they want to test new settings.

import { useState } from "react";

import { ApiError, apiPost } from "@/api/client";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import type { components } from "@/api/schema";
import { formatErrorMessage } from "@/lib/utils";

import type {
  InAppChannelShape,
  NtfyChannelShape,
} from "@/hooks/useConfig";

const TEST_FLASH_MS = 3000;

type TestResult = components["schemas"]["NotificationTestResult"];

type TestFlash =
  | { kind: "success"; message: string }
  | { kind: "error"; message: string };

// Shared "Send test" button. Renders inline status with auto-clear after
// TEST_FLASH_MS. The endpoint contract returns 200 with `status: "error"`
// (notifier-level failure surfaced verbatim) or 404 / 5xx for plumbing
// failures — we treat both uniformly via the displayed error message.
function TestButton({
  channel,
  disabled,
  draftDiffers,
}: {
  channel: string;
  disabled?: boolean;
  draftDiffers: boolean;
}) {
  const [flash, setFlash] = useState<TestFlash | null>(null);
  const [pending, setPending] = useState(false);

  const send = async () => {
    setPending(true);
    setFlash(null);
    try {
      const path =
        `/api/notifications/${encodeURIComponent(channel)}/test` as
        "/api/notifications/{channel}/test";
      const body = (await apiPost(path)) as TestResult;
      if (body.status === "sent") {
        setFlash({ kind: "success", message: "Test sent" });
      } else {
        setFlash({
          kind: "error",
          message: body.error_message ?? "Notifier returned error",
        });
      }
    } catch (err) {
      const message =
        err instanceof ApiError && err.status === 404
          ? "Channel not configured on the server"
          : `Test failed (${formatErrorMessage(err)})`;
      setFlash({ kind: "error", message });
    } finally {
      setPending(false);
      // Auto-clear the status pill regardless of outcome.
      window.setTimeout(() => setFlash(null), TEST_FLASH_MS);
    }
  };

  return (
    <div className="flex flex-wrap items-center gap-3">
      <Button
        type="button"
        size="sm"
        variant="outline"
        onClick={() => void send()}
        disabled={pending || disabled}
      >
        {pending ? "Sending…" : "Send test"}
      </Button>
      {draftDiffers && (
        <span className="text-xs text-muted-foreground">
          Test uses saved config, not unsaved draft.
        </span>
      )}
      {flash && (
        <span
          className={`text-xs ${
            flash.kind === "success"
              ? "text-emerald-600 dark:text-emerald-400"
              : "text-destructive"
          }`}
        >
          {flash.message}
        </span>
      )}
    </div>
  );
}

// --- InApp channel ---------------------------------------------------------

export type InAppChannelCardProps = {
  draft: InAppChannelShape;
  onChange: (next: InAppChannelShape) => void;
};

export function InAppChannelCard({ draft, onChange }: InAppChannelCardProps) {
  return (
    <section className="space-y-3 rounded-md border border-border bg-card p-4">
      <header className="flex items-start justify-between gap-3">
        <div className="space-y-0.5">
          <h3 className="text-sm font-semibold">In-app</h3>
          <p className="text-xs text-muted-foreground">
            Always-on transport — bypasses quiet hours. Alerts land in the feed
            without any network dispatch.
          </p>
        </div>
        <Switch
          checked={draft.enabled}
          onCheckedChange={(checked) => onChange({ enabled: checked })}
          aria-label="Toggle in-app channel"
        />
      </header>
    </section>
  );
}

// --- ntfy channel ----------------------------------------------------------

export type NtfyChannelCardProps = {
  draft: NtfyChannelShape;
  server: NtfyChannelShape;
  onChange: (next: NtfyChannelShape) => void;
};

export function NtfyChannelCard({
  draft,
  server,
  onChange,
}: NtfyChannelCardProps) {
  // Local validation: when enabled, topic is required. Mirrors the existing
  // runtime guard in `book_alerter.notifications.ntfy.send`.
  const topicMissing = draft.enabled && draft.topic.trim() === "";

  const draftDiffers =
    server.enabled !== draft.enabled ||
    server.server !== draft.server ||
    server.topic !== draft.topic ||
    server.priority !== draft.priority ||
    server.tags.join(",") !== draft.tags.join(",");

  // Tags are stored as `list[str]`; surface as a comma-separated string for
  // editing. Empty tokens are dropped on the way back out so the wire payload
  // stays clean.
  const tagsValue = draft.tags.join(", ");
  const setTags = (raw: string) => {
    const tags = raw
      .split(",")
      .map((t) => t.trim())
      .filter((t) => t.length > 0);
    onChange({ ...draft, tags });
  };

  return (
    <section className="space-y-3 rounded-md border border-border bg-card p-4">
      <header className="flex items-start justify-between gap-3">
        <div className="space-y-0.5">
          <h3 className="text-sm font-semibold">ntfy</h3>
          <p className="text-xs text-muted-foreground">
            Self-hosted or ntfy.sh push channel. POSTs alerts to{" "}
            <code className="font-mono">{`<server>/<topic>`}</code>.
          </p>
        </div>
        <Switch
          checked={draft.enabled}
          onCheckedChange={(checked) => onChange({ ...draft, enabled: checked })}
          aria-label="Toggle ntfy channel"
        />
      </header>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="ntfy-server">Server URL</Label>
          <Input
            id="ntfy-server"
            type="url"
            value={draft.server}
            onChange={(e) => onChange({ ...draft, server: e.target.value })}
            placeholder="https://ntfy.sh"
            className="font-mono text-xs"
          />
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="ntfy-topic">Topic</Label>
          <Input
            id="ntfy-topic"
            type="text"
            value={draft.topic}
            onChange={(e) => onChange({ ...draft, topic: e.target.value })}
            placeholder="book-alerter-alerts"
            aria-invalid={topicMissing ? true : undefined}
            aria-describedby={topicMissing ? "ntfy-topic-err" : undefined}
          />
          {topicMissing && (
            <p id="ntfy-topic-err" className="text-xs text-destructive">
              Topic is required when ntfy is enabled.
            </p>
          )}
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="ntfy-priority">Priority</Label>
          <Input
            id="ntfy-priority"
            type="text"
            value={draft.priority}
            onChange={(e) => onChange({ ...draft, priority: e.target.value })}
            placeholder="default"
          />
          <p className="text-xs text-muted-foreground">
            ntfy priority header (e.g. <code>default</code>, <code>high</code>).
          </p>
        </div>
        <div className="space-y-1.5">
          <Label htmlFor="ntfy-tags">Tags</Label>
          <Input
            id="ntfy-tags"
            type="text"
            value={tagsValue}
            onChange={(e) => setTags(e.target.value)}
            placeholder="book, money"
          />
          <p className="text-xs text-muted-foreground">
            Comma-separated; rendered as ntfy emoji tags.
          </p>
        </div>
      </div>

      <TestButton
        channel="ntfy"
        disabled={!server.enabled || server.topic.trim() === ""}
        draftDiffers={draftDiffers}
      />
    </section>
  );
}
