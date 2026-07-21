"""
KP4PRA TNC - Shared Configuration
Loads config from /rw/kp4pra-tnc/config.yaml with safe defaults.
No persistent logging. No disk writes during normal operation.
"""

import os
import yaml
import logging

PRODUCT_NAME = "KP4PRA TNC"

DEFAULT_CONFIG = {
    "product_name": PRODUCT_NAME,
    "direwolf": {
        "host": "127.0.0.1",
        "port": 8001,
    },
    "ble": {
        "enabled": True,
        "device_name": PRODUCT_NAME,
        "start_at_boot": True,
    },
    "rfcomm": {
        "enabled": True,
        "device_name": PRODUCT_NAME,
        "start_at_boot": True,
        "channel": 1,
    },
    "dns_sd": {
        "instance_name": "KP4PRA TNC on orangepi",
    },
    "web": {
        "host": "0.0.0.0",
        "port": 8088,
        "auth_enabled": False,
        "username": "admin",
        "password": "",
    },
    "bluetooth": {
        "auto_discoverable": False,
        "auto_pairable": False,
        "bluez_state_strategy": "persistent_bind_mount",
        # Options: read_only_preprovisioned | persistent_bind_mount | volatile
    },
    "paths": {
        "config": "/rw/kp4pra-tnc/config.yaml",
        "runtime": "/run/kp4pra-tnc",
        "state": "/rw/kp4pra-tnc/state",
        "data": "/rw/kp4pra-tnc/data",
        "bluez_state": "/rw/kp4pra-tnc/bluetooth",
    },
    "debug": {
        "verbose_stdout": False,
    },
    "wifi": {
        "ssid": "",
        "password": "qwerty1234",
        "channel": 6,
        "mode_at_boot": "client",
        "client_ssid": "",
        "client_password": "",
    },
    "rms": {
        "enabled": False,
        "cms_call": "",
        "cms_password": "",
        "frequency_hz": 145050000,
        "mode": "PACKET-1200",
        "cms_host": "cms.winlink.org",
        "cms_port": 8772,
    },
}

CONFIG_PATH = os.environ.get("KP4PRA_CONFIG", "/rw/kp4pra-tnc/config.yaml")

_config_cache = None


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base, returning a new dict."""
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def load_config(path: str = None) -> dict:
    """
    Load configuration from YAML file, merged over defaults.
    Returns defaults if file is missing or unreadable.
    No logging to disk - only stderr output on error.
    """
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    cfg_path = path or CONFIG_PATH
    config = dict(DEFAULT_CONFIG)

    if os.path.isfile(cfg_path):
        try:
            with open(cfg_path, "r") as f:
                loaded = yaml.safe_load(f) or {}
            config = _deep_merge(DEFAULT_CONFIG, loaded)
        except Exception as e:
            print(f"[KP4PRA TNC] Warning: could not read config {cfg_path}: {e}", flush=True)
    else:
        print(f"[KP4PRA TNC] Config {cfg_path} not found, using defaults.", flush=True)

    _config_cache = config
    return config


def reload_config():
    """Force reload of configuration from disk."""
    global _config_cache
    _config_cache = None
    return load_config()


def runtime_path(*parts) -> str:
    """Return a path under the runtime directory."""
    cfg = load_config()
    base = cfg["paths"]["runtime"]
    return os.path.join(base, *parts)


def state_path(*parts) -> str:
    """Return a path under the persistent state directory."""
    cfg = load_config()
    base = cfg["paths"]["state"]
    return os.path.join(base, *parts)
