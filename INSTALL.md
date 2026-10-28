# Installing KP4PRA TNC

This guide takes you from a blank SD card to a working TNC. Most of the time
is spent waiting while the board installs things on its own.

## What you need

- An Orange Pi Zero 2W or a Raspberry Pi (Zero 2 W or 3).
- A microSD card, 16 GB or larger.
- A radio interface: a USB sound card (DigiRig, AIOC, Signalink and similar)
  or a DRA-Pi-Zero board on the header.
- Internet for the board while it installs, over WiFi or a USB network
  adapter.
- A Windows PC with PuTTY to type commands on the board (step 2).

## Step 1. Prepare the SD card

**Orange Pi Zero 2W**

1. Download Armbian (Debian 13, minimal):
   https://dl.armbian.com/orangepizero2w/Trixie_current_minimal
2. Write it to the SD card with balenaEtcher or Raspberry Pi Imager
   ("Use custom").
3. Put the card in the board and power it on. If you have a USB network
   adapter, plug it in now.

**Raspberry Pi**

1. Open Raspberry Pi Imager and choose Raspberry Pi OS Lite (32-bit or 64-bit
   both work).
2. Before writing, open the settings (gear icon) and set:
   - Hostname: `kp4pra`
   - Username `kp4pra` and a password of your choice
   - Your home WiFi, so the board can download what it needs
   - Enable SSH
3. Write the card, put it in the board and power it on.

Give the board two or three minutes for its first start.

## Step 2. Connect to the board

You type commands on the board from your PC with PuTTY
(https://www.putty.org/).

1. Find the board's address. Try `kp4pra.local` (Raspberry Pi) or
   `orangepizero2w.local` (Orange Pi). If that does not work, look for the
   board in your router's list of connected devices and use its IP address,
   for example `192.168.1.42`.
2. In PuTTY, type the address in Host Name and click Open. Accept the
   security message the first time.
3. Log in.
   - Raspberry Pi: user `kp4pra` with the password you chose.
   - Orange Pi: user `root`, password `1234`. Armbian then asks you to pick a
     new password and to create a user. Answer its questions. If it offers to
     connect to WiFi and you have no network adapter plugged in, do so.

To paste a command into PuTTY, copy it here and right-click inside the PuTTY
window, then press Enter.

## Step 3. Install

Paste this one command:

```bash
curl -fsSL https://raw.githubusercontent.com/hebersoto/kp4pra/main/scripts/bootstrap.sh | sudo bash
```

It updates the system, installs everything KP4PRA needs (including the Dire
Wolf modem) and sets up the TNC. This takes 20 to 40 minutes. When it is done
the board restarts by itself.

You do not need to do anything for these, they are found automatically:

- **DRA-Pi-Zero:** if one is plugged in, it is found after the restart and set
  up on its own. The board restarts once or twice more while this happens
  (about 10 minutes). On the Orange Pi this also moves the board to Armbian's
  stable kernel, which the DRA driver needs.
- **Mini PiTFT display:** if one is plugged in, it is turned on.

**How do I know it is finished?** You do not have to watch the screen or type
anything. After the restart:

- The web page (step 4) shows **"Setup in progress"** at the top while the
  board is still working, and **"Setup complete"** when it is done. It keeps
  checking on its own, also while the board restarts.
- A Mini PiTFT shows **"Setting up - please wait, do not unplug"** and then
  **"Setup complete"**.
- When you log in with PuTTY, the welcome text has a **Setup:** line.

Keep the board powered on until you see "Setup complete".

## Step 4. Open the web page

Use either way:

- **From your home network:** open `http://kp4pra.local/` (Raspberry Pi) or
  `http://orangepizero2w.local/` (Orange Pi), or the board's IP address.
- **From the board's own WiFi:** when the board is not on a WiFi network it
  creates one called **KP4PRA**. Join it and open `http://172.16.0.1/`.
  The password is `qwerty1234` on a Raspberry Pi. On the Orange Pi Zero 2W
  this network has no password (see "WiFi" below).

You can open the page while setup is still running; the banner at the top
tells you when it is done. Click **Admin Dashboard**. The first time, a short
setup page opens. Enter
your callsign, check the sound card and choose a Dashboard password, then
click **Save and start**. That's it.

To connect a phone:

- Android: use the Android page in the web interface to pair your phone.
- iPhone: no pairing needed. Open aprs.fi, choose BLE KISS TNC and pick this
  device.

## If something goes wrong

- If the web page says **"Setup stopped"**, it also says what to do. Usually
  that is: run the install command from step 3 again. It is safe to repeat
  and continues where it stopped.
- Everything the installer printed is saved in `/root/kp4pra-install.log`.
- DRA-Pi-Zero: the DRA card on the Config page says what is missing. The DRA
  setup keeps its own log in `/rw/kp4pra-tnc/logs/dra-setup.log`. To run the
  DRA setup by hand:

  ```bash
  cd ~/kp4pra-tnc && sudo bash scripts/setup-dra-pi-zero.sh
  ```

- Added a DRA-Pi-Zero after installing? Plug it in, then run
  `sudo kp4pra-dra-autodetect --again`.

## Updating

Run the install command from step 3 again. It downloads the latest version
of KP4PRA (and of Xara) and installs it, then restarts the board.

**Testers:** the development version (the newest changes, less tested) is
installed and updated with:

```bash
curl -fsSL https://raw.githubusercontent.com/hebersoto/kp4pra/dev/scripts/bootstrap.sh | sudo env KP4PRA_BRANCH=dev bash
```

To go back to the normal version, run the command from step 3 again.

## VARA (Xara)

The install also sets up Xara, a VARA FM/HF modem for Winlink Express and Pat
(not on the original Pi Zero W). To use it, open the Services page and click
**Use Xara (VARA)**. Running the install command again also updates Xara.
Details: docs/XARA.md.

## WiFi: home network or hotspot

The board has one WiFi radio. It either joins your WiFi network or creates
its own KP4PRA network, not both at the same time.

- With no home WiFi set, the board starts its own KP4PRA network.
- With a home WiFi set (Config page, WiFi Client Network), the board joins
  it. If it cannot connect within 5 minutes, it starts the KP4PRA network
  instead, so you can always reach it.
- You can switch any time on the Services page, with a button on the Mini
  PiTFT (see docs/TFT_DISPLAY.md), or with `sudo kp4pra-wifi-mode ap` /
  `sudo kp4pra-wifi-mode client`.

Orange Pi Zero 2W: its WiFi chip cannot run a password-protected network, so
its KP4PRA network is open. Set a Dashboard password so nobody else can change
your settings. For a protected connection, join your phone's hotspot (as a
home WiFi network) or use a USB network adapter. The setting
`wifi.ap_security` in `/rw/kp4pra-tnc/config.yaml` controls this (`auto`,
`wpa2` or `open`).

