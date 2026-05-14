// Filter bar for the full /alerts page.
//
// Controls:
//   • dismissed: active (default) | dismissed | all
//   • kind:      all (default) | target_hit | percentile_cross | new_low
//   • book:      free-text title filter (client-side, case-insensitive)
//
// The dismissed + kind selections feed the `useAlerts` query params; the
// book filter is applied client-side over the resolved title map because the
// backend has no title-search endpoint.

import type { AlertKind } from "@/hooks/useAlerts";

export type DismissedFilter = "active" | "dismissed" | "all";
export type KindFilter = "all" | AlertKind;

export type Filters = {
  dismissed: DismissedFilter;
  kind: KindFilter;
  book: string;
};

type Props = {
  value: Filters;
  onChange: (next: Filters) => void;
};

export function AlertFilters({ value, onChange }: Props) {
  return (
    <div className="flex flex-wrap items-end gap-3">
      <label className="flex flex-col gap-1 text-xs text-muted-foreground">
        Status
        <select
          value={value.dismissed}
          onChange={(e) =>
            onChange({ ...value, dismissed: e.target.value as DismissedFilter })
          }
          className="h-8 rounded-md border border-input bg-background px-2 text-sm"
        >
          <option value="active">Active</option>
          <option value="dismissed">Dismissed</option>
          <option value="all">All</option>
        </select>
      </label>

      <label className="flex flex-col gap-1 text-xs text-muted-foreground">
        Kind
        <select
          value={value.kind}
          onChange={(e) =>
            onChange({ ...value, kind: e.target.value as KindFilter })
          }
          className="h-8 rounded-md border border-input bg-background px-2 text-sm"
        >
          <option value="all">All kinds</option>
          <option value="target_hit">Target hit</option>
          <option value="percentile_cross">Percentile cross</option>
          <option value="new_low">New low</option>
        </select>
      </label>

      <label className="flex flex-col gap-1 text-xs text-muted-foreground">
        Book
        <input
          type="search"
          value={value.book}
          onChange={(e) => onChange({ ...value, book: e.target.value })}
          placeholder="Filter by title…"
          className="h-8 w-56 rounded-md border border-input bg-background px-2 text-sm"
        />
      </label>
    </div>
  );
}
