import { useState } from "react";
import { useFloorPlan } from "../../features/floorplan/useFloorPlan";
import { FloorPlanView } from "./FloorPlanView";
import { RoomSheet } from "./RoomSheet";
import { Spinner } from "../../components/Spinner";
import { EmptyState } from "../../components/EmptyState";
import type { Room } from "../../lib/types";

export default function HomePage() {
  const { data, isLoading, error } = useFloorPlan();
  const [selectedRoom, setSelectedRoom] = useState<Room | null>(null);

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

  if (!data.floor_plans.length) {
    return (
      <div className="text-center py-16">
        <p className="text-slate-400 mb-4">Планів будинку ще немає.</p>
        <p className="text-slate-500 text-sm">Перейди у «Більше → Пристрої» щоб додати кімнати.</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Мій дім</h1>
        <div className="flex gap-3 text-xs text-slate-500">
          <span className="flex items-center gap-1">
            <span className="inline-block w-3 h-3 rounded-sm bg-[#14532d]" /> присутність
          </span>
          <span className="flex items-center gap-1">
            <span className="inline-block w-3 h-3 rounded-sm bg-[#7f1d1d]" /> тривога
          </span>
        </div>
      </div>

      <FloorPlanView
        data={data}
        onRoomClick={setSelectedRoom}
      />

      <RoomSheet
        room={selectedRoom}
        placements={data.placements}
        onClose={() => setSelectedRoom(null)}
      />
    </div>
  );
}
