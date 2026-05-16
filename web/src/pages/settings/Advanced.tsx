// Settings → Advanced tab (Phase 11.5).
//
// Monaco YAML editor for the full config — useful for power users who want
// to hand-edit things the other tabs don't expose. Flow:
//
//   1. Load `config` (GET /api/config) and `schema` (GET /api/config/schema).
//   2. Dump the config to YAML, seed the Monaco editor.
//   3. User edits. "Validate" parses YAML → JSON, then calls
//      `PUT /api/config?dry_run=true` so the backend (Pydantic) is the
//      authoritative validator. Errors surface inline below the editor.
//   4. "Save" — requires the latest validation to have succeeded AND the
//      draft differ from server. Opens `<DiffPreviewDialog>` with the
//      backend-computed diff; on confirm, re-issues PUT with dry_run=false.
//
// Trade-off (documented deviation from the plan): the plan wording is "live
// JSON-schema validation". Wiring Monaco's full YAML schema validation
// requires the heavier `monaco-yaml` worker setup; we opted instead for
// "validate on click" via dry-run PUT. The backend is the authoritative
// validator anyway — client-side Ajv would only duplicate it. Net: fewer
// deps, same authoritative answer, one extra click.
//
// Theme: a MutationObserver on `<html>.classList` keeps Monaco in sync with
// the global dark-mode toggle without a page reload.
//
// Bundle: route-split via `React.lazy` in App.tsx so Monaco only loads when
// the user navigates here (same pattern as BookDetail / Recharts).

import { useCallback, useEffect, useMemo, useState } from "react";

import yaml from "js-yaml";
import Editor from "@monaco-editor/react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { DiffPreviewDialog } from "@/components/settings/DiffPreviewDialog";
import {
  useConfig,
  useConfigSchema,
  useUpdateConfig,
  type ConfigShape,
  type ConfigUpdateResult,
} from "@/hooks/useConfig";
import { useSavedFlash } from "@/hooks/useSavedFlash";
import { diffToRows, formatPutError } from "@/lib/config-diff";
import { formatErrorMessage } from "@/lib/utils";

const EDITOR_HEIGHT = "600px";

type ValidationState =
  | { kind: "idle" }
  | { kind: "ok"; diff: ConfigUpdateResult["diff"]; candidate: ConfigShape }
  | { kind: "yaml-error"; message: string }
  | { kind: "backend-error"; errors: string[] };

function configToYaml(cfg: ConfigShape): string {
  // `noRefs: true` avoids YAML anchor/alias output (unfriendly for hand-edit
  // round-trips); `sortKeys: false` preserves insertion order so the editor
  // matches the on-disk file's natural layout.
  return yaml.dump(cfg, { noRefs: true, sortKeys: false, lineWidth: 100 });
}

function readInitialDark(): boolean {
  return (
    typeof document !== "undefined" &&
    document.documentElement.classList.contains("dark")
  );
}

export function SettingsAdvanced() {
  const cfg = useConfig();
  const schema = useConfigSchema();

  if (cfg.isPending || schema.isPending) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-6 w-64" />
        <Skeleton className="h-96 w-full" />
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

  // schema fetch failure is non-fatal — the editor still works; the schema
  // panel just stays empty.
  return <AdvancedEditor config={cfg.data} schema={schema.data ?? null} />;
}

type AdvancedEditorProps = {
  config: ConfigShape;
  schema: Record<string, unknown> | null;
};

