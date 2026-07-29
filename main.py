from __future__ import annotations
import asyncio
import logging
import os
import signal
import sys
from pathlib import Path
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import uvicorn
from inspector.alerter import Alerter
from inspector.collector import Collector
from inspector.config import Settings, load_config
from inspector.dashboard import create_app
from inspector.notifier import create_notifier
from inspector.scheduler import SettingsHolder, run_inspection_cycle
from inspector.store import SqliteStore

_logger = logging.getLogger(__name__)
_config_path = Path(os.environ.get("INSPECTOR_CONFIG_PATH", "config.yaml"))
_settings_holder: SettingsHolder | None = None
_store: SqliteStore | None = None


def setup_logging(level: str):
    level_name = level.upper()
    if not hasattr(logging, level_name) or not isinstance(getattr(logging, level_name), int):
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
            handlers=[logging.StreamHandler(sys.stdout)],
        )
        _logger.warning("Invalid log level %r; defaulting to INFO", level)
        return
    logging.basicConfig(
        level=getattr(logging, level_name),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def _validate_ssh_keys(settings: Settings):
    for node in settings.nodes:
        try:
            settings.ssh.resolve_private_key_path(node.ssh_private_key_path_env)
        except Exception as exc:
            _logger.error("SSH key validation failed for node %r: %s", node.name, exc)
            sys.exit(1)


async def main():
    global _settings_holder, _store
    settings = load_config(_config_path)
    _settings_holder = SettingsHolder(settings)
    setup_logging(settings.app.log_level)

    _validate_ssh_keys(settings)

    _store = SqliteStore(settings.storage.path)
    await _store.setup()

    collector = Collector(settings, _store)
    alerter = Alerter(settings.alert_rules, _store)
    notifier = create_notifier(settings.notifications)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        run_inspection_cycle,
        "interval",
        minutes=settings.schedule.interval_minutes,
        args=(collector, alerter, notifier, _store, _settings_holder),
        id="inspection_cycle",
        replace_existing=True,
    )
    scheduler.start()

    app = create_app(settings, _store, notifier)
    config = uvicorn.Config(app, host=settings.dashboard.host, port=settings.dashboard.port, log_level="info")
    server = uvicorn.Server(config)

    loop = asyncio.get_running_loop()
    if sys.platform != "win32":
        for sig in (signal.SIGHUP,):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(reload_config()))

    _logger.info("Service started")
    server_task = asyncio.create_task(server.serve())
    try:
        await server_task
    finally:
        _logger.info("Shutting down service")
        try:
            scheduler.shutdown()
        except Exception:
            _logger.exception("Error shutting down scheduler")
        if _store is not None:
            try:
                await _store.close()
            except Exception:
                _logger.exception("Error closing store")
        if not server_task.done():
            server_task.cancel()
            try:
                await server_task
            except asyncio.CancelledError:
                pass


async def reload_config():
    global _settings_holder
    try:
        new_settings = load_config(_config_path)
        _settings_holder.settings = new_settings
        _logger.info("Configuration reloaded")
    except Exception:
        _logger.exception("Failed to reload config, keeping current config")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        _logger.info("Shutdown requested by user")
