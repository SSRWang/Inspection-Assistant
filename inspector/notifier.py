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
    """WPS 协作机器人 Webhook 通知器。

    Webhook URL 格式: https://woa.wps.cn/api/v1/webhook/send?key=xxx
    支持 text 和 markdown 两种消息类型。
    """

    def __init__(self, cfg: NotificationsConfig, url: str, timeout: float = 10.0):
        self.cfg = cfg
        self.url = url
        self.timeout = timeout

    async def send(self, payload: dict) -> bool:
        headers = {"Content-Type": "application/json"}
        body = self._format_wps(payload)
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.post(self.url, headers=headers, json=body)
                return resp.status_code < 400
        except Exception:
            return False

    def _format_wps(self, payload: dict) -> dict:
        payload_type = payload.get("type", "")

        if payload_type == "periodic_report":
            return self._format_periodic_report(payload)
        elif payload_type in ("alert_triggered", "alert_recovered"):
            return self._format_alert(payload)
        else:
            return self._format_text(json.dumps(payload, ensure_ascii=False, default=str))

    def _format_periodic_report(self, payload: dict) -> dict:
        nodes = payload.get("nodes", [])
        node_count = payload.get("node_count", len(nodes))
        online_count = payload.get("online_count", 0)
        message = payload.get("message", "")  # 附加消息（如节点未找到）

        # 判断是单节点查询还是多节点查询
        is_single_node = node_count == 1

        if is_single_node:
            # 单节点查询：只显示该节点详细信息
            n = nodes[0]
            lines = self._format_single_node(n)
        else:
            # 多节点查询：显示所有节点
            lines = [
                "# 🔍 GPU 节点巡检报告",
                "",
                f"**节点总数**: {node_count}　**在线**: {online_count}",
                "",
            ]

            for n in nodes:
                node_lines = self._format_single_node(n)
                lines.extend(node_lines)
                lines.append("")

        # 如果有附加消息（如节点未找到），显示在最后
        if message:
            lines.append("")
            lines.append(f"⚠️ {message}")

        return {"msgtype": "markdown", "markdown": {"text": "\n".join(lines)}}

    def _format_single_node(self, node: dict) -> list[str]:
        """格式化单个节点的信息"""
        lines = []
        status_icon = "🟢" if node.get("reachable") else "🔴"
        status_text = "在线" if node.get("reachable") else "离线"
        node_name = node.get("name", "unknown")

        lines.append(f"# {status_icon} {node_name} — {status_text}")

        # 解析 raw_metrics
        raw = node.get("raw_metrics", "{}")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (ValueError, TypeError):
                raw = {}

        # 如果节点不可达，显示原因
        if not node.get("reachable"):
            error = node.get("error", "")
            if error:
                lines.append(f"> **原因**: {error}")
            else:
                lines.append("> **原因**: SSH 连接失败或命令执行超时")
            lines.append("")
            return lines

        if not raw:
            lines.append("> **状态**: 数据采集失败，可能是首次采集或命令执行异常")
            lines.append("")
            return lines

        # 主机信息
        hostname = raw.get("hostname", "-")
        ip_addr = raw.get("ip_addr", "-").split()[0] if raw.get("ip_addr") else "-"
        os_release = raw.get("os_release", "")
        os_name = ""
        for line in os_release.split("\n"):
            if line.startswith("PRETTY_NAME="):
                os_name = line.split("=", 1)[1].strip('"')
                break
        uname = raw.get("uname", "").split()
        kernel = uname[2] if len(uname) > 2 else "-"

        lines.append(f"> **主机**: {hostname} ({ip_addr})")
        lines.append(f"> **系统**: {os_name or '-'} | **内核**: {kernel}")
        lines.append("")

        # GPU 信息
        gpu_out = raw.get("gpu", "").strip()
        gpu_stderr = raw.get("gpu_stderr", "").strip()
        if gpu_out:
            lines.append("**🎮 GPU**")
            for line in gpu_out.split("\n"):
                parts = [p.strip() for p in line.split(",")]
                if len(parts) >= 8:
                    lines.append(f"- GPU {parts[0]}: {parts[1]}")
                    lines.append(f"  温度: {parts[2]}°C | 利用率: {parts[3]}% | 显存: {parts[5]}/{parts[6]} MB")
                    lines.append(f"  功耗: {parts[7]}W | 风扇: {parts[8]}%")
        elif gpu_stderr:
            lines.append("**🎮 GPU**")
            if "not found" in gpu_stderr.lower() or "command not found" in gpu_stderr.lower():
                lines.append("- ⚠️ 未安装 NVIDIA 驱动，无法获取 GPU 信息")
            else:
                lines.append(f"- {gpu_stderr}")

        # 系统指标
        lines.append("")
        lines.append("**💻 系统指标**")

        # 磁盘
        df_out = raw.get("df", "").strip()
        if df_out:
            for line in df_out.split("\n"):
                parts = line.split()
                if len(parts) >= 5 and "%" in parts[4]:
                    lines.append(f"- 磁盘: {parts[4]} 已用 | 总量: {parts[1]} | 可用: {parts[3]}")

        # 内存
        mem_out = raw.get("memory", "").strip()
        if mem_out:
            for line in mem_out.split("\n"):
                if line.startswith("Mem:"):
                    parts = line.split()
                    if len(parts) >= 7:
                        lines.append(f"- 内存: {parts[2]} MB 已用 / {parts[1]} MB 总量 | 可用: {parts[6]} MB")

        # 负载
        load_out = raw.get("load", "").strip()
        if load_out:
            parts = load_out.split()
            if len(parts) >= 3:
                lines.append(f"- 负载: 1分钟 {parts[0]} | 5分钟 {parts[1]} | 15分钟 {parts[2]}")

        # CPU
        cpu_out = raw.get("cpu", "").strip()
        if cpu_out:
            cpu_info = self._parse_cpu_info(cpu_out)
            if cpu_info:
                lines.append(f"- CPU: {cpu_info}")

        # 运行时长
        uptime_out = raw.get("uptime", "").strip()
        if uptime_out:
            try:
                uptime_secs = float(uptime_out.split()[0])
                days = int(uptime_secs // 86400)
                hours = int((uptime_secs % 86400) // 3600)
                lines.append(f"- 运行时长: {days}天 {hours}小时")
            except (ValueError, IndexError):
                pass

        # 网络 Ping
        ping_out = raw.get("ping", "").strip()
        if ping_out:
            lines.append("")
            lines.append("**🌐 网络 Ping**")
            for line in ping_out.split("\n"):
                if "packet loss" in line:
                    lines.append(f"- {line.strip()}")
                elif "rtt" in line or "min/avg/max" in line:
                    lines.append(f"- {line.strip()}")

        lines.append("")
        return lines

    def _format_alert(self, payload: dict) -> dict:
        alert_type = payload.get("type", "")
        node = payload.get("node", "-")
        rule = payload.get("rule", "-")
        value = payload.get("value", "-")
        threshold = payload.get("threshold", "-")
        message = payload.get("message", "-")
        timestamp = payload.get("timestamp", "-")
        node_status = payload.get("node_status")

        if alert_type == "alert_triggered":
            icon = "🚨"
            color = "warning"
            title = "告警触发"
        else:
            icon = "✅"
            color = "info"
            title = "告警恢复"

        lines = [
            f"# {icon} {title}",
            "",
            f"**节点**: {node}",
            f"**规则**: {rule}",
            f"**当前值**: {value}",
            f"**阈值**: {threshold}",
            f"**时间**: {timestamp}",
            "",
            f"<font color='{color}'>{message}</font>",
        ]

        # 如果有节点完整状态，展示所有监控数据
        if node_status and node_status.get("reachable"):
            raw = node_status.get("raw_metrics", "{}")
            if isinstance(raw, str):
                import json as _json
                try:
                    raw = _json.loads(raw)
                except (ValueError, TypeError):
                    raw = {}

            if raw:
                lines.append("")
                lines.append("---")
                lines.append("## 📊 完整监控数据")
                lines.append("")

                # GPU 信息
                gpu_out = raw.get("gpu", "").strip()
                if gpu_out:
                    lines.append("### 🎮 GPU")
                    for line in gpu_out.split("\n"):
                        parts = [p.strip() for p in line.split(",")]
                        if len(parts) >= 8:
                            lines.append(f"- GPU {parts[0]}: {parts[1]}")
                            lines.append(f"  温度: {parts[2]}°C | 利用率: {parts[3]}% | 显存: {parts[5]}/{parts[6]} MB")
                            lines.append(f"  功耗: {parts[7]}W | 风扇: {parts[8]}%")
                elif raw.get("gpu_stderr", "").strip():
                    lines.append("### 🎮 GPU")
                    lines.append(f"- {raw['gpu_stderr'].strip()}")

                # 系统指标
                lines.append("")
                lines.append("### 💻 系统指标")

                # 磁盘
                df_out = raw.get("df", "").strip()
                if df_out:
                    for line in df_out.split("\n"):
                        parts = line.split()
                        if len(parts) >= 5 and "%" in parts[4]:
                            lines.append(f"- **磁盘**: {parts[4]} 已用 | 总量: {parts[1]} | 可用: {parts[3]}")

                # 内存
                mem_out = raw.get("memory", "").strip()
                if mem_out:
                    for line in mem_out.split("\n"):
                        if line.startswith("Mem:"):
                            parts = line.split()
                            if len(parts) >= 4:
                                lines.append(f"- **内存**: {parts[2]} MB 已用 / {parts[1]} MB 总量 | 可用: {parts[6]} MB")

                # 负载
                load_out = raw.get("load", "").strip()
                if load_out:
                    parts = load_out.split()
                    if len(parts) >= 3:
                        lines.append(f"- **负载**: 1分钟 {parts[0]} | 5分钟 {parts[1]} | 15分钟 {parts[2]}")

                # CPU - 改成大白话
                cpu_out = raw.get("cpu", "").strip()
                if cpu_out:
                    cpu_info = self._parse_cpu_info(cpu_out)
                    if cpu_info:
                        lines.append(f"- **CPU**: {cpu_info}")

                # 运行时长
                uptime_out = raw.get("uptime", "").strip()
                if uptime_out:
                    try:
                        uptime_secs = float(uptime_out.split()[0])
                        days = int(uptime_secs // 86400)
                        hours = int((uptime_secs % 86400) // 3600)
                        lines.append(f"- **运行时长**: {days}天 {hours}小时")
                    except (ValueError, IndexError):
                        pass

                # 网络 Ping
                ping_out = raw.get("ping", "").strip()
                if ping_out:
                    lines.append("")
                    lines.append("### 🌐 网络 Ping")
                    for line in ping_out.split("\n"):
                        if "packet loss" in line:
                            lines.append(f"- {line.strip()}")
                        elif "rtt" in line or "min/avg/max" in line:
                            lines.append(f"- {line.strip()}")

        return {"msgtype": "markdown", "markdown": {"text": "\n".join(lines)}}

    def _parse_cpu_info(self, cpu_output: str) -> str:
        """解析 CPU 信息，返回人类可读的格式"""
        for line in cpu_output.split("\n"):
            if "Cpu(s):" in line or "%Cpu" in line:
                # 提取各指标
                import re
                us = re.search(r"([\d.]+)\s*us", line)
                sy = re.search(r"([\d.]+)\s*sy", line)
                id_val = re.search(r"([\d.]+)\s*id", line)

                if us and sy and id_val:
                    user_pct = float(us.group(1))
                    sys_pct = float(sy.group(1))
                    idle_pct = float(id_val.group(1))
                    used_pct = user_pct + sys_pct

                    return f"使用率 {used_pct:.1f}%（用户 {user_pct:.1f}% + 系统 {sys_pct:.1f}%）| 空闲 {idle_pct:.1f}%"
        return ""

    def _format_text(self, content: str) -> dict:
        return {
            "msgtype": "text",
            "text": {"content": content},
        }


def create_notifier(cfg: NotificationsConfig) -> BaseNotifier:
    url = cfg.resolve_webhook_url()
    if cfg.type == "wps":
        return WPSNotifier(cfg, url)
    return GenericWebhookNotifier(cfg, url)
