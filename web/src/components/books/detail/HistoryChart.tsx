// History chart — Recharts line chart, ONE LINE PER SOURCE, each tracing that
// source's cheapest AVAILABLE price over time (the lowest-price envelope).
//
// Why per-source: a single scrape stamps every source with its own
// `observed_at`, so eBay (whose total includes a chunky postage charge) lands
// on a timestamp where no Amazon offer is present. The old "collapse all
// sources into one New/Used line, MIN per timestamp" logic then drew a point
// at eBay's lone, expensive total and a neighbouring point at Amazon's cheap
// one — a phantom spike. Keeping each source on its own line shows eBay's
// price in its own context and removes the cross-source blending entirely.
//
// Why an envelope (live intervals), not points at `observed_at`: dedup folds
// every re-sighting onto the first one, so a stable cheap offer has a single
// point stranded at its first sighting. Plotting that way makes the cheap offer
// vanish on later scrapes, leaving only newly-seen pricier offers to spike the
// line. Instead each offer is treated as alive across [observed_at, last_seen]
// and we plot min(total) over the offers live at each breakpoint — the true
// "cheapest you could have paid" at every moment. See buildSeries.
//
// Time range filter (7d / 30d / 90d / all) clips the data client-side rather
// than refetching with a smaller window — the request is capped at 500 rows
// regardless, and the cached observations response already includes the full
// window, so re-clipping is free.
//
// Legend toggle: each `<Line>` is keyed off the series key; clicking the
// Recharts legend hides/shows the series natively. We track the hidden set in
// component state and pass `hide` to each `<Line>` so toggling is observable.

import { useMemo, useState } from "react";
import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  type LegendPayload,
} from "recharts";

import { Skeleton } from "@/components/ui/skeleton";
import type { ItemObservation } from "@/lib/item";
import { formatDateTime, formatMoneyMinor } from "@/lib/format";

type Range = "7d" | "30d" | "90d" | "all";

// £1 padding on both axis ends + round to whole-pound ticks.
const Y_AXIS_PAD_PENCE = 100;

const RANGE_SECONDS: Record<Exclude<Range, "all">, number> = {
  "7d": 7 * 24 * 60 * 60,
  "30d": 30 * 24 * 60 * 60,
  "90d": 90 * 24 * 60 * 60,
};

const RANGE_LABEL: Record<Range, string> = {
  "7d": "7 days",
  "30d": "30 days",
  "90d": "90 days",
  all: "All",
};

// Tailwind palette tokens; cycled per series. Recharts wants concrete colour
// values, so we resolve to hex here (matching Tailwind's stone/blue/etc).
const SERIES_COLORS = [
  "#2563eb", // blue-600
  "#dc2626", // red-600
  "#16a34a", // green-600
  "#d97706", // amber-600
  "#7c3aed", // violet-600
  "#0891b2", // cyan-600
  "#db2777", // pink-600
  "#65a30d", // lime-600
];

// One readable label per source; unknown sources fall back to the raw key.
// (`amazon` and `amazon_uk_product` never co-occur on one item, so collapsing
// both to "Amazon" is safe.)
const SOURCE_LABEL: Record<string, string> = {
  amazon: "Amazon",
  amazon_uk_product: "Amazon",
  wob: "World of Books",
  bookfinder: "eBay (BookFinder)",
  keepa: "Keepa",
};

function sourceLabel(source: string): string {
  return SOURCE_LABEL[source] ?? source;
}

type ChartRow = { ts: number } & Record<string, number | null>;

// An offer as a live interval. Dedup keeps only the FIRST sighting of a stable
// offer, but `last_seen` tells us how long it stayed available, so we treat
// each offer as alive across [observed, last].
type LiveOffer = { observed: number; last: number; total: number };

function buildSeries(observations: ItemObservation[], range: Range): {
  rows: ChartRow[];
  series: string[];
} {
  const now = Date.now();
  const cutoff = range === "all" ? 0 : now - RANGE_SECONDS[range] * 1000;

  // Group offers by source as live intervals. Drop offers whose last sighting
  // predates the window — anything still alive at the window edge is kept.
  const bySource = new Map<string, LiveOffer[]>();
  for (const o of observations) {
    const last = new Date(o.last_seen).getTime();
    if (last < cutoff) continue;
    const key = sourceLabel(o.source);
    const offers = bySource.get(key) ?? [];
    offers.push({
      observed: new Date(o.observed_at).getTime(),
      last,
      total: o.total_minor,
    });
    bySource.set(key, offers);
  }

  // Per source, draw the cheapest offer ACTUALLY LIVE at each moment — the
  // "lowest available price" envelope. Plotting points at `observed_at` instead
  // is wrong: a stable cheap offer is deduped to a single first-sighting, so on
  // later scrapes only newly-seen (often pricier) offers have a point there and
  // the line spikes up to them while the cheaper offer is silently still live.
  // We evaluate min(total) over live offers at every interval breakpoint (each
  // observed/last, clamped into the window) to reconstruct the true envelope.
  const byTs = new Map<number, ChartRow>();
  const series: string[] = [];
  for (const [key, offers] of bySource) {
    series.push(key);
    const breakpoints = new Set<number>();
    for (const o of offers) {
      breakpoints.add(Math.max(o.observed, cutoff)); // clamp left edge in-window
      breakpoints.add(Math.min(o.last, now)); // never plot past "now"
    }
    for (const t of breakpoints) {
      let min: number | null = null;
      for (const o of offers) {
        if (o.observed <= t && t <= o.last && (min == null || o.total < min)) {
          min = o.total;
        }
      }
      if (min == null) continue;
      let row = byTs.get(t);
      if (!row) {
        row = { ts: t };
        byTs.set(t, row);
      }
      const prev = row[key];
      if (prev == null || min < prev) row[key] = min;
    }
  }

  return {
    rows: [...byTs.values()].sort((a, b) => a.ts - b.ts),
    series: series.sort((a, b) => a.localeCompare(b)),
  };
}

