// Shared helpers for the `PUT /api/config` flow used by the Recommendation /
// Notifications / Advanced settings tabs.
//
// `diffToRows` flattens the backend's top-level `{added, removed, changed}`
// diff into the row shape `<DiffPreviewDialog>` consumes. Top-level entries
// render as a single row by default; sections listed in `expand` recurse one
// level deeper so nested-field changes (e.g. `notifications.channels.ntfy.topic`)
// show up as individual rows rather than a JSON blob.
//
// `formatPutError` extracts the `detail.errors: list[str]` payload that the
// PUT handler emits on 422 (see Task 7.5). Falls back to a single-element
// list for any other shape.

import { ApiError } from "@/api/client";
import type { DiffRow } from "@/components/settings/DiffPreviewDialog";
import { formatErrorMessage } from "@/lib/utils";
import type { ConfigUpdateResult } from "@/hooks/useConfig";

export type DiffToRowsOptions = {
  // Top-level keys whose `{before, after}` pair should be recursed into so
  // nested fields surface as their own rows.
  expand?: ReadonlySet<string>;
};

export function diffToRows(
  diff: ConfigUpdateResult["diff"],
  options: DiffToRowsOptions = {},
): DiffRow[] {
  const expand = options.expand ?? new Set<string>();
  const rows: DiffRow[] = [];
  for (const [key, ba] of Object.entries(diff.changed ?? {})) {
    const before = (ba as { before?: unknown }).before;
    const after = (ba as { after?: unknown }).after;
    if (
      expand.has(key) &&
      isPlainObject(before) &&
      isPlainObject(after)
    ) {
      walkObjectDiff(before, after, key, rows);
      continue;
    }
    rows.push({
      field: key,
      oldValue: JSON.stringify(before),
      newValue: JSON.stringify(after),
    });
  }
  for (const [key, value] of Object.entries(diff.added ?? {})) {
    rows.push({ field: key, oldValue: "—", newValue: JSON.stringify(value) });
  }
  for (const [key, value] of Object.entries(diff.removed ?? {})) {
    rows.push({ field: key, oldValue: JSON.stringify(value), newValue: "—" });
  }
  return rows;
}

function isPlainObject(v: unknown): v is Record<string, unknown> {
  return v !== null && typeof v === "object" && !Array.isArray(v);
}

// Recursive walk over an expanded sub-tree. Leaves render as one row; nested
// objects descend one level. Arrays + scalars are leaves (compared via
// JSON.stringify so ordering matters — same as the backend diff).
function walkObjectDiff(
  before: Record<string, unknown>,
  after: Record<string, unknown>,
  prefix: string,
  out: DiffRow[],
): void {
  const fields = new Set([...Object.keys(before), ...Object.keys(after)]);
  for (const f of fields) {
    const b = before[f];
    const a = after[f];
    if (isPlainObject(b) && isPlainObject(a)) {
      walkObjectDiff(b, a, `${prefix}.${f}`, out);
      continue;
    }
    if (JSON.stringify(b) !== JSON.stringify(a)) {
      out.push({
        field: `${prefix}.${f}`,
        oldValue: JSON.stringify(b),
        newValue: JSON.stringify(a),
      });
    }
  }
}

// Pull `detail.errors: list[str]` out of a 422 ApiError body; fall back to a
// single-element list for any other error shape so callers can render
// uniformly. Returns `[]` for null/undefined.
export function formatPutError(err: ApiError | null | undefined): string[] {
  if (!err) return [];
  if (err.status === 422 && err.body && typeof err.body === "object") {
    const body = err.body as { detail?: unknown };
    const detail = body.detail;
    if (detail && typeof detail === "object") {
      const errors = (detail as { errors?: unknown }).errors;
      if (Array.isArray(errors) && errors.length > 0) {
        return errors.map((e) => String(e));
      }
    }
  }
  return [formatErrorMessage(err)];
}

// Convenience for callers that want a single inline string (Recommendation /
// Notifications). Returns null when there's no error.
export function formatPutErrorMessage(
  err: ApiError | null | undefined,
): string | null {
  if (!err) return null;
  const parts = formatPutError(err);
  if (parts.length === 0) return null;
  if (err.status === 422) return `Validation failed: ${parts.join("; ")}`;
  return `Save failed (${parts.join("; ")})`;
}
