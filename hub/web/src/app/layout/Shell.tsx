import { Link } from "react-router-dom";
import { Settings } from "lucide-react";
import { OfflineBanner } from "./OfflineBanner";
import { BottomNav } from "./BottomNav";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import { ConfirmBell } from "./ConfirmBell";

interface ShellProps {
  children: React.ReactNode;
}

export function Shell({ children }: ShellProps) {
  return (
    <>
      {/* Desktop/tablet layout: sidebar + topbar */}
      <div className="hidden sm:block">
        <Sidebar />
        <TopBar />
        <div
          className="min-h-screen"
          style={{ marginLeft: "var(--sidebar-w)", paddingTop: "var(--topbar-h)" }}
        >
          <OfflineBanner />
          <main className="max-w-5xl mx-auto px-6 py-6 animate-fade-in">{children}</main>
        </div>
      </div>

      {/* Mobile layout: content + floating bottom nav */}
      <div className="sm:hidden flex flex-col min-h-screen">
        <OfflineBanner />
        {/* Pending-confirmations bell (TopBar is desktop-only) */}
        <div
          className="fixed top-2 right-2 z-30 flex items-center gap-1 rounded-lg bg-[color:var(--card)]/80 backdrop-blur"
          style={{ paddingTop: "max(0px, env(safe-area-inset-top))" }}
        >
          <ConfirmBell />
          <Link
            to="/more"
            aria-label="Налаштування"
            className="p-2 rounded-lg text-[color:var(--text-muted)] hover:text-[color:var(--text)] hover:bg-[color:var(--raised)] transition-colors"
          >
            <Settings size={18} />
          </Link>
        </div>
        <main className="flex-1 px-4 py-4 pb-28 overflow-y-auto animate-fade-in">{children}</main>
        <BottomNav />
      </div>
    </>
  );
}
