"""KP4PRA TNC's assigned APRS TOCALL (APKPPR) is fixed: every frame the
application builds uses it, whatever config.yaml says.

Run: python3 -m unittest discover -s tests
"""
import os
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
sys.path.insert(0, os.path.join(ROOT, "src", "web"))

from web import aprs                           # noqa: E402
from web import ax25_stub                      # noqa: E402
from web import aprs_transports as at          # noqa: E402
from web import aprschat as ac                 # noqa: E402
from common import config                      # noqa: E402
from common import config_writer               # noqa: E402
from common import direwolf_conf as dc         # noqa: E402
from tft import beacon                         # noqa: E402

STATION = {"callsign": "KP4PRA", "ssid": "10", "calias": "CDIGI",
           "ptt": "CM108", "ptt_param": ""}


class FixedTocall(unittest.TestCase):
    def test_value(self):
        self.assertEqual(aprs.TOCALL, "APKPPR")
        self.assertEqual(dc.APRS_TOCALL, aprs.TOCALL)

    def test_not_a_setting(self):
        self.assertNotIn("tocall", config.DEFAULT_CONFIG["aprs"])
        # an old value sent by an old page is not an error (it is ignored)
        ok, msg = config_writer.validate_config_updates({"aprs": {"tocall": "X"}})
        self.assertNotIn("tocall", msg or "")

    def test_defaults(self):
        self.assertEqual(at.RfTransport(ax25_stub, "KP3M").tocall, "APKPPR")
        self.assertEqual(at.AprsIsTransport("KP3M").tocall, "APKPPR")
        self.assertEqual(ac.AprsChat("KP3M", type("T", (), {})()).tocall, "APKPPR")

    def test_aprsis_line(self):
        t = at.AprsIsTransport("KP3M")
        self.assertTrue(aprs.tnc2_line(t.mycall, t.tocall, ">x").startswith("KP3M>APKPPR"))

    def test_direwolf_beacon_dest(self):
        with patch("common.dra_platform.detect_platform", return_value="rpi"):
            text = dc.generate_direwolf_conf(dict(STATION), adevice="plughw:1,0")
        line = [l for l in text.splitlines() if l.startswith("CBEACON")][0]
        self.assertIn("dest=APKPPR ", line)

    def test_tft_beacon_ignores_old_setting(self):
        cfg = {"station": {"callsign": "KP3M", "ssid": "10"},
               "aprs": {"tocall": "APKP41", "digipath": [],
                        "beacon": {"comment": "KP4PRA"}}}
        sent = []

        class Sock:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def sendall(self, b): sent.append(b)

        with patch.object(beacon.socket, "create_connection", return_value=Sock()):
            beacon.send_beacon(cfg)
        h = beacon.AGW_HEADER.unpack(sent[0][:beacon.AGW_HEADER.size])
        self.assertEqual(h[9].rstrip(b"\0"), b"APKPPR")


if __name__ == "__main__":
    unittest.main()
