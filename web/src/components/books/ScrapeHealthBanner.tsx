// Scrape-health banner (T6.1). Shared by the books `Dashboard` and
// `ProductsDashboard` (F8) -- it used to be books-only private JSX, so an
// `amazon_uk_product` block warned on the wrong page and stayed silent on
// the products page whose data it actually governs. Lives under
// `components/books/` alongside `columns.tsx`, which generalised to both
// dashboards the same way: kind-neutral logic extracted from the page it
// was first written for, not a literal per-kind duplicate.
//
// `last_24h.challenged` is a rolled-up proxy -- see `SourceHealthOut` on
// the backend -- so it's worded as "in the last 24 hours" (the actual
// window the number covers, possibly several runs) rather than "in the
// last run", and the caveat line makes clear it isn't an exact bot-block
// count. Fails silently (renders nothing) while loading or on error --
// this is a supplementary heads-up, not the page's primary data.
//
// `scope` filters which sources this instance cares about: `amazon_uk_product`
// is the only source configured for products by default (`config.py`'s
// `_default_sources`); everything else defaults to books. Without the
// filter, both dashboards would show every challenged source and the split
// would be cosmetic only.

import { useSources } from "@/hooks/useSources";
import { sourceListingLabel, sourceTargetsKind } from "@/lib/format";

export function ScrapeHealthBanner({ scope }: { scope: "book" | "product" }) {
  const { data } = useSources();
  const challenged = (data ?? []).filter(
    (s) => s.last_24h.challenged > 0 && sourceTargetsKind(s.name, scope),
  );
  if (challenged.length === 0) return null;

  return (
    <div className="rounded-md border border-amber-500/40 bg-amber-50 p-3 text-sm text-amber-800 dark:bg-amber-900/20 dark:text-amber-300">
      {challenged.map((s) => (
        <p key={s.name}>
          {sourceListingLabel(s.name)} blocked {s.last_24h.challenged} of{" "}
          {s.last_24h.attempted} items in the last 24 hours.
        </p>
      ))}
      <p className="mt-1 text-xs opacity-80">
        Counts items still blocked after a retry. Runs from before this
        counter existed report zero, so the first day may under-report.
      </p>
    </div>
  );
}
