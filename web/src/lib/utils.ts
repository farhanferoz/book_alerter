import { clsx, type ClassValue } from "clsx"
import { twMerge } from "tailwind-merge"

import { ApiError } from "@/api/client"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function navLinkClass({ isActive }: { isActive: boolean }): string {
  return cn(
    "rounded-md px-3 py-1.5 text-sm font-medium transition-colors",
    isActive
      ? "bg-muted text-foreground"
      : "text-muted-foreground hover:bg-muted hover:text-foreground",
  )
}

// Render a user-facing string from an `unknown` thrown value. Used by the
// dashboard + book-detail error cards (where the React Query `error` field is
// typed `unknown` unless the hook narrows it).
export function formatErrorMessage(error: unknown): string {
  if (error instanceof ApiError) return `${error.status} — ${error.message}`
  if (error instanceof Error) return error.message
  return String(error)
}
