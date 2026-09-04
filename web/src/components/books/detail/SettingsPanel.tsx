// Settings panel — target / threshold / alert kinds / mute / notes, plus a
// products-only "track used market" toggle.
//
// Pattern: a single `<form>` carrying local state for every editable field,
// "Save" PATCHes the whole bundle via `PATCH /api/books/{id}` or
// `PATCH /api/products/{id}`. We rebuild state from `item` whenever a fresh
// item lands (via `key={item.updated_at}` on the parent's render — see
// `BookDetail.tsx`/`ProductDetail.tsx`). That keeps the form straightforward
// without effect-driven sync.
//
// Every field here (target price, percentile threshold/window, alert-kind
// disable, mute-until, notes) is already on both `BookPatch` and
// `ProductPatch` — `track_used` is the one products-only field, rendered
// only when `item.kind === "product"` (T5.4).
//
// Money: the user enters whole pounds; we convert to/from minor units at the
// form edge via `poundsToMinor` / `minorToPoundsInput` (see `lib/format.ts`).
//
// Mute: HTML <input type="datetime-local"> uses the user's local zone, with
// no timezone suffix in its `value`. We treat the entered value as local
// time and convert to UTC ISO when sending to the backend.

import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";

import { ApiError, apiPatch } from "@/api/client";
import type { components } from "@/api/schema";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { Textarea } from "@/components/ui/textarea";
import { minorToPoundsInput, poundsToMinor } from "@/lib/format";
import {
  itemApiBase,
  itemDetailQueryKey,
  itemListQueryKey,
  type Item,
} from "@/lib/item";

type BookPatch = components["schemas"]["BookPatch"];
type ItemPatch = BookPatch & { track_used?: boolean };
type AlertKind = "target_hit" | "percentile_cross" | "new_low";

const ALERT_KINDS: ReadonlyArray<{ kind: AlertKind; label: string }> = [
  { kind: "target_hit", label: "Target hit" },
  { kind: "percentile_cross", label: "Percentile cross (→ BUY)" },
  { kind: "new_low", label: "New all-time low" },
];

// Convert ISO string ↔ `<input type="datetime-local">` value (no tz suffix).
// Browser interprets the local string as local time on submit; we re-attach
// the local-tz offset and emit a UTC ISO when PATCHing.
function isoToLocalInput(iso: string | null): string {
  if (!iso) return "";
  const d = new Date(iso);
  const pad = (n: number) => String(n).padStart(2, "0");
  const yyyy = d.getFullYear();
  const mm = pad(d.getMonth() + 1);
  const dd = pad(d.getDate());
  const hh = pad(d.getHours());
  const mi = pad(d.getMinutes());
  return `${yyyy}-${mm}-${dd}T${hh}:${mi}`;
}

function localInputToIso(value: string): string | null {
  if (!value) return null;
  // `new Date("2026-05-14T12:30")` is parsed in the local TZ.
  const d = new Date(value);
  if (Number.isNaN(d.getTime())) return null;
  return d.toISOString();
}

