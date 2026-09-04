// Small formatting helpers for money (minor units) and relative time.
//
// Backend stores money as integer minor units (pence) — see CHANGELOG /
// design spec. We never round-trip through floats; we divide once at the
// edge, in the formatter, with `Intl.NumberFormat`'s built-in grouping.
//
// Relative time uses `Intl.RelativeTimeFormat` with a fixed unit ladder.
// No date library — date-fns / Day.js would add weight for one helper.

const GBP_FORMATTER = new Intl.NumberFormat("en-GB", {
  style: "currency",
  currency: "GBP",
});

const RELATIVE_FORMATTER = new Intl.RelativeTimeFormat("en", {
  numeric: "auto",
});

export function formatMoneyMinor(
  minor: number | null | undefined,
  currency: string = "GBP",
): string {
  if (minor == null) return "—";
  if (currency !== "GBP") {
    // Fallback for non-GBP currencies (none at MVP, but the model carries it).
    return new Intl.NumberFormat("en-GB", {
      style: "currency",
      currency,
    }).format(minor / 100);
  }
  return GBP_FORMATTER.format(minor / 100);
}

const RELATIVE_LADDER: ReadonlyArray<[Intl.RelativeTimeFormatUnit, number]> = [
  ["year", 60 * 60 * 24 * 365],
  ["month", 60 * 60 * 24 * 30],
  ["day", 60 * 60 * 24],
  ["hour", 60 * 60],
  ["minute", 60],
  ["second", 1],
];

export function formatRelativeTime(
  iso: string | null | undefined,
  now: Date = new Date(),
): string {
  if (!iso) return "—";
  const then = new Date(iso);
  const diffSeconds = (then.getTime() - now.getTime()) / 1000;
  for (const [unit, secondsPerUnit] of RELATIVE_LADDER) {
    if (Math.abs(diffSeconds) >= secondsPerUnit || unit === "second") {
      const value = Math.round(diffSeconds / secondsPerUnit);
      return RELATIVE_FORMATTER.format(value, unit);
    }
  }
  return RELATIVE_FORMATTER.format(0, "second");
}

// Absolute timestamp in the user's locale; backend ships UTC ISO strings.
const DATETIME_FORMATTER = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  return DATETIME_FORMATTER.format(new Date(iso));
}

// GBP minor units (pence) ↔ display pounds. Used in the settings panel where
// the user enters whole pounds; the backend stores minor.
export function poundsToMinor(pounds: string): number | null {
  const trimmed = pounds.trim();
  if (trimmed === "") return null;
  const value = Number(trimmed);
  if (!Number.isFinite(value) || value < 0) return null;
  return Math.round(value * 100);
}

// Bookfinder is an aggregator that resolves to whichever marketplace
// (EBAY, BIBLIO_ES, ABEBOOKS, ...) actually fulfils the order. The seller
// string carries the affiliate prefix — surface that as the user-facing
// source label so people see "EBAY", not "BOOKFINDER", as the place
// they'd buy from.
const BOOKFINDER_AFFILIATE_RE = /^([A-Z][A-Z0-9_]+)(?:\s|$)/;

export function displaySourceLabel(
  source: string | null | undefined,
  seller: string | null | undefined,
): string {
  if (!source) return "—";
  if (source === "bookfinder" && seller) {
    const m = BOOKFINDER_AFFILIATE_RE.exec(seller);
    if (m) return m[1];
  }
  return source.toUpperCase();
}

export function isBookfinderSourcedLabel(
  source: string | null | undefined,
): boolean {
  return source === "bookfinder";
}

// Prose-friendly source name for banners/legends (contrast with
// `displaySourceLabel` above, which uppercases the raw key for compact table
// cells). `amazon` and `amazon_uk_product` never co-occur ON ONE ITEM, so
// collapsing both to "Amazon" is safe here -- but that guarantee is
// per-item only. A caller that iterates over SOURCES rather than one
// item's own observations (e.g. a scrape-health banner listing every
// challenged source) can see both in the same listing, indistinguishable
// under this collapse -- use `sourceListingLabel` below for that case.
// Unknown sources fall back to the raw key.
const SOURCE_DISPLAY_NAME: Record<string, string> = {
  amazon: "Amazon",
  amazon_uk_product: "Amazon",
  wob: "World of Books",
  bookfinder: "eBay (BookFinder)",
  keepa: "Keepa",
};

