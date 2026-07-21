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


## 2026-07 — Kernel MGMT advertising regression and workaround

Finding: a June-2026 Linux kernel patch (Bluetooth: MGMT: validate Add
Extended Advertising Data length) causes bluetoothd advertisement
registration to fail with Invalid Parameters (0x0d). Present in Raspberry
Pi OS kernel 6.18-rpt and backported into 6.12.9x stable, so all current
Raspberry Pi OS images (Trixie and Bookworm) are affected on every Pi
model. Diagnosed with btmon: MGMT Add Extended Advertising Parameters
succeeds, Add Extended Advertising Data (valid 3-byte flags payload) is
rejected. Armbian kernels (Orange Pi reference unit) unaffected.

Changes:
- ble_bridge.py: advertisement registration now has a three-stage
  strategy: full advertisement -> minimal advertisement (UUID only,
  no tx-power, no name) -> legacy raw-HCI advertising via
  /usr/local/bin/kp4pra-legacy-adv (flags + 128-bit KISS service UUID
  in ADV data, device name in scan response, connectable undirected).
  Legacy advertising is re-asserted after each client disconnect,
  because raw leadv stops when a connection is accepted.
- If all three stages fail, the bridge logs a clear explanation and
  exits cleanly (exit 0) instead of crash-looping; Android/RFCOMM is
  unaffected either way.
- Removed tx-power from the advertisement Includes (rejected by some
  controllers; also eliminates the harmless TxPower property-probe
  traceback seen on every BLE start).
- New bin/kp4pra-legacy-adv helper + sudoers allowlist entry;
  install.sh installs both.

Revisited verdict: the earlier conclusion that the Pi Zero W Rev 1.1
hardware does not support BLE advertising was premature - that board
runs an affected kernel. Its legacy btmgmt advertising toggle works, so
the raw-HCI fallback is expected to function there; chip-level support
remains unconfirmed until tested on a healthy kernel.

## 2026-07-09 - Pi 3 B+ validation and NoNewPrivileges findings

Validated end-to-end on Raspberry Pi 3 B+ (Bookworm, kernel 6.12.93,
affected by the MGMT regression): automated install with chained stage 2,
port-80 redirect, Dire Wolf, legacy raw-HCI BLE fallback with working
iPhone traffic, and Android/RFCOMM provisioning.

Findings and fixes:
- ProtectKernelModules/ProtectKernelTunables/ProtectControlGroups imply
  NoNewPrivileges for non-root services and CANNOT be overridden by an
  explicit NoNewPrivileges=false. This silently broke every sudo call in
  the web service (restart, remount, pairing). Removed from the web unit
  with a warning comment.
- BLE bridge no longer uses sudo for the legacy-adv helper; the BLE unit
  grants AmbientCapabilities=CAP_NET_ADMIN CAP_NET_RAW instead, which
  coexists with NoNewPrivileges. The legacy-adv sudoers entry is removed.

## 2026-07-09 - Pi 3 B+ validation and NoNewPrivileges findings

Validated end-to-end on Raspberry Pi 3 B+ (Bookworm, kernel 6.12.93,
affected by the MGMT regression): automated install with chained stage 2,
port-80 redirect, Dire Wolf, legacy raw-HCI BLE fallback with working
iPhone traffic, and Android/RFCOMM provisioning.

Findings and fixes:
- ProtectKernelModules/ProtectKernelTunables/ProtectControlGroups imply
  NoNewPrivileges for non-root services and CANNOT be overridden by an
  explicit NoNewPrivileges=false. This silently broke every sudo call in
  the web service (restart, remount, pairing). Removed from the web unit
  with a warning comment.
- BLE bridge no longer uses sudo for the legacy-adv helper; the BLE unit
  grants AmbientCapabilities=CAP_NET_ADMIN CAP_NET_RAW instead, which
  coexists with NoNewPrivileges. The legacy-adv sudoers entry is removed.

## 2026-07-09 - Zero W BLE operational; file-capabilities mechanism

The Pi Zero W Rev 1.1 (BCM43438) now runs BLE fully: legacy raw-HCI
fallback advertising, iPhone aprs.fi connect/traffic/reconnect all
confirmed. The earlier "hardware not supported" verdict was wrong - the
failures were the kernel MGMT regression throughout.

Mechanism change: ambient capabilities on the unit proved unreliable
across the Python-to-helper process chain on some kernels (worked on
Bookworm/6.12 + 3B+, EPERM on Trixie/6.18 + Zero W despite identical
grants; bare systemd-run with the same caps succeeded). Replaced with
file capabilities on private tool copies: install.sh copies hcitool and
hciconfig to /usr/local/lib/kp4pra/ with cap_net_admin,cap_net_raw+ep,
and kp4pra-legacy-adv invokes those. Works under full unit hardening,
no sudo, no ambient-cap inheritance. The legacy-adv sudoers entry is
obsolete and removed.


