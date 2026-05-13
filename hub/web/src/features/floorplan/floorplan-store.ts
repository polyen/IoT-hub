import { create } from "zustand";
import type { FloorPlanData, Room, DevicePlacement, DeviceKind } from "../../lib/types";

export type EditorMode = "select" | "add-room" | "add-device";

interface HistoryEntry {
  rooms: Room[];
  placements: DevicePlacement[];
}

interface FloorPlanStore {
  /* view state */
  editMode: boolean;
  selectedRoomId: string | null;
  setEditMode: (v: boolean) => void;
  setSelectedRoom: (id: string | null) => void;

  /* editor state */
  mode: EditorMode;
  setMode: (m: EditorMode) => void;
  pendingDeviceKind: DeviceKind | null;
  setPendingDeviceKind: (k: DeviceKind | null) => void;

  /* mutable plan data (editor works on a local copy) */
  draft: FloorPlanData | null;
  setDraft: (d: FloorPlanData) => void;
  updateRooms: (rooms: Room[]) => void;
  updatePlacements: (placements: DevicePlacement[]) => void;
  isDirty: boolean;

  /* undo/redo */
  history: HistoryEntry[];
  future: HistoryEntry[];
  pushHistory: () => void;
  undo: () => void;
  redo: () => void;
  canUndo: boolean;
  canRedo: boolean;
}

export const useFloorPlanStore = create<FloorPlanStore>((set, _get) => ({
  editMode: false,
  selectedRoomId: null,
  setEditMode: (v) => set({ editMode: v }),
  setSelectedRoom: (id) => set({ selectedRoomId: id }),

  mode: "select",
  setMode: (m) => set({ mode: m }),
  pendingDeviceKind: null,
  setPendingDeviceKind: (k) => set({ pendingDeviceKind: k }),

  draft: null,
  isDirty: false,
  setDraft: (d) => set({ draft: d, isDirty: false, history: [], future: [] }),

  updateRooms: (rooms) =>
    set((s) => ({
      draft: s.draft ? { ...s.draft, rooms } : null,
      isDirty: true,
    })),

  updatePlacements: (placements) =>
    set((s) => ({
      draft: s.draft ? { ...s.draft, placements } : null,
      isDirty: true,
    })),

  history: [],
  future: [],
  canUndo: false,
  canRedo: false,

  pushHistory: () =>
    set((s) => {
      if (!s.draft) return {};
      const entry: HistoryEntry = {
        rooms: structuredClone(s.draft.rooms),
        placements: structuredClone(s.draft.placements),
      };
      const history = [...s.history.slice(-49), entry];
      return { history, future: [], canUndo: true, canRedo: false };
    }),

  undo: () =>
    set((s) => {
      if (!s.draft || s.history.length === 0) return {};
      const prev = s.history[s.history.length - 1];
      const future: HistoryEntry = {
        rooms: structuredClone(s.draft.rooms),
        placements: structuredClone(s.draft.placements),
      };
      return {
        history: s.history.slice(0, -1),
        future: [future, ...s.future.slice(0, 49)],
        draft: { ...s.draft, rooms: prev.rooms, placements: prev.placements },
        isDirty: true,
        canUndo: s.history.length > 1,
        canRedo: true,
      };
    }),

  redo: () =>
    set((s) => {
      if (!s.draft || s.future.length === 0) return {};
      const next = s.future[0];
      const entry: HistoryEntry = {
        rooms: structuredClone(s.draft.rooms),
        placements: structuredClone(s.draft.placements),
      };
      return {
        history: [...s.history.slice(-49), entry],
        future: s.future.slice(1),
        draft: { ...s.draft, rooms: next.rooms, placements: next.placements },
        isDirty: true,
        canUndo: true,
        canRedo: s.future.length > 1,
      };
    }),
}));
