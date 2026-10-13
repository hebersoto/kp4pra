#!/bin/bash
# setup-dra-pi-zero.sh - Opt-in setup for Masters Communications DRA-Pi-Zero
# (I2S WM8731 codec, relay PTT on header pin 32).
#
# Supported boards (detected from the device tree, see bin/kp4pra-dra-platform):
#   Raspberry Pi        config.txt + audioinjector overlay (unchanged since 1.3.x)
#   Orange Pi Zero 2W   Armbian: KP4PRA overlay + patched WM8731 driver built
#                       with DKMS (one-time, from SSH, needs internet). See
#                       docs/DRA_PI_ZERO.md "Orange Pi Zero 2W".
#
# Modes:
#   --config-only : write config.txt (I2S overlay, onboard audio off, HDMI
#                   audio off), back up config.txt once, add gpio group.
#                   Does NOT touch the mixer or ADEVICE. Reboot required after.
#   --mixer-only  : apply the WM8731 mixer IF the codec is present; if the DRA
#                   is not detected, log and exit 0 (no damage to USB setups).
#   (no flag)     : full run - config, then mixer if the card is already up.
#                   Preserves the original CLI behavior (run, reboot, re-run).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

# Board detection. Falls back to the Raspberry Pi behavior when the helper is
# missing (older installs) or the board is unknown.
PLAT_HELPER="$REPO_DIR/bin/kp4pra-dra-platform"
[ -x "$PLAT_HELPER" ] || PLAT_HELPER="$(command -v kp4pra-dra-platform || true)"
DRA_PLATFORM=unknown
[ -n "$PLAT_HELPER" ] && eval "$("$PLAT_HELPER")"

CARD="audioinjectorpi"
[ "$DRA_PLATFORM" = "opi-zero2w" ] && CARD="$DRA_CARD"
MARKER="# KP4PRA DRA-Pi-Zero"
RUN_USER="${SUDO_USER:-kp4pra}"

MODE="full"
case "${1:-}" in
    --config-only) MODE="config" ;;
    --mixer-only)  MODE="mixer" ;;
    "")            MODE="full" ;;
    *) echo "Usage: $0 [--config-only|--mixer-only]" >&2; exit 2 ;;
esac

# --- locate config.txt ---
find_cfg() {
    if   [ -f /boot/firmware/config.txt ]; then echo /boot/firmware/config.txt
    elif [ -f /boot/config.txt ];          then echo /boot/config.txt
    else return 1; fi
}

