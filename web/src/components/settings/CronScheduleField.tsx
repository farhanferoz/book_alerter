// Visual schedule builder for the source-card cron field.
//
// When `value` parses to one of the four supported shapes (see lib/cron),
// renders unit/interval/offset pickers. When it doesn't (rare — exotic cron),
// falls back to a raw text input with a "Use visual builder" reset button so
// the user isn't trapped, and existing exotic crons aren't silently lost.

import { useMemo } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  DAY_NAMES,
  HOUR_INTERVALS,
  MINUTE_INTERVALS,
  type Schedule,
  describeSchedule,
  parseCron,
  serializeCron,
} from "@/lib/cron";

const DEFAULT_VISUAL_CRON = "0 */6 * * *";

type Mode = Schedule["mode"];

const MODE_LABELS: Record<Mode, string> = {
  minutes: "Every N minutes",
  hours: "Every N hours",
  daily: "Daily",
  weekly: "Weekly",
};

function defaultForMode(mode: Mode): Schedule {
  switch (mode) {
    case "minutes":
      return { mode: "minutes", every: 15 };
    case "hours":
      return { mode: "hours", every: 6, minute: 0 };
    case "daily":
      return { mode: "daily", hour: 9, minute: 0 };
    case "weekly":
      return { mode: "weekly", dayOfWeek: 1, hour: 9, minute: 0 };
  }
}

const selectClass =
  "h-9 rounded-md border border-input bg-transparent px-2 text-sm shadow-sm focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring";

export type CronScheduleFieldProps = {
  id: string;
  value: string;
  onChange: (cron: string) => void;
};

export function CronScheduleField({ id, value, onChange }: CronScheduleFieldProps) {
  const parsed = useMemo(() => parseCron(value), [value]);

  if (parsed === null) {
    return (
      <div className="space-y-1.5">
        <Label htmlFor={id}>Schedule (cron)</Label>
        <div className="flex items-center gap-2">
          <Input
            id={id}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            placeholder={DEFAULT_VISUAL_CRON}
            className="font-mono text-xs"
          />
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => onChange(DEFAULT_VISUAL_CRON)}
          >
            Use visual builder
          </Button>
        </div>
        <p className="text-xs text-muted-foreground">
          Cron doesn't match a builder preset — editing as raw text.
        </p>
      </div>
    );
  }

  const setMode = (mode: Mode) => onChange(serializeCron(defaultForMode(mode)));

  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>Schedule</Label>
      <div className="flex flex-wrap items-center gap-2">
        <select
          id={id}
          className={selectClass}
          value={parsed.mode}
          onChange={(e) => setMode(e.target.value as Mode)}
          aria-label="Schedule frequency"
        >
          {(Object.keys(MODE_LABELS) as Mode[]).map((m) => (
            <option key={m} value={m}>
              {MODE_LABELS[m]}
            </option>
          ))}
        </select>

        {parsed.mode === "minutes" && (
          <select
            className={selectClass}
            value={parsed.every}
            onChange={(e) =>
              onChange(
                serializeCron({ mode: "minutes", every: Number(e.target.value) }),
              )
            }
            aria-label="Minute interval"
          >
            {MINUTE_INTERVALS.map((n) => (
              <option key={n} value={n}>
                {n} minutes
              </option>
            ))}
          </select>
        )}

        {parsed.mode === "hours" && (
          <>
            <select
              className={selectClass}
              value={parsed.every}
              onChange={(e) =>
                onChange(
                  serializeCron({
                    ...parsed,
                    every: Number(e.target.value),
                  }),
                )
              }
              aria-label="Hour interval"
            >
              {HOUR_INTERVALS.map((n) => (
                <option key={n} value={n}>
                  {n === 1 ? "1 hour" : `${n} hours`}
                </option>
              ))}
            </select>
            <span className="text-xs text-muted-foreground">at minute</span>
            <select
              className={selectClass}
              value={parsed.minute}
              onChange={(e) =>
                onChange(
                  serializeCron({
                    ...parsed,
                    minute: Number(e.target.value),
                  }),
                )
              }
              aria-label="Starting minute"
            >
              {Array.from({ length: 60 }, (_, i) => i).map((n) => (
                <option key={n} value={n}>
                  {n}
                </option>
              ))}
            </select>
          </>
        )}

        {parsed.mode === "daily" && (
          <TimePickers
            hour={parsed.hour}
            minute={parsed.minute}
            onChange={(hour, minute) =>
              onChange(serializeCron({ mode: "daily", hour, minute }))
            }
          />
        )}

        {parsed.mode === "weekly" && (
          <>
            <select
              className={selectClass}
              value={parsed.dayOfWeek}
              onChange={(e) =>
                onChange(
                  serializeCron({
                    ...parsed,
                    dayOfWeek: Number(e.target.value),
                  }),
                )
              }
              aria-label="Day of week"
            >
              {DAY_NAMES.map((name, i) => (
                <option key={i} value={i}>
                  {name}
                </option>
              ))}
            </select>
            <TimePickers
              hour={parsed.hour}
              minute={parsed.minute}
              onChange={(hour, minute) =>
                onChange(
                  serializeCron({
                    ...parsed,
                    hour,
                    minute,
                  }),
                )
              }
            />
          </>
        )}
      </div>
      <p className="text-xs text-muted-foreground">
        {describeSchedule(parsed)} ·{" "}
        <code className="font-mono">{value}</code>
      </p>
    </div>
  );
}

function TimePickers({
  hour,
  minute,
  onChange,
}: {
  hour: number;
  minute: number;
  onChange: (hour: number, minute: number) => void;
}) {
  return (
    <>
      <span className="text-xs text-muted-foreground">at</span>
      <select
        className={selectClass}
        value={hour}
        onChange={(e) => onChange(Number(e.target.value), minute)}
        aria-label="Hour"
      >
        {Array.from({ length: 24 }, (_, i) => i).map((n) => (
          <option key={n} value={n}>
            {n.toString().padStart(2, "0")}
          </option>
        ))}
      </select>
      <span className="text-xs text-muted-foreground">:</span>
      <select
        className={selectClass}
        value={minute}
        onChange={(e) => onChange(hour, Number(e.target.value))}
        aria-label="Minute"
      >
        {Array.from({ length: 60 }, (_, i) => i).map((n) => (
          <option key={n} value={n}>
            {n.toString().padStart(2, "0")}
          </option>
        ))}
      </select>
    </>
  );
}