## 2026-07-11 - WiFi access point mode (Pi Zero 2 W validation)

New field-mode feature: kp4pra-wifi-mode switches wlan0 between client
WiFi and a KP4PRA hotspot (NetworkManager AP profile, ipv4 shared =
built-in DHCP via dnsmasq, web UI at http://172.16.0.1/). Validated on
Pi Zero 2 W. Hard-won parameters: explicit RSN/CCMP and PMF DISABLED
(brcmfmac AP mode times out in 802.1X setup with PMF), pinned channel,
rfkill unblock + regulatory-domain pre-flight (fresh images ship WiFi
soft-blocked / country unset). wifi: section in config.yaml (ssid,
password, channel, mode_at_boot; boot default client). Boot service
kp4pra-wifi-mode.service applies mode_at_boot. Web-UI control card:
queued for the capability-gating web pass. Note: AP mode disconnects
the board from client WiFi (single radio) - use the Ethernet dongle or
the AP itself for management during field configuration.

## 2026-07-11 - Headless hotspot fallback at boot

kp4pra-wifi-mode boot now starts the AP automatically when no client
WiFi connection profile exists (image flashed without WiFi credentials)
- out-of-box access is: power on, join KP4PRA, browse 172.16.0.1.
Explicit mode_at_boot settings still win. Considered but not yet
implemented: timeout-based fallback when configured WiFi is unreachable.


## 2026-07-12 - Zero 2 W full validation session

Board identity: Raspberry Pi Zero 2 W Rev 1.0 (devicetree-confirmed;
silkscreen easily misread as original Zero W). Raspberry Pi OS Lite
32-bit, Debian 13 trixie, kernel 6.18.34-v7 (MGMT-regression kernel -
legacy fallback in use throughout).

Validated: BLE incl. iPhone traffic, 20h advertising soak, and
WiFi-AP + BLE concurrency; KP4PRA hotspot; Android provisioning
end-to-end from both the wizard and the Bluetooth Management page,
including the remove -> reboot -> clean-recovery path.

Findings fixed during the session (committed separately):
- kp4pra-tnc-agent.service inactive broke non-interactive pairing
  (workflow needs an agent preflight - queued for web pass).
- Installed sudoers lacked fix-bt-perms and wifi-mode lines vs repo.
- Legacy-adv scan response advertised "KP4PRA TNC" in hardcoded hex;
  now "KP4PRA" to match the adapter alias and MGMT LocalName.
- remove_device used an interactive bluetoothctl session that echoed
  commands without executing them - broken since inception on all
  boards; rewritten argument-style with state verification, along
  with disconnect_device.

## v1.0.1 - 2026-07-15

Fixes:
- Agent service: TimeoutStopSec=5 - bt-agent ignores SIGTERM when D-Bus
  is torn down first at shutdown, previously hanging reboots ~90s.
- Shipped config template: station section blanked to N0CALL/0.0
  sentinels (personal station data removed from the example).
- WiFi: new client_ssid/client_password config; blank client SSID boots
  straight to AP mode; a configured-but-unreachable client network falls
  back to AP after 5 minutes so the unit is never unreachable.
  Documented in INSTALL.md section 6b.

## v1.0.2 - 2026-07-15

Fixes:
- Web UI port 80 redirect: added a persistent redirect service
  (bin/kp4pra-web-redirect + kp4pra-web-redirect.service) that maps
  tcp/80 -> 8088 on all interfaces. This was configured by hand on
  early boards but never captured in install.sh, so fresh installs had
  no port-80 access. The rule lives in its own nft table (ip kp4pra) so
  NetworkManager's AP-mode ruleset does not clobber it; verified to
  coexist with the KP4PRA hotspot.

## v1.1.0 - 2026-07-15

Features:
- Web UI WiFi controls: Config page gains a WiFi Client Network card
  (client SSID/password); Services page gains a WiFi Mode toggle to
  switch between AP hotspot and client mode. New /api/wifi/status and
  /api/wifi/mode endpoints; client mode is refused when no client SSID
  is configured. Client-password validation (8-63 chars or blank).

## v1.1.1 - 2026-07-15

Docs:
- DEPLOYMENT.md: full Raspberry Pi OS support. Part A warns the Armbian
  mkfs steps destroy a Pi's root (p1=boot, p2=root, auto-expanded) and
  gives safe /rw options. Sections reordered (B2/B3 before C). Golden
  image geometry-agnostic (reads actual end sector, no hardcoded size).
  ro-flip PARTUUID-aware for Pi; Part E mounts p2 on Pi.

## v1.1.2 - 2026-07-15

Fixes:
- Installer: install-direwolf-integration.sh now seeds a minimal
  direwolf.conf and ALWAYS creates the /home/kp4pra/direwolf.conf
  symlink. Previously the symlink was only created when a conf already
  existed, so fresh installs (following current INSTALL.md, which has no
  manual-creation step) had no conf and Dire Wolf failed to start (stuck
  in 'activating'). The web UI overwrites the seed on first station save.

## v1.1.3 - 2026-07-15

Fixes:
- Services page: WiFi Mode card moved inside the two-column grid so it
  matches the width of the other service cards (was rendering full-width).

## v1.1.4 - 2026-07-15

Fixes:
- AP mode: default SSID to KP4PRA when wifi.ssid is blank in config.
  Fresh installs ship wifi.ssid: '' (present but empty); the config
  reader returned that empty string rather than the KP4PRA default,
  so nmcli refused the connection and AP mode failed. Now guarded.

## v1.1.5 - 2026-07-19

Fixes (USB sound card / Direwolf startup):
- ADEVICE detection now matches the CM108 on any /dev/hidraw* (not just
  hidraw0) and prefers the stable plughw:<name> form over plughw:<number>,
  since card numbers shift with USB enumeration order while the name is
  stable.
- The boot-time self-heal (kp4pra-adevice-fix) now waits up to 45s for the
  USB sound card to enumerate before detecting ADEVICE, so Direwolf starts
  with the correct device instead of failing when the card is slow to
  appear on cold boot.
- direwolf.service RestartSec 5->2 as a backstop for residual races.

Note: these mitigate SLOW USB enumeration. If the USB sound card fails to
enumerate at all on cold boot (device never appears), that is a hardware/
USB-power issue - a powered USB hub and/or better power supply is the
recommended fix.

## v1.2.0 - 2026-07-19

Features:
- Morse-code station ID on the Raspberry Pi green ACT LED. When the TNC
  is fully operational (Dire Wolf running with a working audio device and
  at least one KISS bridge active), it blinks the configured station
  callsign in Morse at 10 WPM every 15 minutes, then restores the LED's
  normal SD-activity indication. Health-gated, so the absence of the
  periodic blink also serves as a "not ready" indicator. Enable/disable
  via kp4pra-morse-id.timer. See docs/MORSE_ID.md.

## v1.2.1 - 2026-07-19

Changes:
- Morse callsign ID: added a Config-page toggle (station.morse_id_enabled,
  default enabled). The timer keeps running and the ID script reads the
  toggle each fire, so enabling/disabling takes effect on the next cycle
  with no shell access. The installer now auto-enables the Morse ID timer
  on fresh installs. Docs updated.

## v1.2.2 - 2026-07-20

Changes:
- AIOC (All-In-One-Cable) support. The AIOC enumerates as a CM108-class
  USB sound card (plughw:AllInOneCable,0, VID 1209:7388) and is detected
  automatically by the existing logic. detect_sound_cards() now returns
  the stable ALSA name form instead of the card number, so the web Detect
  button matches the boot-time self-heal. Runs at Dire Wolf's default
  44100 Hz for consistency across sound cards. Documented in
  docs/USB_SOUND_CARD.md.

## v1.2.2 - 2026-07-20

Changes:
- AIOC (All-In-One-Cable) support. The AIOC enumerates as a CM108-class
  USB sound card (plughw:AllInOneCable,0, VID 1209:7388) and is detected
  automatically by the existing logic. detect_sound_cards() now returns
  the stable ALSA name form instead of the card number, so the web Detect
  button matches the boot-time self-heal. Runs at Dire Wolf's default
  44100 Hz for consistency across sound cards. Documented in
  docs/USB_SOUND_CARD.md.

## v1.2.3 - 2026-07-20

Fixes:
- Config page: a duplicate "station" key in collectConfig() (introduced
  in 1.2.1 with the Morse ID toggle) overwrote the station object with
  only morse_id_enabled, so Apply to Direwolf generated an empty MYCALL
  and blank CBEACON callsign. Merged morse_id_enabled into the real
  station block; Apply now sends the full station config again.

## v1.2.4 - 2026-07-20

Fixes:
- detect_sound_cards() now genuinely returns the stable ALSA name form
  (plughw:<name>) for the web Detect button, matching the boot-time
  self-heal. The v1.2.2 change was described in its commit but the code
  edit did not actually land; the Detect button was still returning the
  card-number form (e.g. plughw:0,0 for the AIOC). Now verified.
