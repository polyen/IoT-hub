"""Central configuration loaded from environment variables."""

from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://iothub:iothub@localhost:5432/iothub"
    redis_url: str = "redis://localhost:6379/0"
    mqtt_host: str = "mosquitto"
    mqtt_port: int = 1883
    log_level: str = "info"


settings = Settings()
