import { useState } from "react";
import type { FloorPlanData, Room } from "../../lib/types";
import { deviceMeta } from "../../lib/deviceIcons";

const ROOM_COLORS: Record<string, string> = {
  idle: "#0d1a2e",
  alert: "#2d0a0a",
  presence: "#0a1e10",
};

/** Latest microclimate readings keyed by room slug (from /api/sensors/latest). */
export type ClimateBySlug = Record<string, { temperature?: number; humidity?: number }>;

interface Props {
  data: FloorPlanData;
  onRoomClick: (room: Room) => void;
  /** active alerts: set of room names/ids that have alerts */
  alertRooms?: Set<string>;
  presenceRooms?: Set<string>;
  climate?: ClimateBySlug;
}

export function FloorPlanView({ data, onRoomClick, alertRooms, presenceRooms, climate }: Props) {
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

          const cl = climate?.[room.slug];
          const climateLabel = cl
            ? [
                cl.temperature != null ? `${cl.temperature.toFixed(1)}°` : null,
                cl.humidity != null ? `${Math.round(cl.humidity)}%` : null,
              ]
                .filter(Boolean)
                .join("  ")
            : null;

          const hasExtras = climateLabel != null || roomPlacements.length > 0;
          const iconY = cy + (climateLabel ? 2.8 : 1.4);

          return (
            <g key={room.id}>
              <polygon
                points={pts}
                fill={roomColor(room)}
                fillOpacity={isHovered ? 0.9 : 0.7}
                stroke={isHovered ? "#6366f1" : "#29374e"}
                strokeWidth={isHovered ? 0.6 : 0.4}
                className="cursor-pointer transition-all"
                onMouseEnter={() => setHovered(room.id)}
                onMouseLeave={() => setHovered(null)}
                onClick={() => onRoomClick(room)}
              />
              <text
                x={cx}
                y={cy - (hasExtras ? 2.6 : 0)}
                textAnchor="middle"
                dominantBaseline="middle"
                fontSize="3.5"
                fill="#c8c4b8"
                className="pointer-events-none select-none"
              >
                {room.name}
              </text>
              {climateLabel && (
                <text
                  x={cx}
                  y={cy}
                  textAnchor="middle"
                  dominantBaseline="middle"
                  fontSize="2.8"
                  fill="#fbbf77"
                  className="pointer-events-none select-none font-mono"
                >
                  {climateLabel}
                </text>
              )}
              {/* device icons row — lucide glyphs rendered as nested SVGs */}
              {roomPlacements.slice(0, 4).map((p, i) => {
                const { Icon, hex } = deviceMeta(p.kind);
                const w = 3.4;
                const n = Math.min(roomPlacements.length, 4);
                return (
                  <Icon
                    key={p.id}
                    x={cx - (n * w) / 2 + i * w}
                    y={iconY}
                    width={w}
                    height={w}
                    color={hex}
                    strokeWidth={2}
                    className="pointer-events-none select-none"
                  />
                );
              })}
            </g>
          );
        })}
      </svg>
    </div>
  );
}
