import { OfflineBanner } from "./OfflineBanner";
import { BottomNav } from "./BottomNav";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";

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
        <main className="flex-1 px-4 py-4 pb-28 overflow-y-auto animate-fade-in">{children}</main>
        <BottomNav />
      </div>
    </>
  );
}
