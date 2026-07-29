# GPU Node Inspector

轻量级 GPU 与节点状态巡检小助手。通过 SSH 自动采集多个 NVIDIA GPU 节点的运行状态，支持阈值告警、Webhook 推送、Web 看板，部署在金山云服务器上即可使用。

## 架构概览

```
┌─────────────────────────────────────────────────┐
│              金山云服务器（巡检中枢）              │
│                                                   │
│  ┌──────────┐  ┌──────────┐  ┌──────────┐       │
│  │ Scheduler│  │Dashboard │  │ Notifier │       │
│  │(5分钟轮询)│  │(FastAPI) │  │(Webhook) │       │
│  └────┬─────┘  └────┬─────┘  └────▲─────┘       │
│       │              │             │              │
│       ▼              ▼             │              │
│  ┌─────────────────────────────┐  │              │
│  │  Collector → Alerter → Store│──┘              │
│  │  (asyncssh)  (告警)  (SQLite)│                 │
│  └─────────────────────────────┘                  │
└───────────┬───────────┬───────────┬───────────────┘
            │ SSH       │ SSH       │ SSH
            ▼           ▼           ▼
      ┌─────────┐ ┌─────────┐ ┌─────────┐
      │ Node 01 │ │ Node 02 │ │ Node N  │
      │  Linux  │ │  Linux  │ │  Linux  │
      │  NVIDIA │ │  NVIDIA │ │  NVIDIA │
      └─────────┘ └─────────┘ └─────────┘
```

## 功能特性

| 类别 | 功能 |
|------|------|
| **GPU 采集** | 温度、GPU 利用率、显存占用/总量、功耗、风扇转速、GPU 进程 |
| **系统采集** | CPU 使用率、内存使用率、磁盘使用率、负载平均值、运行时长 |
| **网络采集** | Ping 延迟、丢包率（支持自定义目标地址） |
| **阈值告警** | GPU 温度、GPU 显存、磁盘占用、节点不可达；支持防抖（连续 N 个周期超标才触发） |
| **告警恢复** | 指标恢复正常后自动推送"已恢复"通知 |
| **Webhook 推送** | 通用 HTTP Webhook 适配器，支持 WPS 协作机器人扩展 |
| **失败重试** | 推送失败自动入队，指数退避重试，超过次数标记为死信 |
| **Web 看板** | 浏览器可视化查看节点状态、系统指标、网络 Ping、告警列表 |
| **Token 鉴权** | 看板和 API 支持 Token 鉴权（请求头或 URL 参数） |
| **SQLite 持久化** | 指标历史、节点状态、告警状态、Webhook 重试队列全部持久化 |
| **指标 TTL** | 自动清理过期指标数据，防止数据库膨胀 |
| **配置热重载** | 发送 SIGHUP 信号即可重载配置，不影响正在运行的采集 |
| **systemd 服务** | 开机自启、自动重启、日志轮转 |
| **国内镜像** | 安装脚本内置阿里云 PyPI 镜源，国内服务器部署无障碍 |

## 环境要求

### 运行服务器（金山云）

- Linux（推荐 Ubuntu 22.04 LTS）
- Python >= 3.9
- 可通过 SSH 访问目标 GPU 节点

### 被监控的 GPU 节点

- Linux 系统
- 已安装 NVIDIA 驱动和 `nvidia-smi`
- SSH 服务正常运行
- 运行服务器的 SSH 公钥已加入 `~/.ssh/authorized_keys`

---

## 快速开始（本地开发）

```bash
# 1. 克隆仓库
git clone https://gitee.com/wangserran/inspection-assistant.git
cd inspection-assistant

# 2. 创建虚拟环境并安装依赖
python -m venv .venv
source .venv/bin/activate        # Linux/Mac
# .venv\Scripts\activate         # Windows
pip install -r requirements.txt

# 3. 复制示例配置并编辑
cp config.example.yaml config.yaml
# 编辑 config.yaml，填入节点信息、告警阈值、Webhook 地址等

# 4. 启动服务
python -m main
```

服务启动后：
- Web 看板：http://127.0.0.1:8080/?token=<你的token>
- 健康检查：http://127.0.0.1:8080/health
- 节点状态 API：http://127.0.0.1:8080/api/status

---

## 配置文件详解

参考 `config.example.yaml`，完整字段说明如下：

