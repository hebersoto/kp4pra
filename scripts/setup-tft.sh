#!/bin/bash
# setup-tft.sh - Adafruit Mini PiTFT 1.3" (240x240) status display for KP4PRA.
#
# Raspberry Pi and Orange Pi Zero 2W (Armbian). Installs the Python packages,
# GPIO/SPI permissions and the kp4pra-tnc-tft service, and enables SPI:
#   Raspberry Pi       dtparam=spi=on in config.txt (backed up once)
#   Orange Pi Zero 2W  user overlay kp4pra-tft-orangepi (SPI1 + spidev)
#
#   sudo bash scripts/setup-tft.sh          full setup (screen attached or not)
#   sudo bash scripts/setup-tft.sh --auto   used by install.sh: always installs
#        the service; enables SPI only when a Mini PiTFT is detected now
#        (button pull-up probe). Re-run without --auto after attaching one.
#
# The display mode (Auto/On/Off) is set in the web UI: Config -> TFT Display.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
APP_DIR="/opt/kp4pra-tnc"
SVC_USER="kp4pra-tnc"
MODE="full"
case "${1:-}" in
    --auto) MODE="auto" ;;
    "") ;;
    *) echo "Usage: $0 [--auto]" >&2; exit 2 ;;
esac
[ "$(id -u)" -eq 0 ] || exec sudo bash "$0" "$@"

PLAT_HELPER="$REPO_DIR/bin/kp4pra-dra-platform"
[ -x "$PLAT_HELPER" ] || PLAT_HELPER="$(command -v kp4pra-dra-platform || true)"
DRA_PLATFORM=unknown
[ -n "$PLAT_HELPER" ] && eval "$("$PLAT_HELPER")"
case "$DRA_PLATFORM" in
    rpi|opi-zero2w) ;;
    *) echo "TFT: board not supported (${DRA_MODEL:-unknown}) - Raspberry Pi or Orange Pi Zero 2W only."; exit 0 ;;
esac
echo "== Mini PiTFT setup for ${DRA_BOARD} =="

# Read-only root: remount rw for the install, restore on exit.
if ! ( : > /usr/local/.kp4pra-wtest ) 2>/dev/null; then
    command -v kp4pra-remount-rw >/dev/null || { echo "ERROR: root is read-only and kp4pra-remount-rw is missing" >&2; exit 1; }
    kp4pra-remount-rw
    trap 'kp4pra-remount-ro || echo "WARN: run sudo kp4pra-remount-ro"' EXIT
fi
rm -f /usr/local/.kp4pra-wtest

echo "-- [1/4] Packages"
PKGS="python3-pil python3-numpy python3-spidev python3-libgpiod python3-yaml fonts-dejavu-core"
[ "$DRA_PLATFORM" = "opi-zero2w" ] && PKGS="$PKGS device-tree-compiler"
# shellcheck disable=SC2086  # word-split the package list on purpose
apt-get install -y $PKGS >/dev/null && echo "  OK  : $PKGS"

echo "-- [2/4] Permissions"
groupadd -f gpio
usermod -aG gpio "$SVC_USER"
usermod -aG systemd-journal "$SVC_USER"            # reads Dire Wolf's packet log
if [ "$DRA_PLATFORM" = "rpi" ]; then
    getent group spi >/dev/null && usermod -aG spi "$SVC_USER"
else
    cat > /etc/udev/rules.d/60-kp4pra-gpio.rules <<'RULES'
SUBSYSTEM=="gpio", KERNEL=="gpiochip*", GROUP="gpio", MODE="0660"
SUBSYSTEM=="spidev", KERNEL=="spidev*", GROUP="gpio", MODE="0660"
RULES
    udevadm control --reload; udevadm trigger --subsystem-match=gpio --subsystem-match=spidev
fi
echo "  OK  : $SVC_USER in gpio/systemd-journal$([ "$DRA_PLATFORM" = rpi ] && echo /spi)"

DETECTED=1
if [ "$MODE" = "auto" ]; then
    if PYTHONPATH="$REPO_DIR/src" python3 -c 'import sys; from tft import hw; sys.exit(0 if hw.probe(hw.pins_for()) else 1)' 2>/dev/null; then
        echo "  Mini PiTFT detected"
    else
        DETECTED=0
        echo "  No Mini PiTFT detected - SPI left unchanged. Attach one later and run:"
        echo "      sudo bash scripts/setup-tft.sh"
    fi
fi

NEED_REBOOT=0
echo "-- [3/4] SPI"
if [ "$DETECTED" -eq 1 ]; then
    if [ "$DRA_PLATFORM" = "rpi" ]; then
        CFG=""
        for c in /boot/firmware/config.txt /boot/config.txt; do [ -f "$c" ] && { CFG=$c; break; }; done
        [ -n "$CFG" ] || { echo "ERROR: config.txt not found" >&2; exit 1; }
        if grep -qE '^[[:space:]]*dtparam=spi=on' "$CFG"; then
            echo "  OK  : SPI already enabled in $CFG"
        else
            [ -f "${CFG}.kp4pra-tft.bak" ] || cp "$CFG" "${CFG}.kp4pra-tft.bak"
            printf '\n# KP4PRA Mini PiTFT\ndtparam=spi=on\n' >> "$CFG"
            echo "  OK  : dtparam=spi=on added to $CFG (backup ${CFG}.kp4pra-tft.bak)"
            NEED_REBOOT=1
        fi
    else
        DTS="$REPO_DIR/platform/orangepi-zero2w/kp4pra-tft-orangepi.dts"
        [ -f "$DTS" ] || { echo "ERROR: run from the kp4pra-tnc repo ($DTS missing)" >&2; exit 1; }
        if grep -qE '^user_overlays=.*\bkp4pra-tft-orangepi\b' /boot/armbianEnv.txt && \
           [ -f /boot/overlay-user/kp4pra-tft-orangepi.dtbo ]; then
            echo "  OK  : SPI1 overlay already installed"
        else
            [ -f /boot/armbianEnv.txt.kp4pra.bak ] || cp /boot/armbianEnv.txt /boot/armbianEnv.txt.kp4pra.bak
            armbian-add-overlay "$DTS"
            NEED_REBOOT=1
        fi
    fi
else
    echo "  skipped (no display detected)"
fi

echo "-- [4/4] Service"
if [ -d "$APP_DIR/src" ]; then
    cp -r "$REPO_DIR/src/tft" "$APP_DIR/src/"
    [ -f "$REPO_DIR/src/common/dra_platform.py" ] && cp "$REPO_DIR/src/common/dra_platform.py" "$APP_DIR/src/common/"
    chown -R "$SVC_USER:$SVC_USER" "$APP_DIR/src/tft" 2>/dev/null || true
fi
install -m 755 "$REPO_DIR/bin/kp4pra-dra-platform" /usr/local/bin/
install -m 755 "$REPO_DIR/scripts/setup-tft.sh" /usr/local/bin/
cp "$REPO_DIR/systemd/kp4pra-tnc-tft.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable kp4pra-tnc-tft.service >/dev/null 2>&1
[ "$NEED_REBOOT" -eq 0 ] && systemctl restart kp4pra-tnc-tft.service
echo "  OK  : kp4pra-tnc-tft enabled"

echo
if [ "$NEED_REBOOT" -eq 1 ]; then
    echo "REBOOT REQUIRED to enable SPI: sudo reboot"
    echo "The display starts after the reboot (splash 'KP4PRA TNC' for 10 s)."
fi
echo "Display mode: web UI -> Config -> TFT Display (Auto/On/Off)."
