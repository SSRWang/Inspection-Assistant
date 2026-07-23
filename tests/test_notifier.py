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
