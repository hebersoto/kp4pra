#!/bin/bash
# KP4PRA TNC - one-command install on a fresh Raspberry Pi or Orange Pi Zero 2W.
#
# On the board, after logging in:
#   curl -fsSL https://raw.githubusercontent.com/hebersoto/kp4pra/dev/scripts/bootstrap.sh | sudo bash
#
# It does, in order:
#   1. update the system (apt update + full-upgrade)
#   2. install the tools needed to build and run everything
#   3. build Dire Wolf with libgpiod support (skipped if already installed with it)
#   4. download KP4PRA TNC to ~/kp4pra-tnc (or update it if already there)
#   5. run the KP4PRA installer (scripts/install.sh)
#   6. reboot (10 s countdown, Ctrl-C cancels)
#
# After the reboot a one-time check looks for a DRA-Pi-Zero and sets it up by
# itself if one is plugged in (the board may reboot once or twice more).
#
# Safe to run again: finished steps are skipped or repeated harmlessly.
# Output is also saved to /root/kp4pra-install.log.
#
# Options (environment variables):
#   KP4PRA_BRANCH=main   install the stable branch instead of dev
#   KP4PRA_NO_REBOOT=1   do not reboot at the end
set -euo pipefail

# Everything runs inside main(), called on the last line: with "curl | bash"
# bash then reads the whole script before running any of it, so no command
# can swallow the rest of the script from the pipe.
BRANCH="${KP4PRA_BRANCH:-dev}"
REPO_URL="https://github.com/hebersoto/kp4pra.git"
DIREWOLF_URL="https://github.com/wb2osz/direwolf.git"
LOG=/root/kp4pra-install.log
TOTAL=5

say()  { echo -e "\n\033[0;32m[KP4PRA]\033[0m $*"; }
step() { say "[$1/$TOTAL] $2"; }
fail() { echo -e "\n\033[0;31m[KP4PRA] ERROR:\033[0m $*" >&2
         echo "The full output is in $LOG" >&2; exit 1; }

main() {
    [ "$(id -u)" -eq 0 ] || fail "run it with sudo:  curl -fsSL <link> | sudo bash"
    command -v apt-get >/dev/null || fail "this installer needs Raspberry Pi OS or Armbian (Debian based)."

    # Keep a copy of everything on screen in the log file.
    exec > >(tee -a "$LOG") 2>&1
    echo "=== KP4PRA TNC install started $(date) ==="

    # The person who ran sudo owns ~/kp4pra-tnc (root on Armbian, kp4pra on a Pi).
    OWNER="${SUDO_USER:-root}"
    OWNER_HOME="$(getent passwd "$OWNER" | cut -d: -f6)"
    [ -n "$OWNER_HOME" ] || OWNER_HOME=/root
    REPO_DIR="$OWNER_HOME/kp4pra-tnc"

    # A board that already has KP4PRA installed runs with a read-only root.
    if command -v kp4pra-remount-rw >/dev/null 2>&1; then
        kp4pra-remount-rw >/dev/null 2>&1 || true
    fi

    export DEBIAN_FRONTEND=noninteractive
    APT_OPTS=(-y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold)

    # ---------------------------------------------------------------------------
    step 1 "Updating the system (this can take a while on a new board)"
    apt-get update || fail "could not reach the package servers - check the internet connection"
    apt-get "${APT_OPTS[@]}" full-upgrade

    # ---------------------------------------------------------------------------
    step 2 "Installing the tools KP4PRA needs"
    apt-get "${APT_OPTS[@]}" install git curl python3 python3-venv python3-pip \
        bluez bluez-tools alsa-utils avahi-daemon build-essential i2c-tools \
        cmake libasound2-dev libudev-dev libhamlib-dev gpsd libgps-dev libgpiod-dev

    # ---------------------------------------------------------------------------
    step 3 "Dire Wolf (the packet radio modem)"
    direwolf_ok() {
        command -v direwolf >/dev/null 2>&1 && \
            timeout 5 direwolf --help 2>&1 </dev/null | grep -qi libgpiod
    }
    if direwolf_ok; then
        echo "Dire Wolf is already installed with libgpiod support - skipping the build."
    else
        echo "Building Dire Wolf from source (about 10 minutes on a Zero 2W)..."
        BUILD=/var/tmp/kp4pra-direwolf
        rm -rf "$BUILD"
        git clone --depth 1 "$DIREWOLF_URL" "$BUILD"
        mkdir -p "$BUILD/build"
        # One compile job per 512 MB of RAM, at least 1, at most 4.
        mem_mb=$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)
        jobs=$(( mem_mb / 512 )); [ "$jobs" -lt 1 ] && jobs=1; [ "$jobs" -gt 4 ] && jobs=4
        (cd "$BUILD/build" && cmake .. && make -j"$jobs" && make install)
        rm -rf "$BUILD"
        direwolf_ok || fail "Dire Wolf was built without libgpiod support (needed for GPIO PTT)."
        echo "Dire Wolf installed."
    fi

    # ---------------------------------------------------------------------------
    step 4 "Getting KP4PRA TNC ($BRANCH branch) into $REPO_DIR"
    if [ -d "$REPO_DIR/.git" ]; then
        sudo -u "$OWNER" git -C "$REPO_DIR" fetch origin
        sudo -u "$OWNER" git -C "$REPO_DIR" checkout "$BRANCH"
        sudo -u "$OWNER" git -C "$REPO_DIR" pull --ff-only origin "$BRANCH" \
            || fail "$REPO_DIR has local changes - move them away and run this again."
    else
        sudo -u "$OWNER" git clone -b "$BRANCH" "$REPO_URL" "$REPO_DIR"
    fi
    echo "Version: $(cat "$REPO_DIR/VERSION")"

    # ---------------------------------------------------------------------------
    step 5 "Installing KP4PRA TNC"
    bash "$REPO_DIR/scripts/install.sh" || fail "the KP4PRA installer reported an error (see above)."

    # ---------------------------------------------------------------------------
    say "Install finished."
    echo
    echo "After the reboot, open the web page to finish:"
    echo "  - on your network:      http://$(hostname).local/"
    echo "  - on the KP4PRA WiFi:   http://172.16.0.1/"
    echo "If a DRA-Pi-Zero is plugged in, it is found and set up by itself after"
    echo "the reboot (the board may restart once or twice more - that is normal)."
    echo

    if [ "${KP4PRA_NO_REBOOT:-0}" = 1 ]; then
        echo "Reboot when ready:  sudo reboot"
        exit 0
    fi
    echo "Rebooting in 10 seconds (press Ctrl-C to stay logged in and reboot later)."
    for i in 10 9 8 7 6 5 4 3 2 1; do printf '\r  %2d ' "$i"; sleep 1; done
    echo
    sync
    systemctl reboot
}

main "$@" </dev/null
