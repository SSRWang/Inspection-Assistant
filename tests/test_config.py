import os
from pathlib import Path
import pytest
import yaml
from inspector.config import Settings, load_config


def test_load_config_reads_file(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(yaml.safe_dump({
        "app": {"name": "test", "log_level": "DEBUG"},
        "schedule": {"interval_minutes": 5},
        "storage": {"path": "data/test.db", "retain_days": 7},
        "ssh": {"private_key_path_env": "SSH_PRIVATE_KEY_PATH", "connect_timeout": 10, "command_timeout": 30},
        "nodes": [{"name": "n1", "host": "1.2.3.4", "user": "u", "gpu_vendor": "nvidia"}],
        "alert_rules": {"gpu_temp_c": 85, "node_unreachable": True, "stability_cycles": 2},
        "notifications": {"type": "generic", "webhook_url_env": "W", "max_retries": 5},
        "dashboard": {"enabled": True, "host": "127.0.0.1", "port": 8080},
    }))
    os.environ["SSH_PRIVATE_KEY_PATH"] = str(tmp_path / "key.pem")
    key_path = tmp_path / "key.pem"
    key_path.write_text("fake-key")
    key_path.chmod(0o600)

    cfg = load_config(cfg_path)
    assert isinstance(cfg, Settings)
    assert cfg.app.name == "test"
    assert cfg.nodes[0].host == "1.2.3.4"
    assert cfg.alert_rules.gpu_temp_c == 85
