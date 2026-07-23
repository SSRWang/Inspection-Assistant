from __future__ import annotations
from inspector.config import AlertRules
from inspector.models import AlertEvent, NodeMetrics
from inspector.store import SqliteStore


class Alerter:
    def __init__(self, rules: AlertRules, store: SqliteStore):
        self.rules = rules
        self.store = store

    async def evaluate(self, metrics: NodeMetrics) -> list[AlertEvent]:
        events = []
        # Always evaluate node_unreachable so recovery is detected
        events.extend(await self._check_rule(metrics, "node_unreachable", not metrics.reachable))
        if not metrics.reachable:
            return events

        for gpu in metrics.gpus:
            if gpu.temperature_c is not None:
                events.extend(await self._check_rule(metrics, "gpu_temp_c", gpu.temperature_c, gpu.index))
            if gpu.memory_total_mb and gpu.memory_used_mb is not None:
                mem_pct = gpu.memory_used_mb / gpu.memory_total_mb * 100
                events.extend(await self._check_rule(metrics, "gpu_memory_pct", mem_pct, gpu.index))

        if metrics.system:
            if metrics.system.disk_used_pct is not None:
                events.extend(await self._check_rule(metrics, "disk_usage_pct", metrics.system.disk_used_pct))

        return events

    async def _check_rule(self, metrics: NodeMetrics, rule: str, value: float | bool,
                          gpu_index: int | None = None) -> list[AlertEvent]:
        threshold = getattr(self.rules, rule, None)
        if threshold is None:
            return []
        if rule == "node_unreachable" and not threshold:
            return []

        node = metrics.node
        state_row = await self.store.get_alert_state(node, rule)
        state = state_row["state"] if state_row else "normal"
        cycles = state_row["breach_cycles"] if state_row else 0

        is_breach = self._is_breach(value, threshold)
        events = []

        if is_breach:
            cycles += 1
            if cycles >= self.rules.stability_cycles and state != "triggered":
                events.append(AlertEvent(
                    type="alert_triggered",
                    node=node,
                    rule=rule,
                    value=value,
                    threshold=threshold,
                    message=self._message(rule, value, threshold, gpu_index, triggered=True),
                    timestamp=metrics.timestamp,
                ))
                await self.store.update_alert_state(node, rule, "triggered", cycles, float(value) if isinstance(value, (int, float)) else None)
            else:
                await self.store.update_alert_state(node, rule, "breaching", cycles, float(value) if isinstance(value, (int, float)) else None)
        else:
            if state == "triggered":
                events.append(AlertEvent(
                    type="alert_recovered",
                    node=node,
                    rule=rule,
                    value=value,
                    threshold=threshold,
                    message=self._message(rule, value, threshold, gpu_index, triggered=False),
                    timestamp=metrics.timestamp,
                ))
            await self.store.update_alert_state(node, rule, "normal", 0, float(value) if isinstance(value, (int, float)) else None)

        return events

    def _is_breach(self, value: float | bool, threshold: float | bool) -> bool:
        if isinstance(threshold, bool):
            return bool(value)
        if isinstance(value, bool):
            return value
        return value > threshold

    def _message(self, rule: str, value, threshold, gpu_index: int | None, triggered: bool) -> str:
        prefix = "🚨 Triggered" if triggered else "✅ Recovered"
        gpu_str = f" [GPU {gpu_index}]" if gpu_index is not None else ""
        return f"{prefix}: {rule}{gpu_str} value={value}, threshold={threshold}"
