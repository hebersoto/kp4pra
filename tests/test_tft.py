"""Mini PiTFT display: Dire Wolf log -> UZ7HO tokens, screens, buttons, beacon.

Run: PYTHONPATH=src python3 -m unittest discover -s tests
"""
import json
import os
import struct
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from tft import beacon, monitor, render, saver, status   # noqa: E402

try:
    from tft import main as tftmain                  # noqa: E402
except Exception:                                    # pragma: no cover
    tftmain = None


class DirewolfLogToUZ7HO(unittest.TestCase):
    """Tokens must match the UZ7HO Soundmodem example (ConnectModeSoundModem)."""

    def tok(self, line):
        fr = monitor.parse_line(line)
        self.assertIsNotNone(fr, line)
        return fr

    def test_u_frames(self):
        self.assertEqual(self.tok("[0.4 20:04:04] NP4JM-11>NP4JJ-11:(SABM cmd, p=1)").token, "SABM C P")
        self.assertEqual(self.tok("[0.4 20:04:06] KP3A-5>NP4JM-11:(UA res, f=1)").token, "UA R F")
        self.assertEqual(self.tok("[0.4 20:04:35] NP4JM-11>NP4JJ-11:(DM res, f=1)").token, "DM R F")
        self.assertEqual(self.tok("[0.4 20:04:02] NP4JM-11>KP3A-5:(DISC cmd, p=1)").token, "DISC C P")

    def test_s_frames(self):
        self.assertEqual(self.tok("[0.4 20:03:18] NP4JM-4>NP3QL-11:(RR res, n(r)=3, f=1)").token, "RR R F R3")
        self.assertEqual(self.tok("[0.4 20:03:22] NP3QL-11>NP4JM-4:(RR cmd, n(r)=3, p=1)").token, "RR C P R3")
        self.assertEqual(self.tok("[0.4 20:08:04] KP4CE-10>KP3M-10:(RR res, n(r)=1, f=0)").token, "RR R R1")

    def test_i_frame(self):
        fr = self.tok("[0L 20:08:05] KP3M-10>KP4CE-10:(I cmd, n(s)=2, n(r)=5, p=1, pid=0xf0)[B2FHM$]")
        self.assertEqual(fr.token, "I C P R5 S2")             # <I C [P] Rn Sn>
        self.assertEqual(fr.info, "[B2FHM$]")

    def test_xid_test_named_unknown_unkn(self):
        self.assertEqual(self.tok("[0L 20:07:14] KP3M-10>KP4CE-10:(XID res, f=1) stuff").token, "XID R F")
        self.assertEqual(self.tok("[0L 20:07:14] KP3M-10>KP4CE-10:(TEST cmd, p=0)").token, "TEST C")
        self.assertEqual(self.tok("[0.4] KP4CE-10>KP3M-10:(FRMR res, f=1)").token, "FRMR R F")
        self.assertEqual(self.tok("[0.4 20:07:14] KP4CE-10>KP3M-10:(U other???)").token, "UNKN")

    def test_no_pid_or_len(self):
        for l in ("[0L] KP3M-10>KP4CE-10:(I cmd, n(s)=2, n(r)=5, p=0, pid=0xf0)x",
                  "[0.4] NODE-1>ID:(UI cmd, p=0) beacon"):
            self.assertNotRegex(self.tok(l).short, r"(?i)pid|len")

    def test_direction(self):
        self.assertEqual(self.tok("[0L 20:07:17] KP3M-10>KP4CE-10:(UA res, f=1)").direction, monitor.TX)
        self.assertEqual(self.tok("[0H 20:07:17] KP3M-10>KP4CE-10:(UA res, f=1)").direction, monitor.TX)
        self.assertEqual(self.tok("[0.4 20:07:16] KP4CE-10>KP3M-10:(SABM cmd, p=1)").direction, monitor.RX)
        self.assertEqual(self.tok("[0 20:07:16] KP4CE-10>KP3M-10:(SABM cmd, p=1)").direction, monitor.RX)
        self.assertEqual(self.tok("[0.4.1] KP4CE-10>KP3M-10:(SABM cmd, p=1)").direction, monitor.RX)

    def test_aprs_frame(self):
        fr = self.tok("[0.3 20:08:15] NP4JM-10>APRS,WIDE1-1,WIDE2-1*:!1824.17N/06603.90W-Hi")
        self.assertTrue(fr.aprs)
        self.assertEqual(fr.short, "NP4JM-10 <UI C> Hi")       # no TOCALL, no position
        self.assertEqual(fr.route, "NP4JM-10")

    def test_ansi_and_noise(self):
        fr = self.tok("\x1b[0;32m[0.4 20:07:16] KP4CE-10>KP3M-10:(SABM cmd, p=1)\x1b[0m")
        self.assertEqual(fr.short, "KP4CE-10>KP3M-10 <SABM C P>")
        for noise in ("KP4CE-10 audio level = 35(18/10)   [NONE]   |||||||__",
                      "Position, House QTH (VHF), ...", "", "Dire Wolf version 1.8.2"):
            self.assertIsNone(monitor.parse_line(noise))


