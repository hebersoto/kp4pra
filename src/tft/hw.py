"""
KP4PRA TNC - Mini PiTFT 1.3" hardware: ST7789 over spidev, backlight, DC
and the two buttons over libgpiod (v1 or v2 Python bindings).

Pins (Adafruit Mini PiTFT, same header positions on both boards):

  Header  Function       Raspberry Pi            Orange Pi Zero 2W
  19/23   SPI MOSI/SCLK  SPI0                    SPI1 (PH7/PH6)
  24      SPI CS         CE0 -> /dev/spidev0.0   PH5 -> spidev on spi@5011000
  22      DC             gpiochip0 25            gpiochip1 262 (PI6)
  15      Backlight      gpiochip0 22            gpiochip1 261 (PI5)
  16      Button A (23)  gpiochip0 23            gpiochip1 270 (PI14)
  18      Button B (24)  gpiochip0 24            gpiochip1 228 (PH4)

None of these are used by the DRA-Pi-Zero (I2S 12/35/38/40, I2C 3/5, LEDs
and PTT 29/32/36), so both can run together (stacking header needed).

Detection: the ST7789 on this board cannot be read back (MISO is not
connected), so presence is probed through the buttons: the Mini PiTFT has
10 k pull-ups on both. With the SoC's weak internal pull-DOWN enabled, both
lines read 1 only when the board is plugged in.
"""

import glob
import os
import time

from common.dra_platform import detect_platform

PINS = {
    "rpi": {"chip": "gpiochip0", "dc": 25, "bl": 22, "btn_a": 23, "btn_b": 24,
            "spi_ctrl": None, "spidev": "/dev/spidev0.0"},
    "opi-zero2w": {"chip": "gpiochip1", "dc": 262, "bl": 261, "btn_a": 270, "btn_b": 228,
                   "spi_ctrl": "5011000.spi", "spidev": "/dev/spidev1.0"},
}

# ST7789 (240x240 window on a 240x320 controller; Adafruit: rotation 180,
# y_offset 80 for the 1.3" Mini PiTFT).
MADCTL_ROT180 = 0xC0
ROW_OFFSET = 80
SPI_HZ = 40_000_000


def pins_for(platform=None):
    return PINS.get(platform or detect_platform() or "")


def find_spidev(p) -> str:
    """spidev node for the board; on the Orange Pi pick the one on spi@5011000."""
    if p["spi_ctrl"]:
        for dev in sorted(glob.glob("/sys/class/spidev/spidev*")):
            if p["spi_ctrl"] in os.path.realpath(dev):
                return "/dev/" + os.path.basename(dev)
    return p["spidev"]


# ---------------------------------------------------------------- GPIO ----
class Gpio:
    """Minimal line access working with libgpiod Python bindings v1 and v2."""

    def __init__(self, chip, outputs, inputs, pull="up"):
        import gpiod
        self._g = gpiod
        self._v2 = hasattr(gpiod, "request_lines")
        self.outputs, self.inputs = list(outputs), list(inputs)
        path = chip if chip.startswith("/dev/") else "/dev/" + chip
        if self._v2:
            from gpiod.line import Direction, Value, Bias
            self._Value = Value
            bias = Bias.PULL_UP if pull == "up" else Bias.PULL_DOWN
            cfg = {}
            for o in self.outputs:
                cfg[o] = gpiod.LineSettings(direction=Direction.OUTPUT,
                                            output_value=Value.INACTIVE)
            for i in self.inputs:
                cfg[i] = gpiod.LineSettings(direction=Direction.INPUT, bias=bias)
            self._req = gpiod.request_lines(path, consumer="kp4pra-tft", config=cfg)
        else:
            self._chip = gpiod.Chip(path)
            self._lines = {}
            for o in self.outputs:
                ln = self._chip.get_line(o)
                ln.request(consumer="kp4pra-tft", type=gpiod.LINE_REQ_DIR_OUT, default_vals=[0])
                self._lines[o] = ln
            flag = getattr(gpiod, "LINE_REQ_FLAG_BIAS_PULL_UP" if pull == "up"
                           else "LINE_REQ_FLAG_BIAS_PULL_DOWN", 0)
            for i in self.inputs:
                ln = self._chip.get_line(i)
                ln.request(consumer="kp4pra-tft", type=gpiod.LINE_REQ_DIR_IN, flags=flag)
                self._lines[i] = ln

    def set(self, line, value):
        if self._v2:
            self._req.set_value(line, self._Value.ACTIVE if value else self._Value.INACTIVE)
        else:
            self._lines[line].set_value(1 if value else 0)

    def get(self, line) -> int:
        if self._v2:
            return 1 if self._req.get_value(line) == self._Value.ACTIVE else 0
        return self._lines[line].get_value()

    def close(self):
        try:
            if self._v2:
                self._req.release()
            else:
                for ln in self._lines.values():
                    ln.release()
                self._chip.close()
        except Exception:
            pass


