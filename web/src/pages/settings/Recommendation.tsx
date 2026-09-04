// Settings → Recommendation tab (Phase 11.3).
//
// Five scalar fields under `RecommendationConfig`:
//   - min_observations_for_signal (int, ≥1)
//   - buy_percentile               (int 0–100)
//   - watch_percentile             (int 0–100, > buy_percentile)
//   - target_tolerance_pct         (int 0–100)
//   - alert_dedup_window_hours     (int, ≥0)
//
// Plus one boolean (T2.3): `amazon_prime` — "I have Amazon Prime", rendered
// as a labelled `<Switch>` below the numeric grid (no validation; every
// value is valid).
//
// Save flow: build candidate `{...serverConfig, recommendation: draft}`, call
// `PUT /api/config?dry_run=true` to fetch the server-validated diff, show
// `<DiffPreviewDialog>`, then on confirm re-call with `dry_run=false`. The
// backend's diff is top-level only (Task 7.5) so the recommendation block
// shows up under `changed.recommendation = {before, after}` — we render that
// as one field-per-row entry inside the dialog by walking the sub-dict
// ourselves (the generic dialog stays format-agnostic).
//
// Field grouping uses `<Input type="number">` for consistency with the 11.2
// Sources tab (no `<Slider>` primitive — bundle weight not justified; spec
// allowed either). Client-side validation enforces ranges and the
// `watch > buy` ordering; the backend re-validates on submit.

import { useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Skeleton } from "@/components/ui/skeleton";
import { Switch } from "@/components/ui/switch";
import {
  DiffPreviewDialog,
  type DiffRow,
} from "@/components/settings/DiffPreviewDialog";
import {
  useConfig,
  useUpdateConfig,
  type ConfigShape,
  type RecommendationConfigShape,
} from "@/hooks/useConfig";
import { useSavedFlash } from "@/hooks/useSavedFlash";
import { diffToRows, formatPutErrorMessage } from "@/lib/config-diff";
import { formatErrorMessage } from "@/lib/utils";

const EXPAND_KEYS = new Set(["recommendation"]);

// Excludes `amazon_prime` — that field is a `<Switch>` wired directly to
// `setDraft`, not one of the `<NumberField>` rows this key drives.
type FieldKey = Exclude<keyof RecommendationConfigShape, "amazon_prime">;

const FIELD_LABELS: Record<FieldKey, { label: string; unit?: string; hint?: string }> = {
  min_observations_for_signal: {
    label: "Minimum observations before signal computes",
    hint: "Below this, books show INSUFFICIENT_DATA.",
  },
  buy_percentile: {
    label: "Buy percentile",
    unit: "%",
    hint: "Price at or below this percentile triggers BUY.",
  },
  watch_percentile: {
    label: "Watch percentile",
    unit: "%",
    hint: "Price at or below this percentile triggers WATCH.",
  },
  target_tolerance_pct: {
    label: "Target tolerance",
    unit: "% above target",
    hint: "How close to the user's target counts as TARGET_HIT.",
  },
  alert_dedup_window_hours: {
    label: "Dedup window",
    unit: "hours",
    hint: "Suppress duplicate alerts of the same kind within this window.",
  },
  percentile_window_days: {
    label: "Percentile window",
    unit: "days",
    hint: "Trailing window over which the BUY/WATCH percentile is computed.",
  },
};

function draftsEqual(
  a: RecommendationConfigShape,
  b: RecommendationConfigShape,
): boolean {
  return (
    a.min_observations_for_signal === b.min_observations_for_signal &&
    a.buy_percentile === b.buy_percentile &&
    a.watch_percentile === b.watch_percentile &&
    a.target_tolerance_pct === b.target_tolerance_pct &&
    a.alert_dedup_window_hours === b.alert_dedup_window_hours &&
    a.percentile_window_days === b.percentile_window_days &&
    a.amazon_prime === b.amazon_prime
  );
}

type ValidationErrors = Partial<Record<FieldKey, string>>;

function validateDraft(d: RecommendationConfigShape): ValidationErrors {
  const errors: ValidationErrors = {};
  if (!Number.isFinite(d.min_observations_for_signal) || d.min_observations_for_signal < 1) {
    errors.min_observations_for_signal = "Must be ≥ 1.";
  }
  if (
    !Number.isFinite(d.buy_percentile) ||
    d.buy_percentile < 0 ||
    d.buy_percentile > 100
  ) {
    errors.buy_percentile = "Must be between 0 and 100.";
  }
  if (
    !Number.isFinite(d.watch_percentile) ||
    d.watch_percentile < 0 ||
    d.watch_percentile > 100
  ) {
    errors.watch_percentile = "Must be between 0 and 100.";
  }
  if (
    !errors.buy_percentile &&
    !errors.watch_percentile &&
    d.watch_percentile <= d.buy_percentile
  ) {
    errors.watch_percentile = "Must be greater than buy percentile.";
  }
  if (
    !Number.isFinite(d.target_tolerance_pct) ||
    d.target_tolerance_pct < 0 ||
    d.target_tolerance_pct > 100
  ) {
    errors.target_tolerance_pct = "Must be between 0 and 100.";
  }
  if (
    !Number.isFinite(d.alert_dedup_window_hours) ||
    d.alert_dedup_window_hours < 0
  ) {
    errors.alert_dedup_window_hours = "Must be ≥ 0.";
  }
  if (
    !Number.isFinite(d.percentile_window_days) ||
    d.percentile_window_days < 1
  ) {
    errors.percentile_window_days = "Must be ≥ 1.";
  }
  return errors;
}

