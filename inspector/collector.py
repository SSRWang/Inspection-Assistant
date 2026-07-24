from __future__ import annotations
import asyncio
import logging
from datetime import datetime, timezone
import asyncssh
from inspector.config import NodeConfig, Settings
from inspector.metrics import parse_nvidia_smi, parse_ping, parse_system
from inspector.models import NodeMetrics
from inspector.store import SqliteStore


logger = logging.getLogger(__name__)


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
                gpu_res = await self._run(conn, cmds["gpu"])
                df_res = await self._run(conn, cmds["df"])
                mem_res = await self._run(conn, cmds["memory"])
                load_res = await self._run(conn, cmds["load"])
                uptime_res = await self._run(conn, cmds["uptime"])
                cpu_res = await self._run(conn, cmds["cpu"])
                ping_res = await self._run(conn, cmds["ping"])

                gpu_out = gpu_res.stdout
                df_out = df_res.stdout
                mem_out = mem_res.stdout
                load_out = load_res.stdout
                uptime_out = uptime_res.stdout
                cpu_out = cpu_res.stdout
                ping_out = ping_res.stdout

                error_messages = []
                for label, res in (("gpu", gpu_res), ("df", df_res), ("memory", mem_res),
                                   ("load", load_res), ("uptime", uptime_res), ("cpu", cpu_res)):
                    if res.exit_status != 0:
                        error_messages.append(f"{label} command failed (exit={res.exit_status}): {res.stderr.strip()}")

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
                    error="; ".join(error_messages) if error_messages else None,
                    raw={
                        "gpu": gpu_out, "gpu_stderr": gpu_res.stderr or "",
                        "df": df_out, "df_stderr": df_res.stderr or "",
                        "memory": mem_out, "memory_stderr": mem_res.stderr or "",
                        "load": load_out, "load_stderr": load_res.stderr or "",
                        "uptime": uptime_out, "uptime_stderr": uptime_res.stderr or "",
                        "cpu": cpu_out, "cpu_stderr": cpu_res.stderr or "",
                        "ping": ping_out, "ping_stderr": ping_res.stderr or "",
                    }
                )
                return metrics
        except Exception as e:
            logger.exception("Failed to collect metrics from node %s: %s", node.name, e)
            return NodeMetrics(node=node.name, timestamp=timestamp, reachable=False, error=str(e))

    async def _run(self, conn: asyncssh.SSHClientConnection, args: list[str]) -> asyncssh.SSHCompletedProcess:
        result = await conn.run(args[0], args=args[1:], timeout=self.cfg.ssh.command_timeout)
        if result.exit_status != 0:
            logger.warning("SSH command failed: args=%s exit_status=%s stderr=%s",
                           args, result.exit_status, result.stderr)
        return result

    async def collect_all(self) -> list[NodeMetrics]:
        return await asyncio.gather(*[self.collect_node(n) for n in self.cfg.nodes])
