import { lazy, Suspense, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
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

  function enterEditMode() {
    setDraft(data!);
    setEditMode(true);
  }

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
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Мій дім</h1>
        <div className="flex items-center gap-4">
          <div className="hidden sm:flex gap-3 text-xs text-slate-500">
            <span className="flex items-center gap-1">
              <span className="inline-block h-3 w-3 rounded-sm bg-[#14532d]" />
              присутність
            </span>
            <span className="flex items-center gap-1">
              <span className="inline-block h-3 w-3 rounded-sm bg-[#7f1d1d]" />
              тривога
            </span>
          </div>
          <Button size="sm" variant="ghost" onClick={enterEditMode} title="Редагувати план">
            ✏️ Редагувати
          </Button>
        </div>
      </div>

      {data.floor_plans.length === 0 ? (
        <div className="py-16 text-center">
          <p className="mb-2 text-4xl">⌂</p>
          <p className="mb-4 text-slate-400">Плану будинку ще немає.</p>
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
