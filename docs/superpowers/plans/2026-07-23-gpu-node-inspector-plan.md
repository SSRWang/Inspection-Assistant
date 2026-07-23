# GPU Node Inspector 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现一个部署在金山云服务器上的轻量级 GPU 节点巡检服务，支持并发 SSH 采集、阈值告警、webhook 推送、SQLite 状态持久化和简易 Web 看板。

**Architecture:** 单进程 Python 服务，统一使用 `asyncio` 事件循环。`AsyncIOScheduler` 每 5 分钟触发一次采集任务；`asyncssh` 并发登录各节点执行命令；解析后的指标交给 `alerter` 进行阈值判断和状态机管理；`notifier` 负责 webhook 推送，失败消息写入 SQLite 重试队列；`FastAPI` 提供看板和 JSON API。

**Tech Stack:** Python 3.9+, `asyncssh`, `apscheduler`, `fastapi`, `uvicorn`, `httpx`, `pydantic`, `aiosqlite`（或标准库 sqlite3 + asyncio.Lock）, `jinja2`, `pytest`, `pytest-asyncio`

## Global Constraints

- Python >= 3.9
- 单进程架构，仅使用一个全局 `asyncio` 事件循环，禁止在同步代码中调用 `asyncio.run()`
- SQLite 操作必须通过 `asyncio.Lock` 串行化
- SSH 私钥仅允许通过文件路径读取，启动时强制校验文件权限为 `0o600`
- 所有远程命令通过 `asyncssh.run(command, args=[...])` 参数列表调用，禁止字符串拼接
- 节点上只执行只读命令
- Web 看板默认监听 `127.0.0.1`，生产环境通过 Nginx/安全组暴露
- 配置与代码分离，敏感信息走环境变量
- 所有任务必须通过测试验证，频繁提交

---

## 文件结构

```
gpu-node-inspector/
├── inspector/
│   ├── __init__.py
│   ├── config.py
│   ├── store.py
│   ├── metrics.py
│   ├── collector.py
│   ├── alerter.py
│   ├── notifier.py
│   ├── scheduler.py
│   ├── dashboard.py
│   └── models.py
├── main.py
├── requirements.txt
├── pyproject.toml
├── config.example.yaml
├── scripts/
│   ├── install.sh
│   ├── run.sh
│   └── logrotate.conf
├── data/
│   └── .gitkeep
├── logs/
│   └── .gitkeep
└── tests/
    ├── __init__.py
    ├── conftest.py
    ├── test_config.py
    ├── test_store.py
    ├── test_metrics.py
    ├── test_collector.py
    ├── test_alerter.py
    ├── test_notifier.py
    └── test_dashboard.py
```

---

### Task 1: 项目脚手架与配置模块

