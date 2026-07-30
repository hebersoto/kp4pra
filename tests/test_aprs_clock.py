import asyncio
import unittest
from unittest.mock import patch

import aprs_clock
from rms.ax25 import PID_NO_L3, make_frame


class TimestampTests(unittest.TestCase):
    def test_position_ddhhmm_zulu(self):
        got = aprs_clock.parse_aprs_time(b"/281845z1824.17N/06603.90W-Test")
        self.assertEqual((got.hour, got.minute, got.second, got.utc), (18, 45, 0, True))

    def test_position_hhmmss_zulu(self):
        got = aprs_clock.parse_aprs_time(b"@184512h1824.17N/06603.90W-Test")
        self.assertEqual((got.hour, got.minute, got.second, got.utc), (18, 45, 12, True))

    def test_position_local(self):
        got = aprs_clock.parse_aprs_time(b"/281445/1824.17N/06603.90W-Test")
        self.assertEqual((got.hour, got.minute, got.second, got.utc), (14, 45, 0, False))

    def test_object_timestamp(self):
        got = aprs_clock.parse_aprs_time(b";CLOCKSRC *281845z1824.17N/06603.90W")
        self.assertEqual((got.hour, got.minute, got.second), (18, 45, 0))

    def test_untimestamped_ignored(self):
        self.assertIsNone(aprs_clock.parse_aprs_time(b"!1824.17N/06603.90W-Test"))

    def test_invalid_time_ignored(self):
        self.assertIsNone(aprs_clock.parse_aprs_time(b"/289945z1824.17N/06603.90W"))


class FrameFilterTests(unittest.TestCase):
    def frame(self, source, payload, control=0x03, pid=PID_NO_L3):
        return make_frame("APRS", source, control, payload, pid=pid)

    def test_exact_source_with_ssid_accepted(self):
        frame = self.frame("KP3M-5", b"/281845z1824.17N/06603.90W")
        self.assertIsNotNone(aprs_clock.aprs_time_from_frame(frame, "KP3M-5"))

    def test_different_ssid_rejected(self):
        frame = self.frame("KP3M-4", b"/281845z1824.17N/06603.90W")
        self.assertIsNone(aprs_clock.aprs_time_from_frame(frame, "KP3M-5"))

    def test_other_station_rejected(self):
        frame = self.frame("NP4JN", b"/281845z1824.17N/06603.90W")
        self.assertIsNone(aprs_clock.aprs_time_from_frame(frame, "KP3M-5"))

    def test_non_ui_frame_rejected(self):
        frame = self.frame("KP3M-5", b"/281845z1824.17N/06603.90W", control=0x00)
        self.assertIsNone(aprs_clock.aprs_time_from_frame(frame, "KP3M-5"))


class AgwTests(unittest.TestCase):
    def test_enable_raw_command_is_header_only(self):
        command = aprs_clock.build_agw_command("k")
        self.assertEqual(len(command), 36)
        fields = aprs_clock.AGW_HEADER.unpack(command)
        self.assertEqual(chr(fields[4]), "k")
        self.assertEqual(fields[10], 0)

    def test_parse_raw_message_and_remove_port_byte(self):
        frame = make_frame(
            "APRS", "KP3M-5", 0x03,
            b"/281845z1824.17N/06603.90W", pid=PID_NO_L3,
        )
        data = b"\x00" + frame
        header = aprs_clock.AGW_HEADER.pack(
            0, 0, 0, 0, ord("K"), 0, 0, 0,
            b"KP3M-5\x00\x00\x00", b"APRS\x00\x00\x00\x00\x00\x00", len(data), 0,
        )
        msg = aprs_clock.parse_agw_message(header, data)
        self.assertEqual(msg.call_from, "KP3M-5")
        self.assertEqual(aprs_clock.raw_ax25_from_agw(msg), frame)

    def test_non_raw_message_ignored(self):
        header = aprs_clock.AGW_HEADER.pack(
            0, 0, 0, 0, ord("U"), 0, 0, 0, b"", b"", 3, 0
        )
        msg = aprs_clock.parse_agw_message(header, b"abc")
        self.assertIsNone(aprs_clock.raw_ax25_from_agw(msg))

    def test_length_mismatch_rejected(self):
        header = aprs_clock.AGW_HEADER.pack(
            0, 0, 0, 0, ord("K"), 0, 0, 0, b"", b"", 4, 0
        )
        with self.assertRaises(ValueError):
            aprs_clock.parse_agw_message(header, b"abc")