def probe(p, samples=10) -> bool:
    """True if a Mini PiTFT is plugged in (both button pull-ups present)."""
    try:
        g = Gpio(p["chip"], [], [p["btn_a"], p["btn_b"]], pull="down")
    except Exception:
        return False
    try:
        highs = 0
        for _ in range(samples):
            if g.get(p["btn_a"]) and g.get(p["btn_b"]):
                highs += 1
            time.sleep(0.02)
        return highs >= samples - 2       # tolerate a brief button press
    finally:
        g.close()


# --------------------------------------------------------------- ST7789 ----
class ST7789:
    def __init__(self, p):
        import spidev
        self.p = p
        self.gpio = Gpio(p["chip"], [p["dc"], p["bl"]], [p["btn_a"], p["btn_b"]], pull="up")
        dev = find_spidev(p)
        bus, cs = (int(x) for x in os.path.basename(dev)[len("spidev"):].split("."))
        self.spi = spidev.SpiDev()
        self.spi.open(bus, cs)
        self.spi.mode = 0
        self.spi.max_speed_hz = SPI_HZ
        self.init()

    def _cmd(self, c, data=None):
        self.gpio.set(self.p["dc"], 0)
        self.spi.writebytes([c])
        if data:
            self.gpio.set(self.p["dc"], 1)
            self.spi.writebytes2(bytes(data))

    def init(self):
        self._cmd(0x01); time.sleep(0.15)          # SWRESET
        self._cmd(0x11); time.sleep(0.12)          # SLPOUT
        self._cmd(0x3A, [0x55])                    # COLMOD 16-bit RGB565
        self._cmd(0x36, [MADCTL_ROT180])           # MADCTL rotation 180
        self._cmd(0x21)                            # INVON (IPS panel)
        self._cmd(0x13)                            # NORON
        self._cmd(0x29); time.sleep(0.05)          # DISPON
        self.backlight(True)

    def backlight(self, on):
        self.gpio.set(self.p["bl"], 1 if on else 0)

    def sleep(self, on):
        """Screen off = backlight off + display off; on = the reverse."""
        if on:
            self.backlight(False)
            self._cmd(0x28)                        # DISPOFF
        else:
            self._cmd(0x29)                        # DISPON
            self.backlight(True)

    def show(self, img):
        import numpy as np
        a = np.asarray(img.convert("RGB"), dtype=np.uint16)
        rgb565 = ((a[..., 0] & 0xF8) << 8) | ((a[..., 1] & 0xFC) << 3) | (a[..., 2] >> 3)
        buf = rgb565.astype(">u2").tobytes()
        y0, y1 = ROW_OFFSET, ROW_OFFSET + 239
        self._cmd(0x2A, [0, 0, 0, 239])                       # CASET
        self._cmd(0x2B, [y0 >> 8, y0 & 0xFF, y1 >> 8, y1 & 0xFF])  # RASET
        self._cmd(0x2C)                                       # RAMWR
        self.gpio.set(self.p["dc"], 1)
        self.spi.writebytes2(buf)

    def buttons(self):
        """(A pressed, B pressed) - buttons are active low."""
        return (self.gpio.get(self.p["btn_a"]) == 0, self.gpio.get(self.p["btn_b"]) == 0)

    def close(self):
        try:
            self.sleep(True)
        except Exception:
            pass
        try:
            self.spi.close()
        finally:
            self.gpio.close()