**Files:**
- Create: `pyproject.toml`
- Create: `requirements.txt`
- Create: `config.example.yaml`
- Create: `.gitignore`
- Create: `inspector/__init__.py`
- Create: `inspector/models.py`
- Create: `inspector/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `inspector.config:Settings` pydantic model with `from_yaml(path)` and `load_config()` helpers
- Produces: `inspector.models:NodeMetrics`, `AlertEvent`, `NodeStatus` dataclasses

- [ ] **Step 1: Write pyproject.toml**

```toml
[build-system]
requires = ["setuptools>=61", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "gpu-node-inspector"
version = "0.1.0"
requires-python = ">=3.9"
dependencies = [
    "asyncssh>=2.14.0",
    "apscheduler>=3.10.0",
    "fastapi>=0.104.0",
    "uvicorn[standard]>=0.24.0",
    "httpx>=0.25.0",
    "pydantic>=2.0.0",
    "pydantic-settings>=2.0.0",
    "jinja2>=3.1.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.4.0",
    "pytest-asyncio>=0.21.0",
    "pytest-httpx>=0.25.0",
    "pytest-cov>=4.1.0",
    "ruff>=0.1.0",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

- [ ] **Step 2: Write requirements.txt**

```text
asyncssh>=2.14.0
apscheduler>=3.10.0
fastapi>=0.104.0
uvicorn[standard]>=0.24.0
httpx>=0.25.0
pydantic>=2.0.0
pydantic-settings>=2.0.0
jinja2>=3.1.0
```

- [ ] **Step 3: Write .gitignore**

```gitignore
__pycache__/
*.py[cod]
*.egg-info/
.venv/
venv/
data/*.db
logs/*.log
config.yaml
.env
*.pem
*.key
.pytest_cache/
.mypy_cache/
.ruff_cache/
```

- [ ] **Step 4: Write config.example.yaml**

```yaml
app:
  name: "gpu-node-inspector"
  log_level: INFO

schedule:
  interval_minutes: 5

storage:
  path: "data/inspector.db"
  retain_days: 7

ssh:
  private_key_path_env: "SSH_PRIVATE_KEY_PATH"
  connect_timeout: 10
  command_timeout: 30

nodes:
  - name: node-01
    host: 192.168.1.10
    user: ubuntu
    ssh_private_key_path_env: "SSH_PRIVATE_KEY_PATH_NODE_01"
    gpu_vendor: nvidia
    ping_targets:
      - "8.8.8.8"

alert_rules:
  gpu_temp_c: 85
  gpu_memory_pct: 90
  disk_usage_pct: 85
  node_unreachable: true
  stability_cycles: 2

notifications:
  type: generic
  webhook_url_env: "WPS_WEBHOOK_URL"
  format: markdown_card
  include_gpu_processes: true
  max_retries: 5
  retry_interval_seconds: 60

dashboard:
  enabled: true
  host: "127.0.0.1"
  port: 8080
  token_env: "DASHBOARD_TOKEN"
```

- [ ] **Step 5: Write failing test for config**

Create `tests/test_config.py`:

```python
import os
from pathlib import Path
import pytest
import yaml
from inspector.config import Settings, load_config


def test_load_config_reads_file(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "app": {"name": "test", "log_level": "DEBUG"},
        "schedule": {"interval_minutes": 5},
        "storage": {"path": "data/test.db", "retain_days": 7},
        "ssh": {"private_key_path_env": "SSH_PRIVATE_KEY_PATH", "connect_timeout": 10, "command_timeout": 30},
        "nodes": [{"name": "n1", "host": "1.2.3.4", "user": "u", "gpu_vendor": "nvidia"}],
        "alert_rules": {"gpu_temp_c": 85, "node_unreachable": True, "stability_cycles": 2},
        "notifications": {"type": "generic", "webhook_url_env": "W", "max_retries": 5},
        "dashboard": {"enabled": True, "host": "127.0.0.1", "port": 8080},
    }))
    os.environ["SSH_PRIVATE_KEY_PATH"] = str(tmp_path / "key.pem")
    key_path = tmp_path / "key.pem"
    key_path.write_text("fake-key")
    key_path.chmod(0o600)

    cfg = load_config(cfg_path)
    assert isinstance(cfg, Settings)
    assert cfg.app.name == "test"
    assert cfg.nodes[0].host == "1.2.3.4"
    assert cfg.alert_rules.gpu_temp_c == 85
```

- [ ] **Step 6: Run test to verify it fails**

```bash
pytest tests/test_config.py -v
```

Expected: ImportError or ModuleNotFoundError

- [ ] **Step 7: Write models.py**

Create `inspector/models.py`:

```python
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
```

- [ ] **Step 8: Write config.py**

Create `inspector/config.py`:

```python
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
```

- [ ] **Step 9: Run test to verify it passes**

```bash
pytest tests/test_config.py -v
```

Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add pyproject.toml requirements.txt .gitignore config.example.yaml inspector/__init__.py inspector/models.py inspector/config.py tests/test_config.py data/.gitkeep logs/.gitkeep
git commit -m "feat: project scaffolding and pydantic config"
```

---

### Task 2: SQLite 状态存储层

**Files:**
- Create: `inspector/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Consumes: `inspector.models:NodeMetrics`, `AlertEvent`
- Produces: `SqliteStore` class with async methods for metrics, alert states, and webhook queue

- [ ] **Step 1: Write failing test for store**

Create `tests/test_store.py`:

```python
import asyncio
from datetime import datetime, timezone
from pathlib import Path
import pytest
from inspector.models import GpuMetric, NodeMetrics, SystemMetric, AlertEvent
from inspector.store import SqliteStore


@pytest.fixture
async def store(tmp_path):
    db_path = tmp_path / "test.db"
    s = SqliteStore(str(db_path))
    await s.setup()
    yield s
    await s.close()


async def test_write_and_read_node_status(store):
    metrics = NodeMetrics(
        node="n1",
        timestamp=datetime.now(timezone.utc),
        reachable=True,
        gpus=[GpuMetric(index=0, name="T4", temperature_c=60.0, utilization_gpu_pct=10.0,
                        utilization_memory_pct=20.0, memory_used_mb=1000.0,
                        memory_total_mb=16000.0, power_draw_w=35.0, fan_speed_pct=30.0)],
        system=SystemMetric(cpu_usage_pct=15.0, memory_used_mb=4000.0,
                            memory_total_mb=16000.0, disk_used_pct=50.0,
                            load_average_1m=0.5, uptime_seconds=3600.0),
        networks=[],
    )
    await store.write_node_status(metrics)
    status = await store.get_node_status("n1")
    assert status is not None
    assert status["reachable"] == 1
    assert status["node"] == "n1"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_store.py -v
```

Expected: ImportError

- [ ] **Step 3: Write store.py**

Create `inspector/store.py`:

```python
from __future__ import annotations
import asyncio
import json
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from inspector.models import AlertEvent, NodeMetrics


class SqliteStore:
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._connection: sqlite3.Connection | None = None

    async def setup(self) -> None:
        async with self._lock:
            self._connection = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._connection.row_factory = sqlite3.Row
            self._connection.executescript(self._schema())
            self._connection.commit()

    async def close(self) -> None:
        async with self._lock:
            if self._connection:
                self._connection.close()
                self._connection = None

    def _schema(self) -> str:
        return """
        CREATE TABLE IF NOT EXISTS metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node TEXT NOT NULL,
            category TEXT NOT NULL,
            name TEXT NOT NULL,
            value REAL,
            unit TEXT,
            labels TEXT,
            timestamp TEXT NOT NULL,
            raw_output TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_metrics_node_time ON metrics(node, timestamp);

        CREATE TABLE IF NOT EXISTS node_status (
            node TEXT PRIMARY KEY,
            reachable INTEGER NOT NULL,
            summary TEXT,
            last_check_at TEXT NOT NULL,
            raw_metrics TEXT
        );

        CREATE TABLE IF NOT EXISTS alert_states (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node TEXT NOT NULL,
            rule TEXT NOT NULL,
            state TEXT NOT NULL,
            breach_cycles INTEGER NOT NULL DEFAULT 0,
            last_value REAL,
            triggered_at TEXT,
            updated_at TEXT NOT NULL,
            UNIQUE(node, rule)
        );

        CREATE TABLE IF NOT EXISTS pending_webhooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payload TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            next_retry_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
        );
        CREATE INDEX IF NOT EXISTS idx_webhooks_retry ON pending_webhooks(status, next_retry_at);
        """

    async def write_metrics(self, records: list[dict[str, Any]]) -> None:
        async with self._lock:
            now = datetime.now(timezone.utc).isoformat()
            for rec in records:
                self._connection.execute(
                    "INSERT INTO metrics (node, category, name, value, unit, labels, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (rec["node"], rec["category"], rec["name"], rec.get("value"),
                     rec.get("unit"), json.dumps(rec.get("labels") or {}), now)
                )
            self._connection.commit()

    async def write_node_status(self, metrics: NodeMetrics) -> None:
        async with self._lock:
            now = metrics.timestamp.isoformat()
            summary = self._build_summary(metrics)
            self._connection.execute(
                """INSERT INTO node_status (node, reachable, summary, last_check_at, raw_metrics)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(node) DO UPDATE SET
                   reachable=excluded.reachable, summary=excluded.summary,
                   last_check_at=excluded.last_check_at, raw_metrics=excluded.raw_metrics""",
                (metrics.node, int(metrics.reachable), summary, now,
                 json.dumps(metrics.raw, default=str))
            )
            self._connection.commit()

    def _build_summary(self, metrics: NodeMetrics) -> str:
        if not metrics.reachable:
            return "Node unreachable"
        gpu_summary = f"{len(metrics.gpus)} GPUs"
        avg_temp = sum(g.temperature_c for g in metrics.gpus if g.temperature_c is not None) / max(len(metrics.gpus), 1)
        return f"{gpu_summary}, avg temp {avg_temp:.1f}°C"

    async def get_node_status(self, node: str) -> dict[str, Any] | None:
        async with self._lock:
            row = self._connection.execute(
                "SELECT * FROM node_status WHERE node = ?", (node,)
            ).fetchone()
            if row is None:
                return None
            return dict(row)

    async def list_node_status(self) -> list[dict[str, Any]]:
        async with self._lock:
            rows = self._connection.execute("SELECT * FROM node_status ORDER BY node").fetchall()
            return [dict(r) for r in rows]

    async def get_alert_state(self, node: str, rule: str) -> dict[str, Any] | None:
        async with self._lock:
            row = self._connection.execute(
                "SELECT * FROM alert_states WHERE node = ? AND rule = ?", (node, rule)
            ).fetchone()
            if row is None:
                return None
            return dict(row)

    async def update_alert_state(self, node: str, rule: str, state: str,
                                 breach_cycles: int, last_value: float | None) -> None:
        async with self._lock:
            now = datetime.now(timezone.utc).isoformat()
            triggered_at = now if state == "triggered" else None
            self._connection.execute(
                """INSERT INTO alert_states (node, rule, state, breach_cycles, last_value, triggered_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(node, rule) DO UPDATE SET
                   state=excluded.state, breach_cycles=excluded.breach_cycles,
                   last_value=excluded.last_value,
                   triggered_at=COALESCE(excluded.triggered_at, triggered_at),
                   updated_at=excluded.updated_at""",
                (node, rule, state, breach_cycles, last_value, triggered_at, now)
            )
            self._connection.commit()

    async def list_alert_states(self) -> list[dict[str, Any]]:
        async with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM alert_states WHERE state = 'triggered' ORDER BY updated_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    async def enqueue_webhook(self, payload: dict[str, Any], attempts: int = 0) -> None:
        async with self._lock:
            now = datetime.now(timezone.utc)
            next_retry = now + timedelta(seconds=60)
            self._connection.execute(
                "INSERT INTO pending_webhooks (payload, attempts, created_at, next_retry_at, status) VALUES (?, ?, ?, ?, ?)",
                (json.dumps(payload, default=str), attempts, now.isoformat(), next_retry.isoformat(), "pending")
            )
            self._connection.commit()

    async def dequeue_pending_webhooks(self, limit: int = 10) -> list[tuple[int, dict[str, Any], int]]:
        async with self._lock:
            now = datetime.now(timezone.utc).isoformat()
            rows = self._connection.execute(
                "SELECT id, payload, attempts FROM pending_webhooks WHERE status = 'pending' AND next_retry_at <= ? ORDER BY next_retry_at LIMIT ?",
                (now, limit)
            ).fetchall()
            return [(r["id"], json.loads(r["payload"]), r["attempts"]) for r in rows]

    async def delete_webhook(self, record_id: int) -> None:
        async with self._lock:
            self._connection.execute("DELETE FROM pending_webhooks WHERE id = ?", (record_id,))
            self._connection.commit()

    async def update_webhook_retry(self, record_id: int, attempts: int, next_retry_at: datetime) -> None:
        async with self._lock:
            self._connection.execute(
                "UPDATE pending_webhooks SET attempts = ?, next_retry_at = ? WHERE id = ?",
                (attempts, next_retry_at.isoformat(), record_id)
            )
            self._connection.commit()

    async def mark_webhook_dead(self, record_id: int) -> None:
        async with self._lock:
            self._connection.execute(
                "UPDATE pending_webhooks SET status = 'dead' WHERE id = ?", (record_id,)
            )
            self._connection.commit()

    async def cleanup_metrics(self, retain_days: int) -> None:
        async with self._lock:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=retain_days)).isoformat()
            self._connection.execute("DELETE FROM metrics WHERE timestamp < ?", (cutoff,))
            self._connection.commit()
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_store.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add inspector/store.py tests/test_store.py
git commit -m "feat: sqlite store with asyncio lock"
```

---

### Task 3: 指标解析模块

**Files:**
- Create: `inspector/metrics.py`
- Test: `tests/test_metrics.py`

**Interfaces:**
- Produces: `parse_nvidia_smi(output: str) -> list[GpuMetric]`
- Produces: `parse_system(output_map: dict) -> SystemMetric | None`
- Produces: `parse_ping(output: str) -> NetworkMetric`
- Produces: `flatten_metrics(node_metrics: NodeMetrics) -> list[dict]`

- [ ] **Step 1: Write failing test for metrics parser**

Create `tests/test_metrics.py`:

```python
from datetime import datetime, timezone
from inspector.metrics import parse_nvidia_smi, parse_ping, flatten_metrics
from inspector.models import GpuMetric, NodeMetrics, SystemMetric


def test_parse_nvidia_smi_basic():
    output = "0, NVIDIA T4, 60.0, 10.0, 20.0, 1000.0, 16000.0, 35.0, 30.0\n"
    gpus = parse_nvidia_smi(output)
    assert len(gpus) == 1
    assert gpus[0].name == "NVIDIA T4"
    assert gpus[0].temperature_c == 60.0


def test_parse_ping_basic():
    output = "rtt min/avg/max/mdev = 1.2/2.3/3.4/0.5 ms\n0% packet loss"
    net = parse_ping("8.8.8.8", output)
    assert net.target == "8.8.8.8"
    assert net.avg_latency_ms == 2.3
    assert net.packet_loss_pct == 0.0
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_metrics.py -v
```

Expected: ImportError

- [ ] **Step 3: Write metrics.py**

Create `inspector/metrics.py`:

```python
from __future__ import annotations
import re
from inspector.models import GpuMetric, NetworkMetric, NodeMetrics, SystemMetric


def _to_float(value: str) -> float | None:
    value = value.strip()
    if value in ("", "N/A", "Unknown", "[Not Supported]"):
        return None
    # Remove units like ' W', ' %', ' MiB'
    numeric = re.sub(r"[^\d.\-]", "", value.split()[0] if value.split() else value)
    try:
        return float(numeric)
    except ValueError:
        return None


def parse_nvidia_smi(output: str) -> list[GpuMetric]:
    gpus = []
    for line in output.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 9:
            continue
        gpus.append(GpuMetric(
            index=int(_to_float(parts[0]) or 0),
            name=parts[1],
            temperature_c=_to_float(parts[2]),
            utilization_gpu_pct=_to_float(parts[3]),
            utilization_memory_pct=_to_float(parts[4]),
            memory_used_mb=_to_float(parts[5]),
            memory_total_mb=_to_float(parts[6]),
            power_draw_w=_to_float(parts[7]),
            fan_speed_pct=_to_float(parts[8]),
        ))
    return gpus


def parse_system(outputs: dict[str, str]) -> SystemMetric | None:
    try:
        cpu = _parse_cpu(outputs.get("cpu_output", ""))
        mem_used, mem_total = _parse_free(outputs.get("memory_output", ""))
        disk = _parse_df(outputs.get("disk_output", ""))
        load_line = outputs.get("load_output", "")
        load = _to_float(load_line.split()[0]) if load_line.split() else None
        uptime = _to_float(outputs.get("uptime_output", ""))
        return SystemMetric(
            cpu_usage_pct=cpu,
            memory_used_mb=mem_used,
            memory_total_mb=mem_total,
            disk_used_pct=disk,
            load_average_1m=load,
            uptime_seconds=uptime,
        )
    except Exception:
        return None


def _parse_free(output: str) -> tuple[float | None, float | None]:
    for line in output.splitlines():
        if line.startswith("Mem:"):
            parts = line.split()
            if len(parts) >= 3:
                # free -m columns: Mem: total used free ...
                return _to_float(parts[2]), _to_float(parts[1])
            break
    return None, None


def _parse_df(output: str) -> float | None:
    lines = output.strip().splitlines()
    if len(lines) >= 2:
        parts = lines[1].split()
        if len(parts) >= 5:
            return _to_float(parts[4].replace("%", ""))
    return None


def _parse_cpu(output: str) -> float | None:
    for line in output.splitlines():
        if "Cpu(s):" in line:
            # e.g. "%Cpu(s): 10.5 us,  5.2 sy, ..."
            m = re.search(r"([\d.]+)\s*us", line)
            if m:
                return float(m.group(1))
    return None


def parse_ping(target: str, output: str) -> NetworkMetric:
    packet_loss = None
    avg_latency = None
    for line in output.splitlines():
        m = re.search(r"(\d+(?:\.\d+)?)% packet loss", line)
        if m:
            packet_loss = float(m.group(1))
        m = re.search(r"rtt.*=\s*[\d.]+/([\d.]+)/[\d.]+/[\d.]+\s*ms", line)
        if m:
            avg_latency = float(m.group(1))
    return NetworkMetric(target=target, packet_loss_pct=packet_loss, avg_latency_ms=avg_latency)


def flatten_metrics(metrics: NodeMetrics) -> list[dict]:
    records = []
    ts = metrics.timestamp.isoformat()
    for gpu in metrics.gpus:
        records.append({
            "node": metrics.node, "category": "gpu", "name": "temperature_c",
            "value": gpu.temperature_c, "unit": "C", "labels": {"index": gpu.index, "name": gpu.name}, "timestamp": ts
        })
        records.append({
            "node": metrics.node, "category": "gpu", "name": "utilization_gpu_pct",
            "value": gpu.utilization_gpu_pct, "unit": "%", "labels": {"index": gpu.index}, "timestamp": ts
        })
    if metrics.system:
        records.append({
            "node": metrics.node, "category": "system", "name": "disk_used_pct",
            "value": metrics.system.disk_used_pct, "unit": "%", "labels": {}, "timestamp": ts
        })
    return records
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_metrics.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add inspector/metrics.py tests/test_metrics.py
git commit -m "feat: metrics parser for nvidia-smi, system, ping"
```

---

### Task 4: SSH 并发采集器

**Files:**
- Create: `inspector/collector.py`
- Test: `tests/test_collector.py`

**Interfaces:**
- Consumes: `Settings`, `SqliteStore`
- Produces: `Collector.collect_node(node_cfg) -> NodeMetrics`

- [ ] **Step 1: Write failing test for collector**

Create `tests/test_collector.py`:

```python
import asyncio
from datetime import datetime, timezone
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from inspector.collector import Collector
from inspector.config import NodeConfig
from inspector.models import NodeMetrics


async def test_build_commands():
    store = MagicMock()
    collector = Collector(MagicMock(), store)
    cfg = NodeConfig(name="n1", host="1.2.3.4", user="u")
    commands = collector._build_commands(cfg)
    assert commands["gpu"][0] == "nvidia-smi"
    assert commands["ping"][-1] == "8.8.8.8"
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_collector.py -v
```

Expected: ImportError

- [ ] **Step 3: Write collector.py**

Create `inspector/collector.py`:

```python
from __future__ import annotations
import asyncio
import asyncssh
from datetime import datetime, timezone
from pathlib import Path
from inspector.config import NodeConfig, Settings
from inspector.metrics import parse_nvidia_smi, parse_ping, parse_system
from inspector.models import NetworkMetric, NodeMetrics, SystemMetric
from inspector.store import SqliteStore


class Collector:
    def __init__(self, cfg: Settings, store: SqliteStore):
        self.cfg = cfg
        self.store = store

    def _build_commands(self, node: NodeConfig) -> dict[str, list[str]]:
        ping_target = node.ping_targets[0] if node.ping_targets else "8.8.8.8"
        return {
            "gpu": ["nvidia-smi", "--query-gpu=index,name,temperature.gpu,utilization.gpu,utilization.memory,memory.used,memory.total,power.draw,fan.speed", "--format=csv,noheader"],
            "df": ["df", "-h", "/"],
            "memory": ["free", "-m"],
            "load": ["cat", "/proc/loadavg"],
            "uptime": ["cat", "/proc/uptime"],
            "cpu": ["top", "-bn1"],
            "ping": ["ping", "-c", "3", ping_target],
        }

    async def collect_node(self, node: NodeConfig) -> NodeMetrics:
        timestamp = datetime.now(timezone.utc)
        try:
            key_path = self.cfg.ssh.resolve_private_key_path(node.ssh_private_key_path_env)
        except Exception as e:
            return NodeMetrics(node=node.name, timestamp=timestamp, reachable=False, error=str(e))

        try:
            async with asyncssh.connect(
                host=node.host,
                username=node.user,
                client_keys=[str(key_path)],
                known_hosts=None,
                connect_timeout=self.cfg.ssh.connect_timeout,
            ) as conn:
                cmds = self._build_commands(node)
                gpu_out = await self._run(conn, cmds["gpu"])
                df_out = await self._run(conn, cmds["df"])
                mem_out = await self._run(conn, cmds["memory"])
                load_out = await self._run(conn, cmds["load"])
                uptime_out = await self._run(conn, cmds["uptime"])
                cpu_out = await self._run(conn, cmds["cpu"])
                ping_out = await self._run(conn, cmds["ping"])

                gpus = parse_nvidia_smi(gpu_out)
                system = parse_system({
                    "cpu_output": cpu_out,
                    "memory_output": mem_out,
                    "disk_output": df_out,
                    "load_output": load_out,
                    "uptime_output": uptime_out,
                })
                network = parse_ping(cmds["ping"][-1], ping_out)

                metrics = NodeMetrics(
                    node=node.name,
                    timestamp=timestamp,
                    reachable=True,
                    gpus=gpus,
                    system=system,
                    networks=[network],
                    raw={
                        "gpu": gpu_out, "df": df_out, "memory": mem_out,
                        "load": load_out, "uptime": uptime_out, "cpu": cpu_out,
                        "ping": ping_out,
                    }
                )
                return metrics
        except Exception as e:
            return NodeMetrics(node=node.name, timestamp=timestamp, reachable=False, error=str(e))

    async def _run(self, conn: asyncssh.SSHClientConnection, args: list[str]) -> str:
        result = await conn.run(args[0], args=args[1:], timeout=self.cfg.ssh.command_timeout)
        return result.stdout

    async def collect_all(self) -> list[NodeMetrics]:
        return await asyncio.gather(*[self.collect_node(n) for n in self.cfg.nodes])
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_collector.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add inspector/collector.py tests/test_collector.py
git commit -m "feat: async ssh collector using asyncssh"
```

---

### Task 5: 告警模块（阈值判断 + 防抖状态机）

**Files:**
- Create: `inspector/alerter.py`
- Test: `tests/test_alerter.py`

**Interfaces:**
- Consumes: `NodeMetrics`, `Settings`, `SqliteStore`
- Produces: `Alerter.evaluate(metrics) -> list[AlertEvent]`

- [ ] **Step 1: Write failing test for alerter**

Create `tests/test_alerter.py`:

```python
from datetime import datetime, timezone
from unittest.mock import AsyncMock
import pytest
from inspector.alerter import Alerter
from inspector.config import AlertRules
from inspector.models import GpuMetric, NodeMetrics, SystemMetric


async def test_temperature_alert_first_breach_is_breaching():
    store = AsyncMock()
    store.get_alert_state = AsyncMock(return_value=None)
    rules = AlertRules(gpu_temp_c=80, stability_cycles=2)
    alerter = Alerter(rules, store)

    gpu = GpuMetric(index=0, name="T4", temperature_c=85.0, utilization_gpu_pct=10,
                    utilization_memory_pct=20, memory_used_mb=1000,
                    memory_total_mb=16000, power_draw_w=30, fan_speed_pct=30)
    metrics = NodeMetrics(node="n1", timestamp=datetime.now(timezone.utc),
                          reachable=True, gpus=[gpu], system=SystemMetric(disk_used_pct=50))

    events = await alerter.evaluate(metrics)
    assert len(events) == 0
    store.update_alert_state.assert_awaited_with("n1", "gpu_temp_c", "breaching", 1, 85.0)


async def test_temperature_alert_second_breach_triggers():
    store = AsyncMock()
    store.get_alert_state = AsyncMock(return_value={"state": "breaching", "breach_cycles": 1, "last_value": 85.0})
    rules = AlertRules(gpu_temp_c=80, stability_cycles=2)
    alerter = Alerter(rules, store)

    gpu = GpuMetric(index=0, name="T4", temperature_c=85.0, utilization_gpu_pct=10,
                    utilization_memory_pct=20, memory_used_mb=1000,
                    memory_total_mb=16000, power_draw_w=30, fan_speed_pct=30)
    metrics = NodeMetrics(node="n1", timestamp=datetime.now(timezone.utc),
                          reachable=True, gpus=[gpu], system=SystemMetric(disk_used_pct=50))

    events = await alerter.evaluate(metrics)
    assert len(events) == 1
    assert events[0].type == "alert_triggered"
    store.update_alert_state.assert_awaited_with("n1", "gpu_temp_c", "triggered", 2, 85.0)
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_alerter.py -v
```

Expected: ImportError

- [ ] **Step 3: Write alerter.py**

Create `inspector/alerter.py`:

```python
from __future__ import annotations
from datetime import datetime, timezone
from inspector.config import AlertRules
from inspector.models import AlertEvent, GpuMetric, NodeMetrics, SystemMetric
from inspector.store import SqliteStore


class Alerter:
    def __init__(self, rules: AlertRules, store: SqliteStore):
        self.rules = rules
        self.store = store

    async def evaluate(self, metrics: NodeMetrics) -> list[AlertEvent]:
        events = []
        # Always evaluate node_unreachable so recovery is detected
        events.extend(await self._check_rule(metrics, "node_unreachable", not metrics.reachable))
        if not metrics.reachable:
            return events

        for gpu in metrics.gpus:
            if gpu.temperature_c is not None:
                events.extend(await self._check_rule(metrics, "gpu_temp_c", gpu.temperature_c, gpu.index))
            if gpu.memory_total_mb and gpu.memory_used_mb is not None:
                mem_pct = gpu.memory_used_mb / gpu.memory_total_mb * 100
                events.extend(await self._check_rule(metrics, "gpu_memory_pct", mem_pct, gpu.index))

        if metrics.system:
            if metrics.system.disk_used_pct is not None:
                events.extend(await self._check_rule(metrics, "disk_usage_pct", metrics.system.disk_used_pct))

        return events

    async def _check_rule(self, metrics: NodeMetrics, rule: str, value: float | bool,
                          gpu_index: int | None = None) -> list[AlertEvent]:
        threshold = getattr(self.rules, rule, None)
        if threshold is None:
            return []
        if rule == "node_unreachable" and not threshold:
            return []

        node = metrics.node
        state_row = await self.store.get_alert_state(node, rule)
        state = state_row["state"] if state_row else "normal"
        cycles = state_row["breach_cycles"] if state_row else 0

        is_breach = self._is_breach(value, threshold)
        events = []

        if is_breach:
            cycles += 1
            if cycles >= self.rules.stability_cycles and state != "triggered":
                events.append(AlertEvent(
                    type="alert_triggered",
                    node=node,
                    rule=rule,
                    value=value,
                    threshold=threshold,
                    message=self._message(rule, value, threshold, gpu_index, triggered=True),
                    timestamp=metrics.timestamp,
                ))
                await self.store.update_alert_state(node, rule, "triggered", cycles, float(value) if isinstance(value, (int, float)) else None)
            else:
                await self.store.update_alert_state(node, rule, "breaching", cycles, float(value) if isinstance(value, (int, float)) else None)
        else:
            if state == "triggered":
                events.append(AlertEvent(
                    type="alert_recovered",
                    node=node,
                    rule=rule,
                    value=value,
                    threshold=threshold,
                    message=self._message(rule, value, threshold, gpu_index, triggered=False),
                    timestamp=metrics.timestamp,
                ))
            await self.store.update_alert_state(node, rule, "normal", 0, float(value) if isinstance(value, (int, float)) else None)

        return events

    def _is_breach(self, value: float | bool, threshold: float | bool) -> bool:
        if isinstance(threshold, bool):
            return bool(value)
        if isinstance(value, bool):
            return value
        return value > threshold

    def _message(self, rule: str, value, threshold, gpu_index: int | None, triggered: bool) -> str:
        prefix = "🚨 Triggered" if triggered else "✅ Recovered"
        gpu_str = f" [GPU {gpu_index}]" if gpu_index is not None else ""
        return f"{prefix}: {rule}{gpu_str} value={value}, threshold={threshold}"
```

- [ ] **Step 4: Run test to verify it passes**

```bash
pytest tests/test_alerter.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add inspector/alerter.py tests/test_alerter.py
git commit -m "feat: alerter with debounce state machine"
```

---

### Task 6: Webhook 通知模块（抽象适配层）

**Files:**
- Create: `inspector/notifier.py`
- Test: `tests/test_notifier.py`

**Interfaces:**
- Consumes: `AlertEvent`, `NotificationsConfig`
- Produces: `BaseNotifier`, `GenericWebhookNotifier`, `create_notifier(cfg)`

- [ ] **Step 1: Write failing test for notifier**

Create `tests/test_notifier.py`:

```python
import pytest
from unittest.mock import AsyncMock
from inspector.notifier import GenericWebhookNotifier, create_notifier
from inspector.config import NotificationsConfig


async def test_generic_notifier_success(httpx_mock):
    httpx_mock.add_response(status_code=200)
    cfg = NotificationsConfig(type="generic", webhook_url_env="W", max_retries=3)
    n = GenericWebhookNotifier(cfg, "http://example.com/hook")
    ok = await n.send({"type": "test"})
    assert ok is True
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_notifier.py -v
```

Expected: ImportError

- [ ] **Step 3: Write notifier.py**

Create `inspector/notifier.py`:

```python
from __future__ import annotations
from abc import ABC, abstractmethod
import json
import httpx
from inspector.config import NotificationsConfig


class BaseNotifier(ABC):
    @abstractmethod
    async def send(self, payload: dict) -> bool:
        raise NotImplementedError


class GenericWebhookNotifier(BaseNotifier):
    def __init__(self, cfg: NotificationsConfig, url: str, timeout: float = 10.0):
        self.cfg = cfg
        self.url = url
        self.timeout = timeout

    async def send(self, payload: dict) -> bool:
        headers = {"Content-Type": "application/json"}
        body = self._format(payload)
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(self.url, headers=headers, json=body)
                return resp.status_code < 400
        except Exception:
            return False

    def _format(self, payload: dict) -> dict:
        if self.cfg.format == "text":
            return {
                "type": payload.get("type"),
                "title": payload.get("title", "Node Inspector"),
                "content": payload.get("message", json.dumps(payload, ensure_ascii=False)),
            }
        return payload  # markdown_card or raw


class WPSNotifier(BaseNotifier):
    def __init__(self, cfg: NotificationsConfig, url: str, timeout: float = 10.0):
        self.cfg = cfg
        self.url = url
        self.timeout = timeout

    async def send(self, payload: dict) -> bool:
        # Placeholder: WPS-specific formatter to be added when payload format is known
        return await GenericWebhookNotifier(self.cfg, self.url, self.timeout).send(payload)


def create_notifier(cfg: NotificationsConfig) -> BaseNotifier:
    url = cfg.resolve_webhook_url()
    if cfg.type == "wps":
        return WPSNotifier(cfg, url)
    return GenericWebhookNotifier(cfg, url)
```

- [ ] **Step 4: Install pytest-httpx if needed**

Update `pyproject.toml` dev dependencies to include `pytest-httpx>=0.25.0` and run:

```bash
pip install -e ".[dev]"
```

- [ ] **Step 5: Run test to verify it passes**

```bash
pytest tests/test_notifier.py -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add inspector/notifier.py tests/test_notifier.py pyproject.toml
git commit -m "feat: abstract notifier with generic webhook adapter"
```

---

### Task 7: 异步调度器与主入口

**Files:**
- Create: `inspector/scheduler.py`
- Create: `main.py`
- Test: `tests/test_scheduler.py` (mock test)

**Interfaces:**
- Consumes: `Collector`, `Alerter`, `BaseNotifier`, `SqliteStore`, `Settings`
- Produces: `run_inspection_cycle(...)` and async `main()`

- [ ] **Step 1: Write failing test for scheduler lock**

Create `tests/test_scheduler.py`:

```python
import asyncio
from unittest.mock import AsyncMock
import pytest
from inspector.scheduler import run_inspection_cycle


async def test_run_cycle_skips_when_locked():
    collector = AsyncMock()
    alerter = AsyncMock()
    notifier = AsyncMock()
    store = AsyncMock()
    cfg = AsyncMock()

    Collector.is_collecting = True
    await run_inspection_cycle(collector, alerter, notifier, store, cfg)
    collector.collect_all.assert_not_awaited()
    Collector.is_collecting = False
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_scheduler.py -v
```

Expected: ImportError

- [ ] **Step 3: Write scheduler.py**

Create `inspector/scheduler.py`:

```python
from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from inspector.alerter import Alerter
from inspector.collector import Collector
from inspector.config import Settings
from inspector.metrics import flatten_metrics
from inspector.notifier import BaseNotifier
from inspector.store import SqliteStore

logger = logging.getLogger(__name__)
_is_collecting = False


class SettingsHolder:
    """Allows configuration hot-reload without recreating scheduled jobs."""
    def __init__(self, settings: Settings):
        self.settings = settings


async def run_inspection_cycle(collector: Collector, alerter: Alerter, notifier: BaseNotifier,
                               store: SqliteStore, cfg_holder: SettingsHolder) -> None:
    global _is_collecting
    if _is_collecting:
        logger.warning("Previous inspection cycle is still running; skipping this round")
        return

    cfg = cfg_holder.settings
    _is_collecting = True
    try:
        await _retry_pending_webhooks(store, notifier, cfg)
        metrics_list = await collector.collect_all()
        for metrics in metrics_list:
            await store.write_node_status(metrics)
            records = flatten_metrics(metrics)
            await store.write_metrics(records)

            events = await alerter.evaluate(metrics)
            for event in events:
                payload = _event_to_payload(event)
                ok = await notifier.send(payload)
                if not ok:
                    await store.enqueue_webhook(payload)

        await _send_periodic_report(store, notifier)
        await store.cleanup_metrics(cfg.storage.retain_days)
    except Exception as e:
        logger.exception("Inspection cycle failed: %s", e)
    finally:
        _is_collecting = False


async def _retry_pending_webhooks(store: SqliteStore, notifier: BaseNotifier, cfg: Settings) -> None:
    pending = await store.dequeue_pending_webhooks(limit=10)
    for record_id, payload, attempts in pending:
        ok = await notifier.send(payload)
        if ok:
            await store.delete_webhook(record_id)
        else:
            if attempts + 1 >= cfg.notifications.max_retries:
                await store.mark_webhook_dead(record_id)
            else:
                next_retry = datetime.now(timezone.utc) + timedelta(
                    seconds=cfg.notifications.retry_interval_seconds)
                await store.update_webhook_retry(record_id, attempts + 1, next_retry)


async def _send_periodic_report(store: SqliteStore, notifier: BaseNotifier) -> None:
    statuses = await store.list_node_status()
    payload = {
        "type": "periodic_report",
        "title": "GPU 节点巡检报告",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "node_count": len(statuses),
        "online_count": sum(1 for s in statuses if s["reachable"]),
        "nodes": [
            {
                "name": s["node"],
                "reachable": bool(s["reachable"]),
                "summary": s["summary"],
                "last_check_at": s["last_check_at"],
            }
            for s in statuses
        ],
    }
    ok = await notifier.send(payload)
    if not ok:
        await store.enqueue_webhook(payload)


def _event_to_payload(event) -> dict:
    return {
        "type": event.type,
        "node": event.node,
        "rule": event.rule,
        "value": event.value,
        "threshold": event.threshold,
        "message": event.message,
        "timestamp": event.timestamp.isoformat(),
    }
```

- [ ] **Step 4: Write main.py**

Create `main.py`:

```python
from __future__ import annotations
import asyncio
import logging
import signal
import sys
from pathlib import Path
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import uvicorn
from inspector.alerter import Alerter
from inspector.collector import Collector
from inspector.config import Settings, load_config
from inspector.dashboard import create_app
from inspector.notifier import create_notifier
from inspector.scheduler import SettingsHolder, run_inspection_cycle
from inspector.store import SqliteStore

_logger = logging.getLogger(__name__)
_config_path = Path("config.yaml")
_settings_holder: SettingsHolder | None = None
_store: SqliteStore | None = None


def setup_logging(level: str):
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


async def main():
    global _settings_holder, _store
    settings = load_config(_config_path)
    _settings_holder = SettingsHolder(settings)
    setup_logging(settings.app.log_level)

    _store = SqliteStore(settings.storage.path)
    await _store.setup()

    collector = Collector(settings, _store)
    alerter = Alerter(settings.alert_rules, _store)
    notifier = create_notifier(settings.notifications)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_inspection_cycle,
        "interval",
        minutes=settings.schedule.interval_minutes,
        args=(collector, alerter, notifier, _store, _settings_holder),
        id="inspection_cycle",
        replace_existing=True,
    )
    scheduler.start()

    app = create_app(settings, _store)
    config = uvicorn.Config(app, host=settings.dashboard.host, port=settings.dashboard.port, log_level="info")
    server = uvicorn.Server(config)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGHUP,):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(reload_config()))

    _logger.info("Service started")
    await server.serve()


async def reload_config():
    global _settings_holder
    try:
        new_settings = load_config(_config_path)
        _settings_holder.settings = new_settings
        _logger.info("Configuration reloaded")
    except Exception:
        _logger.exception("Failed to reload config, keeping current config")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
```

- [ ] **Step 6: Run scheduler test**

```bash
pytest tests/test_scheduler.py -v
```

Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add main.py inspector/scheduler.py tests/test_scheduler.py inspector/store.py
git commit -m "feat: async scheduler, retry queue, and main entry"
```

---

### Task 8: FastAPI Web 看板

**Files:**
- Create: `inspector/dashboard.py`
- Create templates directory and `index.html`
- Test: `tests/test_dashboard.py`

**Interfaces:**
- Consumes: `Settings`, `SqliteStore`
- Produces: `create_app(cfg, store) -> FastAPI`

- [ ] **Step 1: Write failing test for dashboard auth**

Create `tests/test_dashboard.py`:

```python
import os
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient
from inspector.config import DashboardConfig, Settings
from inspector.dashboard import create_app
from inspector.store import SqliteStore


@pytest_asyncio.fixture
async def app(tmp_path):
    db_path = tmp_path / "test.db"
    store = SqliteStore(str(db_path))
    await store.setup()
    cfg = Settings(nodes=[], dashboard=DashboardConfig(enabled=True, host="127.0.0.1", port=8080, token_env="TK"))
    os.environ["TK"] = "secret"
    return create_app(cfg, store)


def test_dashboard_requires_token(app):
    client = TestClient(app)
    resp = client.get("/")
    assert resp.status_code == 401

    resp = client.get("/", headers={"X-Inspect-Token": "secret"})
    assert resp.status_code == 200
```

- [ ] **Step 2: Run test to verify it fails**

```bash
pytest tests/test_dashboard.py -v
```

Expected: ImportError

- [ ] **Step 3: Write dashboard.py**

Create `inspector/dashboard.py`:

```python
from __future__ import annotations
from typing import Annotated
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.security import APIKeyHeader
from fastapi.templating import Jinja2Templates
from inspector.config import Settings
from inspector.store import SqliteStore

API_KEY_HEADER = APIKeyHeader(name="X-Inspect-Token", auto_error=False)


def create_app(cfg: Settings, store: SqliteStore) -> FastAPI:
    app = FastAPI(title="GPU Node Inspector")
    templates = Jinja2Templates(directory="templates")

    expected_token = cfg.dashboard.resolve_token()

    async def verify_token(token: Annotated[str | None, Depends(API_KEY_HEADER)]):
        if not expected_token:
            return True
        if token != expected_token:
            raise HTTPException(status_code=401, detail="Invalid or missing token")
        return True

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request, _=Depends(verify_token)):
        statuses = await store.list_node_status()
        return templates.TemplateResponse("index.html", {
            "request": request,
            "statuses": statuses,
            "node_count": len(statuses),
            "online_count": sum(1 for s in statuses if s["reachable"]),
        })

    @app.get("/api/status")
    async def api_status(_=Depends(verify_token)):
        return {"nodes": await store.list_node_status()}

    @app.get("/api/alerts")
    async def api_alerts(_=Depends(verify_token)):
        return {"alerts": await store.list_alert_states()}

    return app
```

- [ ] **Step 4: Create templates/index.html**

Create `templates/index.html`:

```html
<!DOCTYPE html>
<html>
<head>
    <title>GPU Node Inspector</title>
    <style>
        body { font-family: sans-serif; margin: 2rem; background: #f5f5f5; }
        .card { background: white; border-radius: 8px; padding: 1rem; margin-bottom: 1rem; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }
        .online { color: green; }
        .offline { color: red; }
        .summary { font-size: 0.9rem; color: #555; }
    </style>
</head>
<body>
    <h1>GPU Node Inspector</h1>
    <div class="card">
        <p>节点总数: {{ node_count }} | 在线: {{ online_count }}</p>
    </div>
    {% for s in statuses %}
    <div class="card">
        <h3>{{ s.node }} <span class="{{ 'online' if s.reachable else 'offline' }}">{{ 'Online' if s.reachable else 'Offline' }}</span></h3>
        <p class="summary">{{ s.summary }}</p>
        <p>Last check: {{ s.last_check_at }}</p>
    </div>
    {% endfor %}
</body>
</html>
```

- [ ] **Step 5: Run dashboard test**

```bash
pytest tests/test_dashboard.py -v
```

Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add inspector/dashboard.py templates/index.html tests/test_dashboard.py inspector/store.py
git commit -m "feat: fastapi dashboard with token auth"
```

---

### Task 9: 部署脚本与运维配置

**Files:**
- Create: `scripts/install.sh`
- Create: `scripts/run.sh`
- Create: `scripts/logrotate.conf`
- Update: `config.example.yaml`

**Interfaces:**
- Produces: Installable systemd service

- [ ] **Step 1: Write scripts/install.sh**

Create `scripts/install.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="/opt/gpu-node-inspector"
CONFIG_DIR="/etc/gpu-node-inspector"
LOG_DIR="/var/log/gpu-node-inspector"
USER="inspector"

if [ "$EUID" -ne 0 ]; then
    echo "Please run as root"
    exit 1
fi

# Create user
if ! id "$USER" &>/dev/null; then
    useradd --system --no-create-home --home-dir "$INSTALL_DIR" "$USER"
fi

# Install directory
mkdir -p "$INSTALL_DIR"
cp -r inspector main.py requirements.txt pyproject.toml templates "$INSTALL_DIR/"
mkdir -p "$INSTALL_DIR"/data "$INSTALL_DIR"/logs
chown -R "$USER:$USER" "$INSTALL_DIR"

# Config directory
mkdir -p "$CONFIG_DIR"
if [ ! -f "$CONFIG_DIR/config.yaml" ]; then
    cp config.example.yaml "$CONFIG_DIR/config.yaml"
    echo "Please edit $CONFIG_DIR/config.yaml"
fi
if [ ! -f "$CONFIG_DIR/env" ]; then
    touch "$CONFIG_DIR/env"
    chmod 600 "$CONFIG_DIR/env"
    chown "$USER:$USER" "$CONFIG_DIR/env"
    echo "Please edit $CONFIG_DIR/env with secrets"
fi
chmod 600 "$CONFIG_DIR/config.yaml"

# Log directory
mkdir -p "$LOG_DIR"
chown "$USER:$USER" "$LOG_DIR"

# Virtualenv
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --upgrade pip
"$INSTALL_DIR/venv/bin/pip" install -r "$INSTALL_DIR/requirements.txt"

# Systemd service
cat > /etc/systemd/system/gpu-node-inspector.service <<EOF
[Unit]
Description=GPU Node Inspector
After=network.target

[Service]
Type=simple
User=$USER
Group=$USER
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$CONFIG_DIR/env
ExecStart=$INSTALL_DIR/venv/bin/python -m main
Restart=always
RestartSec=5
StandardOutput=append:$LOG_DIR/inspector.log
StandardError=append:$LOG_DIR/inspector.log

[Install]
WantedBy=multi-user.target
EOF

# Logrotate
cp scripts/logrotate.conf /etc/logrotate.d/gpu-node-inspector

# SSH key permissions hint
echo "Ensure your SSH private key files are chmod 600 and owned by $USER"

systemctl daemon-reload
systemctl enable gpu-node-inspector.service
echo "Run: systemctl start gpu-node-inspector"
```

- [ ] **Step 2: Write scripts/run.sh**

```bash
#!/usr/bin/env bash
set -euo pipefail
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp -n config.example.yaml config.yaml || true
echo "Edit config.yaml and run: python -m main"
```

- [ ] **Step 3: Write scripts/logrotate.conf**

```
/var/log/gpu-node-inspector/*.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    create 0600 inspector inspector
    sharedscripts
    postrotate
        systemctl reload gpu-node-inspector || true
    endscript
}
```

- [ ] **Step 4: Make scripts executable**

```bash
chmod +x scripts/install.sh scripts/run.sh
```

- [ ] **Step 5: Commit**

```bash
git add scripts/install.sh scripts/run.sh scripts/logrotate.conf
git commit -m "feat: systemd install script and logrotate config"
```

---

### Task 10: 集成测试与清理

**Files:**
- Update: `tests/conftest.py`
- Update: `pyproject.toml`
- Update: `README.md`
- Test: `tests/test_integration.py`

- [ ] **Step 1: Write tests/conftest.py**

Create `tests/conftest.py`:

```python
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
```

- [ ] **Step 2: Write integration test**

Create `tests/test_integration.py`:

```python
import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from inspector.alerter import Alerter
from inspector.collector import Collector
from inspector.config import AlertRules, NodeConfig, NotificationsConfig, Settings, StorageConfig
from inspector.models import GpuMetric, NodeMetrics, NetworkMetric, SystemMetric
from inspector.notifier import GenericWebhookNotifier
from inspector.scheduler import SettingsHolder, run_inspection_cycle
from inspector.store import SqliteStore


async def test_full_cycle(tmp_path):
    db_path = tmp_path / "test.db"
    store = SqliteStore(str(db_path))
    await store.setup()

    cfg = Settings(
        nodes=[NodeConfig(name="n1", host="1.2.3.4", user="u")],
        storage=StorageConfig(path=str(db_path), retain_days=7),
        alert_rules=AlertRules(gpu_temp_c=80, stability_cycles=1),
        notifications=NotificationsConfig(type="generic", webhook_url_env="W", max_retries=0),
    )
    cfg_holder = SettingsHolder(cfg)

    collector = MagicMock(spec=Collector)
    collector.collect_all = AsyncMock(return_value=[
        NodeMetrics(
            node="n1",
            timestamp=datetime.now(timezone.utc),
            reachable=True,
            gpus=[GpuMetric(index=0, name="T4", temperature_c=85.0, utilization_gpu_pct=10,
                            utilization_memory_pct=20, memory_used_mb=1000,
                            memory_total_mb=16000, power_draw_w=30, fan_speed_pct=30)],
            system=SystemMetric(disk_used_pct=50),
            networks=[NetworkMetric(target="8.8.8.8", packet_loss_pct=0.0, avg_latency_ms=2.0)],
        )
    ])

    alerter = Alerter(cfg.alert_rules, store)
    notifier = GenericWebhookNotifier(cfg.notifications, "http://localhost:9999/hook")
    notifier.send = AsyncMock(return_value=True)

    await run_inspection_cycle(collector, alerter, notifier, store, cfg_holder)

    status = await store.get_node_status("n1")
    assert status is not None
    assert status["reachable"] == 1
    notifier.send.assert_awaited()
```

- [ ] **Step 3: Run all tests**

```bash
pytest -v
```

Expected: All PASS

- [ ] **Step 4: Write README.md**

Create `README.md` with setup, config, run, deploy instructions.

```markdown
# GPU Node Inspector

轻量级 GPU 节点状态巡检服务。

## 快速开始

```bash
bash scripts/run.sh
# 编辑 config.yaml
python -m main
```

## 部署到金山云服务器

```bash
sudo bash scripts/install.sh
sudo systemctl start gpu-node-inspector
sudo systemctl status gpu-node-inspector
```

## 查看日志

```bash
sudo tail -f /var/log/gpu-node-inspector/inspector.log
```
```

- [ ] **Step 5: Final commit**

```bash
git add README.md tests/conftest.py tests/test_integration.py
git commit -m "test: integration test and project readme"
```

---

## 计划自检

### 1. Spec 覆盖检查

| Spec 需求 | 对应任务 |
|---|---|
| AsyncIOScheduler + 统一事件循环 | Task 7 |
| 采集防堆积锁 | Task 7 (`_is_collecting`) |
| SQLite 并发锁 | Task 2 |
| SSH 私钥文件化 + 600 权限 | Task 1 (`SshConfig.resolve_private_key_path`) |
| `asyncssh.run(command, args=[...])` | Task 4 |
| 周期性 5 分钟报告 | Task 7 |
| 阈值告警 + 防抖 | Task 5 |
| Webhook 推送 + 失败重试队列 | Task 6, Task 7 |
| Web 看板鉴权 | Task 8 |
| 指标 TTL 清理 | Task 2 + Task 7 |
| systemd + logrotate | Task 9 |

### 2. Placeholder 扫描

- 无 TBD / TODO
- 所有任务包含可运行代码和测试命令
- WPS 适配器预留为 GenericWebhookNotifier 的扩展点

### 3. 类型一致性

- `Settings` 在 Task 1 定义，后续任务一致使用
- `NodeMetrics`, `AlertEvent` 在 Task 1 `models.py` 定义
- `SqliteStore` 接口在 Task 2 定义，后续通过新增方法扩展，签名一致