function AdvancedEditor({ config, schema }: AdvancedEditorProps) {
  const serverYaml = useMemo(() => configToYaml(config), [config]);
  const [draftYaml, setDraftYaml] = useState<string>(serverYaml);
  const [validation, setValidation] = useState<ValidationState>({ kind: "idle" });
  const [diffOpen, setDiffOpen] = useState(false);
  const [showSchema, setShowSchema] = useState(false);
  const { savedAt, flash: flashSaved } = useSavedFlash();
  const update = useUpdateConfig();
    // Live-watch `<html>.classList` for the `dark` toggle so Monaco re-themes
  // without a page reload. MutationObserver only observes the attribute we
  // care about (`class`) so it stays cheap.
  const [isDark, setIsDark] = useState<boolean>(readInitialDark);
  useEffect(() => {
    const root = document.documentElement;
    const observer = new MutationObserver(() => {
      setIsDark(root.classList.contains("dark"));
    });
    observer.observe(root, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);
  const theme = isDark ? "vs-dark" : "light";

  // Reset draft + validation if the server config changes underneath us
  // (e.g. another tab persisted via PUT). Key-on-server-yaml at parent
  // would force-remount; instead, watch for serverYaml diverging from the
  // draft baseline and let the user keep editing — Reset clears.
  const dirty = draftYaml !== serverYaml;

  const onValidate = useCallback(() => {
    update.reset();
    let parsed: unknown;
    try {
      parsed = yaml.load(draftYaml);
    } catch (e) {
      setValidation({
        kind: "yaml-error",
        message: e instanceof Error ? e.message : String(e),
      });
      return;
    }
    if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
      setValidation({
        kind: "yaml-error",
        message: "Top-level YAML must be a mapping (key: value pairs).",
      });
      return;
    }
    const candidate = parsed as ConfigShape;
    update.mutate(
      { config: candidate, dryRun: true },
      {
        onSuccess: (result) => {
          if (result.errors && result.errors.length > 0) {
            setValidation({ kind: "backend-error", errors: result.errors });
            return;
          }
          setValidation({ kind: "ok", diff: result.diff, candidate });
        },
        onError: (err) => {
          setValidation({
            kind: "backend-error",
            errors: formatPutError(err),
          });
        },
      },
    );
  }, [draftYaml, update]);

  const onOpenDiff = () => {
    if (validation.kind !== "ok") return;
    setDiffOpen(true);
  };

  const onConfirmSave = () => {
    if (validation.kind !== "ok") return;
    update.mutate(
      { config: validation.candidate, dryRun: false },
      {
        onSuccess: (result) => {
          if (result.applied) {
            setDiffOpen(false);
            // Move the editor's baseline to the just-saved YAML so "dirty"
            // resets. The useConfig query invalidation will re-fetch and
            // ultimately reset draftYaml on next mount, but we don't want
            // a flicker — set explicitly.
            const fresh = configToYaml(validation.candidate);
            setDraftYaml(fresh);
            setValidation({ kind: "idle" });
            flashSaved();
          }
        },
      },
    );
  };

  const onReset = () => {
    setDraftYaml(serverYaml);
    setValidation({ kind: "idle" });
    update.reset();
  };

  const diffRows = validation.kind === "ok" ? diffToRows(validation.diff) : [];

  return (
    <section className="space-y-3">
      <header className="flex items-start justify-between gap-3">
        <div>
          <h2 className="text-sm font-semibold">Raw config (advanced)</h2>
          <p className="text-xs text-muted-foreground">
            Edit <code className="rounded bg-muted px-1 font-mono">config.yaml</code>{" "}
            directly. Validation runs server-side on click; the diff preview
            shows what will change before save.
          </p>
        </div>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={() => setShowSchema((s) => !s)}
          disabled={!schema}
        >
          {showSchema ? "Hide schema" : "Show schema"}
        </Button>
      </header>

      <div className="grid grid-cols-1 gap-3 lg:grid-cols-[1fr_auto]">
        <div className="overflow-hidden rounded-md border border-border">
          <Editor
            height={EDITOR_HEIGHT}
            language="yaml"
            value={draftYaml}
            onChange={(value) => {
              setDraftYaml(value ?? "");
              // Any edit invalidates the previous validation result.
              if (validation.kind !== "idle") {
                setValidation({ kind: "idle" });
              }
            }}
            theme={theme}
            options={{
              minimap: { enabled: false },
              fontSize: 13,
              automaticLayout: true,
              tabSize: 2,
              scrollBeyondLastLine: false,
              wordWrap: "on",
            }}
          />
        </div>
        {showSchema && schema && (
          <aside className="hidden max-h-[600px] w-96 overflow-auto rounded-md border border-border bg-muted/30 p-2 lg:block">
            <pre className="text-[11px] leading-snug font-mono">
              {JSON.stringify(schema, null, 2)}
            </pre>
          </aside>
        )}
      </div>

      <ValidationMessage state={validation} />

      <div className="flex flex-wrap items-center gap-3">
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={onReset}
          disabled={!dirty || update.isPending}
        >
          Reset
        </Button>
        <Button
          type="button"
          size="sm"
          variant="outline"
          onClick={onValidate}
          disabled={!dirty || update.isPending}
        >
          {update.isPending && !diffOpen ? "Validating…" : "Validate"}
        </Button>
        <Button
          type="button"
          size="sm"
          onClick={onOpenDiff}
          disabled={validation.kind !== "ok" || !dirty || update.isPending}
        >
          Save…
        </Button>
        {savedAt && (
          <span className="text-xs text-emerald-600 dark:text-emerald-400">
            Saved at {savedAt}
          </span>
        )}
      </div>

      <DiffPreviewDialog
        open={diffOpen}
        onOpenChange={(open) => {
          if (!update.isPending) setDiffOpen(open);
        }}
        title="Save config changes"
        description="Review the changed top-level sections before writing config.yaml."
        diff={diffRows}
        onConfirm={onConfirmSave}
        isPending={update.isPending}
        errorMessage={
          diffOpen && update.error
            ? formatPutError(update.error).join("; ")
            : null
        }
      />
    </section>
  );
}

function ValidationMessage({ state }: { state: ValidationState }) {
  if (state.kind === "idle") {
    return (
      <p className="text-xs text-muted-foreground">
        Edit the YAML above, then click <strong>Validate</strong> to check it
        against the config schema.
      </p>
    );
  }
  if (state.kind === "yaml-error") {
    return (
      <div className="rounded-md border border-destructive/40 bg-destructive/5 p-2 text-xs text-destructive">
        <strong>YAML parse error:</strong> {state.message}
      </div>
    );
  }
  if (state.kind === "backend-error") {
    return (
      <div className="rounded-md border border-destructive/40 bg-destructive/5 p-2 text-xs text-destructive">
        <strong>Validation failed:</strong>
        <ul className="mt-1 list-disc space-y-0.5 pl-4">
          {state.errors.map((e, i) => (
            <li key={i} className="font-mono">{e}</li>
          ))}
        </ul>
      </div>
    );
  }
  // ok
  return (
    <div className="rounded-md border border-emerald-500/40 bg-emerald-50 p-2 text-xs text-emerald-700 dark:bg-emerald-900/20 dark:text-emerald-400">
      Valid. Click <strong>Save…</strong> to review the diff and write
      <code className="ml-1 rounded bg-emerald-100 px-1 font-mono dark:bg-emerald-900/40">
        config.yaml
      </code>
      .
    </div>
  );
}
