import { lazy, Suspense } from "react";
import { Routes, Route } from "react-router-dom";
import { Shell } from "./layout/Shell";
import { Spinner } from "../components/Spinner";

const HomePage = lazy(() => import("../pages/home/HomePage"));
const CamerasPage = lazy(() => import("../pages/cameras/CamerasPage"));
const VoicePage = lazy(() => import("../pages/voice/VoicePage"));
const ConfirmPage = lazy(() => import("../pages/confirm/ConfirmPage"));
const MoreIndex = lazy(() => import("../pages/more/MoreIndex"));
const EventsPage = lazy(() => import("../pages/more/EventsPage"));
const ScenariosPage = lazy(() => import("../pages/more/ScenariosPage"));
const DigestPage = lazy(() => import("../pages/more/DigestPage"));
const DevicesListPage = lazy(() => import("../pages/more/DevicesListPage"));
const SecurityPage = lazy(() => import("../pages/more/SecurityPage"));
const SystemPage = lazy(() => import("../pages/more/SystemPage"));
const PolicyPage = lazy(() => import("../pages/more/PolicyPage"));
const PrivacyPage = lazy(() => import("../pages/more/PrivacyPage"));
const SettingsPage = lazy(() => import("../pages/more/SettingsPage"));
const AboutPage = lazy(() => import("../pages/more/AboutPage"));
const ModelsPage = lazy(() => import("../pages/more/ModelsPage"));

function PageLoader() {
  return (
    <div className="flex items-center justify-center min-h-[200px]">
      <Spinner className="h-8 w-8" />
    </div>
  );
}

export function AppRouter() {
  return (
    <Shell>
      <Suspense fallback={<PageLoader />}>
        <Routes>
          <Route path="/" element={<HomePage />} />
          <Route path="/cameras" element={<CamerasPage />} />
          <Route path="/voice" element={<VoicePage />} />
          <Route path="/confirm" element={<ConfirmPage />} />
          <Route path="/more" element={<MoreIndex />} />
          <Route path="/more/events" element={<EventsPage />} />
          <Route path="/more/scenarios" element={<ScenariosPage />} />
          <Route path="/more/digest" element={<DigestPage />} />
          <Route path="/more/devices" element={<DevicesListPage />} />
          <Route path="/more/security" element={<SecurityPage />} />
          <Route path="/more/system" element={<SystemPage />} />
          <Route path="/more/policy" element={<PolicyPage />} />
          <Route path="/more/privacy" element={<PrivacyPage />} />
          <Route path="/more/settings" element={<SettingsPage />} />
          <Route path="/more/about" element={<AboutPage />} />
          <Route path="/more/models" element={<ModelsPage />} />
        </Routes>
      </Suspense>
    </Shell>
  );
}
