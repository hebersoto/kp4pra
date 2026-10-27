# Xara VARA modem

[Xara](https://github.com/x0rro/xara) is a free VARA FM / VARA HF compatible
modem for Linux, written by x0rro (MIT license). With Xara, your KP4PRA TNC
can be the VARA modem for Winlink Express, Pat and other programs that talk to
VARA over the network.

KP4PRA does not change Xara's code. It installs Xara with **registered VARA
speeds** turned on (see "Registered speeds" below) and keeps it up to date.

## One modem at a time

Xara and Dire Wolf use the same radio and the same sound card, so only one of
them runs at a time:

- **Dire Wolf (packet)** is the normal mode: APRS, Bluetooth and BLE apps
  (APRSDroid, aprs.fi), the RMS gateway.
- **Xara (VARA)** is for VARA. While it runs, Dire Wolf is stopped, so there
  is no APRS, no Bluetooth/BLE KISS traffic and no RMS gateway.

You switch on the **Services** page. The choice stays after a restart.

## Installing

Nothing to do in most cases. The install command (INSTALL.md, step 3) also
downloads Xara when there is a ready-made Xara for your board:

- Orange Pi Zero 2W, Raspberry Pi Zero 2 W, Pi 3, Pi 4, Pi 5: yes
  (64-bit and 32-bit systems).
- Original Raspberry Pi Zero W: no. It is too slow for VARA.

The download is checked against the checksum file that comes with it. If it
does not match, nothing is installed.

If Xara was not installed (no internet at the time, for example), click
**Install Xara** on the Services page, or type:

```bash
sudo kp4pra-xara install
```

## Using Xara

1. **Config page, "Xara VARA Modem" card:** pick the VARA mode (FM narrow is
   what most FM Winlink stations use) and click **Save Xara settings**.
   Xara uses the sound card and push-to-talk you set in Station Information,
   the same as Dire Wolf. A DRA-Pi-Zero works too.
2. **Services page, "VARA Modem (Xara)" card:** click **Use Xara (VARA)**.
3. In your Winlink program, point the VARA modem at the board instead of a
   VARA program on your PC. The Services page shows the address to use while
   Xara runs.
   - **Winlink Express:** open a VARA FM (or VARA HF) session, then Settings,
     VARA TNC Setup. Set the TNC address to the board's IP address and the
     port to **8300**. Leave the box that starts VARA on this computer
     unchecked.
   - **Pat:** in Pat's config, set the `varafm` (or `varahf`) `addr` to
     `<board IP>:8300`.
4. To go back to APRS and Bluetooth, click **Back to Dire Wolf (packet)**.

Xara has its own status page with a waterfall: **Open Xara page** on the
Services page (port 8080). It only shows information; you change settings on
the KP4PRA Config page.

Ports while Xara runs: 8300 (VARA commands), 8301 (VARA data), 8100 (VARA
KISS), 8080 (Xara page). To keep VARA programs on other devices out, set
"VARA apps on other devices" to "Only on this board".

## Registered speeds

VARA's faster speeds are for stations with a VARA registration. Xara has them
only when it is built with its "registered" option, and Xara's own downloads
are built without it. So that no board ever has to build Xara (that takes up
to an hour and a half on a Zero 2W), the KP4PRA repository builds it:

- A GitHub workflow in the KP4PRA repo (Actions, "Registered Xara builds")
  checks every Monday for a new Xara version. When there is one, it builds
  Xara from Xara's unchanged source with the registered option, for every
  board KP4PRA supports, and publishes it on the KP4PRA repo as a release
  named `xara-v<version>`. It can also be started by hand (Run workflow).
- Boards download that build like any update. Each version is built once.
- If a new Xara version is out but its registered build is not published
  yet, the board stays on its current registered version until it is.
- On a brand-new install, before any registered build exists, the board gets
  Xara's standard build and switches to the registered one with the next
  update.

Registered speeds are on by default. To use standard speeds, set **VARA
speeds** to "Standard" on the Config page. The Config page and the Services
page show whether the installed Xara has registered speeds.

## Updating Xara

Any of these gets the newest Xara release:

- Run the install command from INSTALL.md again (it updates everything).
- Services page: **Check for update**.
- `sudo kp4pra-xara update`

If Xara is running, it restarts on the new version by itself. The previous
version stays on the board. To go back to it:

```bash
sudo kp4pra-xara rollback
```

To stay on one version, set **Xara version** on the Config page (under
"Ports and version") to that version, for example `0.1.1`. Set it back to
`latest` to follow new releases again.

## Push-to-talk

Xara keys the radio the same way as Dire Wolf, using the PTT setting in
Station Information:

| Station PTT         | Xara uses                                     |
|---------------------|-----------------------------------------------|
| CM108 (DigiRig, AIOC, most USB sound cards) | the CM108 GPIO, found each time Xara starts |
| GPIOD (DRA-Pi-Zero) | KP4PRA's small PTT helper (kp4pra-xara-ptt)   |
| RTS / DTR           | the serial port in PTT parameter              |
| VOX (Signalink)     | VOX                                           |
| RIG                 | your own rigctld (PTT parameter host:port)    |
| None                | receive only                                  |

Xara does not drive GPIO pins itself, so for the DRA-Pi-Zero KP4PRA runs a
helper that Xara talks to like a rigctld. The helper lets go of PTT if Xara
stops or if PTT stays on for more than 2 minutes.

## If something goes wrong

- **"Xara did not start - going back to Dire Wolf":** the Services page shows
  Xara's messages under "Xara log". The board is back on Dire Wolf, so
  nothing else is affected.
- **No transmit:** check the PTT line on the Services page ("Mode / PTT"). For
  a USB sound card it must say CM108 with a /dev/hidraw device.
- **Too much or too little audio:** lower or raise "TX audio level" on the
  Config page and save.
- From the command line:

  ```bash
  kp4pra-xara status
  journalctl -u xara@kp4pra -n 50
  sudo kp4pra-xara modem packet      # back to Dire Wolf
  ```

## Commands

| Command                              | What it does                               |
|--------------------------------------|--------------------------------------------|
| `sudo kp4pra-xara install [VERSION]` | install Xara (newest release by default)   |
| `sudo kp4pra-xara update [VERSION]`  | upgrade (or change) the Xara version       |
| `sudo kp4pra-xara rollback`          | go back to the previous version            |
| `sudo kp4pra-xara modem xara`        | switch to Xara                             |
| `sudo kp4pra-xara modem packet`      | switch to Dire Wolf                        |
| `kp4pra-xara status`                 | what is installed and running              |
| `kp4pra-xara latest`                 | newest Xara version on GitHub              |
| `sudo kp4pra-xara uninstall`         | remove Xara (settings are kept)            |

`--build` (install/update) builds Xara on the board itself, only for special
cases: a board with no ready-made build, or no internet access to GitHub. It
takes a long time (30 to 90 minutes on a Zero 2W) and needs Rust 1.85 or
newer, which it installs if needed. With registered speeds on, it builds the
registered version. Normal installs and updates never build.

## How it fits together (for developers)

- Downloads come from `github.com/hebersoto/kp4pra` releases `xara-v<version>`
  (registered builds, made by `.github/workflows/xara-registered.yml`; each
  has a `BUILD-FEATURES` file saying `registered`), or from Xara's own
  releases (`github.com/x0rro/xara`, tag `v<version>`) when registered
  speeds are off or no registered build exists yet. Both are checked
  against their `SHA256SUMS`.
- `/opt/xara/<version>/` holds each release as published (binaries, `dist/`).
  `/opt/xara/current` points at the one in use; `/usr/local/bin/xara` points
  into it. Two versions are kept.
- Xara's own `xara@.service`, udev rule and system user come from the
  release's `dist/` folder and are installed again on every upgrade, so
  changes in new Xara versions arrive with them. KP4PRA's additions are only
  in `/etc/systemd/system/xara@kp4pra.service.d/kp4pra.conf`.
- `kp4pra-xara-prep.service` writes `/rw/xara/kp4pra.conf` from config.yaml
  (`src/common/xara_conf.py`) right before Xara starts.
  `/etc/xara/kp4pra.conf` links to it (the root filesystem may be read-only).
- `/var/lib/xara` (Xara's state) is a bind mount of `/rw/xara/state`.
- The chosen modem is in `/rw/kp4pra-tnc/modem`; `kp4pra-xara-boot.service`
  starts Xara at boot when it says `xara`.
- `registered = on` is written to Xara's config only when the installed
  build has registered speeds (Xara refuses the option otherwise).
- Settings: the `xara:` section of `/rw/kp4pra-tnc/config.yaml`.
