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

export function minorToPoundsInput(minor: number | null | undefined): string {
  if (minor == null) return "";
  // Two decimals; matches how `formatMoneyMinor` would render but without the
  // currency symbol / grouping commas (those break <input type="number">).
  return (minor / 100).toFixed(2);
}
