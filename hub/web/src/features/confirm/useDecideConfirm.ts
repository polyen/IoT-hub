import { useMutation, useQueryClient } from "@tanstack/react-query";
import { api } from "../../lib/api";

export function useDecideConfirm() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, decision }: { id: string; decision: "approve" | "reject" }) =>
      api.post(`/api/confirm/${id}/decide`, { decision }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["confirm-pending"] }),
  });
}
