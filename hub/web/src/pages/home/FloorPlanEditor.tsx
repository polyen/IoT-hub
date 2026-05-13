import { useCallback, useEffect, useRef, useState } from "react";
import { Circle, Group, Layer, Line, Stage, Text } from "react-konva";
import type { KonvaEventObject } from "konva/lib/Node";
import { useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { useFloorPlanStore } from "../../features/floorplan/floorplan-store";
import { useFloorPlan } from "../../features/floorplan/useFloorPlan";
import { api } from "../../lib/api";
import { Button } from "../../components/Button";
import { Spinner } from "../../components/Spinner";
import type { DeviceKind, DevicePlacement, Room } from "../../lib/types";

const KIND_ICON: Record<string, string> = {
  camera: "📷",
  light: "💡",
  lock: "🔒",
  thermostat: "🌡",
  relay: "⚡",
  sensor_pir: "👁",
  sensor_door: "🚪",
  sensor_dht: "💧",
  sensor_mq2: "💨",
  sensor_power: "🔌",
  speaker: "🔊",
};

const DEVICE_KINDS: DeviceKind[] = [
  "camera", "light", "lock", "thermostat", "relay",
  "sensor_pir", "sensor_door", "sensor_dht", "sensor_mq2", "sensor_power", "speaker",
];

const ROOM_FILL_COLORS = ["#1e3a5f", "#1a3a2a", "#3a1e2a", "#2a1e3a", "#3a2a1e"];
const SNAP = 0.05;

function snapN(n: number): number {
  return Math.round(n / SNAP) * SNAP;
}

function clampN(n: number): number {
  return Math.max(0, Math.min(1, n));
}

function pointInPolygon(px: number, py: number, poly: [number, number][]): boolean {
  let inside = false;
  for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) {
    const [xi, yi] = poly[i];
    const [xj, yj] = poly[j];
    if (yi > py !== yj > py && px < ((xj - xi) * (py - yi)) / (yj - yi) + xi) {
      inside = !inside;
    }
  }
  return inside;
}

interface PendingPoly {
  vertices: [number, number][];
  cursor: [number, number] | null;
}

