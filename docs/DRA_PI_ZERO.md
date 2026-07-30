# DRA-Pi-Zero (Masters Communications) — I2S sound card + GPIOD PTT

Opt-in support for the DRA-Pi-Zero, an I2S daughterboard on the 40-pin
header. Unlike the CM108-class USB cards (AIOC, DigiRig Lite), it is NOT a
USB device: audio is a WM8731 codec on I2S, PTT is a relay driven by GPIO 12
using Dire Wolf's GPIOD (libgpiod) interface. NOTE: Dire Wolf MUST be built
with libgpiod-dev present, or GPIOD PTT crash-loops on kernel 6.x (legacy
sysfs GPIO is unreliable and can leave the line locked).

## Setup (web UI — recommended)

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

## Setup (manual script — alternative)

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
ALSA). The green Carrier-Detect LED (GPIO 16) is driven by a
"DCD GPIOD gpiochip0 16" line, emitted automatically for the I2S board.

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
card ID (audioinjectorpi) it now logs a skip and exits immediately: no boot
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
- Blue BT LED (GPIO 5): on while a phone is CONNECTED (BLE or RFCOMM),
  off otherwise. Driven by the kp4pra-tnc-bt-led service (polls
  `bluetoothctl devices Connected` every 3s). On/off only - GPIO cannot
  dim from software; if it is too bright, fit a larger current-limiting
  resistor on GPIO 5 (hardware mod). The service user needs the gpio group.
- GPIO permission errors in the journal: service user must be in the
  `gpio` group (setup script does this; needs service restart to apply).
