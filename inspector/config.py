from __future__ import annotations
import os
import stat
from pathlib import Path
from typing import Literal
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings


class AppConfig(BaseModel):
    name: str = "gpu-node-inspector"
    log_level: str = "INFO"


class ScheduleConfig(BaseModel):
    interval_minutes: int = Field(default=5, ge=1)


class StorageConfig(BaseModel):
    path: str = "data/inspector.db"
    retain_days: int = Field(default=7, ge=1)


class SshConfig(BaseModel):
    private_key_path_env: str = "SSH_PRIVATE_KEY_PATH"
    connect_timeout: int = 10
    command_timeout: int = 30

    def resolve_private_key_path(self, override_env: str | None = None) -> Path:
        env_name = override_env or self.private_key_path_env
        path_str = os.environ.get(env_name)
        if not path_str:
            raise RuntimeError(f"Missing environment variable: {env_name}")
        path = Path(path_str).expanduser()
        if not path.exists():
            raise RuntimeError(f"SSH private key not found: {path}")
        mode = stat.S_IMODE(path.stat().st_mode)
        if mode != 0o600:
            raise RuntimeError(f"SSH private key permissions must be 0o600, got 0o{mode:o}: {path}")
        return path


class NodeConfig(BaseModel):
    name: str
    host: str
    user: str
    ssh_private_key_path_env: str | None = None
    gpu_vendor: Literal["nvidia"] = "nvidia"
    ping_targets: list[str] = Field(default_factory=lambda: ["8.8.8.8"])


class AlertRules(BaseModel):
    gpu_temp_c: float = 85.0
    gpu_memory_pct: float = 90.0
    disk_usage_pct: float = 85.0
    node_unreachable: bool = True
    stability_cycles: int = Field(default=2, ge=1)


class NotificationsConfig(BaseModel):
    type: Literal["generic", "wps"] = "generic"
    webhook_url_env: str = "WPS_WEBHOOK_URL"
    format: Literal["text", "markdown_card"] = "markdown_card"
    include_gpu_processes: bool = True
    max_retries: int = Field(default=5, ge=0)
    retry_interval_seconds: int = Field(default=60, ge=0)

    def resolve_webhook_url(self) -> str:
        url = os.environ.get(self.webhook_url_env)
        if not url:
            raise RuntimeError(f"Missing environment variable: {self.webhook_url_env}")
        return url


class DashboardConfig(BaseModel):
    enabled: bool = True
    host: str = "127.0.0.1"
    port: int = 8080
    token_env: str = "DASHBOARD_TOKEN"

    def resolve_token(self) -> str:
        return os.environ.get(self.token_env, "")


class Settings(BaseSettings):
    app: AppConfig = AppConfig()
    schedule: ScheduleConfig = ScheduleConfig()
    storage: StorageConfig = StorageConfig()
    ssh: SshConfig = SshConfig()
    nodes: list[NodeConfig]
    alert_rules: AlertRules = AlertRules()
    notifications: NotificationsConfig = NotificationsConfig()
    dashboard: DashboardConfig = DashboardConfig()

    @field_validator("nodes")
    @classmethod
    def at_least_one_node(cls, v: list[NodeConfig]) -> list[NodeConfig]:
        if not v:
            raise ValueError("At least one node must be configured")
        return v


def load_config(path: str | Path = "config.yaml") -> Settings:
    import yaml
    path = Path(path)
    if not path.exists():
        raise RuntimeError(f"Config file not found: {path}")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return Settings(**data)