### 基础配置

```yaml
app:
  name: "gpu-node-inspector"   # 应用名称
  log_level: INFO              # 日志级别：DEBUG/INFO/WARNING/ERROR
```

### 调度配置

```yaml
schedule:
  interval_minutes: 5          # 采集周期（分钟），建议 1-15
```

### 存储配置

```yaml
storage:
  path: "data/inspector.db"    # SQLite 数据库路径（生产环境建议用绝对路径）
  retain_days: 7               # 指标数据保留天数，超过自动清理
```

### SSH 配置

```yaml
ssh:
  private_key_path_env: "SSH_PRIVATE_KEY_PATH"  # 环境变量名，值为私钥文件路径
  connect_timeout: 10          # SSH 连接超时（秒）
  command_timeout: 30          # 远程命令执行超时（秒）
```

> **安全要求**：SSH 私钥文件权限必须为 `0o600`，否则服务启动时会拒绝加载。

### 节点配置

```yaml
nodes:
  - name: gpu-node-01                # 节点名称（显示在看板和告警中）
    host: 192.168.1.10               # 节点 IP 或主机名
    user: ubuntu                     # SSH 登录用户
    ssh_private_key_path_env: "SSH_PRIVATE_KEY_PATH"  # 该节点使用的私钥环境变量（可选，覆盖全局）
    gpu_vendor: nvidia               # GPU 厂商（目前仅支持 nvidia）
    ping_targets:                    # Ping 目标列表（取第一个）
      - "8.8.8.8"

  - name: gpu-node-02
    host: 192.168.1.11
    user: root
    gpu_vendor: nvidia
    ping_targets:
      - "114.114.114.114"
```

### 告警规则

```yaml
alert_rules:
  gpu_temp_c: 85               # GPU 温度阈值（°C），超过触发告警
  gpu_memory_pct: 90           # GPU 显存占用阈值（%），超过触发告警
  disk_usage_pct: 85           # 磁盘使用率阈值（%），超过触发告警
  node_unreachable: true       # 节点不可达告警开关
  stability_cycles: 2          # 防抖：连续 N 个周期超标才触发，过滤瞬时毛刺
```

### Webhook 通知

```yaml
notifications:
  type: generic                # 适配器类型：generic（通用）/ wps（WPS 协作机器人）
  webhook_url_env: "WPS_WEBHOOK_URL"  # 环境变量名，值为 Webhook URL
  format: markdown_card        # 消息格式：text（纯文本）/ markdown_card（Markdown 卡片）
  include_gpu_processes: true  # 是否包含 GPU 进程信息
  max_retries: 5               # 推送失败最大重试次数
  retry_interval_seconds: 60   # 重试间隔（秒）
```

### Web 看板

```yaml
dashboard:
  enabled: true                # 是否启用看板
  host: "0.0.0.0"              # 监听地址（0.0.0.0 允许外部访问）
  port: 8080                   # 监听端口
  token_env: "DASHBOARD_TOKEN" # 环境变量名，值为鉴权 Token
```

### 环境变量

在 `/etc/gpu-node-inspector/env`（生产）或 `.env`（开发）中配置：

```bash
# SSH 私钥文件路径（必须，权限 0o600）
SSH_PRIVATE_KEY_PATH=/etc/gpu-node-inspector/id_rsa

# Webhook URL（必须，否则服务启动时报错）
WPS_WEBHOOK_URL=https://hooks.example.com/your-webhook

# 看板鉴权 Token（必须）
DASHBOARD_TOKEN=your-secret-token
```

---

## 部署到金山云服务器

### 首次部署

