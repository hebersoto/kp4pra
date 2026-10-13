# Mini PiTFT status display (Adafruit 1.3" 240x240)

Optional front-panel display for the KP4PRA TNC: station call sign, live
packet traffic in UZ7HO Soundmodem style, Winlink/WiFi/Bluetooth counters and
the network address. Works on the **Raspberry Pi** and the **Orange Pi Zero
2W (Armbian)**, with or without the DRA-Pi-Zero (they share no pins; use a
stacking header to fit both).

Hardware: [Adafruit Mini PiTFT 1.3" 240x240](https://www.adafruit.com/product/4484)
(ST7789). Font: DejaVu Sans 24 pt.

## Setup

The installer (`scripts/install.sh`) runs `scripts/setup-tft.sh --auto`: it
always installs the `kp4pra-tnc-tft` service and enables SPI **only if a Mini
PiTFT is plugged in at that moment** (reboot afterwards). To add a display
later, plug it in and run:

```bash
sudo kp4pra-remount-rw
cd ~/kp4pra-tnc
sudo bash scripts/setup-tft.sh
sudo reboot
```

Web UI -> **Config -> TFT Display**: **Auto** (default - use the display when
it is detected), **On** (always) or **Off**. The card shows the live status.
**Orientation**: Normal (default) or Flipped 180° if the picture is upside
down in your enclosure. Changes apply within about 10 seconds (no restart
needed).

What the setup does: installs `python3-pil python3-numpy python3-spidev
python3-libgpiod python3-yaml fonts-dejavu-core`; adds `kp4pra-tnc` to the
`gpio`, `systemd-journal` (and on a Pi `spi`) groups; enables SPI
(Pi: `dtparam=spi=on` in config.txt, backed up to `config.txt.kp4pra-tft.bak`;
Orange Pi: user overlay `kp4pra-tft-orangepi` = SPI1 + spidev); enables the
service.

## Screens

| Row | Screen 1 | Screen 2 |
|---|---|---|
| 1 | Call sign + SSID | Winlink: N pending |
| 2-4 | Last 3 frames (red = TX, green = RX) | WiFi: N devices (AP) / client · BT/BLE: N |
| 5-7 | Current packet: `FROM>TO` (UI: `FROM`), `<frame type>` + time + TX/RX, UI text | Last 4 frames (rows 4-7) |
| 8 | `AP 172.16.0.1` or `CL <ip>` (same on both screens) | same |

At power-up a splash screen shows **KP4PRA TNC** in KP4PRA green on white for
10 seconds. Lines wider than the screen scroll slowly.

Every traffic line starts with the callsigns, `FROM>TO`, followed by a short
AX.25 monitor token (no Pid / Len, so lines stay short; long lines scroll):

| Frame | Token |
|---|---|
| Connected data | `<I C [P] Rn Sn>` |
| Supervisory | `<RR C\|R [P\|F] Rn>`, `<RNR ...>`, `<REJ ...>`, `<SREJ ...>` |
| Link setup / teardown | `<SABM C P>`, `<SABME C P>`, `<DISC C P>`, `<UA R F>`, `<DM R F>`, `<FRMR R F>` |
| Other | `<XID C\|R [P\|F]>`, `<TEST C\|R [P\|F]>`, `<UNKN>` |
| UI (APRS, beacons) | `<UI C>` |

Examples: `KP3M-11>WP4DMD-2 <RR R F R3>`, `KP3M-10>KP4CE-10 <I C P R5 S2>`.
UI frames leave out the destination (the TOCALL such as `APRS`), show the
frame text with repeated spaces collapsed, and drop GPS position data
(coordinates, symbol, course/speed, altitude, raw NMEA):
`NP4JM-10 <UI C> Hello from Ponce`.
C/R = command/response, P/F = poll/final, R/S = receive/send sequence numbers.

## Buttons

| Button | Action |
|---|---|
| 23 (top), short press | Switch screen 1 <-> 2. If the screen is off, turns it on |
| 23 held 3 s | Switch WiFi between Client and Access Point mode. Shows "WiFi Access Point Mode" or "WiFi Client Mode" (splash colors) for 10 s. Switching to Access Point drops a client-WiFi SSH session |
| 24 (bottom), short press | Screen off / on (wakes on the same screen) |
| 24 held 3 s | Send one APRS beacon: call sign + SSID and position (beacon lat/lon, else station lat/lon, else grid). Without a position it sends a status beacon (`>comment`). "Beacon sent" shows for 3 s |

Client mode turns the hotspot off and joins the client WiFi set in
Config -> WiFi. With no client WiFi configured, WiFi stays disconnected and
the bottom row shows `CL` with the wired / USB Ethernet address (if any).
Access Point mode needs `dnsmasq-base` (installed by the installer). If the
switch fails the screen shows "WiFi switch failed" in red; details in
`journalctl -u kp4pra-tnc-tft -b`.

After a WiFi mode switch the DRA-Pi-Zero blue LED keeps following Bluetooth
availability (kp4pra-tnc-bt-led, every 2 s): off while neither BLE nor
RFCOMM is available, solid when available, fast flash with a client
connected.

The beacon uses the APRS settings from the Config page (callsign, digipeater
path, symbol, comment) and goes out through Dire Wolf's AGW port; the APRS
chat page does not need to be running.

## How it works

- `src/tft/main.py` (service `kp4pra-tnc-tft`, user `kp4pra-tnc`, system
  python3).
- Traffic comes from Dire Wolf's own packet log (`journalctl -u direwolf`):
  it is the only source that shows **our own transmissions of every frame
  type** (Dire Wolf's AGW monitor omits own-transmitted connected-mode
  frames). `src/tft/monitor.py` converts its `(SABM cmd, p=1)` style
  descriptions to UZ7HO tokens.
- Detection (Auto): the ST7789 cannot be read back (MISO not connected), so
  the service probes the two buttons: with the SoC's weak pull-downs enabled,
  both read high only when the board's 10 k pull-ups are present.
- Status for the web UI: `/run/kp4pra-tnc/tft.json`.

## Pins

| Header | Function | Raspberry Pi | Orange Pi Zero 2W |
|---|---|---|---|
| 19/23/24 | SPI MOSI/SCLK/CS | SPI0 CE0 (`spidev0.0`) | SPI1 PH7/PH6/PH5 |
| 22 | DC | `gpiochip0 25` | `gpiochip1 262` (PI6) |
| 15 | Backlight | `gpiochip0 22` | `gpiochip1 261` (PI5) |
| 16 | Button 23 | `gpiochip0 23` | `gpiochip1 270` (PI14) |
| 18 | Button 24 | `gpiochip0 24` | `gpiochip1 228` (PH4) |

## Troubleshooting

- `systemctl status kp4pra-tnc-tft`, `journalctl -u kp4pra-tnc-tft -b`.
- Config card says "SPI not enabled": run `sudo bash scripts/setup-tft.sh`
  and reboot.
- "no Mini PiTFT detected (auto)" with a display attached: check it is
  seated; or set the Config to **On** to skip detection.
- No traffic lines: the service user needs the `systemd-journal` group
  (`id kp4pra-tnc`); re-run the setup script, then
  `sudo systemctl restart kp4pra-tnc-tft`.
- Picture upside down: Config -> TFT Display -> Orientation -> Flipped 180°.
- Picture shifted or mirrored: the driver assumes the 1.3" 240x240 Mini
  PiTFT (Normal: rows 0-239; Flipped: rows 80-319). Other ST7789 sizes are
  not supported.
