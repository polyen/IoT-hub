import { useState } from "react";
import type { FloorPlanData, Room } from "../../lib/types";

const ROOM_COLORS: Record<string, string> = {
  idle: "#0d1a2e",
  alert: "#2d0a0a",
  presence: "#0a1e10",
};

const KIND_ICON: Record<string, string> = {
  camera: "⬛", light: "💡", lock: "🔒", thermostat: "🌡", relay: "⚡",
  sensor_pir: "👁", sensor_door: "🚪", sensor_dht: "🌡", sensor_mq2: "💨",
  sensor_power: "⚡", speaker: "🔊",
};

interface Props {
  data: FloorPlanData;
  onRoomClick: (room: Room) => void;
  /** active alerts: set of room names/ids that have alerts */
  alertRooms?: Set<string>;
  presenceRooms?: Set<string>;
}

export function FloorPlanView({ data, onRoomClick, alertRooms, presenceRooms }: Props) {
  const [hovered, setHovered] = useState<string | null>(null);

  const plan = data.floor_plans[0];
  if (!plan) return null;

  const rooms = data.rooms.filter((r) => r.floor_plan_id === plan.id);
  const placements = data.placements;

  /* normalised → SVG coords (0..100) */
  const toSvg = ([x, y]: [number, number]): string => `${x * 100},${y * 100}`;

  const roomColor = (room: Room): string => {
    if (alertRooms?.has(room.id)) return ROOM_COLORS.alert;
    if (presenceRooms?.has(room.id)) return ROOM_COLORS.presence;
    return room.color ?? ROOM_COLORS.idle;
  };

  return (
    <div
      className="relative w-full rounded-xl overflow-hidden"
      style={{ border: "1px solid var(--border)", background: "var(--card)" }}
    >
      <svg
        viewBox="0 0 100 100"
        className="w-full"
        style={{ aspectRatio: `${plan.width} / ${plan.height}` }}
        preserveAspectRatio="xMidYMid meet"
      >
        {rooms.map((room) => {
          const pts = room.polygon.map(toSvg).join(" ");
          const cx = room.polygon.reduce((s, [x]) => s + x, 0) / room.polygon.length * 100;
          const cy = room.polygon.reduce((s, [, y]) => s + y, 0) / room.polygon.length * 100;
          const isHovered = hovered === room.id;
          const roomPlacements = placements.filter((p) => p.room_id === room.id);

          return (
            <g key={room.id}>
              <polygon
                points={pts}
                fill={roomColor(room)}
                fillOpacity={isHovered ? 0.9 : 0.7}
                stroke={isHovered ? "#c9a84c" : "#1a2e4a"}
                strokeWidth={isHovered ? 0.6 : 0.4}
                className="cursor-pointer transition-all"
                onMouseEnter={() => setHovered(room.id)}
                onMouseLeave={() => setHovered(null)}
                onClick={() => onRoomClick(room)}
              />
              <text
                x={cx}
                y={cy - (roomPlacements.length > 0 ? 2 : 0)}
                textAnchor="middle"
                dominantBaseline="middle"
                fontSize="3.5"
                fill="#c8c4b8"
                className="pointer-events-none select-none"
              >
                {room.name}
              </text>
              {/* device icons row */}
              {roomPlacements.slice(0, 4).map((p, i) => (
                <text
                  key={p.id}
                  x={cx - (roomPlacements.length * 3) / 2 + i * 3 + 1.5}
                  y={cy + 3}
                  textAnchor="middle"
                  dominantBaseline="middle"
                  fontSize="3"
                  className="pointer-events-none select-none"
                >
                  {KIND_ICON[p.kind] ?? "⚙"}
                </text>
              ))}
            </g>
          );
        })}
      </svg>
    </div>
  );
}
