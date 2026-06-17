import type { FloorPlanData, Room } from "../../lib/types";
import { deviceMeta } from "../../lib/deviceIcons";

/** Latest microclimate readings keyed by room slug (from /api/sensors/latest). */
export type ClimateBySlug = Record<string, { temperature?: number; humidity?: number }>;

interface Props {
  data: FloorPlanData;
  onRoomClick: (room: Room) => void;
  /** active alerts: set of room ids that have alerts */
  alertRooms?: Set<string>;
  presenceRooms?: Set<string>;
  climate?: ClimateBySlug;
}

type RoomStatus = "idle" | "presence" | "alert";

/**
 * Builds an SVG path for a polygon with rounded corners. Each vertex is
 * replaced by a quadratic curve, with the corner radius clamped to half the
 * shorter adjacent edge so it never self-intersects on small/narrow rooms.
 * Points are expected pre-scaled to the 0..100 viewBox.
 */
function roundedPath(pts: [number, number][], radius: number): string {
  const n = pts.length;
  if (n < 3) return "";
  let d = "";
  for (let i = 0; i < n; i++) {
    const prev = pts[(i - 1 + n) % n];
    const curr = pts[i];
    const next = pts[(i + 1) % n];
    const v1x = curr[0] - prev[0];
    const v1y = curr[1] - prev[1];
    const v2x = next[0] - curr[0];
    const v2y = next[1] - curr[1];
    const len1 = Math.hypot(v1x, v1y) || 1;
    const len2 = Math.hypot(v2x, v2y) || 1;
    const r = Math.min(radius, len1 / 2, len2 / 2);
    const inx = curr[0] - (v1x / len1) * r;
    const iny = curr[1] - (v1y / len1) * r;
    const outx = curr[0] + (v2x / len2) * r;
    const outy = curr[1] + (v2y / len2) * r;
    d += i === 0 ? `M ${inx} ${iny} ` : `L ${inx} ${iny} `;
    d += `Q ${curr[0]} ${curr[1]} ${outx} ${outy} `;
  }
  return d + "Z";
}

