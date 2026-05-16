// Dashboard column: three stacked mini-bars (1m / 3m / 12m). Dot position
// = current price's percentile rank in that window. Dot color encodes
// goodness (low rank = cheap = green).

import type { Book } from "@/hooks/useBooks";
import { ordinalSuffix } from "@/lib/format";
import { WINDOW_KEYS } from "@/lib/windows";

function dotClass(rank: number | null): string {
  if (rank == null) return "hidden";
  if (rank <= 25) return "bg-green-600 dark:bg-green-400";
  if (rank <= 50) return "bg-amber-500 dark:bg-amber-400";
  return "bg-rose-600 dark:bg-rose-400";
}

function tooltip(book: Book): string {
  const windows = book.stats.windows ?? {};
  return WINDOW_KEYS.map((k) => {
    const w = windows[k];
    if (!w || w.count === 0) return `${k}: no data`;
    const rank = w.rank == null ? "—" : `${w.rank}${ordinalSuffix(w.rank)}`;
    return `${k}: ${rank} (n=${w.count})`;
  }).join(" · ");
}

export function MiniBars({ book }: { book: Book }) {
  const windows = book.stats.windows ?? {};
  const title = tooltip(book);
  return (
    <div
      role="img"
      className="flex flex-col gap-1 tabular-nums"
      title={title}
      aria-label={title}
    >
      {WINDOW_KEYS.map((k) => {
        const w = windows[k];
        const empty = !w || w.count === 0;
        const rank = w?.rank ?? null;
        return (
          <div key={k} className="flex items-center gap-1.5">
            <span
              className={`w-6 text-[10px] uppercase ${
                empty ? "text-muted-foreground/50" : "text-muted-foreground"
              }`}
            >
              {k}
            </span>
            <div
              className={`relative h-1 w-16 rounded-full ${
                empty ? "bg-muted/50" : "bg-muted"
              }`}
            >
              {rank != null && (
                <span
                  className={`absolute top-1/2 h-2 w-2 -translate-x-1/2 -translate-y-1/2 rounded-full ${dotClass(
                    rank,
                  )}`}
                  // Clamp so rank=0/100 dots don't render half outside the bar.
                  style={{ left: `${Math.max(2, Math.min(98, rank))}%` }}
                />
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

