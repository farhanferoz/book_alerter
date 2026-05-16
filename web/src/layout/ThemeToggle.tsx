import { useEffect } from "react";
import { Button } from "@/components/ui/button";
import { useIsDark } from "@/hooks/useIsDark";

const STORAGE_KEY = "book-alerter:theme";

type Theme = "light" | "dark";

function applyTheme(theme: Theme): void {
  const root = document.documentElement;
  if (theme === "dark") {
    root.classList.add("dark");
  } else {
    root.classList.remove("dark");
  }
}

export function ThemeToggle() {
  // Single source of truth — derive both the visible label and the next
  // toggle target from `useIsDark()` so external mutations to <html>.dark
  // (other tabs, devtools) can't desync the button label from the DOM.
  const isDark = useIsDark();
  const theme: Theme = isDark ? "dark" : "light";

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, theme);
    } catch {
      /* ignore */
    }
  }, [theme]);

  return (
    <Button
      variant="outline"
      size="sm"
      onClick={() => applyTheme(isDark ? "light" : "dark")}
      aria-label="Toggle dark mode"
    >
      {isDark ? "Light" : "Dark"} mode
    </Button>
  );
}
