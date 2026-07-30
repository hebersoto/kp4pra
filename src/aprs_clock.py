"""KP4PRA TNC APRS clock fallback.

This daemon is deliberately receive-only.  It connects to Dire Wolf's existing
AGW TCP server and asks for raw received AX.25 frames.  It never opens the
radio or audio device, never transmits an AX.25 frame, and never starts another
Dire Wolf instance.

The AGW stream is used instead of another KISS TCP connection because Dire
Wolf has a small per-port KISS client limit and KP4PRA already uses KISS for
RMS, BLE, and RFCOMM.  Keeping clock monitoring on AGW avoids consuming a KISS
client slot or disturbing those services.

APRS timestamps do not provide a reliable year and frequently do not provide
any date.  Consequently this module uses only the transmitted time-of-day and
retains the board's current date.  It corrects clock drift; it cannot repair a
grossly wrong board date.

Stepping wall-clock time, especially backward, can affect journal ordering,
systemd timers, TLS validation, Morse-ID timers, and RMS/Winlink session timing.
The daemon therefore acts only after sustained NTP loss, only for the configured
station, only above a 120-second delta, and no more than once per cooldown.
"""

from __future__ import annotations

import asyncio
import dataclasses
import datetime as dt
import re
import struct
import subprocess
import time
from typing import Optional

from common.config import reload_config
from common.runtime_status import write_status
from rms.ax25 import PID_NO_L3, split_frame

LOG_PREFIX = "[KP4PRA APRS CLOCK]"
NTP_CHECK_SECONDS = 30
NTP_FAILURES_REQUIRED = 3
READ_TIMEOUT_SECONDS = 30
RECONNECT_SECONDS = 10
STEP_THRESHOLD_SECONDS = 120
STEP_COOLDOWN_SECONDS = 300
TIMEDATECTL = "/usr/bin/timedatectl"
DATE = "/bin/date"
SUDO = "/usr/bin/sudo"

# The project runs Dire Wolf's AGW server on its standard port 8000 and KISS
# on the configured direwolf.port (normally 8001).  No parallel config key is
# introduced for this feature.
AGW_PORT = 8000
AGW_HEADER = struct.Struct("<8B10s10sII")
AGW_MAX_DATA = 4096

_CALL_RE = re.compile(r"^[A-Z0-9]{1,6}(?:-(?:[0-9]|1[0-5]))?$")
_TS_RE = re.compile(r"^[0-9]{6}[zZhH/]$")


@dataclasses.dataclass(frozen=True)
class AprsTime:
    hour: int
    minute: int
    second: int
    utc: bool
    format_name: str

    @property
    def seconds_since_midnight(self) -> int:
        return self.hour * 3600 + self.minute * 60 + self.second


@dataclasses.dataclass(frozen=True)
class AgwMessage:
    port: int
    kind: str
    pid: int
    call_from: str
    call_to: str
    data: bytes


def log(message: str) -> None:
    print(f"{LOG_PREFIX} {message}", flush=True)


def normalize_callsign(value: object) -> str:
    call = str(value or "").strip().upper()
    return call if _CALL_RE.fullmatch(call) else ""


def _timestamp_field(payload: bytes) -> Optional[str]:
    """Return an APRS timestamp field from a position or object report.

    Direct timestamped position reports begin with '/' or '@'.  APRS object
    reports begin with ';', followed by a 9-byte object name and an alive/dead
    marker; their timestamp begins at byte 11.
    """
    try:
        text = payload.decode("ascii", errors="strict")
    except UnicodeDecodeError:
        return None

    if len(text) >= 8 and text[0] in "/@":
        return text[1:8]
    if len(text) >= 18 and text[0] == ";" and text[10] in "*_":
        return text[11:18]
    return None


