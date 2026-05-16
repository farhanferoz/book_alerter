// Subscribe to the global `<html>.dark` class so any component (Monaco
// theme, charts, etc.) can react to the dark-mode toggle without a page
// reload. The ThemeToggle component owns the source of truth — flipping
// the class — and this hook is the read side.

import { useEffect, useState } from "react";

function readInitialDark(): boolean {
  return (
    typeof document !== "undefined" &&
    document.documentElement.classList.contains("dark")
  );
}

export function useIsDark(): boolean {
  const [isDark, setIsDark] = useState<boolean>(readInitialDark);
  useEffect(() => {
    const root = document.documentElement;
    const observer = new MutationObserver(() => {
      setIsDark(root.classList.contains("dark"));
    });
    observer.observe(root, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);
  return isDark;
}
