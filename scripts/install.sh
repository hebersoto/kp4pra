#!/bin/bash
# KP4PRA TNC - Install Script
# Run during system image build or initial first-boot setup ONLY.
# This script is NOT run during normal read-only operation.
#
# Prerequisites:
#   - Debian/Armbian on Orange Pi
#   - Python 3.10+
#   - BlueZ installed (bluetooth, bluez, bluez-tools)
#   - Dire Wolf installed and configured
#   - Root or sudo access
#   - /rw partition mounted and writable
#
# Usage:
#   sudo bash install.sh
#   Or: bash install.sh  (if already root)

set -euo pipefail

PRODUCT="KP4PRA TNC"
APP_DIR="/opt/kp4pra-tnc"
CONFIG_DIR="/rw/kp4pra-tnc"
RUNTIME_DIR="/run/kp4pra-tnc"
BLUEZ_STATE_DIR="/rw/kp4pra-tnc/bluetooth"
SERVICE_USER="kp4pra-tnc"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[KP4PRA TNC]${NC} $*"; }
STEP=0; TOTAL_STEPS=10
step() { STEP=$((STEP+1)); echo -e "${GREEN}[KP4PRA TNC] [${STEP}/${TOTAL_STEPS}]${NC} $*"; }
warn() { echo -e "${YELLOW}[KP4PRA TNC WARN]${NC} $*"; }
err()  { echo -e "${RED}[KP4PRA TNC ERROR]${NC} $*" >&2; }
die()  { err "$*"; exit 1; }

# ── Preflight checks ─────────────────────────────────────────────────────────

log "Starting KP4PRA TNC installation..."

[[ $EUID -eq 0 ]] || die "Run as root: sudo bash $0"

python3 --version >/dev/null 2>&1 || die "Python 3 not found. Install: apt install python3 python3-pip python3-venv"

bluetoothctl --version >/dev/null 2>&1 || die "bluetoothctl not found. Install: apt install bluez bluez-tools"

sdptool --help >/dev/null 2>&1 || warn "sdptool not found - install bluez-tools (required for RFCOMM SDP registration)"
python3 -m venv --help >/dev/null 2>&1 || die "python3-venv not available. Install: apt install python3-venv (or python3.X-venv for your version)"
command -v bt-agent >/dev/null 2>&1 || warn "bt-agent not found - install bluez-tools (required for Just Works pairing agent)"

if ! mountpoint -q /rw 2>/dev/null && ! [ -d /rw/kp4pra-tnc ]; then
    warn "/rw does not appear to be a separate writable partition."
    warn "Configuration and Bluetooth state will use the root filesystem."
    warn "For production use, mount a dedicated writable partition at /rw."
    read -rp "Continue anyway? [y/N] " ans
    [[ "$ans" =~ ^[Yy]$ ]] || exit 1
fi

# ── System user ──────────────────────────────────────────────────────────────

step "Creating system user: $SERVICE_USER"
if ! id "$SERVICE_USER" &>/dev/null; then
    useradd --system --no-create-home --shell /bin/false \
            --groups bluetooth \
            "$SERVICE_USER"
    log "User $SERVICE_USER created"
else
    # Ensure bluetooth group membership
    usermod -aG bluetooth "$SERVICE_USER"
    log "User $SERVICE_USER already exists, ensured bluetooth group"
fi

# ── Application directory ────────────────────────────────────────────────────

step "Installing application to $APP_DIR"
mkdir -p "$APP_DIR"
cp -r "$PROJECT_DIR/src" "$APP_DIR/"
chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR"
chmod -R 755 "$APP_DIR"

# ── Python virtual environment ───────────────────────────────────────────────

step "Creating Python virtualenv at $APP_DIR/venv"
python3 -m venv "$APP_DIR/venv"

step "Installing Python dependencies..."
"$APP_DIR/venv/bin/pip" install --upgrade pip --quiet