class DeltaTests(unittest.TestCase):
    def test_midnight_wrap_forward(self):
        self.assertEqual(aprs_clock.circular_delta_seconds(86390, 10), 20)

    def test_midnight_wrap_backward(self):
        self.assertEqual(aprs_clock.circular_delta_seconds(10, 86390), -20)

    def test_regular_delta(self):
        self.assertEqual(aprs_clock.circular_delta_seconds(3600, 3901), 301)


class GateTests(unittest.TestCase):
    def test_sustained_ntp_loss_required(self):
        gate = aprs_clock.NtpLossGate(required=3)
        self.assertFalse(gate.observe(False))
        self.assertFalse(gate.observe(False))
        self.assertTrue(gate.observe(False))
        self.assertFalse(gate.observe(True))
        self.assertEqual(gate.failures, 0)


class StepTests(unittest.IsolatedAsyncioTestCase):
    async def test_large_delta_steps_once(self):
        service = aprs_clock.AprsClockService()
        service.offline_confirmed = True
        frame = make_frame(
            "APRS", "KP3M-5", 0x03,
            b"/281845z1824.17N/06603.90W", pid=PID_NO_L3,
        )
        with patch.object(aprs_clock, "ntp_synchronized", return_value=False), \
             patch.object(aprs_clock, "_clock_values", return_value=(0, "2026-07-28 14:45:00")), \
             patch.object(aprs_clock, "set_system_time", return_value=(True, "")) as setter, \
             patch.object(aprs_clock, "write_status"):
            await service.consider_frame(frame, "KP3M-5")
        setter.assert_called_once_with("2026-07-28 14:45:00")

    async def test_exact_threshold_does_not_step(self):
        service = aprs_clock.AprsClockService()
        frame = make_frame(
            "APRS", "KP3M-5", 0x03,
            b"@000200h1824.17N/06603.90W", pid=PID_NO_L3,
        )
        with patch.object(aprs_clock, "ntp_synchronized", return_value=False), \
             patch.object(aprs_clock, "_clock_values", return_value=(0, "2026-07-28 20:02:00")), \
             patch.object(aprs_clock, "set_system_time") as setter, \
             patch.object(aprs_clock, "write_status"):
            await service.consider_frame(frame, "KP3M-5")
        setter.assert_not_called()

    async def test_ntp_recovery_blocks_step(self):
        service = aprs_clock.AprsClockService()
        frame = make_frame(
            "APRS", "KP3M-5", 0x03,
            b"@184512h1824.17N/06603.90W", pid=PID_NO_L3,
        )
        with patch.object(aprs_clock, "ntp_synchronized", return_value=True), \
             patch.object(aprs_clock, "set_system_time") as setter, \
             patch.object(aprs_clock, "write_status"):
            await service.consider_frame(frame, "KP3M-5")
        setter.assert_not_called()
        self.assertEqual(service.last_state, "ntp-primary")


class PrivilegeCommandTests(unittest.TestCase):
    def test_clock_step_uses_narrow_date_command(self):
        completed = type("Completed", (), {
            "returncode": 0, "stdout": "", "stderr": ""
        })()
        with patch.object(aprs_clock.subprocess, "run", return_value=completed) as runner:
            ok, detail = aprs_clock.set_system_time("2026-07-28 12:34:56")
        self.assertTrue(ok)
        self.assertEqual(detail, "")
        runner.assert_called_once_with(
            ["/usr/bin/sudo", "-n", "/bin/date", "--set", "2026-07-28 12:34:56"],
            check=False, capture_output=True, text=True, timeout=15,
        )


class CallTests(unittest.TestCase):
    def test_callsign_with_ssid(self):
        self.assertEqual(aprs_clock.normalize_callsign("kp3m-5"), "KP3M-5")

    def test_bad_callsign_disables(self):
        self.assertEqual(aprs_clock.normalize_callsign("KP3M-99"), "")


if __name__ == "__main__":
    unittest.main()
