import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Circle, Group, Layer, Line, Stage, Text } from "react-konva";
import type { KonvaEventObject } from "konva/lib/Node";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { useFloorPlanStore } from "../../features/floorplan/floorplan-store";
import { useFloorPlan } from "../../features/floorplan/useFloorPlan";
import { api } from "../../lib/api";
import { Button } from "../../components/Button";
import { Spinner } from "../../components/Spinner";
import { RoomAliasesPanel } from "./RoomAliasesPanel";
import { DeviceIcon, deviceMeta, DEVICE_KINDS } from "../../lib/deviceIcons";
import type { DeviceKind, DevicePlacement, Room } from "../../lib/types";

interface DiscoveredDevice {
  device_id: string;
  kind_guess: DeviceKind;
  last_seen: string | null;
  source: "mqtt" | "redis";
}

const ROOM_FILL_COLORS = ["#0c1a30", "#091420", "#130a18", "#091614", "#180e0a"];

// Canvas color constants (hardcoded for Konva — cannot use CSS vars in canvas props)
const C = {
  grid: "#0d1824",
  roomStroke: "#29374e",
  roomStrokeSelected: "#6366f1",
  roomText: "#c8c4b8",
  vertexFill: "#6366f1",
  vertexStroke: "#f2dfa0",
  pendingStroke: "#6366f1",
  pendingStrokeClose: "#e8c95a",
  deviceFill: "#08132a",
  deviceFillSelected: "#1a2840",
  deviceStroke: "#1e3050",
  deviceStrokeSelected: "#6366f1",
  deviceLabel: "#7a8ba8",
} as const;
const SNAP = 0.05;

