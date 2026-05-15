// Book cover renderer with a lucide BookIcon fallback when no URL is present.
//
// Centralises the four blank-rect sites that previously rendered `bg-muted`
// placeholders (dashboard rows, book detail header, AddBookModal lookup
// preview, AddBookModal search hits). When OL/GB have no cover registered
// for an ISBN — common for niche/new titles — this keeps the UI legible
// instead of showing a confusing empty rectangle.

import { BookIcon } from "lucide-react";

import { cn } from "@/lib/utils";

export function CoverImage({
  src,
  alt = "",
  className,
}: {
  src: string | null | undefined;
  alt?: string;
  className?: string;
}) {
  if (src) {
    return (
      <img
        src={src}
        alt={alt}
        className={cn("object-cover", className)}
        loading="lazy"
      />
    );
  }
  return (
    <div
      className={cn(
        "flex items-center justify-center bg-muted text-muted-foreground/60",
        className,
      )}
      aria-hidden
    >
      <BookIcon className="size-1/2" />
    </div>
  );
}