do_config() {
    local CFG
    CFG="$(find_cfg)" || { echo "ERROR: config.txt not found in /boot/firmware or /boot" >&2; exit 1; }
    echo "Using ${CFG}"

    # One-time backup so a revert is always possible.
    if [ ! -f "${CFG}.kp4pra.bak" ]; then
        sudo cp "$CFG" "${CFG}.kp4pra.bak"
        echo "Backed up ${CFG} -> ${CFG}.kp4pra.bak"
    fi

    NEED_REBOOT=0

    # Disable onboard analog audio if currently enabled.
    if grep -qE '^[[:space:]]*dtparam=audio=on' "$CFG"; then
        sudo sed -i 's/^[[:space:]]*dtparam=audio=on/#&  # disabled by setup-dra-pi-zero/' "$CFG"
        NEED_REBOOT=1
        echo "Commented out dtparam=audio=on"
    fi

    # Disable HDMI audio. The vc4-kms-v3d overlay carries HDMI audio; add the
    # ,noaudio parameter IN PLACE on the existing line (a second vc4 line would
    # conflict). Only touch a line that doesn't already have noaudio.
    if grep -qE '^[[:space:]]*dtoverlay=vc4-kms-v3d([[:space:],]|$)' "$CFG"; then
        if ! grep -qE '^[[:space:]]*dtoverlay=vc4-kms-v3d[^#]*noaudio' "$CFG"; then
            sudo sed -i -E 's/^([[:space:]]*dtoverlay=vc4-kms-v3d)([[:space:],]*)/\1,noaudio\2/' "$CFG"
            NEED_REBOOT=1
            echo "Added ,noaudio to vc4-kms-v3d (HDMI audio off)"
        fi
    elif grep -qE '^[[:space:]]*dtoverlay=vc4-fkms-v3d' "$CFG"; then
        # Older firmware KMS overlay - same treatment.
        if ! grep -qE 'vc4-fkms-v3d[^#]*noaudio' "$CFG"; then
            sudo sed -i -E 's/^([[:space:]]*dtoverlay=vc4-fkms-v3d)([[:space:],]*)/\1,noaudio\2/' "$CFG"
            NEED_REBOOT=1
            echo "Added ,noaudio to vc4-fkms-v3d (HDMI audio off)"
        fi
    fi
    # If no KMS overlay line exists at all, we do NOT add one - the graphics
    # stack config is out of scope and adding a KMS overlay blindly is risky.

    # Append the DRA I2S lines if missing.
    add_line() {
        if ! grep -qxF "$1" "$CFG"; then
            if ! grep -qxF "$MARKER" "$CFG"; then
                printf '\n%s\n' "$MARKER" | sudo tee -a "$CFG" >/dev/null
            fi
            echo "$1" | sudo tee -a "$CFG" >/dev/null
            NEED_REBOOT=1
            echo "Added: $1"
        fi
    }
    add_line "dtparam=i2c_arm=on"
    add_line "dtparam=i2s=on"
    add_line "dtparam=audio=off"
    add_line "dtoverlay=audioinjector-wm8731-audio"

    # GPIO group for PTT.
    if ! id -nG "$RUN_USER" | grep -qw gpio; then
        sudo usermod -aG gpio "$RUN_USER"
        echo "Added ${RUN_USER} to gpio group (restart services to apply)"
    fi

    if [ "$NEED_REBOOT" -eq 1 ]; then
        echo "REBOOT REQUIRED: config.txt changed. The mixer will be applied"
        echo "automatically on next boot (kp4pra-dra-mixer.service)."
    else
        echo "config.txt already up to date."
    fi
}

do_mixer() {
    # No-op safely if the codec isn't present (board absent / not seated).
    if ! arecord -l 2>/dev/null | grep -q "$CARD"; then
        echo "DRA-Pi-Zero ('${CARD}') not detected - skipping mixer setup."
        echo "(If you just enabled the overlay, a reboot is required first.)"
        return 0
    fi

    mset() { local ctl="$1"; shift; amixer -c "$CARD" sset "$ctl" "$@" >/dev/null 2>&1; }
    mset_req() {
        local ctl="$1"
        if mset "$@"; then echo "  OK  : $*"; else
            echo "ERROR: amixer sset failed for required control: $*" >&2
            echo "Run: amixer -c $CARD scontents   and report the '$ctl' block." >&2
            exit 1
        fi
    }
    mset_opt() { if mset "$@"; then echo "  OK  : $*"; else echo "  WARN: skipped: $*"; fi; }

    echo "Configuring WM8731 mixer on card '${CARD}'..."
    mset_req 'Input Mux' 'Mic'
    mset_req 'Mic' cap
    if mset 'Mic Boost' off || mset 'Mic Boost' 0 || mset 'Mic Boost' 0%; then
        echo "  OK  : Mic Boost -> off/0"
    else
        echo "  WARN: could not set Mic Boost (leaving default)"
    fi
    mset_opt 'Line' nocap
    mset_opt 'ADC High Pass Filter' on
    mset_opt 'Sidetone' 0%
    mset_req 'Output Mixer HiFi' on
    mset_opt 'Output Mixer Line Bypass' off
    mset_opt 'Output Mixer Mic Sidetone' off
    mset_req 'Master' 100 unmute      # tested TX level (-21dB)
    mset_opt 'Capture' 31             # tested RX level (max); RX gain set by R12 pot
    sudo alsactl store
    echo "Mixer configured and stored."

    if command -v direwolf >/dev/null 2>&1; then
        if ! timeout 5 direwolf 2>&1 </dev/null | grep -qi libgpiod; then
            echo "WARNING: installed direwolf lacks libgpiod support."
            echo "         GPIOD PTT (used by the DRA-Pi-Zero) will fail."
            echo "         Rebuild direwolf with libgpiod-dev present."
            echo "         See docs/DRA_PI_ZERO.md."
        fi
    fi
    echo "DONE. In the web UI Config page: Detect sound cards, select"
    if [ "$DRA_PLATFORM" = "opi-zero2w" ]; then
        echo "\"DRA-Pi-Zero (I2S)\" (plughw:${CARD},0). PTT pre-fills to GPIOD"
        echo "\"${DRA_PTT_CHIP} ${DRA_PTT_LINE}\" and the green LED line is added automatically."
        echo "Preview, then Apply."
    else
        echo "plughw:${CARD},0 and set PTT to GPIOD with parameter \"gpiochip0 12\"."
    fi
}

