"""DRA-Pi-Zero platform table and direwolf.conf generation per board.

Run: python3 -m unittest discover -s tests
"""
import os
import re
import sys
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from common import dra_platform as dp          # noqa: E402
from common import direwolf_conf as dc         # noqa: E402

STATION = {"callsign": "KP4PRA", "ssid": "10", "calias": "CDIGI",
           "ptt": "GPIOD", "ptt_param": ""}
PI_ADEV = "plughw:audioinjectorpi,0"
OPI_ADEV = "plughw:DRAPiZero,0"


def gen(station, adevice, platform):
    with patch.object(dp, "detect_platform", return_value=platform):
        return dc.generate_direwolf_conf(dict(station), adevice=adevice)


class ModelDetection(unittest.TestCase):
    def test_models(self):
        self.assertEqual(dp.platform_from_model("Raspberry Pi Zero 2 W Rev 1.0"), "rpi")
        self.assertEqual(dp.platform_from_model("Raspberry Pi 3 Model A Plus Rev 1.1"), "rpi")
        self.assertEqual(dp.platform_from_model("OrangePi Zero 2W"), "opi-zero2w")
        self.assertEqual(dp.platform_from_model("Orange Pi Zero 2W"), "opi-zero2w")
        self.assertIsNone(dp.platform_from_model("Inovato Quadra"))
        self.assertIsNone(dp.platform_from_model(""))

    def test_dra_adevice(self):
        for a in (PI_ADEV, OPI_ADEV, "plughw:drapizero,0", "audioinjector"):
            self.assertTrue(dp.is_dra_adevice(a), a)
        for a in ("plughw:Device,0", "plughw:1,0", "", None):
            self.assertFalse(dp.is_dra_adevice(a), a)


class RaspberryPiUnchanged(unittest.TestCase):
    """The Pi must render exactly what it did before the Orange Pi port."""

    def test_pi_default_ptt_and_dcd(self):
        conf = gen(STATION, PI_ADEV, "rpi")
        self.assertIn("PTT GPIOD gpiochip0 12", conf)
        self.assertIn("DCD GPIOD gpiochip0 16", conf)

    def test_pi_explicit_param_kept(self):
        conf = gen(dict(STATION, ptt_param="gpiochip0 12"), PI_ADEV, "rpi")
        self.assertIn("PTT GPIOD gpiochip0 12", conf)

    def test_pi_custom_param_kept(self):
        conf = gen(dict(STATION, ptt_param="gpiochip0 6"), PI_ADEV, "rpi")
        self.assertIn("PTT GPIOD gpiochip0 6", conf)

    def test_unknown_board_falls_back_to_pi_values(self):
        conf = gen(STATION, PI_ADEV, None)
        self.assertIn("PTT GPIOD gpiochip0 12", conf)
        self.assertIn("DCD GPIOD gpiochip0 16", conf)

    def test_usb_card_has_no_dcd(self):
        conf = gen(dict(STATION, ptt="CM108"), "plughw:Device,0", "rpi")
        self.assertIn("PTT CM108", conf)
        self.assertNotIn("DCD", conf)


class OrangePiZero2W(unittest.TestCase):
    def test_opi_default_ptt_and_dcd(self):
        conf = gen(STATION, OPI_ADEV, "opi-zero2w")
        self.assertIn("ADEVICE  plughw:DRAPiZero,0", conf)
        self.assertIn("PTT GPIOD gpiochip1 267", conf)
        self.assertIn("DCD GPIOD gpiochip1 76", conf)

    def test_pi_value_remapped_on_opi(self):
        # Field case from 2026-10-12: the web UI had stored the Pi default.
        conf = gen(dict(STATION, ptt_param="gpiochip0 12"), OPI_ADEV, "opi-zero2w")
        self.assertIn("PTT GPIOD gpiochip1 267", conf)
        self.assertNotIn("gpiochip0", conf)

    def test_opi_custom_param_kept(self):
        conf = gen(dict(STATION, ptt_param="gpiochip1 270"), OPI_ADEV, "opi-zero2w")
        self.assertIn("PTT GPIOD gpiochip1 270", conf)

    def test_cm108_with_usb_card_unaffected(self):
        conf = gen(dict(STATION, ptt="CM108"), "plughw:Device,0", "opi-zero2w")
        self.assertIn("PTT CM108", conf)
        self.assertNotIn("DCD", conf)

    def test_card_label_and_suggestion(self):
        aplay = (b"card 1: DRAPiZero [DRAPiZero], device 0: ahub_plat-wm8731-hifi "
                 b"wm8731-hifi-0 [ahub_plat-wm8731-hifi wm8731-hifi-0]\n")

        class R:
            stdout = aplay
        with patch.object(dp, "detect_platform", return_value="opi-zero2w"), \
             patch.object(dc.subprocess, "run", return_value=R()), \
             patch.object(dc, "detect_cp210x_ports", return_value=[]):
            cards = dc.detect_sound_cards()
        self.assertEqual(len(cards), 1)
        self.assertEqual(cards[0]["label"], "DRA-Pi-Zero (I2S)")
        self.assertEqual(cards[0]["plughw"], "plughw:DRAPiZero,0")
        self.assertEqual(cards[0]["ptt_suggest"], "GPIOD")
        self.assertEqual(cards[0]["ptt_param_suggest"], "gpiochip1 267")



