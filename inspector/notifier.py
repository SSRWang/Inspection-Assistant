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

        lines = [
            "# 🔍 GPU 节点巡检报告",
            "",
            f"> **节点总数**: {node_count}　**在线**: {online_count}",
            "",
        ]

        for n in nodes:
            status_icon = "🟢" if n.get("reachable") else "🔴"
            status_text = "在线" if n.get("reachable") else "离线"
            lines.append(f"### {status_icon} {n.get('name', 'unknown')} — {status_text}")
            lines.append(f"> {n.get('summary', '-')}")
            lines.append(f"> 最后检查: {n.get('last_check_at', '-')}")
            lines.append("")

        return {"msgtype": "markdown", "markdown": {"content": "\n".join(lines)}}

    def _format_alert(self, payload: dict) -> dict:
        alert_type = payload.get("type", "")
        node = payload.get("node", "-")
        rule = payload.get("rule", "-")
        value = payload.get("value", "-")
        threshold = payload.get("threshold", "-")
        message = payload.get("message", "-")
        timestamp = payload.get("timestamp", "-")

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
            f"> **节点**: {node}",
            f"> **规则**: {rule}",
            f"> **当前值**: {value}",
            f"> **阈值**: {threshold}",
            f"> **时间**: {timestamp}",
            "",
            f"<font color='{color}'>{message}</font>",
        ]

        return {"msgtype": "markdown", "markdown": {"content": "\n".join(lines)}}

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