# ════════════════════════════════════════════════════════════════════════════
# Orange Pi Zero 2W (Armbian)
# ════════════════════════════════════════════════════════════════════════════
OPI_DIR="$REPO_DIR/platform/orangepi-zero2w"
OPI_DTS_NAME="kp4pra-dra-orangepi"
ENV_TXT="/boot/armbianEnv.txt"
DKMS_NAME="kp4pra-wm8731"

opi_installed() {  # driver built for this kernel + overlay enabled
    local k; k="$(uname -r)"
    [ -f "/boot/overlay-user/${OPI_DTS_NAME}.dtbo" ] || return 1
    grep -qE "^user_overlays=.*\b${OPI_DTS_NAME}\b" "$ENV_TXT" 2>/dev/null || return 1
    find "/lib/modules/$k/updates" -name 'snd-soc-wm8731.ko*' -path '*dkms*' 2>/dev/null | grep -q .
}

opi_status() {  # --config-only on the Orange Pi (web button): report, change nothing
    if opi_installed; then
        if arecord -l 2>/dev/null | grep -q "$CARD"; then
            echo "INFO: DRA-Pi-Zero is set up and detected on this Orange Pi Zero 2W" \
                 "(card $CARD). Select \"DRA-Pi-Zero (I2S)\" under Sound Card and Apply."
        else
            echo "INFO: DRA-Pi-Zero driver and overlay are installed but the card is not" \
                 "detected. Reboot; if it is still missing, check that the DRA board is seated."
        fi
    else
        echo "INFO: On the Orange Pi Zero 2W the one-time DRA setup builds the codec driver" \
             "and must run from SSH (needs internet, ~5 minutes, then a reboot):" \
             "sudo bash ~/kp4pra-tnc/scripts/setup-dra-pi-zero.sh - see docs/DRA_PI_ZERO.md."
    fi
}

opi_die() { echo "ERROR: $*" >&2; exit 1; }

