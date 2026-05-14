// Minimal pulse-placeholder primitive used by every page's loading state.
//
// 16 loading sites in Phase 10 all rendered the same `animate-pulse rounded[-md]
// bg-muted/{40,60}` div with a per-site height/width class; this component
// keeps the base classes in one place. Defaults to `rounded-md bg-muted/40`;
// override either via `tone="muted"` (→ `bg-muted/60`) or any class through
// `className` (twMerge resolves conflicts — e.g. `rounded` overrides
// `rounded-md`).

import { cn } from "@/lib/utils";

type Tone = "default" | "muted";

const TONE_CLASS: Record<Tone, string> = {
  default: "bg-muted/40",
  muted: "bg-muted/60",
};

export interface SkeletonProps extends React.HTMLAttributes<HTMLDivElement> {
  tone?: Tone;
}

export function Skeleton({
  className,
  tone = "default",
  ...rest
}: SkeletonProps) {
  return (
    <div
      className={cn("animate-pulse rounded-md", TONE_CLASS[tone], className)}
      {...rest}
    />
  );
}
