// AlertKindsEditor — three toggles wired to `NotificationsConfig.alert_kinds_enabled`.
//
// The backend stores enabled kinds as a *list* (subset of
// ["target_hit","percentile_cross","new_low"]) — not a dict. We expose a
// boolean-per-kind UX while keeping the wire shape as a sorted list so the
// diff preview stays stable across reloads. Deselecting every kind is allowed
// (backend does not reject the empty list — see Phase 11.4 RESUME note) but
// we surface an inline warning since "no alerts will fire" is rarely intent.

import { Switch } from "@/components/ui/switch";

import {
  ALERT_KINDS,
  type AlertKind,
} from "@/hooks/useConfig";

const KIND_META: Record<AlertKind, { label: string; hint: string }> = {
  target_hit: {
    label: "Target hit",
    hint: "Fires when current best price reaches the book's target.",
  },
  percentile_cross: {
    label: "Percentile cross",
    hint: "Fires when the current price crosses the buy / watch threshold.",
  },
  new_low: {
    label: "New low",
    hint: "Fires when the current best price is the lowest ever observed.",
  },
};

export type AlertKindsEditorProps = {
  value: AlertKind[];
  onChange: (next: AlertKind[]) => void;
};

export function AlertKindsEditor({ value, onChange }: AlertKindsEditorProps) {
  const enabled = new Set(value);

  const toggle = (kind: AlertKind, on: boolean) => {
    const next = new Set(enabled);
    if (on) next.add(kind);
    else next.delete(kind);
    // Preserve canonical order so the diff preview stays deterministic.
    onChange(ALERT_KINDS.filter((k) => next.has(k)));
  };

  return (
    <section className="space-y-3 rounded-md border border-border bg-card p-4">
      <header>
        <h3 className="text-sm font-semibold">Alert kinds</h3>
        <p className="text-xs text-muted-foreground">
          Choose which alert kinds dispatch through enabled channels.
        </p>
      </header>

      <ul className="space-y-2">
        {ALERT_KINDS.map((kind) => {
          const meta = KIND_META[kind];
          const id = `alert-kind-${kind}`;
          const on = enabled.has(kind);
          return (
            <li
              key={kind}
              className="flex items-start justify-between gap-3 rounded-md border border-border/60 bg-background/40 p-3"
            >
              <div className="space-y-0.5">
                <label
                  htmlFor={id}
                  className="text-sm font-medium leading-none"
                >
                  {meta.label}
                </label>
                <p className="text-xs text-muted-foreground">{meta.hint}</p>
              </div>
              <Switch
                id={id}
                checked={on}
                onCheckedChange={(checked) => toggle(kind, checked)}
                aria-label={`Toggle ${meta.label} alerts`}
              />
            </li>
          );
        })}
      </ul>

      {value.length === 0 && (
        <p className="text-xs text-amber-600 dark:text-amber-400">
          No alert kinds enabled — no alerts will fire.
        </p>
      )}
    </section>
  );
}