# Install all dependencies at install time (no runtime pip installs).
# Versions pinned to a combination verified on Python 3.13 / Debian trixie.
# PyBluez intentionally absent: unmaintained, fails to build on py3.13.
# The RFCOMM bridge uses the built-in socket module instead.
# --only-binary refuses source builds: on ARMv6/512MB boards (Pi Zero W)
# compiling uvloop/pydantic-core swaps the box to death. Plain uvicorn
# (no [standard] extras) avoids uvloop entirely; performance difference
# is irrelevant for a single-user LAN UI.
"$APP_DIR/venv/bin/pip" install --only-binary=:all: \
    "fastapi==0.115.5" \
    "starlette==0.41.3" \
    uvicorn \
    "jinja2==3.1.4" \
    pyyaml \
    dbus-next \
    --quiet

log "Python dependencies installed"

# ── Writable configuration directory ─────────────────────────────────────────

step "Setting up writable config directory: $CONFIG_DIR"
mkdir -p "$CONFIG_DIR" \
         "$CONFIG_DIR/state" \
         "$CONFIG_DIR/data" \
         "$CONFIG_DIR/bluetooth"
chown -R "$SERVICE_USER:$SERVICE_USER" "$CONFIG_DIR"
chmod -R 750 "$CONFIG_DIR"

# Install default config if not present
if [ ! -f "$CONFIG_DIR/config.yaml" ]; then
    cp "$PROJECT_DIR/config/config.yaml" "$CONFIG_DIR/config.yaml"
    chown "$SERVICE_USER:$SERVICE_USER" "$CONFIG_DIR/config.yaml"
    log "Default config installed to $CONFIG_DIR/config.yaml"
else
    log "Config already exists at $CONFIG_DIR/config.yaml - not overwriting"
fi

# ── BlueZ state directory setup ──────────────────────────────────────────────

step "Setting up BlueZ persistent state directory: $BLUEZ_STATE_DIR"
mkdir -p "$BLUEZ_STATE_DIR"

# Copy any existing BlueZ pairing state to the persistent location
if [ -d /var/lib/bluetooth ] && [ "$(ls -A /var/lib/bluetooth 2>/dev/null)" ]; then
    log "Copying existing BlueZ state to $BLUEZ_STATE_DIR..."
    cp -a /var/lib/bluetooth/. "$BLUEZ_STATE_DIR/"
fi

chown -R root:bluetooth "$BLUEZ_STATE_DIR"
chmod -R 750 "$BLUEZ_STATE_DIR"

# ── Systemd units ────────────────────────────────────────────────────────────

step "Installing systemd service units"
cp "$PROJECT_DIR/systemd/kp4pra-tnc.target"          /etc/systemd/system/
cp "$PROJECT_DIR/systemd/kp4pra-tnc-rfcomm.service"  /etc/systemd/system/
cp "$PROJECT_DIR/systemd/kp4pra-tnc-ble.service"     /etc/systemd/system/
cp "$PROJECT_DIR/systemd/kp4pra-tnc-web.service"     /etc/systemd/system/
cp "$PROJECT_DIR/systemd/var-lib-bluetooth.mount"     /etc/systemd/system/
cp "$PROJECT_DIR/systemd/kp4pra-tnc-agent.service"    /etc/systemd/system/
cp "$PROJECT_DIR/systemd/kp4pra-bt-perms.service"     /etc/systemd/system/
cp "$PROJECT_DIR/systemd/kp4pra-wifi-mode.service"   /etc/systemd/system/

log "Installing helper scripts to /usr/local/bin"
install -m 755 "$PROJECT_DIR/bin/kp4pra-remount-rw"   /usr/local/bin/
install -m 755 "$PROJECT_DIR/bin/kp4pra-remount-ro"   /usr/local/bin/
install -m 755 "$PROJECT_DIR/bin/kp4pra-fix-bt-perms" /usr/local/bin/
install -m 755 "$PROJECT_DIR/bin/kp4pra-legacy-adv"   /usr/local/bin/
install -m 755 "$PROJECT_DIR/bin/kp4pra-wifi-mode"   /usr/local/bin/