export function sourceDisplayName(source: string): string {
  return SOURCE_DISPLAY_NAME[source] ?? source;
}

// F8: distinguishing label for a SOURCE-LISTING context (the scrape-health
// banner) rather than a per-item history legend -- `amazon` and
// `amazon_uk_product` are both configured (`config.py`'s
// `_default_sources`) and can both appear challenged in the same listing,
// where `sourceDisplayName`'s collapse to plain "Amazon" would print two
// lines differing only in their numbers with no way to tell the book
// scraper from the product scraper.
const SOURCE_LISTING_LABEL: Record<string, string> = {
  amazon: "Amazon (books)",
  amazon_uk_product: "Amazon (products)",
  wob: "World of Books",
  bookfinder: "eBay (BookFinder)",
  keepa: "Keepa",
};

export function sourceListingLabel(source: string): string {
  return SOURCE_LISTING_LABEL[source] ?? source;
}

// Which item kind a scraper source targets, mirroring `_default_sources()`
// in `config.py` (`amazon_uk_product` is the only source configured
// `item_kinds=[ItemKind.PRODUCT]`; the rest default to books). Not on the
// wire -- `SourceConfigOut` omits `item_kinds` -- so this is a small,
// closed, frontend-side mirror kept only for scoping the scrape-health
// banner to the dashboard it's actually relevant to (F8): without it,
// `amazon_uk_product` being blocked would warn on the books page and stay
// silent on the products page whose data it governs, or vice versa. Update
// this mapping if a source's configured `item_kinds` ever changes.
const SOURCE_ITEM_KIND: Record<string, "book" | "product"> = {
  wob: "book",
  bookfinder: "book",
  amazon: "book",
  amazon_uk_product: "product",
};

export function sourceTargetsKind(
  source: string,
  kind: "book" | "product",
): boolean {
  return (SOURCE_ITEM_KIND[source] ?? "book") === kind;
}

export function formatCondition(
  condition: string | null | undefined,
): string {
  if (!condition) return "—";
  return condition.replace(/_/g, " ");
}

// Compact shipping descriptor for tables: "—" (unknown), "free" (zero),
// or "+£X.XX" (paid). For the prose snapshot variant, format the value
// directly with `formatMoneyMinor`.
export function formatShippingMinor(
  minor: number | null | undefined,
  currency: string = "GBP",
): string {
  if (minor == null) return "—";
  if (minor === 0) return "free";
  return `+${formatMoneyMinor(minor, currency)}`;
}

// Renders "~+£X.XX*" when actual shipping is unknown but the cascade
// produced an estimate; falls through to formatShippingMinor otherwise.
// "*" marks the value as imputed (matches the SignalCard tooltip).
export function formatShippingMinorWithEstimate(
  observed: number | null | undefined,
  estimate: number | null | undefined,
  currency: string = "GBP",
): string {
  if (observed != null) return formatShippingMinor(observed, currency);
  if (estimate == null || estimate === 0) return formatShippingMinor(observed, currency);
  return `~+${formatMoneyMinor(estimate, currency)}*`;
}

export function ordinalSuffix(n: number): string {
  const tens = n % 100;
  if (tens >= 11 && tens <= 13) return "th";
  switch (n % 10) {
    case 1:
      return "st";
    case 2:
      return "nd";
    case 3:
      return "rd";
    default:
      return "th";
  }
}

export function minorToPoundsInput(minor: number | null | undefined): string {
  if (minor == null) return "";
  // Two decimals; matches how `formatMoneyMinor` would render but without the
  // currency symbol / grouping commas (those break <input type="number">).
  return (minor / 100).toFixed(2);
}
