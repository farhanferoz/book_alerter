import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";

import { AppShell } from "@/layout/AppShell";
import { Skeleton } from "@/components/ui/skeleton";
import { Alerts } from "@/pages/Alerts";
import { Dashboard } from "@/pages/Dashboard";
import { ProductsDashboard } from "@/pages/ProductsDashboard";
import { ProductDetail } from "@/pages/ProductDetail";
import { Settings } from "@/pages/Settings";
import { SettingsSources } from "@/pages/settings/Sources";
import { SettingsRecommendation } from "@/pages/settings/Recommendation";
import { SettingsNotifications } from "@/pages/settings/Notifications";

// BookDetail pulls Recharts, which is heavy (~300 KB raw). Lazy-loading
// keeps it off the dashboard's critical path.
const BookDetail = lazy(() =>
  import("@/pages/BookDetail").then((m) => ({ default: m.BookDetail })),
);

// SettingsAdvanced pulls Monaco editor (~2 MB raw). Route-split so the
// editor only loads when the user actually visits /settings/advanced —
// same pattern as BookDetail above.
const SettingsAdvanced = lazy(() =>
  import("@/pages/settings/Advanced").then((m) => ({
    default: m.SettingsAdvanced,
  })),
);

function RouteSpinner() {
  return <Skeleton className="h-32" aria-hidden />;
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
        <Route path="products" element={<ProductsDashboard />} />
        <Route path="products/:id" element={<ProductDetail />} />
        <Route path="alerts" element={<Alerts />} />
        <Route path="settings" element={<Settings />}>
          <Route index element={<Navigate replace to="/settings/sources" />} />
          <Route path="sources" element={<SettingsSources />} />
          <Route path="recommendation" element={<SettingsRecommendation />} />
          <Route path="notifications" element={<SettingsNotifications />} />
          <Route
            path="advanced"
            element={
              <Suspense fallback={<RouteSpinner />}>
                <SettingsAdvanced />
              </Suspense>
            }
          />
        </Route>
      </Route>
    </Routes>
  );
}

export default App;
