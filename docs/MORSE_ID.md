# Morse-Code Station ID (ACT LED)

When the TNC is fully operational, KP4PRA TNC blinks the configured
station callsign in Morse code on the Raspberry Pi's green ACT LED,
every 15 minutes, at 10 WPM.

## What it does

- Reads the callsign from `station.callsign` in the config.
- Every 15 minutes, if the TNC is fully operational, it blinks that
  callsign in Morse on the green ACT LED, then restores the LED's normal
  SD-card-activity indication.
- "Fully operational" means Dire Wolf is running with a working audio
  device AND at least one KISS bridge (BLE or RFCOMM) is running. If the
  TNC is not fully operational (e.g. the USB sound card has not
  enumerated), the ID is skipped - the absence of the periodic blink is
  itself a useful "not ready" indicator.

## Timing

10 WPM, PARIS standard (1 unit = 120 ms): dot = 1 unit, dash = 3 units,
gap between elements = 1 unit, gap between letters = 3 units.

## Components

- `bin/kp4pra-morse-id` - the ID script (health-gated).
- `systemd/kp4pra-morse-id.service` - oneshot that runs the script.
- `systemd/kp4pra-morse-id.timer` - fires every 15 minutes.

## Enable / disable

The feature is **enabled by default** (auto-enabled by the installer).

**Everyday control - the web Config page:** the "Morse callsign ID on the
ACT LED" toggle in the Config page turns it on or off. The timer keeps
running; the ID script reads this toggle each time it fires, so changes
take effect on the next 15-minute cycle with no restart or shell access.
This maps to `station.morse_id_enabled` in the config.

**Remove entirely (shell):** to stop the timer from firing at all:

    sudo systemctl disable --now kp4pra-morse-id.timer   # stop entirely
    sudo systemctl enable --now kp4pra-morse-id.timer    # restore

## Test on demand

    sudo /usr/local/bin/kp4pra-morse-id

Blinks the callsign immediately if operational, or prints a skip message
if not. Watch the green ACT LED.

## Notes

- Controlling the ACT LED temporarily overrides its SD-activity function
  for the few seconds the ID takes; the previous trigger is restored
  afterward.
- LED control requires root, which is why the service runs as root.
