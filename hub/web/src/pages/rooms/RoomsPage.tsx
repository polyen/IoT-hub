import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import {
  Lightbulb,
  Thermometer,
  ShieldCheck,
  Cpu,
  ChevronRight,
  Map as MapIcon,
  LayoutGrid,
  Users,
  AlertTriangle,
  LucideIcon,
} from "lucide-react";
import { clsx } from "clsx";
import { useFloorPlan } from "../../features/floorplan/useFloorPlan";
import { FloorPlanView, type ClimateBySlug } from "../home/FloorPlanView";
import { RoomSheet } from "../home/RoomSheet";
import { Spinner } from "../../components/Spinner";
import { EmptyState } from "../../components/EmptyState";
import { api } from "../../lib/api";
import type { DeviceKind, Room } from "../../lib/types";

interface RoomStates {
  presence_rooms: string[];
  alert_rooms: string[];
}
interface LatestClimate {
  rooms: Record<string, { values: Record<string, number> }>;
}

type Lens = "plan" | "categories";

// ── Device-type categories (sourced from floor-plan placements) ──────────────
interface Category {
  key: string;
  label: string;
  Icon: LucideIcon;
  color: string;
  bg: string;
  kinds: DeviceKind[];
  to: string;
}

const CATEGORIES: Category[] = [
  {
    key: "light",
    label: "Світло",
    Icon: Lightbulb,
    color: "text-amber-400",
    bg: "bg-amber-500/15",
    kinds: ["light"],
    to: "/more/devices",
  },
  {
    key: "climate",
    label: "Клімат",
    Icon: Thermometer,
    color: "text-orange-400",
    bg: "bg-orange-500/15",
    kinds: ["thermostat", "sensor_dht"],
    to: "/more/climate",
  },
  {
    key: "security",
    label: "Безпека",
    Icon: ShieldCheck,
    color: "text-emerald-400",
    bg: "bg-emerald-500/15",
    kinds: ["lock", "camera", "sensor_pir", "sensor_door", "sensor_mq2"],
    to: "/more/security",
  },
  {
    key: "devices",
    label: "Пристрої",
    Icon: Cpu,
    color: "text-cyan-400",
    bg: "bg-cyan-500/15",
    kinds: ["relay", "sensor_power", "speaker"],
    to: "/more/devices",
  },
];

function LensToggle({ lens, setLens }: { lens: Lens; setLens: (l: Lens) => void }) {
  const opts: { value: Lens; label: string; Icon: LucideIcon }[] = [
    { value: "plan", label: "План", Icon: MapIcon },
    { value: "categories", label: "Категорії", Icon: LayoutGrid },
  ];
  return (
    <div className="inline-flex rounded-xl border border-[color:var(--border)] bg-[color:var(--card)] p-1">
      {opts.map((o) => {
        const Icon = o.Icon;
        const active = lens === o.value;
        return (
          <button
            key={o.value}
            onClick={() => setLens(o.value)}
            className={clsx(
              "flex items-center gap-1.5 rounded-lg px-3 py-1.5 text-sm font-medium transition-colors",
              active
                ? "bg-primary-600/20 text-primary-300"
                : "text-[color:var(--text-muted)] hover:text-[color:var(--text)]",
            )}
          >
            <Icon size={15} />
            {o.label}
          </button>
        );
      })}
    </div>
  );
}

