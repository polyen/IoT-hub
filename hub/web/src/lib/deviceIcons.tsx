import {
  Cctv,
  Lightbulb,
  Lock,
  Thermometer,
  Power,
  Radar,
  DoorOpen,
  Droplets,
  Wind,
  Gauge,
  Speaker,
  Settings2,
  type LucideIcon,
} from "lucide-react";
import type { DeviceKind } from "./types";

export interface DeviceMeta {
  Icon: LucideIcon;
  label: string;
  /** Hex colour for canvas/SVG markers (Konva can't read CSS vars). */
  hex: string;
  /** Tailwind text colour for DOM tinting. */
  text: string;
  /** Tailwind background tint for icon chips. */
  bg: string;
}

/**
 * Single source of truth for device presentation — lucide icons (no emoji),
 * a Ukrainian label, and a semantic tone. Replaces the per-file emoji maps that
 * used to live in FloorPlanView / FloorPlanEditor / DevicesListPage / DeviceQuickControl.
 */
export const DEVICE_META: Record<DeviceKind, DeviceMeta> = {
  camera:       { Icon: Cctv,        label: "Камера",       hex: "#818cf8", text: "text-primary-400", bg: "bg-primary-500/15" },
  light:        { Icon: Lightbulb,   label: "Лампа",        hex: "#fbbf24", text: "text-amber-400",   bg: "bg-amber-500/15" },
  lock:         { Icon: Lock,        label: "Замок",        hex: "#34d399", text: "text-emerald-400", bg: "bg-emerald-500/15" },
  thermostat:   { Icon: Thermometer, label: "Термостат",    hex: "#fb923c", text: "text-orange-400",  bg: "bg-orange-500/15" },
  relay:        { Icon: Power,       label: "Реле",         hex: "#818cf8", text: "text-primary-400", bg: "bg-primary-500/15" },
  sensor_pir:   { Icon: Radar,       label: "Рух (PIR)",    hex: "#38bdf8", text: "text-sky-400",     bg: "bg-sky-500/15" },
  sensor_door:  { Icon: DoorOpen,    label: "Двері",        hex: "#38bdf8", text: "text-sky-400",     bg: "bg-sky-500/15" },
  sensor_dht:   { Icon: Droplets,    label: "Темп/Волога",  hex: "#38bdf8", text: "text-sky-400",     bg: "bg-sky-500/15" },
  sensor_mq2:   { Icon: Wind,        label: "Газ MQ-2",     hex: "#fbbf24", text: "text-amber-400",   bg: "bg-amber-500/15" },
  sensor_power: { Icon: Gauge,       label: "Лічильник",    hex: "#94a3b8", text: "text-slate-400",   bg: "bg-slate-500/15" },
  speaker:      { Icon: Speaker,     label: "Динамік",      hex: "#a78bfa", text: "text-violet-400",  bg: "bg-violet-500/15" },
};

const FALLBACK: DeviceMeta = {
  Icon: Settings2,
  label: "Пристрій",
  hex: "#94a3b8",
  text: "text-slate-400",
  bg: "bg-slate-500/15",
};

export function deviceMeta(kind: string): DeviceMeta {
  return DEVICE_META[kind as DeviceKind] ?? FALLBACK;
}

export const DEVICE_KINDS = Object.keys(DEVICE_META) as DeviceKind[];

/** Convenience renderer for DOM surfaces. */
export function DeviceIcon({
  kind,
  size = 16,
  className,
}: {
  kind: string;
  size?: number;
  className?: string;
}) {
  const { Icon } = deviceMeta(kind);
  return <Icon size={size} strokeWidth={1.9} className={className} />;
}
