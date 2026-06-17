import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ShieldCheck, Users, AlertTriangle, Minimize2, Wifi, WifiOff } from "lucide-react";
import { useFloorPlan } from "../../features/floorplan/useFloorPlan";
import { FloorPlanView, type ClimateBySlug } from "../home/FloorPlanView";
import { SCENES, useRunScene } from "../../features/scenes/scenes";
import { useOnlineStatus } from "../../hooks/useOnlineStatus";
import { Spinner } from "../../components/Spinner";
import { useWebSocket } from "../../hooks/useWebSocket";

interface RoomStates {
  presence_rooms: string[];
  alert_rooms: string[];
}
interface LatestClimate {
  rooms: Record<string, { values: Record<string, number> }>;
}

function greeting(hour: number): string {
  if (hour >= 5 && hour < 11) return "Доброго ранку";
  if (hour >= 11 && hour < 17) return "Доброго дня";
  if (hour >= 17 && hour < 22) return "Добрий вечір";
  return "Доброї ночі";
}

/** Screen dimming for a wall-mounted display — darker late at night. */
function nightDim(hour: number): number {
  if (hour >= 23 || hour < 6) return 0.5;
  if (hour >= 22 || hour < 7) return 0.25;
  return 0;
}

export default function WallPage() {
  const { data, isLoading } = useFloorPlan();
  const isOnline = useOnlineStatus();
  const { run, runningId } = useRunScene();
  const { events } = useWebSocket();
  const [now, setNow] = useState(() => new Date());
  const [lastMotionTs, setLastMotionTs] = useState<number | null>(null);

  const WAKE_WINDOW_MS = 90_000;

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 10_000);
    return () => clearInterval(id);
  }, []);

  // Update lastMotionTs whenever a new presence/motion event arrives via WebSocket.
  useEffect(() => {
    const newest = events[0];
    if (!newest) return;
    const isPresence =
      newest.type === "presence" ||
      (newest.type === "alert" &&
        ["motion", "presence", "occupancy"].includes(
          String(newest.payload?.alert_type ?? "").toLowerCase(),
        ));
    if (isPresence) {
      const ts = Date.parse(newest.timestamp);
      if (!isNaN(ts)) {
        setLastMotionTs((prev) => (prev === null || ts > prev ? ts : prev));
      }
    }
  }, [events]);

  const { data: roomStates } = useQuery<RoomStates>({
    queryKey: ["room_states"],
    queryFn: () => fetch("/api/floorplan/room_states").then((r) => r.json()),
    refetchInterval: 20_000,
    enabled: !!data,
  });
  const { data: latestClimate } = useQuery<LatestClimate>({
    queryKey: ["sensors-latest"],
    queryFn: () => fetch("/api/sensors/latest").then((r) => r.json()),
    refetchInterval: 20_000,
    enabled: !!data,
  });

  const climate = useMemo<ClimateBySlug>(() => {
    const out: ClimateBySlug = {};
    for (const [slug, c] of Object.entries(latestClimate?.rooms ?? {})) {
      out[slug] = { temperature: c.values.temperature, humidity: c.values.humidity };
    }
    return out;
  }, [latestClimate]);

  const alertRooms = useMemo(() => new Set(roomStates?.alert_rooms ?? []), [roomStates?.alert_rooms]);
  const presenceRooms = useMemo(
    () => new Set(roomStates?.presence_rooms ?? []),
    [roomStates?.presence_rooms],
  );

  const nameById = useMemo(() => {
    const m = new Map<string, string>();
    for (const r of data?.rooms ?? []) m.set(r.id, r.name);
    return m;
  }, [data?.rooms]);

  const mood = alertRooms.size > 0 ? "alarm" : presenceRooms.size > 0 ? "active" : "calm";
  const alertNames = [...alertRooms].map((id) => nameById.get(id) ?? id);
  const presenceNames = [...presenceRooms].map((id) => nameById.get(id) ?? id);

  const climateRooms = useMemo(
    () =>
      (data?.rooms ?? [])
        .map((r) => ({ name: r.name, cl: climate[r.slug] }))
        .filter((x) => x.cl?.temperature != null),
    [data?.rooms, climate],
  );

  const motionRecently =
    presenceRooms.size > 0 ||
    (lastMotionTs !== null && now.getTime() - lastMotionTs < WAKE_WINDOW_MS);
  const baseDim = nightDim(now.getHours());
  const dim = motionRecently || mood === "alarm" ? 0 : baseDim;
  const quickScenes = SCENES.filter((s) => s.quick);

  if (isLoading || !data) {
    return (
      <div className="grid h-screen place-items-center bg-[color:var(--bg)]">
        <Spinner className="h-10 w-10" />
      </div>
    );
  }

  const statusStyle =
    mood === "alarm"
      ? { Icon: AlertTriangle, fg: "text-red-400", bg: "bg-red-500/15", ring: "ring-2 ring-red-500/60" }
      : mood === "active"
        ? { Icon: Users, fg: "text-primary-400", bg: "bg-primary-500/15", ring: "ring-1 ring-primary-500/30" }
        : { Icon: ShieldCheck, fg: "text-emerald-400", bg: "bg-emerald-500/15", ring: "ring-1 ring-emerald-500/25" };
  const StatusIcon = statusStyle.Icon;
  const statusText =
    mood === "alarm"
      ? `Тривога: ${alertNames.join(", ")}`
      : mood === "active"
        ? `Вдома · ${presenceNames.join(", ")}`
        : "Все спокійно";

  return (
    <div className="relative flex h-screen flex-col gap-5 overflow-hidden bg-[color:var(--bg)] p-7">
      {/* Night dimming overlay — always mounted so opacity transition fires smoothly */}
      <div
        className="pointer-events-none fixed inset-0 z-50 bg-black transition-opacity duration-1000"
        style={{ opacity: dim }}
      />

      {/* Header */}
      <header className="flex shrink-0 items-end justify-between">
        <div className="flex items-end gap-6">
          <span className="font-mono text-7xl font-bold leading-none tracking-tight text-[color:var(--text)] tabular-nums">
            {now.toLocaleTimeString("uk-UA", { hour: "2-digit", minute: "2-digit" })}
          </span>
          <div className="pb-1">
            <p className="font-display text-2xl font-semibold text-[color:var(--text)]">
              {greeting(now.getHours())}
            </p>
            <p className="text-sm uppercase tracking-wide text-[color:var(--text-muted)]">
              {now.toLocaleDateString("uk-UA", { weekday: "long", day: "numeric", month: "long" })}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-4">
          <span className={isOnline ? "text-emerald-400" : "text-warm-500"}>
            {isOnline ? <Wifi size={22} /> : <WifiOff size={22} />}
          </span>
          <Link
            to="/"
            aria-label="Вийти з кіоску"
            className="rounded-xl p-2 text-[color:var(--text-faint)] transition-colors hover:text-[color:var(--text)]"
          >
            <Minimize2 size={22} />
          </Link>
        </div>
      </header>

      {/* Status banner */}
      <div
        className={`flex shrink-0 items-center gap-5 rounded-3xl bg-[color:var(--card)] px-7 py-5 ${statusStyle.ring} ${mood === "alarm" ? "animate-pulse-slow" : ""
          }`}
      >
        <div className={`grid h-16 w-16 shrink-0 place-items-center rounded-2xl ${statusStyle.bg}`}>
          <StatusIcon size={32} strokeWidth={1.8} className={statusStyle.fg} />
        </div>
        <p className="text-3xl font-semibold text-[color:var(--text)]">{statusText}</p>
      </div>

      {/* Main: floor plan + side column */}
      <main className="grid min-h-0 flex-1 grid-cols-3 gap-5">
        {/* Floor plan */}
        <div className="col-span-2 flex min-h-0 items-center justify-center overflow-hidden rounded-3xl bg-[color:var(--card)] p-4">
          {data.floor_plans.length > 0 ? (
            <div
              className="max-w-full"
              style={{
                height: "100%",
                width: "auto",
                aspectRatio: `${data.floor_plans[0].width} / ${data.floor_plans[0].height}`,
              }}
            >
              <FloorPlanView
                data={data}
                onRoomClick={() => { }}
                alertRooms={alertRooms}
                presenceRooms={presenceRooms}
                climate={climate}
              />
            </div>
          ) : (
            <p className="text-[color:var(--text-muted)]">Плану будинку ще немає</p>
          )}
        </div>

        {/* Side column */}
        <div className="flex min-h-0 flex-col gap-5">
          {/* Climate */}
          {climateRooms.length > 0 && (
            <div className="grid grid-cols-2 gap-3">
              {climateRooms.slice(0, 4).map(({ name, cl }) => (
                <div key={name} className="rounded-2xl bg-[color:var(--card)] px-4 py-3">
                  <p className="truncate text-sm text-[color:var(--text-muted)]">{name}</p>
                  <p className="font-mono text-3xl font-bold text-[color:var(--text)] tabular-nums">
                    {cl!.temperature!.toFixed(1)}°
                    {cl!.humidity != null && (
                      <span className="ml-2 text-lg font-normal text-[color:var(--text-faint)]">
                        {Math.round(cl!.humidity)}%
                      </span>
                    )}
                  </p>
                </div>
              ))}
            </div>
          )}

          {/* Scenes */}
          <div className="grid flex-1 grid-cols-2 gap-3">
            {quickScenes.map((scene) => {
              const busy = runningId === scene.id;
              return (
                <button
                  key={scene.id}
                  disabled={busy}
                  onClick={() => run(scene)}
                  className="flex flex-col items-center justify-center gap-2 rounded-2xl bg-[color:var(--card)] p-4 transition-colors hover:bg-[color:var(--card-hover)] disabled:opacity-50"
                >
                  {busy ? <Spinner className="h-7 w-7" /> : <span className="text-4xl">{scene.icon}</span>}
                  <span className="text-base font-medium text-[color:var(--text)]">
                    {scene.short ?? scene.name}
                  </span>
                </button>
              );
            })}
          </div>
        </div>
      </main>
    </div>
  );
}
