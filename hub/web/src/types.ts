export interface Event {
  id: string;
  timestamp: string;
  room: string | null;
  type: string;
  tier: number;
  payload: Record<string, unknown> | null;
  model_version: string | null;
}

export interface FeedbackPayload {
  alert_id: string;
  user_label: "TP" | "FP" | "not_sure";
  tag?: string;
}
