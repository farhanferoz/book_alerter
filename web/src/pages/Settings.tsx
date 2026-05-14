// Settings shell — top-tab nav + nested `<Outlet />` for each pane.
//
// Tabs land progressively in Phases 11.2 → 11.5: Sources (11.2), Recommendation
// (11.3), Notifications (11.4), Advanced (11.5). `/settings` redirects to
// `/settings/sources` via an index `<Navigate replace />` route declared in
// `App.tsx`. The non-active panes are intentionally trivial — each phase will
// rewrite its own placeholder.

import { NavLink, Outlet } from "react-router-dom";

import { navLinkClass } from "@/lib/utils";

export function Settings() {
  return (
    <section className="space-y-4">
      <header>
        <h1 className="text-xl font-semibold">Settings</h1>
        <p className="text-sm text-muted-foreground">
          Configure sources, recommendations, notifications, and the raw config.
        </p>
      </header>
      <nav className="flex flex-wrap gap-1 border-b border-border pb-2">
        <NavLink to="/settings/sources" className={navLinkClass}>
          Sources
        </NavLink>
        <NavLink to="/settings/recommendation" className={navLinkClass}>
          Recommendation
        </NavLink>
        <NavLink to="/settings/notifications" className={navLinkClass}>
          Notifications
        </NavLink>
        <NavLink to="/settings/advanced" className={navLinkClass}>
          Advanced
        </NavLink>
      </nav>
      <div>
        <Outlet />
      </div>
    </section>
  );
}
