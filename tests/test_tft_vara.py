"""Mini PiTFT VARA screens: Xara live feed (state + WebSocket client) and
the VARA monitor / log rendering.

Run: PYTHONPATH=src python3 -m unittest discover -s tests
"""
import base64
import hashlib
import json
import os
import socket
import struct
import sys
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from tft import xara_feed as xf           # noqa: E402
from tft import render                    # noqa: E402

STATUS = {"t": "status", "state": "Receiving", "peer": "KP3M-12", "level": 7, "sn": 12.5,
          "bitrate": {"level": 7, "bps": 2412, "tx": False}, "rx": {"rms": -20},
          "tx": {"rms": -120}, "ptt": False, "busy": True, "buffer": 1500, "retries": 1}


def row_frame(values):
    return bytes([1, 0]) + struct.pack("<H", len(values)) + bytes(values)


class Labels(unittest.TestCase):
    def test_link_label(self):
        self.assertEqual(xf.link_label("Listen", ""), ("Listening", "idle"))
        self.assertEqual(xf.link_label("Calling", "KP3M-12"), ("Calling KP3M-12", "calling"))
        for st in ("Receiving", "Sending", "Idle", "IdleSent", "AwaitGo"):
            self.assertEqual(xf.link_label(st, "KP3M-12"), ("Linked KP3M-12", "connected"), st)
        self.assertEqual(xf.link_label("Qrt", "X")[1], "closing")

    def test_bpm(self):
        self.assertEqual(xf.bpm(2412), 18090)       # 2412 bit/s * 60 / 8
        self.assertEqual(xf.bpm(None), 0)


class State(unittest.TestCase):
    def setUp(self):
        self.t = [1000.0]
        self.s = xf.XaraState(clock=lambda: self.t[0])

    def test_status_history_and_activity(self):
        self.s.apply({"t": "hello", "mode": "FM wide"})
        for i in range(5):
            self.t[0] += 1
            self.s.apply(STATUS)
        snap = self.s.snapshot()
        self.assertTrue(snap["connected"] and snap["activity"] and snap["changed"])
        self.assertEqual(snap["mode"], "FM wide")
        self.assertEqual(len(snap["sn_hist"]), 5)
        self.assertEqual(snap["status"]["peer"], "KP3M-12")
        again = self.s.snapshot()
        self.assertFalse(again["changed"] or again["activity"])     # flags reset

    def test_listening_is_not_activity(self):
        self.s.apply({"t": "status", "state": "Listen", "ptt": False, "busy": False})
        self.s.snapshot()
        self.s.apply({"t": "status", "state": "Listen", "ptt": False, "busy": False})
        self.assertFalse(self.s.snapshot()["activity"])

    def test_session_log(self):
        self.s.apply(STATUS)
        self.s.apply({"t": "host", "kind": "connected", "caller": "KP3M-2",
                      "answerer": "KP3M-12", "bw": "wide"})
        self.s.apply({"t": "host", "kind": "bitrate", "level": 7, "bps": 2412, "tx": False})
        self.s.apply({"t": "host", "kind": "disconnected"})
        self.s.apply({"t": "notice", "kind": "unable_to_connect"})
        texts = [t for _, t in self.s.snapshot()["log"]]
        self.assertEqual(texts[0], "Linked KP3M-2>KP3M-12 wide")
        self.assertTrue(texts[1].startswith("Disconnected"))
        self.assertEqual(texts[2], "Unable to connect")

    def test_waterfall_rows(self):
        self.s.apply_row(row_frame([10] * 512))
        self.s.apply_row(b"\x02\x00\x01\x00x")          # not a waterfall row
        self.assertEqual(len(self.s.snapshot()["rows"]), 1)

    def test_disconnect_clears(self):
        self.s.apply({"t": "hello", "mode": "FM"})
        self.s.apply(STATUS)
        self.s.set_connected(False)
        snap = self.s.snapshot()
        self.assertFalse(snap["connected"])
        self.assertEqual(snap["status"], {})


