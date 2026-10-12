# DRA-Pi-Zero (Masters Communications) — I2S sound card + GPIOD PTT

Opt-in support for the DRA-Pi-Zero, an I2S daughterboard on the 40-pin
header. Unlike the CM108-class USB cards (AIOC, DigiRig Lite), it is NOT a
USB device: audio is a WM8731 codec on I2S, PTT is a relay driven by header
pin 32 using Dire Wolf's GPIOD (libgpiod) interface.

Supported boards (1.4.9): **Raspberry Pi** (all models with the 40-pin
header) and **Orange Pi Zero 2W with Armbian** - see
[Orange Pi Zero 2W](#orange-pi-zero-2w-armbian) below. The header pins are the
same on both boards; the Linux names behind them differ:

| Header pin | DRA function | Raspberry Pi | Orange Pi Zero 2W |
|---|---|---|---|
| 12/35/38/40 | I2S BCLK/LRCK/DIN/DOUT | GPIO 18/19/20/21 | PI1/PI2/PI4/PI3 |
| 3/5 | I2C to the codec | GPIO 2/3 (i2c-1) | PI8/PI7 (i2c-2) |
| 32 | PTT relay + red LED | `gpiochip0 12` | `gpiochip1 267` (PI11) |
| 36 | Green carrier-detect LED | `gpiochip0 16` | `gpiochip1 76` (PC12) |
| 29 | Blue Bluetooth LED | `gpiochip0 5` | `gpiochip1 256` (PI0) |
| - | ALSA card ID | `audioinjectorpi` | `DRAPiZero` |

The web UI, the Dire Wolf config generator and the LED service pick the
right column automatically (bin/kp4pra-dra-platform, src/common/dra_platform.py). NOTE: Dire Wolf MUST be built
with libgpiod-dev present, or GPIOD PTT crash-loops on kernel 6.x (legacy
sysfs GPIO is unreliable and can leave the line locked).

## Setup on a Raspberry Pi (web UI — recommended)

The Config page has a **DRA-Pi-Zero (I²S) Setup** card. It shows whether a
DRA is currently detected, and a **Set up DRA-Pi-Zero** button that runs a
guarded two-phase setup:

1. Click **Set up DRA-Pi-Zero** and confirm. This writes the I2S overlay,
   `dtparam=audio=off`, and HDMI-audio-off (`vc4-kms-v3d,noaudio`) to
   config.txt. **config.txt is backed up first to `config.txt.kp4pra.bak`.**
   The mixer and ADEVICE are NOT touched yet.
2. **Reboot** when ready. On boot, `kp4pra-dra-mixer.service` detects the
   codec and applies the WM8731 mixer automatically (no second manual run).
3. After reboot, the Config card shows "DRA-Pi-Zero detected". Then Detect
   sound cards, select "DRA-Pi-Zero (I2S)", and Apply (PTT pre-fills to
   GPIOD gpiochip0 12).

Safety: if no DRA is present after the reboot, the mixer service no-ops and
your existing sound configuration is unchanged. The web app can only run the
config step (narrow sudoers: `--config-only` only), never arbitrary commands.

**If the board will not boot after the config change** (worst case for any
config.txt edit): restore the backup from another machine or a recovery boot:
`cp /boot/firmware/config.txt.kp4pra.bak /boot/firmware/config.txt`.

## Setup on a Raspberry Pi (manual script — alternative)

1. Run the opt-in script (NOT part of the standard installer):

       sudo bash scripts/setup-dra-pi-zero.sh

   It adds the overlay lines to config.txt (i2c_arm, i2s, audio=off,
   audioinjector-wm8731-audio), adds the service user to the `gpio` group,
   and tells you if a reboot is required.

2. Reboot, then run the SAME script again. Second pass configures the
   WM8731 mixer and persists it with `alsactl store`.

3. Web UI -> Config -> Detect sound cards. Select the entry labeled
   "DRA-Pi-Zero (I2S)" (plughw:audioinjectorpi,0). PTT is pre-filled as
   GPIOD with parameter "gpiochip0 12". Apply + restart Dire Wolf.

## Why the mixer step matters

Radio RX audio enters the codec's MIC input (per the Masters Communications
docs), but the WM8731 driver defaults the Input Mux to Line In — recordings
succeed but are pure silence. The script sets: Input Mux=Mic, Mic capture
on, Mic Boost off, Line capture off, ADC HPF on, Sidetone 0, Output Mixer
HiFi on, Master 100 (tested: -21dB; the earlier 37 was a wrong scale that
left TX nearly silent), Capture 31 (max; RX gain is set by the R12 pot, not
ALSA). The same settings are used on both boards. The green Carrier-Detect
LED (header pin 36) is driven by a "DCD GPIOD ..." line, emitted
automatically for the I2S board with the board's chip/line.

