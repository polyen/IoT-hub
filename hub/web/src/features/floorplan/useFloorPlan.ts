import { useQuery } from "@tanstack/react-query";
import { api } from "../../lib/api";
import type { FloorPlanData } from "../../lib/types";

export function useFloorPlan() {
  return useQuery<FloorPlanData>({
    queryKey: ["floorplan"],
    queryFn: () => api.get<FloorPlanData>("/api/floorplan"),
    staleTime: 60_000,
  });
}
