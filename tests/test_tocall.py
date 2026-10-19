"""KP4PRA TNC's assigned APRS TOCALL (APKPPR) is the destination of the
KP4PRA beacon in the generated direwolf.conf.

Run: PYTHONPATH=src python3 -m unittest discover -s tests
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from common import direwolf_conf as dc         # noqa: E402

STATION = {"callsign": "KP4PRA", "ssid": "10", "calias": "CDIGI",
           "ptt": "CM108", "ptt_param": ""}


class FixedTocall(unittest.TestCase):
    def test_value(self):
        self.assertEqual(dc.APRS_TOCALL, "APKPPR")

    def test_direwolf_beacon_dest(self):
        text = dc.generate_direwolf_conf(dict(STATION), adevice="plughw:1,0")
        line = [l for l in text.splitlines() if l.startswith("CBEACON")][0]
        self.assertTrue(line.startswith("CBEACON dest=APKPPR "), line)


if __name__ == "__main__":
    unittest.main()
