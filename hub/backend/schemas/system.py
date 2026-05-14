from __future__ import annotations

from pydantic import BaseModel


class ServiceHealthOut(BaseModel):
    name: str
    status: str
    uptime: str | None


class HardwareOut(BaseModel):
    cpu_pct: float
    ram_used_gb: float
    ram_total_gb: float
    nvme_free_gb: float
    npu_pct: float | None
    temp_c: float | None


class LatencyOut(BaseModel):
    cv_p50_ms: int | None
    cv_p95_ms: int | None
    voice_e2e_p50_ms: int | None


class ModelsOut(BaseModel):
    cv_version: str | None
    llm_version: str | None
    whisper_version: str | None


class SyncOut(BaseModel):
    last_bridge_ts: str | None
    t1_queue_depth: int


class SystemHealthOut(BaseModel):
    services: list[ServiceHealthOut]
    hardware: HardwareOut
    latency: LatencyOut
    models: ModelsOut
    sync: SyncOut
