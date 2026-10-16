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


# ---------------------------------------------------------------------------
# Setup state (shown on the Config page when the DRA card is NOT detected).
# Read-only file checks, no root needed. `root` and `boot_time` exist for tests.
SETUP_CMD = "cd ~/kp4pra-tnc && sudo bash scripts/setup-dra-pi-zero.sh"
OPI_DTBO = "boot/overlay-user/kp4pra-dra-orangepi.dtbo"
OPI_ENV = "boot/armbianEnv.txt"
RESUME_MARK = "rw/kp4pra-tnc/.dra-setup-resume"   # setup continues after a reboot


def _boot_time() -> float:
    try:
        with open("/proc/uptime") as f:
            import time
            return time.time() - float(f.read().split()[0])
    except (OSError, ValueError):
        return 0.0


def _read(path: str) -> str:
    try:
        with open(path, errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def setup_state(plat: Optional[str], kernel: Optional[str] = None,
                root: str = "/", boot_time: Optional[float] = None):
    """Why is the DRA card missing, and what should the user do?

    Returns (state, hint). state is one of:
      not_set_up      no sign of a DRA setup (USB-only boards land here)
      in_progress     the setup switched kernels and finishes by itself after
                      the reboot (kp4pra-dra-setup-resume.service)
      kernel_pending  setup started, but the running kernel was replaced
                      (kernel switch / update without reboot) -> reboot, run again
      incomplete      setup started but driver or overlay missing -> run again
      reboot_needed   setup finished after the last boot -> reboot
      no_card         everything installed and booted, card still missing
                      -> check the board is seated
    """
    import glob
    j = lambda p: os.path.join(root, p)
    k = kernel or os.uname().release
    bt = _boot_time() if boot_time is None else boot_time

    if plat == "opi-zero2w":
        dtbo = j(OPI_DTBO)
        started = (os.path.isdir(j("var/lib/dkms/kp4pra-wm8731"))
                   or os.path.isdir(j("var/backups/kp4pra")) or os.path.exists(dtbo))
        if os.path.exists(j(RESUME_MARK)):
            return ("in_progress", "DRA setup is finishing automatically: the board reboots, "
                    "builds the codec driver (about 5 minutes) and reboots once more. "
                    "Nothing to do - progress in /rw/kp4pra-tnc/logs/dra-setup.log.")
        if not started:
            return ("not_set_up", "Orange Pi Zero 2W: if a DRA-Pi-Zero is installed, run the "
                    f"one-time setup from SSH: {SETUP_CMD}")
        if not os.path.isdir(j(f"lib/modules/{k}/kernel")):
            return ("kernel_pending", "DRA setup not finished: the kernel was changed or updated "
                    f"and the board has not been rebooted. Reboot, then run again from SSH: {SETUP_CMD}")
        built = glob.glob(j(f"lib/modules/{k}/updates/dkms/snd-soc-wm8731*.ko*"))
        overlay = os.path.exists(dtbo) and "kp4pra-dra-orangepi" in _read(j(OPI_ENV))
        if not built or not overlay:
            missing = " and ".join(x for x, ok in (("codec driver", built), ("DRA overlay", overlay)) if not ok)
            return ("incomplete", f"DRA setup not finished ({missing} missing for kernel {k}). "
                    f"Run again from SSH: {SETUP_CMD} - it may ask for a reboot and one more run.")
        newest = max(os.path.getmtime(p) for p in built + [dtbo])
        if bt and newest > bt:
            return ("reboot_needed", "DRA setup finished - reboot to load the DRA-Pi-Zero.")
        return ("no_card", "Driver and overlay are installed but the DRA card did not appear. "
                "Check the DRA-Pi-Zero is seated on the header, then reboot; "
                "details: dmesg | grep KP4PRA (docs/DRA_PI_ZERO.md).")

    # Raspberry Pi (or unknown): config.txt overlay line
    for cfg in (j("boot/firmware/config.txt"), j("boot/config.txt")):
        txt = _read(cfg)
        if txt:
            if "dtoverlay=audioinjector-wm8731-audio" not in txt:
                break
            if bt and os.path.getmtime(cfg) > bt:
                return ("reboot_needed", "DRA overlay written to config.txt - reboot to load the DRA-Pi-Zero.")
            return ("no_card", "The DRA overlay is set in config.txt but the card did not appear. "
                    "Check the DRA-Pi-Zero is seated on the header, then reboot.")
    return ("not_set_up", "No DRA-Pi-Zero detected - USB or no I²S board. "
            "Run setup only if a DRA-Pi-Zero is installed.")
