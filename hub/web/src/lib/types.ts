/* Shared TypeScript types — mirroring Pydantic schemas */

export interface HubEvent {
  id: string;
  timestamp: string;
  room: string | null;
  type: string;
  tier: number;
  payload: Record<string, unknown> | null;
  model_version: string | null;
  user_feedback?: string | null;
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

export type DeviceAction = "on" | "off" | "toggle" | "brightness_set" | "temp_set";

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
  slug: string;
  type: RoomType;
  polygon: [number, number][];
  color: string | null;
  order: number;
  aliases: string[];
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
  aliases: string[];
  controllable: boolean;
  actions: string[];
}

/** Full device registry row returned by GET /api/devices */
export interface DeviceRow {
  id: string;
  device_id: string;
  kind: DeviceKind;
  label: string | null;
  room_id: string;
  room_name: string;
  room_slug: string;
  room_aliases: string[];
  aliases: string[];
  controllable: boolean;
  actions: string[];
  config: Record<string, unknown>;
}

/** PATCH /api/devices/{device_id} body */
export interface DeviceUpdate {
  label?: string | null;
  aliases?: string[];
  controllable?: boolean;
  actions?: string[];
  config?: Record<string, unknown>;
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
  kps?: [number, number][]; // 17 COCO keypoints (x, y) normalized, present when pose ran
}

export interface CvFrame {
  ts: string;
  room: string;
  dets: Detection[];
}

/* Agent */
export type ActionClass = "AUTO" | "CONFIRM" | "DENY" | "ERROR" | "INFO" | "WARN";

export interface DisambiguateCandidate {
  device_id: string;
  label: string | null;
  room: string;
  kind: string;
}

export interface AgentTurnEvent {
  type: "intent" | "tool_call" | "result" | "ping";
  text?: string;
  class_?: string;
  failure_kind?: string;
  reasoning?: string;
  action_class?: ActionClass;
  tool?: string | null;
  topic?: string | null;
  payload?: Record<string, unknown>;
  candidates?: DisambiguateCandidate[];
  score?: number;
  prototype?: string;
  ts: string;
}

export interface AgentAuditEntry {
  id: string;
  timestamp: string;
  intent_text: string;
  tool: string | null;
  action_class: ActionClass;
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

export interface Incident {
  id: string;
  timestamp: string;
  intent_text: string;
  tool: string | null;
  severity: "high" | "medium" | "low";
}

export interface DigestData {
  period: "today" | "yesterday" | "week";
  start: string;
  end: string;
  total_events: number;
  counts: Record<string, number>;
  peak_hour: number | null;
  hourly_counts?: Record<number, number>;
  narrative: string | null;
}

export type SecurityMode = "disarmed" | "armed_home" | "armed_away";

export interface SecurityState {
  mode: SecurityMode;
  since: string | null;
}

export interface SecurityEvent {
  id: string;
  timestamp: string;
  type: string;
  room: string | null;
  payload: Record<string, unknown> | null;
}

export interface PrivacyReport {
  sent_to_cloud_bytes_7d: number;
  by_tier: Record<string, number>;
  by_tool: Record<string, number>;
  cloud_consent_state: boolean;
  report_generated_at: string;
}