opi_setup() {
    [ "$(id -u)" -eq 0 ] || exec sudo bash "$0"
    # Never stop silently: report where an unexpected failure happened.
    set -E
    trap 'echo "ERROR: DRA setup stopped unexpectedly at line $LINENO (command: $BASH_COMMAND)." >&2; echo "Please report this output." >&2' ERR
    [ -f "$ENV_TXT" ] || opi_die "$ENV_TXT not found. The Orange Pi Zero 2W DRA support needs Armbian."
    [ -f "$OPI_DIR/${OPI_DTS_NAME}.dts" ] || opi_die "run this from the kp4pra-tnc repo checkout (platform/ files missing)."
    command -v armbian-add-overlay >/dev/null || opi_die "armbian-add-overlay not found (Armbian tooling missing)."
    local K VER NEED_REBOOT=0
    K="$(uname -r)"
    VER="$(cat "$REPO_DIR/VERSION" 2>/dev/null || echo 0.0.0)"
    echo "== DRA-Pi-Zero setup for ${DRA_BOARD} (kernel $K, KP4PRA $VER) =="

    # Read-only root: remount rw for the install, restore on exit.
    if ! ( : > /usr/src/.kp4pra-wtest ) 2>/dev/null; then
        command -v kp4pra-remount-rw >/dev/null || opi_die "root filesystem is read-only and kp4pra-remount-rw is missing."
        echo "Root filesystem is read-only - remounting rw for the setup"
        kp4pra-remount-rw
        trap 'echo "Restoring read-only root"; kp4pra-remount-ro || echo "WARN: run sudo kp4pra-remount-ro"' EXIT
    fi
    rm -f /usr/src/.kp4pra-wtest

    # 0. A kernel update without a reboot leaves the RUNNING kernel's files
    #    deleted from disk (modules, /boot/vmlinuz-$K). Nothing can be built
    #    or loaded for it, so stop with a clear instruction instead.
    if [ ! -d "/lib/modules/$K/kernel" ]; then
        local newk
        newk="$(find /lib/modules -mindepth 1 -maxdepth 1 -type d -printf '%f\n' 2>/dev/null | sort -V | tail -1)"
        echo "ERROR: the kernel was updated but the board has not been rebooted yet." >&2
        echo "  running kernel : $K (its files are no longer on disk)" >&2
        echo "  installed kernel: ${newk:-unknown}" >&2
        echo "  Reboot (sudo reboot), then run this script again." >&2
        exit 1
    fi

    # 1. Kernel headers matching the running Armbian kernel.
    echo "-- [1/6] Kernel headers"
    if [ -e "/lib/modules/$K/build/Makefile" ]; then
        echo "  OK  : headers for $K present"
    else
        local IMG HDR IVER
        # Which installed linux-image package is the running kernel?
        # 1) the owner of /boot/vmlinuz-$K, 2) any installed Armbian sunxi64 image.
        IMG="$( { dpkg-query -S "/boot/vmlinuz-$K" 2>/dev/null || true; } | sed -n '1s/:.*//p')"
        if [ -z "$IMG" ]; then
            IMG="$(dpkg-query -W -f '${Package} ${db:Status-Abbrev}\n' 'linux-image-*' 2>/dev/null \
                   | awk '$2 ~ /^ii/ && $1 ~ /sunxi64/ {print $1; exit}' || true)"
        fi
        [ -n "$IMG" ] || opi_die "cannot find the installed Armbian kernel package (linux-image-*-sunxi64) for $K."
        HDR="${IMG/linux-image/linux-headers}"
        IVER="$(dpkg-query -W -f '${Version}' "$IMG" 2>/dev/null || true)"
        [ -n "$IVER" ] || opi_die "cannot read the version of $IMG."
        echo "  installing $HDR=$IVER"
        apt-get update -qq || true
        if ! apt-get install -y "$HDR=$IVER"; then
            cat >&2 <<EOF
ERROR: kernel headers $HDR=$IVER are not available from your apt sources.
Armbian's beta repository (beta.armbian.com) often has no headers for its
trunk kernels. Switch to the stable repository (apt.armbian.com), install the
stable kernel + headers, reboot, and run this script again. Step-by-step:
docs/DRA_PI_ZERO.md, section "Kernel headers: beta vs stable repository".
EOF
            exit 1
        fi
    fi

    # 2. Build tools and runtime tools.
    echo "-- [2/6] Packages: dkms, compiler, device-tree compiler, i2c-tools, gpiod"
    apt-get install -y dkms gcc make device-tree-compiler i2c-tools gpiod curl alsa-utils python3

    # 3. Patched WM8731 driver via DKMS (downloads the matching kernel source).
    echo "-- [3/6] WM8731 driver (DKMS package $DKMS_NAME/$VER)"
    # Remove hand-built copies from the 2026-10-12 bring-up (updates/, not updates/dkms/).
    find /lib/modules/*/updates -maxdepth 1 -name 'snd-soc-wm8731*.ko*' -print -delete 2>/dev/null || true
    local old
    for old in $(dkms status "$DKMS_NAME" 2>/dev/null | sed -nE "s#^${DKMS_NAME}/([^,: ]+).*#\1#p" | sort -u); do
        echo "  removing previous $DKMS_NAME/$old"
        dkms remove "$DKMS_NAME/$old" --all >/dev/null 2>&1 || true
    done
    rm -rf "/usr/src/${DKMS_NAME}-${VER}"
    cp -r "$OPI_DIR/wm8731-dkms" "/usr/src/${DKMS_NAME}-${VER}"
    sed -i "s/@VERSION@/${VER}/" "/usr/src/${DKMS_NAME}-${VER}/dkms.conf"
    chmod 755 "/usr/src/${DKMS_NAME}-${VER}/prepare.sh"
    dkms add "$DKMS_NAME/$VER"
    if ! dkms install "$DKMS_NAME/$VER" -k "$K"; then
        echo "ERROR: driver build failed. Full log:" >&2
        tail -30 "/var/lib/dkms/$DKMS_NAME/$VER/build/make.log" >&2 2>/dev/null || true
        exit 1
    fi
    depmod -a "$K"
    NEED_REBOOT=1   # a fresh module must be loaded at boot with the overlay

    # 4. I2C on header pins 3/5 + the DRA overlay.
    echo "-- [4/6] Device-tree overlay"
    [ -f "${ENV_TXT}.kp4pra.bak" ] || { cp "$ENV_TXT" "${ENV_TXT}.kp4pra.bak"; echo "  backed up $ENV_TXT"; }
    if grep -qE '^overlays=' "$ENV_TXT"; then
        grep -qE '^overlays=.*\bi2c1-pi\b' "$ENV_TXT" || sed -i -E 's/^(overlays=.*)$/\1 i2c1-pi/' "$ENV_TXT"
    else
        echo "overlays=i2c1-pi" >> "$ENV_TXT"
    fi
    echo "  OK  : overlays includes i2c1-pi"
    armbian-add-overlay "$OPI_DIR/${OPI_DTS_NAME}.dts"
    echo "  OK  : user overlay ${OPI_DTS_NAME} installed"

    # 5. GPIO access (PTT, LEDs) + helpers + Dire Wolf pre-start I2C check.
    echo "-- [5/6] GPIO permissions and helpers"
    groupadd -f gpio
    printf '%s\n' 'SUBSYSTEM=="gpio", KERNEL=="gpiochip*", GROUP="gpio", MODE="0660"' \
        'SUBSYSTEM=="spidev", KERNEL=="spidev*", GROUP="gpio", MODE="0660"' > /etc/udev/rules.d/60-kp4pra-gpio.rules
    udevadm control --reload; udevadm trigger --subsystem-match=gpio --subsystem-match=spidev
    local u
    for u in kp4pra kp4pra-tnc; do
        id "$u" >/dev/null 2>&1 && usermod -aG gpio "$u" && echo "  OK  : $u in gpio group"
    done
    install -m 755 "$REPO_DIR/bin/kp4pra-dra-platform"  /usr/local/bin/
    install -m 755 "$REPO_DIR/bin/kp4pra-dra-i2c-check" /usr/local/bin/
    install -m 755 "$REPO_DIR/bin/kp4pra-bt-led"        /usr/local/bin/
    install -m 755 "$REPO_DIR/bin/kp4pra-adevice-fix"   /usr/local/bin/
    install -m 755 "$REPO_DIR/scripts/setup-dra-pi-zero.sh" /usr/local/bin/
    mkdir -p /etc/systemd/system/direwolf.service.d
    cat > /etc/systemd/system/direwolf.service.d/kp4pra-dra-i2c.conf <<'EOF'
# KP4PRA: check (and if needed recover) the DRA codec I2C bus before Dire Wolf
# opens the card. Root ("+"), never blocks the start ("-").
[Service]
ExecStartPre=-+/usr/local/bin/kp4pra-dra-i2c-check
EOF
    systemctl daemon-reload
    systemctl restart kp4pra-tnc-bt-led 2>/dev/null || true
    echo "  OK  : helpers installed, Dire Wolf pre-start I2C check enabled"

    # 6. Mixer now if the card is already up, else on next boot.
    echo "-- [6/6] Mixer"
    if arecord -l 2>/dev/null | grep -q "$CARD"; then
        do_mixer
    else
        echo "  card $CARD not loaded yet - kp4pra-dra-mixer.service applies the mixer on next boot."
    fi

    echo
    echo "== DRA-Pi-Zero setup finished for ${DRA_BOARD} =="
    if [ "$NEED_REBOOT" -eq 1 ]; then
        echo "REBOOT REQUIRED: sudo reboot"
        echo "After the reboot, in the web UI Config page: Detect sound cards, select"
        echo "\"DRA-Pi-Zero (I2S)\" - PTT pre-fills to GPIOD \"${DRA_PTT_CHIP} ${DRA_PTT_LINE}\" - then Preview and Apply."
    fi
}

if [ "$DRA_PLATFORM" = "opi-zero2w" ]; then
    case "$MODE" in
        config) opi_status ;;
        mixer)  do_mixer ;;
        full)   opi_setup ;;
    esac
    exit 0
fi

case "$MODE" in
    config) do_config ;;
    mixer)  do_mixer ;;
    full)   do_config; do_mixer ;;
esac
