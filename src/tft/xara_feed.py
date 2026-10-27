"""
KP4PRA TNC - live VARA data from Xara for the Mini PiTFT.

Xara (the VARA-compatible modem, docs/XARA.md) serves its own web page with a
live feed: WebSocket /ws on its UI port (8080), open read-only to localhost.
Every 100 ms it sends a "status" message, plus events and, when asked with
"WATERFALL ON", binary waterfall rows. This module keeps the latest of that
for the VARA screens (render.vara_screen / render.vara_log):

  status   link state, peer, speed level, bit rate, S/N, RX/TX audio level,
           PTT, busy, bytes waiting to be sent, retries
  history  S/N once per second (graph), waterfall rows (spectrum picture),
           session log (connected / disconnected / unable to connect / ...)

Only the Python standard library (a tiny RFC 6455 client), so nothing new to
install. XaraState holds the data and is tested without a network.
"""

import base64
import collections
import json
import os
import socket
import struct
import threading
import time

WATERFALL_ROWS = 64          # rows kept for the picture (about 5 s)
SN_HISTORY = 90              # one S/N sample per second
LOG_LINES = 30

# Xara link states (xara-arq fm/hf Session State) -> what the screen says
CONNECTED_STATES = {"Receiving", "Sending", "Idle", "IdleSent", "AwaitGo",
                    "Answering", "HeaderSent", "Break", "FqWait"}
TX_STATES = {"Sending", "IdleSent", "HeaderSent"}


def link_label(state: str, peer: str) -> tuple:
    """(text, kind) for the header. kind: idle | calling | connected | closing."""
    if state in ("", "Listen"):
        return "Listening", "idle"
    if state == "Digipeating":
        return "Digipeating", "calling"
    if state == "Calling":
        return ("Calling " + peer).strip(), "calling"
    if state in ("Qrt", "Closing"):
        return "Disconnecting", "closing"
    if state in CONNECTED_STATES:
        return ("Linked " + peer).strip(), "connected"
    return state, "calling"


def bpm(bps) -> int:
    """VARA link rate (bits/s) as bytes per minute, Winlink's BPM unit."""
    try:
        return int(round(float(bps) * 60 / 8))
    except (TypeError, ValueError):
        return 0


