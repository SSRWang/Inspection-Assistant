from __future__ import annotations
import asyncio
import asyncssh
from datetime import datetime, timezone
from pathlib import Path
from inspector.config import NodeConfig, Settings
from inspector.metrics import parse_nvidia_smi, parse_ping, parse_system
from inspector.models import NetworkMetric, NodeMetrics, SystemMetric
from inspector.store import SqliteStore


class Collector:
    def __init__(self, cfg: Settings, store: SqliteStore):
        self.cfg = cfg
        self.store = store

    def _build_commands(self, node: NodeConfig) -> dict[str, list[str]]:
        ping_target = node.ping_targets[0] if node.ping_targets else "8.8.8.8"
        return {
            "gpu": ["nvidia-smi", "--query-gpu=index,name,temperature.gpu,utilization.gpu,utilization.memory,memory.used,memory.total,power.draw,fan.speed", "--format=csv,noheader"],
            "df": ["df", "-h", "/"],
            "memory": ["free", "-m"],
            "load": ["cat", "/proc/loadavg"],
            "uptime": ["cat", "/proc/uptime"],
            "cpu": ["top", "-bn1"],
            "ping": ["ping", "-c", "3", ping_target],
        }

    async def collect_node(self, node: NodeConfig) -> NodeMetrics:
        timestamp = datetime.now(timezone.utc)
        try:
            key_path = self.cfg.ssh.resolve_private_key_path(node.ssh_private_key_path_env)
        except Exception as e:
            return NodeMetrics(node=node.name, timestamp=timestamp, reachable=False, error=str(e))

        try:
            async with asyncssh.connect(
                host=node.host,
                username=node.user,
                client_keys=[str(key_path)],
                known_hosts=None,
                connect_timeout=self.cfg.ssh.connect_timeout,
            ) as conn:
                cmds = self._build_commands(node)
                gpu_out = await self._run(conn, cmds["gpu"])
                df_out = await self._run(conn, cmds["df"])
                mem_out = await self._run(conn, cmds["memory"])
                load_out = await self._run(conn, cmds["load"])
                uptime_out = await self._run(conn, cmds["uptime"])
                cpu_out = await self._run(conn, cmds["cpu"])
                ping_out = await self._run(conn, cmds["ping"])

                gpus = parse_nvidia_smi(gpu_out)
                system = parse_system({
                    "cpu_output": cpu_out,
                    "memory_output": mem_out,
                    "disk_output": df_out,
                    "load_output": load_out,
                    "uptime_output": uptime_out,
                })
                network = parse_ping(cmds["ping"][-1], ping_out)

                metrics = NodeMetrics(
                    node=node.name,
                    timestamp=timestamp,
                    reachable=True,
                    gpus=gpus,
                    system=system,
                    networks=[network],
                    raw={
                        "gpu": gpu_out, "df": df_out, "memory": mem_out,
                        "load": load_out, "uptime": uptime_out, "cpu": cpu_out,
                        "ping": ping_out,
                    }
                )
                return metrics
        except Exception as e:
            return NodeMetrics(node=node.name, timestamp=timestamp, reachable=False, error=str(e))

    async def _run(self, conn: asyncssh.SSHClientConnection, args: list[str]) -> str:
        result = await conn.run(args[0], args=args[1:], timeout=self.cfg.ssh.command_timeout)
        return result.stdout

    async def collect_all(self) -> list[NodeMetrics]:
        return await asyncio.gather(*[self.collect_node(n) for n in self.cfg.nodes])
