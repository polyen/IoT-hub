import { lazy, Suspense, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { PencilLine } from "lucide-react";
import { useFloorPlan } from "../../features/floorplan/useFloorPlan";
import { useFloorPlanStore } from "../../features/floorplan/floorplan-store";
import { FloorPlanView, type ClimateBySlug } from "./FloorPlanView";
import { RoomSheet } from "./RoomSheet";
import { HomeStatus } from "./HomeStatus";
import { SceneChips } from "./SceneChips";
import { AttentionFeed } from "./AttentionFeed";
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

interface LatestClimate {
  rooms: Record<string, { values: Record<string, number> }>;
}

interface DigestSummary {
  total_events: number;
  faces_today: number;
  alerts_today: number;
  cameras_online: number;
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

  const { data: latestClimate } = useQuery<LatestClimate>({
    queryKey: ["sensors-latest"],
    queryFn: () => api.get<LatestClimate>("/api/sensors/latest", true),
    refetchInterval: 30_000,
    staleTime: 25_000,
    enabled: !!data,
  });

  const { data: digest } = useQuery<DigestSummary>({
    queryKey: ["digest-summary"],
    queryFn: () => api.get<DigestSummary>("/api/digest/summary", true),
    staleTime: 60_000,
    refetchInterval: 60_000,
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

  // Resolve room IDs → display names for the status hero.
  const nameById = useMemo(() => {
    const m = new Map<string, string>();
    for (const r of data?.rooms ?? []) m.set(r.id, r.name);
    return m;
  }, [data?.rooms]);

  const alertNames = useMemo(
    () => [...alertRooms].map((id) => nameById.get(id) ?? id),
    [alertRooms, nameById],
  );
  const presenceNames = useMemo(
    () => [...presenceRooms].map((id) => nameById.get(id) ?? id),
    [presenceRooms, nameById],
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
      {/* Status hero — answers "is everything okay?" first */}
      <HomeStatus
        alertRooms={alertNames}
        presenceRooms={presenceNames}
        camerasOnline={digest?.cameras_online ?? "—"}
        eventsToday={digest?.total_events ?? "—"}
      />

      {/* One-tap context switching */}
      <SceneChips />

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
        <section className="space-y-2">
          <div className="flex items-center justify-between px-1">
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

          <FloorPlanView
            data={data}
            onRoomClick={setSelectedRoom}
            alertRooms={alertRooms}
            presenceRooms={presenceRooms}
            climate={climate}
          />
          <RoomSheet room={selectedRoom} data={data} onClose={() => setSelectedRoom(null)} />
        </section>
      )}

      {/* De-noised attention feed */}
      <AttentionFeed />
    </div>
  );
}