class UIText(unittest.TestCase):
    """UI lines: FROM <UI C> text - no TOCALL, single spaces, no GPS data."""

    def short(self, line):
        return monitor.parse_line(line).short

    def test_connected_frames_start_with_from_to(self):
        self.assertEqual(self.short("[0.4] KP3M-11>WP4DMD-2:(RR res, n(r)=3, f=1)"),
                         "KP3M-11>WP4DMD-2 <RR R F R3>")

    def test_position_removed_spaces_collapsed(self):
        self.assertEqual(self.short("[0.4] NP4JM-10>APRS,WIDE1-1:!1824.17N/06603.90W-Hello   from    Ponce"),
                         "NP4JM-10 <UI C> Hello from Ponce")
        self.assertEqual(self.short("[0.4] N0CALL>APRS:/092345z4903.50N/07201.75W>088/036/A=001234Moving"),
                         "N0CALL <UI C> Moving")
        self.assertEqual(self.short("[0.4] N0CALL>APRS:!/5L!!<*e7>7P[ compressed comment"),
                         "N0CALL <UI C> compressed comment")
        self.assertEqual(self.short("[0.4] KP4ABC-9>T2SP0W,WIDE1-1:`c5Kl!x>/`\"4F}Mobile 146.52"),
                         "KP4ABC-9 <UI C> Mobile 146.52")

    def test_digipeaters_shown_wide_omitted(self):
        # UI via a named digi and a used WIDE1 (WIDE1-1 -> WIDE1*)
        self.assertEqual(self.short("[0.4] NP4JM-10>APRS,KP4DIG-1,WIDE1*,WIDE2-1:>Hello"),
                         "NP4JM-10 via KP4DIG-1 <UI C> Hello")
        # two digis, last one marked; unused WIDE2-1 left out
        self.assertEqual(self.short("[0.4] NP4JM-10>APRS,KP4A,KP4B-2*,WIDE2-1:>Hi"),
                         "NP4JM-10 via KP4A,KP4B-2 <UI C> Hi")
        # connected mode through a digipeater
        self.assertEqual(self.short("[0.4] KP3M-11>WP4DMD-2,KP4DIG*:(RR res, n(r)=3, f=1)"),
                         "KP3M-11>WP4DMD-2 via KP4DIG <RR R F R3>")
        # not digipeated (yet): no via, WIDE path hidden
        self.assertEqual(self.short("[0L] KP4PRA-10>APDW17,WIDE2-2:>Beacon"),
                         "KP4PRA-10 <UI C> Beacon")
        self.assertEqual(self.short("[0L] KP3M-11>WP4DMD-2,KP4DIG:(SABM cmd, p=1)"),
                         "KP3M-11>WP4DMD-2 <SABM C P>")
        # only WIDEs used: nothing to show
        self.assertEqual(self.short("[0.4] N0CALL>APRS,WIDE1,WIDE2*:>x"), "N0CALL <UI C> x")
        self.assertEqual(monitor.used_digis(",WIDE1-1"), ())

    def test_other_aprs_types(self):
        self.assertEqual(self.short("[0.4] N0CALL>APRS::KP3M-10  :Hello there{12"),
                         "N0CALL <UI C> KP3M-10: Hello there")
        self.assertEqual(self.short("[0.4] N0CALL>APRS:>Net tonight 8pm"), "N0CALL <UI C> Net tonight 8pm")
        self.assertEqual(self.short("[0.4] N0CALL>APRS:$GPRMC,123519,A,4807.038,N"), "N0CALL <UI C>")
        self.assertEqual(self.short("[0.4] N0CALL>APRS:;LEADER   *092345z4903.50N/07201.75W>Convoy"),
                         "N0CALL <UI C> LEADER Convoy")