function uuid4(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) return crypto.randomUUID();
  // Fallback for HTTP (non-secure) contexts — crypto.getRandomValues is always available
  return "10000000-1000-4000-8000-100000000000".replace(/[018]/g, (c) => {
    const n = parseInt(c, 10);
    return (n ^ (crypto.getRandomValues(new Uint8Array(1))[0] & (15 >> (n / 4)))).toString(16);
  });
}

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
  const [stageW, setStageW] = useState(0);
  const [stageH, setStageH] = useState(0);

  const {
    mode, setMode,
    pendingDeviceKind, setPendingDeviceKind,
    draft, setDraft, updateRooms, updatePlacements,
    isDirty, pushHistory, undo, redo, canUndo, canRedo,
    setEditMode,
  } = useFloorPlanStore();

  // Prefill device_id when placing a discovered device
  const [pendingDeviceId, setPendingDeviceId] = useState<string | null>(null);

  const { data: discoveredDevices } = useQuery<DiscoveredDevice[]>({
    queryKey: ["discovered-devices"],
    queryFn: () => api.get<DiscoveredDevice[]>("/api/floorplan/devices/discovered"),
    enabled: mode === "add-device",
    staleTime: 30_000,
  });

  const { data, isLoading } = useFloorPlan();
  const qc = useQueryClient();

  // Seed draft once
  useEffect(() => {
    if (data && !draft) setDraft(data);
  }, [data, draft, setDraft]);

  // Read actual container dimensions synchronously after mount (before first paint).
  // This prevents Konva from ever mounting with a 0-size canvas.
  useLayoutEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const plan = draft?.floor_plans[0];
    const ratio = plan ? plan.height / plan.width : 0.6;
    const w = el.getBoundingClientRect().width || el.clientWidth;
    if (w > 0) {
      setStageW(w);
      setStageH(Math.max(1, Math.round(w * ratio)));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []); // once on mount

  // Resize observer — keep stage sized to container on subsequent changes
  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const obs = new ResizeObserver(() => {
      const plan = draft?.floor_plans[0];
      const ratio = plan ? plan.height / plan.width : 0.6;
      const w = el.clientWidth;
      if (w <= 0) return;
      setStageW(w);
      setStageH(Math.max(1, Math.round(w * ratio)));
    });
    obs.observe(el);
    return () => obs.disconnect();
  }, [draft]);

  const [pending, setPending] = useState<PendingPoly | null>(null);
  // Room drawing: rectangle drag (default, easy) vs freehand polygon (advanced).
  const [roomDraw, setRoomDraw] = useState<"rect" | "poly">("rect");
  const [rect, setRect] = useState<{ start: [number, number]; now: [number, number] } | null>(null);
  const [newRoomState, setNewRoomState] = useState<{ polygon: [number, number][]; name: string } | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editPlacement, setEditPlacement] = useState<{ id: string; label: string; device_id: string; rtsp_url: string; rtsp_hd_url: string } | null>(null);

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
        setRect(null);
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
      id: uuid4(),
      floor_plan_id: draft.floor_plans[0].id,
      name: name.trim() || "Кімната",
      slug: "",        // set by backend on PUT /api/floorplan
      type: "other",
      polygon: newRoomState.polygon,
      color: null,
      order: draft.rooms.length,
      aliases: [],
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

  function saveEditPlacement() {
    if (!editPlacement || !draft) return;
    updatePlacements(
      draft.placements.map((p) => {
        if (p.id !== editPlacement.id) return p;
        const config = { ...p.config };
        if (p.kind === "camera") {
          const url = editPlacement.rtsp_url.trim();
          if (url) config.rtsp_url = url;
          else delete config.rtsp_url;
          const hdUrl = editPlacement.rtsp_hd_url.trim();
          if (hdUrl) config.rtsp_hd_url = hdUrl;
          else delete config.rtsp_hd_url;
        }
        return { ...p, label: editPlacement.label || null, device_id: editPlacement.device_id.trim(), config };
      }),
    );
    setEditPlacement(null);
  }

  async function handleSave() {
    if (!draft) return;
    try {
      await api.put("/api/floorplan", draft, false, 30_000);
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
      if (roomDraw !== "poly") return; // rectangle mode uses mouse down/up drag
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
        id: uuid4(),
        room_id: roomUnder.id,
        device_id: pendingDeviceId ?? `${pendingDeviceKind}_${Date.now()}`,
        kind: pendingDeviceKind,
        x: clampN(nx),
        y: clampN(ny),
        label: null,
        config: {},
        aliases: [],
        controllable: false,
        actions: [],
      };
      updatePlacements([...draft.placements, placement]);
      // Reset discovered device selection after placing
      if (pendingDeviceId) setPendingDeviceId(null);
    }
  }

  function handleStageMouseDown(e: KonvaEventObject<MouseEvent>) {
    if (mode !== "add-room" || roomDraw !== "rect") return;
    if (e.target !== e.target.getStage()) return;
    const pos = e.target.getStage()!.getPointerPosition()!;
    const p: [number, number] = [clampN(snapN(pos.x / stageW)), clampN(snapN(pos.y / stageH))];
    setRect({ start: p, now: p });
  }

  function handleStageMouseMove(e: KonvaEventObject<MouseEvent>) {
    const pos = e.target.getStage()!.getPointerPosition()!;
    const p: [number, number] = [clampN(snapN(pos.x / stageW)), clampN(snapN(pos.y / stageH))];
    if (mode === "add-room" && roomDraw === "rect" && rect) {
      setRect((r) => (r ? { ...r, now: p } : null));
      return;
    }
    if (mode !== "add-room" || !pending) return;
    setPending((prev) => (prev ? { ...prev, cursor: p } : null));
  }

  function handleStageMouseUp() {
    if (mode !== "add-room" || roomDraw !== "rect" || !rect) return;
    const [sx, sy] = rect.start;
    const [ex, ey] = rect.now;
    const x0 = Math.min(sx, ex), y0 = Math.min(sy, ey);
    const x1 = Math.max(sx, ex), y1 = Math.max(sy, ey);
    setRect(null);
    if (x1 - x0 < 0.04 || y1 - y0 < 0.04) return; // ignore taps / tiny drags
    finishPolygon([[x0, y0], [x1, y0], [x1, y1], [x0, y1]]);
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
    mode === "add-room"
      ? roomDraw === "rect"
        ? "Перетягни прямокутник, щоб створити кімнату. Esc — скасувати."
        : pending
          ? `${pending.vertices.length} верш. — подвійний клік або Enter завершує (мін. 3)`
          : "Клацай, щоб додати вершини. Подвійний клік / Enter — завершити. Esc — скасувати."
      : mode === "add-device" && !pendingDeviceKind
        ? "Обери тип пристрою нижче."
        : mode === "add-device"
          ? `Клацни всередині кімнати щоб розмістити «${deviceMeta(pendingDeviceKind!).label}»`
          : selectedId
            ? "Перетягни для переміщення. Del — видалити."
            : "Клацни кімнату або пристрій для вибору.";

  const modeLabel: Record<string, string> = {
    select: "Вибір",
    "add-room": "+ Кімната",
    "add-device": "+ Пристрій",
  };

  return (
    <div className="space-y-3">
      {/* ── Toolbar ── */}
      <div className="flex flex-wrap items-center gap-2">
        {/* Mode switcher */}
        <div
          className="flex gap-1 rounded-xl p-1"
          style={{ background: "var(--raised)", border: "1px solid var(--border)" }}
        >
          {(["select", "add-room", "add-device"] as const).map((m) => (
            <button
              key={m}
              onClick={() => { setMode(m); setPending(null); setRect(null); setSelectedId(null); }}
              className={[
                "rounded-lg px-3 py-1.5 text-xs font-medium transition-all duration-150",
                mode === m
                  ? "bg-primary-600 text-white shadow-gold"
                  : "text-[color:var(--text-muted)] hover:text-[color:var(--text)] hover:bg-[color:var(--card)]",
              ].join(" ")}
            >
              {modeLabel[m]}
            </button>
          ))}
        </div>

        {/* Undo / Redo */}
        <button
          onClick={undo}
          disabled={!canUndo}
          title="Скасувати (Ctrl+Z)"
          className="rounded-lg p-1.5 text-sm text-[color:var(--text-muted)] hover:text-[color:var(--text)] hover:bg-[color:var(--raised)] transition-colors disabled:opacity-25"
        >
          ↩
        </button>
        <button
          onClick={redo}
          disabled={!canRedo}
          title="Повторити (Ctrl+Y)"
          className="rounded-lg p-1.5 text-sm text-[color:var(--text-muted)] hover:text-[color:var(--text)] hover:bg-[color:var(--raised)] transition-colors disabled:opacity-25"
        >
          ↪
        </button>

        {/* Context actions for selected element */}
        {selectedId && draft?.placements.some((p) => p.id === selectedId) && (
          <button
            onClick={() => {
              const p = draft!.placements.find((pl) => pl.id === selectedId)!;
              setEditPlacement({ id: p.id, label: p.label ?? "", device_id: p.device_id, rtsp_url: (p.config?.rtsp_url as string) ?? "", rtsp_hd_url: (p.config?.rtsp_hd_url as string) ?? "" });
            }}
            className="rounded-lg px-2.5 py-1.5 text-xs font-medium transition-colors"
            style={{ background: "var(--primary-dim)", color: "var(--primary)", border: "1px solid rgba(99,102,241,0.2)" }}
          >
            Редагувати
          </button>
        )}
        {selectedId && (
          <button
            onClick={deleteSelected}
            className="rounded-lg px-2.5 py-1.5 text-xs font-medium bg-red-500/10 text-red-400 border border-red-500/20 hover:bg-red-500/20 transition-colors"
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

      {/* ── Room draw-mode toggle ── */}
      {mode === "add-room" && (
        <div
          className="flex flex-wrap items-center gap-3 rounded-xl p-3 animate-fade-in"
          style={{ border: "1px solid var(--border)", background: "var(--card)" }}
        >
          <span className="text-[9px] font-mono font-medium uppercase tracking-[0.18em] text-[color:var(--text-faint)]">
            Форма кімнати
          </span>
          <div className="flex gap-1 rounded-lg p-1" style={{ background: "var(--raised)" }}>
            {([
              ["rect", "Прямокутник"],
              ["poly", "Полігон"],
            ] as const).map(([v, label]) => (
              <button
                key={v}
                onClick={() => { setRoomDraw(v); setPending(null); setRect(null); }}
                className={[
                  "rounded-md px-3 py-1 text-xs font-medium transition-all",
                  roomDraw === v
                    ? "bg-primary-600 text-white"
                    : "text-[color:var(--text-muted)] hover:text-[color:var(--text)]",
                ].join(" ")}
              >
                {label}
              </button>
            ))}
          </div>
          <span className="text-xs text-[color:var(--text-faint)]">
            {roomDraw === "rect" ? "перетягни прямокутник на плані" : "клацай вершини, подвійний клік завершує"}
          </span>
        </div>
      )}

      {/* ── Device palette ── */}
      {mode === "add-device" && (
        <div className="space-y-2 animate-fade-in">
          {/* Generic kinds */}
          <div
            className="rounded-xl p-3 space-y-2"
            style={{ border: "1px solid var(--border)", background: "var(--card)" }}
          >
            <p className="text-[9px] font-mono font-medium uppercase tracking-[0.18em] text-[color:var(--text-faint)]">
              Тип пристрою
            </p>
            <div className="grid grid-cols-4 gap-1 sm:grid-cols-6">
              {DEVICE_KINDS.map((k) => (
                <button
                  key={k}
                  onClick={() => { setPendingDeviceKind(k); setPendingDeviceId(null); }}
                  className={[
                    "flex flex-col items-center gap-1 rounded-lg py-2 px-1 text-center transition-all duration-150",
                    pendingDeviceKind === k && !pendingDeviceId
                      ? "bg-primary-600/20 text-primary-300"
                      : "text-[color:var(--text-muted)] hover:text-[color:var(--text)] hover:bg-[color:var(--raised)]",
                  ].join(" ")}
                  style={pendingDeviceKind === k && !pendingDeviceId ? { border: "1px solid rgba(99,102,241,0.35)" } : { border: "1px solid transparent" }}
                >
                  <DeviceIcon kind={k} size={18} className={deviceMeta(k).text} />
                  <span className="text-[9px] leading-tight">{deviceMeta(k).label}</span>
                </button>
              ))}
            </div>
          </div>

          {/* Discovered devices from MQTT */}
          {discoveredDevices && discoveredDevices.length > 0 && (
            <div
              className="rounded-xl p-3 space-y-2"
              style={{ border: "1px solid var(--border-subtle)", background: "var(--card)" }}
            >
              <p className="text-[9px] font-mono font-medium uppercase tracking-[0.18em] text-[color:var(--text-faint)]">
                Виявлені пристрої (MQTT)
              </p>
              <div className="flex flex-wrap gap-1.5">
                {discoveredDevices.map((d) => (
                  <button
                    key={d.device_id}
                    onClick={() => { setPendingDeviceKind(d.kind_guess); setPendingDeviceId(d.device_id); }}
                    title={`${d.source} · ${d.device_id}`}
                    className={[
                      "flex items-center gap-1.5 rounded-lg px-2.5 py-1.5 text-xs transition-all duration-150",
                      pendingDeviceId === d.device_id
                        ? "bg-primary-600/20 text-primary-300"
                        : "text-[color:var(--text-muted)] hover:text-[color:var(--text)] hover:bg-[color:var(--raised)]",
                    ].join(" ")}
                    style={pendingDeviceId === d.device_id ? { border: "1px solid rgba(99,102,241,0.35)" } : { border: "1px solid var(--border)" }}
                  >
                    <DeviceIcon kind={d.kind_guess} size={14} className={deviceMeta(d.kind_guess).text} />
                    <span className="font-mono max-w-[120px] truncate">{d.device_id}</span>
                  </button>
                ))}
              </div>
            </div>
          )}
        </div>
      )}

      {/* ── Room aliases panel (shown when a room is selected in select mode) ── */}
      {inSelectMode && selectedId && draft?.rooms.some((r) => r.id === selectedId) && (() => {
        const room = draft.rooms.find((r) => r.id === selectedId)!;
        return (
          <RoomAliasesPanel
            room={room}
            onUpdate={(updated) => {
              pushHistory();
              updateRooms(draft.rooms.map((r) => r.id === updated.id ? updated : r));
            }}
          />
        );
      })()}

      {/* ── Hint ── */}
      <p className="text-xs font-mono text-[color:var(--text-faint)] min-h-[1.25rem] px-0.5">{hint}</p>

      {/* ── Canvas ── */}
      <div
        ref={containerRef}
        className="rounded-xl overflow-hidden touch-none"
        style={{
          border: "1px solid var(--border)",
          background: "#020817",
          cursor: mode === "add-room" || mode === "add-device" ? "crosshair" : "default",
        }}
      >
        {stageW === 0 && (
          <div className="flex items-center justify-center py-16">
            <span className="text-xs font-mono text-[color:var(--text-faint)]">Ініціалізація…</span>
          </div>
        )}
        {stageW > 0 && <Stage
          width={stageW}
          height={stageH}
          onClick={handleStageClick}
          onMouseDown={handleStageMouseDown}
          onMouseMove={handleStageMouseMove}
          onMouseUp={handleStageMouseUp}
          onDblClick={handleStageDblClick}
        >
          <Layer>
            {/* Grid — 5% lines */}
            {Array.from({ length: 19 }, (_, i) => (i + 1) / 20).map((n) => [
              <Line
                key={`gv${n}`}
                points={[n * stageW, 0, n * stageW, stageH]}
                stroke={C.grid}
                strokeWidth={0.5}
                listening={false}
              />,
              <Line
                key={`gh${n}`}
                points={[0, n * stageH, stageW, n * stageH]}
                stroke={C.grid}
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
                    stroke={isSelected ? C.roomStrokeSelected : C.roomStroke}
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
                    fontFamily='"DM Sans", system-ui, sans-serif'
                    fill={C.roomText}
                    align="center"
                    listening={false}
                  />

                  {/* Device indicator dots in room (colour = device tone) */}
                  {roomPlacements.slice(0, 6).map((p, i) => (
                    <Circle
                      key={p.id}
                      x={cx - (Math.min(roomPlacements.length, 6) * 12) / 2 + i * 12 + 6}
                      y={cy + 8}
                      radius={4}
                      fill={deviceMeta(p.kind).hex}
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
                        fill={C.vertexFill}
                        stroke={C.vertexStroke}
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
                  onDblClick={(e) => {
                    e.cancelBubble = true;
                    setEditPlacement({ id: p.id, label: p.label ?? "", device_id: p.device_id, rtsp_url: (p.config?.rtsp_url as string) ?? "", rtsp_hd_url: (p.config?.rtsp_hd_url as string) ?? "" });
                  }}
                  onDragStart={() => pushHistory()}
                  onDragEnd={(e) => {
                    const [nx, ny] = toNorm(e.target.x(), e.target.y());
                    updatePlacements(draft.placements.map((pl) => (pl.id === p.id ? { ...pl, x: nx, y: ny } : pl)));
                  }}
                >
                  <Circle
                    radius={12}
                    fill={isSelected ? C.deviceFillSelected : C.deviceFill}
                    stroke={isSelected ? C.deviceStrokeSelected : deviceMeta(p.kind).hex}
                    strokeWidth={isSelected ? 2.5 : 2}
                  />
                  <Circle radius={4.5} fill={deviceMeta(p.kind).hex} listening={false} />
                  {p.label && (
                    <Text
                      text={p.label}
                      fontSize={9}
                      fill={C.deviceLabel}
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

            {/* ── In-progress rectangle drag ── */}
            {rect && (
              <Line
                points={[
                  rect.start[0] * stageW, rect.start[1] * stageH,
                  rect.now[0] * stageW, rect.start[1] * stageH,
                  rect.now[0] * stageW, rect.now[1] * stageH,
                  rect.start[0] * stageW, rect.now[1] * stageH,
                ]}
                closed
                stroke={C.pendingStroke}
                strokeWidth={2}
                dash={[6, 4]}
                fill="rgba(99,102,241,0.12)"
                listening={false}
              />
            )}

            {/* ── In-progress polygon ── */}
            {pending && pending.vertices.length > 0 && (
              <Group listening={false}>
                {pending.vertices.length >= 2 && (
                  <Line
                    points={pending.vertices.flatMap(([x, y]) => [x * stageW, y * stageH])}
                    stroke={C.pendingStroke}
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
                    stroke={C.pendingStroke}
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
                    stroke={C.pendingStroke}
                    strokeWidth={1.5}
                    dash={[2, 3]}
                    opacity={0.7}
                  />
                )}
                {/* Vertex dots */}
                {pending.vertices.map(([nx, ny], i) => (
                  <Circle key={i} x={nx * stageW} y={ny * stageH} radius={4} fill={C.pendingStroke} />
                ))}
                {/* Highlight first vertex as close target */}
                {pending.vertices.length >= 3 && (
                  <Circle
                    x={pending.vertices[0][0] * stageW}
                    y={pending.vertices[0][1] * stageH}
                    radius={7}
                    fill="transparent"
                    stroke={C.pendingStrokeClose}
                    strokeWidth={2}
                  />
                )}
              </Group>
            )}
          </Layer>
        </Stage>}
      </div>

      {/* ── New room name dialog ── */}
      {newRoomState && (
        <div
          className="fixed inset-0 z-50 flex items-center justify-center animate-fade-in"
          style={{ background: "rgba(2,8,23,0.8)", backdropFilter: "blur(4px)" }}
        >
          <div
            className="w-80 space-y-4 rounded-2xl p-6 shadow-glass animate-slide-up"
            style={{ border: "1px solid var(--border)", background: "var(--card)" }}
          >
            <div>
              <h3 className="font-display font-semibold text-base text-[color:var(--text)]">
                Назва кімнати
              </h3>
              <p className="text-xs font-mono text-[color:var(--text-faint)] mt-1">
                Будь-яка назва — Спальня, Кухня, Офіс
              </p>
            </div>
            <input
              autoFocus
              className="w-full rounded-xl px-3 py-2 text-sm transition-colors focus:outline-none focus:ring-2 focus:ring-primary-500/40"
              style={{
                background: "var(--raised)",
                border: "1px solid var(--border)",
                color: "var(--text)",
              }}
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
      {/* ── Edit placement dialog ── */}
      {editPlacement && (() => {
        const p = draft?.placements.find((pl) => pl.id === editPlacement.id);
        return (
          <div
            className="fixed inset-0 z-50 flex items-center justify-center animate-fade-in"
            style={{ background: "rgba(2,8,23,0.8)", backdropFilter: "blur(4px)" }}
          >
            <div
              className="w-96 space-y-5 rounded-2xl p-6 shadow-glass animate-slide-up"
              style={{ border: "1px solid var(--border)", background: "var(--card)" }}
            >
              <div>
                <h3 className="font-display font-semibold text-base text-[color:var(--text)] flex items-center gap-2">
                  <DeviceIcon kind={p?.kind ?? ""} size={18} className={deviceMeta(p?.kind ?? "").text} />
                  Редагувати пристрій
                </h3>
                <p className="text-xs text-[color:var(--text-faint)] mt-1">
                  {deviceMeta(p?.kind ?? "").label}
                </p>
              </div>

              <div className="space-y-3">
                <label className="block space-y-1.5">
                  <span className="text-[10px] font-mono font-medium uppercase tracking-widest text-[color:var(--text-faint)]">
                    Мітка
                  </span>
                  <input
                    autoFocus
                    className="w-full rounded-xl px-3 py-2 text-sm transition-colors focus:outline-none focus:ring-2 focus:ring-primary-500/40"
                    style={{
                      background: "var(--raised)",
                      border: "1px solid var(--border)",
                      color: "var(--text)",
                    }}
                    placeholder="напр. PIR Вітальня"
                    value={editPlacement.label}
                    onChange={(e) => setEditPlacement({ ...editPlacement, label: e.target.value })}
                    onKeyDown={(e) => { if (e.key === "Enter") saveEditPlacement(); if (e.key === "Escape") setEditPlacement(null); }}
                  />
                </label>

                <label className="block space-y-1.5">
                  <span className="text-[10px] font-mono font-medium uppercase tracking-widest text-[color:var(--text-faint)]">
                    Device ID
                  </span>
                  <input
                    className="w-full rounded-xl px-3 py-2 font-mono text-sm transition-colors focus:outline-none focus:ring-2 focus:ring-primary-500/40"
                    style={{
                      background: "var(--raised)",
                      border: "1px solid var(--border)",
                      color: "var(--primary)",
                    }}
                    placeholder="напр. pir_living_room"
                    value={editPlacement.device_id}
                    onChange={(e) => setEditPlacement({ ...editPlacement, device_id: e.target.value })}
                    onKeyDown={(e) => { if (e.key === "Enter") saveEditPlacement(); if (e.key === "Escape") setEditPlacement(null); }}
                  />
                  <p className="text-[10px] font-mono text-[color:var(--text-faint)]">
                    Має збігатися з MQTT-топіком · <code>mosquitto_sub -t 'home/#' -v</code>
                  </p>
                </label>

                {p?.kind === "camera" && (
                  <>
                    <label className="block space-y-1.5">
                      <span className="text-[10px] font-mono font-medium uppercase tracking-widest text-[color:var(--text-faint)]">
                        RTSP URL (субпотік — CV + мікрофон)
                      </span>
                      <input
                        className="w-full rounded-xl px-3 py-2 font-mono text-sm transition-colors focus:outline-none focus:ring-2 focus:ring-primary-500/40"
                        style={{
                          background: "var(--raised)",
                          border: "1px solid var(--border)",
                          color: "var(--text)",
                        }}
                        placeholder="rtsp://admin:pass@192.168.1.x/h264Preview_01_sub"
                        value={editPlacement.rtsp_url}
                        onChange={(e) => setEditPlacement({ ...editPlacement, rtsp_url: e.target.value })}
                        onKeyDown={(e) => { if (e.key === "Enter") saveEditPlacement(); if (e.key === "Escape") setEditPlacement(null); }}
                      />
                      <p className="text-[10px] font-mono text-[color:var(--text-faint)]">
                        Низька роздільна здатність · CV обробка + мікрофон
                      </p>
                    </label>

                    <label className="block space-y-1.5">
                      <span className="text-[10px] font-mono font-medium uppercase tracking-widest text-[color:var(--text-faint)]">
                        RTSP HD URL (основний потік — перегляд)
                      </span>
                      <input
                        className="w-full rounded-xl px-3 py-2 font-mono text-sm transition-colors focus:outline-none focus:ring-2 focus:ring-primary-500/40"
                        style={{
                          background: "var(--raised)",
                          border: "1px solid var(--border)",
                          color: "var(--text)",
                        }}
                        placeholder="rtsp://admin:pass@192.168.1.x/h264Preview_01_main"
                        value={editPlacement.rtsp_hd_url}
                        onChange={(e) => setEditPlacement({ ...editPlacement, rtsp_hd_url: e.target.value })}
                        onKeyDown={(e) => { if (e.key === "Enter") saveEditPlacement(); if (e.key === "Escape") setEditPlacement(null); }}
                      />
                      <p className="text-[10px] font-mono text-[color:var(--text-faint)]">
                        Висока роздільна здатність · HLS/WebRTC для перегляду у браузері
                      </p>
                    </label>
                  </>
                )}
              </div>

              <div className="flex items-center gap-2 pt-1">
                <button
                  onClick={() => {
                    if (!draft) return;
                    pushHistory();
                    updatePlacements(draft.placements.filter((pl) => pl.id !== editPlacement.id));
                    setEditPlacement(null);
                    setSelectedId(null);
                  }}
                  className="rounded-lg px-2.5 py-1.5 text-xs font-medium bg-red-500/10 text-red-400 border border-red-500/20 hover:bg-red-500/20 transition-colors"
                >
                  Видалити
                </button>
                <div className="ml-auto flex gap-2">
                  <Button size="sm" variant="secondary" onClick={() => setEditPlacement(null)}>
                    Скасувати
                  </Button>
                  <Button size="sm" variant="primary" onClick={saveEditPlacement} disabled={!editPlacement.device_id.trim()}>
                    Зберегти
                  </Button>
                </div>
              </div>
            </div>
          </div>
        );
      })()}
    </div>
  );
}
