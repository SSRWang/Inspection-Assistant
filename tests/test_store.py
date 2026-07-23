from datetime import datetime, timezone
import pytest
from inspector.models import GpuMetric, NodeMetrics, SystemMetric
from inspector.store import SqliteStore


@pytest.fixture
async def store(tmp_path):
    db_path = tmp_path / "test.db"
    s = SqliteStore(str(db_path))
    await s.setup()
    yield s
    await s.close()


async def test_write_and_read_node_status(store):
    metrics = NodeMetrics(
        node="n1",
        timestamp=datetime.now(timezone.utc),
        reachable=True,
        gpus=[GpuMetric(index=0, name="T4", temperature_c=60.0, utilization_gpu_pct=10.0,
                        utilization_memory_pct=20.0, memory_used_mb=1000.0,
                        memory_total_mb=16000.0, power_draw_w=35.0, fan_speed_pct=30.0)],
        system=SystemMetric(cpu_usage_pct=15.0, memory_used_mb=4000.0,
                            memory_total_mb=16000.0, disk_used_pct=50.0,
                            load_average_1m=0.5, uptime_seconds=3600.0),
        networks=[],
    )
    await store.write_node_status(metrics)
    status = await store.get_node_status("n1")
    assert status is not None
    assert status["reachable"] == 1
    assert status["node"] == "n1"
