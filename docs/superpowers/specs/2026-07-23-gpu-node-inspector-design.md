# 轻量级 GPU 与节点状态巡查小助手 — 技术方案文档

**版本**: v1.0  
**日期**: 2026-07-23  
**状态**: 待评审  

---

## 1. 项目背景与目标

本工具部署在金山云云服务器上，用于自动采集一组 NVIDIA GPU 节点（Linux）的运行状态，并通过 WPS 协作机器人 webhook 将状态汇总和告警信息推送到协作群，形成“采集 → 判断 → 推送”的闭环。

### 核心目标

- **自动化巡检**：每 5 分钟并发采集所有节点的 GPU、系统、网络指标。
- **阈值告警**：对 GPU 温度、显存、磁盘占用、节点不可达等场景触发告警，并支持防抖。
- **闭环通知**：所有状态报告和告警通过 webhook 推送到工作群。
- **轻量看板**：提供基于 Web 的简易状态看板与 JSON API。
- **安全可运维**：密钥文件化、命令参数化、日志轮转、systemd 托管。

---

## 2. 需求范围

### 2.1 功能需求

| 编号 | 需求 | 优先级 |
|---|---|---|
| F1 | 通过 SSH 并发登录多个 Linux 节点采集 NVIDIA GPU 指标 | P0 |
| F2 | 采集系统指标（CPU、内存、磁盘、负载、运行时长） | P0 |
| F3 | 采集网络连通性（ping 目标地址） | P0 |
| F4 | 每 5 分钟生成并推送周期性状态报告 | P0 |
| F5 | 支持 GPU 温度、显存、磁盘、节点不可达阈值告警 | P0 |
| F6 | 告警触发后“只报一次”，恢复后再报“已恢复” | P0 |
| F7 | 通过 WPS 协作机器人 webhook 推送消息 | P0 |
| F8 | 提供 Web 看板与 JSON API | P1 |
| F9 | 告警防抖（连续 N 个周期超标才触发） | P1 |
| F10 | Webhook 发送失败持久化队列，支持重试 | P1 |
| F11 | 历史指标自动清理（TTL） | P1 |
| F12 | 配置热重载（SIGHUP） | P1 |

### 2.2 非功能需求

| 编号 | 需求 | 说明 |
|---|---|---|
| NF1 | 轻量部署 | 单进程 Python 服务，systemd 托管 |
| NF2 | 并发采集 | 节点之间并行，避免单点阻塞 |
| NF3 | 资源可控 | 单进程低内存占用，SQLite 本地存储 |
| NF4 | 安全 | SSH 密钥文件化、权限 600、命令参数化防止注入 |
| NF5 | 可维护 | 配置与代码分离，模块职责清晰 |

---

## 3. 总体架构

```
┌─────────────────────────────────────────────────────────────┐
│                    Kingsoft Cloud Server                    │
│                                                             │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────┐   │
│  │  Scheduler   │   │  Web Dashboard│   │  Notifier    │   │
│  │(AsyncIOSched)│   │  (FastAPI)    │   │  (Webhook)    │   │
│  └──────┬───────┘   └──────┬───────┘   └──────▲───────┘   │
│         │                  │                    │          │
│         ▼                  ▼                    │          │
│  ┌─────────────────────────────────────────┐   │          │
│  │           Inspector Core                │   │          │
│  │  Collector → Metrics → Alerter → Store  │   │          │
│  └─────────────────────────────────────────┘   │          │
│                        │                       │          │
│                        ▼                       │          │
│                 ┌─────────────┐                │          │
│                 │   SQLite    │                │          │
│                 │  (state)    │────────────────┘          │
│                 └─────────────┘                           │
└──────────────────────┬────────────────────────────────────┘
                       │ SSH (asyncssh)
        ┌──────────────┼──────────────┐
        ▼              ▼              ▼
   ┌─────────┐   ┌─────────┐   ┌─────────┐
   │ Node 01 │   │ Node 02 │   │ Node N  │
   │  Linux  │   │  Linux  │   │  Linux  │
   │  NVIDIA │   │  NVIDIA │   │  NVIDIA │
   └─────────┘   └─────────┘   └─────────┘
                       │
                       ▼
            ┌─────────────────────┐
            │ WPS 协作机器人 Webhook│
            │   （通用适配层）      │
            └─────────────────────┘
```

