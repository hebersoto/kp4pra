"""
KP4PRA TNC - Filesystem Manager
Handles read-only root filesystem remount for permanent Bluetooth provisioning.
Uses helper scripts for remount so sudoers rules work without -o option parsing issues.
"""

import os
import subprocess
import time
from typing import Tuple, Optional

from .runtime_status import set_provisioning_status

BLUEZ_STATE_PATH = "/var/lib/bluetooth"
RW_CONFIG_PATH = "/rw/kp4pra-tnc"


def get_fs_mode(path: str = "/") -> str:
    try:
        with open("/proc/mounts", "r") as f:
            mounts = f.readlines()
        best_match = ""
        best_options = ""
        for line in mounts:
            parts = line.split()
            if len(parts) < 4:
                continue
            mount_point = parts[1]
            options = parts[3]
            if path.startswith(mount_point) and len(mount_point) >= len(best_match):
                best_match = mount_point
                best_options = options
        if "rw" in best_options.split(","):
            return "rw"
        return "ro"
    except Exception:
        return "unknown"


def is_path_writable(path: str) -> bool:
    return os.access(path, os.W_OK)


def is_rw_partition_available() -> bool:
    return os.path.isdir(RW_CONFIG_PATH) and is_path_writable(RW_CONFIG_PATH)


def is_bluez_state_writable() -> bool:
    return os.path.isdir(BLUEZ_STATE_PATH) and is_path_writable(BLUEZ_STATE_PATH)


def get_filesystem_status() -> dict:
    root_mode = get_fs_mode("/")
    rw_mode = get_fs_mode(RW_CONFIG_PATH) if os.path.exists(RW_CONFIG_PATH) else "missing"
    return {
        "root_mode": root_mode,
        "root_readonly": root_mode == "ro",
        "rw_partition_available": is_rw_partition_available(),
        "rw_partition_writable": is_path_writable(RW_CONFIG_PATH) if os.path.isdir(RW_CONFIG_PATH) else False,
        "rw_partition_path": RW_CONFIG_PATH,
        "bluez_state_path": BLUEZ_STATE_PATH,
        "bluez_state_writable": is_bluez_state_writable(),
        "bluez_state_exists": os.path.isdir(BLUEZ_STATE_PATH),
        "runtime_path": "/run/kp4pra-tnc",
        "runtime_writable": is_path_writable("/run/kp4pra-tnc") if os.path.isdir("/run/kp4pra-tnc") else False,
    }


def can_perform_permanent_provisioning() -> Tuple[bool, str]:
    if is_bluez_state_writable():
        return True, "BlueZ state directory is writable"
    return True, "Will remount to make BlueZ state writable"


def remount_root_rw() -> Tuple[bool, str]:
    set_provisioning_status("remount_rw", "Remounting filesystem read/write")
    try:
        result = subprocess.run(
            ["sudo", "/usr/local/bin/kp4pra-remount-rw"],
            capture_output=True, timeout=10
        )
        if result.returncode == 0:
            return True, "Filesystem remounted read/write"
        err = result.stderr.decode(errors="replace").strip()
        return False, f"Failed to remount rw: {err}"
    except subprocess.TimeoutExpired:
        return False, "remount timed out"
    except Exception as e:
        return False, str(e)


def remount_root_ro() -> Tuple[bool, str]:
    set_provisioning_status("remount_ro", "Remounting filesystem read-only")
    try:
        result = subprocess.run(
            ["sudo", "/usr/local/bin/kp4pra-remount-ro"],
            capture_output=True, timeout=10
        )
        if result.returncode == 0:
            return True, "Filesystem remounted read-only"
        err = result.stderr.decode(errors="replace").strip()
        return False, f"Failed to remount ro: {err}"
    except subprocess.TimeoutExpired:
        return False, "remount timed out"
    except Exception as e:
        return False, str(e)


def sync_storage() -> bool:
    try:
        subprocess.run(["sync"], timeout=10)
        time.sleep(1)
        subprocess.run(["sync"], timeout=10)
        return True
    except Exception as e:
        print(f"[KP4PRA TNC] sync failed: {e}", flush=True)
        return False


def schedule_reboot(delay: int = 3) -> Tuple[bool, str]:
    set_provisioning_status("reboot_scheduled", f"Rebooting in {delay} seconds")
    try:
        result = subprocess.run(
            ["sudo", "/bin/systemctl", "reboot"],
            capture_output=True, timeout=10
        )
        if result.returncode == 0:
            return True, "Reboot scheduled"
        return False, "Could not schedule reboot"
    except Exception as e:
        return False, str(e)


