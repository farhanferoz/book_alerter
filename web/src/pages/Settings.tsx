import { NavLink, Outlet } from "react-router-dom";

import { navLinkClass } from "@/lib/utils";

export function Settings() {
  return (
    <section className="space-y-4">
      <header>
        <h1 className="text-xl font-semibold">Settings</h1>
        <p className="text-sm text-muted-foreground">
          Configuration panes will be wired up in Phase 11.
        </p>
      </header>
      <nav className="flex flex-wrap gap-1 border-b border-border pb-2">
        <NavLink to="/settings" end className={navLinkClass}>
          Overview
        </NavLink>
        <NavLink to="/settings/sources" className={navLinkClass}>
          Sources
        </NavLink>
        <NavLink to="/settings/notifications" className={navLinkClass}>
          Notifications
        </NavLink>
        <NavLink to="/settings/config" className={navLinkClass}>
          Config (YAML)
        </NavLink>
      </nav>
      <div>
        <Outlet />
      </div>
    </section>
  );
}

export function SettingsOverview() {
  return (
    <p className="text-sm text-muted-foreground">
      Pick a sub-section above. Detailed panes land in Phase 11.
    </p>
  );
}

export function SettingsSources() {
  return (
    <p className="text-sm text-muted-foreground">
      Source toggles + per-source overrides (Phase 11).
    </p>
  );
}

export function SettingsNotifications() {
  return (
    <p className="text-sm text-muted-foreground">
      Channel configuration and test buttons (Phase 11).
    </p>
  );
}

export function SettingsConfig() {
  return (
    <p className="text-sm text-muted-foreground">
      Monaco-based YAML editor for the full config (Phase 11).
    </p>
  );
}
