## APRS clock fallback — new in 1.3.10
- Added a receive-only Dire Wolf AGW listener using the existing
  `station.clock` source callsign and `direwolf.host` configuration.
- Uses AGW port 8000 instead of consuming another KISS client slot; existing
  RMS, BLE, RFCOMM, and external KISS clients remain on the configured KISS
  port (normally 8001).
- NTP remains primary. APRS engages only after three consecutive
  unsynchronized checks and NTP is checked again immediately before a step.
- Supports timestamped position/object reports using DDHHMMz, HHMMSSh, and
  DDHHMM/; ignores all other stations and untimestamped packets.
- Uses only APRS time-of-day and retains the board date. APRS cannot provide a
  reliable year/date, so this corrects drift, not a grossly wrong date.
- Steps only above 120 seconds, handles midnight wraparound, and enforces a
  five-minute cooldown. Added a date-set-only sudoers rule, systemd unit,
  installer wiring, documentation, and automated tests.
- Hardware validation remains required before merge and tag.
## DRA-Pi-Zero web setup + Reboot button — new in 1.3.9

- Web UI DRA-Pi-Zero setup (Config page card): one button writes the I2S
  overlay + onboard/HDMI audio-off to config.txt (backed up first), then the
  mixer applies automatically on the next boot via kp4pra-dra-mixer.service.
  Two endpoints: POST /api/dra/setup (narrow sudoers, --config-only only) and
  GET /api/dra/status. Status card shows detected / not-detected.
- setup-dra-pi-zero.sh refactored: --config-only / --mixer-only / (full).
  Adds HDMI-audio-off (vc4-kms-v3d,noaudio) and a one-time config.txt backup.
- kp4pra-dra-mixer.service: oneshot, applies the WM8731 mixer on boot if the
  codec is present; no-ops safely on non-DRA boards (enabled on all boards).
- Reboot Now button + POST /api/system/reboot (narrow sudoers: /sbin/reboot).
  Confirm dialog; response flushes before the box goes down.
- Validated on a Raspberry Pi 3B+ with a DRA-Pi-Zero installed: web setup ->
  reboot -> overlay loads -> mixer auto-applies -> RX decode, TX, GPIOD PTT,
  green DCD + blue BT LEDs. Reboot button: confirm -> clean reboot -> DRA
  operational. Safety paths (no-op on non-DRA, config backup/restore, narrow
  sudoers scope) validated earlier on a USB-only board.

## DRA-Pi-Zero support + multi-card + LEDs — new in 1.3.7

- Masters Communications DRA-Pi-Zero (I2S WM8731) support: opt-in
  scripts/setup-dra-pi-zero.sh (overlay + mixer + gpio group), auto-
  detected in the web UI as "DRA-Pi-Zero (I2S)" with GPIOD PTT pre-filled.
  Validated on RF: RX decode, TX, digipeated round trip.
- PTT via GPIOD (libgpiod), not legacy sysfs. Kernel 6.x sysfs GPIO is
  unreliable - line numbering moved (BCM12 -> sysfs 524) and a crash can
  leave the line locked. GPIOD form: PTT GPIOD gpiochip0 12. Requires
  direwolf built with libgpiod-dev (added to INSTALL build deps + PREREQS;
  setup script warns if the binary lacks it).
- LEDs: red PTT (relay, automatic), green DCD carrier-detect (GPIO 16,
  DCD GPIOD gpiochip0 16), blue BT-connected (GPIO 5, new
  kp4pra-tnc-bt-led service polling `bluetoothctl devices Connected`).
  LEDs are on/off only - GPIO cannot dim from software.
- ADEVICE self-heal now skips the 45s USB wait and conf rewrite when an
  I2S card (audioinjectorpi) is configured, so a plugged-in USB card can't
  steal the DRA config. USB path unchanged.
- config.py DEFAULT_CONFIG gains the station block (closes a long-standing
  gap where a save from defaults could drop station fields).
- Onboard analog audio (dtparam=audio=off) now set by the main installer
  for all users (dedicated sound card is always used). CAVEAT: validated
  only on the DRA board this cycle, not yet on a CM108-only board - it
  should be harmless (USB card is separate) but is noted as a follow-up.
- Web UI: PTT dropdown gains GPIOD; Detect now applies the PTT suggestion
  to the pre-selected card (was only wired to onchange).