export default function RoomsPage() {
  const { data, isLoading, error } = useFloorPlan();
  const [lens, setLens] = useState<Lens>("plan");
  const [selectedRoom, setSelectedRoom] = useState<Room | null>(null);

  const { data: roomStates } = useQuery<RoomStates>({
    queryKey: ["room_states"],
    queryFn: () => api.get<RoomStates>("/api/floorplan/room_states", true),
    refetchInterval: 30_000,
    staleTime: 25_000,
    enabled: !!data,
  });
  const { data: latestClimate } = useQuery<LatestClimate>({
    queryKey: ["sensors-latest"],
    queryFn: () => api.get<LatestClimate>("/api/sensors/latest", true),
    refetchInterval: 30_000,
    staleTime: 25_000,
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

  // Device counts per category (from placements — same source as DevicesListPage).
  const countByKind = useMemo(() => {
    const m = new Map<string, number>();
    for (const p of data?.placements ?? []) m.set(p.kind, (m.get(p.kind) ?? 0) + 1);
    return m;
  }, [data?.placements]);

  if (isLoading) {
    return (
      <div className="flex justify-center pt-16">
        <Spinner className="h-8 w-8" />
      </div>
    );
  }
  if (error || !data) {
    return <EmptyState message="Не вдалося завантажити кімнати" icon="⌂" />;
  }

  const rooms = [...data.rooms].sort((a, b) => a.order - b.order);

  return (
    <div className="space-y-5 animate-fade-in">
      <div className="flex items-center justify-between">
        <h1 className="font-display text-2xl font-semibold text-[color:var(--text)]">Кімнати</h1>
        <LensToggle lens={lens} setLens={setLens} />
      </div>

      {lens === "plan" ? (
        <>
          {data.floor_plans.length > 0 && (
            <FloorPlanView
              data={data}
              onRoomClick={setSelectedRoom}
              alertRooms={alertRooms}
              presenceRooms={presenceRooms}
              climate={climate}
            />
          )}

          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            {rooms.map((room) => {
              const cl = climate[room.slug];
              const deviceCount = data.placements.filter((p) => p.room_id === room.id).length;
              const hasAlert = alertRooms.has(room.id);
              const hasPresence = presenceRooms.has(room.id);
              return (
                <button
                  key={room.id}
                  onClick={() => setSelectedRoom(room)}
                  className={clsx(
                    "card card-hover flex items-center gap-3 rounded-2xl px-4 py-3 text-left",
                    hasAlert && "ring-1 ring-red-500/40",
                  )}
                >
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-sm font-semibold text-[color:var(--text)]">
                        {room.name}
                      </span>
                      {hasAlert && <AlertTriangle size={13} className="shrink-0 text-red-400" />}
                      {!hasAlert && hasPresence && (
                        <Users size={13} className="shrink-0 text-emerald-400" />
                      )}
                    </div>
                    <p className="mt-0.5 text-xs text-[color:var(--text-muted)]">
                      {deviceCount} {deviceCount === 1 ? "пристрій" : "пристроїв"}
                    </p>
                  </div>
                  {cl && (cl.temperature != null || cl.humidity != null) && (
                    <div className="shrink-0 text-right font-mono">
                      {cl.temperature != null && (
                        <p className="text-sm font-semibold text-[color:var(--text)]">
                          {cl.temperature.toFixed(1)}°
                        </p>
                      )}
                      {cl.humidity != null && (
                        <p className="text-xs text-[color:var(--text-faint)]">
                          {Math.round(cl.humidity)}%
                        </p>
                      )}
                    </div>
                  )}
                </button>
              );
            })}
          </div>
        </>
      ) : (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          {CATEGORIES.map((cat) => {
            const Icon = cat.Icon;
            const count = cat.kinds.reduce((s, k) => s + (countByKind.get(k) ?? 0), 0);
            return (
              <Link
                key={cat.key}
                to={cat.to}
                className="card card-hover flex items-center gap-3.5 rounded-2xl px-4 py-4"
              >
                <div className={`grid h-11 w-11 shrink-0 place-items-center rounded-xl ${cat.bg}`}>
                  <Icon size={20} strokeWidth={1.8} className={cat.color} />
                </div>
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-semibold text-[color:var(--text)]">{cat.label}</p>
                  <p className="mt-0.5 text-xs text-[color:var(--text-muted)]">
                    {count} {count === 1 ? "пристрій" : "пристроїв"}
                  </p>
                </div>
                <ChevronRight size={15} className="text-[color:var(--text-faint)]" />
              </Link>
            );
          })}
        </div>
      )}

      <RoomSheet room={selectedRoom} data={data} onClose={() => setSelectedRoom(null)} />
    </div>
  );
}
