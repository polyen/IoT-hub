import { create } from "zustand";

interface FloorPlanStore {
  editMode: boolean;
  selectedRoomId: string | null;
  setEditMode: (v: boolean) => void;
  setSelectedRoom: (id: string | null) => void;
}

export const useFloorPlanStore = create<FloorPlanStore>((set) => ({
  editMode: false,
  selectedRoomId: null,
  setEditMode: (v) => set({ editMode: v }),
  setSelectedRoom: (id) => set({ selectedRoomId: id }),
}));