export function SettingsRecommendation() {
  const cfg = useConfig();

  if (cfg.isPending) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-6 w-64" />
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {Array.from({ length: 5 }).map((_, i) => (
            <Skeleton key={i} className="h-16 w-full" />
          ))}
        </div>
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

  // Mount key forces re-init of local state after a successful PUT swaps the
  // server snapshot (same pattern as `<SourceCard>` in 11.2). Build a stable
  // string from the recommendation block; if anything changes server-side,
  // the form remounts with the fresh draft.
  const r = cfg.data.recommendation;
  const mountKey = [
    r.min_observations_for_signal,
    r.buy_percentile,
    r.watch_percentile,
    r.target_tolerance_pct,
    r.alert_dedup_window_hours,
    r.percentile_window_days,
    r.amazon_prime,
  ].join("|");

  return <RecommendationForm key={mountKey} config={cfg.data} />;
}

function RecommendationForm({ config }: { config: ConfigShape }) {
  const server = config.recommendation;
  const [draft, setDraft] = useState<RecommendationConfigShape>(server);
  const [diffOpen, setDiffOpen] = useState(false);
  const [diffRows, setDiffRows] = useState<DiffRow[]>([]);
  const { savedAt, flash: flashSaved } = useSavedFlash();

  const update = useUpdateConfig();

  const validation = useMemo(() => validateDraft(draft), [draft]);
  const isValid = Object.keys(validation).length === 0;
  const dirty = !draftsEqual(server, draft);

  const onPreview = () => {
    if (!dirty || !isValid) return;
    update.reset();
    const candidate: ConfigShape = { ...config, recommendation: draft };
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
    const candidate: ConfigShape = { ...config, recommendation: draft };
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

  const setField = (key: FieldKey, raw: string) => {
    // Treat empty string as 0; let the validator catch out-of-range. Number()
    // returns NaN for non-numeric input which validateDraft also catches.
    const parsed = raw === "" ? 0 : Number(raw);
    setDraft((d) => ({ ...d, [key]: parsed }));
  };

  const errorMessage = formatPutErrorMessage(update.error);

  return (
    <section className="space-y-4">
      <header>
        <h2 className="text-sm font-semibold">Recommendation thresholds</h2>
        <p className="text-xs text-muted-foreground">
          Governs how BUY / WATCH / WAIT / TARGET_HIT signals are computed and
          how aggressively alerts deduplicate.
        </p>
      </header>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <NumberField
          fieldKey="min_observations_for_signal"
          value={draft.min_observations_for_signal}
          onChange={setField}
          error={validation.min_observations_for_signal}
          min={1}
        />
        <NumberField
          fieldKey="buy_percentile"
          value={draft.buy_percentile}
          onChange={setField}
          error={validation.buy_percentile}
          min={0}
          max={100}
        />
        <NumberField
          fieldKey="watch_percentile"
          value={draft.watch_percentile}
          onChange={setField}
          error={validation.watch_percentile}
          min={0}
          max={100}
        />
        <NumberField
          fieldKey="target_tolerance_pct"
          value={draft.target_tolerance_pct}
          onChange={setField}
          error={validation.target_tolerance_pct}
          min={0}
          max={100}
        />
        <NumberField
          fieldKey="alert_dedup_window_hours"
          value={draft.alert_dedup_window_hours}
          onChange={setField}
          error={validation.alert_dedup_window_hours}
          min={0}
        />
        <NumberField
          fieldKey="percentile_window_days"
          value={draft.percentile_window_days}
          onChange={setField}
          error={validation.percentile_window_days}
          min={1}
        />
      </div>

      <div className="flex items-start justify-between gap-3 rounded-md border border-border/60 bg-background/40 p-3">
        <div className="space-y-0.5">
          <Label htmlFor="rec-amazon-prime" className="text-sm font-medium leading-none">
            I have Amazon Prime (treat Amazon-fulfilled delivery as free)
          </Label>
          <p className="text-xs text-muted-foreground">
            Applies only to offers Amazon itself fulfils — third-party
            sellers on Amazon are unaffected. Changes how offers rank and
            when alerts fire; scraped shipping figures are never rewritten.
          </p>
        </div>
        <Switch
          id="rec-amazon-prime"
          checked={draft.amazon_prime}
          onCheckedChange={(checked) =>
            setDraft((d) => ({ ...d, amazon_prime: checked }))
          }
          aria-label="Toggle Amazon Prime shipping assumption"
        />
      </div>

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
          disabled={!dirty || !isValid || update.isPending}
        >
          {update.isPending && !diffOpen ? "Computing diff…" : "Save…"}
        </Button>
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
        title="Save recommendation changes"
        description="Review the changed fields before writing config.yaml."
        diff={diffRows}
        onConfirm={onConfirmSave}
        isPending={update.isPending}
        errorMessage={diffOpen ? errorMessage : null}
      />
    </section>
  );
}

type NumberFieldProps = {
  fieldKey: FieldKey;
  value: number;
  onChange: (key: FieldKey, raw: string) => void;
  error?: string;
  min?: number;
  max?: number;
};

function NumberField({
  fieldKey,
  value,
  onChange,
  error,
  min,
  max,
}: NumberFieldProps) {
  const meta = FIELD_LABELS[fieldKey];
  const id = `rec-${fieldKey}`;
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id}>
        {meta.label}
        {meta.unit && (
          <span className="ml-1 text-xs font-normal text-muted-foreground">
            ({meta.unit})
          </span>
        )}
      </Label>
      <Input
        id={id}
        type="number"
        min={min}
        max={max}
        value={value}
        onChange={(e) => onChange(fieldKey, e.target.value)}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${id}-err` : undefined}
      />
      {error ? (
        <p id={`${id}-err`} className="text-xs text-destructive">
          {error}
        </p>
      ) : meta.hint ? (
        <p className="text-xs text-muted-foreground">{meta.hint}</p>
      ) : null}
    </div>
  );
}