class SetupState(unittest.TestCase):
    """Config page hint when the DRA card is missing (fake filesystem)."""
    K = "6.18.44-current-sunxi64"

    def setUp(self):
        import tempfile
        self.root = tempfile.mkdtemp()

    def mk(self, rel, text="", mtime=None):
        path = os.path.join(self.root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            f.write(text)
        if mtime is not None:
            os.utime(path, (mtime, mtime))
        return path

    def state(self, plat="opi-zero2w", boot=1000.0):
        return dp.setup_state(plat, kernel=self.K, root=self.root, boot_time=boot)[0]

    def opi_installed(self, mtime=500.0):
        self.mk(f"lib/modules/{self.K}/kernel/x")
        self.mk(f"lib/modules/{self.K}/updates/dkms/snd-soc-wm8731.ko.xz", mtime=mtime)
        self.mk("boot/overlay-user/kp4pra-dra-orangepi.dtbo", mtime=mtime)
        self.mk("boot/armbianEnv.txt", "user_overlays=kp4pra-dra-orangepi\n")

    def test_opi_never_set_up(self):
        self.assertEqual(self.state(), "not_set_up")

    def test_opi_kernel_switched_not_rebooted(self):
        os.makedirs(os.path.join(self.root, "var/backups/kp4pra"))
        self.assertEqual(self.state(), "kernel_pending")       # no /lib/modules/<K>/kernel

    def test_opi_incomplete_after_reboot(self):
        os.makedirs(os.path.join(self.root, "var/backups/kp4pra"))
        self.mk(f"lib/modules/{self.K}/kernel/x")
        st, hint = dp.setup_state("opi-zero2w", kernel=self.K, root=self.root, boot_time=1000.0)
        self.assertEqual(st, "incomplete")
        self.assertIn("codec driver and DRA overlay missing", hint)
        self.assertIn("setup-dra-pi-zero.sh", hint)

    def test_opi_finished_needs_reboot(self):
        self.opi_installed(mtime=2000.0)                       # installed after boot
        self.assertEqual(self.state(boot=1000.0), "reboot_needed")

    def test_opi_installed_booted_no_card(self):
        self.opi_installed(mtime=500.0)
        self.assertEqual(self.state(boot=1000.0), "no_card")

    def test_pi_states(self):
        self.assertEqual(self.state("rpi"), "not_set_up")
        cfg = self.mk("boot/firmware/config.txt", "dtoverlay=audioinjector-wm8731-audio\n", mtime=2000.0)
        self.assertEqual(self.state("rpi", boot=1000.0), "reboot_needed")
        os.utime(cfg, (500.0, 500.0))
        self.assertEqual(self.state("rpi", boot=1000.0), "no_card")

class ShellTwinInSync(unittest.TestCase):
    """bin/kp4pra-dra-platform must carry the same values as the Python table."""

    def test_values_match(self):
        with open(os.path.join(ROOT, "bin", "kp4pra-dra-platform")) as f:
            text = f.read()
        blocks = re.findall(r"DRA_PLATFORM=(\S+)\n(.*?)EOF", text, re.S)
        found = {}
        for key, body in blocks:
            vals = dict(re.findall(r"^(DRA_\w+)=(.*)$", body, re.M))
            found[key] = vals
        for key, p in dp.PLATFORMS.items():
            self.assertIn(key, found, key)
            v = found[key]
            self.assertEqual(v["DRA_CARD"], p["card"], key)
            self.assertEqual((v["DRA_PTT_CHIP"], v["DRA_PTT_LINE"]), p["ptt"], key)
            self.assertEqual((v["DRA_DCD_CHIP"], v["DRA_DCD_LINE"]), p["dcd"], key)
            self.assertEqual((v["DRA_LED_CHIP"], v["DRA_LED_LINE"]), p["led"], key)


if __name__ == "__main__":
    unittest.main()