## LEDs

| LED | Meaning |
|---|---|
| Red | PTT relay energized (Dire Wolf is transmitting) |
| Green | Carrier detect: brief flash per received packet (DCD asserts only during demodulation - a blink, not a steady glow, is correct) |
| Blue, off | Neither Bluetooth service (BLE, RFCOMM) is running, or the adapter is off |
| Blue, solid | Bluetooth service available, no client connected |
| Blue, fast flash (5 Hz) | A client is connected (BLE iPhone or RFCOMM Android); back to solid when it disconnects |

The blue LED is driven by `kp4pra-tnc-bt-led` (polls every 2 s) on every
board. On a board it does not know, it leaves the pin alone.

## Hardware settings on the board

- R12 (larger pot): RX audio level. Target ~50 in Dire Wolf's received-audio
  readout; ~2.0 V p-p input puts the pot near 50%.
- R14 (smaller pot): TX audio level (deviation).
- TX header: set to LEFT (single-channel config transmits on the left).
- RX header: 1200 for standard 1200-baud packet.
- Red LED = PTT relay energized (free PTT test indicator).
- Cables: DigiRig-pinout TRRS cables. Mobilinkd cables are NOT compatible
  (TX audio and PTT swapped).

## Interaction with the USB self-heal

`kp4pra-adevice-fix` normally waits up to 45 s for a CM108-class USB card
and rewrites ADEVICE to it. When the configured ADEVICE contains an I2S
card ID (audioinjectorpi on a Pi, DRAPiZero on an Orange Pi) it now logs a skip and exits immediately: no boot
delay, and a plugged-in USB card can NOT silently steal the config. To go
back to a USB card, just re-select it in the web UI — the self-heal
reactivates automatically because ADEVICE no longer matches an I2S card.

## Troubleshooting

- Silent RX after an image restore: mixer state lives in
  /var/lib/alsa/asound.state, not the conf — re-run the setup script.
- `arecord -D plughw:audioinjectorpi,0 -f S16_LE -r 44100 -c 1 -V mono -d 10 /tmp/t.wav`
  with radio squelch open: VU meter must move.
- PTT test: red LED on the board must light when Dire Wolf keys.
- If direwolf crash-loops with a GPIO/PTT error: the binary lacks libgpiod.
  Rebuild with libgpiod-dev present (it is in install.sh PREREQS as of 1.3.7).
- Green RX LED: flashes briefly per decoded packet (DCD asserts only during
  carrier demodulation - a brief blink, not a steady glow, is correct).
