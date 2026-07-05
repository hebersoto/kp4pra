# KP4PRA TNC

**Bluetooth KISS TNC bridge and web management interface for Orange Pi /
Raspberry Pi running [Dire Wolf](https://github.com/wb2osz/direwolf).**

Turns a small single-board computer with a USB radio interface into a
field-ready APRS TNC that phones connect to over Bluetooth — with a web
UI for setup, pairing, Dire Wolf configuration, and live traffic
monitoring. Designed for unattended operation on a read-only filesystem
with no persistent logs (SD-card friendly).

Built by KP4PRA. Station example: KP3M, FK68wd, Puerto Rico.

## What it does
Android (APRSDroid) ──RFCOMM/SPP──┐
├──> KISS TCP :8001 ──> Dire Wolf ──> Radio
iPhone (aprs.fi) ──BLE KISS GATT──┘         ▲
│
WiFi/LAN APRS apps ── DNS-SD discovery ─────┘
Browser ──> http://<host>/ (port 80→8088) ──> Web management UI

- **Android** pairs over Bluetooth Classic (Just Works — no PIN) and
  APRSDroid connects via RFCOMM/SPP.
- **iPhone** connects from inside aprs.fi over BLE KISS GATT — no iOS
  pairing, no reboot, using the standard KISS-over-BLE UUIDs.
- **Both bridges** pass raw KISS binary, auto-reconnect if Dire Wolf
  restarts, and survive idle periods (no traffic ≠ disconnect).
- **Web UI** (FastAPI + Jinja2, mobile-friendly): dashboard with live
  status, Bluetooth pairing wizards for Android and iPhone, service
  control (bridges + Dire Wolf), live Dire Wolf traffic view, and a
  configuration page.
- **Station Information → direwolf.conf**: callsign/SSID, grid,
  lat/lon, sound card (live detection incl. Yaesu FT-991A / Signalink /
  CM108 disambiguation), PTT method (CM108 GPIO, serial RTS/DTR, VOX,
  GPIO pin), CDIGIPEAT alias — Preview and Apply regenerates
  direwolf.conf and restarts Dire Wolf; bridges reconnect automatically.

## Appliance design principles

- **Read-only root** in production; all persistent state on a small
  `/rw` partition (config, BlueZ pairing data via bind mount over
  /var/lib/bluetooth).
- **No persistent logs** — journald is volatile (RAM), status is read
  from live system state, nothing is written during normal operation.
- **Strictly allowlisted privileges** — the web service can only run
  specific systemctl/helper commands via narrow sudoers rules; no
  arbitrary command execution.
- **Self-healing** — ADEVICE re-detected at boot (USB renumbering),
  Dire Wolf auto-restart on failure, permission repair after pairing.

## Repository layout

| Path | Contents |
|---|---|
| `src/common/` | Config, BlueZ management, filesystem/service control, direwolf.conf generator, runtime status |
| `src/rfcomm/` | RFCOMM/SPP bridge (Android) — built-in socket AF_BLUETOOTH, no PyBluez |
| `src/ble/` | BLE KISS GATT bridge (iPhone) — dbus-next |
| `src/web/` | FastAPI app + Jinja2 templates (green/white theme, CSS variables) |
| `systemd/` | Service units: bridges, web, pairing agent, Dire Wolf, ADEVICE fix, BlueZ bind mount, perms fix |
| `bin/` | Helper scripts (remount rw/ro, BlueZ perms, ADEVICE fix) |
| `sudoers.d/` | Allowlisted sudo rules |
| `config/` | Example config.yaml, tmpfiles rule |
| `scripts/` | install.sh (stage 1), install-direwolf-integration.sh (stage 2), sync-from-system.sh |

## Installation

See **[INSTALL.md](INSTALL.md)** — from blank SD card to working TNC on
Orange Pi Zero 2W or Raspberry Pi Zero 2 W.

## Change history

See **[CHANGES.md](CHANGES.md)** for every fix from on-device bring-up
(Python 3.13, BlueZ 5.7x, Debian trixie) and the reasoning behind each.

## Hardware validated

- Orange Pi Zero 2W (1GB), Debian trixie, Python 3.13, BlueZ 5.7x
- CM108 USB sound dongle with HID PTT
- Yaesu FT-991A (USB audio codec + CP210x serial pair, PTT via RTS)
- Clients: Android APRSDroid (RFCOMM), iPhone aprs.fi (BLE KISS)

## Status / roadmap

Working: both Bluetooth paths bidirectional, web provisioning end to
end, Dire Wolf generation/control/traffic view.
Next: production hardening (zram + read-only root switch), Clock Source
Station stage.

## License

Private repository — all rights reserved by the owner.
