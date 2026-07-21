"""
KP4PRA TNC - direwolf.conf generator
Builds direwolf.conf from the station section of config.yaml.
Sound card and CM108 ADEVICE detection from live system state.
Only writes when the user explicitly applies from the web UI.
"""

import os
import re
import subprocess
from typing import Optional

# Real storage location (writable partition). The web service writes here
# directly. /home/kp4pra/direwolf.conf is a symlink to this file, used only
# by Direwolf's -c flag; the service user cannot traverse /home/kp4pra.
DIREWOLF_CONF_PATH = "/rw/kp4pra-tnc/direwolf.conf"

# label shown in UI -> keyword patterns matched against `aplay -l` card names
CARD_SIGNATURES = [
    ("CM108 USB dongle",  ["cm108", "c-media", "usb audio device", "usb pnp sound"]),
    # "usb audio codec" (TI PCM290x) is shared by Signalink AND Yaesu USB
    # radios (FT-991A etc). Disambiguated in detect_sound_cards() by the
    # presence of CP210x serial ports, which only the Yaesu provides.
    ("Signalink",         ["usb audio codec"]),
    ("Yaesu USB radio",   ["yaesu", "ft-991", "ftdx"]),
    ("Icom USB radio",    ["icom", "ic-7300", "ic-705", "ic-9700"]),
    ("Kenwood USB radio", ["kenwood", "ts-590", "ts-890"]),
]

# Suggested PTT per detected card label: (ptt_method, ptt_param)
PTT_SUGGESTIONS = {
    "CM108 USB dongle":  ("CM108", ""),
    "Signalink":         ("VOX", ""),
    "Yaesu USB radio":   ("RTS", "/dev/ttyUSB1"),   # FT-991A: USB1 = PTT/standard port
    "Icom USB radio":    ("RTS", "/dev/ttyUSB1"),
    "Kenwood USB radio": ("RTS", "/dev/ttyUSB0"),
}


def detect_cp210x_ports() -> list:
    """Return /dev/ttyUSBn ports driven by cp210x (Yaesu FT-991A exposes two)."""
    import glob
    ports = []
    for dev in sorted(glob.glob("/sys/bus/usb-serial/devices/ttyUSB*")):
        name = os.path.basename(dev)
        try:
            drv = os.path.basename(os.path.realpath(os.path.join(dev, "driver")))
        except Exception:
            drv = ""
        if drv == "cp210x":
            ports.append(f"/dev/{name}")
    return ports


def detect_sound_cards() -> list:
    """Parse `aplay -l`; return [{'card','name','label','plughw','ptt_suggest','ptt_param_suggest'}]."""
    cards = []
    cp210x = detect_cp210x_ports()
    try:
        out = subprocess.run(["aplay", "-l"], capture_output=True, timeout=5
                             ).stdout.decode(errors="replace")
    except Exception:
        return cards
    for line in out.splitlines():
        m = re.match(r"card (\d+): (\S+) \[(.*?)\], device (\d+):", line)
        if not m:
            continue
        card_num, _, card_name, dev = m.group(1), m.group(2), m.group(3), m.group(4)
        low = card_name.lower()
        label = card_name
        for lbl, keys in CARD_SIGNATURES:
            if any(k in low for k in keys):
                label = lbl
                break
        # Disambiguate the shared TI codec: CP210x pair present => Yaesu
        if label == "Signalink" and len(cp210x) >= 2:
            label = "Yaesu USB radio"
        ptt_s, ptt_p = PTT_SUGGESTIONS.get(label, ("", ""))
        entry = {"card": int(card_num), "name": card_name,
                 "label": label, "plughw": f"plughw:{card_name},{dev}",
                 "ptt_suggest": ptt_s, "ptt_param_suggest": ptt_p}
        if not any(x["plughw"] == entry["plughw"] for x in cards):
            cards.append(entry)
    return cards


def detect_cm108_adevice() -> Optional[str]:
    """Find the ADEVICE for the CM108 sound card from `cm108` output.
    Matches the CM108 PTT HID on ANY /dev/hidraw* (not just hidraw0 - the
    hidraw number is not stable across reboots / USB re-enumeration), and
    PREFERS the name-based plughw:<name>,N token over the number-based
    plughw:<num>,N when both are present, because the card NUMBER shifts
    with enumeration order while the name is stable. Returns None if no
    CM108 mapping is found, so callers can fall back."""
    try:
        out = subprocess.run(["cm108"], capture_output=True, timeout=5
                             ).stdout.decode(errors="replace")
    except Exception:
        return None
    name_form = None
    number_form = None
    for line in out.splitlines():
        if "/dev/hidraw" not in line:
            continue
        for tok in line.split():
            if tok.startswith("plughw:"):
                body = tok.split(":", 1)[1]
                # plughw:1,0 -> number form; plughw:Device,0 -> name form
                if body[:1].isdigit():
                    number_form = number_form or tok
                else:
                    name_form = name_form or tok
    return name_form or number_form