log "Creating capability-bearing HCI tool copies for the legacy-adv fallback"
# File capabilities on private copies: works regardless of unit hardening,
# capability inheritance quirks, or NoNewPrivileges (which blocks sudo).
mkdir -p /usr/local/lib/kp4pra
for t in hcitool hciconfig; do
    if [ -x "/usr/bin/$t" ]; then
        cp "/usr/bin/$t" /usr/local/lib/kp4pra/
        setcap cap_net_admin,cap_net_raw+ep "/usr/local/lib/kp4pra/$t" ||             warn "setcap failed for $t - legacy BLE advertising fallback may not work"
    else
        warn "$t not found - install bluez (legacy BLE advertising fallback needs it)"
    fi
done

log "Installing tmpfiles rule for /run/kp4pra-tnc"
install -m 644 "$PROJECT_DIR/config/tmpfiles-kp4pra-tnc.conf" /etc/tmpfiles.d/kp4pra-tnc.conf
systemd-tmpfiles --create /etc/tmpfiles.d/kp4pra-tnc.conf

log "Enabling bluetoothd compatibility mode (-C) for sdptool SDP registration"
BLUETOOTHD_BIN=""
for p in /usr/libexec/bluetooth/bluetoothd /usr/lib/bluetooth/bluetoothd; do
    [ -x "$p" ] && BLUETOOTHD_BIN="$p" && break
done
if [ -n "$BLUETOOTHD_BIN" ]; then
    mkdir -p /etc/systemd/system/bluetooth.service.d
    cat > /etc/systemd/system/bluetooth.service.d/compat.conf << BTEOF
[Service]
ExecStart=
ExecStart=$BLUETOOTHD_BIN -C
BTEOF
else
    warn "bluetoothd binary not found - SDP registration may fail without compat mode"
fi

chmod 644 /etc/systemd/system/kp4pra-tnc*.service \
          /etc/systemd/system/kp4pra-tnc.target \
          /etc/systemd/system/var-lib-bluetooth.mount

systemctl daemon-reload

# ── Bind mount for BlueZ state ───────────────────────────────────────────────

log "Enabling var-lib-bluetooth.mount (persistent BlueZ state bind mount)"
systemctl enable var-lib-bluetooth.mount

# ── Sudoers ──────────────────────────────────────────────────────────────────

step "Installing sudoers rules"
cp "$PROJECT_DIR/sudoers.d/kp4pra-tnc" /etc/sudoers.d/kp4pra-tnc
chmod 0440 /etc/sudoers.d/kp4pra-tnc
# Validate sudoers syntax
visudo -c -f /etc/sudoers.d/kp4pra-tnc || {
    err "Sudoers validation failed! Removing invalid file."
    rm /etc/sudoers.d/kp4pra-tnc
    die "Fix sudoers file and retry."
}
log "Sudoers rules installed and validated"

# ── Avahi DNS-SD ─────────────────────────────────────────────────────────────

# DNS-SD: Dire Wolf (built with dns-sd support) advertises _kiss-tnc._tcp
# itself - no separate Avahi service file is needed or installed. If your
# Dire Wolf build lacks dns-sd, create /etc/avahi/services/kiss-tnc.service
# manually or rebuild Dire Wolf with avahi support.
log "DNS-SD advertisement is handled by Dire Wolf - skipping Avahi file"

# ── BlueZ configuration ──────────────────────────────────────────────────────

log "Configuring BlueZ main.conf for KP4PRA TNC"
BLUEZ_CONF=/etc/bluetooth/main.conf

if [ -f "$BLUEZ_CONF" ]; then
    # Set device name for Bluetooth Classic
    sed -i "s/^#*Name = .*/Name = KP4PRA TNC/" "$BLUEZ_CONF" 2>/dev/null || true
    # Ensure Class shows as a device (not specific class for RFCOMM)
    log "BlueZ main.conf updated"
else
    warn "BlueZ main.conf not found at $BLUEZ_CONF"
fi

# ── Enable and start services ────────────────────────────────────────────────

