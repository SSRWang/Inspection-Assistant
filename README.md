# GPU Node Inspector

轻量级 GPU 节点状态巡检服务。

## 功能

- 通过 SSH 采集 GPU 温度、利用率、显存、功耗、风扇转速
- 采集系统负载、磁盘、网络延迟等指标
- 阈值告警与防抖，支持告警恢复通知
- Webhook（通用 / WPS）告警推送与失败重试队列
- 5 分钟周期性节点巡检报告
- 内置 Web 看板（Token 鉴权）
- SQLite 指标持久化与 TTL 清理
- systemd 服务与 logrotate 日志轮转

## 环境要求

- Python >= 3.9
- 目标节点可通过 SSH 访问，并安装 `nvidia-smi`
- 运行服务器需配置 SSH 私钥（权限 0o600）

## 快速开始

```bash
# 1. 创建虚拟环境并安装依赖
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 2. 复制示例配置并编辑
bash scripts/run.sh
# 或者手动复制后编辑：
cp config.example.yaml config.yaml
# 编辑 config.yaml，填入节点信息、告警阈值、Webhook 地址等

# 3. 启动服务
python -m main
```

## 配置文件

参考 `config.example.yaml`。关键字段说明：

- `nodes`: 巡检节点列表，包含 `name`、`host`、`user`、`gpu_vendor`、`ping_targets`
- `storage.path`: SQLite 数据库路径
- `storage.retain_days`: 指标保留天数
- `alert_rules`: GPU 温度 `gpu_temp_c`、显存占用 `gpu_memory_pct`、磁盘 `disk_usage_pct`、节点不可达告警阈值
- `notifications`: Webhook 类型、环境变量名、重试次数与间隔
- `dashboard`: 内置看板开关、监听地址、鉴权 Token 环境变量

## 运行与日志

```bash
python -m main
```

日志默认输出到 `logs/inspector.log`，由 `scripts/run.sh` 或 systemd 服务自动创建目录。

## 部署到金山云服务器

```bash
# 安装并注册 systemd 服务
sudo bash scripts/install.sh

# 启动并查看状态
sudo systemctl start gpu-node-inspector
sudo systemctl status gpu-node-inspector

# 设置为开机自启
sudo systemctl enable gpu-node-inspector
```

## 查看日志

```bash
sudo tail -f /var/log/gpu-node-inspector/inspector.log
```

## 测试

```bash
pytest -v
```

## 许可证

MIT
