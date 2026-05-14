// useSavedFlash — transient "Saved at HH:MM:SS" indicator used after a
// successful settings PUT. Calling `flash()` records the current local time
// and clears it after `durationMs` (default 3 s). The cleanup guards against
// races where another save fires before the timeout expires by only nulling
// the entry that matches the value we set.
//
// Phase 11.3 (Recommendation), 11.4 (Notifications), and 11.5 (Advanced) all
// open-coded the same pattern; this hook is the lift.

import { useState } from "react";

const DEFAULT_FLASH_MS = 3000;

export type UseSavedFlash = {
  savedAt: string | null;
  flash: () => void;
};

export function useSavedFlash(durationMs: number = DEFAULT_FLASH_MS): UseSavedFlash {
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const flash = () => {
    const now = new Date().toLocaleTimeString();
    setSavedAt(now);
    setTimeout(() => {
      setSavedAt((prev) => (prev === now ? null : prev));
    }, durationMs);
  };
  return { savedAt, flash };
}
