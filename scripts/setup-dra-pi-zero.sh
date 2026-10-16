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
#
# Orange Pi Zero 2W only (Raspberry Pi unchanged):
#   The full run reboots by itself when it is done (10 s countdown, Ctrl-C
#   cancels). If it first has to switch to Armbian's stable kernel (rolling
#   images have no kernel headers), it schedules kp4pra-dra-setup-resume.service,
#   reboots, finishes the setup automatically after the reboot and reboots once
#   more - one command, no second run by hand.
#   --no-reboot   : do not reboot automatically (print the instructions instead)
#   --resume      : used by kp4pra-dra-setup-resume.service (runs once)
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

ORIG_ARGS=("$@")
MODE="full"
NO_REBOOT=0
RESUME=0
for arg in "$@"; do
    case "$arg" in
        --config-only) MODE="config" ;;
        --mixer-only)  MODE="mixer" ;;
        --no-reboot)   NO_REBOOT=1 ;;
        --resume)      RESUME=1 ;;
        *) echo "Usage: $0 [--config-only|--mixer-only|--no-reboot]" >&2; exit 2 ;;
    esac
done

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

SETUP_LOG_DIR="/rw/kp4pra-tnc/logs"
SETUP_LOG="$SETUP_LOG_DIR/dra-setup.log"
# One line per boot-time mixer run in the setup log (for remote diagnosis).
mixlog() { [ "$MODE" = mixer ] && { mkdir -p "$SETUP_LOG_DIR" && echo "$(date -Is) boot mixer: $*" >> "$SETUP_LOG"; } 2>/dev/null || true; }

# Is the DRA overlay set up on this board (so the card SHOULD appear)?
dra_expected() {
    if [ "$DRA_PLATFORM" = "opi-zero2w" ]; then
        [ -f /boot/overlay-user/kp4pra-dra-orangepi.dtbo ]
    else
        local c; c="$(find_cfg 2>/dev/null)" || return 1
        grep -qE '^[[:space:]]*dtoverlay=audioinjector-wm8731-audio' "$c"
    fi
}

