import { Routes, Route, NavLink } from "react-router-dom";
import EventsFeed from "./pages/EventsFeed";
import Devices from "./pages/Devices";
import Settings from "./pages/Settings";
import { useOnlineStatus } from "./hooks/useOnlineStatus";

export default function App() {
  const isOnline = useOnlineStatus();

  return (
    <div className="min-h-screen flex flex-col">
      {!isOnline && (
        <div className="bg-amber-600 text-white text-center text-sm py-1 px-4">
          Офлайн — показуються кешовані дані
        </div>
      )}
      <nav className="bg-slate-800 border-b border-slate-700">
        <div className="max-w-4xl mx-auto px-4 flex gap-6 h-14 items-center">
          <span className="font-bold text-blue-400">IoT Hub</span>
          <NavLink
            to="/"
            className={({ isActive }) =>
              isActive ? "text-white font-medium" : "text-slate-400 hover:text-white"
            }
          >
            Події
          </NavLink>
          <NavLink
            to="/devices"
            className={({ isActive }) =>
              isActive ? "text-white font-medium" : "text-slate-400 hover:text-white"
            }
          >
            Пристрої
          </NavLink>
          <NavLink
            to="/settings"
            className={({ isActive }) =>
              isActive ? "text-white font-medium" : "text-slate-400 hover:text-white"
            }
          >
            Налаштування
          </NavLink>
        </div>
      </nav>
      <main className="flex-1 max-w-4xl mx-auto w-full px-4 py-6">
        <Routes>
          <Route path="/" element={<EventsFeed />} />
          <Route path="/devices" element={<Devices />} />
          <Route path="/settings" element={<Settings />} />
        </Routes>
      </main>
    </div>
  );
}
