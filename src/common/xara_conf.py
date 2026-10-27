"""
KP4PRA TNC - Xara (VARA-compatible modem) configuration.

Xara (https://github.com/x0rro/xara) is installed and upgraded by
bin/kp4pra-xara. This module only turns KP4PRA's own settings (station +
the "xara" section of config.yaml) into Xara's config file, so the Config
page stays the one place to set things:

  /rw/xara/kp4pra.conf     read by xara@kp4pra.service (/etc/xara/kp4pra.conf
                           is a symlink to it - the root filesystem may be
                           read-only)
  /run/kp4pra-xara/ptt.json  GPIO line for kp4pra-xara-ptt (DRA-Pi-Zero and
                           other GPIO PTT; Xara itself has no GPIO PTT and
                           keys it through the rigctld protocol instead)

It runs as root right before Xara starts (kp4pra-xara-prep.service), so the
sound card and the CM108 /dev/hidrawN (both renumber between boots) are
looked up fresh every time.

    python -m common.xara_conf --write     # write both files
    python -m common.xara_conf --show      # print the config text
"""

import json
import os
import sys

from common.direwolf_conf import (
    _call_with_ssid, resolve_adevice, gpiod_chip_line, find_cm108_hidraw,
)

CONF_PATH = "/rw/xara/kp4pra.conf"
PTT_PLAN_PATH = "/run/kp4pra-xara/ptt.json"
# Local rigctld-protocol PTT helper (bin/kp4pra-xara-ptt). Not 4532, so a
# real rigctld for a CAT radio can keep its usual port.
PTT_HELPER_ADDR = "127.0.0.1:4539"

MODES = {
    "fm-narrow": ("fm", "narrow"),
    "fm-wide": ("fm", "wide"),
    "hf-500": ("hf", "500"),
    "hf-2300": ("hf", "2300"),
    "hf-2750": ("hf", "2750"),
}
MODE_LABELS = {
    "fm-narrow": "VARA FM narrow",
    "fm-wide": "VARA FM wide",
    "hf-500": "VARA HF 500 Hz",
    "hf-2300": "VARA HF 2300 Hz",
    "hf-2750": "VARA HF 2750 Hz",
}
DEFAULTS = {
    "version": "latest",
    "mode": "fm-narrow",
    "call": "",
    "drive_db": -5,
    "ptt_lead_ms": 0,
    "ptt_tail_ms": 100,
    "network": True,
    "ui_port": 8080,
    "port": 8300,
    "kiss_port": 8100,
}


def settings(cfg: dict) -> dict:
    """The xara section merged over DEFAULTS."""
    out = dict(DEFAULTS)
    out.update({k: v for k, v in ((cfg or {}).get("xara") or {}).items() if v is not None})
    return out


def valid_vara_call(call: str) -> bool:
    """Same rule as Xara (xara-host valid_call): base 1-7 letters/digits,
    optional SSID 1-19 or one of T R X L (VARA has no -0)."""
    u = (call or "").strip().upper()
    base, _, ssid = u.partition("-")
    if not (1 <= len(base) <= 7 and base.isalnum() and base.isascii()):
        return False
    if "-" not in u:
        return True
    if len(ssid) == 1:
        return ssid in "TRXL" or ssid in "123456789"
    return len(ssid) == 2 and ssid.isdigit() and 1 <= int(ssid) <= 19


def xara_call(cfg: dict) -> str:
    """Xara callsign: xara.call if set, else station callsign-SSID. An SSID
    VARA does not accept (0, 20+) is left off instead of failing Xara."""
    x = settings(cfg)
    if str(x.get("call") or "").strip():
        return str(x["call"]).strip().upper()
    st = (cfg or {}).get("station", {}) or {}
    call = _call_with_ssid(st.get("callsign", ""), st.get("ssid", ""))
    if not valid_vara_call(call):
        call = _call_with_ssid(st.get("callsign", ""), "")
    return call