do_mixer() {
    # At boot (kp4pra-dra-mixer.service) the codec can register a few seconds
    # after sound.target - on the Orange Pi the I2S card often does. When the
    # DRA overlay is set up, wait for the card (up to 30 s) instead of giving
    # up on the first look; otherwise the mixer stays at its defaults (Mic not
    # captured, output off) and the DRA has no RX/TX audio until the setup is
    # run again by hand.
    if [ "$MODE" = mixer ] && dra_expected; then
        local i
        for i in $(seq 1 30); do
            arecord -l 2>/dev/null | grep -q "$CARD" && break
            sleep 1
        done
        if arecord -l 2>/dev/null | grep -q "$CARD" && [ "$i" -gt 1 ]; then
            echo "DRA card '$CARD' appeared after ${i} s"
        fi
    fi
    # No-op safely if the codec isn't present (board absent / not seated).
    if ! arecord -l 2>/dev/null | grep -q "$CARD"; then
        echo "DRA-Pi-Zero ('${CARD}') not detected - skipping mixer setup."
        echo "(If you just enabled the overlay, a reboot is required first.)"
        if dra_expected; then mixlog "card $CARD NOT detected after 30 s - mixer not applied"; fi
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
    # Saving can fail at boot on a read-only root; the boot service re-applies
    # the mixer every boot, so that is not an error.
    if sudo alsactl store 2>/dev/null; then
        echo "Mixer configured and stored."
    else
        echo "Mixer configured (not saved: read-only root - re-applied at every boot)."
    fi
    mixlog "mixer applied on card $CARD"

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

# --- Armbian kernel/headers helpers (any Armbian image) ---------------------
# Versions of a package available from the apt sources, one per line.
opi_versions() {
    apt-cache madison "$1" 2>/dev/null | awk -F'|' '{gsub(/[[:space:]]/, "", $2); print $2}' | LC_ALL=C sort -u || true
}
opi_has_version() { opi_versions "$1" | grep -qxF "$2"; }

# Newest version available for ALL of image, dtb and headers; stable releases
# preferred over rolling "trunk" builds.
opi_pick_kernel() {
    local common pick
    common="$(LC_ALL=C comm -12 <(opi_versions "$1") <(opi_versions "$2") \
              | LC_ALL=C comm -12 - <(opi_versions "$3") || true)"
    pick="$(printf '%s\n' "$common" | grep -v -e trunk -e '^$' | sort -V | tail -1 || true)"
    [ -n "$pick" ] || pick="$(printf '%s\n' "$common" | grep -v '^$' | sort -V | tail -1 || true)"
    printf '%s\n' "$pick"
}

# Point Armbian's apt source at the stable repository (apt.armbian.com).
# Originals are backed up to /var/backups/kp4pra/. If no Armbian source is
# configured at all, add the stable one.
opi_use_stable_repo() {
    local f code
    mkdir -p /var/backups/kp4pra
    for f in $(grep -rls 'beta\.armbian\.com' /etc/apt/sources.list /etc/apt/sources.list.d/ 2>/dev/null || true); do
        [ -f "/var/backups/kp4pra/$(basename "$f")" ] || cp "$f" "/var/backups/kp4pra/$(basename "$f")"
        sed -i 's#beta\.armbian\.com#apt.armbian.com#g' "$f"
        echo "  apt source $f -> stable repository apt.armbian.com (backup in /var/backups/kp4pra/)"
    done
    if ! grep -rqs 'apt\.armbian\.com' /etc/apt/sources.list /etc/apt/sources.list.d/; then
        code="$(. /etc/os-release 2>/dev/null; echo "${VERSION_CODENAME:-trixie}")"
        cat > /etc/apt/sources.list.d/kp4pra-armbian-stable.sources <<EOF
Types: deb
URIs: http://apt.armbian.com
Suites: $code
Components: main ${code}-utils
Signed-By: /usr/share/keyrings/armbian-archive-keyring.gpg
EOF
        echo "  added the stable Armbian repository (apt.armbian.com, $code)"
    fi
    apt-get update -qq || true
}

# --- automatic continue / reboot (Orange Pi) ---------------------------------
RESUME_MARK="/rw/kp4pra-tnc/.dra-setup-resume"
RESUME_UNIT="kp4pra-dra-setup-resume.service"
RO_RESTORE=0
REBOOT_AFTER=0

opi_packages() {   # build + runtime tools (needs internet the first time)
    apt-get install -y dkms gcc make device-tree-compiler i2c-tools gpiod curl alsa-utils python3
}

# Download (and cache) the WM8731 source for a kernel release now, so the
# automatic run after the reboot also works without internet (e.g. when the
# board comes up as its own WiFi hotspot).
opi_prefetch_source() {
    local tmp; tmp="$(mktemp -d)"
    cp -r "$OPI_DIR/wm8731-dkms/." "$tmp/"
    if bash "$tmp/prepare.sh" "$1" >/dev/null 2>&1; then
        echo "  OK  : codec driver source for kernel $1 downloaded and cached"
    else
        echo "  WARN: could not pre-download the codec driver source for $1 (the automatic run will try again)"
    fi
    rm -rf "$tmp"
}

# Run this script once more, automatically, at the next boot.
opi_schedule_resume() {
    mkdir -p "$(dirname "$RESUME_MARK")"
    date -Is > "$RESUME_MARK"
    cat > "/etc/systemd/system/$RESUME_UNIT" <<EOF
[Unit]
Description=KP4PRA TNC - finish the DRA-Pi-Zero setup after the kernel change (runs once)
ConditionPathExists=$RESUME_MARK
Wants=network-online.target
After=network-online.target local-fs.target

[Service]
Type=oneshot
ExecStart=/bin/bash $REPO_DIR/scripts/setup-dra-pi-zero.sh --resume
TimeoutStartSec=30min

[Install]
WantedBy=multi-user.target
EOF
    systemctl daemon-reload
    systemctl enable "$RESUME_UNIT" >/dev/null 2>&1
    echo "  OK  : setup will continue automatically after the reboot ($RESUME_UNIT)"
}

# The resume runs only once, whatever happens (no reboot loops).
opi_clear_resume() {
    rm -f "$RESUME_MARK"
    if [ -f "/etc/systemd/system/$RESUME_UNIT" ]; then
        systemctl disable "$RESUME_UNIT" >/dev/null 2>&1 || true
        rm -f "/etc/systemd/system/$RESUME_UNIT"
        systemctl daemon-reload || true
    fi
}

opi_on_exit() {
    local rc=$?
    if [ "$RESUME" = 1 ]; then
        [ "$rc" -ne 0 ] && echo "ERROR: the automatic DRA setup failed (exit $rc). Run it again from SSH: cd ~/kp4pra-tnc && sudo bash scripts/setup-dra-pi-zero.sh"
        opi_clear_resume
    fi
    if [ "$RO_RESTORE" = 1 ]; then
        echo "Restoring read-only root"
        kp4pra-remount-ro || echo "WARN: run sudo kp4pra-remount-ro"
    fi
    if [ "$REBOOT_AFTER" = 1 ]; then
        echo "Rebooting now."
        sync
        systemctl reboot
    fi
}

# Reboot at the end of the script: 10 s countdown when run by hand
# (Ctrl-C cancels), immediately when run by the resume service.
opi_auto_reboot() {
    if [ "$NO_REBOOT" = 1 ]; then
        echo "REBOOT REQUIRED (--no-reboot given): sudo reboot"
        return 0
    fi
    if [ "$RESUME" = 1 ] || [ ! -t 0 ]; then
        REBOOT_AFTER=1
        return 0
    fi
    local i cancelled=0
    trap 'cancelled=1' INT
    echo "Rebooting automatically in 10 seconds - press Ctrl-C to cancel."
    for i in $(seq 10 -1 1); do
        printf '\r  %2d ' "$i"
        sleep 1 || true
        [ "$cancelled" = 1 ] && break
    done
    trap - INT
    echo
    if [ "$cancelled" = 1 ]; then
        echo "Reboot cancelled. Reboot when ready: sudo reboot"
    else
        REBOOT_AFTER=1
    fi
}

opi_setup() {
    [ "$(id -u)" -eq 0 ] || exec sudo bash "$0" "${ORIG_ARGS[@]}"
    # Never stop silently: report where an unexpected failure happened.
    set -E
    trap 'echo "ERROR: DRA setup stopped unexpectedly at line $LINENO (command: $BASH_COMMAND)." >&2; echo "Please report this output." >&2' ERR
    [ -f "$ENV_TXT" ] || opi_die "$ENV_TXT not found. The Orange Pi Zero 2W DRA support needs Armbian."
    [ -f "$OPI_DIR/${OPI_DTS_NAME}.dts" ] || opi_die "run this from the kp4pra-tnc repo checkout (platform/ files missing)."
    command -v armbian-add-overlay >/dev/null || opi_die "armbian-add-overlay not found (Armbian tooling missing)."
    local K VER NEED_REBOOT=0
    K="$(uname -r)"
    VER="$(cat "$REPO_DIR/VERSION" 2>/dev/null || echo 0.0.0)"
    # Keep a copy of every run on the writable partition, so a tester can
    # send it: /rw/kp4pra-tnc/logs/dra-setup.log
    if mkdir -p "$SETUP_LOG_DIR" 2>/dev/null && touch "$SETUP_LOG" 2>/dev/null; then
        # tee ignores Ctrl-C, so cancelling the reboot countdown cannot cut
        # the script's output (and kill it with SIGPIPE).
        exec > >(trap '' INT; exec tee -a "$SETUP_LOG") 2>&1
        echo "--- $(date -Is) setup-dra-pi-zero.sh (mode: $MODE) ---"
    fi
    echo "== DRA-Pi-Zero setup for ${DRA_BOARD} (kernel $K, KP4PRA $VER) =="
    trap opi_on_exit EXIT
    if [ "$RESUME" = 1 ]; then
        if [ ! -f "$RESUME_MARK" ]; then
            echo "Nothing to resume."
            RESUME=0; opi_clear_resume; exit 0
        fi
        echo "Continuing the DRA setup automatically after the kernel change."
    fi

    # Read-only root: remount rw for the install, restore on exit.
    if ! ( : > /usr/src/.kp4pra-wtest ) 2>/dev/null; then
        command -v kp4pra-remount-rw >/dev/null || opi_die "root filesystem is read-only and kp4pra-remount-rw is missing."
        echo "Root filesystem is read-only - remounting rw for the setup"
        kp4pra-remount-rw
        RO_RESTORE=1
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

    # 1. Kernel headers matching the running Armbian kernel. Works on any
    #    Armbian image: rolling/trunk images (beta.armbian.com) often have no
    #    headers for their kernel, so fall back to the stable repository and,
    #    if needed, to the newest stable kernel that has headers (then reboot
    #    and run again).
    echo "-- [1/6] Kernel headers"
    if [ -e "/lib/modules/$K/build/Makefile" ]; then
        echo "  OK  : headers for $K present"
    else
        local IMG HDR DTB IVER PICK
        # Which installed linux-image package is the running kernel?
        # 1) the owner of /boot/vmlinuz-$K, 2) any installed Armbian sunxi64 image.
        IMG="$( { dpkg-query -S "/boot/vmlinuz-$K" 2>/dev/null || true; } | sed -n '1s/:.*//p')"
        if [ -z "$IMG" ]; then
            IMG="$(dpkg-query -W -f '${Package} ${db:Status-Abbrev}\n' 'linux-image-*' 2>/dev/null \
                   | awk '$2 ~ /^ii/ && $1 ~ /sunxi64/ {print $1; exit}' || true)"
        fi
        [ -n "$IMG" ] || opi_die "cannot find the installed Armbian kernel package (linux-image-*-sunxi64) for $K."
        HDR="${IMG/linux-image/linux-headers}"
        DTB="${IMG/linux-image/linux-dtb}"
        IVER="$(dpkg-query -W -f '${Version}' "$IMG" 2>/dev/null || true)"
        [ -n "$IVER" ] || opi_die "cannot read the version of $IMG."
        echo "  running kernel package: $IMG $IVER"
        apt-get update -qq || true
        if opi_has_version "$HDR" "$IVER"; then
            echo "  installing $HDR=$IVER"
            apt-get install -y "$HDR=$IVER"
        else
            echo "  $HDR=$IVER is not in your apt sources (normal for Armbian rolling/trunk images)."
            opi_use_stable_repo
            if opi_has_version "$HDR" "$IVER"; then
                echo "  installing $HDR=$IVER"
                apt-get install -y "$HDR=$IVER"
            else
                PICK="$(opi_pick_kernel "$IMG" "$DTB" "$HDR")"
                [ -n "$PICK" ] || opi_die "no Armbian kernel with matching headers found for $IMG in your apt sources. Check the internet connection and 'apt-cache policy $HDR', then report this output."
                echo "  switching to stable kernel $PICK (image + device tree + headers)"
                apt-get install -y --allow-downgrades "$IMG=$PICK" "$DTB=$PICK" "$HDR=$PICK"
                local NEWK
                NEWK="$(dpkg -L "$IMG" 2>/dev/null | sed -n 's#^/boot/vmlinuz-##p' | head -1)"
                # Everything that needs the internet, now.
                echo "  installing build tools and caching the driver source for the new kernel"
                opi_packages
                [ -n "$NEWK" ] && opi_prefetch_source "$NEWK"
                echo
                echo "== Kernel changed to the stable Armbian kernel $PICK =="
                if [ "$NO_REBOOT" = 1 ]; then
                    echo "REBOOT REQUIRED (--no-reboot given), then run the same command again:"
                    echo "  sudo reboot"
                    echo "  cd ~/kp4pra-tnc && sudo bash scripts/setup-dra-pi-zero.sh"
                    exit 0
                fi
                opi_schedule_resume
                echo "After the reboot the setup finishes by itself (about 5 minutes) and the"
                echo "board reboots once more. Then the DRA-Pi-Zero is ready - nothing else to run."
                echo "Progress: Config page (DRA card) or /rw/kp4pra-tnc/logs/dra-setup.log"
                opi_auto_reboot
                exit 0
            fi
        fi
    fi

    # 2. Build tools and runtime tools.
    echo "-- [2/6] Packages: dkms, compiler, device-tree compiler, i2c-tools, gpiod"
    opi_packages

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
        echo "After the reboot, in the web UI Config page: Detect sound cards, select"
        echo "\"DRA-Pi-Zero (I2S)\" - PTT pre-fills to GPIOD \"${DRA_PTT_CHIP} ${DRA_PTT_LINE}\" - then Preview and Apply."
        opi_auto_reboot
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