### 关键设计决策

- **单进程架构**：`main.py` 统一拉起事件循环，AsyncIOScheduler 调度采集任务，FastAPI 作为子任务运行在同一进程中。
- **全异步 I/O**：所有 SSH、HTTP、数据库访问均为异步，避免阻塞。
- **统一事件循环**：`asyncio.run(main())` 仅创建一次事件循环，供调度器、asyncssh、FastAPI 共用。
- **本地 SQLite**：保存指标、告警状态、webhook 重试队列，实现状态持久化和去重。

---

## 4. 模块设计

### 4.1 `main.py` — 启动入口

职责：初始化配置、创建事件循环、启动调度器和 Web 服务、注册信号处理。

```python
async def main():
    cfg = load_config()
    store = SqliteStore(cfg.storage.path)
    await store.setup()
    collector = Collector(cfg, store)
    alerter = Alerter(cfg, store)
    notifier = create_notifier(cfg)
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_inspection_cycle,
        "interval",
        minutes=cfg.schedule.interval_minutes,
        args=(collector, alerter, notifier, store, cfg)
    )
    scheduler.start()
    await run_dashboard(cfg, store)

if __name__ == "__main__":
    asyncio.run(main())
```

### 4.2 `scheduler.py` — 异步调度 + 防堆积锁

- 使用 `AsyncIOScheduler`。
- 维护内存布尔锁 `_is_collecting`。
- 任务触发时：
  - 若 `_is_collecting == True`：打印日志，跳过本轮。
  - 否则：上锁 → 执行 `run_inspection_cycle()` → `finally` 释放锁。
- 禁止在同步调度器中调用 `asyncio.run()`。

### 4.3 `collector.py` — SSH 并发采集

- 使用 `asyncssh` 建立密钥认证连接。
- 所有节点并发采集，超时控制单节点命令 30 秒。
- 对每台节点顺序执行：
  1. SSH 可达性检测
  2. `nvidia-smi` 查询 GPU
  3. `nvidia-smi pmon` 查询进程（可选）
  4. `df -h`、`free -m`、`cat /proc/loadavg`、`uptime`
  5. `ping -c 3 <target>`
- `ping` 命令使用参数列表调用：

```python
await conn.run("ping", args=["-c", "3", target])
```

### 4.4 `metrics.py` — 指标解析

- 将 `nvidia-smi --query-gpu=... --format=csv,noheader` 解析为结构化字典。
- 将系统命令输出解析为 CPU、内存、磁盘、负载等字段。
- 解析失败时保留原始输出到日志。

### 4.5 `alerter.py` — 阈值判断与告警事件生成

职责：接收解析后的指标，判断阈值，生成标准化告警事件，**不读写数据库，不发 webhook**。

告警事件结构：

```json
{
  "type": "alert_triggered",
  "node": "node-01",
  "rule": "gpu_temp_c",
  "value": 89,
  "threshold": 85,
  "message": "GPU temperature 89°C exceeds threshold 85°C",
  "timestamp": "2026-07-23T10:05:00+08:00"
}
```

### 4.6 `store.py` — 状态持久化（带并发锁）

- 使用 `asyncio.Lock` 串行化所有 SQLite 读写。
- 提供方法：
  - `write_node_status(node, metrics, timestamp)`
  - `write_metrics(records)`
  - `get_alert_state(node, rule)`
  - `update_alert_state(node, rule, state)`
  - `enqueue_webhook(payload)`
  - `dequeue_pending_webhooks(limit)`
  - `mark_webhook_dead(id)`
  - `cleanup_metrics(retain_days)`
- 事务尽量短小，禁止长事务持有锁。

### 4.7 `notifier.py` — Webhook 推送（抽象适配层）