class Screens(unittest.TestCase):
    def state(self):
        frames = [monitor.parse_line(l) for l in (
            "[0.4 20:07:16] KP4CE-10>KP3M-10:(SABM cmd, p=1)",
            "[0L 20:07:17] KP3M-10>KP4CE-10:(UA res, f=1)",
            "[0.3 20:08:15] NP4JM-10>APRS,WIDE1-1:!1824.17N/06603.90W-Hi")]
        return {"call": "KP3M-10", "mode": "AP", "ip": "172.16.0.1", "wifi_clients": 2,
                "bt_clients": 1, "winlink_pending": 3, "frames": frames, "current": frames[-1]}

    def test_sizes_and_colors(self):
        s = self.state()
        for img in (render.splash(), render.screen1(s, 0), render.screen2(s, 1.5),
                    render.screen1({"mode": "CL", "ip": "10.0.0.5"}, 0)):
            self.assertEqual(img.size, (240, 240))
        sp = render.splash()
        self.assertEqual(sp.getpixel((2, 2)), render.WHITE)
        self.assertIn(render.KP4PRA_GREEN, [c for _, c in sp.getcolors(65536)])
        s1 = render.screen1(s, 0)
        colors = [c for _, c in s1.getcolors(65536)]
        self.assertIn(render.TX_RED, colors)
        self.assertIn(render.RX_GREEN, colors)

    def test_status_row_is_identical_on_both_screens(self):
        s = self.state()
        a = render.screen1(s, 0).crop((0, 210, 240, 240)).tobytes()
        b = render.screen2(s, 7.0).crop((0, 210, 240, 240)).tobytes()
        self.assertEqual(a, b)


class Status(unittest.TestCase):
    def test_station_call(self):
        self.assertEqual(status.station_call({"station": {"callsign": "kp3m", "ssid": "10"}}), "KP3M-10")
        self.assertEqual(status.station_call({"station": {"callsign": "KP3M", "ssid": ""}}), "KP3M")

    def test_winlink_pending(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "mailq"))
            for i, st in enumerate(("Holding", "Approved", "Sent", "Rejected", "Sending")):
                with open(os.path.join(d, "mailq", "m%d.json" % i), "w") as f:
                    json.dump({"status": st}, f)
            self.assertEqual(status.winlink_pending({"paths": {"data": d}}), 3)


class Beacon(unittest.TestCase):
    CFG = {"station": {"callsign": "KP3M", "ssid": "10", "mygrid": "FK68"},
           "aprs": {"tocall": "APKP41", "digipath": ["WIDE1-1", "WIDE2-1"],
                    "beacon": {"symbol_table": "/", "symbol_code": "-", "comment": "KP4PRA"}}}

    def test_position_from_grid(self):
        info = beacon.beacon_info(self.CFG)
        self.assertTrue(info.startswith("="), info)
        self.assertTrue(info.endswith("-KP4PRA"), info)

    def test_no_position_is_status(self):
        cfg = {"station": {"callsign": "KP3M", "ssid": "10"},
               "aprs": {"beacon": {"comment": "KP4PRA"}}}
        self.assertEqual(beacon.beacon_info(cfg), ">KP4PRA")

    def test_agw_v_frame(self):
        fr = beacon.agw_frame("KP3M-10", "APKP41", "=x", ["WIDE1-1", "WIDE2-1"])
        h = beacon.AGW_HEADER.unpack(fr[:beacon.AGW_HEADER.size])
        self.assertEqual(chr(h[4]), "V")
        self.assertEqual(h[6], 0xF0)
        self.assertEqual(h[8].rstrip(b"\0"), b"KP3M-10")
        self.assertEqual(h[9].rstrip(b"\0"), b"APKP41")
        data = fr[beacon.AGW_HEADER.size:]
        self.assertEqual(len(data), h[10])
        self.assertEqual(data[0], 2)
        self.assertEqual(data[1:11].rstrip(b"\0"), b"WIDE1-1")
        self.assertEqual(data[21:], b"=x")

    def test_agw_m_frame_without_path(self):
        fr = beacon.agw_frame("KP3M-10", "APKP41", ">hi", [])
        h = beacon.AGW_HEADER.unpack(fr[:beacon.AGW_HEADER.size])
        self.assertEqual(chr(h[4]), "M")
        self.assertEqual(fr[beacon.AGW_HEADER.size:], b">hi")


