"""
KP4PRA TNC - Mini PiTFT display service (kp4pra-tnc-tft.service).

  config  display.tft: auto | on | off        (web UI: Config -> TFT Display)
          auto = run when a Mini PiTFT is detected (button pull-up probe)

Behavior
  - Splash "KP4PRA TNC" (white background, KP4PRA green) for 10 s at start.
  - Screen 1: callsign, last 3 frames (red TX / green RX), current packet,
              AP/CL + IP on the bottom row.
  - Screen 2: Winlink pending, WiFi devices, BT/BLE devices, last 4 frames,
              AP/CL + IP on the bottom row.
  - Button 23: switch screen 1 <-> 2 (wakes the screen if it is off).
  - Button 23 held >= 3 s: switch WiFi between Client and Access Point mode
    (kp4pra-wifi-mode via its sudoers rule); shows "WiFi Access Point Mode"
    or "WiFi Client Mode" in the splash colors for 10 s.
  - Button 24: screen off / on (any button wakes it on the same screen).
  - Button 24 held >= 3 s: send one APRS beacon (position, or status text
    when no position is configured).
Status for the web UI: /run/kp4pra-tnc/tft.json
"""

import collections
import os
import subprocess
import sys
import threading
import time

from common.config import load_config, reload_config
from common.runtime_status import write_status
from tft import beacon, hw, monitor, render, status

SPLASH_S = 10
LONG_PRESS_S = 3.0
CONFIG_POLL_S = 10
STATUS_POLL_S = 5
FRAME_HISTORY = 20
TOAST_S = 3


def log(msg):
    print("[KP4PRA TFT] %s" % msg, flush=True)


def tft_rotation(cfg) -> int:
    r = str((cfg.get("display", {}) or {}).get("tft_rotation", 0)).strip()
    return 180 if r == "180" else 0


def tft_mode(cfg) -> str:
    m = str(((cfg.get("display", {}) or {}).get("tft", "auto"))).lower()
    return m if m in ("auto", "on", "off") else "auto"