def _call_with_ssid(call: str, ssid) -> str:
    """CALLSIGN or CALLSIGN-SSID. Never emits stray spaces or a bare dash."""
    call = (call or "").strip().upper()
    s = str(ssid).strip() if ssid not in (None, "") else ""
    return f"{call}-{s}" if s else call


def generate_direwolf_conf(station: dict, adevice: str = None) -> str:
    """Render direwolf.conf text from the station config section."""
    mycall = _call_with_ssid(station.get("callsign", ""), station.get("ssid", ""))
    alias  = _call_with_ssid(station.get("calias", "") or "CDIGI",
                             station.get("aliasssid", ""))
    if not adevice:
        # Prefer the ADEVICE of the sound card selected in the web UI,
        # matched against live detection; fall back to CM108 hidraw
        # mapping, then to the historical default.
        selected = (station.get("sound") or "").strip()
        if selected:
            for card in detect_sound_cards():
                if card["label"] == selected or card["name"] == selected:
                    adevice = card["plughw"]
                    break
        if not adevice:
            adevice = detect_cm108_adevice() or "plughw:1,0"

    ptt = (station.get("ptt") or "CM108").upper()
    ptt_param = (station.get("ptt_param") or "").strip()
    ptt_lines = []
    if ptt == "CM108":
        ptt_lines.append("PTT CM108")
    elif ptt == "VOX":
        ptt_lines.append("# PTT handled by VOX (Signalink) - no PTT line")
    elif ptt == "GPIO":
        pin = ptt_param if ptt_param.isdigit() else "25"
        ptt_lines.append(f"PTT GPIO {pin}")
    elif ptt in ("RTS", "DTR"):
        dev = ptt_param if ptt_param.startswith("/dev/") else "/dev/ttyUSB0"
        ptt_lines.append(f"PTT {dev} {ptt}")
    elif ptt == "RIG":
        ptt_lines.append("# PTT RIG requested but Hamlib is not compiled into this")
        ptt_lines.append("# Direwolf build. Recompile with hamlib to enable, then")
        ptt_lines.append("# replace this comment with: PTT RIG <model> <port>")
    elif ptt == "NONE":
        ptt_lines.append("# No PTT configured")

    lines = [
        "# Generated by KP4PRA TNC web interface - do not edit by hand.",
        "# Edit Station Information at http://<host>:8088/config and Apply.",
        f"ADEVICE  {adevice}",
        "CHANNEL 0",
        f"MYCALL {mycall}",
        *ptt_lines,
        f"CDIGIPEAT 0 0 {alias}",
        (f'CBEACON delay=1:00 every=59:00 INFO="{mycall} - BLUETOOTH TNC '
         f'Android/Iphone - {alias} alias for AX.25 by KP4PRA"'),
        "",
    ]
    return "\n".join(lines)


def write_direwolf_conf(text: str, path: str = DIREWOLF_CONF_PATH):
    """Atomic write. Returns (ok, message). Never remounts by itself -
    in production the conf should live on /rw via symlink."""
    target = os.path.realpath(path)   # follow the production symlink
    d = os.path.dirname(target)
    if not os.access(d, os.W_OK) and not (os.path.exists(target) and os.access(target, os.W_OK)):
        return False, (f"{target} is not writable. In production, symlink "
                       f"{path} -> /rw/kp4pra-tnc/direwolf.conf so Direwolf "
                       "settings can be applied without remounting root.")
    tmp = target + ".tmp"
    try:
        with open(tmp, "w") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
        return True, f"direwolf.conf written to {target}"
    except Exception as e:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        return False, f"Failed to write direwolf.conf: {e}"


def restart_direwolf():
    """Restart Direwolf (allowlisted in sudoers). Returns (ok, message)."""
    try:
        r = subprocess.run(["sudo", "/bin/systemctl", "restart", "direwolf.service"],
                           capture_output=True, timeout=20)
        if r.returncode == 0:
            return True, "Direwolf restarted"
        return False, f"Direwolf restart failed: {r.stderr.decode(errors='replace').strip()}"
    except Exception as e:
        return False, str(e)
