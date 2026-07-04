"""
KP4PRA TNC - Runtime Status Helpers
All status written to /run/kp4pra-tnc/ (tmpfs).
Disappears after reboot. Never written to persistent storage.
"""

import os
import json
import time
from .config import load_config

def _runtime_dir() -> str:
    return load_config()["paths"]["runtime"]


def write_status(name: str, data: dict):
    """Write a JSON status blob to /run/kp4pra-tnc/<name>.json (runtime only)."""
    rdir = _runtime_dir()
    os.makedirs(rdir, exist_ok=True)
    path = os.path.join(rdir, f"{name}.json")
    tmp = path + ".tmp"
    data["_updated"] = time.time()
    try:
        with open(tmp, "w") as f:
            json.dump(data, f)
        os.replace(tmp, path)
    except Exception as e:
        print(f"[KP4PRA TNC] Warning: could not write runtime status {path}: {e}", flush=True)


def read_status(name: str) -> dict:
    """Read a JSON status blob from /run/kp4pra-tnc/<name>.json."""
    rdir = _runtime_dir()
    path = os.path.join(rdir, f"{name}.json")
    try:
        with open(path, "r") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        print(f"[KP4PRA TNC] Warning: could not read runtime status {path}: {e}", flush=True)
        return {}


def clear_status(name: str):
    """Remove a runtime status file."""
    rdir = _runtime_dir()
    path = os.path.join(rdir, f"{name}.json")
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass
    except Exception as e:
        print(f"[KP4PRA TNC] Warning: could not clear runtime status {path}: {e}", flush=True)


def set_reboot_pending(reason: str):
    write_status("reboot_pending", {"pending": True, "reason": reason})


def clear_reboot_pending():
    clear_status("reboot_pending")


def is_reboot_pending() -> bool:
    return read_status("reboot_pending").get("pending", False)


def set_provisioning_status(step: str, detail: str = "", success: bool = None, error: str = ""):
    write_status("provisioning", {
        "step": step,
        "detail": detail,
        "success": success,
        "error": error,
    })


def clear_provisioning_status():
    clear_status("provisioning")