class FakeXara(threading.Thread):
    """Accepts one WebSocket client like Xara's /ws and sends a few messages."""

    def __init__(self):
        super().__init__(daemon=True)
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(1)
        self.port = self.srv.getsockname()[1]
        self.got = []            # text the client sent (unmasked)
        self.pong = None
        self.request = b""

    def run(self):
        c, _ = self.srv.accept()
        while b"\r\n\r\n" not in self.request:
            self.request += c.recv(1024)
        key = [l.split(b":", 1)[1].strip() for l in self.request.split(b"\r\n")
               if l.lower().startswith(b"sec-websocket-key")][0]
        acc = base64.b64encode(hashlib.sha1(key + b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11").digest())
        c.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                  b"Connection: Upgrade\r\nSec-WebSocket-Accept: " + acc + b"\r\n\r\n")
        op, data = xf.read_frame(c)        # client text, must be masked (read_frame unmasks)
        self.got.append((op, data))

        def send(op, payload):
            n = len(payload)
            head = bytes([0x80 | op]) + (bytes([n]) if n < 126 else bytes([126]) + struct.pack(">H", n))
            c.sendall(head + payload)
        send(1, json.dumps({"t": "hello", "mode": "FM wide"}).encode())
        send(1, json.dumps(STATUS).encode())
        send(2, row_frame([20] * 512))
        send(9, b"hi")                      # ping
        op, data = xf.read_frame(c)
        self.pong = (op, data)
        send(8, b"\x03\xe8")                # close
        c.close()


class Client(unittest.TestCase):
    def test_feed_against_fake_xara(self):
        fake = FakeXara()
        fake.start()
        state = xf.XaraState()
        stop = threading.Event()
        logs = []

        def log(m):
            logs.append(m)
            if "connected" in m:
                pass
        t = threading.Thread(target=xf.run_feed, args=(state, lambda: fake.port, stop),
                             kwargs={"log": log}, daemon=True)
        t.start()
        fake.join(5)
        stop.set()
        self.assertIn(b"Host: localhost:%d" % fake.port, fake.request)
        self.assertEqual(fake.got, [(1, b"WATERFALL ON")])
        self.assertEqual(fake.pong, (0xA, b"hi"))
        t.join(2)
        snap = state.snapshot()
        self.assertEqual(snap["mode"], "FM wide")
        # the rows are cleared when the fake closes; the counter is not
        self.assertEqual(state.rows_seen, 1)
        self.assertFalse(snap["connected"])          # closed -> not connected
        self.assertIn("VARA feed connected", logs[0])


class Screens(unittest.TestCase):
    def test_render(self):
        s = xf.XaraState()
        s.apply({"t": "hello", "mode": "FM wide"})
        for _ in range(3):
            s.apply_row(row_frame(list(range(256)) * 2))
        s.apply(STATUS)
        s.apply({"t": "host", "kind": "connected", "caller": "A", "answerer": "B", "bw": "wide"})
        base = {"mode": "CL", "ip": "192.168.86.133", "vara": s.snapshot()}
        for fn in (render.vara_screen, render.vara_log):
            img = fn(base, 0.0)
            self.assertEqual(img.size, (240, 240))
        # starting screen when the feed is not up yet
        img = render.vara_screen({"mode": "AP", "ip": "172.16.0.1", "vara": {"connected": False}})
        self.assertEqual(img.size, (240, 240))

    def test_bpm_shown(self):
        s = xf.XaraState()
        s.apply({"t": "hello", "mode": "FM"})
        s.apply(STATUS)
        img = render.vara_screen({"mode": "CL", "ip": "x", "vara": s.snapshot()}, 0.0)
        # the BPM value is drawn in RX green on the right of row 4
        px = [img.getpixel((x, y)) for x in range(150, 236) for y in range(84, 102)]
        self.assertIn(render.RX_GREEN, px)


if __name__ == "__main__":
    unittest.main()