step "Enabling KP4PRA TNC services"
systemctl enable kp4pra-tnc.target
systemctl enable kp4pra-tnc-rfcomm.service
systemctl enable kp4pra-tnc-ble.service
systemctl enable kp4pra-tnc-web.service
systemctl enable kp4pra-tnc-agent.service
systemctl enable kp4pra-bt-perms.service
systemctl enable kp4pra-wifi-mode.service

log "Starting KP4PRA TNC services"
# Start the bind mount first
systemctl start var-lib-bluetooth.mount || warn "BlueZ bind mount failed - check /rw/kp4pra-tnc/bluetooth"
# Fresh Raspberry Pi OS images may ship with radios soft-blocked
rfkill unblock bluetooth 2>/dev/null || true
systemctl restart bluetooth.service     || warn "Bluetooth service restart failed"
systemctl start kp4pra-tnc-agent.service  || warn "Pairing agent start failed (bt-agent from bluez-tools required)"
systemctl start kp4pra-bt-perms.service   || warn "BlueZ perms fix failed"
/usr/local/bin/kp4pra-fix-bt-perms 2>/dev/null || true
usermod -aG bluetooth "$SERVICE_USER" 2>/dev/null || true
systemctl start kp4pra-tnc-rfcomm.service || warn "RFCOMM bridge start failed (may need Bluetooth adapter)"
systemctl start kp4pra-tnc-ble.service    || warn "BLE bridge start failed (may need Bluetooth adapter)"
systemctl start kp4pra-tnc-web.service    || warn "Web interface start failed"

# ── journald: ensure volatile-only ─────────────────────────────────────────

step "Configuring journald for volatile-only logging (no persistent log files)"
mkdir -p /etc/systemd/journald.conf.d
cat > /etc/systemd/journald.conf.d/kp4pra-tnc-volatile.conf << 'EOF'
# KP4PRA TNC - Force volatile (RAM-only) journal
# No log files are written to disk.
# All journal data disappears after reboot.
[Journal]
Storage=volatile
Compress=no
EOF
systemctl restart systemd-journald 2>/dev/null || true
log "journald configured for volatile storage"

# ── Final status ─────────────────────────────────────────────────────────────

# ── Stage 2: Dire Wolf integration (chained automatically) ──────────────────
if [ -x "$PROJECT_DIR/scripts/install-direwolf-integration.sh" ]; then
    log "Running stage 2 (Dire Wolf integration)..."
    if bash "$PROJECT_DIR/scripts/install-direwolf-integration.sh"; then
        log "Stage 2 complete"
    else
        warn "Stage 2 reported errors - review output above."
        warn "It can be re-run any time: sudo bash scripts/install-direwolf-integration.sh"
    fi
else
    warn "Stage 2 script not found - run it manually after building Dire Wolf:"
    warn "  sudo bash scripts/install-direwolf-integration.sh"
fi

echo ""
log "═══════════════════════════════════════════════════════"
log "  KP4PRA TNC installation complete!"
log "═══════════════════════════════════════════════════════"
echo ""
log "  Web interface: http://$(hostname).local:8088"
log "  BLE device name:    KP4PRA TNC"
log "  RFCOMM device name: KP4PRA TNC"
log "  Config: $CONFIG_DIR/config.yaml"
echo ""
log "  Service status:"
systemctl is-active kp4pra-tnc-rfcomm.service && log "  ✓ RFCOMM bridge: running" || warn "  ✗ RFCOMM bridge: not running"
systemctl is-active kp4pra-tnc-ble.service    && log "  ✓ BLE bridge:    running" || warn "  ✗ BLE bridge:    not running"
systemctl is-active kp4pra-tnc-web.service    && log "  ✓ Web interface: running" || warn "  ✗ Web interface: not running"
echo ""
warn "  To complete setup on a read-only root system:"
warn "  1. Reboot to verify services start from saved state"
warn "  2. Open http://$(hostname).local:8088 to manage Bluetooth"
warn "  3. Android: use the Android provisioning wizard to pair your phone"
warn "  4. iPhone: no pairing needed - open aprs.fi, choose BLE KISS TNC,"
warn "     and select this device (see the iPhone page in the web UI)"
echo ""
