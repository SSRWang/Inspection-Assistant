from unittest.mock import AsyncMock
import pytest
from inspector.config import NodeConfig, Settings
from inspector.scheduler import SettingsHolder, run_inspection_cycle
import inspector.scheduler as sched


@pytest.fixture(autouse=True)
def reset_collection_lock():
    """Ensure the module-level lock is clean before and after each test."""
    sched._is_collecting = False
    yield
    sched._is_collecting = False


async def test_run_cycle_skips_when_locked():
    collector = AsyncMock()
    alerter = AsyncMock()
    notifier = AsyncMock()
    store = AsyncMock()
    cfg_holder = AsyncMock()

    sched._is_collecting = True
    await run_inspection_cycle(collector, alerter, notifier, store, cfg_holder)
    collector.collect_all.assert_not_awaited()


async def test_run_cycle_runs_when_unlocked():
    collector = AsyncMock()
    collector.collect_all.return_value = []
    alerter = AsyncMock()
    alerter.evaluate.return_value = []
    notifier = AsyncMock()
    notifier.send.return_value = True
    store = AsyncMock()
    store.dequeue_pending_webhooks.return_value = []
    store.list_node_status.return_value = []

    cfg = Settings(nodes=[NodeConfig(name="n1", host="h", user="u")])
    cfg_holder = SettingsHolder(cfg)

    await run_inspection_cycle(collector, alerter, notifier, store, cfg_holder)

    collector.collect_all.assert_awaited_once()
    store.cleanup_metrics.assert_awaited_once()
    assert sched._is_collecting is False