export function FloorPlanView({ data, onRoomClick, alertRooms, presenceRooms, climate }: Props) {
  const plan = data.floor_plans[0];
  if (!plan) return null;

  const rooms = data.rooms.filter((r) => r.floor_plan_id === plan.id);
  const placements = data.placements;

  const statusOf = (room: Room): RoomStatus => {
    if (alertRooms?.has(room.id)) return "alert";
    if (presenceRooms?.has(room.id)) return "presence";
    return "idle";
  };

  const FILL: Record<RoomStatus, string> = {
    idle: "url(#fpGlassIdle)",
    presence: "url(#fpGlassPresence)",
    alert: "url(#fpGlassAlert)",
  };
  const FILTER: Record<RoomStatus, string> = {
    idle: "url(#fpShadow)",
    presence: "url(#fpGlowPresence)",
    alert: "url(#fpGlowAlert)",
  };

  return (
    <div
      className="relative w-full overflow-hidden rounded-2xl"
      style={{
        border: "1px solid var(--border)",
        background:
          "radial-gradient(120% 120% at 50% 0%, var(--card-hover), var(--card) 70%)",
        boxShadow: "var(--shadow-card)",
      }}
    >
      <svg
        viewBox="0 0 100 100"
        className="w-full"
        style={{ aspectRatio: `${plan.width} / ${plan.height}` }}
        preserveAspectRatio="xMidYMid meet"
      >
        <defs>
          {/* Glass fills — vertical gradient gives a frosted sheen */}
          <linearGradient id="fpGlassIdle" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--room-fill-top)" />
            <stop offset="100%" stopColor="var(--room-fill-bottom)" />
          </linearGradient>
          <linearGradient id="fpGlassPresence" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="rgba(16,185,129,0.26)" />
            <stop offset="100%" stopColor="rgba(16,185,129,0.06)" />
          </linearGradient>
          <linearGradient id="fpGlassAlert" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="rgba(239,68,68,0.30)" />
            <stop offset="100%" stopColor="rgba(239,68,68,0.08)" />
          </linearGradient>
          {/* Top sheen overlay — concentrated in top 30% for natural specular highlight */}
          <linearGradient id="fpSheen" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="var(--room-sheen)" />
            <stop offset="30%" stopColor="rgba(255,255,255,0)" />
          </linearGradient>

          {/* Soft drop shadow → floating-panel depth */}
          <filter id="fpShadow" x="-20%" y="-20%" width="140%" height="140%">
            <feDropShadow dx="0" dy="0.5" stdDeviation="0.7" floodColor="var(--room-shadow)" />
          </filter>
          {/* Coloured glows (dy=0 → omnidirectional) */}
          <filter id="fpGlowPresence" x="-30%" y="-30%" width="160%" height="160%">
            <feDropShadow dx="0" dy="0" stdDeviation="1.3" floodColor="#10b981" floodOpacity="0.75" />
          </filter>
          <filter id="fpGlowAlert" x="-40%" y="-40%" width="180%" height="180%">
            <feDropShadow dx="0" dy="0" stdDeviation="1.8" floodColor="#ef4444" floodOpacity="0.9" />
          </filter>

          {/* Faint blueprint dot grid */}
          <pattern id="fpGrid" width="4" height="4" patternUnits="userSpaceOnUse">
            <circle cx="0.4" cy="0.4" r="0.16" fill="var(--room-grid)" />
          </pattern>
        </defs>

        <rect x="0" y="0" width="100" height="100" fill="url(#fpGrid)" />

        {rooms.map((room) => {
          const pts = room.polygon.map(([x, y]) => [x * 100, y * 100] as [number, number]);
          const d = roundedPath(pts, 2.2);
          const cx = (pts.reduce((s, [x]) => s + x, 0) / pts.length) || 0;
          const cy = (pts.reduce((s, [, y]) => s + y, 0) / pts.length) || 0;
          const status = statusOf(room);
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
          const nameY = cy - (hasExtras ? 2.8 : 0);
          const climateY = cy + 0.4;
          const chipsY = cy + (climateLabel ? 4.4 : 3.2);
          const chipW = climateLabel ? climateLabel.length * 1.55 + 2.6 : 0;

          return (
            <g
              key={room.id}
              className={`fp-room${status === "alert" ? " animate-pulse-slow" : ""}`}
              onClick={() => onRoomClick(room)}
            >
              {/* Glass body */}
              <path
                d={d}
                fill={FILL[status]}
                stroke="var(--room-stroke)"
                strokeWidth={0.22}
                filter={FILTER[status]}
              />
              {/* Sheen highlight — specular top reflection, key to glass look */}
              <path d={d} fill="url(#fpSheen)" fillOpacity={0.9} className="pointer-events-none" />

              {/* Room name */}
              <text
                x={cx}
                y={nameY}
                textAnchor="middle"
                dominantBaseline="middle"
                fontSize="3.4"
                fontWeight="600"
                fill="var(--room-label)"
                className="pointer-events-none select-none"
                style={{ letterSpacing: "-0.03em" }}
              >
                {room.name}
              </text>

              {/* Climate pill */}
              {climateLabel && (
                <>
                  <rect
                    x={cx - chipW / 2}
                    y={climateY - 1.65}
                    width={chipW}
                    height={3.3}
                    rx={1.65}
                    fill="var(--room-climate-bg)"
                    className="pointer-events-none"
                  />
                  <text
                    x={cx}
                    y={climateY}
                    textAnchor="middle"
                    dominantBaseline="middle"
                    fontSize="2.5"
                    fontWeight="600"
                    fill="var(--room-climate)"
                    className="pointer-events-none select-none font-mono"
                  >
                    {climateLabel}
                  </text>
                </>
              )}

              {/* Device chips */}
              {roomPlacements.slice(0, 4).map((p, i) => {
                const { Icon, hex } = deviceMeta(p.kind);
                const n = Math.min(roomPlacements.length, 4);
                const spacing = 4.2;
                const chipCx = cx - ((n - 1) * spacing) / 2 + i * spacing;
                const r = 1.7;
                return (
                  <g key={p.id} className="pointer-events-none">
                    <circle
                      cx={chipCx}
                      cy={chipsY}
                      r={r}
                      fill="var(--room-chip-bg)"
                      stroke="var(--room-stroke)"
                      strokeWidth={0.2}
                    />
                    <Icon
                      x={chipCx - 1.05}
                      y={chipsY - 1.05}
                      width={2.1}
                      height={2.1}
                      color={hex}
                      strokeWidth={2}
                    />
                  </g>
                );
              })}
            </g>
          );
        })}
      </svg>
    </div>
  );
}
