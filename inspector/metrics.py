from __future__ import annotations
import re
from inspector.models import GpuMetric, NetworkMetric, NodeMetrics, SystemMetric


def _to_float(value: str) -> float | None:
    value = value.strip()
    if value in ("", "N/A", "Unknown", "[Not Supported]"):
        return None
    # Remove units like ' W', ' %', ' MiB'
    numeric = re.sub(r"[^\d.\-]", "", value.split()[0] if value.split() else value)
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
        parts = lines[1].split()
        if len(parts) >= 5:
            return _to_float(parts[4].replace("%", ""))
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
        records.append({
            "node": metrics.node, "category": "gpu", "name": "temperature_c",
            "value": gpu.temperature_c, "unit": "C", "labels": {"index": gpu.index, "name": gpu.name}, "timestamp": ts
        })
        records.append({
            "node": metrics.node, "category": "gpu", "name": "utilization_gpu_pct",
            "value": gpu.utilization_gpu_pct, "unit": "%", "labels": {"index": gpu.index}, "timestamp": ts
        })
    if metrics.system:
        records.append({
            "node": metrics.node, "category": "system", "name": "disk_used_pct",
            "value": metrics.system.disk_used_pct, "unit": "%", "labels": {}, "timestamp": ts
        })
    return records
