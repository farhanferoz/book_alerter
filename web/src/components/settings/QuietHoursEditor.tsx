// QuietHoursEditor — start/end/tz editor for `NotificationsConfig.quiet_hours`.
//
// The Pydantic model is `QuietHours | None`. We surface a single "enabled"
// toggle to flip between `None` and the defaults so users can disable quiet
// hours without losing their start/end values mid-edit (they survive in the
// component's `last` ref so reflipping the toggle restores them).
//
// Wrap-around (start > end) is the "spans midnight" case (e.g. 22:00 → 08:00);
// inline hint surfaces this. Timezone is shown as a free-text input — the
// backend stores it as a string and we default to the user's local TZ via
// `Intl.DateTimeFormat().resolvedOptions().timeZone` for new configs. No
// `<select>` over IANA zones; not worth the bundle weight for an MVP setting
// the user touches twice a year.

import { useEffect, useRef } from "react";

import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";

import type { QuietHoursShape } from "@/hooks/useConfig";

const DEFAULT_QUIET_HOURS: QuietHoursShape = {
  start: "22:00",
  end: "08:00",
  tz: "Europe/London",
};

function defaultQuietHours(): QuietHoursShape {
  // Resolve the user's local IANA zone for newly-enabled quiet hours so the
  // window doesn't silently use a default that doesn't match the user's wall
  // clock. Falls back to the spec default on browsers that return undefined.
  try {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    return { ...DEFAULT_QUIET_HOURS, tz: tz || DEFAULT_QUIET_HOURS.tz };
  } catch {
    return DEFAULT_QUIET_HOURS;
  }
}

function spansMidnight(start: string, end: string): boolean {
  // Time strings are zero-padded `HH:MM` from `<input type="time">`, so a
  // lexical comparison gives the same answer as numeric.
  return start > end;
}

export type QuietHoursEditorProps = {
  value: QuietHoursShape | null;
  onChange: (next: QuietHoursShape | null) => void;
};

export function QuietHoursEditor({ value, onChange }: QuietHoursEditorProps) {
  // Hold the last non-null value so toggling off → on restores the user's
  // settings rather than snapping back to the spec defaults. The ref is
  // written from a passive effect (not during render) — see react-hooks/refs.
  const lastRef = useRef<QuietHoursShape>(value ?? defaultQuietHours());
  useEffect(() => {
    if (value) lastRef.current = value;
  }, [value]);

  const enabled = value !== null;

  const toggleEnabled = (on: boolean) => {
    if (on) {
      onChange(lastRef.current);
    } else {
      onChange(null);
    }
  };

  const setField = (key: keyof QuietHoursShape, raw: string) => {
    if (!value) return;
    onChange({ ...value, [key]: raw });
  };

  return (
    <section className="space-y-3 rounded-md border border-border bg-card p-4">
      <header className="flex items-start justify-between gap-3">
        <div className="space-y-0.5">
          <h3 className="text-sm font-semibold">Quiet hours</h3>
          <p className="text-xs text-muted-foreground">
            Push channels stay silent during this window; in-app notifications
            still arrive. Alerts remain queued.
          </p>
        </div>
        <Switch
          checked={enabled}
          onCheckedChange={toggleEnabled}
          aria-label="Toggle quiet hours"
        />
      </header>

      {enabled && value && (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
          <div className="space-y-1.5">
            <Label htmlFor="quiet-start">Start</Label>
            <Input
              id="quiet-start"
              type="time"
              value={value.start}
              onChange={(e) => setField("start", e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="quiet-end">End</Label>
            <Input
              id="quiet-end"
              type="time"
              value={value.end}
              onChange={(e) => setField("end", e.target.value)}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="quiet-tz">Timezone</Label>
            <Input
              id="quiet-tz"
              type="text"
              value={value.tz}
              onChange={(e) => setField("tz", e.target.value)}
              placeholder="Europe/London"
              className="font-mono text-xs"
            />
            <p className="text-xs text-muted-foreground">
              IANA name (e.g. <code>Europe/London</code>).
            </p>
          </div>
        </div>
      )}

      {enabled && value && spansMidnight(value.start, value.end) && (
        <p className="text-xs text-muted-foreground">
          Window spans midnight: silent from {value.start} through {value.end}{" "}
          the next day.
        </p>
      )}
    </section>
  );
}
