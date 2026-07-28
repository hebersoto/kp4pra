#!/bin/bash
# setup-dra-pi-zero.sh - Opt-in setup for Masters Communications DRA-Pi-Zero
# (I2S WM8731 codec, GPIO 12 relay PTT). Idempotent: run once, reboot if told
# to, run again to finish mixer setup. Not part of stage-2 install by design.
set -euo pipefail

CARD="audioinjectorpi"
MARKER="# KP4PRA DRA-Pi-Zero"
RUN_USER="${SUDO_USER:-kp4pra}"

# --- locate config.txt ---
if   [ -f /boot/firmware/config.txt ]; then CFG=/boot/firmware/config.txt
elif [ -f /boot/config.txt ];          then CFG=/boot/config.txt
else echo "ERROR: config.txt not found in /boot/firmware or /boot" >&2; exit 1
fi
echo "Using ${CFG}"

NEED_REBOOT=0

# --- disable onboard analog audio if enabled ---
if grep -qE '^[[:space:]]*dtparam=audio=on' "$CFG"; then
    sudo sed -i 's/^[[:space:]]*dtparam=audio=on/#&  # disabled by setup-dra-pi-zero/' "$CFG"
    NEED_REBOOT=1
    echo "Commented out dtparam=audio=on"
fi

# --- append required lines if missing ---
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

# --- GPIO group for PTT (service runs as kp4pra) ---
if ! id -nG "$RUN_USER" | grep -qw gpio; then
    sudo usermod -aG gpio "$RUN_USER"
    echo "Added ${RUN_USER} to gpio group (re-login/restart service to apply)"
fi

# --- stop here if the codec is not up yet ---
if ! arecord -l 2>/dev/null | grep -q "$CARD"; then
    if [ "$NEED_REBOOT" -eq 1 ]; then
        echo "REBOOT REQUIRED: overlay added. Reboot, then run this script again."
    else
        echo "ERROR: overlay present in ${CFG} but card '${CARD}' not detected." >&2
        echo "Check board seating on the 40-pin header, then reboot." >&2
        exit 1
    fi
    exit 0
fi

# --- WM8731 mixer: DRA-Pi-Zero feeds radio RX audio into the MIC input ---
# Control names/types vary slightly across kernel versions (e.g. 'Mic Boost'
# is a switch on some, a 0..1 volume on 6.x). mset reports the exact control
# on failure instead of dying silently; mset_req aborts, mset_opt warns.
mset() {  # args: control value...
    local ctl="$1"; shift
    amixer -c "$CARD" sset "$ctl" "$@" >/dev/null 2>&1
}
mset_req() {
    local ctl="$1"
    if mset "$@"; then echo "  OK  : $*"; else
        echo "ERROR: amixer sset failed for required control: $*" >&2
        echo "Run: amixer -c $CARD scontents   and report the '$ctl' block." >&2
        exit 1
    fi
}
mset_opt() {
    if mset "$@"; then echo "  OK  : $*"; else echo "  WARN: skipped (not settable here): $*"; fi
}

echo "Configuring WM8731 mixer on card '${CARD}'..."
mset_req 'Input Mux' 'Mic'
mset_req 'Mic' cap
# Mic Boost: try switch form, then volume form (kernel-dependent)
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
mset_req 'Master' 100 unmute      # tested TX level (-21dB); 37 was wrong scale
mset_opt 'Capture' 31             # tested RX level (max); RX gain set by R12 pot
sudo alsactl store
echo "Mixer configured and stored."

# GPIOD PTT requires direwolf built with libgpiod. A binary built without
# it will crash-loop on "PTT GPIOD". Warn loudly rather than fail silently.
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
if [ "$NEED_REBOOT" -eq 1 ]; then
    echo "NOTE: a reboot is still pending for config.txt changes."
fi