function TooltipContent({
  active,
  payload,
  label,
}: {
  active?: boolean;
  payload?: ReadonlyArray<{
    dataKey: string;
    value: number | null;
    color: string;
  }>;
  label?: number;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-md border border-border bg-popover p-2 text-xs shadow-md">
      <p className="font-medium">{label != null ? formatDateTime(new Date(label).toISOString()) : ""}</p>
      <ul className="mt-1 space-y-0.5">
        {payload.map((p) => (
          <li key={p.dataKey} className="flex items-center gap-2">
            <span
              aria-hidden
              className="inline-block size-2 rounded-full"
              style={{ background: p.color }}
            />
            <span className="text-muted-foreground">{p.dataKey}</span>
            <span className="font-medium">
              {formatMoneyMinor(p.value ?? null)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function HistoryChart({
  observations,
  isLoading,
}: {
  observations: ItemObservation[];
  isLoading: boolean;
}) {
  const [range, setRange] = useState<Range>("90d");
  const [hidden, setHidden] = useState<Set<string>>(new Set());

  const { rows, series } = useMemo(
    () => buildSeries(observations, range),
    [observations, range],
  );

  const toggleSeries = (key: string) => {
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  return (
    <div className="rounded-md border border-border bg-card p-4">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-xs font-medium uppercase text-muted-foreground">
          Price history
        </h2>
        <div className="flex gap-1">
          {(Object.keys(RANGE_LABEL) as Range[]).map((r) => (
            <button
              key={r}
              type="button"
              onClick={() => setRange(r)}
              className={`rounded-md px-2 py-0.5 text-xs ${
                r === range
                  ? "bg-muted text-foreground"
                  : "text-muted-foreground hover:bg-muted/50"
              }`}
            >
              {RANGE_LABEL[r]}
            </button>
          ))}
        </div>
      </div>

      <div className="mt-3 h-64">
        {isLoading ? (
          <Skeleton className="h-full rounded" />
        ) : rows.length === 0 ? (
          <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
            No price history in this window.
          </div>
        ) : (
          <ResponsiveContainer width="100%" height="100%">
            <LineChart data={rows} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="currentColor" opacity={0.1} />
              <XAxis
                dataKey="ts"
                type="number"
                domain={["dataMin", "dataMax"]}
                tickFormatter={(v: number) =>
                  new Intl.DateTimeFormat(undefined, {
                    month: "short",
                    day: "numeric",
                  }).format(new Date(v))
                }
                stroke="currentColor"
                tick={{ fontSize: 11, opacity: 0.7 }}
              />
              <YAxis
                tickFormatter={(v: number) => formatMoneyMinor(v)}
                stroke="currentColor"
                tick={{ fontSize: 11, opacity: 0.7 }}
                width={64}
                domain={[
                  (dataMin: number) =>
                    Math.max(0, Math.floor((dataMin - Y_AXIS_PAD_PENCE) / Y_AXIS_PAD_PENCE) * Y_AXIS_PAD_PENCE),
                  (dataMax: number) =>
                    Math.ceil((dataMax + Y_AXIS_PAD_PENCE) / Y_AXIS_PAD_PENCE) * Y_AXIS_PAD_PENCE,
                ]}
                allowDataOverflow={false}
              />
              <Tooltip content={<TooltipContent />} />
              <Legend
                onClick={(payload: LegendPayload) => {
                  const key = (payload as { dataKey?: string }).dataKey;
                  if (typeof key === "string") toggleSeries(key);
                }}
                wrapperStyle={{ fontSize: 11 }}
              />
              {series.map((key, i) => (
                <Line
                  key={key}
                  type="monotone"
                  dataKey={key}
                  stroke={SERIES_COLORS[i % SERIES_COLORS.length]}
                  strokeWidth={2}
                  dot={{ r: 2 }}
                  activeDot={{ r: 4 }}
                  connectNulls
                  hide={hidden.has(key)}
                  isAnimationActive={false}
                />
              ))}
            </LineChart>
          </ResponsiveContainer>
        )}
      </div>
    </div>
  );
}
