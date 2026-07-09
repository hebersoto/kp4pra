#!/bin/bash
# Sync live system files into this repo before committing.
set -e
cd "$(dirname "$0")/.."
sudo cp -r /opt/kp4pra-tnc/src .
for u in direwolf kp4pra-adevice kp4pra-tnc-agent kp4pra-bt-perms; do
  sudo cp /etc/systemd/system/$u.service systemd/ 2>/dev/null || true
done
sudo cp /usr/local/bin/kp4pra-* bin/ 2>/dev/null || true
sudo cp /etc/sudoers.d/kp4pra-tnc* sudoers.d/ 2>/dev/null || true
# NOTE: deliberately does NOT copy /rw config.yaml - the repo keeps the
# generic N0CALL example.
sudo chown -R kp4pra:kp4pra .
sudo find . -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null
echo "Synced. Review with: git status --short"
