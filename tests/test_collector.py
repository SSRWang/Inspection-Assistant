import asyncio
from datetime import datetime, timezone
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from inspector.collector import Collector
from inspector.config import NodeConfig
from inspector.models import NodeMetrics


async def test_build_commands():
    store = MagicMock()
    collector = Collector(MagicMock(), store)
    cfg = NodeConfig(name="n1", host="1.2.3.4", user="u")
    commands = collector._build_commands(cfg)
    assert commands["gpu"][0] == "nvidia-smi"
    assert commands["ping"][-1] == "8.8.8.8"
