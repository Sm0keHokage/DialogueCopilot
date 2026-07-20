"""Configuration: secrets from env (IR-26), app parameters from YAML (IR-27)."""
from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class ClassifierConfig(BaseModel):
    batch_size: int = 10
    batch_window_ms: int = 800
    max_retries: int = 2
    cli_timeout_s: int = 30
    cache_ttl_s: int = 60
    api_timeout_s: float = 30.0
    retry_attempts: int = 3
    backoff_base_s: float = 1.0
    backoff_max_s: float = 60.0
    latency_samples: int = 1000
    cost_per_mtok_usd: float = 0.0
    idle_sleep_s: float = 0.5
    # Parallel AI agents per channel (upper bound for the per-channel setting).
    max_workers: int = 8
    # A pending message untouched this long is reclaimed by another agent.
    reclaim_idle_ms: int = 60_000


class IngestConfig(BaseModel):
    redis_stream_maxlen: int = 100_000
    dedup_ttl_s: int = 600
    recent_messages: int = 50


class EventSubConfig(BaseModel):
    websocket_url: str = "wss://eventsub.wss.twitch.tv/ws"
    reconnect_max_backoff_s: int = 30
    keepalive_grace_s: int = 15


class SecurityConfig(BaseModel):
    session_ttl_s: int = 7 * 24 * 3600
    session_sliding_renewal: bool = True
    rate_limit_per_minute: int = 240


class AppConfig(BaseModel):
    classifier: ClassifierConfig = Field(default_factory=ClassifierConfig)
    ingest: IngestConfig = Field(default_factory=IngestConfig)
    eventsub: EventSubConfig = Field(default_factory=EventSubConfig)
    security: SecurityConfig = Field(default_factory=SecurityConfig)


def load_app_config(path: str | Path | None) -> AppConfig:
    """Load YAML app config; missing file means defaults (IR-27)."""
    if not path:
        return AppConfig()
    p = Path(path)
    if not p.exists():
        return AppConfig()
    data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return AppConfig.model_validate(data)


class Settings(BaseSettings):
    """Secrets and deployment knobs, environment-only (IR-26)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    twitch_client_id: str = ""
    twitch_client_secret: str = ""
    twitch_redirect_uri: str = "http://localhost:8000/auth/twitch/callback"
    encryption_key: str = ""
    database_url: str = "postgresql+asyncpg://twitchguard:twitchguard@localhost:5432/twitchguard"
    redis_url: str = "redis://localhost:6379/0"
    session_secret: str = ""

    config_file: str = "config.yaml"
    frontend_origin: str = "http://localhost:5173"
    session_cookie_name: str = "tg_session"
    session_cookie_secure: bool = True
    twitch_id_base_url: str = "https://id.twitch.tv"
    helix_base_url: str = "https://api.twitch.tv/helix"
    builtin_rules_dir: str = "rules_builtin"
    db_create_all: bool = False
    start_workers: bool = True
    log_level: str = "INFO"
