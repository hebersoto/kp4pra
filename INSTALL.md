# KP4PRA TNC — Installation on a Fresh Board

Targets: **Orange Pi Zero 2W** (Armbian/Debian trixie) and
**Raspberry Pi Zero 2 W** (Raspberry Pi OS Lite 64-bit, Bookworm or later).
Requires Python 3.11+ and BlueZ 5.6x+.

## 1. Flash the OS
- Search the web for "Orange Pi Zero 2W: Armbian minimal/CLI image" and install to the → SD card.
- Raspberry Pi Zero 2 W: Raspberry Pi OS Lite via Raspberry Pi Imager —
  **32-bit or 64-bit both work** (both validated; 32-bit on Trixie is the reference configuration). On this 512MB board
  the 32-bit image leaves more free RAM; choose it if memory is tight.

  In the Raspberry Pi Imager or your selected imager settings,
  preconfigure:
  - **Hostname:** set it to `kp4pra`. The board is then reachable at
    `kp4pra.local` and shows up as `kp4pra` on your network.
  - **Username / password:** create the user `kp4pra` with a password you
    choose.
  - **Enable SSH:** turn it on so you can connect with PuTTY (section 1b).
  - **WiFi:** optional — see the note below before deciding.

  > **WiFi vs. the KP4PRA hotspot — pick one.** The board has a single WiFi
  > radio, so it can either *join* your WiFi **or** broadcast its own KP4PRA
  > hotspot — **not both at once.** If you enter your WiFi here and the board
  > connects, its own hotspot will **not** be available. Leave WiFi blank for a
  > self-contained field unit: the board then starts its own KP4PRA hotspot at
  > boot so a phone connects to it directly (see section 6b). You can switch
  > between the two modes anytime with `sudo kp4pra-wifi-mode ap|client`.

Boot the board — headless (no monitor or keyboard) is fine. Section 1b
connects you to its command line and updates the system.

## 1b. Connect to the board over SSH (PuTTY)

> **New to Linux or headless setup?** This section gets you a terminal on the
> board from a Windows PC. **Experienced users** who already SSH in — or who
> use a monitor and keyboard on the board — can skip to section 2.

The board runs "headless": no monitor or keyboard. You reach its command line
from your Windows PC over the network with **PuTTY**, a free SSH client.

1. **Enable SSH when you flash** (section 1). In Raspberry Pi Imager's settings
   (the gear icon), turn on **Enable SSH**, set the username to `kp4pra` with a
   password, and enter your WiFi so the board joins your network at boot. On
   Armbian, SSH is enabled by default.
2. **Install PuTTY** on your PC from https://www.putty.org/ (Windows installer).
3. **Find the board's address.** Give it a minute to boot, then try the
   hostname `kp4pra.local`. If that does not connect, open your router's
   device/DHCP list and note the board's IP address (for example
   `192.168.1.42`).
4. **Open PuTTY**, enter `kp4pra.local` (or the IP) in **Host Name**, leave
   **Port** at `22`, and click **Open**. The first time, accept the security
   alert (**Accept** / **Yes**) — this just records the board's key.
5. **Log in** as `kp4pra` with the password you set. You now have the board's
   command line. Every command in the sections below is typed — or pasted —
   into this PuTTY window.

