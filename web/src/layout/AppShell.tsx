import { useEffect, useState } from "react";
import { NavLink, Outlet } from "react-router-dom";

import { AlertsSidebar } from "@/components/alerts/AlertsSidebar";
import { Button } from "@/components/ui/button";
import { ThemeToggle } from "@/layout/ThemeToggle";
import { apiGet, ApiError } from "@/api/client";
import { cn, navLinkClass } from "@/lib/utils";

const SIDEBAR_STORAGE_KEY = "book-alerter:alerts-sidebar-open";

type HealthState =
  | { status: "loading" }
  | { status: "ok" }
  | { status: "error"; message: string };

function readSidebarInitial(): boolean {
  try {
    const v = localStorage.getItem(SIDEBAR_STORAGE_KEY);
    if (v === null) return true;
    return v === "1";
  } catch {
    return true;
  }
}

function BackendHealthBadge() {
  const [state, setState] = useState<HealthState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    apiGet("/api/health")
      .then(() => {
        if (!cancelled) setState({ status: "ok" });
      })
      .catch((err: unknown) => {
        if (!cancelled) {
          const message =
            err instanceof ApiError
              ? err.message
              : err instanceof Error
                ? err.message
                : String(err);
          setState({ status: "error", message });
        }
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const tone =
    state.status === "ok"
      ? "text-emerald-600 dark:text-emerald-400"
      : state.status === "error"
        ? "text-destructive"
        : "text-muted-foreground";
  const label =
    state.status === "ok"
      ? "Backend: OK"
      : state.status === "error"
        ? "Backend: ERR"
        : "Backend: …";

  return (
    <span
      title={state.status === "error" ? state.message : undefined}
      className={cn("text-xs font-medium", tone)}
    >
      {label}
    </span>
  );
}

export function AppShell() {
  const [sidebarOpen, setSidebarOpen] = useState<boolean>(readSidebarInitial);

  useEffect(() => {
    try {
      localStorage.setItem(SIDEBAR_STORAGE_KEY, sidebarOpen ? "1" : "0");
    } catch {
      /* ignore */
    }
  }, [sidebarOpen]);

  return (
    <div className="min-h-svh bg-background text-foreground flex flex-col">
      <header className="border-b border-border">
        <div className="flex h-12 items-center gap-4 px-4">
          <span className="text-sm font-semibold tracking-tight">Book Alerter</span>
          <nav className="flex items-center gap-1">
            <NavLink to="/" end className={navLinkClass}>
              Dashboard
            </NavLink>
            <NavLink to="/alerts" className={navLinkClass}>
              Alerts
            </NavLink>
            <NavLink to="/settings" className={navLinkClass}>
              Settings
            </NavLink>
          </nav>
          <div className="ml-auto flex items-center gap-3">
            <BackendHealthBadge />
            <ThemeToggle />
            <Button
              variant="outline"
              size="sm"
              onClick={() => setSidebarOpen((v) => !v)}
              aria-label="Toggle alerts sidebar"
              aria-expanded={sidebarOpen}
            >
              {sidebarOpen ? "Hide alerts" : "Show alerts"}
            </Button>
          </div>
        </div>
      </header>

      <div className="flex flex-1 min-h-0">
        <main className="flex-1 p-6 overflow-auto">
          <Outlet />
        </main>
        {sidebarOpen && (
          <aside
            aria-label="Alerts sidebar"
            className="w-80 shrink-0 border-l border-border bg-sidebar text-sidebar-foreground p-4 overflow-hidden flex"
          >
            <div className="flex-1 min-w-0">
              <AlertsSidebar />
            </div>
          </aside>
        )}
      </div>
    </div>
  );
}
