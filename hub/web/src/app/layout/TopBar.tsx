import React from "react";
import { Sun, Moon, WifiOff, Maximize2 } from "lucide-react";
import { Link } from "react-router-dom";
import { useTheme } from "../providers/ThemeProvider";
import { useOnlineStatus } from "../../hooks/useOnlineStatus";
import { ConfirmBell } from "./ConfirmBell";

function Clock() {
  const [time, setTime] = React.useState(() => new Date());

  React.useEffect(() => {
    const id = setInterval(() => setTime(new Date()), 10_000);
    return () => clearInterval(id);
  }, []);

  return (
    <span className="text-sm font-mono font-medium text-[color:var(--text-muted)] tabular-nums tracking-wide">
      {time.toLocaleTimeString("uk-UA", { hour: "2-digit", minute: "2-digit" })}
    </span>
  );
}

export function TopBar() {
  const { theme, toggle } = useTheme();
  const isOnline = useOnlineStatus();

  return (
    <header
      className="fixed top-0 right-0 z-20 flex items-center justify-between px-5"
      style={{
        left: "var(--sidebar-w)",
        height: "var(--topbar-h)",
        background: "var(--card)",
        borderBottom: "1px solid var(--border)",
      }}
    >
      <div className="flex items-center gap-2">
        <span
          className={`status-dot ${isOnline ? "bg-green-500" : "bg-warm-500 animate-pulse"}`}
        />
        <span className="text-xs text-[color:var(--text-muted)]">
          {isOnline ? "Онлайн" : "Офлайн"}
        </span>
      </div>

      <div className="flex items-center gap-4">
        <Clock />

        <ConfirmBell />

        <Link
          to="/wall"
          title="Режим кіоску"
          className="p-2 rounded-lg text-[color:var(--text-muted)] hover:text-[color:var(--text)] hover:bg-[color:var(--raised)] transition-colors"
        >
          <Maximize2 size={16} />
        </Link>

        <button
          onClick={toggle}
          className="p-2 rounded-lg text-[color:var(--text-muted)] hover:text-[color:var(--text)] hover:bg-[color:var(--raised)] transition-colors"
          title={theme === "dark" ? "Світла тема" : "Темна тема"}
        >
          {theme === "dark" ? <Sun size={16} /> : <Moon size={16} />}
        </button>

        {!isOnline && (
          <span className="text-warm-500">
            <WifiOff size={16} />
          </span>
        )}
      </div>
    </header>
  );
}
