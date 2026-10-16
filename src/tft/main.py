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
  - Screen saver: no traffic for 3 min -> the callsign crawls around the
    screen like the Atari centipede (src/tft/saver.py); 2 min later the
    screen goes black. RX/TX traffic brings back the screen with the data.
    A button press while the saver runs only wakes the screen (no action);
    after that the buttons work as above. A screen turned off with button 24
    stays off.
  - Shutdown from the web UI (/run/kp4pra-tnc/shutdown.json): the screen
    wakes, clears and shows the same 10 -> 0 countdown (buttons ignored),
    then "Shutting Down, disconnect power in 15 seconds". On power-off
    (SIGTERM while shutting down, also for a "sudo poweroff" from SSH) that
    message is left on screen instead of blanking it.
Status for the web UI: /run/kp4pra-tnc/tft.json
"""

import collections
import json
import math
import os
import signal
import subprocess
import sys
import threading
import time

from common.config import load_config, reload_config
from common.runtime_status import read_status, write_status
from tft import beacon, hw, monitor, render, saver, status

SPLASH_S = 10
LONG_PRESS_S = 3.0
CONFIG_POLL_S = 10
STATUS_POLL_S = 5
FRAME_HISTORY = 20
TOAST_S = 3
SAVER_AFTER_S = 180          # no traffic for 3 min -> centipede
SAVER_BLANK_S = 120          # centipede for 2 min -> black screen
SAVER_FRAME_S = 0.1          # centipede animation, 10 frames/s
SHUTDOWN_POLL_S = 0.5        # how often to look for /run/kp4pra-tnc/shutdown.json
SHUTDOWN_STALE_S = 60        # ignore a shutdown file this long past its deadline

_TERM = threading.Event()    # SIGTERM received (service stop / system power-off)


INSTALL_STATE_FILE = "/rw/kp4pra-tnc/install-state.json"
INSTALL_ACTIVE = ("installing", "checking", "dra_setup")
INSTALL_POLL_S = 2
INSTALL_TITLES = {"installing": "Installing", "checking": "Finishing setup",
                  "dra_setup": "Setting up"}


def read_install_state() -> dict:
    try:
        with open(INSTALL_STATE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def system_stop_kind():
    """None while running; "reboot" or "poweroff" while systemd stops the system."""
    if not system_stopping():
        return None
    try:
        jobs = subprocess.run(["systemctl", "list-jobs", "--no-legend"],
                              capture_output=True, text=True, timeout=3).stdout
    except Exception:
        jobs = ""
    return "reboot" if ("reboot.target" in jobs or "kexec.target" in jobs) else "poweroff"


def system_stopping() -> bool:
    """True while systemd is shutting the system down (poweroff/reboot)."""
    try:
        r = subprocess.run(["systemctl", "is-system-running"],
                           capture_output=True, text=True, timeout=3)
        return r.stdout.strip() == "stopping"
    except Exception:
        return False


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
        self.saver = None            # None | "worm" | "blank"
        self.saver_since = 0.0
        self.worm = None
        self.last_activity = time.time()
        self.traffic_seen = False
        self._swallow = False        # button press that woke the saver
        self.shutdown_deadline = None  # epoch seconds of the pending power-off
        self._shutdown_shown = None    # last countdown value drawn
        self._shutdown_poll = 0.0
        self.keep_on_exit = False      # leave the shutdown message on screen
        self.install = None            # (state, message) while setup runs / failed
        self._install_upd = None
        self._install_drawn = None
        self._install_poll = 0.0
        self._failed_dismissed = None  # "updated" of a failed state closed by a button

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
                            self.traffic_seen = True
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

    # ---- screen saver ----
    def enter_saver(self, now):
        call = (self.sys or {}).get("call", "")
        self.worm = saver.Centipede(call, now=now)
        self.saver, self.saver_since = "worm", now
        log("no traffic for %d s - screen saver" % SAVER_AFTER_S)

    def exit_saver(self, now):
        with self.dev_lock:
            if self.saver == "blank":
                self.dev.sleep(False)
            self.saver = None
        self.worm = None
        self.last_activity = now
        self.dirty = True

    def saver_tick(self, now):
        """Start the saver after SAVER_AFTER_S idle, blank it SAVER_BLANK_S later."""
        with self.lock:
            traffic, self.traffic_seen = self.traffic_seen, False
        if traffic:
            self.last_activity = now
            if self.saver:
                self.exit_saver(now)
            return
        if self.saver is None:
            if not self.off and now >= self.splash_until \
                    and now - self.last_activity >= SAVER_AFTER_S:
                self.enter_saver(now)
        elif self.saver == "worm" and now - self.saver_since >= SAVER_BLANK_S:
            with self.dev_lock:
                self.saver = "blank"
                self.dev.sleep(True)

    # ---- shutdown (web UI) ----
    def shutdown_tick(self, now):
        """Follow /run/kp4pra-tnc/shutdown.json written by the web UI."""
        if now - self._shutdown_poll < SHUTDOWN_POLL_S:
            return
        self._shutdown_poll = now
        try:
            deadline = float(read_status("shutdown").get("deadline") or 0)
        except (TypeError, ValueError):
            deadline = 0
        if deadline and now > deadline + SHUTDOWN_STALE_S:
            deadline = 0                 # stale (web restarted mid-countdown)
        if deadline and self.shutdown_deadline is None:
            log("shutdown requested from the web UI - showing countdown")
            if self.saver:
                self.exit_saver(now)
            self.set_off(False)
            self.splash_until = 0
            self._shutdown_shown = None
        elif not deadline and self.shutdown_deadline is not None:
            log("shutdown cancelled - back to normal screen")
            self.last_activity = now
            self.dirty = True
        self.shutdown_deadline = deadline or None

    def draw_shutdown(self, now, final=False):
        left = None if final else math.ceil(self.shutdown_deadline - now)
        if left is not None and left <= 0:
            left = None
        if left != self._shutdown_shown or final:
            self._shutdown_shown = left
            with self.dev_lock:
                self.dev.show(render.shutdown_screen(left))

    # ---- setup progress (bin/kp4pra-install-state) ----
    def install_tick(self, now):
        if now - self._install_poll < INSTALL_POLL_S:
            return
        self._install_poll = now
        d = read_install_state()
        st, msg, upd = d.get("state", "complete"), d.get("message", ""), d.get("updated")
        if st in INSTALL_ACTIVE and now - float(upd or now) > 3600:
            st, msg = "failed", "Setup has not finished after an hour. See the web page."
        if st == "failed" and self._failed_dismissed == upd:
            st = "complete"
        if st in INSTALL_ACTIVE or st == "failed":
            if self.install is None:
                log("setup in progress: %s - %s" % (st, msg))
                if self.saver:
                    self.exit_saver(now)
                self.set_off(False)
            self.install, self._install_upd = (st, msg), upd
        elif self.install is not None:
            was_active = self.install[0] in INSTALL_ACTIVE
            self.install, self._install_drawn = None, None
            self.last_activity, self.dirty = now, True
            if was_active and st == "complete":
                log("setup complete")
                self.show_splash(render.install_screen(
                    "Setup complete", msg or "Your KP4PRA TNC is ready.", kind="ok"))

    def draw_install(self):
        if self.install == self._install_drawn:
            return
        self._install_drawn = self.install
        st, msg = self.install
        if st == "failed":
            img = render.install_screen("Setup stopped", msg,
                                        "Press a button to close", kind="failed")
        else:
            img = render.install_screen(INSTALL_TITLES.get(st, "Setting up"), msg,
                                        "Please wait, do not unplug")
        with self.dev_lock:
            self.dev.show(img)

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
        While the screen saver runs, a press only wakes the screen: nothing
        else happens until both buttons are released.
        Returns "beacon" or "wifi" when that action must run, else None."""
        action = None
        pa, pb = self._prev
        if (a and not pa) or (b and not pb):
            self.last_activity = now
        if self.saver and (a or b):
            self.exit_saver(now)
            self._swallow = True
        if self._swallow:
            if not a and not b:
                self._swallow = False
            self._a_down = self._b_down = None
            self._prev = (a, b)
            return None
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
            if _TERM.is_set():
                # Stopped by systemd. While the system powers off, leave the
                # "disconnect power" message on screen instead of blanking;
                # while it restarts (e.g. during the DRA setup), "Restarting".
                kind = "poweroff" if self.shutdown_deadline is not None else system_stop_kind()
                if kind == "reboot":
                    self.set_off(False)
                    msg = self.install[1] if self.install else ""
                    with self.dev_lock:
                        self.dev.show(render.install_screen(
                            "Restarting", msg or "Back in about a minute.",
                            "Please wait, do not unplug"))
                    self.keep_on_exit = True
                elif kind == "poweroff":
                    self.set_off(False)
                    self.draw_shutdown(now, final=True)
                    self.keep_on_exit = True
                return
            self.shutdown_tick(now)
            if self.shutdown_deadline is not None:
                self._prev = self.dev.buttons()      # buttons ignored meanwhile
                self.draw_shutdown(now)
                time.sleep(0.05)
                continue
            self.install_tick(now)
            if self.install is not None:
                a, b = self.dev.buttons()
                pa, pb = self._prev
                if self.install[0] == "failed" and ((a and not pa) or (b and not pb)):
                    # Close the error screen; that press does nothing else.
                    self._failed_dismissed = self._install_upd
                    self.install, self._install_drawn = None, None
                    self.dirty, self._swallow = True, True
                self._prev = (a, b)
                if self.install is not None:
                    if now >= self.splash_until:
                        self.draw_install()
                    time.sleep(0.05)
                    continue
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

            self.saver_tick(now)
            if self.saver == "worm":
                if now - last_draw >= SAVER_FRAME_S:
                    self.worm.update(now)
                    with self.dev_lock:
                        if self.saver == "worm":
                            self.dev.show(self.worm.render(now))
                    last_draw = now
            # Redraw on new data, and 4x/s for the scrolling (marquee) rows.
            elif self.saver is None and not self.off and now >= self.splash_until:
                if self.dirty or now - last_draw >= 0.25:
                    self.dirty = False
                    self.draw(now)
                    last_draw = now
            time.sleep(0.05)


def main():
    signal.signal(signal.SIGTERM, lambda *_: _TERM.set())
    p = hw.pins_for()
    if not p:
        write_status("tft", {"state": "unsupported",
                             "detail": "board not supported (Raspberry Pi or Orange Pi Zero 2W)"})
        log("unsupported board - exiting")
        return 0
    announced = None
    while not _TERM.is_set():
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
            _TERM.wait(CONFIG_POLL_S)
            continue
        dev = None
        try:
            dev = hw.ST7789(p, rotation=tft_rotation(cfg))
            dev.rotation = tft_rotation(cfg)
            write_status("tft", {"state": "running", "detail": "display active", "mode": mode})
            log("display active (%s)" % mode)
            announced = "running"
            disp = Display(dev)
            disp.run()
            if disp.keep_on_exit:
                # Powering off: exit WITHOUT blanking the panel, so the
                # "disconnect power" message stays until the power goes.
                log("power-off: leaving the shutdown message on screen")
                os._exit(0)
        except Exception as e:
            log("display error: %s" % e)
            write_status("tft", {"state": "error", "detail": str(e), "mode": mode})
            _TERM.wait(CONFIG_POLL_S)
        finally:
            if dev:
                dev.close()


if __name__ == "__main__":
    sys.exit(main())
