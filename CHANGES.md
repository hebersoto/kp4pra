# KP4PRA TNC — Changes applied in this bundle (v2)

All fixes discovered and validated during on-device bring-up on
Orange Pi Zero 2W / Debian trixie / Python 3.13 / BlueZ 5.7x.

## RFCOMM bridge (src/rfcomm/rfcomm_bridge.py) — rewritten
- PyBluez removed (unmaintained, fails to build on Python 3.13).
  Uses built-in socket with AF_BLUETOOTH/BTPROTO_RFCOMM.
- Bind address must be "00:00:00:00:00:00" (BDADDR_ANY), not "".
- SDP SPP record registered via sdptool (bluez-tools); bluetoothd
  runs with -C compat mode (installed by install.sh).
- FIX: Dire Wolf socket timeout cleared after connect. The connect
  timeout persisted into recv(), killing the link after any 5s quiet
  period ("KISS client ... has gone away").
- Dire Wolf reconnects WITHOUT dropping the RFCOMM client.
- listen(2) + 1s accept poll: fast client reconnects, no refused
  connections during teardown.

## BLE bridge (src/ble/ble_bridge.py)
- FIX: D-Bus 'ay' properties must return bytes, not list
  (SignatureBodyMismatchError broke GATT registration).
- FIX: notifications now emitted via emit_properties_changed on OUR
  exported RX characteristic. Previous code called Set on org.bluez,
  which fails ("interface not found") — TNC→app direction was dead.
- FIX: Dire Wolf socket timeout cleared after connect (same 5s idle
  bug as RFCOMM).
- FIX: fast shutdown via os._exit(0) — loop.stop() left executor
  threads alive, hanging every stop for 90s until SIGKILL.
- TNC->app transfers now logged in verbose mode.
- Known cosmetic: a TxPower UNKNOWN_PROPERTY traceback logs once per
  advertisement registration; harmless (optional property probe).

## Bluetooth management (src/common/bluez_manager.py)
- Device listing uses 'devices Paired'/'devices Trusted' (BlueZ >=
  5.65 removed paired-devices/trusted-devices) with fallback.
- pair_device relies on the system-wide NoInputNoOutput agent
  (kp4pra-tnc-agent.service) => Just Works pairing, no passkey.
  Success verified against actual device state.
- trust/untrust verified against device state, not output text.
- verify_bluez_state_written is non-fatal on permission errors.

## Provisioning workflow (src/web/web_app.py)
- Pair automatically trusts in the same pass ("Pair & Trust").
- BlueZ state group permissions repaired after every action
  (BlueZ recreates bond files root:root 600).
- No reboot when the filesystem was already writable (dev mode);
  reboot only when a remount was actually needed.
- Verification failures no longer abort a successful pairing.
- All provisioning endpoints return JSON even on internal errors.

## System integration
- NEW bin/kp4pra-remount-rw, bin/kp4pra-remount-ro: sudoers cannot
  parse "mount -o remount,rw /", so remount goes through helpers.
- NEW bin/kp4pra-fix-bt-perms + kp4pra-bt-perms.service (boot) and
  in-workflow call: keeps /var/lib/bluetooth group-readable.
- NEW systemd/kp4pra-tnc-agent.service: bt-agent NoInputNoOutput
  (Just Works pairing for phones).
- sudoers.d/kp4pra-tnc rewritten: helper-script based, validated.
- RuntimeDirectory= removed from units (raced with ownership);
  replaced by /etc/tmpfiles.d/kp4pra-tnc.conf.
- install.sh: PROJECT_DIR path fix; pinned pip versions
  (fastapi==0.115.5, starlette==0.41.3, jinja2==3.1.4 — fixes the
  "unhashable type: dict" template cache crash); installs helpers,
  agent, perms service, tmpfiles rule, bluetoothd -C drop-in;
  preflight checks for python3-venv and bluez-tools.

## Configuration
- NEW station section (config.yaml + Config page): callsign, SSID,
  grid, lat/lon, sound card (dropdown: CM108/Signalink/USB CP210x),
  PTT (dropdown: CM108/VOX/RTS/DTR/GPIO/RIG/NONE), clock source,
  CDIGIPEAT alias + SSID. Validated on save.
- DNS-SD instance name is now informational: Dire Wolf publishes the
  advertisement itself (use DNSSDNAME in direwolf.conf); remove
  /etc/avahi/services/kiss-tnc.service if Dire Wolf advertises.

## Deployment notes (not in install.sh)
- Port 80 -> 8088 redirect: apt install nftables, add "table ip nat"
  prerouting rule "tcp dport 80 redirect to :8088" to
  /etc/nftables.conf, enable nftables.service.
- journald volatile config and read-only-root fstab remain as
  documented in README.md.