@unittest.skipIf(tftmain is None, "tft.main import failed")
class Buttons(unittest.TestCase):
    class Dev:
        def __init__(self):
            self.sleeping = False

        def sleep(self, on):
            self.sleeping = on

        def show(self, img):
            pass

    def make(self):
        d = tftmain.Display(self.Dev())
        return d

    def press(self, disp, a, b, t):
        return disp.on_buttons(a, b, t)

    def test_button23_switches_screens_and_wakes(self):
        d = self.make()
        self.press(d, True, False, 0.0); self.press(d, False, False, 0.1)
        self.assertEqual(d.screen, 2)
        self.press(d, True, False, 1.0); self.press(d, False, False, 1.1)
        self.assertEqual(d.screen, 1)
        d.set_off(True)
        self.press(d, True, False, 2.0); self.press(d, False, False, 2.1)
        self.assertFalse(d.off)
        self.assertEqual(d.screen, 1)          # woke on the same screen

    def test_button24_short_press_toggles_off_on(self):
        d = self.make()
        self.press(d, False, True, 0.0); self.press(d, False, False, 0.3)
        self.assertTrue(d.off and d.dev.sleeping)
        self.press(d, False, True, 1.0); self.press(d, False, False, 1.2)
        self.assertFalse(d.off)

    def test_button23_long_press_switches_wifi_once(self):
        d = self.make()
        actions = [self.press(d, True, False, t / 10) for t in range(0, 45)]
        self.assertEqual(actions.count("wifi"), 1)
        self.press(d, False, False, 4.6)
        self.assertEqual(d.screen, 1)          # long press does not switch screens
        self.assertFalse(d.off)

    def test_mode_splash_uses_splash_colors(self):
        for mode in ("AP", "CL"):
            img = render.mode_splash(mode)
            self.assertEqual(img.size, (240, 240))
            self.assertEqual(img.getpixel((2, 2)), render.WHITE)
            self.assertIn(render.KP4PRA_GREEN, [c for _, c in img.getcolors(65536)])

    def test_button24_long_press_beacons_once(self):
        d = self.make()
        actions = [self.press(d, False, True, t / 10) for t in range(0, 45)]
        self.assertEqual(actions.count("beacon"), 1)
        self.press(d, False, False, 4.6)
        self.assertFalse(d.off)                # long press does not blank the screen


@unittest.skipIf(tftmain is None, "tft.main import failed")
class ScreenSaver(unittest.TestCase):
    def make(self, t0=1000.0):
        d = tftmain.Display(Buttons.Dev())
        d.last_activity, d.splash_until = t0, 0
        d.sys = {"call": "KP4PRA-10"}
        return d

    def test_centipede_after_3_min_then_black_after_2(self):
        d = self.make()
        d.saver_tick(1000 + 179)
        self.assertIsNone(d.saver)
        d.saver_tick(1000 + 180)
        self.assertEqual(d.saver, "worm")
        self.assertEqual(d.worm.word, "KP4PRA")
        d.saver_tick(1000 + 180 + 119)
        self.assertEqual(d.saver, "worm")
        d.saver_tick(1000 + 180 + 120)
        self.assertEqual(d.saver, "blank")
        self.assertTrue(d.dev.sleeping)

    def test_traffic_wakes_with_data(self):
        d = self.make()
        d.saver_tick(1200); d.saver_tick(1400)
        self.assertEqual(d.saver, "blank")
        d.traffic_seen = True
        d.saver_tick(1401)
        self.assertIsNone(d.saver)
        self.assertFalse(d.dev.sleeping)
        d.saver_tick(1401 + 179)             # idle time restarts from the traffic
        self.assertIsNone(d.saver)

    def test_button_only_wakes_then_works_normally(self):
        for btn in ((True, False), (False, True)):
            d = self.make()
            d.saver_tick(1200)
            self.assertEqual(d.saver, "worm")
            acts = [d.on_buttons(*btn, 1200 + k / 10) for k in range(0, 45)]  # even held 4.5 s
            d.on_buttons(False, False, 1205)
            self.assertEqual(acts.count(None), len(acts))     # no wifi / beacon
            self.assertIsNone(d.saver)
            self.assertEqual((d.screen, d.off), (1, False))   # same screen, still on
            d.on_buttons(True, False, 1206); d.on_buttons(False, False, 1206.1)
            self.assertEqual(d.screen, 2)                     # buttons work again

    def test_no_saver_when_screen_turned_off(self):
        d = self.make()
        d.set_off(True)
        d.saver_tick(2000)
        self.assertIsNone(d.saver)


