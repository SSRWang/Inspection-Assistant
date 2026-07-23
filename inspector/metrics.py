from __future__ import annotations
import re
from inspector.models import GpuMetric, NetworkMetric, NodeMetrics, SystemMetric


_FLOAT_RE = re.compile(r"^-?\d+(?:\.\d+)?$")


def _to_float(value: str) -> float | None:
    value = value.strip()
    if value in ("", "N/A", "Unknown", "[Not Supported]"):
        return None
    # Remove units like ' W', ' %', ' MiB' then keep only a leading numeric token.
    raw_token = value.split()[0] if value.split() else value
    numeric = re.sub(r"[^\d.\-]", "", raw_token)
    if not _FLOAT_RE.match(numeric):
        return None
    try:
        return float(numeric)
    except ValueError:
        return None


def parse_nvidia_smi(output: str) -> list[GpuMetric]:
    gpus = []
    for line in output.strip().splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 9:
            continue
        gpus.append(GpuMetric(
            index=int(_to_float(parts[0]) or 0),
            name=parts[1],
            temperature_c=_to_float(parts[2]),
            utilization_gpu_pct=_to_float(parts[3]),
            utilization_memory_pct=_to_float(parts[4]),
            memory_used_mb=_to_float(parts[5]),
            memory_total_mb=_to_float(parts[6]),
            power_draw_w=_to_float(parts[7]),
            fan_speed_pct=_to_float(parts[8]),
        ))
    return gpus


def parse_system(outputs: dict[str, str]) -> SystemMetric | None:
    try:
        cpu = _parse_cpu(outputs.get("cpu_output", ""))
        mem_used, mem_total = _parse_free(outputs.get("memory_output", ""))
        disk = _parse_df(outputs.get("disk_output", ""))
        load_line = outputs.get("load_output", "")
        load = _to_float(load_line.split()[0]) if load_line.split() else None
        uptime = _to_float(outputs.get("uptime_output", ""))
        return SystemMetric(
            cpu_usage_pct=cpu,
            memory_used_mb=mem_used,
            memory_total_mb=mem_total,
            disk_used_pct=disk,
            load_average_1m=load,
            uptime_seconds=uptime,
        )
    except Exception:
        return None


def _parse_free(output: str) -> tuple[float | None, float | None]:
    for line in output.splitlines():
        if line.startswith("Mem:"):
            parts = line.split()
            if len(parts) >= 3:
                # free -m columns: Mem: total used free ...
                return _to_float(parts[2]), _to_float(parts[1])
            break
    return None, None


def _parse_df(output: str) -> float | None:
    lines = output.strip().splitlines()
    if len(lines) >= 2:
        for token in lines[1].split():
            m = re.search(r"(\d+)%", token)
            if m:
                return float(m.group(1))
    return None


def _parse_cpu(output: str) -> float | None:
    for line in output.splitlines():
        if "Cpu(s):" in line:
            # e.g. "%Cpu(s): 10.5 us,  5.2 sy, ..."
            m = re.search(r"([\d.]+)\s*us", line)
            if m:
                return float(m.group(1))
    return None


def parse_ping(target: str, output: str) -> NetworkMetric:
    packet_loss = None
    avg_latency = None
    for line in output.splitlines():
        m = re.search(r"(\d+(?:\.\d+)?)% packet loss", line)
        if m:
            packet_loss = float(m.group(1))
        m = re.search(r"rtt.*=\s*[\d.]+/([\d.]+)/[\d.]+/[\d.]+\s*ms", line)
        if m:
            avg_latency = float(m.group(1))
    return NetworkMetric(target=target, packet_loss_pct=packet_loss, avg_latency_ms=avg_latency)


def flatten_metrics(metrics: NodeMetrics) -> list[dict]:
    records = []
    ts = metrics.timestamp.isoformat()
    for gpu in metrics.gpus:
        base_labels = {"index": gpu.index, "name": gpu.name}
        for name, value, unit, labels in (
            ("temperature_c", gpu.temperature_c, "C", base_labels),
            ("utilization_gpu_pct", gpu.utilization_gpu_pct, "%", {"index": gpu.index}),
            ("utilization_memory_pct", gpu.utilization_memory_pct, "%", {"index": gpu.index}),
            ("memory_used_mb", gpu.memory_used_mb, "MiB", {"index": gpu.index}),
            ("memory_total_mb", gpu.memory_total_mb, "MiB", {"index": gpu.index}),
            ("power_draw_w", gpu.power_draw_w, "W", {"index": gpu.index}),
            ("fan_speed_pct", gpu.fan_speed_pct, "%", {"index": gpu.index}),
        ):
            records.append({
                "node": metrics.node,
                "category": "gpu",
                "name": name,
                "value": value,
                "unit": unit,
                "labels": labels,
                "timestamp": ts,
            })
    if metrics.system:
        sys = metrics.system
        for name, value, unit in (
            ("cpu_usage_pct", sys.cpu_usage_pct, "%"),
            ("memory_used_mb", sys.memory_used_mb, "MiB"),
            ("memory_total_mb", sys.memory_total_mb, "MiB"),
            ("disk_used_pct", sys.disk_used_pct, "%"),
            ("load_average_1m", sys.load_average_1m, ""),
            ("uptime_seconds", sys.uptime_seconds, "s"),
        ):
            records.append({
                "node": metrics.node,
                "category": "system",
                "name": name,
                "value": value,
                "unit": unit,
                "labels": {},
                "timestamp": ts,
            })
    for net in metrics.networks:
        for name, value, unit in (
            ("packet_loss_pct", net.packet_loss_pct, "%"),
            ("avg_latency_ms", net.avg_latency_ms, "ms"),
        ):
            records.append({
                "node": metrics.node,
                "category": "network",
                "name": name,
                "value": value,
                "unit": unit,
                "labels": {"target": net.target},
                "timestamp": ts,
            })
    return records
