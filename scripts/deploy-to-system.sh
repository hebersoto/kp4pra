#!/bin/bash
# KP4PRA TNC - Deploy this repo's app code to the live install and restart.
#
# The counterpart to scripts/sync-from-system.sh (which copies /opt -> repo to
# capture live edits before committing). This goes the other way: repo -> /opt,
# so patches applied in the checkout actually reach the service that runs from
# the install dir. Use it after applying any src/ change (e.g. the apply_aprs*
# installers). It does NOT touch /rw config, systemd units, the venv, or
# Bluetooth -- for those, re-run scripts/install.sh (image build / first boot).
#
# Usage:
#   sudo bash scripts/deploy-to-system.sh                 # sync + restart web
#   sudo bash scripts/deploy-to-system.sh --with-rms      # also restart RMS/APRS-affected services
#   sudo bash scripts/deploy-to-system.sh svc1 svc2 ...   # also restart the named services
#
# Overridable for testing:  KP4PRA_APP_DIR, SERVICE_USER
set -euo pipefail

APP_DIR="${KP4PRA_APP_DIR:-/opt/kp4pra-tnc}"
SERVICE_USER="${SERVICE_USER:-kp4pra-tnc}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; RED='\033[0;31m'; NC='\033[0m'
log()  { echo -e "${GREEN}[deploy]${NC} $*"; }
warn() { echo -e "${YELLOW}[deploy WARN]${NC} $*"; }
die()  { echo -e "${RED}[deploy ERROR]${NC} $*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "Run as root: sudo bash $0"
[[ -f "$REPO_DIR/src/web/web_app.py" ]] || die "No src/web/web_app.py under $REPO_DIR -- run from the repo."
[[ -d "$APP_DIR" ]] || die "Install dir $APP_DIR not found. First-time setup uses scripts/install.sh."

# Which services to restart. Web always; extras from args / --with-rms.
RESTART=(kp4pra-tnc-web.service)
for a in "$@"; do
    case "$a" in
        --with-rms) RESTART+=(kp4pra-tnc-rms.service kp4pra-tnc-aprs-clock.service) ;;
        --*)        warn "unknown flag $a (ignored)" ;;
        *)          RESTART+=("$a") ;;
    esac
done

# ── Read-only root: remount rw for the copy, restore ro on exit ──────────────
REMOUNTED=0
cleanup() { [[ "$REMOUNTED" == 1 ]] && { log "Restoring read-only root"; kp4pra-remount-ro 2>/dev/null || warn "remount-ro failed -- run 'sudo kp4pra-remount-ro' manually"; }; }
trap cleanup EXIT

if ! ( : > "$APP_DIR/.deploy-write-test" ) 2>/dev/null; then
    if command -v kp4pra-remount-rw >/dev/null 2>&1; then
        log "$APP_DIR is read-only; remounting rw"
        kp4pra-remount-rw || die "kp4pra-remount-rw failed"
        REMOUNTED=1
        ( : > "$APP_DIR/.deploy-write-test" ) 2>/dev/null || die "$APP_DIR still not writable after remount"
    else
        die "$APP_DIR is not writable and kp4pra-remount-rw is not installed."
    fi
fi
rm -f "$APP_DIR/.deploy-write-test"

# ── Sync app code (mirror of what install.sh copies) ─────────────────────────
log "Deploying src -> $APP_DIR"
cp -r "$REPO_DIR/src" "$APP_DIR/"
[[ -f "$REPO_DIR/VERSION" ]] && cp "$REPO_DIR/VERSION" "$APP_DIR/VERSION"   # web UI reads this
chown -R "$SERVICE_USER:$SERVICE_USER" "$APP_DIR/src" "$APP_DIR/VERSION" 2>/dev/null \
    || warn "chown to $SERVICE_USER failed (ok if that user doesn't exist here)"
chmod -R u+rwX,go+rX "$APP_DIR/src"
# Drop stale bytecode so the new source is what runs.
find "$APP_DIR/src" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
log "Code deployed (VERSION: $(cat "$APP_DIR/VERSION" 2>/dev/null || echo dev))"

# ── Restart the affected services ────────────────────────────────────────────
if command -v systemctl >/dev/null 2>&1; then
    for svc in "${RESTART[@]}"; do
        if systemctl restart "$svc" 2>/dev/null; then
            log "restarted $svc -> $(systemctl is-active "$svc" 2>/dev/null)"
        else
            warn "could not restart $svc (not installed?)"
        fi
    done
else
    warn "systemctl not found -- restart the services manually."
fi

log "Done. Reload the web UI (Ctrl+Shift+R) to see changes."
