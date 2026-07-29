# APRS clock fallback

The APRS clock fallback keeps the KP4PRA TNC system clock roughly correct when
network time is unavailable. NTP remains the primary time source at all times.

## Configuration

Use the existing station field; no second clock-source setting is added:

```yaml
station:
  clock: "KP3M-5"
```

The value must match the source callsign **including SSID**. A blank value
completely disables the feature.

The configured station must actually transmit timestamped APRS position or
object reports. Many APRS packets contain no timestamp. Untimestamped packets
provide no usable time and are ignored.

Supported timestamp forms:

- `DDHHMMz` — day/hour/minute in UTC
- `HHMMSSh` — hour/minute/second in UTC
- `DDHHMM/` — day/hour/minute in the sender's local time

For `/` local timestamps, the reference station and KP4PRA TNC must use the
same local time zone.

## Important date limitation

APRS does not provide a reliable year and sometimes provides no date at all.
The service therefore uses **only the APRS time-of-day** and retains the
board's current date. The APRS day-of-month is validated but is not applied.

This feature corrects ordinary time drift. It cannot repair a grossly wrong
system date. Set the correct date during provisioning or from another trusted
source.

## Receive path

The service reads received frames from Dire Wolf's existing AGW TCP server:

- Host: existing `direwolf.host` value, normally `127.0.0.1`
- AGW port: `8000`
- Existing KISS port: `direwolf.port`, normally `8001`

The service sends only the AGW control request that enables raw received-frame
monitoring for its own TCP connection. It never asks Dire Wolf to transmit.

AGW is deliberately used instead of opening another KISS connection. KP4PRA
already uses KISS for RMS, BLE, and RFCOMM, and Dire Wolf has a small KISS
client limit. The clock listener therefore does not occupy or compete for a
KISS client slot.

It never opens the sound device or radio, starts another Dire Wolf process, or
transmits an AX.25 packet.

## Operation and safety

The service:

1. Checks `timedatectl` every 30 seconds.
2. Requires three consecutive unsynchronized checks, approximately 90 seconds,
   before APRS fallback becomes eligible.
3. Connects to the existing AGW server only after sustained NTP loss.
4. Reuses the project's existing AX.25 decoder from `src/rms/ax25.py`.
5. Accepts only APRS UI frames whose source exactly matches `station.clock`.
6. Steps the clock only when the shortest time-of-day difference is more than
   120 seconds. Midnight wraparound is handled correctly.
7. Enforces a 300-second cooldown between successful clock steps.
8. Rechecks NTP immediately before the privileged clock-setting action.
9. Disconnects from AGW and returns to idle when NTP recovers.

Stepping time backward can disturb journal ordering, systemd timers, TLS,
Morse-ID timers, and RMS/Winlink session timing. The threshold, sustained NTP
loss check, final NTP recheck, and cooldown reduce this risk but cannot remove
it completely.

## Privilege model

The daemon runs as `kp4pra-tnc`. Its separate sudoers file permits only:

`/bin/date --set` is used rather than `timedatectl set-time`. `timedatectl`
refuses a manual time change while automatic NTP remains enabled, even if the
clock is currently unsynchronized. Using `date` allows NTP to stay enabled so
it can immediately resume as the primary source when connectivity returns.

```text
/bin/date --set *
```

The installer sets mode `0440` and validates the file using `visudo -c -f`.
The systemd unit intentionally does not use `NoNewPrivileges=true`, because
that setting blocks the narrowly authorized sudo action.

## Status and troubleshooting

```bash
systemctl status kp4pra-tnc-aprs-clock.service
journalctl -u kp4pra-tnc-aprs-clock.service -f
cat /run/kp4pra-tnc/aprs_clock.json
```

Useful checks:

```bash
timedatectl show -p NTPSynchronized --value
grep -A15 '^station:' /rw/kp4pra-tnc/config.yaml
ss -ltnp | grep ':8000'
sudo -u kp4pra-tnc sudo -n /bin/date --set "$(date '+%F %T')"
```

The last command tests the privilege path using approximately the current time
and may update the RTC. Do not run it during an active RMS/Winlink session.

## Hardware validation checklist

Perform these tests on a development board before merging into `main`:

1. With NTP synchronized, verify the service remains `ntp-primary`, does not
   connect to AGW, and does not set the clock.
2. Configure the exact reference callsign, isolate internet/NTP, and wait for
   three failed synchronization checks.
3. Confirm the service attaches to AGW port 8000 and does not add a client to
   KISS port 8001.
4. Confirm packets from every other callsign are ignored, including the same
   base callsign with a different SSID.
5. Hear a timestamped packet from the configured station with a delta greater
   than 120 seconds and verify one clock step occurs.
6. Repeat with a delta of 120 seconds or less and verify no step occurs.
7. Test `23:59:50` versus `00:00:10`; verify the delta is 20 seconds and no
   step occurs.
8. Verify the five-minute cooldown prevents repeated steps.
9. Verify BLE, RFCOMM, RMS, and an external KISS client continue normally while
   the AGW clock listener is connected.
10. Restore internet and verify NTP recovery returns the service to idle and
    closes the AGW connection.

Do not mark the feature hardware-validated until all checks pass on the target
Raspberry Pi or Orange Pi.
