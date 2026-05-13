import { Sheet } from "../../components/Dialog";
import { DeviceQuickControl } from "./DeviceQuickControl";
import type { Room, DevicePlacement } from "../../lib/types";

interface Props {
  room: Room | null;
  placements: DevicePlacement[];
  onClose: () => void;
}

const ROOM_TYPE_LABEL: Record<string, string> = {
  bedroom: "Спальня", kitchen: "Кухня", living: "Вітальня",
  bath: "Ванна", hall: "Коридор", outdoor: "Надвір", other: "Інше",
};

export function RoomSheet({ room, placements, onClose }: Props) {
  if (!room) return null;
  const roomPlacements = placements.filter((p) => p.room_id === room.id);

  return (
    <Sheet open={!!room} onOpenChange={(o) => !o && onClose()} title={room.name}>
      <p className="text-xs text-slate-500 mb-3">{ROOM_TYPE_LABEL[room.type] ?? room.type}</p>
      {roomPlacements.length === 0 ? (
        <p className="text-sm text-slate-500 py-4 text-center">Немає пристроїв у цій кімнаті</p>
      ) : (
        <div>
          {roomPlacements.map((p) => (
            <DeviceQuickControl key={p.id} placement={p} />
          ))}
        </div>
      )}
    </Sheet>
  );
}
