from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal


@dataclass
class GpuMetric:
    index: int
    name: str
    temperature_c: float | None
    utilization_gpu_pct: float | None
    utilization_memory_pct: float | None
    memory_used_mb: float | None
    memory_total_mb: float | None
    power_draw_w: float | None
    fan_speed_pct: float | None


@dataclass
class SystemMetric:
    cpu_usage_pct: float | None = None
    memory_used_mb: float | None = None
    memory_total_mb: float | None = None
    disk_used_pct: float | None = None
    load_average_1m: float | None = None
    uptime_seconds: float | None = None


@dataclass
class NetworkMetric:
    target: str
    packet_loss_pct: float | None
    avg_latency_ms: float | None


@dataclass
class NodeMetrics:
    node: str
    timestamp: datetime
    reachable: bool
    gpus: list[GpuMetric] = field(default_factory=list)
    system: SystemMetric | None = None
    networks: list[NetworkMetric] = field(default_factory=list)
    error: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class AlertEvent:
    type: Literal["alert_triggered", "alert_recovered", "periodic_report"]
    node: str
    rule: str
    value: float | bool | None
    threshold: float | bool | None
    message: str
    timestamp: datetime


@dataclass
class NodeStatus:
    node: str
    reachable: bool
    summary: str
    last_check_at: datetime
    raw_metrics: dict[str, Any]
