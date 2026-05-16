import { useEffect, useState } from "react";
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
  // Read the current theme via `useIsDark` so the button label flips when
  // any code mutates `<html>.classList` (e.g. another tab synced via storage).
  const isDark = useIsDark();
  const [theme, setTheme] = useState<Theme>(isDark ? "dark" : "light");

  useEffect(() => {
    applyTheme(theme);
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
      onClick={() => setTheme((t) => (t === "dark" ? "light" : "dark"))}
      aria-label="Toggle dark mode"
    >
      {theme === "dark" ? "Light" : "Dark"} mode
    </Button>
  );
}