```bash
# 1. 克隆仓库
git clone https://gitee.com/wangserran/inspection-assistant.git
cd inspection-assistant

# 2. 运行安装脚本（自动创建用户、目录、虚拟环境、systemd 服务）
sudo bash scripts/install.sh

# 3. 编辑节点配置
sudo nano /etc/gpu-node-inspector/config.yaml

# 4. 编辑环境变量（密钥路径、Webhook URL、Token）
sudo nano /etc/gpu-node-inspector/env

# 5. 生成 SSH 密钥（如果没有）
sudo ssh-keygen -t rsa -b 2048 -f /etc/gpu-node-inspector/id_rsa -N ""
sudo chmod 600 /etc/gpu-node-inspector/id_rsa
sudo chown inspector:inspector /etc/gpu-node-inspector/id_rsa

# 6. 把公钥加到目标节点的 authorized_keys
sudo cat /etc/gpu-node-inspector/id_rsa.pub
# 将输出内容追加到目标节点的 ~/.ssh/authorized_keys

# 7. 测试 SSH 连通性
sudo -u inspector ssh -i /etc/gpu-node-inspector/id_rsa -o StrictHostKeyChecking=no root@<节点IP> 'echo SSH_OK'

# 8. 启动服务
sudo systemctl start gpu-node-inspector
sudo systemctl status gpu-node-inspector

# 9. 查看日志
sudo tail -f /var/log/gpu-node-inspector/inspector.log
```

### 安装脚本做了什么

`scripts/install.sh` 自动完成：

- 创建 `inspector` 系统用户
- 复制代码到 `/opt/gpu-node-inspector/`
- 创建 SQLite 数据目录 `/var/lib/gpu-node-inspector/`（通过符号链接，重装不丢数据）
- 创建 Python 虚拟环境并安装依赖（使用阿里云 PyPI 镜源）
- 复制配置到 `/etc/gpu-node-inspector/`（仅首次，不覆盖已有配置）
- 创建 systemd 服务文件
- 配置 logrotate 日志轮转
- 设置文件权限（私钥 600、配置 600）

### 目录结构（部署后）

```
/opt/gpu-node-inspector/          # 代码目录
├── inspector/                    # Python 包
├── main.py                       # 入口
├── templates/                    # 看板模板
├── venv/                         # Python 虚拟环境
├── data -> /var/lib/gpu-node-inspector  # 符号链接，数据不丢
└── requirements.txt

/etc/gpu-node-inspector/          # 配置目录
├── config.yaml                   # 节点配置、告警规则、看板设置
├── env                           # 环境变量（密钥路径、Webhook URL、Token）
└── id_rsa                        # SSH 私钥（权限 600）

/var/lib/gpu-node-inspector/      # 数据目录
└── inspector.db                  # SQLite 数据库

/var/log/gpu-node-inspector/      # 日志目录
└── inspector.log                 # 服务日志（logrotate 自动轮转）
```

---

## 添加 GPU 节点

### 前提条件

| 条件 | 说明 |
|------|------|
| 网络通 | 金山云服务器能 SSH 到目标 GPU 机器（端口 22） |
| 有 nvidia-smi | GPU 机器已安装 NVIDIA 驱动 |
| SSH 免密 | GPU 机器的 `authorized_keys` 里有 inspector 的公钥 |

### 步骤

1. **把公钥加到 GPU 机器**

```bash
# 在金山云服务器上查看公钥
cat /etc/gpu-node-inspector/id_rsa.pub

# 将输出追加到 GPU 机器的 ~/.ssh/authorized_keys
# 在 GPU 机器上执行：
echo "ssh-rsa AAAA..." >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
```

2. **测试免密 SSH**

```bash
sudo -u inspector ssh -i /etc/gpu-node-inspector/id_rsa -o StrictHostKeyChecking=no ubuntu@<GPU机器IP> 'nvidia-smi'
```

能输出 GPU 信息 → 通了。

3. **编辑配置添加节点**

```bash
sudo nano /etc/gpu-node-inspector/config.yaml
```

在 `nodes` 下添加：

```yaml
nodes:
  # 已有节点...
  - name: gpu-node-01
    host: 192.168.1.100       # GPU 机器 IP
    user: ubuntu              # SSH 用户
    ssh_private_key_path_env: "SSH_PRIVATE_KEY_PATH"
    gpu_vendor: nvidia
    ping_targets:
      - "8.8.8.8"
```

4. **重启服务**

```bash
sudo systemctl restart gpu-node-inspector
```

5. **验证**

```bash
# 查看日志，应该有新节点的采集记录
sudo tail -n 30 /var/log/gpu-node-inspector/inspector.log

# API 查看所有节点
curl -H "X-Inspect-Token: <token>" http://127.0.0.1:8080/api/status

# 刷新看板
# http://<服务器IP>:8080/?token=<token>
```

---

## Web 看板

### 访问方式

```
http://<服务器IP>:8080/?token=<你的token>
```

或使用请求头：

