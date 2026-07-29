from inspector.notifier import GenericWebhookNotifier, WPSNotifier
from inspector.config import NotificationsConfig


async def test_generic_notifier_success(httpx_mock):
    httpx_mock.add_response(status_code=200)
    cfg = NotificationsConfig(type="generic", webhook_url_env="W", max_retries=3)
    n = GenericWebhookNotifier(cfg, "http://example.com/hook")
    ok = await n.send({"type": "test"})
    assert ok is True


async def test_wps_periodic_report(httpx_mock):
    httpx_mock.add_response(status_code=200)
    cfg = NotificationsConfig(type="wps", webhook_url_env="W", max_retries=3)
    n = WPSNotifier(cfg, "http://example.com/hook")
    payload = {
        "type": "periodic_report",
        "title": "巡检报告",
        "timestamp": "2026-07-29T13:00:00+08:00",
        "node_count": 2,
        "online_count": 1,
        "nodes": [
            {"name": "node-01", "reachable": True, "summary": "2 GPUs, avg temp 62°C", "last_check_at": "2026-07-29T13:00:00"},
            {"name": "node-02", "reachable": False, "summary": "Node unreachable", "last_check_at": "2026-07-29T13:00:00"},
        ],
    }
    ok = await n.send(payload)
    assert ok is True
    # Verify the request body was WPS markdown format
    request = httpx_mock.get_requests()[0]
    import json
    body = json.loads(request.content)
    assert body["msgtype"] == "markdown"
    assert "GPU 节点巡检报告" in body["markdown"]["text"]
    assert "🟢" in body["markdown"]["text"]
    assert "🔴" in body["markdown"]["text"]


async def test_wps_alert_triggered(httpx_mock):
    httpx_mock.add_response(status_code=200)
    cfg = NotificationsConfig(type="wps", webhook_url_env="W", max_retries=3)
    n = WPSNotifier(cfg, "http://example.com/hook")
    payload = {
        "type": "alert_triggered",
        "node": "node-01",
        "rule": "gpu_temp_c",
        "value": 89.0,
        "threshold": 85.0,
        "message": "告警触发: gpu_temp_c 89.0 > 85.0",
        "timestamp": "2026-07-29T13:05:00+08:00",
    }
    ok = await n.send(payload)
    assert ok is True
    request = httpx_mock.get_requests()[0]
    import json
    body = json.loads(request.content)
    assert body["msgtype"] == "markdown"
    assert "告警触发" in body["markdown"]["text"]
    assert "89" in body["markdown"]["text"]


async def test_wps_alert_recovered(httpx_mock):
    httpx_mock.add_response(status_code=200)
    cfg = NotificationsConfig(type="wps", webhook_url_env="W", max_retries=3)
    n = WPSNotifier(cfg, "http://example.com/hook")
    payload = {
        "type": "alert_recovered",
        "node": "node-01",
        "rule": "gpu_temp_c",
        "value": 72.0,
        "threshold": 85.0,
        "message": "告警恢复: gpu_temp_c 72.0 <= 85.0",
        "timestamp": "2026-07-29T13:10:00+08:00",
    }
    ok = await n.send(payload)
    assert ok is True
    request = httpx_mock.get_requests()[0]
    import json
    body = json.loads(request.content)
    assert body["msgtype"] == "markdown"
    assert "告警恢复" in body["markdown"]["text"]
