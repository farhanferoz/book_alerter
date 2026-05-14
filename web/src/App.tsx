import { lazy, Suspense } from "react";
import { Route, Routes } from "react-router-dom";

import { AppShell } from "@/layout/AppShell";
import { Alerts } from "@/pages/Alerts";
import { Dashboard } from "@/pages/Dashboard";
import {
  Settings,
  SettingsConfig,
  SettingsNotifications,
  SettingsOverview,
  SettingsSources,
} from "@/pages/Settings";

// BookDetail pulls Recharts, which is heavy (~300 KB raw). Lazy-loading
// keeps it off the dashboard's critical path.
const BookDetail = lazy(() =>
  import("@/pages/BookDetail").then((m) => ({ default: m.BookDetail })),
);

function RouteSpinner() {
  return (
    <div className="h-32 animate-pulse rounded-md bg-muted/40" aria-hidden />
  );
}

function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Dashboard />} />
        <Route
          path="books/:id"
          element={
            <Suspense fallback={<RouteSpinner />}>
              <BookDetail />
            </Suspense>
          }
        />
        <Route path="alerts" element={<Alerts />} />
        <Route path="settings" element={<Settings />}>
          <Route index element={<SettingsOverview />} />
          <Route path="sources" element={<SettingsSources />} />
          <Route path="notifications" element={<SettingsNotifications />} />
          <Route path="config" element={<SettingsConfig />} />
        </Route>
      </Route>
    </Routes>
  );
}

export default App;
