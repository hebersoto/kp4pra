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
Changes apply within about 10 seconds (no restart needed).

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
| 5-7 | Current packet: `SRC>DST`, `<frame type>`, time + TX/RX | Last 4 frames (rows 4-7) |
| 8 | `AP 172.16.0.1` or `CL <ip>` (same on both screens) | same |

At power-up a splash screen shows **KP4PRA TNC** in KP4PRA green on white for
10 seconds. Lines wider than the screen scroll slowly.

Frame types follow UZ7HO Soundmodem: `KP4CE-10 <SABM C P>`, `<UA R F>`,
`<RR R F R2>`, `<I C P S0 R3>`, `<DISC C P>`, `<DM R F>`, APRS `<UI C>`;
XID/TEST and unknown frames show as `<UNKN ...>` (as UZ7HO does).
C/R = command/response, P/F = poll/final, S/R = send/receive sequence numbers.

## Buttons

| Button | Action |
|---|---|
| 23 (top) | Switch screen 1 <-> 2. If the screen is off, turns it on |
| 24 (bottom), short press | Screen off / on (wakes on the same screen) |
| 24 held 3 s | Send one APRS beacon: call sign + SSID and position (beacon lat/lon, else station lat/lon, else grid). Without a position it sends a status beacon (`>comment`). "Beacon sent" shows for 3 s |

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
- Picture shifted or mirrored: the driver assumes the 1.3" 240x240 Mini
  PiTFT (rotation 180, row offset 80). Other ST7789 sizes are not supported.