export function FloorPlanEditor() {
  const containerRef = useRef<HTMLDivElement>(null);
  const [stageW, setStageW] = useState(800);
  const [stageH, setStageH] = useState(480);

  const {
    mode, setMode,
    pendingDeviceKind, setPendingDeviceKind,
    draft, setDraft, updateRooms, updatePlacements,
    isDirty, pushHistory, undo, redo, canUndo, canRedo,
    setEditMode,
  } = useFloorPlanStore();

  const { data, isLoading } = useFloorPlan();
  const qc = useQueryClient();

  // Seed draft once
  useEffect(() => {
    if (data && !draft) setDraft(data);
  }, [data, draft, setDraft]);

  // Resize observer — keep stage sized to container
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const obs = new ResizeObserver(() => {
      const plan = draft?.floor_plans[0];
      const ratio = plan ? plan.height / plan.width : 0.6;
      const w = el.clientWidth;
      setStageW(w);
      setStageH(Math.round(w * ratio));
    });
    obs.observe(el);
    return () => obs.disconnect();
  }, [draft]);

  const [pending, setPending] = useState<PendingPoly | null>(null);
  const [newRoomState, setNewRoomState] = useState<{ polygon: [number, number][]; name: string } | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  const toNorm = useCallback(
    (kx: number, ky: number): [number, number] => [
      clampN(snapN(kx / stageW)),
      clampN(snapN(ky / stageH)),
    ],
    [stageW, stageH],
  );

  // Keyboard shortcuts
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setPending(null);
        setSelectedId(null);
      }
      if (e.key === "Enter" && pending && pending.vertices.length >= 3) {
        finishPolygon(pending.vertices);
      }
      if ((e.metaKey || e.ctrlKey) && !e.shiftKey && e.key === "z") {
        e.preventDefault();
        undo();
      }
      if ((e.metaKey || e.ctrlKey) && (e.key === "y" || (e.shiftKey && e.key === "z"))) {
        e.preventDefault();
        redo();
      }
      if ((e.key === "Delete" || e.key === "Backspace") && selectedId) {
        deleteSelected();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pending, selectedId, undo, redo]);

  function finishPolygon(vertices: [number, number][]) {
    setPending(null);
    setNewRoomState({ polygon: vertices, name: "Нова кімната" });
  }

  function confirmNewRoom(name: string) {
    if (!newRoomState || !draft) return;
    pushHistory();
    const room: Room = {
      id: crypto.randomUUID(),
      floor_plan_id: draft.floor_plans[0].id,
      name: name.trim() || "Кімната",
      type: "other",
      polygon: newRoomState.polygon,
      color: null,
      order: draft.rooms.length,
    };
    updateRooms([...draft.rooms, room]);
    setNewRoomState(null);
    setMode("select");
  }

  function deleteSelected() {
    if (!draft || !selectedId) return;
    pushHistory();
    const isRoom = draft.rooms.some((r) => r.id === selectedId);
    if (isRoom) {
      updateRooms(draft.rooms.filter((r) => r.id !== selectedId));
      updatePlacements(draft.placements.filter((p) => p.room_id !== selectedId));
    } else {
      updatePlacements(draft.placements.filter((p) => p.id !== selectedId));
    }
    setSelectedId(null);
  }

  async function handleSave() {
    if (!draft) return;
    try {
      await api.put("/api/floorplan", draft);
      await qc.invalidateQueries({ queryKey: ["floorplan"] });
      toast.success("Збережено");
      setDraft(draft); // reset dirty + history
      setEditMode(false);
    } catch {
      // api module already toasted the error
    }
  }

  // ── Stage event handlers ──────────────────────────────────────────────────

  function handleStageClick(e: KonvaEventObject<MouseEvent>) {
    // Only handle clicks that hit the stage background, not child shapes
    if (e.target !== e.target.getStage()) return;

    const pos = e.target.getStage()!.getPointerPosition()!;
    const [nx, ny] = [snapN(pos.x / stageW), snapN(pos.y / stageH)];

    if (mode === "select") {
      setSelectedId(null);
      return;
    }

    if (mode === "add-room") {
      const snapped: [number, number] = [clampN(nx), clampN(ny)];
      setPending((prev) => ({
        vertices: prev ? [...prev.vertices, snapped] : [snapped],
        cursor: snapped,
      }));
      return;
    }

    if (mode === "add-device" && pendingDeviceKind && draft) {
      const roomUnder = draft.rooms.find(
        (r) => r.floor_plan_id === draft.floor_plans[0]?.id && pointInPolygon(nx, ny, r.polygon),
      );
      if (!roomUnder) {
        toast.warning("Клацни всередині кімнати");
        return;
      }
      pushHistory();
      const placement: DevicePlacement = {
        id: crypto.randomUUID(),
        room_id: roomUnder.id,
        device_id: `${pendingDeviceKind}_${Date.now()}`,
        kind: pendingDeviceKind,
        x: clampN(nx),
        y: clampN(ny),
        label: null,
        config: {},
      };
      updatePlacements([...draft.placements, placement]);
    }
  }

  function handleStageMouseMove(e: KonvaEventObject<MouseEvent>) {
    if (mode !== "add-room" || !pending) return;
    const pos = e.target.getStage()!.getPointerPosition()!;
    const cursor: [number, number] = [clampN(snapN(pos.x / stageW)), clampN(snapN(pos.y / stageH))];
    setPending((prev) => (prev ? { ...prev, cursor } : null));
  }

  function handleStageDblClick(e: KonvaEventObject<MouseEvent>) {
    if (mode !== "add-room" || !pending || pending.vertices.length < 3) return;
    e.evt.preventDefault();
    // Remove the extra vertex added by the final click before dblclick fires
    finishPolygon(pending.vertices.slice(0, -1).length >= 3 ? pending.vertices.slice(0, -1) : pending.vertices);
  }

  // ── Render guards ──────────────────────────────────────────────────────────

  if (isLoading || !draft) {
    return (
      <div className="flex justify-center py-16">
        <Spinner className="h-8 w-8" />
      </div>
    );
  }

  const plan = draft.floor_plans[0];
  if (!plan) return null;

  const rooms = draft.rooms.filter((r) => r.floor_plan_id === plan.id);
  const inSelectMode = mode === "select";

  // ── Toolbar hint text ──────────────────────────────────────────────────────
  const hint =
    mode === "add-room" && !pending
      ? "Клацай щоб додати вершини. Подвійний клік або Enter — завершити (мін. 3). Esc — скасувати."
      : mode === "add-room" && pending
        ? `${pending.vertices.length} верш.  — ще ${Math.max(0, 3 - pending.vertices.length)} потрібно`
        : mode === "add-device" && !pendingDeviceKind
          ? "Обери тип пристрою нижче."
          : mode === "add-device"
            ? `Клацни всередині кімнати щоб розмістити ${KIND_ICON[pendingDeviceKind!]}`
            : selectedId
              ? "Перетягни для переміщення. Del — видалити."
              : "Клацни кімнату або пристрій для вибору.";

  return (
    <div className="space-y-2">
      {/* ── Toolbar ── */}
      <div className="flex flex-wrap items-center gap-2">
        {/* Mode switcher */}
        <div className="flex gap-0.5 rounded-lg border border-slate-700 bg-slate-800 p-1">
          {(["select", "add-room", "add-device"] as const).map((m) => (
            <button
              key={m}
              onClick={() => { setMode(m); setPending(null); setSelectedId(null); }}
              className={[
                "rounded px-3 py-1 text-xs font-medium transition-colors",
                mode === m
                  ? "bg-blue-600 text-white"
                  : "text-slate-400 hover:bg-slate-700 hover:text-white",
              ].join(" ")}
            >
              {m === "select" ? "Вибір" : m === "add-room" ? "+ Кімната" : "+ Пристрій"}
            </button>
          ))}
        </div>

        {/* Undo / Redo */}
        <button
          onClick={undo}
          disabled={!canUndo}
          title="Скасувати (Ctrl+Z)"
          className="rounded p-1.5 text-slate-400 hover:bg-slate-700 hover:text-white disabled:opacity-30"
        >
          ↩
        </button>
        <button
          onClick={redo}
          disabled={!canRedo}
          title="Повторити (Ctrl+Y)"
          className="rounded p-1.5 text-slate-400 hover:bg-slate-700 hover:text-white disabled:opacity-30"
        >
          ↪
        </button>

        {/* Delete selected */}
        {selectedId && (
          <button
            onClick={deleteSelected}
            className="rounded px-2.5 py-1 text-xs font-medium bg-red-900/50 text-red-300 hover:bg-red-800 hover:text-white"
          >
            Видалити
          </button>
        )}

        {/* Save / Cancel */}
        <div className="ml-auto flex gap-2">
          <Button
            size="sm"
            variant="secondary"
            onClick={() => { setEditMode(false); setMode("select"); }}
          >
            Скасувати
          </Button>
          <Button size="sm" variant="primary" onClick={handleSave} disabled={!isDirty}>
            Зберегти
          </Button>
        </div>
      </div>

      {/* ── Device palette ── */}
      {mode === "add-device" && (
        <div className="flex flex-wrap gap-1 rounded-lg border border-slate-700 bg-slate-800/60 p-2">
          {DEVICE_KINDS.map((k) => (
            <button
              key={k}
              onClick={() => setPendingDeviceKind(k)}
              className={[
                "flex items-center gap-1 rounded px-2 py-1 text-xs transition-colors",
                pendingDeviceKind === k
                  ? "bg-blue-600 text-white"
                  : "bg-slate-700 text-slate-300 hover:bg-slate-600",
              ].join(" ")}
            >
              <span aria-hidden>{KIND_ICON[k]}</span>
              <span>{k.replace("sensor_", "")}</span>
            </button>
          ))}
        </div>
      )}

      {/* ── Hint ── */}
      <p className="text-xs text-slate-500 min-h-[1.25rem]">{hint}</p>

      {/* ── Canvas ── */}
      <div
        ref={containerRef}
        className="rounded-xl overflow-hidden border border-slate-700 bg-slate-900 touch-none"
        style={{ cursor: mode === "add-room" || mode === "add-device" ? "crosshair" : "default" }}
      >
        <Stage
          width={stageW}
          height={stageH}
          onClick={handleStageClick}
          onMouseMove={handleStageMouseMove}
          onDblClick={handleStageDblClick}
        >
          <Layer>
            {/* Grid — 5% lines */}
            {Array.from({ length: 19 }, (_, i) => (i + 1) / 20).map((n) => [
              <Line
                key={`gv${n}`}
                points={[n * stageW, 0, n * stageW, stageH]}
                stroke="#1e293b"
                strokeWidth={0.5}
                listening={false}
              />,
              <Line
                key={`gh${n}`}
                points={[0, n * stageH, stageW, n * stageH]}
                stroke="#1e293b"
                strokeWidth={0.5}
                listening={false}
              />,
            ])}

            {/* ── Rooms ── */}
            {rooms.map((room, idx) => {
              const pts = room.polygon.flatMap(([x, y]) => [x * stageW, y * stageH]);
              const cx = (room.polygon.reduce((s, [x]) => s + x, 0) / room.polygon.length) * stageW;
              const cy = (room.polygon.reduce((s, [, y]) => s + y, 0) / room.polygon.length) * stageH;
              const isSelected = selectedId === room.id;
              const fill = room.color ?? ROOM_FILL_COLORS[idx % ROOM_FILL_COLORS.length];
              const roomPlacements = draft.placements.filter((p) => p.room_id === room.id);

              return (
                <Group key={room.id}>
                  <Line
                    points={pts}
                    closed
                    fill={fill}
                    opacity={0.8}
                    stroke={isSelected ? "#60a5fa" : "#475569"}
                    strokeWidth={isSelected ? 2 : 1}
                    listening={inSelectMode}
                    draggable={inSelectMode}
                    onClick={(e) => {
                      e.cancelBubble = true;
                      setSelectedId(room.id);
                    }}
                    onDragStart={() => pushHistory()}
                    onDragEnd={(e) => {
                      const dx = snapN(e.target.x() / stageW);
                      const dy = snapN(e.target.y() / stageH);
                      e.target.position({ x: 0, y: 0 });
                      const newPoly: [number, number][] = room.polygon.map(([x, y]) => [
                        clampN(x + dx),
                        clampN(y + dy),
                      ]);
                      updateRooms(draft.rooms.map((r) => (r.id === room.id ? { ...r, polygon: newPoly } : r)));
                    }}
                  />

                  {/* Room label */}
                  <Text
                    x={cx - 60}
                    y={cy - (roomPlacements.length > 0 ? 14 : 0) - 8}
                    width={120}
                    text={room.name}
                    fontSize={13}
                    fontFamily="system-ui, sans-serif"
                    fill="#e2e8f0"
                    align="center"
                    listening={false}
                  />

                  {/* Device icons in room */}
                  {roomPlacements.slice(0, 6).map((p, i) => (
                    <Text
                      key={p.id}
                      x={cx - (Math.min(roomPlacements.length, 6) * 14) / 2 + i * 14}
                      y={cy + 4}
                      text={KIND_ICON[p.kind] ?? "⚙"}
                      fontSize={12}
                      align="center"
                      listening={false}
                    />
                  ))}

                  {/* Vertex handles (only when selected in select mode) */}
                  {isSelected &&
                    room.polygon.map(([nx, ny], vi) => (
                      <Circle
                        key={vi}
                        x={nx * stageW}
                        y={ny * stageH}
                        radius={5}
                        fill="#3b82f6"
                        stroke="#ffffff"
                        strokeWidth={1.5}
                        draggable
                        onDragStart={() => pushHistory()}
                        onDragEnd={(e) => {
                          const [nnx, nny] = toNorm(e.target.x(), e.target.y());
                          const newPoly: [number, number][] = room.polygon.map((pt, i) =>
                            i === vi ? [nnx, nny] : pt,
                          );
                          updateRooms(draft.rooms.map((r) => (r.id === room.id ? { ...r, polygon: newPoly } : r)));
                        }}
                      />
                    ))}
                </Group>
              );
            })}

            {/* ── Device placements ── */}
            {draft.placements.map((p) => {
              const isSelected = selectedId === p.id;
              return (
                <Group
                  key={p.id}
                  x={p.x * stageW}
                  y={p.y * stageH}
                  listening={inSelectMode}
                  draggable={inSelectMode}
                  onClick={(e) => {
                    e.cancelBubble = true;
                    setSelectedId(p.id);
                  }}
                  onDragStart={() => pushHistory()}
                  onDragEnd={(e) => {
                    const [nx, ny] = toNorm(e.target.x(), e.target.y());
                    updatePlacements(draft.placements.map((pl) => (pl.id === p.id ? { ...pl, x: nx, y: ny } : pl)));
                  }}
                >
                  <Circle
                    radius={12}
                    fill={isSelected ? "#1d4ed8" : "#1e3a5f"}
                    stroke={isSelected ? "#60a5fa" : "#3b82f6"}
                    strokeWidth={isSelected ? 2 : 1}
                  />
                  <Text
                    text={KIND_ICON[p.kind] ?? "⚙"}
                    fontSize={13}
                    offsetX={7}
                    offsetY={7}
                    listening={false}
                  />
                  {p.label && (
                    <Text
                      text={p.label}
                      fontSize={9}
                      fill="#94a3b8"
                      offsetX={30}
                      y={14}
                      width={60}
                      align="center"
                      listening={false}
                    />
                  )}
                </Group>
              );
            })}

            {/* ── In-progress polygon ── */}
            {pending && pending.vertices.length > 0 && (
              <Group listening={false}>
                {pending.vertices.length >= 2 && (
                  <Line
                    points={pending.vertices.flatMap(([x, y]) => [x * stageW, y * stageH])}
                    stroke="#f59e0b"
                    strokeWidth={2}
                    dash={[5, 3]}
                  />
                )}
                {/* Closing line back to first vertex */}
                {pending.vertices.length >= 3 && pending.cursor && (
                  <Line
                    points={[
                      pending.cursor[0] * stageW,
                      pending.cursor[1] * stageH,
                      pending.vertices[0][0] * stageW,
                      pending.vertices[0][1] * stageH,
                    ]}
                    stroke="#f59e0b"
                    strokeWidth={1}
                    dash={[3, 4]}
                    opacity={0.4}
                  />
                )}
                {/* Preview line to cursor */}
                {pending.cursor && (
                  <Line
                    points={[
                      pending.vertices[pending.vertices.length - 1][0] * stageW,
                      pending.vertices[pending.vertices.length - 1][1] * stageH,
                      pending.cursor[0] * stageW,
                      pending.cursor[1] * stageH,
                    ]}
                    stroke="#f59e0b"
                    strokeWidth={1.5}
                    dash={[2, 3]}
                    opacity={0.7}
                  />
                )}
                {/* Vertex dots */}
                {pending.vertices.map(([nx, ny], i) => (
                  <Circle key={i} x={nx * stageW} y={ny * stageH} radius={4} fill="#f59e0b" />
                ))}
                {/* Highlight first vertex as close target */}
                {pending.vertices.length >= 3 && (
                  <Circle
                    x={pending.vertices[0][0] * stageW}
                    y={pending.vertices[0][1] * stageH}
                    radius={7}
                    fill="transparent"
                    stroke="#f59e0b"
                    strokeWidth={2}
                  />
                )}
              </Group>
            )}
          </Layer>
        </Stage>
      </div>

      {/* ── New room name dialog ── */}
      {newRoomState && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60">
          <div className="w-80 space-y-4 rounded-xl border border-slate-700 bg-slate-800 p-6 shadow-xl">
            <h3 className="font-semibold text-white">Назва кімнати</h3>
            <input
              autoFocus
              className="w-full rounded border border-slate-600 bg-slate-700 px-3 py-2 text-sm text-white focus:outline-none focus:ring-2 focus:ring-blue-500"
              value={newRoomState.name}
              onChange={(e) => setNewRoomState({ ...newRoomState, name: e.target.value })}
              onKeyDown={(e) => {
                if (e.key === "Enter") confirmNewRoom(newRoomState.name);
                if (e.key === "Escape") setNewRoomState(null);
              }}
              placeholder="Наприклад: Спальня"
            />
            <div className="flex justify-end gap-2">
              <Button size="sm" variant="secondary" onClick={() => setNewRoomState(null)}>
                Скасувати
              </Button>
              <Button size="sm" variant="primary" onClick={() => confirmNewRoom(newRoomState.name)}>
                Додати
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