class Display:
    def __init__(self, dev):
        self.dev = dev
        self.lock = threading.Lock()
        self.dev_lock = threading.RLock()   # one SPI transaction at a time
        self.frames = collections.deque(maxlen=FRAME_HISTORY)
        self.current = None
        self.sys = {}
        self.screen = 1
        self.off = False
        self.toast = None            # (text, color, until)
        self.dirty = True
        self.stop = threading.Event()
        self.splash_until = 0.0
        self._a_down = None
        self._a_fired = False
        self.rotation = getattr(dev, "rotation", 0)
        self._prev = (False, False)
        self._b_down = None
        self._b_fired = False

    # ---- data feeds ----
    def journal_reader(self):
        cmd = ["journalctl", "-u", "direwolf", "-f", "-n", "0", "-o", "cat", "--no-pager"]
        while not self.stop.is_set():
            try:
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, text=True,
                                        errors="replace", bufsize=1)
                for line in proc.stdout:
                    if self.stop.is_set():
                        break
                    fr = monitor.parse_line(line)
                    if fr:
                        with self.lock:
                            self.frames.append(fr)
                            self.current = fr
                            self.dirty = True
                proc.terminate()
            except Exception as e:
                log("journal reader: %s" % e)
            self.stop.wait(5)

    def status_poller(self):
        while not self.stop.is_set():
            try:
                snap = status.snapshot(load_config())
                with self.lock:
                    if snap != self.sys:
                        self.sys = snap
                        self.dirty = True
            except Exception as e:
                log("status: %s" % e)
            self.stop.wait(STATUS_POLL_S)

    # ---- actions ----
    def send_beacon(self):
        try:
            info = beacon.send_beacon(load_config())
            log("beacon sent: %s" % info)
            self.set_toast("Beacon sent", render.RX_GREEN)
        except Exception as e:
            log("beacon failed: %s" % e)
            self.set_toast("Beacon failed", render.TX_RED)

    def switch_wifi(self):
        """Toggle Client <-> Access Point. The splash for the new mode is shown
        right away; nmcli can take several seconds in the background."""
        target = "client" if status.wifi_mode() == "AP" else "ap"
        self.show_splash(render.mode_splash("AP" if target == "ap" else "CL"))
        log("button 23 held: switching WiFi to %s mode" % target)
        try:
            r = subprocess.run(["sudo", "-n", "/usr/local/bin/kp4pra-wifi-mode", target],
                               capture_output=True, text=True, timeout=120)
            log("wifi-mode %s: rc=%s %s" % (target, r.returncode,
                                           (r.stdout or r.stderr).strip()[:120]))
            if r.returncode != 0:
                self.set_toast("WiFi switch failed", render.TX_RED)
                self.splash_until = 0
        except Exception as e:
            log("wifi-mode %s failed: %s" % (target, e))
            self.set_toast("WiFi switch failed", render.TX_RED)
            self.splash_until = 0
        with self.lock:
            self.dirty = True

    def show_splash(self, img):
        """Full-screen message for SPLASH_S seconds (wakes the screen)."""
        self.splash_until = time.time() + SPLASH_S
        with self.dev_lock:
            if self.off:
                self.set_off(False)
            self.dev.show(img)

    def set_toast(self, text, color):
        with self.lock:
            self.toast = (text, color, time.time() + TOAST_S)
            self.dirty = True

    def set_off(self, off):
        if off != self.off:
            self.off = off
            with self.dev_lock:
                self.dev.sleep(off)
            self.dirty = True

    # ---- render ----
    def draw(self, t):
        with self.lock:
            state = dict(self.sys)
            state["frames"] = list(self.frames)
            state["current"] = self.current
            toast = self.toast
        img = render.screen1(state, t) if self.screen == 1 else render.screen2(state, t)
        if toast and time.time() < toast[2]:
            from PIL import ImageDraw
            d = ImageDraw.Draw(img)
            y = 3 * render.ROW_H
            d.rectangle([0, y, render.W - 1, y + 2 * render.ROW_H], fill=render.BAR_BG)
            render._row(d, 3, toast[0], toast[1], 0)
        with self.dev_lock:
            if time.time() >= self.splash_until:
                self.dev.show(img)

    def run(self):
        try:
            self._run()
        finally:
            self.stop.set()

    def on_buttons(self, a, b, now):
        """Button state machine (called every 50 ms with current levels).

        23 (A) release before 3 s: wake if off, else switch screen 1 <-> 2.
        23 (A) held 3 s: switch WiFi Client <-> Access Point, once.
        24 (B) release before 3 s: screen off <-> on.
        24 (B) held 3 s: send a beacon once (no off/on on release).
        Returns "beacon" or "wifi" when that action must run, else None."""
        action = None
        pa, pb = self._prev
        if a and not pa:
            self._a_down, self._a_fired = now, False
        if a and self._a_down is not None and not self._a_fired \
                and now - self._a_down >= LONG_PRESS_S:
            self._a_fired = True
            action = "wifi"
        if not a and pa:
            if not self._a_fired:
                if self.off:
                    self.set_off(False)
                else:
                    self.screen = 2 if self.screen == 1 else 1
                    self.dirty = True
                self.splash_until = 0
            self._a_down = None
        if b and not pb:
            self._b_down, self._b_fired = now, False
        if b and self._b_down is not None and not self._b_fired \
                and now - self._b_down >= LONG_PRESS_S:
            self._b_fired = True
            action = "beacon"
        if not b and pb:
            if not self._b_fired:
                self.set_off(not self.off)
            self.splash_until = 0
            self._b_down = None
        self._prev = (a, b)
        return action

    def _run(self):
        self.dev.show(render.splash())
        threading.Thread(target=self.journal_reader, daemon=True).start()
        threading.Thread(target=self.status_poller, daemon=True).start()
        self.splash_until = time.time() + SPLASH_S
        last_draw = 0.0
        last_cfg = time.time()
        while not self.stop.is_set():
            now = time.time()
            a, b = self.dev.buttons()
            act = self.on_buttons(a, b, now)
            if act == "beacon":
                threading.Thread(target=self.send_beacon, daemon=True).start()
            elif act == "wifi":
                threading.Thread(target=self.switch_wifi, daemon=True).start()

            if now - last_cfg >= CONFIG_POLL_S:
                last_cfg = now
                cfg = reload_config()
                if tft_mode(cfg) == "off":
                    log("display.tft set to off - stopping")
                    return
                if tft_rotation(cfg) != self.rotation:
                    log("display.tft_rotation changed - restarting display")
                    return

            # Redraw on new data, and 4x/s for the scrolling (marquee) rows.
            if not self.off and now >= self.splash_until:
                if self.dirty or now - last_draw >= 0.25:
                    self.dirty = False
                    self.draw(now)
                    last_draw = now
            time.sleep(0.05)


def main():
    p = hw.pins_for()
    if not p:
        write_status("tft", {"state": "unsupported",
                             "detail": "board not supported (Raspberry Pi or Orange Pi Zero 2W)"})
        log("unsupported board - exiting")
        return 0
    announced = None
    while True:
        cfg = reload_config() or load_config()
        mode = tft_mode(cfg)
        state, detail = None, ""
        if mode == "off":
            state, detail = "off", "disabled in Config"
        elif not os.path.exists(hw.find_spidev(p)):
            state, detail = "no_spi", "SPI not enabled - run: sudo bash scripts/setup-tft.sh"
        elif mode == "auto" and not hw.probe(p):
            state, detail = "not_detected", "no Mini PiTFT detected (auto)"
        if state:
            if announced != state:
                log(detail)
                announced = state
            write_status("tft", {"state": state, "detail": detail, "mode": mode})
            time.sleep(CONFIG_POLL_S)
            continue
        dev = None
        try:
            dev = hw.ST7789(p, rotation=tft_rotation(cfg))
            dev.rotation = tft_rotation(cfg)
            write_status("tft", {"state": "running", "detail": "display active", "mode": mode})
            log("display active (%s)" % mode)
            announced = "running"
            Display(dev).run()
        except Exception as e:
            log("display error: %s" % e)
            write_status("tft", {"state": "error", "detail": str(e), "mode": mode})
            time.sleep(CONFIG_POLL_S)
        finally:
            if dev:
                dev.close()


if __name__ == "__main__":
    sys.exit(main())