Bluetooth (Android and iPhone) works the same in both WiFi modes.

## Manual install (for experienced users)

The one command in step 3 runs these steps. You can run them yourself
instead:

```bash
sudo apt update && sudo apt full-upgrade -y
sudo apt install -y git curl python3 python3-venv python3-pip bluez bluez-tools \
    alsa-utils avahi-daemon build-essential i2c-tools cmake libasound2-dev \
    libudev-dev libhamlib-dev gpsd libgps-dev libgpiod-dev
git clone --depth 1 https://github.com/wb2osz/direwolf.git
cd direwolf && mkdir build && cd build && cmake .. && make -j2 && sudo make install
cd ~ && git clone https://github.com/hebersoto/kp4pra.git kp4pra-tnc
cd kp4pra-tnc && sudo bash scripts/install.sh
sudo reboot
```

`direwolf --help` must list `libgpiod` (needed for the DRA-Pi-Zero's
push-to-talk). Add `-b dev` to the clone to install the development version.

The installer keeps settings in `/rw`. For a field unit you can put `/rw` on
its own partition and make the system read-only; see DEPLOYMENT.md.

## Notes for some boards

- **Original Raspberry Pi Zero W (single core, 512 MB):** it works but is
  slow. Python packages must come from piwheels (the default on Raspberry Pi
  OS). The Zero 2 W or the Orange Pi Zero 2W is a better choice.
- **Bluetooth LE on Raspberry Pi:** some Raspberry Pi kernels from June 2026
  have a bug that blocks BLE advertising. KP4PRA works around it on its own,
  and Android Bluetooth is not affected. Newer kernels have the fix, so keep
  the system updated.
- **Sound card numbers** change between boards and restarts. Always pick the
  card by name on the web page instead of by number.