def parse_aprs_time(payload: bytes) -> Optional[AprsTime]:
    field = _timestamp_field(payload)
    if not field or not _TS_RE.fullmatch(field):
        return None

    suffix = field[-1].lower()
    try:
        if suffix in ("z", "/"):
            # DDHHMMz and DDHHMM/.  Day-of-month is validated but deliberately
            # ignored: APRS supplies no reliable year and the board date stays.
            day = int(field[0:2])
            hour = int(field[2:4])
            minute = int(field[4:6])
            second = 0
            if not 1 <= day <= 31:
                return None
            name = "DDHHMMz" if suffix == "z" else "DDHHMM/"
            utc = suffix == "z"
        else:
            # HHMMSSh is UTC/Zulu.
            hour = int(field[0:2])
            minute = int(field[2:4])
            second = int(field[4:6])
            name = "HHMMSSh"
            utc = True
    except ValueError:
        return None

    if not (0 <= hour <= 23 and 0 <= minute <= 59 and 0 <= second <= 59):
        return None
    return AprsTime(hour, minute, second, utc, name)


def circular_delta_seconds(current: int, target: int) -> int:
    """Return the shortest signed target-current difference on a 24-hour clock."""
    return int((target - current + 43200) % 86400 - 43200)


def aprs_time_from_frame(frame: bytes, expected_source: str) -> Optional[AprsTime]:
    """Extract time only from an APRS UI frame sent by the exact source."""
    try:
        _dest, source, _path, control, pid, payload = split_frame(frame)
    except ValueError:
        return None
    if normalize_callsign(source) != normalize_callsign(expected_source):
        return None
    if (control & 0xEF) != 0x03 or pid != PID_NO_L3:
        return None
    return parse_aprs_time(payload)


def build_agw_command(kind: str) -> bytes:
    """Build a header-only AGW command.

    Lower-case 'k' toggles raw received-frame delivery for a newly connected
    client.  Dire Wolf initializes that toggle off for every new connection,
    so sending it once immediately after connect always enables reception.
    """
    if len(kind) != 1:
        raise ValueError("AGW command kind must be one character")
    return AGW_HEADER.pack(
        0, 0, 0, 0, ord(kind), 0, 0, 0,
        b"", b"", 0, 0,
    )


def parse_agw_message(header: bytes, data: bytes) -> AgwMessage:
    if len(header) != AGW_HEADER.size:
        raise ValueError("invalid AGW header length")
    fields = AGW_HEADER.unpack(header)
    data_len = fields[10]
    if data_len != len(data):
        raise ValueError("AGW data length mismatch")

    def call(raw: bytes) -> str:
        return raw.split(b"\x00", 1)[0].decode("ascii", errors="ignore").strip().upper()

    return AgwMessage(
        port=fields[0],
        kind=chr(fields[4]),
        pid=fields[6],
        call_from=call(fields[8]),
        call_to=call(fields[9]),
        data=data,
    )


async def read_agw_message(reader: asyncio.StreamReader) -> AgwMessage:
    header = await reader.readexactly(AGW_HEADER.size)
    fields = AGW_HEADER.unpack(header)
    data_len = fields[10]
    if data_len > AGW_MAX_DATA:
        raise ValueError(f"AGW data length {data_len} exceeds limit")
    data = await reader.readexactly(data_len) if data_len else b""
    return parse_agw_message(header, data)


def raw_ax25_from_agw(message: AgwMessage) -> Optional[bytes]:
    """Return the raw AX.25 frame from a Dire Wolf AGW 'K' message.

    Dire Wolf prepends one byte identifying the TNC/radio port.  It is not part
    of the AX.25 frame and must be removed before using the project's decoder.
    """
    if message.kind != "K" or len(message.data) < 2:
        return None
    return message.data[1:]


def _clock_values(aprs: AprsTime) -> tuple[int, str]:
    """Return current seconds-of-day and local timestamp for timedatectl.

    UTC APRS timestamps are compared against current UTC time and converted to
    the board's local zone for timedatectl. Local ('/') timestamps are compared
    in local time. In both cases only time-of-day is applied and the board's
    existing LOCAL calendar date is retained.
    """
    now_local = dt.datetime.now().astimezone()
    if aprs.utc:
        now_compare = dt.datetime.now(dt.timezone.utc)
        target_utc = now_compare.replace(
            hour=aprs.hour,
            minute=aprs.minute,
            second=aprs.second,
            microsecond=0,
        )
        converted = target_utc.astimezone(now_local.tzinfo)
        target_local = now_local.replace(
            hour=converted.hour,
            minute=converted.minute,
            second=converted.second,
            microsecond=0,
        )
    else:
        now_compare = now_local
        target_local = now_local.replace(
            hour=aprs.hour,
            minute=aprs.minute,
            second=aprs.second,
            microsecond=0,
        )

    current_seconds = (
        now_compare.hour * 3600 + now_compare.minute * 60 + now_compare.second
    )
    return current_seconds, target_local.strftime("%Y-%m-%d %H:%M:%S")


