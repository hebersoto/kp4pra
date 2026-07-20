# USB Sound Card Notes (DigiRig Lite, CM108, and similar)

Findings and troubleshooting for USB audio interfaces used as the Dire Wolf
sound device on KP4PRA TNC. From field testing on a Raspberry Pi 3 A+ with a
DigiRig Lite; applies to any CM108-class USB audio adapter.

## How KP4PRA TNC picks the sound device

Dire Wolf needs an ADEVICE line in direwolf.conf pointing at the audio device
(e.g. ADEVICE plughw:Device,0). Because USB audio devices enumerate
unpredictably, KP4PRA TNC does NOT hardcode this. At boot, kp4pra-adevice-fix
(the self-heal) runs before direwolf.service:

1. Waits up to 45 seconds for a CM108-class card to enumerate.
2. Runs cm108 to find the device and derive ADEVICE, matching on any
   /dev/hidraw* and preferring the stable name form (plughw:Device,0) over the
   volatile number form (plughw:1,0).
3. Writes that value into direwolf.conf, then Dire Wolf starts.

The web UI (Config > Sound Card > Detect) uses the same detection.

## Two distinct failure modes

### 1. SLOW enumeration (software handles this)

On cold boot the USB card can take 20-40 seconds to appear. Field logs showed a
DigiRig enumerating about 27 seconds after power-on. Without the wait, Dire Wolf
starts first, finds no audio device, and logs "Could not open audio device" /
"Pointless to continue without audio device." Dire Wolf restarts every 2s, so
once the card appears it recovers on the next retry.

If RF is dead right after a cold boot, WAIT about 60 seconds before doing
anything. It commonly starts working on its own once the card finishes
enumerating.

### 2. NO enumeration (hardware - software cannot fix)

Sometimes the USB card does not enumerate at all on cold boot. /proc/asound/cards
shows no soundcards, cm108 produces no output, and the self-heal waits the full
45s finding nothing. No software can use a device the USB layer never brought up.

Field symptom: after power-on the phone shows the TNC connected over Bluetooth,
but nothing transmits on RF, because there is no audio device for Dire Wolf to
key the radio through. Physically unplugging and reconnecting the USB interface
forces enumeration and it starts working.

This is a hardware / USB-power issue, most likely on single-USB, power-limited
boards (Pi 3 A+, Pi Zero) and worse from vehicle power supplies that ramp slowly
or noisily at power-on.

## Recommended fixes for the no-enumeration case

In order of effectiveness:

1. Powered USB hub (Pi > powered hub > DigiRig). Gives the device stable power
   independent of the Pi's port. Most reliable cure for the "needs a replug on
   cold boot" symptom.
2. Known-good USB cable / reseat the connector. If a replug fixes it, the cable
   or connector is marginal.
3. Better / cleaner power supply, especially in a vehicle.
4. config.txt USB current (marginal): max_usb_current=1 in
   /boot/firmware/config.txt raises available port current. Minor help vs a hub.

## Optional: onboard/HDMI audio

Disabling onboard analog and HDMI audio removes competing sound cards. NOT
required (name-based detection finds the USB card regardless) but reduces
clutter. On Raspberry Pi OS, in /boot/firmware/config.txt:

    dtparam=audio=off
    dtoverlay=vc4-kms-v3d,noaudio

Manual, board-specific step; the installer does not do it.

## Quick diagnosis

    cat /proc/asound/cards
    cm108
    grep ADEVICE /rw/kp4pra-tnc/direwolf.conf
    journalctl -u direwolf.service -b | grep -iE "audio|Pointless"
    journalctl -u kp4pra-adevice.service -b

If /proc/asound/cards shows no soundcards, it is the hardware no-enumeration
case - reseat/replug the USB device or add a powered hub.
