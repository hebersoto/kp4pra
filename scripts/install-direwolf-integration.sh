#!/bin/bash
# KP4PRA TNC - Stage 2: Dire Wolf integration
# Run AFTER scripts/install.sh, on a system where Dire Wolf is built and
# direwolf.conf exists at /home/kp4pra/direwolf.conf (or pass CONF=path).
set -euo pipefail
PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONF="${CONF:-/home/kp4pra/direwolf.conf}"
DW_BIN="$(command -v direwolf || echo /usr/local/bin/direwolf)"
[[ $EUID -eq 0 ]] || { echo "Run as root"; exit 1; }

echo "[stage2] Group memberships (journal read, audio detect, serial PTT)"
usermod -aG systemd-journal kp4pra-tnc
usermod -aG audio kp4pra-tnc
usermod -aG dialout kp4pra 2>/dev/null || true
# Dire Wolf runs as kp4pra and must traverse /rw/kp4pra-tnc (750,
# owned kp4pra-tnc) to read its conf through the symlink:
usermod -aG kp4pra-tnc kp4pra 2>/dev/null || true

echo "[stage2] Seeding /rw/kp4pra-tnc/direwolf.conf and symlink for -c path"
RW_CONF="/rw/kp4pra-tnc/direwolf.conf"
# 1. If the admin pre-created a real conf at $CONF, migrate it to /rw.
if [ -f "$CONF" ] && [ ! -L "$CONF" ]; then
    cp "$CONF" "$RW_CONF"
    mv "$CONF" "$CONF.bak"
fi
# 2. If /rw has no usable conf yet, write a minimal starter so Dire Wolf
#    can start on first boot (the web UI overwrites this when the user
#    saves their station config).
if [ ! -s "$RW_CONF" ]; then
    cat > "$RW_CONF" << 'DWCONF'
# KP4PRA TNC - minimal starter Dire Wolf config.
# Replace via the web UI (Config -> station -> Apply) with real settings.
ADEVICE plughw:1,0
ACHANNELS 1
CHANNEL 0
MYCALL N0CALL
MODEM 1200
DWCONF
fi
chown kp4pra-tnc:kp4pra-tnc "$RW_CONF"
# 3. ALWAYS ensure the symlink exists (this was the fresh-install bug:
#    previously the link was only made when $CONF pre-existed).
if [ ! -L "$CONF" ]; then
    rm -f "$CONF"
    ln -s "$RW_CONF" "$CONF"
fi

echo "[stage2] ADEVICE self-heal service"
install -m 755 "$PROJECT_DIR/bin/kp4pra-adevice-fix" /usr/local/bin/
cp "$PROJECT_DIR/systemd/kp4pra-adevice.service" /etc/systemd/system/

echo "[stage2] Dire Wolf systemd unit"
sed "s|/usr/local/bin/direwolf|$DW_BIN|" \
    "$PROJECT_DIR/systemd/direwolf.service" > /etc/systemd/system/direwolf.service

echo "[stage2] Sudoers for Dire Wolf control"
cp "$PROJECT_DIR/sudoers.d/kp4pra-tnc-direwolf" /etc/sudoers.d/
chmod 0440 /etc/sudoers.d/kp4pra-tnc-direwolf
visudo -c -f /etc/sudoers.d/kp4pra-tnc-direwolf

echo "[stage2] Port 80 -> 8088 redirect (nftables)"
apt-get install -y nftables >/dev/null
if ! grep -q "redirect to :8088" /etc/nftables.conf 2>/dev/null; then
cat >> /etc/nftables.conf << 'NFT'

table ip nat {
        chain prerouting {
                type nat hook prerouting priority dstnat;
                tcp dport 80 redirect to :8088
        }
}
NFT
fi
systemctl enable --now nftables

echo "[stage2] Enable and start"
systemctl daemon-reload
systemctl enable kp4pra-adevice.service direwolf.service
systemctl restart kp4pra-adevice.service direwolf.service kp4pra-tnc-web.service
echo "[stage2] Done. Verify: systemctl is-active direwolf.service"
