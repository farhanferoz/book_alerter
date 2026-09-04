// Detail-page distribution chart: horizontal box-plot per window over
// imputed totals, with a vertical current-price line cutting all rows.
// Hand-rolled SVG — Recharts has no native box-plot shape.

import type { Item } from "@/lib/item";
import { formatMoneyMinor } from "@/lib/format";
import { WINDOW_KEYS } from "@/lib/windows";

function emptyState(message: string) {
  return (
    <div className="rounded-md border border-border bg-card p-4">
      <h2 className="text-xs font-medium uppercase text-muted-foreground">
        Price distribution
      </h2>
      <p className="mt-2 text-sm text-muted-foreground">{message}</p>
    </div>
  );
}

function niceTicks(min: number, max: number, target = 5): number[] {
  // 1/2/5 × 10^n step picker for a tidy £ axis.
  const range = max - min;
  if (range <= 0) return [min];
  const roughStep = range / target;
  const pow10 = Math.pow(10, Math.floor(Math.log10(roughStep)));
  const candidates = [1, 2, 5, 10].map((m) => m * pow10);
  const step =
    candidates.find((c) => range / c <= target * 1.2) ??
    candidates[candidates.length - 1];
  const first = Math.ceil(min / step) * step;
  const ticks: number[] = [];
  for (let t = first; t <= max + step * 0.001; t += step) ticks.push(t);
  return ticks;
}

export function PercentileChart({ item }: { item: Item }) {
  const windows = item.stats.windows ?? {};
  const current = item.stats.current_effective_total_minor;
  const shippingEstimate = item.stats.shipping_estimate_minor;
  const usedImputedShipping =
    item.stats.current_best_shipping_minor == null && shippingEstimate != null;

  const hasAnyData = WINDOW_KEYS.some((k) => windows[k]?.p5 != null);
  if (!hasAnyData) {
    return emptyState(
      "Not enough history to draw distributions yet. Collect a few more observations and check back.",
    );
  }

  // X-axis range: span every visible whisker + the current price, padded
  // 6% on each side so endpoints don't kiss the plot edge.
  const xs: number[] = [];
  for (const k of WINDOW_KEYS) {
    const w = windows[k];
    if (w?.p5 != null) xs.push(w.p5);
    if (w?.p95 != null) xs.push(w.p95);
  }
  if (current != null) xs.push(current);
  const dMin = Math.min(...xs);
  const dMax = Math.max(...xs);
  const span = Math.max(dMax - dMin, 100);
  const pad = span * 0.06;
  const xMin = Math.max(0, dMin - pad);
  const xMax = dMax + pad;

  const W = 800;
  const labelW = 48;
  const plotL = labelW + 16;
  const plotR = W - 20;
  const plotW = plotR - plotL;
  const axisH = 22;
  const rowH = 36;
  const topPad = 8;
  const H = topPad + axisH + rowH * WINDOW_KEYS.length + 12;

  const x = (p: number) => plotL + ((p - xMin) / (xMax - xMin)) * plotW;
  const ticks = niceTicks(xMin, xMax, 5);

  return (
    <div className="rounded-md border border-border bg-card p-4">
      <div className="flex items-baseline justify-between gap-2">
        <h2 className="text-xs font-medium uppercase text-muted-foreground">
          Price distribution
        </h2>
        {current != null && (
          <span className="text-xs text-muted-foreground">
            {usedImputedShipping ? "Effective" : "Current"}{" "}
            <span className="font-medium text-foreground">
              {formatMoneyMinor(current, item.currency)}
            </span>
            {usedImputedShipping && (
              <span className="ml-1">
                (incl. ~{formatMoneyMinor(shippingEstimate, item.currency)} est. ship)
              </span>
            )}
          </span>
        )}
      </div>
      <p className="mt-1 text-xs text-muted-foreground">
        Box = p25–p75 · line in box = median · whiskers = p5/p95.
        {usedImputedShipping &&
          " Distribution uses shipping-imputed totals so offers with and without listed shipping rank consistently."}
      </p>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="mt-3 w-full"
        role="img"
        aria-label="Price distribution box plot across 1m, 3m, and 12m windows"
      >
        {ticks.map((t, i) => (
          <g key={`tick-${i}`}>
            <line
              x1={x(t)}
              x2={x(t)}
              y1={topPad + axisH - 4}
              y2={H - 12}
              className="stroke-border/40"
              strokeWidth={1}
            />
            <text
              x={x(t)}
              y={topPad + axisH - 8}
              textAnchor="middle"
              className="fill-muted-foreground text-[10px]"
            >
              {formatMoneyMinor(t, item.currency)}
            </text>
          </g>
        ))}

        {WINDOW_KEYS.map((k, i) => {
          const w = windows[k];
          const yCenter = topPad + axisH + i * rowH + rowH / 2;
          const empty = !w || w.count === 0 || w.p5 == null;
          return (
            <g key={k} opacity={empty ? 0.4 : 1}>
              <text
                x={labelW}
                y={yCenter + 4}
                textAnchor="end"
                className="fill-muted-foreground text-[11px] uppercase"
              >
                {k}
              </text>
              <text
                x={labelW}
                y={yCenter + 16}
                textAnchor="end"
                className="fill-muted-foreground/70 text-[9px]"
              >
                {empty ? "no data" : `n=${w!.count}`}
              </text>
              {empty ? (
                <line
                  x1={plotL}
                  x2={plotR}
                  y1={yCenter}
                  y2={yCenter}
                  className="stroke-border"
                  strokeWidth={1}
                  strokeDasharray="3 3"
                />
              ) : (
                <>
                  <line
                    x1={x(w!.p5!)}
                    x2={x(w!.p95!)}
                    y1={yCenter}
                    y2={yCenter}
                    className="stroke-foreground/60"
                    strokeWidth={1.5}
                  />
                  {[w!.p5!, w!.p95!].map((v, ci) => (
                    <line
                      key={`cap-${ci}`}
                      x1={x(v)}
                      x2={x(v)}
                      y1={yCenter - 6}
                      y2={yCenter + 6}
                      className="stroke-foreground/60"
                      strokeWidth={1.5}
                    />
                  ))}
                  {w!.p25 != null && w!.p75 != null && (
                    <rect
                      x={x(w!.p25)}
                      y={yCenter - 10}
                      width={Math.max(x(w!.p75) - x(w!.p25), 1)}
                      height={20}
                      className="fill-primary/25 stroke-primary/80"
                      strokeWidth={1}
                      rx={2}
                    />
                  )}
                  {w!.p50 != null && (
                    <line
                      x1={x(w!.p50)}
                      x2={x(w!.p50)}
                      y1={yCenter - 10}
                      y2={yCenter + 10}
                      className="stroke-foreground"
                      strokeWidth={2}
                    />
                  )}
                </>
              )}
            </g>
          );
        })}

        {current != null && (
          <g>
            <line
              x1={x(current)}
              x2={x(current)}
              y1={topPad + axisH - 2}
              y2={H - 12}
              className="stroke-emerald-600 dark:stroke-emerald-400"
              strokeWidth={1.5}
              strokeDasharray="4 3"
            />
            <text
              x={x(current)}
              y={H - 2}
              textAnchor="middle"
              className="fill-emerald-700 text-[10px] font-medium dark:fill-emerald-300"
            >
              {formatMoneyMinor(current, item.currency)}
            </text>
          </g>
        )}
      </svg>
    </div>
  );
}
