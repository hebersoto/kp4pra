"""
KP4PRA TNC - Configuration Writer
Writes configuration atomically to /rw/kp4pra-tnc/config.yaml.
Write-then-rename pattern for safety. fsync before rename.
Never writes to root filesystem. Never writes logs.
"""

import os
import yaml
import fcntl
from typing import Any

from .config import load_config, reload_config, CONFIG_PATH


def save_config(updates: dict, config_path: str = None) -> tuple[bool, str]:
    """
    Merge updates into current config and write atomically.
    Uses write-to-tmpfile + fsync + rename pattern.
    Returns (success, message).
    """
    cfg_path = config_path or CONFIG_PATH

    # Ensure directory exists (must be writable)
    cfg_dir = os.path.dirname(cfg_path)
    if not os.path.isdir(cfg_dir):
        try:
            os.makedirs(cfg_dir, exist_ok=True)
        except Exception as e:
            return False, f"Cannot create config directory {cfg_dir}: {e}"

    if not os.access(cfg_dir, os.W_OK):
        return False, f"Config directory {cfg_dir} is not writable"

    # Load current config
    current = load_config(cfg_path)

    # Deep merge updates into current
    merged = _deep_merge(current, updates)

    # Write atomically
    tmp_path = cfg_path + ".tmp"
    try:
        with open(tmp_path, "w") as f:
            yaml.dump(merged, f, default_flow_style=False, allow_unicode=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, cfg_path)
        reload_config()
        return True, "Configuration saved"
    except Exception as e:
        try:
            os.unlink(tmp_path)
        except Exception:
            pass
        return False, f"Failed to write config: {e}"


def _deep_merge(base: dict, override: dict) -> dict:
    result = dict(base)
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def validate_config_updates(updates: dict) -> tuple[bool, str]:
    """
    Validate proposed configuration updates before saving.
    Returns (valid, error_message).
    """
    errors = []

    if "direwolf" in updates:
        dw = updates["direwolf"]
        if "port" in dw:
            p = dw["port"]
            if not isinstance(p, int) or not (1 <= p <= 65535):
                errors.append(f"direwolf.port must be 1-65535, got {p!r}")
        if "host" in dw:
            h = dw["host"]
            if not isinstance(h, str) or len(h) > 253:
                errors.append(f"direwolf.host must be a valid hostname/IP")

    if "web" in updates:
        web = updates["web"]
        if "port" in web:
            p = web["port"]
            if not isinstance(p, int) or not (1024 <= p <= 65535):
                errors.append(f"web.port must be 1024-65535, got {p!r}")
        if "password" in web and isinstance(web["password"], str) and len(web["password"]) > 256:
            errors.append("web.password too long")

    if "ble" in updates:
        ble = updates["ble"]
        if "device_name" in ble:
            n = ble["device_name"]
            if not isinstance(n, str) or len(n) > 248 or len(n) == 0:
                errors.append(f"ble.device_name must be 1-248 chars")

    if "rfcomm" in updates:
        rf = updates["rfcomm"]
        if "device_name" in rf:
            n = rf["device_name"]
            if not isinstance(n, str) or len(n) > 248 or len(n) == 0:
                errors.append(f"rfcomm.device_name must be 1-248 chars")
        if "channel" in rf:
            c = rf["channel"]
            if not isinstance(c, int) or not (1 <= c <= 30):
                errors.append(f"rfcomm.channel must be 1-30")

    if "station" in updates:
        st = updates["station"]
        import re as _re
        if "callsign" in st:
            cs = st["callsign"]
            if not isinstance(cs, str) or not (1 <= len(cs) <= 6) or not _re.match(r"^[A-Za-z0-9]+$", cs):
                errors.append("station.callsign must be 1-6 alphanumeric characters")
        for ssid_field in ("ssid", "aliasssid"):
            if ssid_field in st and st[ssid_field] not in ("", None):
                try:
                    v = int(st[ssid_field])
                    if not (1 <= v <= 15):
                        errors.append(f"station.{ssid_field} must be between 1 and 15")
                except (ValueError, TypeError):
                    errors.append(f"station.{ssid_field} must be a number between 1 and 15")
        if "mygrid" in st:
            g = st["mygrid"]
            if not isinstance(g, str) or not _re.match(r"^[A-Ra-r]{2}[0-9]{2}([A-Xa-x]{2})?$", g):
                errors.append("station.mygrid must be a valid Maidenhead grid square (e.g. FK68 or FK68wd)")
        if "lat" in st:
            try:
                v = float(st["lat"])
                if not (-90.0 <= v <= 90.0):
                    errors.append("station.lat must be between -90 and 90")
            except (ValueError, TypeError):
                errors.append("station.lat must be a number")
        if "lon" in st:
            try:
                v = float(st["lon"])
                if not (-180.0 <= v <= 180.0):
                    errors.append("station.lon must be between -180 and 180")
            except (ValueError, TypeError):
                errors.append("station.lon must be a number")
        if "calias" in st and st["calias"]:
            ca = st["calias"]
            if not isinstance(ca, str) or len(ca) > 6 or not _re.match(r"^[A-Za-z0-9]+$", ca):
                errors.append("station.calias must be 1-6 alphanumeric characters")
        if "clock" in st and st["clock"]:
            ck = st["clock"]
            if not isinstance(ck, str) or not _re.match(r"^[A-Za-z0-9]{1,6}(-([1-9]|1[0-5]))?$", ck):
                errors.append("station.clock must be CALLSIGN or CALLSIGN-SSID (SSID 1-15)")

    if errors:
        return False, "; ".join(errors)
    return True, ""
