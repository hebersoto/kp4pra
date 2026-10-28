"""
KP4PRA TNC - system facts shown on the TFT (all read-only, all best-effort).

Every function returns a safe default instead of raising, so a missing tool
or a permission problem shows "?" on the screen rather than stopping it.
"""

import glob
import json
import os
import re
import subprocess

AP_CONNECTION = "KP4PRA-AP"      # NetworkManager profile made by kp4pra-wifi-mode
AP_IP = "172.16.0.1"
WINLINK_PENDING = ("Holding", "Approved", "Sending")


def _run(cmd, timeout=4) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, timeout=timeout,
                              text=True).stdout
    except Exception:
        return ""


def station_call(cfg: dict) -> str:
    """Station callsign with SSID, e.g. KP4PRA-10 (same as Dire Wolf MYCALL)."""
    st = cfg.get("station", {}) or {}
    call = (st.get("callsign") or "").strip().upper() or "NOCALL"
    ssid = str(st.get("ssid") or "").strip()
    return "%s-%s" % (call, ssid) if ssid not in ("", "0") else call


def wifi_mode() -> str:
    """'AP' when the KP4PRA hotspot is up, else 'CL' (client)."""
    out = _run(["nmcli", "-t", "-f", "NAME", "connection", "show", "--active"])
    return "AP" if any(l.strip() == AP_CONNECTION for l in out.splitlines()) else "CL"


def primary_ip(mode: str) -> str:
    """The address users reach the web UI on."""
    if mode == "AP":
        return AP_IP
    out = _run(["ip", "-4", "route", "get", "1.1.1.1"])
    m = re.search(r"\bsrc\s+(\d+\.\d+\.\d+\.\d+)", out)
    if m:
        return m.group(1)
    out = _run(["ip", "-4", "-o", "addr", "show", "scope", "global"])
    m = re.search(r"inet\s+(\d+\.\d+\.\d+\.\d+)", out)
    return m.group(1) if m else "no IP"


def wifi_clients(mode: str) -> int:
    """Devices joined to the KP4PRA hotspot (0 in client mode)."""
    if mode != "AP":
        return 0
    out = _run(["iw", "dev", "wlan0", "station", "dump"])
    n = sum(1 for l in out.splitlines() if l.startswith("Station "))
    if n:
        return n
    # Fallback without nl80211 access: live neighbours on the AP interface.
    out = _run(["ip", "neigh", "show", "dev", "wlan0"])
    return sum(1 for l in out.splitlines()
               if l.strip() and not re.search(r"\b(FAILED|INCOMPLETE)\b", l))


def bt_clients() -> int:
    """Phones connected over Bluetooth (BLE or RFCOMM)."""
    out = _run(["bluetoothctl", "devices", "Connected"])
    return sum(1 for l in out.splitlines() if l.startswith("Device "))


def winlink_pending(cfg: dict) -> int:
    """Web-email messages not yet sent out via Winlink."""
    data = (cfg.get("paths", {}) or {}).get("data", "/rw/kp4pra-tnc/data")
    n = 0
    for path in glob.glob(os.path.join(data, "mailq", "*.json")):
        try:
            with open(path, encoding="utf-8") as f:
                if json.load(f).get("status") in WINLINK_PENDING:
                    n += 1
        except Exception:
            continue
    return n


def snapshot(cfg: dict) -> dict:
    mode = wifi_mode()
    return {
        "call": station_call(cfg),
        "mode": mode,
        "ip": primary_ip(mode),
        "wifi_clients": wifi_clients(mode),
        "bt_clients": bt_clients(),
        "winlink_pending": winlink_pending(cfg),
    }