class XaraState:
    """Latest Xara telemetry. apply() takes one decoded JSON message,
    apply_row() one binary waterfall frame."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self.lock = threading.Lock()
        self.connected = False          # feed connected (Xara running)
        self.mode = ""                  # e.g. "FM wide"
        self.status = {}
        self.rows = collections.deque(maxlen=WATERFALL_ROWS)
        self.sn = collections.deque(maxlen=SN_HISTORY)
        self.log = collections.deque(maxlen=LOG_LINES)
        self.wf_floor, self.wf_step = -130.0, 0.5
        self.peak_bps = 0
        self.rows_seen = 0              # waterfall rows received (never reset)
        self.changed = False            # new data since the last snapshot()
        self.activity = False           # something worth waking the screen
        self._last_sn_t = 0.0
        self._last_state = None

    def _note(self, text):
        self.log.append((time.strftime("%H:%M", time.localtime(self.clock())), text))
        self.activity = True

    def apply(self, m: dict):
        t = m.get("t")
        with self.lock:
            self.changed = True
            if t == "hello":
                self.mode = str(m.get("mode") or "")
                self.connected = True
            elif t == "status":
                self._status(m)
            elif t == "waterfall":
                self.wf_floor = float(m.get("floor", self.wf_floor))
                self.wf_step = float(m.get("step", self.wf_step))
            elif t == "host":
                self._host(m)
            elif t == "notice":
                kind = m.get("kind", "")
                self._note({"unable_to_connect": "Unable to connect",
                            "timed_out": "Link timed out"}.get(kind, kind.replace("_", " ")))

    def _status(self, m):
        self.status = m
        state = m.get("state", "")
        if state != self._last_state:
            if self._last_state is not None and state == "Calling":
                self._note("Calling %s" % m.get("peer", ""))
            self._last_state = state
        if m.get("ptt") or m.get("busy") or state not in ("", "Listen"):
            self.activity = True
        br = m.get("bitrate") or {}
        if br.get("bps"):
            self.peak_bps = max(self.peak_bps, int(br["bps"]))
        now = self.clock()
        if now - self._last_sn_t >= 1.0:
            self._last_sn_t = now
            sn = m.get("sn")
            self.sn.append(None if sn is None or state in ("", "Listen") else float(sn))

    def _host(self, m):
        kind = m.get("kind")
        if kind == "connected":
            self.peak_bps = 0
            self._note("Linked %s>%s %s" % (m.get("caller", ""), m.get("answerer", ""),
                                            m.get("bw", "")))
        elif kind == "disconnected":
            self._note("Disconnected" + (" (top %s BPM)" % bpm(self.peak_bps)
                                         if self.peak_bps else ""))
        elif kind == "bitrate":
            self.activity = True

    def apply_row(self, frame: bytes):
        """Binary waterfall frame: [kind=1, 0, len lo, len hi, bins...]."""
        if len(frame) < 4 or frame[0] != 1:
            return
        n = struct.unpack("<H", frame[2:4])[0]
        with self.lock:
            self.rows.append(bytes(frame[4:4 + n]))
            self.rows_seen += 1
            self.changed = True

    def set_connected(self, on: bool):
        with self.lock:
            if self.connected != on:
                self.connected = on
                self.changed = True
                if not on:
                    self.status, self._last_state = {}, None
                    self.rows.clear()

    def snapshot(self) -> dict:
        """Copy for drawing; also tells whether anything changed / woke."""
        with self.lock:
            s = dict(self.status)
            out = {
                "connected": self.connected, "mode": self.mode, "status": s,
                "rows": list(self.rows), "sn_hist": list(self.sn),
                "log": list(self.log), "wf_floor": self.wf_floor,
                "wf_step": self.wf_step, "peak_bps": self.peak_bps,
                "changed": self.changed, "activity": self.activity,
            }
            self.changed = self.activity = False
            return out


# ---------------------------------------------------------------------------
# Minimal WebSocket client (RFC 6455): Xara only needs GET /ws + text/binary.

def _frame(op: int, payload: bytes) -> bytes:
    """Client frames must be masked."""
    mask = os.urandom(4)
    n = len(payload)
    head = bytes([0x80 | op])
    if n < 126:
        head += bytes([0x80 | n])
    elif n < 65536:
        head += bytes([0x80 | 126]) + struct.pack(">H", n)
    else:
        head += bytes([0x80 | 127]) + struct.pack(">Q", n)
    return head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))


def _read_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            raise ConnectionError("closed")
        buf += chunk
    return buf


def read_frame(sock):
    """(opcode, payload). Server frames are not masked."""
    h = _read_exact(sock, 2)
    op, n = h[0] & 0x0F, h[1] & 0x7F
    if n == 126:
        n = struct.unpack(">H", _read_exact(sock, 2))[0]
    elif n == 127:
        n = struct.unpack(">Q", _read_exact(sock, 8))[0]
    if h[1] & 0x80:
        mask = _read_exact(sock, 4)
        data = _read_exact(sock, n)
        return op, bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    return op, _read_exact(sock, n)


def connect(port: int, host="127.0.0.1", timeout=5.0):
    sock = socket.create_connection((host, port), timeout=timeout)
    key = base64.b64encode(os.urandom(16)).decode()
    sock.sendall(("GET /ws HTTP/1.1\r\nHost: localhost:%d\r\nUpgrade: websocket\r\n"
                  "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
                  "Sec-WebSocket-Version: 13\r\n\r\n" % (port, key)).encode())
    head = b""
    while b"\r\n\r\n" not in head:
        chunk = sock.recv(1024)
        if not chunk:
            raise ConnectionError("closed during handshake")
        head += chunk
        if len(head) > 8192:
            raise ConnectionError("bad handshake")
    if b" 101 " not in head.split(b"\r\n", 1)[0]:
        raise ConnectionError(head.split(b"\r\n", 1)[0].decode(errors="replace"))
    sock.settimeout(None)
    return sock


def run_feed(state: XaraState, port_fn, stop: threading.Event, waterfall=True, log=print):
    """Keep a connection to Xara's feed while Xara runs; retry every 5 s."""
    announced = False
    while not stop.is_set():
        port = port_fn()
        sock = None
        try:
            sock = connect(port)
            sock.settimeout(10)
            if waterfall:
                sock.sendall(_frame(0x1, b"WATERFALL ON"))
            log("VARA feed connected (Xara page port %d)" % port)
            announced = False
            while not stop.is_set():
                op, data = read_frame(sock)
                if op == 0x1:
                    try:
                        state.apply(json.loads(data.decode("utf-8", "replace")))
                    except ValueError:
                        pass
                elif op == 0x2:
                    state.apply_row(data)
                elif op == 0x9:                       # ping -> pong
                    sock.sendall(_frame(0xA, data))
                elif op == 0x8:
                    break
        except (OSError, ConnectionError) as e:
            if not announced:
                log("VARA feed not available (%s) - Xara not running?" % e)
                announced = True
        finally:
            if sock:
                try:
                    sock.close()
                except OSError:
                    pass
            state.set_connected(False)
        stop.wait(5)