def restart_service(service_name: str) -> Tuple[bool, str]:
    ALLOWED = {
        "kp4pra-tnc-rfcomm.service",
        "kp4pra-tnc-ble.service",
        "kp4pra-tnc-web.service",
        "bluetooth.service",
    }
    if service_name not in ALLOWED:
        return False, f"Service {service_name!r} not in allowlist"
    try:
        result = subprocess.run(
            ["sudo", "/bin/systemctl", "restart", service_name],
            capture_output=True, timeout=15
        )
        if result.returncode == 0:
            return True, f"{service_name} restarted"
        err = result.stderr.decode(errors="replace").strip()
        return False, f"restart failed: {err}"
    except Exception as e:
        return False, str(e)


def stop_service(service_name: str) -> Tuple[bool, str]:
    ALLOWED = {
        "kp4pra-tnc-rfcomm.service",
        "kp4pra-tnc-ble.service",
    }
    if service_name not in ALLOWED:
        return False, f"Service {service_name!r} not in allowlist"
    try:
        result = subprocess.run(
            ["sudo", "/bin/systemctl", "stop", service_name],
            capture_output=True, timeout=15
        )
        if result.returncode == 0:
            return True, f"{service_name} stopped"
        err = result.stderr.decode(errors="replace").strip()
        return False, f"stop failed: {err}"
    except Exception as e:
        return False, str(e)


def get_service_status(service_name: str) -> dict:
    ALLOWED = {
        "kp4pra-tnc-rfcomm.service",
        "kp4pra-tnc-ble.service",
        "kp4pra-tnc-web.service",
        "bluetooth.service",
        "direwolf.service",
    }
    if service_name not in ALLOWED:
        return {"error": "not in allowlist", "active": False}
    try:
        result = subprocess.run(
            ["systemctl", "is-active", service_name],
            capture_output=True, timeout=5
        )
        active_state = result.stdout.decode().strip()
        result2 = subprocess.run(
            ["systemctl", "show", service_name,
             "--property=ActiveState,SubState,LoadState,MainPID"],
            capture_output=True, timeout=5
        )
        props = {}
        for line in result2.stdout.decode().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                props[k] = v
        return {
            "service": service_name,
            "active_state": props.get("ActiveState", active_state),
            "sub_state": props.get("SubState", ""),
            "load_state": props.get("LoadState", ""),
            "pid": props.get("MainPID", ""),
            "active": active_state == "active",
            "running": props.get("SubState", "") == "running",
        }
    except Exception as e:
        return {"service": service_name, "error": str(e), "active": False}


def get_volatile_service_log(service_name: str, lines: int = 30) -> Optional[str]:
    ALLOWED = {
        "kp4pra-tnc-rfcomm.service",
        "kp4pra-tnc-ble.service",
        "kp4pra-tnc-web.service",
    }
    if service_name not in ALLOWED:
        return None
    try:
        result = subprocess.run(
            ["journalctl", "-u", service_name, "-n", str(lines),
             "--no-pager", "--output=short-iso", "--boot"],
            capture_output=True, timeout=5
        )
        if result.returncode == 0:
            return result.stdout.decode(errors="replace")
        return None
    except Exception:
        return None


def check_direwolf_tcp(host: str = "127.0.0.1", port: int = 8001) -> dict:
    import socket
    try:
        with socket.create_connection((host, port), timeout=2.0):
            return {"reachable": True, "host": host, "port": port}
    except ConnectionRefusedError:
        return {"reachable": False, "host": host, "port": port, "error": "Connection refused"}
    except Exception as e:
        return {"reachable": False, "host": host, "port": port, "error": str(e)}


def check_dns_sd() -> dict:
    try:
        result = subprocess.run(
            ["avahi-browse", "-t", "-r", "_kiss-tnc._tcp"],
            capture_output=True, timeout=8
        )
        out = result.stdout.decode(errors="replace")
        found = "KP4PRA TNC" in out or "kiss" in out.lower()
        return {
            "available": result.returncode == 0,
            "found_kiss_tnc": found,
            "output_snippet": out[:400] if out else "",
        }
    except FileNotFoundError:
        return {"available": False, "error": "avahi-browse not found"}
    except Exception as e:
        return {"available": False, "error": str(e)}
