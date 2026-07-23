from __future__ import annotations
import logging
from datetime import datetime, timezone, timedelta
from inspector.alerter import Alerter
from inspector.collector import Collector
from inspector.config import Settings
from inspector.metrics import flatten_metrics
from inspector.notifier import BaseNotifier
from inspector.store import SqliteStore

logger = logging.getLogger(__name__)
_is_collecting = False


class SettingsHolder:
    """Allows configuration hot-reload without recreating scheduled jobs."""
    def __init__(self, settings: Settings):
        self.settings = settings


async def run_inspection_cycle(collector: Collector, alerter: Alerter, notifier: BaseNotifier,
                               store: SqliteStore, cfg_holder: SettingsHolder) -> None:
    global _is_collecting
    if _is_collecting:
        logger.warning("Previous inspection cycle is still running; skipping this round")
        return
    _is_collecting = True
    try:
        cfg = cfg_holder.settings
        await _retry_pending_webhooks(store, notifier, cfg)
        metrics_list = await collector.collect_all()
        for metrics in metrics_list:
            await store.write_node_status(metrics)
            records = flatten_metrics(metrics)
            await store.write_metrics(records)

            events = await alerter.evaluate(metrics)
            for event in events:
                payload = _event_to_payload(event)
                ok = await notifier.send(payload)
                if not ok:
                    await store.enqueue_webhook(payload)

        await _send_periodic_report(store, notifier)
        await store.cleanup_metrics(cfg.storage.retain_days)
    except Exception as e:
        logger.exception("Inspection cycle failed: %s", e)
    finally:
        _is_collecting = False


async def _retry_pending_webhooks(store: SqliteStore, notifier: BaseNotifier, cfg: Settings) -> None:
    pending = await store.dequeue_pending_webhooks(limit=10)
    for record_id, payload, attempts in pending:
        ok = await notifier.send(payload)
        if ok:
            await store.delete_webhook(record_id)
        else:
            if attempts + 1 >= cfg.notifications.max_retries:
                await store.mark_webhook_dead(record_id)
            else:
                next_retry = datetime.now(timezone.utc) + timedelta(
                    seconds=cfg.notifications.retry_interval_seconds)
                await store.update_webhook_retry(record_id, attempts + 1, next_retry)


async def _send_periodic_report(store: SqliteStore, notifier: BaseNotifier) -> None:
    statuses = await store.list_node_status()
    payload = {
        "type": "periodic_report",
        "title": "GPU 节点巡检报告",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "node_count": len(statuses),
        "online_count": sum(1 for s in statuses if s["reachable"]),
        "nodes": [
            {
                "name": s["node"],
                "reachable": bool(s["reachable"]),
                "summary": s["summary"],
                "last_check_at": s["last_check_at"],
            }
            for s in statuses
        ],
    }
    ok = await notifier.send(payload)
    if not ok:
        await store.enqueue_webhook(payload)


def _event_to_payload(event) -> dict:
    return {
        "type": event.type,
        "node": event.node,
        "rule": event.rule,
        "value": event.value,
        "threshold": event.threshold,
        "message": event.message,
        "timestamp": event.timestamp.isoformat(),
    }