class Centipede(unittest.TestCase):
    def test_moves_one_cell_wraps_and_never_reverses(self):
        import random
        c = saver.Centipede("KP4PRA", rng=random.Random(7))
        for _ in range(500):
            d0 = c.dir
            c.step()
            self.assertNotEqual(c.dir, (-d0[0], -d0[1]))
            self.assertEqual(len(c.cells), 6)
            for (ax, ay), (bx, by) in zip(c.cells, c.cells[1:]):   # body stays joined
                self.assertEqual(abs(c._delta(ax, bx)) + abs(c._delta(ay, by)), 1)
            for x, y in c.cells:
                self.assertTrue(0 <= x < saver.GRID and 0 <= y < saver.GRID)

    def test_render_and_word(self):
        self.assertEqual(saver.word_for("KP4PRA-10"), "KP4PRA")
        self.assertEqual(saver.word_for(""), "KP4PRA")
        c = saver.Centipede("NP4JM-7")
        img = c.render(0.12)
        self.assertEqual(img.size, (240, 240))
        colors = [col for _, col in img.getcolors(65536)]
        self.assertIn(saver.HEAD, colors)
        self.assertIn(saver.BODY, colors)


@unittest.skipIf(tftmain is None, "tft.main import failed")
class ShutdownOnTFT(unittest.TestCase):
    """Web UI shutdown -> countdown + final message on the Mini PiTFT."""
    class Dev:
        def __init__(self):
            self.sleeping, self.shown, self.pressed = False, [], (False, False)
        def sleep(self, on): self.sleeping = on
        def show(self, img): self.shown.append(img)
        def buttons(self): return self.pressed

    def setUp(self):
        self.status = {}
        self._orig = tftmain.read_status
        tftmain.read_status = lambda name: dict(self.status.get(name, {}))
        tftmain._TERM.clear()

    def tearDown(self):
        tftmain.read_status = self._orig
        tftmain._TERM.clear()

    def make(self):
        d = tftmain.Display(self.Dev())
        d.splash_until = 0
        return d

    def tick(self, d, now):
        d._shutdown_poll = 0
        d.shutdown_tick(now)
        if d.shutdown_deadline is not None:
            d.draw_shutdown(now)

    def test_countdown_wakes_from_saver_and_off(self):
        for start in ("saver", "off"):
            d = self.make()
            if start == "saver":
                d.sys = {"call": "KP4PRA-10"}; d.last_activity = 0; d.saver_tick(1000)
                self.assertEqual(d.saver, "worm")
            else:
                d.set_off(True)
            self.status["shutdown"] = {"deadline": 2010.0}
            self.tick(d, 2000.0)
            self.assertIsNone(d.saver)
            self.assertFalse(d.off)
            self.assertFalse(d.dev.sleeping)
            self.assertEqual(d._shutdown_shown, 10)
            self.tick(d, 2003.2)
            self.assertEqual(d._shutdown_shown, 7)
            self.tick(d, 2010.5)
            self.assertIsNone(d._shutdown_shown)        # final message

    def test_cancel_returns_to_normal(self):
        d = self.make()
        self.status["shutdown"] = {"deadline": 2010.0}
        self.tick(d, 2000.0)
        del self.status["shutdown"]
        self.tick(d, 2004.0)
        self.assertIsNone(d.shutdown_deadline)
        self.assertTrue(d.dirty)

    def test_stale_file_ignored(self):
        d = self.make()
        self.status["shutdown"] = {"deadline": 1000.0}
        self.tick(d, 1000.0 + tftmain.SHUTDOWN_STALE_S + 1)
        self.assertIsNone(d.shutdown_deadline)

    def test_stop_while_shutting_down_keeps_message(self):
        d = self.make()
        self.status["shutdown"] = {"deadline": 2010.0}
        self.tick(d, 2000.0)
        tftmain._TERM.set()
        d.stop.clear()
        d._run_loop_once = None
        # run the real loop: it must return at once and keep the panel on
        d.dev.show(render.splash())
        orig_threads = tftmain.threading.Thread
        tftmain.threading.Thread = lambda *a, **k: type("T", (), {"start": lambda self: None})()
        try:
            d._run()
        finally:
            tftmain.threading.Thread = orig_threads
        self.assertTrue(d.keep_on_exit)
        self.assertFalse(d.dev.sleeping)

    def test_shutdown_screens_render(self):
        for left in (10, 3, None):
            img = render.shutdown_screen(left)
            self.assertEqual(img.size, (240, 240))


