import { lazy, Suspense, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Users, AlertTriangle, Camera, Activity, PencilLine } from "lucide-react";
import { useFloorPlan } from "../../features/floorplan/useFloorPlan";
import { useFloorPlanStore } from "../../features/floorplan/floorplan-store";
import { FloorPlanView } from "./FloorPlanView";
import { RoomSheet } from "./RoomSheet";
import { Button } from "../../components/Button";
import { Spinner } from "../../components/Spinner";
import { EmptyState } from "../../components/EmptyState";
import { api } from "../../lib/api";
import type { Room } from "../../lib/types";

const FloorPlanEditor = lazy(() =>
  import("./FloorPlanEditor").then((m) => ({ default: m.FloorPlanEditor })),
);

interface RoomStates {
  presence_rooms: string[];
  alert_rooms: string[];
}

interface DigestSummary {
  total_events: number;
  faces_today: number;
  alerts_today: number;
  cameras_online: number;
}

type Tone = "neutral" | "ok" | "danger" | "info" | "sky";

const TONE: Record<Tone, { bg: string; fg: string }> = {
  neutral: { bg: "bg-[color:var(--raised)]", fg: "text-[color:var(--text-muted)]" },
  ok: { bg: "bg-emerald-500/15", fg: "text-emerald-400" },
  danger: { bg: "bg-red-500/15", fg: "text-red-400" },
  info: { bg: "bg-primary-500/15", fg: "text-primary-400" },
  sky: { bg: "bg-sky-500/15", fg: "text-sky-400" },
};

interface StatCardProps {
  icon: React.ReactNode;
  label: string;
  value: string | number;
  tone?: Tone;
  highlight?: boolean;
}

function StatCard({ icon, label, value, tone = "neutral", highlight }: StatCardProps) {
  const c = TONE[tone];
  return (
    <div
      className={`card card-hover rounded-2xl px-4 py-3.5 flex items-center gap-3 ${
        highlight ? "ring-1 ring-red-500/40" : ""
      }`}
    >
      <div className={`w-10 h-10 rounded-xl flex items-center justify-center shrink-0 ${c.bg}`}>
        <span className={c.fg}>{icon}</span>
      </div>
      <div className="min-w-0">
        <p className="text-xl font-bold font-mono tabular-nums text-[color:var(--text)] leading-none">
          {value}
        </p>
        <p className="text-xs text-[color:var(--text-muted)] mt-1 truncate">{label}</p>
      </div>
    </div>
  );
}

function InsightsStrip({
  presenceCount,
  alertCount,
}: {
  presenceCount: number;
  alertCount: number;
}) {
  const { data: digest } = useQuery<DigestSummary>({
    queryKey: ["digest-summary"],
    queryFn: () => api.get<DigestSummary>("/api/digest/summary", true),
    staleTime: 60_000,
    refetchInterval: 60_000,
  });

  return (
    <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
      <StatCard
        icon={<Users size={18} strokeWidth={1.8} />}
        label="Присутніх"
        value={presenceCount}
        tone="ok"
      />
      <StatCard
        icon={<AlertTriangle size={18} strokeWidth={1.8} />}
        label="Тривог сьогодні"
        value={digest?.alerts_today ?? alertCount}
        tone={alertCount > 0 ? "danger" : "neutral"}
        highlight={alertCount > 0}
      />
      <StatCard
        icon={<Camera size={18} strokeWidth={1.8} />}
        label="Камери онлайн"
        value={digest?.cameras_online ?? "—"}
        tone="info"
      />
      <StatCard
        icon={<Activity size={18} strokeWidth={1.8} />}
        label="Подій сьогодні"
        value={digest?.total_events ?? "—"}
        tone="sky"
      />
    </div>
  );
}

export default function HomePage() {
  const { data, isLoading, error } = useFloorPlan();
  const { editMode, setEditMode, setDraft } = useFloorPlanStore();
  const [selectedRoom, setSelectedRoom] = useState<Room | null>(null);

  const { data: roomStates } = useQuery<RoomStates>({
    queryKey: ["room_states"],
    queryFn: () => api.get<RoomStates>("/api/floorplan/room_states", true),
    refetchInterval: 30_000,
    staleTime: 25_000,
    enabled: !!data,
  });

  const alertRooms = useMemo(
    () => new Set(roomStates?.alert_rooms ?? []),
    [roomStates?.alert_rooms],
  );
  const presenceRooms = useMemo(
    () => new Set(roomStates?.presence_rooms ?? []),
    [roomStates?.presence_rooms],
  );

  if (isLoading) {
    return (
      <div className="flex justify-center pt-16">
        <Spinner className="h-8 w-8" />
      </div>
    );
  }

  if (error || !data) {
    return <EmptyState message="Не вдалося завантажити план будинку" icon="⌂" />;
  }

  if (editMode) {
    return (
      <Suspense
        fallback={
          <div className="flex justify-center pt-16">
            <Spinner className="h-8 w-8" />
          </div>
        }
      >
        <FloorPlanEditor />
      </Suspense>
    );
  }

  return (
    <div className="space-y-5 animate-fade-in">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="font-display font-semibold text-2xl text-[color:var(--text)]">
            Мій дім
          </h1>
          <p className="text-xs font-mono text-[color:var(--text-muted)] mt-1 tracking-wide uppercase">
            {new Date().toLocaleDateString("uk-UA", {
              weekday: "long",
              day: "numeric",
              month: "long",
            })}
          </p>
        </div>
        <Button
          size="sm"
          variant="ghost"
          onClick={() => {
            setDraft(data);
            setEditMode(true);
          }}
          className="gap-1.5"
        >
          <PencilLine size={14} />
          Редагувати
        </Button>
      </div>

      {/* Insights strip */}
      <InsightsStrip
        presenceCount={presenceRooms.size}
        alertCount={alertRooms.size}
      />

      {/* Floor plan */}
      {data.floor_plans.length === 0 ? (
        <div className="py-16 text-center">
          <p className="mb-2 text-4xl opacity-20">⌂</p>
          <p className="mb-4 text-sm text-[color:var(--text-muted)]">Плану будинку ще немає.</p>
          <Button
            size="sm"
            variant="primary"
            onClick={() => {
              setDraft(data);
              setEditMode(true);
            }}
          >
            Створити план
          </Button>
        </div>
      ) : (
        <>
          {/* Legend */}
          <div className="flex items-center gap-4 text-xs text-[color:var(--text-muted)]">
            <span className="flex items-center gap-1.5">
              <span className="inline-block h-2.5 w-2.5 rounded-full bg-emerald-500" />
              присутність
            </span>
            <span className="flex items-center gap-1.5">
              <span className="inline-block h-2.5 w-2.5 rounded-full bg-red-500" />
              тривога
            </span>
          </div>

          <FloorPlanView
            data={data}
            onRoomClick={setSelectedRoom}
            alertRooms={alertRooms}
            presenceRooms={presenceRooms}
          />
          <RoomSheet
            room={selectedRoom}
            placements={data.placements}
            onClose={() => setSelectedRoom(null)}
          />
        </>
      )}
    </div>
  );
}
