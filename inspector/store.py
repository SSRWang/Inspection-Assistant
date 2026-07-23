from __future__ import annotations
import asyncio
import json
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from inspector.models import NodeMetrics


class SqliteStore:
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()
        self._connection: sqlite3.Connection | None = None

    async def setup(self) -> None:
        async with self._lock:
            self._connection = sqlite3.connect(str(self.db_path), check_same_thread=False)
            self._connection.row_factory = sqlite3.Row
            self._connection.executescript(self._schema())
            self._connection.commit()

    async def close(self) -> None:
        async with self._lock:
            if self._connection:
                self._connection.close()
                self._connection = None

    def _schema(self) -> str:
        return """
        CREATE TABLE IF NOT EXISTS metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node TEXT NOT NULL,
            category TEXT NOT NULL,
            name TEXT NOT NULL,
            value REAL,
            unit TEXT,
            labels TEXT,
            timestamp TEXT NOT NULL,
            raw_output TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_metrics_node_time ON metrics(node, timestamp);

        CREATE TABLE IF NOT EXISTS node_status (
            node TEXT PRIMARY KEY,
            reachable INTEGER NOT NULL,
            summary TEXT,
            last_check_at TEXT NOT NULL,
            raw_metrics TEXT
        );

        CREATE TABLE IF NOT EXISTS alert_states (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            node TEXT NOT NULL,
            rule TEXT NOT NULL,
            state TEXT NOT NULL,
            breach_cycles INTEGER NOT NULL DEFAULT 0,
            last_value REAL,
            triggered_at TEXT,
            updated_at TEXT NOT NULL,
            UNIQUE(node, rule)
        );

        CREATE TABLE IF NOT EXISTS pending_webhooks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            payload TEXT NOT NULL,
            attempts INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            next_retry_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
        );
        CREATE INDEX IF NOT EXISTS idx_webhooks_retry ON pending_webhooks(status, next_retry_at);
        """

    async def write_metrics(self, records: list[dict[str, Any]]) -> None:
        async with self._lock:
            now = datetime.now(timezone.utc).isoformat()
            for rec in records:
                self._connection.execute(
                    "INSERT INTO metrics (node, category, name, value, unit, labels, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (rec["node"], rec["category"], rec["name"], rec.get("value"),
                     rec.get("unit"), json.dumps(rec.get("labels") or {}), now)
                )
            self._connection.commit()

    async def write_node_status(self, metrics: NodeMetrics) -> None:
        async with self._lock:
            now = metrics.timestamp.isoformat()
            summary = self._build_summary(metrics)
            self._connection.execute(
                """INSERT INTO node_status (node, reachable, summary, last_check_at, raw_metrics)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(node) DO UPDATE SET
                   reachable=excluded.reachable, summary=excluded.summary,
                   last_check_at=excluded.last_check_at, raw_metrics=excluded.raw_metrics""",
                (metrics.node, int(metrics.reachable), summary, now,
                 json.dumps(metrics.raw, default=str))
            )
            self._connection.commit()

    def _build_summary(self, metrics: NodeMetrics) -> str:
        if not metrics.reachable:
            return "Node unreachable"
        gpu_summary = f"{len(metrics.gpus)} GPUs"
        avg_temp = sum(g.temperature_c for g in metrics.gpus if g.temperature_c is not None) / max(len(metrics.gpus), 1)
        return f"{gpu_summary}, avg temp {avg_temp:.1f}°C"

    async def get_node_status(self, node: str) -> dict[str, Any] | None:
        async with self._lock:
            row = self._connection.execute(
                "SELECT * FROM node_status WHERE node = ?", (node,)
            ).fetchone()
            if row is None:
                return None
            return dict(row)

    async def list_node_status(self) -> list[dict[str, Any]]:
        async with self._lock:
            rows = self._connection.execute("SELECT * FROM node_status ORDER BY node").fetchall()
            return [dict(r) for r in rows]

    async def get_alert_state(self, node: str, rule: str) -> dict[str, Any] | None:
        async with self._lock:
            row = self._connection.execute(
                "SELECT * FROM alert_states WHERE node = ? AND rule = ?", (node, rule)
            ).fetchone()
            if row is None:
                return None
            return dict(row)

    async def update_alert_state(self, node: str, rule: str, state: str,
                                 breach_cycles: int, last_value: float | None) -> None:
        async with self._lock:
            now = datetime.now(timezone.utc).isoformat()
            triggered_at = now if state == "triggered" else None
            self._connection.execute(
                """INSERT INTO alert_states (node, rule, state, breach_cycles, last_value, triggered_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(node, rule) DO UPDATE SET
                   state=excluded.state, breach_cycles=excluded.breach_cycles,
                   last_value=excluded.last_value,
                   triggered_at=COALESCE(excluded.triggered_at, triggered_at),
                   updated_at=excluded.updated_at""",
                (node, rule, state, breach_cycles, last_value, triggered_at, now)
            )
            self._connection.commit()

    async def list_alert_states(self) -> list[dict[str, Any]]:
        async with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM alert_states WHERE state = 'triggered' ORDER BY updated_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    async def enqueue_webhook(self, payload: dict[str, Any], attempts: int = 0) -> None:
        async with self._lock:
            now = datetime.now(timezone.utc)
            next_retry = now + timedelta(seconds=60)
            self._connection.execute(
                "INSERT INTO pending_webhooks (payload, attempts, created_at, next_retry_at, status) VALUES (?, ?, ?, ?, ?)",
                (json.dumps(payload, default=str), attempts, now.isoformat(), next_retry.isoformat(), "pending")
            )
            self._connection.commit()

    async def dequeue_pending_webhooks(self, limit: int = 10) -> list[tuple[int, dict[str, Any], int]]:
        async with self._lock:
            now = datetime.now(timezone.utc).isoformat()
            rows = self._connection.execute(
                "SELECT id, payload, attempts FROM pending_webhooks WHERE status = 'pending' AND next_retry_at <= ? ORDER BY next_retry_at LIMIT ?",
                (now, limit)
            ).fetchall()
            return [(r["id"], json.loads(r["payload"]), r["attempts"]) for r in rows]

    async def delete_webhook(self, record_id: int) -> None:
        async with self._lock:
            self._connection.execute("DELETE FROM pending_webhooks WHERE id = ?", (record_id,))
            self._connection.commit()

    async def update_webhook_retry(self, record_id: int, attempts: int, next_retry_at: datetime) -> None:
        async with self._lock:
            self._connection.execute(
                "UPDATE pending_webhooks SET attempts = ?, next_retry_at = ? WHERE id = ?",
                (attempts, next_retry_at.isoformat(), record_id)
            )
            self._connection.commit()

    async def mark_webhook_dead(self, record_id: int) -> None:
        async with self._lock:
            self._connection.execute(
                "UPDATE pending_webhooks SET status = 'dead' WHERE id = ?", (record_id,)
            )
            self._connection.commit()

    async def cleanup_metrics(self, retain_days: int) -> None:
        async with self._lock:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=retain_days)).isoformat()
            self._connection.execute("DELETE FROM metrics WHERE timestamp < ?", (cutoff,))
            self._connection.commit()

    async def list_metrics(self, node: str | None = None, rule: str | None = None,
                           limit: int = 100) -> list[dict[str, Any]]:
        async with self._lock:
            query = "SELECT * FROM metrics WHERE 1=1"
            params: list[Any] = []
            if node:
                query += " AND node = ?"
                params.append(node)
            if rule:
                query += " AND name = ?"
                params.append(rule)
            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)
            rows = self._connection.execute(query, params).fetchall()
            return [dict(r) for r in rows]
