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
      {/* Desktop layout: sidebar + topbar */}
      <div className="hidden sm:block">
        <Sidebar />
        <TopBar />
        <div className="ml-[220px] mt-14">
          <OfflineBanner />
          <main className="max-w-5xl mx-auto px-6 py-6">{children}</main>
        </div>
      </div>

      {/* Mobile layout: top offline banner + content + bottom nav */}
      <div className="sm:hidden flex flex-col min-h-screen">
        <OfflineBanner />
        <main className="flex-1 px-4 py-4 pb-[76px] overflow-y-auto">{children}</main>
        <BottomNav />
      </div>
    </>
  );
}
