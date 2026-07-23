from datetime import datetime, timezone
from unittest.mock import AsyncMock
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
    store.update_alert_state.assert_any_await("n1", "gpu_temp_c", "breaching", 1, 85.0)


async def test_temperature_alert_sustained_breach_emits_single_event():
    store = AsyncMock()
    rules = AlertRules(gpu_temp_c=80, disk_usage_pct=None, stability_cycles=2)
    alerter = Alerter(rules, store)

    gpu = GpuMetric(index=0, name="T4", temperature_c=85.0, utilization_gpu_pct=10,
                    utilization_memory_pct=20, memory_used_mb=1000,
                    memory_total_mb=16000, power_draw_w=30, fan_speed_pct=30)
    metrics = NodeMetrics(node="n1", timestamp=datetime.now(timezone.utc),
                          reachable=True, gpus=[gpu], system=SystemMetric(disk_used_pct=50))

    # First cycle: breaching, no event
    store.get_alert_state = AsyncMock(return_value=None)
    events = await alerter.evaluate(metrics)
    assert events == []
    store.update_alert_state.assert_any_await("n1", "gpu_temp_c", "breaching", 1, 85.0)

    # Second cycle: trigger
    store.reset_mock()
    store.get_alert_state = AsyncMock(return_value={"state": "breaching", "breach_cycles": 1, "last_value": 85.0})
    events = await alerter.evaluate(metrics)
    assert len(events) == 1
    assert events[0].type == "alert_triggered"
    store.update_alert_state.assert_any_await("n1", "gpu_temp_c", "triggered", 2, 85.0)

    # Third and fourth cycles: already triggered, no new trigger event
    for cycle in (3, 4):
        store.reset_mock()
        store.get_alert_state = AsyncMock(return_value={"state": "triggered", "breach_cycles": cycle - 1, "last_value": 85.0})
        events = await alerter.evaluate(metrics)
        triggered_events = [e for e in events if e.type == "alert_triggered"]
        assert triggered_events == []
        store.update_alert_state.assert_any_await("n1", "gpu_temp_c", "triggered", cycle, 85.0)


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
    store.update_alert_state.assert_any_await("n1", "gpu_temp_c", "triggered", 2, 85.0)
