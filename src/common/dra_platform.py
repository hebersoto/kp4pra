"""
KP4PRA TNC - DRA-Pi-Zero board/platform table.

The DRA-Pi-Zero (Masters Communications, WM8731 I2S codec) plugs into the
40-pin header. The header is pin-compatible across boards, but the Linux
names behind it are not: the ALSA card ID and the gpiochip/line numbers for
PTT, the carrier-detect LED and the Bluetooth LED differ per SoC.

This module is the single Python source of truth for those values.
bin/kp4pra-dra-platform is the shell twin (tests/test_dra_platform.py keeps
the two in sync).

    Header pin  Function             Raspberry Pi        Orange Pi Zero 2W
    29          Blue BT LED          gpiochip0 5         gpiochip1 256 (PI0)
    32          PTT relay + red LED  gpiochip0 12        gpiochip1 267 (PI11)
    36          Green DCD LED        gpiochip0 16        gpiochip1 76  (PC12)
    I2S card    ALSA card ID         audioinjectorpi     DRAPiZero
"""

import os
from typing import Optional

MODEL_PATH = "/proc/device-tree/model"

PLATFORMS = {
    "rpi": {
        "name": "Raspberry Pi",
        "card": "audioinjectorpi",
        "ptt": ("gpiochip0", "12"),
        "dcd": ("gpiochip0", "16"),
        "led": ("gpiochip0", "5"),
    },
    "opi-zero2w": {
        "name": "Orange Pi Zero 2W",
        "card": "DRAPiZero",
        "ptt": ("gpiochip1", "267"),
        "dcd": ("gpiochip1", "76"),
        "led": ("gpiochip1", "256"),
    },
}

# Every ALSA card ID the DRA can appear as, on any supported board.
DRA_CARD_IDS = tuple(p["card"] for p in PLATFORMS.values())


def platform_from_model(model: str) -> Optional[str]:
    """Map a device-tree model string to a platform key, or None."""
    m = (model or "").lower().replace(" ", "")
    if "raspberrypi" in m:
        return "rpi"
    if "orangepizero2w" in m:
        return "opi-zero2w"
    return None


def detect_platform(model_path: str = MODEL_PATH) -> Optional[str]:
    try:
        with open(model_path, "rb") as f:
            model = f.read().replace(b"\0", b"").decode(errors="replace")
    except OSError:
        return None
    return platform_from_model(model)


def platform_info(platform: Optional[str] = None) -> dict:
    """Values for the given (or detected) platform. Unknown boards fall back
    to the Raspberry Pi table, which is the historical behavior."""
    key = platform if platform in PLATFORMS else detect_platform()
    return PLATFORMS.get(key or "rpi", PLATFORMS["rpi"])


def is_dra_adevice(adevice: str) -> bool:
    """True if an ADEVICE string (plughw:<id>,N) points at the DRA I2S card.
    'audioinjector' keeps matching older Pi configs written as a prefix."""
    low = (adevice or "").lower()
    return "audioinjector" in low or any(c.lower() in low for c in DRA_CARD_IDS)


def dra_ptt_param(platform: Optional[str] = None) -> str:
    chip, line = platform_info(platform)["ptt"]
    return f"{chip} {line}"


def dra_dcd_line(platform: Optional[str] = None) -> str:
    chip, line = platform_info(platform)["dcd"]
    return f"DCD GPIOD {chip} {line}"


def foreign_dra_ptt_params(platform: Optional[str] = None) -> set:
    """DRA PTT params that belong to OTHER boards. A config copied from a Pi
    (or pre-filled by an older web UI) carries "gpiochip0 12"; on an Orange Pi
    that line does not exist and Dire Wolf exits. Callers remap these."""
    mine = dra_ptt_param(platform)
    return {f"{c} {l}" for c, l in (p["ptt"] for p in PLATFORMS.values())} - {mine}
