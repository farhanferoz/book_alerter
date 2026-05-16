/* eslint-disable react-refresh/only-export-components */
// Filter bar above the dashboard table.
//
// Spec (§ Dashboard) calls for: signal · status · source-health · sort.
// 10.1 implements signal + status + sort; source-health needs the sources
// runs endpoint (Phase 11.2). Plain native <select> elements — shadcn's
// Select primitive is a Phase 11.x add when the design demands it.

import type { Signal } from "./signal";

export type StatusFilter = "active" | "archived" | "bought" | "all";
export type SignalFilter = Signal | "ALL";
export type SortKey =
  | "signal"
  | "best_price"
  | "percentile"
  | "last_seen"
  | "title";

export interface BookFiltersValue {
  signal: SignalFilter;
  status: StatusFilter;
  sort: SortKey;
}

export const DEFAULT_FILTERS: BookFiltersValue = {
  signal: "ALL",
  status: "active",
  sort: "signal",
};

const SIGNAL_OPTIONS: { value: SignalFilter; label: string }[] = [
  { value: "ALL", label: "All signals" },
  { value: "BUY", label: "BUY" },
  { value: "TARGET_HIT", label: "TARGET HIT" },
  { value: "WATCH", label: "WATCH" },
  { value: "WAIT", label: "WAIT" },
  { value: "INSUFFICIENT_DATA", label: "No data" },
];

const STATUS_OPTIONS: { value: StatusFilter; label: string }[] = [
  { value: "active", label: "Active" },
  { value: "bought", label: "Bought" },
  { value: "archived", label: "Archived" },
  { value: "all", label: "All" },
];

const SORT_OPTIONS: { value: SortKey; label: string }[] = [
  { value: "signal", label: "Signal" },
  { value: "best_price", label: "Best price" },
  { value: "percentile", label: "Percentile (3m)" },
  { value: "last_seen", label: "Last seen" },
  { value: "title", label: "Title" },
];

interface BookFiltersProps {
  value: BookFiltersValue;
  onChange: (next: BookFiltersValue) => void;
}

const SELECT_CLASS =
  "h-8 rounded-md border border-border bg-background px-2 text-sm";

export function BookFilters({ value, onChange }: BookFiltersProps) {
  return (
    <div className="flex flex-wrap items-center gap-2">
      <label className="flex items-center gap-1 text-xs text-muted-foreground">
        Signal
        <select
          className={SELECT_CLASS}
          value={value.signal}
          onChange={(e) =>
            onChange({ ...value, signal: e.target.value as SignalFilter })
          }
        >
          {SIGNAL_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </label>
      <label className="flex items-center gap-1 text-xs text-muted-foreground">
        Status
        <select
          className={SELECT_CLASS}
          value={value.status}
          onChange={(e) =>
            onChange({ ...value, status: e.target.value as StatusFilter })
          }
        >
          {STATUS_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </label>
      <label className="flex items-center gap-1 text-xs text-muted-foreground">
        Sort by
        <select
          className={SELECT_CLASS}
          value={value.sort}
          onChange={(e) =>
            onChange({ ...value, sort: e.target.value as SortKey })
          }
        >
          {SORT_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </label>
    </div>
  );
}
