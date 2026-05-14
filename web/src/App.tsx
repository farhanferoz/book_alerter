import { Route, Routes } from "react-router-dom";

import { AppShell } from "@/layout/AppShell";
import { Alerts } from "@/pages/Alerts";
import { BookDetail } from "@/pages/BookDetail";
import { Dashboard } from "@/pages/Dashboard";
import {
  Settings,
  SettingsConfig,
  SettingsNotifications,
  SettingsOverview,
  SettingsSources,
} from "@/pages/Settings";

function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Dashboard />} />
        <Route path="books/:id" element={<BookDetail />} />
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
