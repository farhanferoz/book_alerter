// Small colour-coded pill for the alert `kind` enum.
//
// Inline Tailwind classes (no shadcn Badge primitive) — matches the pattern
// used for `SignalPill` and the status pill in `HeaderCard`. Shared label +
// palette live in `lib/alert-kind.ts` so this module exports a component
// only (react-refresh rule).

import { ALERT_KIND_LABEL, ALERT_KIND_PILL_CLASS, type AlertKind } from "@/lib/alert-kind";

export function AlertKindBadge({ kind }: { kind: AlertKind }) {
  return (
    <span
      className={`inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${ALERT_KIND_PILL_CLASS[kind]}`}
    >
      {ALERT_KIND_LABEL[kind]}
    </span>
  );
}