- Fix: /run/kp4pra-tnc runtime-dir deletion on RMS stop (see separate
  commit; pre-existing v1.3.6 bug).

NOTE: BLE still uses the pre-existing raw-HCI advertising fallback for the
2026-06 kernel MGMT regression (see below); unrelated to this release.

## RMS Gateway (src/rms/) — new in 1.3.0
- Native Python Winlink RMS gateway: Dire Wolf KISS TCP -> AX.25 connected
  mode -> authenticated CMS stream. RF Winlink clients (Winlink Express,
  pat, etc.) connect over the air; the gateway relays transparently to
  Winlink CMS using the approved gateway callsign's secure-login
  credentials.
- FIX: AX.25 command/response and Poll/Final bit handling. UA/DM/ack-RR
  frames were previously always sent as Command frames with no Final
  bit, which is spec-invalid as a reply to a Poll-bit SABM/DISC -- RF
  clients would not recognize the link as established and would
  retry-loop indefinitely instead of completing login. Found via a
  side-by-side capture against a working BPQ32 RMS reference. Fixed by
  adding response-frame support to make_frame() and mirroring the
  incoming Poll bit into Final on every reply.
- FIX: rapid re-SABM race condition. A peer retrying SABM quickly could
  leave a stale cms_to_rf() task racing the new session's CmsSession
  over the same socket, crashing with "read() called while another
  coroutine is already waiting for incoming data". Fixed by cancelling
  any live relay task before starting a new session.
