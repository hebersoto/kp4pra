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

echo "[stage2] Moving direwolf.conf to /rw with symlink for -c path"
if [ -f "$CONF" ] && [ ! -L "$CONF" ]; then
    cp "$CONF" /rw/kp4pra-tnc/direwolf.conf
    mv "$CONF" "$CONF.bak"
    ln -s /rw/kp4pra-tnc/direwolf.conf "$CONF"
fi
touch /rw/kp4pra-tnc/direwolf.conf
chown kp4pra-tnc:kp4pra-tnc /rw/kp4pra-tnc/direwolf.conf

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
