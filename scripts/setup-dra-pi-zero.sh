#!/bin/bash
# setup-dra-pi-zero.sh - Opt-in setup for Masters Communications DRA-Pi-Zero
# (I2S WM8731 codec, GPIO 12 relay PTT).
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

CARD="audioinjectorpi"
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
    echo "plughw:${CARD},0 and set PTT to GPIOD with parameter \"gpiochip0 12\"."
}

case "$MODE" in
    config) do_config ;;
    mixer)  do_mixer ;;
    full)   do_config; do_mixer ;;
esac