- Blue BT LED (header pin 29): see [LEDs](#leds). GPIO cannot dim from
  software; if it is too bright, fit a larger current-limiting resistor on
  pin 29 (hardware mod). The service user needs the gpio group. If the
  service fails with `status=216/GROUP`, the gpio group is missing - re-run
  the setup script (Armbian has no gpio group by default).
- GPIO permission errors in the journal: service user must be in the
  `gpio` group (setup script does this; needs service restart to apply).

## Orange Pi Zero 2W (Armbian)

Tested: Orange Pi Zero 2W, Armbian 26.8.3 (stable), kernel
6.18.44-current-sunxi64, DRA-Pi-Zero with the 12.000 MHz crystal. RX, TX,
all three LEDs, BLE (iPhone) validated on 2026-10-12.

### Requirements

- **Armbian** with Armbian's own kernel ("current" or "edge" branch). Its
  sunxi AHUB audio support is an Armbian patch - Orange Pi's vendor images
  ("Orange Pi OS", legacy 4.9/5.4/6.1 BSP kernels) use a different audio
  stack and are not supported.
- **Kernel headers** for the running kernel, from Armbian's **stable**
  repository (see below).
- **Internet** during setup and whenever a new kernel is installed (the codec
  driver source for that exact kernel is downloaded and cached).
- Only the **Zero 2W** is covered. Other H616/H618 boards (Zero 2, Zero 3)
  have different header pinouts.

### Setup (from SSH, once)

```bash
cd ~/kp4pra-tnc
sudo bash scripts/setup-dra-pi-zero.sh
sudo reboot
```

Then in the web UI Config page: **Detect sound cards**, select
**DRA-Pi-Zero (I2S)** (plughw:DRAPiZero,0). PTT pre-fills to GPIOD
`gpiochip1 267` and the green LED line (`DCD GPIOD gpiochip1 76`) is added
automatically. Preview, then Apply. The "Set up DRA-Pi-Zero" button on the
Config page only reports the status on an Orange Pi.

What the script does (six steps, all logged):

1. Installs the kernel headers that match the running kernel.
2. Installs dkms, gcc/make, device-tree-compiler, i2c-tools, gpiod.
3. Builds the KP4PRA-patched WM8731 driver as DKMS package
   `kp4pra-wm8731`: downloads `sound/soc/codecs/wm8731*` for exactly your
   kernel version (git.kernel.org, GitHub mirror as fallback), caches it in
   /var/cache/kp4pra-wm8731, applies the patches. **DKMS rebuilds it
   automatically on every kernel update** - nothing to do by hand.
4. Adds `i2c1-pi` to `overlays=` and installs the user overlay
   `kp4pra-dra-orangepi` (armbianEnv.txt is backed up once to
   armbianEnv.txt.kp4pra.bak).
5. Creates the gpio group + udev rule, adds `kp4pra` (Dire Wolf) and
   `kp4pra-tnc` (LED service) to it, installs the helpers, and adds a Dire
   Wolf pre-start I2C check (see below).
6. Applies the mixer (or schedules it for the next boot).

Re-running the script is safe; it rebuilds the driver and keeps your settings.

### Kernel headers: beta vs stable repository

Armbian images that point at `beta.armbian.com` often run "trunk" kernels
that have **no headers package**; the script then stops with a message. Move
to the stable repository and the stable kernel, then re-run:

```bash
grep -rl beta.armbian.com /etc/apt/sources.list /etc/apt/sources.list.d/
# edit that file: beta.armbian.com -> apt.armbian.com (keep a backup), then:
sudo apt update
apt-cache madison linux-image-current-sunxi64      # pick the newest stable version
V=26.8.3                                           # example
sudo apt install linux-image-current-sunxi64=$V linux-dtb-current-sunxi64=$V linux-headers-current-sunxi64=$V
sudo reboot
```

Do not install Debian's `linux-headers-*` packages - they do not match
Armbian's kernel.

### How it works (bring-up notes)

- **Codec clocking.** The DRA's WM8731 runs from its own 12.000 MHz crystal
  in USB mode and is the I2S clock master, as on the Pi. Patch 1
  (`kp4pra,xtal-12mhz`) makes the driver use the crystal and ignore the
  sunxi machine driver's 0 Hz sysclk request.
- **AHUB LRCK period.** In USB mode the codec sends 250 BCLKs per frame at
  48 kHz (125 per channel; 272/136 at 44.1 kHz). The H618's AHUB I2S
  receiver only latches frames when its LRCK period matches exactly - even as
  clock slave - and its driver can only program slot multiples (125 is
  unreachable). Patch 2 (`kp4pra,ahub-fmt0-reg`) has the codec write the
  right period into AHUB I2S0 FMT0 during hw_params. Symptom without it:
  `arecord` fails with `Input/output error` after ~0.6 s.
- **Pin states.** The AHUB driver applies its "default" pins only at probe
  and ASoC parks idle devices in "sleep", so the overlay's sleep state uses
  the same I2S mux. PI0 (I2S MCLK) is deliberately left free - it drives
  the blue LED.
- **I2C controller lock-up.** The SoC's I2C controller can get stuck
  reporting `mv64xxx: I2C bus locked` with idle wires; every codec write
  then times out (~2 s each) and Dire Wolf's start stalls ~30 s with no
  RX/TX. `kp4pra-dra-i2c-check` runs before every Dire Wolf start (systemd
  drop-in `direwolf.service.d/kp4pra-dra-i2c.conf`), probes the codec and,
  if needed, resets the controller and restores the mixer. Run it by hand
  with `sudo kp4pra-dra-i2c-check`; results are in
  `journalctl -u direwolf`.

### Troubleshooting (Orange Pi)

- `dmesg | grep KP4PRA` must show both `fixed 12 MHz crystal` and
  `AHUB LRCK period fixup at 0x05097204`. If not: `dkms status` should list
  `kp4pra-wm8731/<version>, <kernel>: installed`.
- `aplay -l` must list `card N: DRAPiZero`. 10 s capture test:
  `arecord -D hw:DRAPiZero,0 -f S16_LE -r 48000 -c 2 -d 10 /tmp/t.wav`
  (must finish in 10 s, file 1920044 bytes).
- Dire Wolf exits with `Failed to request GPIO line` on `gpiochip0`: the
  config still has the Raspberry Pi PTT line. Re-select the DRA in the web
  UI and Apply (a Pi value is remapped to `gpiochip1 267` automatically).
- Slow Dire Wolf start / `I2C bus locked` in dmesg: `sudo kp4pra-dra-i2c-check`.
- After a kernel update the DRA is gone: the update ran without internet, so
  DKMS could not fetch the new driver source. Connect and run
  `sudo dkms autoinstall`, then reboot.