- Telnet Winlink / "Network Post Office" access: a transparent TCP
  proxy to the real CMS on all network interfaces (default port 8772,
  matching Winlink Express's own convention). No local login logic --
  the connecting client performs its entire secure-login exchange
  directly against real CMS's protocol, since only CMS can validate an
  arbitrary station's own Winlink account password. Fully independent
  of the RF session state; RF and Telnet sessions do not contend with
  each other.
- Services tab: new card showing live gateway status with Start/Stop.
- Config page: RMS Gateway toggle, split Callsign/SSID fields (the RMS
  gateway callsign may differ from the station callsign), CMS password,
  frequency, and packet mode.
- 21 automated tests: 10 covering AX.25/KISS framing edge cases (split
  reads, digipeater paths, window boundaries), 11 covering Gateway relay
  logic (chunking, flow control, session teardown, the re-SABM race fix,
  and Telnet proxy fidelity/independence).
- Live-tested end to end: real RF session via Winlink Express through a
  physical radio link, and real Telnet/Network Post Office session, both
  completing full B2F exchanges against production CMS with clean
  session teardown.

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

## v1.2.4 - 2026-07-20

Fixes:
- detect_sound_cards() (web Detect button) now uses the ALSA card ID for
  the plughw device instead of the human-readable bracketed description.
  aplay -l reports "card N: <ID> [<description>]"; ALSA accepts the ID
  (e.g. AllInOneCable) but rejects the hyphenated description
  (All-In-One-Cable) as a device name. The Detect button now returns a
  working plughw that matches the boot-time self-heal.

## v1.3.1 - 2026-07-22

Changes:
- Added a pre-commit secret guard (scripts/git-hooks/pre-commit) that blocks
  commits containing private keys, non-blank credential values in tracked
  YAML (cms_password, cms_user, password, client_password, psk), or known
  secret literals listed in an untracked .git/secret-guard-literals file.
  The documented public AP default is allowlisted so normal commits are not
  blocked. Hooks are not tracked by git, so install once per clone with
  `bash scripts/install-hooks.sh`.

## v1.3.2 - 2026-07-25

Fixes (fresh-install, found on a clean Orange Pi / Debian 13):
- install.sh venv prerequisite check now tests `python3 -c "import
  ensurepip"` instead of `python3 -m venv --help`. The old probe passed
  even when the python3.X-venv package (which provides ensurepip) was
  missing, so the install failed later at venv creation instead of dying
  early with a clear message.
- install.sh now copies kp4pra-tnc-rms.service (and the morse-id
  service/timer) in the unit-install step. The enable step referenced the
  RMS unit but the copy step never installed it, causing "Unit
  kp4pra-tnc-rms.service does not exist" on fresh installs. Also enables
  the morse-id timer.

## v1.3.3 - 2026-07-25

Changes:
- install.sh now auto-installs its prerequisite packages up front on
  Debian/Ubuntu (python3, python3-venv, python3-pip, bluez, bluez-tools,
  network-manager) before the dependency checks run, so a fresh image
  installs cleanly on the first attempt instead of failing on a missing
  python3-venv or warning about missing NetworkManager. Idempotent; the
  existing checks remain as a safety net. Non-Debian systems get a warning
  listing the packages to install manually.

## v1.3.3 - 2026-07-25

Changes:
- install.sh now auto-installs its prerequisite packages up front on
  Debian/Ubuntu (python3, python3-venv, python3-pip, bluez, bluez-tools,
  network-manager) before the dependency checks run, so a fresh image
  installs cleanly on the first attempt instead of failing on a missing
  python3-venv or warning about missing NetworkManager. Idempotent; the
  existing checks remain as a safety net. Non-Debian systems get a warning
  listing the packages to install manually.

## v1.3.4 - 2026-07-26

Changes:
- Web UI header now shows the installed version (e.g. "KP4PRA TNC v1.3.4")
  next to the title on every page, read from the VERSION file via the
  existing app_version template global. (The footer already showed it; a
  redeploy also fixes boards that were serving a stale 'vdev' placeholder.)

## v1.3.4 - 2026-07-26

Changes:
- Web UI header now shows the installed version (e.g. "KP4PRA TNC v1.3.4")
  next to the title on every page, read from the VERSION file via the
  existing app_version template global. (The footer already showed it; a
  redeploy also fixes boards that were serving a stale 'vdev' placeholder.)

## v1.3.5 - 2026-07-26

Changes:
- Added scripts/cleanup-direwolf-source.sh to reclaim ~180MB by removing the
  Dire Wolf source/build tree, which is not needed once the binary is
  installed to /usr/local/bin. Guarded: refuses unless the installed binary
  exists and direwolf.service runs it from outside the source tree; prompts
  before deleting. Stage 2 points users to it.

## v1.3.6 - 2026-07-26

Fixes:
- install.sh now copies the VERSION file to /opt/kp4pra-tnc. The install step
  copied only src/, so fresh installs had no VERSION in /opt and the web UI
  showed "dev" instead of the installed version. Fresh installs now show the
  correct version.

## v1.3.8 - 2026-07-27

Changes:
- Stage 2 (install-direwolf-integration.sh) now automatically removes the Dire
  Wolf source/build tree after install (guarded, non-interactive), reclaiming
  ~180MB on fresh installs. cleanup-direwolf-source.sh gained a --yes mode; a
  flag-parsing bug that mistook --yes for a source path is fixed. Guards
  unchanged; cleanup is warn-and-continue so it never fails an install.

## v1.3.11 - 2026-07-28

Docs:
- README now documents the APRS clock fallback (shipped in 1.3.10): a feature
  section describing the receive-only AGW-8000 listener, NTP-primary behavior,
  the Config-page Clock Source Station field, and the narrow sudoers grant,
  pointing to docs/APRS_CLOCK.md. Moved from the roadmap "Next" list to
  "Working".

## v1.3.12 - 2026-07-30

Fixes (Bluetooth provisioning):
- Pairing agent now reliably starts at boot. Requires=bluetooth.service was
  changed to Wants=, and Restart=always/RestartSec=2 added, because a
  boot-time race silently dropped the agent's start job, leaving pairing
  broken (devices got "software needed" / failed) until a manual start. This
  was the root cause of intermittent pairing.
- Android provisioning page now trusts an already-paired device via
  /api/bt/trust instead of pairing-by-MAC via /api/bt/pair. The phone pairs
  itself (Just Works); pairing-by-MAC failed on an already-paired device and
  skipped the trust step. Trust remains an explicit operator action.
- Device scan extended from 10s to 30s; Step 4 reworded to the
  pair-from-phone-then-trust flow.
- Bluetooth page shows a "rebooting to save" message instead of "undefined"
  when a permanent action reboots mid-response.

## v1.3.13 - 2026-07-30

Docs:
- Added Raspberry Pi 3 Model A+ (Raspberry Pi OS Lite 64-bit, Debian 13
  trixie) to the Hardware validated list, with the v1.3.12 Bluetooth
  provisioning-reliability validation (boot-reliable pairing agent;
  pair-from-phone then Trust-and-Authorize flow).
- Status/roadmap "Working" updated to reflect the provisioning-reliability
  fixes.

## v1.3.14 - 2026-07-30

Fixes (KISS bridge reliability):
- RFCOMM and BLE bridges no longer leak the Dire Wolf KISS socket on client
  disconnect. Both used a blocking recv() with no timeout, so when the phone
  disconnected the reader hung and the 8001 socket lingered in CLOSE-WAIT,
  consuming one of Dire Wolf's limited KISS client slots. Accumulated stale
  slots made a second WoAD/Winlink RMS session fail until the bridge was
  restarted (which itself sometimes timed out because the hung thread would
  not exit). Now: 1s socket timeouts make recv interruptible, teardown
  shutdown()+close()s the socket, and RFCOMM stop() closes the in-flight
  socket to unblock the reader. Verified with repeated back-to-back
  WoAD->Bluetooth->RF RMS sessions.

## v1.3.15 - 2026-08-01

Fixes (BLE bridge):
- REGRESSION FIX for v1.3.14. The v1.3.14 socket-teardown work set
  settimeout(1.0) on the Direwolf reader inside direwolf_loop() but did not
  add the matching socket.timeout handler there. Every idle second raised,
  closed the KISS connection and reconnected about every four seconds, so the
  BLE bridge never held a usable KISS path: aprs.fi reported connected but
  beacons never reached the radio. Both Direwolf readers now treat a timeout
  as an idle interval rather than a disconnect.
- Legacy raw-HCI advertising (the workaround for the June-2026 kernel MGMT
  regression) is switched off by the controller when a connection is accepted
  and is also lost on classic-BT activity and adapter events, none of which
  raise a D-Bus signal. The bridge could therefore remain "active" while being
  completely undiscoverable until restarted by hand - observed in the field as
  2.5 hours with no log output while the iPhone could not find the TNC. A 20
  second watchdog now re-asserts the idempotent legacy-adv helper whenever no
  BLE client is connected.
- Validated on a Pi 3 A+: single stable ESTAB on port 8001, no reconnect
  storm, and repeated iPhone beacons transmitted and digipeated over RF.

## v1.3.16 - 2026-08-01

Docs:
- README now notes that BLE legacy raw-HCI advertising is re-asserted
  automatically about every 20 seconds while idle (added in 1.3.15), because
  the controller silently stops it on connection accept and on classic-BT
  activity.
- Status/roadmap "Working" updated with the KISS bridge reliability work:
  clean socket teardown on both bridges and automatic BLE advertising
  recovery.

## v1.3.17 - 2026-08-01

Docs:
- README "What it does": simplified the iPhone entry to "connects over BLE
  KISS GATT — no reboot, using the standard KISS-over-BLE UUIDs", since the
  BLE KISS path is not specific to aprs.fi.

## v1.3.18 - 2026-08-02

Features:
- The gateway now advertises the Dire Wolf KISS TCP port as `_kiss-tnc._tcp`
  over DNS-SD/mDNS, so WiFi KISS clients (including clients joined to the
  TNC's own hotspot) can discover it without being given an IP address. The
  advertisement is published by the system Avahi daemon from a service file
  generated at install time using `dns_sd.instance_name` and `direwolf.port`.
  Previously install.sh assumed Dire Wolf advertised this itself and wrote no
  service file; most Dire Wolf builds have no dns-sd support, so in practice
  nothing was advertised on any board.
- `avahi-daemon` and `avahi-utils` added to the installer prerequisites. The
  Dashboard DNS-SD card calls `avahi-browse`, which was not installed, so the
  card reported "Avahi available: No" on every board regardless of state.

Fixes:
- `check_dns_sd()` matched any line containing "kiss", so another station on
  the same LAN advertising `_kiss-tnc._tcp` was reported as this TNC's own
  advertisement. It now matches the configured `dns_sd.instance_name`.

Docs:
- README: new "Network discovery (DNS-SD / mDNS)" section describing what is
  advertised, that it comes from the gateway rather than Dire Wolf, and that
  the name and port are set at install time from config.

## v1.3.19 - 2026-08-02

Docs:
- Hardware validated: added the Inovato Quadra and Inovato Quadra Plus on
  Armbian 26.5.1 bookworm (Debian 12), 64-bit. The Quadra has no Bluetooth
  hardware and runs as a WiFi/TCP KISS TNC; the RFCOMM and BLE paths are
  unavailable there by hardware rather than by configuration.

## v1.3.20 - 2026-08-02

Docs:
- Hardware validated: added iPhone/iPad RadioMail 1.6.1 to the tested clients
  list.