```python
class BaseNotifier(ABC):
    @abstractmethod
    async def send(self, payload: dict) -> bool: ...

class WPSNotifier(BaseNotifier): ...
class GenericWebhookNotifier(BaseNotifier): ...
```

配置 `notifier.type` 选择实例：`wps`、`generic`。

`notifier.py` 只负责：
- 接收标准化告警/报告事件。
- 按平台格式转换 payload。
- 发起 HTTP POST。
- 返回成功/失败，由调用方决定是否入队重试。

**禁止**：读取或修改告警状态、直接读取历史指标做逻辑判断。

---

## 5. 数据模型

### 5.1 `metrics` 表

| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK | 自增 |
| node | TEXT | 节点名 |
| category | TEXT | gpu/system/network |
| name | TEXT | 指标名 |
| value | REAL | 数值 |
| unit | TEXT | 单位 |
| labels | TEXT(JSON) | 附加标签 |
| timestamp | DATETIME | 采集时间 |
| raw_output | TEXT | 原始输出（可选） |

### 5.2 `node_status` 表

| 字段 | 类型 | 说明 |
|---|---|---|
| node | TEXT PK | 节点名 |
| reachable | BOOLEAN | 是否可达 |
| summary | TEXT | 摘要 |
| last_check_at | DATETIME | 最后检查时间 |
| raw_metrics | TEXT(JSON) | 原始完整指标 |

### 5.3 `alert_states` 表

| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK | 自增 |
| node | TEXT | 节点名 |
| rule | TEXT | 规则名 |
| state | TEXT | normal / triggered / recovered |
| breach_cycles | INTEGER | 连续超标周期数 |
| last_value | REAL | 上次指标值 |
| triggered_at | DATETIME | 首次触发时间 |
| updated_at | DATETIME | 最后更新时间 |

唯一约束：`(node, rule)`

### 5.4 `pending_webhooks` 表

| 字段 | 类型 | 说明 |
|---|---|---|
| id | INTEGER PK | 自增 |
| payload | TEXT(JSON) | 待发送 payload |
| attempts | INTEGER | 已尝试次数 |
| created_at | DATETIME | 创建时间 |
| next_retry_at | DATETIME | 下次重试时间 |
| status | TEXT | pending / dead |

---

## 6. 配置设计

配置文件：`config.yaml`  
敏感信息：环境变量

### 6.1 示例配置

```yaml
app:
  name: "gpu-node-inspector"
  log_level: INFO

schedule:
  interval_minutes: 5

storage:
  path: "data/inspector.db"
  retain_days: 7  # 指标保留 7 天

nodes:
  - name: node-01
    host: 192.168.1.10
    user: ubuntu
    ssh_private_key_path_env: "SSH_PRIVATE_KEY_PATH_NODE_01"
    # 或全局指定：使用 config.ssh 段
    gpu_vendor: nvidia
    ping_targets:
      - "8.8.8.8"
      - "gateway"

ssh:
  # 全局默认私钥路径；节点级可覆盖（节点级优先级高于全局）
  private_key_path_env: "SSH_PRIVATE_KEY_PATH"
  connect_timeout: 10
  command_timeout: 30

alert_rules:
  gpu_temp_c: 85
  gpu_memory_pct: 90
  disk_usage_pct: 85
  node_unreachable: true  # true 表示启用节点不可达检测；该布尔规则没有数值阈值
  stability_cycles: 2  # 连续 2 个周期超标才触发

notifications:
  type: generic  # wps | generic
  webhook_url_env: "WPS_WEBHOOK_URL"
  format: markdown_card  # text | markdown_card
  include_gpu_processes: true
  max_retries: 5
  retry_interval_seconds: 60

dashboard:
  enabled: true
  host: "127.0.0.1"
  port: 8080
  token_env: "DASHBOARD_TOKEN"  # 为空则关闭鉴权
```

### 6.2 环境变量