```bash
curl -H "X-Inspect-Token: <token>" http://<服务器IP>:8080/
```

### 看板内容

| 区域 | 内容 |
|------|------|
| **节点统计** | 节点总数、在线数 |
| **GPU 信息** | 每张卡的温度、利用率、显存、功耗、风扇（无 GPU 时显示警告） |
| **系统指标** | 磁盘使用率、内存、负载、运行时长、CPU |
| **网络 Ping** | 延迟、丢包率 |
| **活跃告警** | 当前触发的告警列表，带状态标签 |

### API 接口

| 接口 | 说明 | 鉴权 |
|------|------|------|
| `GET /health` | 健康检查 | 不需要 |
| `GET /` | HTML 看板 | Token |
| `GET /api/status` | 所有节点状态 JSON | Token |
| `GET /api/alerts` | 当前活跃告警 JSON | Token |
| `GET /api/history?node=&rule=&limit=` | 历史指标 JSON | Token |

---

## Webhook 集成

### 通用 Webhook

配置 `notifications.type: generic`，服务会向 `WPS_WEBHOOK_URL` 发送 HTTP POST：

```json
{
  "type": "periodic_report",
  "title": "GPU 节点巡检报告",
  "timestamp": "2026-07-29T13:00:00+08:00",
  "node_count": 2,
  "online_count": 2,
  "nodes": [
    {
      "name": "gpu-node-01",
      "reachable": true,
      "summary": "2 GPUs, avg temp 62°C",
      "last_check_at": "2026-07-29T13:00:00+08:00"
    }
  ]
}
```

告警事件：

```json
{
  "type": "alert_triggered",
  "node": "gpu-node-01",
  "rule": "gpu_temp_c",
  "value": 89.0,
  "threshold": 85.0,
  "message": "🚨 Triggered: gpu_temp_c [GPU 0] value=89.0, threshold=85.0",
  "timestamp": "2026-07-29T13:05:00+08:00"
}
```

### 测试 Webhook

