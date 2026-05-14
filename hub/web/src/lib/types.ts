/* Shared TypeScript types — mirroring Pydantic schemas */

export interface HubEvent {
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
  user_label: string;
  tag?: string;
}

/* Floor plan */
export type RoomType = "bedroom" | "kitchen" | "living" | "bath" | "hall" | "outdoor" | "other";
export type DeviceKind =
  | "camera" | "light" | "lock" | "thermostat" | "relay"
  | "sensor_pir" | "sensor_door" | "sensor_dht" | "sensor_mq2" | "sensor_power" | "speaker";

export interface FloorPlan {
  id: string;
  name: string;
  floor: number;
  width: number;
  height: number;
  background_url: string | null;
}

export interface Room {
  id: string;
  floor_plan_id: string;
  name: string;
  type: RoomType;
  polygon: [number, number][];
  color: string | null;
  order: number;
}

export interface DevicePlacement {
  id: string;
  room_id: string;
  device_id: string;
  kind: DeviceKind;
  x: number;
  y: number;
  label: string | null;
  config: Record<string, unknown>;
}

export interface FloorPlanData {
  floor_plans: FloorPlan[];
  rooms: Room[];
  placements: DevicePlacement[];
}

/* Confirm */
export type ConfirmState = "pending" | "approved" | "rejected" | "timeout" | "executed";

export interface ConfirmRequest {
  id: string;
  created_at: string;
  expires_at: string;
  tool: string;
  payload: Record<string, unknown>;
  intent_text: string;
  confirm_message: string;
  schedule_origin: string | null;
  state: ConfirmState;
  decided_by: string | null;
  decided_at: string | null;
}

/* Camera */
export interface Camera {
  id: string;
  name: string;
  stream_hls: string | null;
  stream_webrtc: string | null;
  online: boolean;
}

export interface Detection {
  bbox: [number, number, number, number]; // [x1, y1, x2, y2] normalized 0..1
  cls: string;
  conf: number;
  track_id: number | null;
  face_id: string | null;
}

export interface CvFrame {
  ts: string;
  dets: Detection[];
}

/* Agent */
export interface AgentAuditEntry {
  id: string;
  timestamp: string;
  intent_text: string;
  tool: string | null;
  action_class: "AUTO" | "CONFIRM" | "DENY";
  executed: boolean;
  confirmation: string | null;
  latency_ms: number | null;
  llm_version: string | null;
}

/* System */
export type ServiceStatus = "ok" | "warn" | "error" | "offline";

export interface ServiceHealth {
  name: string;
  status: ServiceStatus;
  uptime: string | null;
}

export interface SystemHealth {
  services: ServiceHealth[];
  hardware: {
    cpu_pct: number;
    ram_used_gb: number;
    ram_total_gb: number;
    nvme_free_gb: number;
    npu_pct: number | null;
    temp_c: number | null;
  };
  latency: {
    cv_p50_ms: number | null;
    cv_p95_ms: number | null;
    voice_e2e_p50_ms: number | null;
  };
  models: {
    cv_version: string | null;
    llm_version: string | null;
    whisper_version: string | null;
  };
  sync: {
    last_bridge_ts: string | null;
    t1_queue_depth: number;
  };
}
