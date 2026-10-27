"""Xara (VARA modem) integration: config generation, PTT plan, settings
validation and the GPIO PTT helper.

Run: PYTHONPATH=src python3 -m unittest discover -s tests
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from common import xara_conf as xc            # noqa: E402
from common import dra_platform as dp         # noqa: E402
from common import config_writer              # noqa: E402

# Xara 0.1.1 options (crates/xara/src/main.rs take() + cli.rs UiArgs). The
# config file is "key = value"; switches take no value (or yes/no).
XARA_VALUE_KEYS = {"audio", "capture", "playback", "call", "digi", "hf", "fm", "port",
                   "kiss-port", "persistence", "addr", "drive-db", "ui-token", "state-dir",
                   "ptt", "ptt-lead-ms", "ptt-tail-ms", "ptt-max-s", "record",
                   "ui-port", "ui-addr", "ui-host", "offer-level"}
XARA_SWITCHES = {"quiet", "ui-control", "radio", "registered"}


def xara_accepts(text: str):
    """Re-implementation of Xara 0.1.1 config_args() + check(); returns a
    list of errors (empty = Xara would start with this file)."""
    errs, a = [], {}
    for n, raw in enumerate(text.splitlines(), 1):
        l = raw.strip()
        if not l or l.startswith("#"):
            continue
        k, sep, v = l.partition("=")
        k, v = k.strip(), (v.strip() if sep else None)
        if not k or not all(c.islower() or c.isdigit() or c == "-" for c in k):
            errs.append(f"line {n}: expected key = value")
            continue
        if k in XARA_SWITCHES:
            if v not in (None, "yes", "true", "on", "no", "false", "off"):
                errs.append(f"line {n}: {k} is a switch")
            a[k] = v in (None, "yes", "true", "on")
        elif k in XARA_VALUE_KEYS:
            if v is None:
                errs.append(f"line {n}: {k} needs a value")
            a[k] = v
        else:
            errs.append(f"line {n}: unknown option --{k}")
    if not a.get("capture") or not a.get("playback"):
        errs.append("--capture and --playback are required")
    if not a.get("call"):
        errs.append("--call is required")
    elif not xc.valid_vara_call(a["call"]):
        errs.append("bad call " + a["call"])
    if "hf" in a and "fm" in a:
        errs.append("--hf and --fm cannot both be given")
    if a.get("hf") not in (None, "500", "2300", "2750"):
        errs.append("bad hf")
    if a.get("fm") not in (None, "narrow", "wide"):
        errs.append("bad fm")
    if a.get("radio") and not a.get("ptt"):
        errs.append("--radio needs --ptt")
    if a.get("ptt") and not a.get("radio"):
        errs.append("--ptt needs --radio")
    if a.get("ptt"):
        kind = a["ptt"].split(":")[0]
        if kind not in ("vox", "rts", "dtr", "cm108", "rigctld"):
            errs.append("bad ptt " + a["ptt"])
    if float(a.get("drive-db", -5)) > 0:
        errs.append("--drive-db must be <= 0")
    for k in ("ptt-lead-ms", "ptt-tail-ms"):
        if int(a.get(k, 0)) > 1000:
            errs.append(k + " too big")
    if int(a.get("port", 8300)) >= 65535:
        errs.append("--port must be below 65535")
    return errs


def cfg(**station):
    st = {"callsign": "KP4PRA", "ssid": "10", "sound": "", "ptt": "CM108", "ptt_param": ""}
    st.update(station)
    return {"station": st, "xara": {}}


def gen(c, adevice="plughw:Device,0", hid="/dev/hidraw2", registered_build=True):
    with patch.object(dp, "detect_platform", return_value="rpi"):
        return xc.generate(c, adevice=adevice, hidraw_lookup=lambda a: hid,
                           registered_build=registered_build)


class Generate(unittest.TestCase):
    def test_cm108_default(self):
        text, plan = gen(cfg())
        self.assertEqual(xara_accepts(text), [], text)
        self.assertIn("call = KP4PRA-10\n", text)
        self.assertIn("fm = narrow\n", text)
        self.assertIn("capture = plughw:Device,0\n", text)
        self.assertIn("playback = plughw:Device,0\n", text)
        self.assertIn("radio = on\n", text)
        self.assertIn("ptt = cm108:/dev/hidraw2\n", text)
        self.assertIn("addr = 0.0.0.0\n", text)
        self.assertIn("ui-port = 8080\n", text)
        self.assertIsNone(plan["gpio"])

    def test_cm108_missing_is_receive_only(self):
        text, plan = gen(cfg(), hid=None)
        self.assertEqual(xara_accepts(text), [], text)
        self.assertNotIn("radio", text.replace("# ", "#"))
        self.assertIn("receive only", plan["describe"])

    def test_dra_on_pi_uses_gpio_helper(self):
        text, plan = gen(cfg(ptt="GPIOD"), adevice="plughw:audioinjectorpi,0")
        self.assertEqual(xara_accepts(text), [], text)
        self.assertIn("ptt = rigctld:127.0.0.1:4539\n", text)
        self.assertEqual(plan["gpio"], {"chip": "gpiochip0", "line": 12, "active_low": False})

    def test_dra_on_orange_pi_remaps_pi_line(self):
        c = cfg(ptt="GPIOD", ptt_param="gpiochip0 12")
        with patch.object(dp, "detect_platform", return_value="opi-zero2w"):
            text, plan = xc.generate(c, adevice="plughw:DRAPiZero,0", hidraw_lookup=lambda a: None,
                                     registered_build=False)
        self.assertEqual(plan["gpio"]["chip"], "gpiochip1")
        self.assertEqual(plan["gpio"]["line"], 267)

    def test_active_low_gpio(self):
        _, plan = gen(cfg(ptt="GPIOD", ptt_param="gpiochip0 -17"))
        self.assertEqual(plan["gpio"], {"chip": "gpiochip0", "line": 17, "active_low": True})

    def test_serial_vox_none_rig(self):
        for ptt, param, want in (("RTS", "/dev/ttyUSB1", "ptt = rts:/dev/ttyUSB1\n"),
                                 ("DTR", "", "ptt = dtr:/dev/ttyUSB0\n"),
                                 ("VOX", "", "ptt = vox\n"),
                                 ("RIG", "", "ptt = rigctld\n"),
                                 ("RIG", "192.168.1.5:4532", "ptt = rigctld:192.168.1.5:4532\n")):
            text, _ = gen(cfg(ptt=ptt, ptt_param=param))
            self.assertEqual(xara_accepts(text), [], text)
            self.assertIn(want, text)
        text, _ = gen(cfg(ptt="NONE"))
        self.assertEqual(xara_accepts(text), [], text)
        self.assertNotIn("\nradio", text)

    def test_modes(self):
        for mode, line in (("fm-wide", "fm = wide"), ("hf-500", "hf = 500"),
                           ("hf-2300", "hf = 2300"), ("hf-2750", "hf = 2750")):
            c = cfg()
            c["xara"] = {"mode": mode}
            text, _ = gen(c)
            self.assertEqual(xara_accepts(text), [], text)
            self.assertIn(line + "\n", text)

    def test_settings(self):
        c = cfg()
        c["xara"] = {"call": "kp3m-5", "drive_db": -12, "ptt_lead_ms": 50, "ptt_tail_ms": 200,
                     "network": False, "kiss_port": "off", "ui_port": 0, "port": 8400}
        text, _ = gen(c)
        self.assertEqual(xara_accepts(text), [], text)
        for want in ("call = KP3M-5", "drive-db = -12", "ptt-lead-ms = 50", "ptt-tail-ms = 200",
                     "addr = 127.0.0.1", "kiss-port = off", "port = 8400"):
            self.assertIn(want + "\n", text)
        self.assertNotIn("ui-port", text)

    def test_ssid_vara_rules(self):
        for ssid, want in (("10", "KP4PRA-10"), ("", "KP4PRA"), ("0", "KP4PRA"), ("15", "KP4PRA-15"),
                           ("19", "KP4PRA-19"), ("20", "KP4PRA")):
            self.assertEqual(xc.xara_call(cfg(ssid=ssid)), want, ssid)
        self.assertTrue(xc.valid_vara_call("KP4PRA-T"))
        self.assertFalse(xc.valid_vara_call("KP4PRA-0"))
        self.assertFalse(xc.valid_vara_call("TOOLONGC"))

    def test_registered_default_on(self):
        text, plan = gen(cfg())
        self.assertEqual(xara_accepts(text), [], text)
        self.assertIn("registered = on\n", text)
        self.assertTrue(plan["registered"])

    def test_registered_only_with_registered_build(self):
        # Xara built without the feature refuses "registered": leave it out.
        text, plan = gen(cfg(), registered_build=False)
        self.assertNotIn("registered = on", text)
        self.assertIn("# registered speeds", text)
        self.assertFalse(plan["registered"])

    def test_registered_off(self):
        c = cfg()
        c["xara"] = {"registered": False}
        text, plan = gen(c)
        self.assertNotIn("registered", text)
        self.assertFalse(plan["registered"])

    def test_build_marker(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, "BUILD-FEATURES")
            self.assertFalse(xc.build_is_registered(f))
            open(f, "w").write("registered\nsource: x\n")
            self.assertTrue(xc.build_is_registered(f))

    def test_write_files(self):
        with tempfile.TemporaryDirectory() as d:
            conf, plan = os.path.join(d, "x", "kp4pra.conf"), os.path.join(d, "run", "ptt.json")
            with patch.object(dp, "detect_platform", return_value="rpi"), \
                 patch.object(xc, "resolve_adevice", return_value="plughw:audioinjectorpi,0"), \
                 patch.object(xc, "build_is_registered", return_value=False):
                xc.write(cfg(ptt="GPIOD"), conf_path=conf, plan_path=plan)
            self.assertEqual(oct(os.stat(conf).st_mode & 0o777), "0o640")
            self.assertEqual(json.load(open(plan))["gpio"]["line"], 12)


class Validation(unittest.TestCase):
    def ok(self, x):
        return config_writer.validate_config_updates({"xara": x})

    def test_good(self):
        self.assertEqual(self.ok({"mode": "hf-2300", "drive_db": -3, "version": "0.1.1",
                                  "kiss_port": "off", "network": True})[0], True)

    def test_bad(self):
        for x in ({"mode": "fm"}, {"drive_db": 1}, {"ptt_tail_ms": 5000}, {"version": "new"},
                  {"port": 8087}, {"ui_port": 8088}, {"call": "KP4PRA-0"}, {"network": "yes"},
                  {"registered": "on"}):
            self.assertFalse(self.ok(x)[0], x)


FAKE_GPIOD = textwrap.dedent('''
    import json, os
    LOG = os.environ["FAKE_GPIO_LOG"]
    def _log(v):
        with open(LOG, "a") as f: f.write(json.dumps(v) + "\\n")
    class LineSettings:
        def __init__(self, **kw): self.kw = kw
    class _Req:
        def set_value(self, line, val): _log([line, val])
        def release(self): _log("released")
    def request_lines(path, consumer, config):
        _log(["request", path, sorted(config)])
        return _Req()
''')
FAKE_LINE = textwrap.dedent('''
    class Direction: OUTPUT = "out"
    class Value: ACTIVE = 1; INACTIVE = 0
''')


class PttHelper(unittest.TestCase):
    """bin/kp4pra-xara-ptt with a fake libgpiod 2.x module."""

    def start(self, plan, port, max_s="120"):
        self.d = tempfile.TemporaryDirectory()
        d = self.d.name
        os.makedirs(os.path.join(d, "gpiod"))
        open(os.path.join(d, "gpiod", "__init__.py"), "w").write(FAKE_GPIOD)
        open(os.path.join(d, "gpiod", "line.py"), "w").write(FAKE_LINE)
        self.log = os.path.join(d, "gpio.log")
        planf = os.path.join(d, "ptt.json")
        json.dump(plan, open(planf, "w"))
        env = dict(os.environ, PYTHONPATH=d, FAKE_GPIO_LOG=self.log,
                   KP4PRA_XARA_PTT_PLAN=planf, KP4PRA_XARA_PTT_PORT=str(port),
                   KP4PRA_XARA_PTT_MAX_S=max_s)
        env.pop("NOTIFY_SOCKET", None)
        self.p = subprocess.Popen([sys.executable, os.path.join(ROOT, "bin", "kp4pra-xara-ptt")],
                                  env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        return self.p

    def tearDown(self):
        if getattr(self, "p", None) and self.p.poll() is None:
            self.p.terminate()
            self.p.wait(5)
        if getattr(self, "p", None):
            self.p.stdout.close()
        if getattr(self, "d", None):
            self.d.cleanup()

    def values(self):
        return [json.loads(l) for l in open(self.log)]

    def test_not_gpio_exits(self):
        p = self.start({"ptt": "vox", "gpio": None, "describe": "VOX"}, 45391)
        self.assertEqual(p.wait(10), 0)

    def test_keying(self):
        port = 45392
        self.start({"gpio": {"chip": "gpiochip0", "line": 12, "active_low": False}}, port)
        for _ in range(50):
            try:
                s = socket.create_connection(("127.0.0.1", port), timeout=1)
                break
            except OSError:
                time.sleep(0.1)
        f = s.makefile("rw")
        for cmd, want in (("T 1", "RPRT 0"), ("t", "1"), ("T 0", "RPRT 0"), ("t", "0"),
                          ("T 1", "RPRT 0"), ("F 145000000", "RPRT -11")):
            f.write(cmd + "\n"); f.flush()
            self.assertEqual(f.readline().strip(), want, cmd)
        f.close(); s.close()              # Xara gone while keyed -> PTT off
        time.sleep(0.5)
        self.p.terminate(); self.p.wait(5)
        v = self.values()
        self.assertEqual(v[0], ["request", "/dev/gpiochip0", [12]])
        sets = [x for x in v if isinstance(x, list) and x[0] == 12]
        self.assertEqual([x[1] for x in sets], [1, 0, 1, 0, 0])   # last: drop + shutdown
        self.assertEqual(v[-1], "released")

    def connect(self, port):
        for _ in range(50):
            try:
                return socket.create_connection(("127.0.0.1", port), timeout=1)
            except OSError:
                time.sleep(0.1)
        self.fail("helper did not listen")

    def test_stuck_ptt_released(self):
        port = 45393
        self.start({"gpio": {"chip": "gpiochip0", "line": 12, "active_low": False}}, port, max_s="1")
        s = self.connect(port)
        f = s.makefile("rw")
        f.write("T 1\n"); f.flush()
        self.assertEqual(f.readline().strip(), "RPRT 0")
        time.sleep(2.5)                       # held too long: watchdog drops it
        f.write("t\n"); f.flush()
        self.assertEqual(f.readline().strip(), "0")
        f.close(); s.close()


if __name__ == "__main__":
    unittest.main()