用 [webhook.site](https://webhook.site) 获取一个测试 URL，修改 env 后重启服务即可看到推送。

### 扩展 WPS 协作机器人

修改 `inspector/notifier.py` 中的 `WPSNotifier` 类，按 WPS 的 payload 格式实现 `send()` 方法即可。

---

## 迭代更新

### 本地开发 → 推送代码

```bash
# 修改代码后
git add .
git commit -m "你的改动描述"
git push gitee main
```

### 服务器拉取更新

```bash
cd ~/Inspection-Assistant
sudo bash scripts/update.sh
```

`update.sh` 会自动：
1. 从 Gitee 拉取最新代码（国内服务器，速度快）
2. 重新安装依赖（使用阿里云 PyPI 镜源）
3. 重启服务
4. 显示服务状态

---

## 告警防抖机制

为避免温度毛刺等瞬时波动触发误报，告警支持防抖：

```yaml
alert_rules:
  stability_cycles: 2   # 连续 2 个周期超标才触发
```

状态机：

```
normal ──(超标)──> breaching ──(连续N次)──> triggered ──(恢复正常)──> normal
                                              │
                                              └── 推送 alert_triggered
                                                  恢复时推送 alert_recovered
```

- 同一个告警**只触发一次**，不会重复推送
- 恢复正常后才会推送"已恢复"通知
- 防抖计数器在恢复正常时自动清零

---

## 安全设计

| 项目 | 措施 |
|------|------|
| SSH 认证 | 仅使用私钥文件，禁止密码登录 |
| 私钥权限 | 启动时强制校验 `0o600`，否则拒绝启动 |
| 命令执行 | 使用 `shlex.quote` 转义参数，防止 shell 注入 |
| 节点权限 | 只执行只读命令（nvidia-smi、df、free、ping 等） |
| 看板鉴权 | Token 头或 URL 参数鉴权 |
| 敏感文件 | 配置和 env 文件权限 `600` |
| 日志脱敏 | 不在日志中打印私钥、Webhook URL、Token |
| 数据持久化 | SQLite 数据在 `/var/lib/` 下，重装不丢失 |

---

## 故障排查

### 服务启动失败

```bash
# 查看服务状态
sudo systemctl status gpu-node-inspector

# 查看日志
sudo tail -n 50 /var/log/gpu-node-inspector/inspector.log
```

常见问题：

| 错误 | 原因 | 解决 |
|------|------|------|
| `Config file not found` | 配置文件路径错误 | 检查 `/etc/gpu-node-inspector/config.yaml` 是否存在 |
| `SSH private key permissions must be 0o600` | 私钥权限不对 | `sudo chmod 600 /etc/gpu-node-inspector/id_rsa` |
| `Missing environment variable` | env 文件没配 | 编辑 `/etc/gpu-node-inspector/env` |
| `Failed to collect metrics` | SSH 连接失败 | 检查网络、密钥、authorized_keys |

### 节点显示 unreachable

```bash
# 测试 SSH 连通性
sudo -u inspector ssh -i /etc/gpu-node-inspector/id_rsa -o StrictHostKeyChecking=no <user>@<host> 'echo OK'
```

如果失败：
- 检查防火墙/安全组是否放行 22 端口
- 检查公钥是否加到目标节点的 `authorized_keys`
- 检查目标节点 SSH 服务是否运行

### Webhook 推送失败

```bash
# 检查 Webhook URL 是否可达
curl -X POST <webhook_url> -H "Content-Type: application/json" -d '{"test":true}'

# 查看重试队列
curl -H "X-Inspect-Token: <token>" http://127.0.0.1:8080/api/status
```

### 看板打不开

```bash
# 检查服务是否在跑
sudo systemctl status gpu-node-inspector

# 检查监听地址
sudo ss -tlnp | grep 8080

# 如果显示 127.0.0.1:8080，改为 0.0.0.0
sudo sed -i 's/host: "127.0.0.1"/host: "0.0.0.0"/' /etc/gpu-node-inspector/config.yaml
sudo systemctl restart gpu-node-inspector

# 检查安全组是否放行 8080 端口
```

---

## 项目结构

```
inspector/
├── __init__.py
├── config.py          # Pydantic 配置加载与校验
├── store.py           # SQLite 存储层（asyncio.Lock 串行化）
├── metrics.py         # 指标解析（nvidia-smi、df、free、top、ping）
├── collector.py       # SSH 并发采集器（asyncssh）
├── alerter.py         # 阈值判断与告警状态机（防抖）
├── notifier.py        # Webhook 通知抽象层（通用/WPS）
├── scheduler.py       # 异步调度器（APScheduler + 防堆积锁）
├── dashboard.py       # FastAPI Web 看板（Token 鉴权）
└── models.py          # 数据模型（dataclass）

main.py                # 入口（asyncio.run + uvicorn + 信号处理）
templates/index.html   # 看板 HTML 模板

scripts/
├── install.sh         # 生产环境安装脚本（systemd + logrotate）
├── update.sh          # 迭代更新脚本（git pull + 重装 + 重启）
├── run.sh             # 本地开发启动脚本
└── logrotate.conf     # 日志轮转配置

config.example.yaml    # 示例配置
requirements.txt       # Python 依赖
pyproject.toml         # 项目元数据与测试配置

tests/
├── test_config.py     # 配置加载测试
├── test_store.py      # SQLite 存储测试
├── test_metrics.py    # 指标解析测试
├── test_collector.py  # SSH 采集器测试
├── test_alerter.py    # 告警状态机测试
├── test_notifier.py   # Webhook 通知测试
├── test_dashboard.py  # 看板鉴权测试
├── test_scheduler.py  # 调度器锁测试
└── test_integration.py # 端到端集成测试
```

---

## 运行测试

```bash
# 全部测试
pytest -v

# 单个模块
pytest tests/test_alerter.py -v

# 代码检查
ruff check .

# 查看覆盖率
pytest --cov=inspector --cov-report=term-missing
```

---

## 技术栈

| 组件 | 技术 |
|------|------|
| 语言 | Python 3.9+ |
| SSH | asyncssh（异步并发） |
| 调度 | APScheduler（AsyncIOScheduler） |
| Web 框架 | FastAPI + Uvicorn |
| 模板 | Jinja2 |
| 数据库 | SQLite（asyncio.Lock 串行化） |
| 配置 | Pydantic + PyYAML |
| HTTP 客户端 | httpx |
| 部署 | systemd + logrotate |
| PyPI 镜源 | 阿里云（国内服务器自动使用） |
| 代码镜像 | Gitee（国内服务器自动使用） |

---

## 许可证

MIT