> **Pasting into PuTTY:** use the **copy button** on any command block below,
> then **right-click** inside the PuTTY window to paste, and press **Enter**.
> PuTTY pastes with a right-click, not Ctrl+V. (Every ` ``` ` command block on
> GitHub has a copy button in its top-right corner when you hover over it.)

Once connected, update the system before installing:
```bash
sudo apt update && sudo apt upgrade -y
```

> **KNOWN ISSUE (June 2026) — BLE advertising broken on current Raspberry Pi
> OS kernels.** A kernel patch ("Bluetooth: MGMT: validate Add Extended
> Advertising Data length", June 2026) breaks BlueZ instance advertising with
> Invalid Parameters (0x0d). It shipped in kernel 6.18-rpt and was backported
> into the 6.12.9x stable series, so BOTH current images (Trixie and Bookworm,
> all Pi models) are affected. Confirmed on Pi Zero W Rev 1.1 and Pi 3 B+;
> Armbian kernels (Orange Pi) are currently unaffected. The KP4PRA TNC BLE
> bridge detects the failure and falls back to legacy raw-HCI advertising
> automatically (bin/kp4pra-legacy-adv, installed by install.sh); if that also
> fails it exits cleanly and Android/RFCOMM continues working. Track the
> Raspberry Pi forums and raspberrypi/linux for the upstream fix; no kernel
> hold is required since the fallback handles affected kernels.

## 2. Base packages
```bash
sudo apt install -y git python3 python3-venv python3-pip \
    bluez bluez-tools alsa-utils avahi-daemon build-essential
```
Note: on some releases python3-venv is versioned (e.g. `python3.13-venv`) —
install whichever apt suggests if venv creation fails.

## 3. Build Dire Wolf (with CM108 PTT support)
```bash
sudo apt install -y cmake libasound2-dev libudev-dev libhamlib-dev gpsd libgps-dev libgpiod-dev
cd ~ && git clone https://github.com/wb2osz/direwolf.git
cd direwolf && mkdir build && cd build
cmake .. && make -j2 && sudo make install
```
Verify the build:
```bash
direwolf --help    # the optional-support line must list libgpiod
cm108              # lists the CM108 HID → ADEVICE mapping
```
The `--help` banner must list `libgpiod` in optional support - the
DRA-Pi-Zero's GPIOD PTT needs it, and on kernel 6.x legacy sysfs GPIO
is unreliable. If it's missing, install libgpiod-dev and rebuild.

**DRA-Pi-Zero users only** (I2S board): after install, run
`sudo bash scripts/setup-dra-pi-zero.sh`, reboot, then run it once more
to apply the WM8731 mixer. It enables the I2S overlay, disables onboard
audio, sets mixer levels, and adds the service user to the gpio group.
USB (CM108) users skip this entirely. See docs/DRA_PI_ZERO.md.
The initial direwolf.conf is created automatically by the installer
(a minimal config is seeded on /rw with a symlink at
/home/kp4pra/direwolf.conf). Configure your station via the web UI
after installation.

## 4. Writable partition (production layout)
Create a second partition on the SD card mounted at `/rw`
(see README.md, "Read-Only Filesystem Deployment").

For bench testing you can skip the extra partition and simply create the
`/rw` directory on the root filesystem, then migrate to a real partition
later (the rest of the install is identical either way). Enter this command:
```bash
sudo mkdir -p /rw
```

## 5. KP4PRA TNC — stage 1
```bash
cd ~ && git clone https://github.com/hebersoto/kp4pra.git kp4pra-tnc
cd kp4pra-tnc
sudo bash scripts/install.sh
```
Installs: system user, venv + pinned Python deps, systemd units
(bridges, web, pairing agent, BlueZ bind mount, perms fix), sudoers,
helper scripts, tmpfiles rule, bluetoothd -C compat mode, volatile journald.

At the end of the install the KP4PRA hotspot starts automatically
(unless you are installing over a WiFi connection, in which case start
it manually: `sudo kp4pra-wifi-mode ap`). See section 6b for the SSID,
password, and web UI address.

## 6. KP4PRA TNC — stage 2 (Dire Wolf integration)

**Runs automatically at the end of step 5 install.sh.** Run it manually
only if stage 2 reported errors, or after building Dire Wolf later
(stage 2 tolerates a missing direwolf binary, but the direwolf service
cannot start until step 3 is done):
```bash
sudo bash scripts/install-direwolf-integration.sh
```
Installs: direwolf.service (journald output for the web traffic view),
ADEVICE self-heal at boot, direwolf.conf on /rw with symlink,
group memberships (systemd-journal, audio, dialout), Dire Wolf
control sudoers, port 80→8088 redirect.

## 6b. Accessing the web interface — two ways

**On your home/shop network (client mode):** set the WiFi the TNC should
join in the config (`wifi.client_ssid` / `wifi.client_password` in
/rw/kp4pra-tnc/config.yaml, or preconfigure WiFi in the imager at flash
time). Find its address (`hostname -I` on the console, your router's
client list, or `http://kp4pra.local/`) and browse to it — port 80
works, no :8088 needed.

**Anywhere, via the built-in hotspot (AP / field mode):** the TNC can
broadcast its own WiFi network so a phone connects directly — no other
network needed. Switch modes:

```bash
sudo kp4pra-wifi-mode ap        # start the hotspot
sudo kp4pra-wifi-mode client    # return to your configured WiFi
sudo kp4pra-wifi-mode status
```

Hotspot defaults (change them on the Config page / in
/rw/kp4pra-tnc/config.yaml under `wifi:` — especially the password):

| Setting | Default |
|---|---|
| SSID | `KP4PRA` |
| Password | `qwerty1234` |
| Web UI | `http://172.16.0.1/` |

**Boot behavior:**
- `wifi.client_ssid` **blank** → the TNC boots straight into AP mode
  (the hotspot above) so it is always reachable out of the box.
- `wifi.client_ssid` **set** → the TNC tries to join that network; if it
  has not connected within **5 minutes**, it automatically falls back to
  AP mode so a failed WiFi never leaves the unit unreachable.
- Switch manually anytime: `sudo kp4pra-wifi-mode ap|client|status`.

> **Notes:** AP mode disconnects the board from your home WiFi (one
> radio) — switch back with `kp4pra-wifi-mode client` from a device on
> the KP4PRA network, or use a USB Ethernet adapter for uninterrupted
> management. To make the hotspot start automatically at boot (field
> units), set `wifi.mode_at_boot: "ap"` in the config. Bluetooth
> KISS (Android and iPhone) works the same in either mode.

## 7. First-boot verification
- `http://<host>:8088` (or plain `http://<host>/`) → Dashboard all green.
- Config page → Station Information → set callsign/grid/etc → Detect
  sound card → Preview → Apply to Dire Wolf.
- Optional: `sudo kp4pra-wifi-mode ap`, join the KP4PRA WiFi from a
  phone (password above), browse http://172.16.0.1/ — then switch back.
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
- **BLE on Zero W Rev 1.x: WORKING** via the legacy raw-HCI advertising
  fallback (iPhone aprs.fi connect and traffic confirmed). The kernel
  MGMT regression affects this board like all current Pi kernels; the
  fallback handles it automatically. Android/RFCOMM works normally.
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