@unittest.skipIf(tftmain is None, "tft.main import failed")
class InstallProgressOnTFT(unittest.TestCase):
    """Setup progress (install-state.json) on the Mini PiTFT."""
    Dev = ShutdownOnTFT.Dev

    def setUp(self):
        self.state = {}
        self._orig = (tftmain.read_install_state, tftmain.system_stop_kind)
        tftmain.read_install_state = lambda: dict(self.state)
        tftmain._TERM.clear()

    def tearDown(self):
        tftmain.read_install_state, tftmain.system_stop_kind = self._orig
        tftmain._TERM.clear()

    def make(self):
        d = tftmain.Display(self.Dev())
        d.splash_until = 0
        return d

    def tick(self, d, now):
        d._install_poll = 0
        d.install_tick(now)

    def test_active_setup_wakes_and_shows(self):
        d = self.make()
        d.sys = {"call": "KP4PRA-10"}; d.last_activity = 0; d.saver_tick(1000)
        self.assertEqual(d.saver, "worm")
        self.state = {"state": "dra_setup", "message": "building", "updated": 1000}
        self.tick(d, 1001)
        self.assertEqual(d.install, ("dra_setup", "building"))
        self.assertIsNone(d.saver)
        d.draw_install()
        self.assertEqual(len(d.dev.shown), 1)
        d.draw_install()                                   # unchanged -> not redrawn
        self.assertEqual(len(d.dev.shown), 1)

    def test_complete_shows_splash_then_normal(self):
        d = self.make()
        self.state = {"state": "checking", "message": "", "updated": 1000}
        self.tick(d, 1001)
        self.state = {"state": "complete", "message": "", "updated": 1010}
        self.tick(d, 1011)
        self.assertIsNone(d.install)
        self.assertGreater(d.splash_until, 1011)          # "Setup complete" for 10 s

    def test_failed_closed_by_button(self):
        d = self.make()
        self.state = {"state": "failed", "message": "x", "updated": 1000}
        self.tick(d, 1001)
        self.assertEqual(d.install[0], "failed")
        d._failed_dismissed = d._install_upd               # what a button press does
        d.install = None
        self.tick(d, 1003)
        self.assertIsNone(d.install)                       # same failure stays closed

    def test_stale_setup_reported(self):
        d = self.make()
        self.state = {"state": "dra_setup", "message": "x", "updated": 1000}
        self.tick(d, 1000 + 3601)
        self.assertEqual(d.install[0], "failed")

    def test_reboot_shows_restarting_poweroff_keeps_shutdown_text(self):
        for kind in ("reboot", "poweroff"):
            d = self.make()
            tftmain.system_stop_kind = lambda k=kind: k
            tftmain._TERM.set()
            orig_threads = tftmain.threading.Thread
            tftmain.threading.Thread = lambda *a, **k: type("T", (), {"start": lambda self: None})()
            try:
                d._run()
            finally:
                tftmain.threading.Thread = orig_threads
            self.assertTrue(d.keep_on_exit, kind)
            self.assertFalse(d.dev.sleeping)


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(tftmain is None, "tft.main import failed")
class Rotation(unittest.TestCase):
    def test_rotation_setting(self):
        self.assertEqual(tftmain.tft_rotation({}), 0)
        self.assertEqual(tftmain.tft_rotation({"display": {"tft_rotation": 180}}), 180)
        self.assertEqual(tftmain.tft_rotation({"display": {"tft_rotation": "180"}}), 180)
        self.assertEqual(tftmain.tft_rotation({"display": {"tft_rotation": 90}}), 0)

    def test_orientation_table(self):
        from tft import hw
        self.assertEqual(hw.ORIENTATIONS[0], (0x00, 0))
        self.assertEqual(hw.ORIENTATIONS[180], (0xC0, 80))
