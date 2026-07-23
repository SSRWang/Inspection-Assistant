import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch
from inspector.alerter import Alerter
from inspector.collector import Collector
from inspector.config import AlertRules, NodeConfig, NotificationsConfig, Settings, StorageConfig
from inspector.models import GpuMetric, NetworkMetric, NodeMetrics, SystemMetric
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
