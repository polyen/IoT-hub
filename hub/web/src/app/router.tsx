import { lazy, Suspense } from "react";
import { Routes, Route, Navigate, Outlet } from "react-router-dom";
import { Shell } from "./layout/Shell";
import { Spinner } from "../components/Spinner";

const HomePage = lazy(() => import("../pages/home/HomePage"));
const RoomsPage = lazy(() => import("../pages/rooms/RoomsPage"));
const ScenesPage = lazy(() => import("../pages/scenes/ScenesPage"));
const WallPage = lazy(() => import("../pages/wall/WallPage"));
const CamerasPage = lazy(() => import("../pages/cameras/CamerasPage"));
const AssistantPage = lazy(() => import("../pages/assistant/AssistantPage"));
const ConfirmPage = lazy(() => import("../pages/confirm/ConfirmPage"));
const MoreIndex = lazy(() => import("../pages/more/MoreIndex"));
const EventsPage = lazy(() => import("../pages/more/EventsPage"));
const DigestPage = lazy(() => import("../pages/more/DigestPage"));
const ClimatePage = lazy(() => import("../pages/more/ClimatePage"));
const DevicesListPage = lazy(() => import("../pages/more/DevicesListPage"));
const SecurityPage = lazy(() => import("../pages/more/SecurityPage"));
const SystemPage = lazy(() => import("../pages/more/SystemPage"));
const PolicyPage = lazy(() => import("../pages/more/PolicyPage"));
const PrivacyPage = lazy(() => import("../pages/more/PrivacyPage"));
const SettingsPage = lazy(() => import("../pages/more/SettingsPage"));
const ModelsPage = lazy(() => import("../pages/more/ModelsPage"));

function PageLoader() {
  return (
    <div className="flex items-center justify-center min-h-[200px]">
      <Spinner className="h-8 w-8" />
    </div>
  );
}

/** Wraps the standard app chrome (sidebar / topbar / bottom-nav) around routed pages. */
function ShellLayout() {
  return (
    <Shell>
      <Outlet />
    </Shell>
  );
}

export function AppRouter() {
  return (
    <Suspense fallback={<PageLoader />}>
      <Routes>
        {/* Full-screen wall-mounted kiosk — no app chrome */}
        <Route path="/wall" element={<WallPage />} />

        {/* Default: open wall/kiosk mode */}
        <Route path="/" element={<Navigate to="/wall" replace />} />

        <Route element={<ShellLayout />}>
          <Route path="/home" element={<HomePage />} />
          <Route path="/rooms" element={<RoomsPage />} />
          <Route path="/scenes" element={<ScenesPage />} />
          <Route path="/cameras" element={<CamerasPage />} />
          <Route path="/assistant" element={<AssistantPage />} />
          <Route path="/events" element={<EventsPage />} />
          <Route path="/confirm" element={<ConfirmPage />} />
          <Route path="/more" element={<MoreIndex />} />
          <Route path="/more/digest" element={<DigestPage />} />
          <Route path="/more/climate" element={<ClimatePage />} />
          <Route path="/more/devices" element={<DevicesListPage />} />
          <Route path="/more/security" element={<SecurityPage />} />
          <Route path="/more/policy" element={<PolicyPage />} />
          <Route path="/more/privacy" element={<PrivacyPage />} />
          <Route path="/more/settings" element={<SettingsPage />} />
          <Route path="/more/system" element={<SystemPage />} />
          <Route path="/more/models" element={<ModelsPage />} />

          {/* Legacy redirects (kept so old bookmarks / PWA cache don't 404) */}
          <Route path="/voice" element={<Navigate to="/assistant" replace />} />
          <Route path="/more/scenarios" element={<Navigate to="/assistant?tab=scenarios" replace />} />
          <Route path="/more/events" element={<Navigate to="/events" replace />} />
          <Route path="/more/about" element={<Navigate to="/more/settings" replace />} />
        </Route>
      </Routes>
    </Suspense>
  );
}