| 变量名 | 说明 |
|---|---|
| `WPS_WEBHOOK_URL` | 机器人 webhook URL |
| `SSH_PRIVATE_KEY_PATH` | 默认 SSH 私钥文件路径 |
| `DASHBOARD_TOKEN` | Web 看板访问令牌 |
| `LOG_LEVEL` | 日志级别（可选） |

### 6.3 安全校验

- 启动时校验私钥文件权限，必须为 `0o600`，否则抛出异常。
- `/etc/gpu-node-inspector/env` 权限必须为 `600`。

---

## 7. 告警与通知流程

### 7.1 周期采集流程

```
scheduler 触发
    ↓
检查 is_collecting 锁
    ↓
并发采集所有节点
    ↓
解析指标
    ↓
alerter 判断阈值
    ↓
store 更新 alert_states（含 breach_cycles）
    ↓
生成新触发/恢复事件
    ↓
notifier 推送事件
    ↓
推送失败 → 入 pending_webhooks 队列
    ↓
清理过期 metrics
    ↓
释放锁
```

### 7.2 告警状态机

```
          ┌─────────────┐
          │   normal    │
          └──────┬──────┘
                 │ metric > threshold
                 ▼
          ┌─────────────┐
          │  breaching  │ (breach_cycles += 1)
          └──────┬──────┘
                 │ cycles >= stability_cycles
                 ▼
          ┌─────────────┐
          │  triggered  │ ──► 发送 alert_triggered
          └──────┬──────┘
                 │ metric <= threshold
                 ▼
          ┌─────────────┐
          │  recovered  │ ──► 发送 alert_recovered
          └─────────────┘
                 │
                 ▼
               normal
```

### 7.3 Webhook 重试流程

1. 每次调用 `notifier.send(payload)` 返回 `bool`。
2. 失败时写入 `pending_webhooks`，初始 `attempts=1`。
3. 下一轮采集启动时，先检查队列中 `status='pending'` 且 `next_retry_at <= now()` 的记录，尝试重发。
4. `attempts >= max_retries` 时标记为 `dead`，不再发送。
5. 死信记录保留在数据库，供人工排查。

---

## 8. Web 看板设计

基于 FastAPI + Jinja2，仅做轻量展示。

### 8.1 路由

| 路由 | 说明 | 鉴权 |
|---|---|---|
| `GET /` | HTML 看板首页 | X-Inspect-Token |
| `GET /api/status` | 所有节点当前状态 JSON | X-Inspect-Token |
| `GET /api/alerts` | 当前活跃告警 JSON | X-Inspect-Token |
| `GET /api/history?node=&rule=&limit=` | 历史指标 JSON | X-Inspect-Token |
| `GET /health` | 服务健康检查 | 无需鉴权 |

### 8.2 鉴权方式

- 请求头：`X-Inspect-Token: <token>`
- 服务端比对 `DASHBOARD_TOKEN` 环境变量。
- 不匹配返回 HTTP 401。
- 若 `DASHBOARD_TOKEN` 为空字符串，则关闭鉴权（仅调试使用）。

### 8.3 看板首页内容

- 节点总数、在线数、告警数。
- 每个节点的 GPU 摘要卡（温度、利用率、显存）。
- 系统摘要（CPU、内存、磁盘）。
- 最近告警列表。
- 最近一次巡检时间。

---

## 9. 部署方案

### 9.1 运行环境

- **OS**: Linux（推荐 Ubuntu 22.04 LTS 或同类）
- **Python**: 3.9+
- **依赖**: `asyncssh`, `apscheduler`, `fastapi`, `uvicorn`, `httpx`, `pydantic`, `jinja2`

### 9.2 目录结构

```
/opt/gpu-node-inspector/
├── main.py
├── inspector/
├── config.yaml
├── data/
│   └── inspector.db
├── logs/
│   └── inspector.log
├── venv/
├── requirements.txt
└── scripts/
    ├── install.sh
    └── run.sh
```

### 9.3 安装脚本 `scripts/install.sh`

主要步骤：

