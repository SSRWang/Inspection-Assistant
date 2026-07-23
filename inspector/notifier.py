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