def ntp_synchronized() -> bool:
    """Query systemd's synchronization state.

    A command failure is treated as not synchronized, but APRS is not enabled
    until multiple consecutive checks have failed.  A final check is also made
    immediately before every privileged clock step.
    """
    for prop in ("NTPSynchronized", "SystemClockSynchronized"):
        try:
            result = subprocess.run(
                [TIMEDATECTL, "show", f"--property={prop}", "--value"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        value = result.stdout.strip().lower()
        if result.returncode == 0 and value in {"yes", "true", "1"}:
            return True
        if result.returncode == 0 and value in {"no", "false", "0"}:
            return False
    return False


def set_system_time(timestamp: str) -> tuple[bool, str]:
    """Run the single clock-setting action permitted by sudoers.

    GNU date is used for the step because timedatectl rejects manual set-time
    while an NTP service remains enabled, even when it is currently
    unsynchronized.  Keeping NTP enabled lets it resume as primary source as
    soon as connectivity returns.
    """
    try:
        result = subprocess.run(
            [SUDO, "-n", DATE, "--set", timestamp],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    detail = (result.stderr or result.stdout).strip()
    return result.returncode == 0, detail


class NtpLossGate:
    def __init__(self, required: int = NTP_FAILURES_REQUIRED) -> None:
        self.required = required
        self.failures = 0

    def observe(self, synchronized: bool) -> bool:
        if synchronized:
            self.failures = 0
            return False
        self.failures = min(self.failures + 1, self.required)
        return self.failures >= self.required


class AprsClockService:
    def __init__(self) -> None:
        self.gate = NtpLossGate()
        self.offline_confirmed = False
        self.last_step_monotonic = -STEP_COOLDOWN_SECONDS
        self.last_state: Optional[str] = None
        self.last_ntp_synced: Optional[bool] = None

    def status(self, **extra: object) -> None:
        data = {
            "state": self.last_state or "starting",
            "ntp_synchronized": self.last_ntp_synced,
            "transport": "AGW",
            "agw_port": AGW_PORT,
            **extra,
        }
        try:
            write_status("aprs_clock", data)
        except Exception:
            pass

    def set_state(self, state: str, message: Optional[str] = None, **extra: object) -> None:
        changed = state != self.last_state
        self.last_state = state
        if changed and message:
            log(message)
        self.status(**extra)

    @staticmethod
    def settings() -> tuple[str, str, int]:
        cfg = reload_config()
        station = cfg.get("station", {})
        if not isinstance(station, dict):
            station = {}
        source = normalize_callsign(station.get("clock"))

        direwolf = cfg.get("direwolf", {})
        if not isinstance(direwolf, dict):
            direwolf = {}
        host = str(direwolf.get("host") or "127.0.0.1")
        try:
            kiss_port = int(direwolf.get("port", 8001))
        except (TypeError, ValueError):
            kiss_port = 8001
        return source, host, kiss_port

    async def update_ntp_gate(self) -> bool:
        synced = await asyncio.to_thread(ntp_synchronized)
        self.last_ntp_synced = synced
        self.offline_confirmed = self.gate.observe(synced)
        if synced:
            self.set_state("ntp-primary", "NTP synchronized; APRS fallback idle")
        elif not self.offline_confirmed:
            self.set_state(
                "confirming-ntp-loss",
                "NTP is not synchronized; waiting for sustained loss before fallback",
                ntp_failures=self.gate.failures,
            )
        return self.offline_confirmed

    async def consider_frame(self, frame: bytes, source_call: str) -> None:
        aprs = aprs_time_from_frame(frame, source_call)
        if aprs is None:
            return

        # Recheck immediately before any privileged action. NTP remains primary
        # even if it recovered between periodic checks.
        if await asyncio.to_thread(ntp_synchronized):
            self.gate.observe(True)
            self.last_ntp_synced = True
            self.offline_confirmed = False
            self.set_state("ntp-primary", "NTP recovered; APRS fallback idle")
            return

        current, timestamp = _clock_values(aprs)
        delta = circular_delta_seconds(current, aprs.seconds_since_midnight)
        if abs(delta) <= STEP_THRESHOLD_SECONDS:
            self.set_state(
                "watching",
                source=source_call,
                last_packet_format=aprs.format_name,
                last_delta_seconds=delta,
            )
            return

        since_step = time.monotonic() - self.last_step_monotonic
        if since_step < STEP_COOLDOWN_SECONDS:
            self.set_state(
                "cooldown",
                source=source_call,
                last_packet_format=aprs.format_name,
                last_delta_seconds=delta,
                cooldown_remaining_seconds=round(STEP_COOLDOWN_SECONDS - since_step),
            )
            return

        ok, detail = await asyncio.to_thread(set_system_time, timestamp)
        if ok:
            self.last_step_monotonic = time.monotonic()
            now_iso = dt.datetime.now().astimezone().isoformat(timespec="seconds")
            log(
                f"clock stepped from {source_call} ({aprs.format_name}); "
                f"delta was {delta:+d}s"
            )
            self.set_state(
                "stepped",
                source=source_call,
                last_packet_format=aprs.format_name,
                last_delta_seconds=delta,
                last_sync=now_iso,
            )
        else:
            self.set_state(
                "set-failed",
                f"clock step failed: {detail or 'unknown timedatectl error'}",
                source=source_call,
                last_delta_seconds=delta,
                error=detail,
            )

    async def listen_once(self, source: str, host: str, kiss_port: int) -> None:
        self.set_state(
            "connecting",
            f"NTP loss confirmed; connecting to receive-only AGW {host}:{AGW_PORT}",
            source=source,
            configured_kiss_port=kiss_port,
        )
        reader, writer = await asyncio.open_connection(host, AGW_PORT)
        writer.write(build_agw_command("k"))
        await writer.drain()

        next_control_check = time.monotonic() + NTP_CHECK_SECONDS
        self.set_state(
            "watching",
            f"watching timestamped APRS packets from {source} via AGW",
            source=source,
            configured_kiss_port=kiss_port,
        )
        try:
            while True:
                try:
                    message = await asyncio.wait_for(
                        read_agw_message(reader), timeout=READ_TIMEOUT_SECONDS
                    )
                except asyncio.TimeoutError:
                    if not await self.update_ntp_gate():
                        return
                    if self.settings() != (source, host, kiss_port):
                        return
                    continue

                frame = raw_ax25_from_agw(message)
                if frame is not None:
                    await self.consider_frame(frame, source)

                # Busy RF channels may never produce a timeout. Recheck NTP and
                # configuration on a monotonic schedule even under traffic.
                if time.monotonic() >= next_control_check:
                    if not await self.update_ntp_gate():
                        return
                    if self.settings() != (source, host, kiss_port):
                        return
                    next_control_check = time.monotonic() + NTP_CHECK_SECONDS
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    async def run(self) -> None:
        while True:
            source, host, kiss_port = self.settings()
            if not source:
                self.gate.failures = 0
                self.offline_confirmed = False
                self.set_state(
                    "disabled",
                    "station.clock is blank; APRS clock fallback disabled",
                )
                await asyncio.sleep(NTP_CHECK_SECONDS)
                continue

            if not await self.update_ntp_gate():
                await asyncio.sleep(NTP_CHECK_SECONDS)
                continue

            try:
                await self.listen_once(source, host, kiss_port)
            except (asyncio.IncompleteReadError, ConnectionError, OSError) as exc:
                self.set_state(
                    "agw-unavailable",
                    f"AGW stream unavailable ({exc}); retrying quietly",
                    source=source,
                )
                await asyncio.sleep(RECONNECT_SECONDS)
            except Exception as exc:
                # Malformed traffic or a transient service problem must not
                # affect Dire Wolf or create a systemd crash loop.
                self.set_state(
                    "error",
                    f"listener error ({exc}); retrying quietly",
                    source=source,
                )
                await asyncio.sleep(RECONNECT_SECONDS)


def main() -> int:
    try:
        asyncio.run(AprsClockService().run())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