export function SettingsPanel({ item }: { item: Item }) {
  const qc = useQueryClient();
  const [targetPounds, setTargetPounds] = useState(
    minorToPoundsInput(item.target_price_minor),
  );
  const [threshold, setThreshold] = useState<string>(
    item.percentile_threshold == null ? "" : String(item.percentile_threshold),
  );
  const [windowDays, setWindowDays] = useState<string>(
    item.percentile_window_days == null ? "" : String(item.percentile_window_days),
  );
  const initialDisabled = new Set<AlertKind>(
    (item.alert_kinds_disabled ?? []).filter(
      (k): k is AlertKind =>
        k === "target_hit" || k === "percentile_cross" || k === "new_low",
    ),
  );
  const [disabledKinds, setDisabledKinds] =
    useState<Set<AlertKind>>(initialDisabled);
  const [mute, setMute] = useState<string>(isoToLocalInput(item.muted_until));
  const [notes, setNotes] = useState<string>(item.notes ?? "");
  const [trackUsed, setTrackUsed] = useState<boolean>(
    item.kind === "product" ? item.track_used : false,
  );
  const [error, setError] = useState<string | null>(null);

  const save = useMutation<Item, ApiError, ItemPatch>({
    mutationFn: async (body) => {
      const path = `${itemApiBase(item.kind)}/${item.id}` as
        | "/api/books/{book_id}"
        | "/api/products/{product_id}";
      return (await apiPatch(path, body)) as Item;
    },
    onSuccess: () => {
      setError(null);
      void qc.invalidateQueries({ queryKey: [itemDetailQueryKey(item.kind), item.id] });
      void qc.invalidateQueries({ queryKey: [itemListQueryKey(item.kind)] });
    },
    onError: (err) => {
      setError(`Save failed (${err.status}) — ${err.message}`);
    },
  });

  const toggleKind = (kind: AlertKind) => {
    setDisabledKinds((prev) => {
      const next = new Set(prev);
      if (next.has(kind)) next.delete(kind);
      else next.add(kind);
      return next;
    });
  };

  const onSubmit = (e: React.FormEvent<HTMLFormElement>) => {
    e.preventDefault();

    const targetMinor = poundsToMinor(targetPounds);
    if (targetPounds.trim() !== "" && targetMinor === null) {
      setError("Target must be a non-negative number (pounds).");
      return;
    }

    let pct: number | null = null;
    const trimmedPct = threshold.trim();
    if (trimmedPct !== "") {
      const parsed = Number(trimmedPct);
      if (!Number.isFinite(parsed) || parsed < 0 || parsed > 100) {
        setError("Percentile threshold must be 0–100.");
        return;
      }
      pct = parsed;
    }

    let win: number | null = null;
    const trimmedWin = windowDays.trim();
    if (trimmedWin !== "") {
      const parsed = Number(trimmedWin);
      if (!Number.isFinite(parsed) || parsed < 1) {
        setError("Window must be ≥ 1 day.");
        return;
      }
      win = Math.round(parsed);
    }

    const body: ItemPatch = {
      target_price_minor: targetMinor,
      percentile_threshold: pct,
      percentile_window_days: win,
      alert_kinds_disabled: [...disabledKinds],
      muted_until: localInputToIso(mute),
      notes: notes.trim() === "" ? null : notes.trim(),
    };
    if (item.kind === "product") {
      body.track_used = trackUsed;
    }
    save.mutate(body);
  };

  const noun = item.kind === "product" ? "product" : "book";

  return (
    <form
      onSubmit={onSubmit}
      className="space-y-4 rounded-md border border-border bg-card p-4"
    >
      <h2 className="text-xs font-medium uppercase text-muted-foreground">
        Settings
      </h2>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <div className="space-y-1.5">
          <Label htmlFor="target-price">Target price (GBP)</Label>
          <Input
            id="target-price"
            type="number"
            inputMode="decimal"
            step="0.01"
            min="0"
            value={targetPounds}
            onChange={(e) => setTargetPounds(e.target.value)}
            placeholder="e.g. 6.99"
          />
          <p className="text-xs text-muted-foreground">
            Empty = no target.
          </p>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="threshold">Percentile threshold (0–100)</Label>
          <Input
            id="threshold"
            type="number"
            inputMode="numeric"
            step="1"
            min="0"
            max="100"
            value={threshold}
            onChange={(e) => setThreshold(e.target.value)}
            placeholder="e.g. 10"
          />
          <p className="text-xs text-muted-foreground">
            Override the global buy-percentile. Empty = use default.
          </p>
        </div>

        <div className="space-y-1.5">
          <Label htmlFor="window-days">Percentile window (days)</Label>
          <Input
            id="window-days"
            type="number"
            inputMode="numeric"
            step="1"
            min="1"
            value={windowDays}
            onChange={(e) => setWindowDays(e.target.value)}
            placeholder="e.g. 90"
          />
          <p className="text-xs text-muted-foreground">
            Override the global percentile window. Empty = use default.
          </p>
        </div>

        {item.kind === "product" && (
          <div className="space-y-1.5">
            <Label htmlFor="track-used">Track used market</Label>
            <div className="flex items-center gap-2">
              <Switch
                id="track-used"
                checked={trackUsed}
                onCheckedChange={setTrackUsed}
              />
              <span className="text-xs text-muted-foreground">
                {trackUsed ? "New + used" : "New only"}
              </span>
            </div>
            <p className="text-xs text-muted-foreground">
              When on, also tracks used grades from the Amazon offer-listing
              page. Default off — most non-book products have no meaningful
              used market.
            </p>
          </div>
        )}
      </div>

      <div className="space-y-1.5">
        <Label>Alert kinds</Label>
        <div className="flex flex-wrap gap-2">
          {ALERT_KINDS.map(({ kind, label }) => {
            const enabled = !disabledKinds.has(kind);
            return (
              <button
                key={kind}
                type="button"
                onClick={() => toggleKind(kind)}
                aria-pressed={enabled}
                className={`rounded-full border px-2.5 py-0.5 text-xs ${
                  enabled
                    ? "border-primary/40 bg-primary/10 text-primary"
                    : "border-border bg-muted/40 text-muted-foreground"
                }`}
              >
                {label}
              </button>
            );
          })}
        </div>
        <p className="text-xs text-muted-foreground">
          Toggle off the kinds you don&apos;t want for this {noun}.
        </p>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="mute-until">Mute until</Label>
        <Input
          id="mute-until"
          type="datetime-local"
          value={mute}
          onChange={(e) => setMute(e.target.value)}
        />
        <p className="text-xs text-muted-foreground">
          Local time. Empty = not muted.
        </p>
      </div>

      <div className="space-y-1.5">
        <Label htmlFor="notes">Notes</Label>
        <Textarea
          id="notes"
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          rows={3}
        />
      </div>

      {error && <p className="text-xs text-destructive">{error}</p>}
      {save.isSuccess && !error && (
        <p className="text-xs text-green-600 dark:text-green-400">Saved.</p>
      )}

      <div className="flex justify-end">
        <Button type="submit" disabled={save.isPending}>
          {save.isPending ? "Saving…" : "Save settings"}
        </Button>
      </div>
    </form>
  );
}
