from __future__ import annotations
import asyncio
import logging
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
_config_path = Path("config.yaml")
_settings_holder: SettingsHolder | None = None
_store: SqliteStore | None = None


def setup_logging(level: str):
    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


async def main():
    global _settings_holder, _store
    settings = load_config(_config_path)
    _settings_holder = SettingsHolder(settings)
    setup_logging(settings.app.log_level)

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

    app = create_app(settings, _store)
    config = uvicorn.Config(app, host=settings.dashboard.host, port=settings.dashboard.port, log_level="info")
    server = uvicorn.Server(config)

    loop = asyncio.get_running_loop()
    if sys.platform != "win32":
        for sig in (signal.SIGHUP,):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(reload_config()))

    _logger.info("Service started")
    await server.serve()


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
        pass