def ptt_plan(station: dict, adevice: str, hidraw_lookup=find_cm108_hidraw) -> dict:
    """How Xara keys the radio, from the station PTT setting (same setting
    Dire Wolf uses). Returns {"ptt": <xara --ptt value or None>, "gpio":
    {"chip", "line", "active_low"} or None, "describe": text}."""
    ptt = (station.get("ptt") or "CM108").upper()
    param = (station.get("ptt_param") or "").strip()
    if ptt == "CM108":
        hid = hidraw_lookup(adevice)
        if not hid:
            return {"ptt": None, "gpio": None,
                    "describe": "CM108 PTT selected but no CM108 device found - receive only"}
        return {"ptt": f"cm108:{hid}", "gpio": None, "describe": f"CM108 GPIO on {hid}"}
    if ptt in ("GPIOD", "GPIO"):
        if ptt == "GPIO":   # legacy sysfs pin number -> line on the first chip
            param = f"gpiochip0 {param}" if param.lstrip("-").isdigit() else param
        chip, line = gpiod_chip_line(param, adevice)
        low = line.startswith("-")
        line = line.lstrip("-")
        return {"ptt": f"rigctld:{PTT_HELPER_ADDR}",
                "gpio": {"chip": chip, "line": int(line), "active_low": low},
                "describe": f"GPIO {chip} line {line}{' (active low)' if low else ''}"}
    if ptt in ("RTS", "DTR"):
        dev = param if param.startswith("/dev/") else "/dev/ttyUSB0"
        return {"ptt": f"{ptt.lower()}:{dev}", "gpio": None, "describe": f"{ptt} on {dev}"}
    if ptt == "VOX":
        return {"ptt": "vox", "gpio": None, "describe": "VOX"}
    if ptt == "RIG":
        # A rigctld the operator runs for a CAT radio (default localhost:4532).
        addr = param if ":" in param else ""
        return {"ptt": "rigctld" + (f":{addr}" if addr else ""), "gpio": None,
                "describe": f"rigctld at {addr or '127.0.0.1:4532'}"}
    return {"ptt": None, "gpio": None, "describe": "no PTT - receive only"}


def generate(cfg: dict, adevice: str = None, hidraw_lookup=find_cm108_hidraw) -> tuple:
    """Return (config text, ptt plan)."""
    x = settings(cfg)
    st = (cfg or {}).get("station", {}) or {}
    adevice = adevice or resolve_adevice(st)
    band, value = MODES.get(str(x["mode"]), MODES["fm-narrow"])
    plan = ptt_plan(st, adevice, hidraw_lookup)
    addr = "0.0.0.0" if x.get("network") else "127.0.0.1"
    kiss = x.get("kiss_port")
    lines = [
        "# Generated by KP4PRA TNC - do not edit by hand.",
        "# Change these on the KP4PRA web page (Config > Xara VARA modem).",
        f"call = {xara_call(cfg)}",
        f"{band} = {value}",
        "audio = alsa",
        f"capture = {adevice}",
        f"playback = {adevice}",
    ]
    if plan["ptt"]:
        lines += [
            "radio = on",
            f"ptt = {plan['ptt']}",
            f"drive-db = {float(x['drive_db']):g}",
            f"ptt-lead-ms = {int(x['ptt_lead_ms'])}",
            f"ptt-tail-ms = {int(x['ptt_tail_ms'])}",
        ]
    else:
        lines.append(f"# {plan['describe']}")
    lines += [
        f"addr = {addr}",
        f"port = {int(x['port'])}",
        f"kiss-port = {int(kiss) if kiss not in (None, '', 'off', False) else 'off'}",
    ]
    if x.get("ui_port"):
        lines += [f"ui-port = {int(x['ui_port'])}", "ui-addr = 0.0.0.0"]
    return "\n".join(lines) + "\n", plan


def _write_atomic(path: str, text: str, mode: int, group: str = None):
    d = os.path.dirname(path)
    os.makedirs(d, exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, mode)
    if group:
        try:
            import grp
            os.chown(tmp, 0, grp.getgrnam(group).gr_gid)
        except (KeyError, PermissionError):
            pass
    os.replace(tmp, path)


def write(cfg: dict, conf_path: str = CONF_PATH, plan_path: str = PTT_PLAN_PATH) -> dict:
    text, plan = generate(cfg)
    _write_atomic(conf_path, text, 0o640, group="xara")
    _write_atomic(plan_path, json.dumps(plan) + "\n", 0o644)
    return plan


def main(argv) -> int:
    from common.config import load_config
    cfg = load_config()
    if "--write" in argv:
        plan = write(cfg)
        print(f"xara config written to {CONF_PATH} (PTT: {plan['describe']})")
        return 0
    text, plan = generate(cfg)
    sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
