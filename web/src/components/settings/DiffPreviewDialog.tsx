// Generic diff-preview dialog used before any settings write.
//
// Accepts a precomputed `diff: DiffRow[]` so the caller controls how to
// represent old/new values (strings, JSON, etc.) — keeps the dialog free of
// any per-field knowledge. Phase 11.3 (recommendation) + 11.5 (advanced YAML)
// will reuse the same shape.
//
// The dialog stays open on a failed save so the user can either retry (the
// save button calls `onConfirm` again) or cancel. The caller controls the
// `isPending` / `errorMessage` props.

import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

export type DiffRow = {
  field: string;
  oldValue: string;
  newValue: string;
};

export type DiffPreviewDialogProps = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title?: string;
  description?: string;
  diff: DiffRow[];
  onConfirm: () => void;
  isPending?: boolean;
  errorMessage?: string | null;
};

export function DiffPreviewDialog({
  open,
  onOpenChange,
  title = "Confirm changes",
  description = "Review the changes below before saving.",
  diff,
  onConfirm,
  isPending = false,
  errorMessage = null,
}: DiffPreviewDialogProps) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription>{description}</DialogDescription>
        </DialogHeader>

        {diff.length === 0 ? (
          <p className="text-sm text-muted-foreground">No changes to apply.</p>
        ) : (
          <ul className="space-y-2 text-sm">
            {diff.map((row) => (
              <li
                key={row.field}
                className="rounded-md border border-border bg-muted/30 p-2"
              >
                <div className="font-mono text-xs font-medium">{row.field}</div>
                <div className="mt-1 grid grid-cols-[auto_1fr] gap-x-2 text-xs">
                  <span className="text-muted-foreground">old:</span>
                  <span className="font-mono">{row.oldValue}</span>
                  <span className="text-muted-foreground">new:</span>
                  <span className="font-mono">{row.newValue}</span>
                </div>
              </li>
            ))}
          </ul>
        )}

        {errorMessage && (
          <p className="text-xs text-destructive">{errorMessage}</p>
        )}

        <DialogFooter>
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={isPending}
          >
            Cancel
          </Button>
          <Button
            type="button"
            onClick={onConfirm}
            disabled={isPending || diff.length === 0}
          >
            {isPending ? "Saving…" : "Save"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
