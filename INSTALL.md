# KP4PRA TNC — Installation on a Fresh Board

Targets: **Orange Pi Zero 2W** (Armbian/Debian trixie) and
**Raspberry Pi Zero 2 W** (Raspberry Pi OS Lite 64-bit, Bookworm or later).
Requires Python 3.11+ and BlueZ 5.6x+.

## 1. Flash the OS
- Orange Pi Zero 2W: Armbian minimal/CLI image → SD card.
- Raspberry Pi Zero 2 W: Raspberry Pi OS Lite (64-bit) via Raspberry Pi
  Imager; preconfigure user `kp4pra`, WiFi, and SSH in the imager.
Boot, log in as `kp4pra`, and update: `sudo apt update && sudo apt upgrade -y`

## 2. Base packages
```bash
sudo apt install -y git python3 python3-venv python3-pip \
    bluez bluez-tools alsa-utils avahi-daemon build-essential
```
Note: on some releases python3-venv is versioned (e.g. `python3.13-venv`) —
install whichever apt suggests if venv creation fails.

## 3. Build Dire Wolf (with CM108 PTT support)
```bash
sudo apt install -y cmake libasound2-dev libudev-dev libhamlib-dev gpsd libgps-dev
cd ~ && git clone https://github.com/wb2osz/direwolf.git
cd direwolf && mkdir build && cd build
cmake .. && make -j2 && sudo make install
```
Verify: `direwolf --help` and `cm108` (lists CM108 HID→ADEVICE mapping).
Create an initial `/home/kp4pra/direwolf.conf` (the web UI will regenerate
it later; a minimal `ADEVICE plughw:1,0` / `MYCALL N0CALL` file is enough
to start).

## 4. Writable partition (production layout)
Create a second partition on the SD card mounted at `/rw`
(see README.md, "Read-Only Filesystem Deployment"). For bench testing you
may instead just `sudo mkdir -p /rw` on the root filesystem and migrate to
a real partition later (INSTALL step is identical afterward).

## 5. KP4PRA TNC — stage 1
```bash
cd ~ && git clone https://github.com/hebersoto/kp4pra.git kp4pra-tnc
cd kp4pra-tnc
sudo bash scripts/install.sh
```
Installs: system user, venv + pinned Python deps, systemd units
(bridges, web, pairing agent, BlueZ bind mount, perms fix), sudoers,
helper scripts, tmpfiles rule, bluetoothd -C compat mode, volatile journald.

## 6. KP4PRA TNC — stage 2 (Dire Wolf integration)
```bash
sudo bash scripts/install-direwolf-integration.sh
```
Installs: direwolf.service (journald output for the web traffic view),
ADEVICE self-heal at boot, direwolf.conf on /rw with symlink,
group memberships (systemd-journal, audio, dialout), Dire Wolf
control sudoers, port 80→8088 redirect.

## 7. First-boot verification
- `http://<host>:8088` (or plain `http://<host>/`) → Dashboard all green.
- Config page → Station Information → set callsign/grid/etc → Detect
  sound card → Preview → Apply to Dire Wolf.
- Pair Android via the Android wizard (Just Works — confirm on phone).
- iPhone: aprs.fi → BLE KISS → select the TNC (no iOS pairing).
- Services page → Dire Wolf Traffic → Refresh: RF decodes appear.

## 8. Production hardening (when ready for the field)
Read-only root, pre-flight checklist, and golden SD image creation: see **DEPLOYMENT.md**.
Summary: fstab root `ro`, `/rw` partition `rw,noatime`; BlueZ state and
config live on /rw; runtime state on /run (tmpfs); no persistent logs.

## Low-memory / ARMv6 boards (original Pi Zero W, Rev 1.x)

Validated to install, with caveats: single ARMv6 core + 512MB RAM.
- Dependencies MUST come from prebuilt wheels. install.sh enforces
  --only-binary; ensure piwheels is configured in /etc/pip.conf
  (extra-index-url=https://www.piwheels.org/simple - default on
  Raspberry Pi OS).
- Use the 512MB tmpfs sizes from DEPLOYMENT.md.
- Expect a slow web UI and high CPU from Dire Wolf's demodulator.
  The Zero 2 W or Orange Pi Zero 2W is the recommended platform.

## Raspberry Pi Zero 2 W differences
- Same instructions; device names differ (`/dev/mmcblk0` on both, but
  verify with `lsblk` before partitioning).
- Onboard audio/HDMI cards enumerate differently — always use the web
  UI's **Detect** button rather than assuming card numbers.
- BlueZ/systemd versions on Raspberry Pi OS Bookworm are compatible with
  everything here (bluetoothd path may be /usr/libexec/bluetooth/bluetoothd
  — install.sh auto-detects it).