1. 创建系统用户 `inspector`。
2. 创建 `/opt/gpu-node-inspector` 目录，设置属主。
3. 创建 Python venv 并安装依赖。
4. 创建 `/etc/gpu-node-inspector/` 目录：
   - `config.yaml`
   - `env`（systemd 环境文件）
5. 设置私钥、env 文件权限为 `600`。
6. 创建并启用 systemd service：`gpu-node-inspector.service`。
7. 启动服务。
8. 输出日志路径和状态查看命令。

### 9.4 systemd 服务文件

```ini
[Unit]
Description=GPU Node Inspector
After=network.target

[Service]
Type=simple
User=inspector
Group=inspector
WorkingDirectory=/opt/gpu-node-inspector
EnvironmentFile=/etc/gpu-node-inspector/env
ExecStart=/opt/gpu-node-inspector/venv/bin/python -m main
Restart=always
RestartSec=5
StandardOutput=append:/var/log/gpu-node-inspector/inspector.log
StandardError=append:/var/log/gpu-node-inspector/inspector.log

[Install]
WantedBy=multi-user.target
```

### 9.5 日志轮转

创建 `/etc/logrotate.d/gpu-node-inspector`：

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
        systemctl reload gpu-node-inspector
    endscript
}
```

---

## 10. 安全设计

| 项目 | 措施 |
|---|---|
| SSH 认证 | 仅使用私钥文件，禁止把完整私钥写入环境变量 |
| 私钥权限 | 强制校验 `0o600`，否则拒绝启动 |
| 命令执行 | 使用 `asyncssh.run(command, args=[...])`，禁止字符串拼接 |
| 节点权限 | 在节点上只执行只读命令（nvidia-smi、df、free、ping 等） |
| 看板访问 | Token 头鉴权；生产环境配 Nginx + HTTPS |
| 敏感文件 | `/etc/gpu-node-inspector/env` 权限 `600` |
| 日志脱敏 | 不在日志中打印私钥、webhook URL、token |

---

## 11. 配置热重载

- 捕获 `SIGHUP` 信号。
- 重新加载 `config.yaml`。
- **不中断**当前正在执行的采集任务。
- 新配置仅对**下一轮采集**生效。
- 错误配置（校验失败）时保留旧配置，打印错误日志。

---

## 12. 测试策略

| 类型 | 方法 | 覆盖点 |
|---|---|---|
| 单元测试 | pytest | `metrics.py` 解析逻辑、配置校验、告警状态机 |
| Mock 测试 | asyncssh mock | 并发采集、SSH 失败场景 |
| 集成测试 | 本地测试节点 | 端到端一次完整巡检 |
| 告警测试 | 临时调低阈值 | 验证触发、防抖、恢复通知 |
| Webhook 测试 | Mock webhook server | 验证重试队列 |
| 压力测试 | 模拟 50+ 节点 | 验证并发锁、SQLite 锁、内存占用 |

---

## 13. 风险与应对

| 风险 | 影响 | 应对措施 |
|---|---|---|
| SSH 连接数过多 | 云服务器或节点连接耗尽 | 并发量与节点数挂钩；单节点命令串行；采集锁防堆积 |
| SQLite 锁竞争 | 高并发下 database is locked | 所有 DB 操作加 `asyncio.Lock`；事务短小 |
| 节点网络抖动 | 误报不可达 | 告警防抖 + 多次探测确认 |
| Webhook 不可用 | 告警丢失 | 持久化重试队列；死信保留 |
| 私钥泄露 | 节点被入侵 | 文件化存储、600 权限、不打印日志 |
| 配置错误热重载 | 服务异常 | 校验失败保留旧配置 |

---

## 14. 后续扩展建议

- 支持 AMD GPU（`rocm-smi`）。
- 支持多 webhook 目标同时推送。
- 接入 Prometheus / Grafana 导出指标。
- 历史趋势图表。
- 多用户分权看板。

---

## 15. 评审记录

| 日期 | 评审人 | 结论 | 备注 |
|---|---|---|---|
| 2026-07-23 | - | 待评审 | 初稿 |

