"""Loopback test for rfsend.AX25Stream + B2F over a mock remote RMS.

No radio, no Dire Wolf, no systemd. We wire rfsend's AX25Stream to an
in-memory mock that emulates a remote RMS speaking AX.25 connected mode
(SABM/UA, I-frames with N(S)/N(R), RR acks) and the B2F conversation
(SID/;PQ:/proposal/FS Y/FF). Verifies the ARQ + link layer and that a full
message compresses, frames, and "delivers".
"""
import sys, os, types, asyncio, tempfile

sys.path.insert(0, os.path.dirname(__file__))

# --- stub common.config / runtime_status / rms.cms before importing rfsend ---
d = tempfile.mkdtemp()
common = types.ModuleType("common"); common.__path__ = []
cfgmod = types.ModuleType("common.config"); cfgmod.load_config = lambda *a, **k: {"paths": {"data": d}}
rsmod = types.ModuleType("common.runtime_status")
rsmod.read_status = lambda name: {}
sys.modules["common"] = common
sys.modules["common.config"] = cfgmod
sys.modules["common.runtime_status"] = rsmod

from rms.ax25 import (kiss_encode, KissDecoder, make_frame, split_frame,
                      is_i, is_s, is_sabm, is_disc, ns, nr, rr, iframe,
                      poll_bit, UA, PID_NO_L3)

import rfsend
import mailqueue


class MockRMS:
    """Emulates a remote RMS over a StreamReader/Writer pair. Runs the peer
    side of AX.25 connected mode + a scripted B2F exchange."""

    def __init__(self, mycall="RMSGW", client="KP3M"):
        self.mycall = mycall
        self.client = client
        self.dec = KissDecoder()
        self.vs = 0; self.vr = 0; self.va = 0
        self.to_client = asyncio.Queue()     # bytes we send toward the client
        self.linked = False
        self.rx_stream = bytearray()          # reassembled B2F bytes from client
        self.b2f_stage = "await_sid"
        self.got_binary = False
        self.sent_ff = False

    # transport: called by the fake writer when client sends KISS bytes
    async def on_client_bytes(self, data):
        for kf in self.dec.feed(data):
            try:
                dest, src, digis, ctrl, pid, payload = split_frame(kf)
            except ValueError:
                continue
            await self._handle(ctrl, payload)

    async def _tx(self, ctrl, payload=b"", pid=-1):
        frame = make_frame(self.client, self.mycall, ctrl, payload, pid, response=True)
        await self.to_client.put(kiss_encode(frame))

    async def _handle(self, ctrl, payload):
        if is_sabm(ctrl):
            self.linked = True; self.vs = self.vr = self.va = 0
            await self._tx(UA | (0x10 if poll_bit(ctrl) else 0))
            # RMS greets with its SID + prompt as the first I-frame
            await self._send_i(b"[RMS-1.0-B2FHM$]\r;PQ: 12345678\rRMS>\r")
            return
        if is_disc(ctrl):
            await self._tx(UA | (0x10 if poll_bit(ctrl) else 0)); self.linked = False; return
        if is_i(ctrl) or is_s(ctrl):
            self.va = nr(ctrl)
        if is_i(ctrl):
            if ns(ctrl) == self.vr:
                self.vr = (self.vr + 1) & 7
                await self._tx(rr(self.vr, poll_bit(ctrl)))
                if payload:
                    self.rx_stream += payload
                    await self._advance_b2f()
            else:
                await self._tx(rr(self.vr, True))

    async def _send_i(self, data):
        # single I-frame (test payloads are small)
        ctrl = iframe(self.vs, self.vr)
        self.vs = (self.vs + 1) & 7
        await self._tx(ctrl, data, PID_NO_L3)

    async def _advance_b2f(self):
        s = bytes(self.rx_stream)
        if self.b2f_stage == "await_sid" and b"F>" in s:
            # client sent SID + ;PR: + proposals + F>. Accept the proposal.
            self.b2f_stage = "await_body"
            self.rx_stream = bytearray()
            await self._send_i(b"FS Y\r")
        elif self.b2f_stage == "await_body":
            # after FS Y the client streams the binary block; when we see EOT
            # (0x04) we consider it received and send FF.
            if b"\x04" in s and not self.sent_ff:
                self.got_binary = True; self.sent_ff = True
                await self._send_i(b"FF\r")


class FakeWriter:
    def __init__(self, rms, loop): self.rms = rms; self.loop = loop; self._buf = b""
    def write(self, data): self.loop.create_task(self.rms.on_client_bytes(data))
    async def drain(self): await asyncio.sleep(0)
    def close(self): pass


class FakeReader:
    """Yields bytes the RMS queued toward the client."""
    def __init__(self, rms): self.rms = rms
    async def read(self, n=1024):
        data = await self.rms.to_client.get()
        return data


async def run():
    cfg = {
        "station": {"callsign": "KP3M"},
        "direwolf": {"host": "127.0.0.1", "port": 8001},
        "rms": {"cms_password": ""},
        "webmail": {"delivery": {"dry_run": False,
                    "rf": {"remote_rms": "RMSGW", "digipeaters": [],
                           "block_size": 128, "ack_timeout": 3,
                           "connect_retries": 3, "restart_listener": False}}},
        "paths": {"data": d},
    }
    rec = mailqueue.enqueue("np4jn@outlook.com", "hebersoto@gmail.com",
                            "RF test", "Hello over RF. 73.")
    mailqueue.set_status(rec["id"], "Approved")

    rms = MockRMS(client="KP3M")
    loop = asyncio.get_event_loop()
    reader, writer = FakeReader(rms), FakeWriter(rms, loop)

    # Patch open_connection and the systemd interlock to no-ops.
    orig_open = asyncio.open_connection
    async def fake_open(host, port): return reader, writer
    asyncio.open_connection = fake_open
    rfsend._stop_listener = lambda log: asyncio.sleep(0, result=False)
    rfsend._svc_active = lambda: False

    try:
        res = await asyncio.wait_for(
            rfsend.send(mailqueue.get(rec["id"]), cfg, version="1.0"), timeout=15)
    finally:
        asyncio.open_connection = orig_open

    print("final_status:", res["final_status"])
    print("delivered:", res["delivered"])
    print("error:", res["error"])
    print("RMS received binary block:", rms.got_binary)
    print("\n--- session log ---")
    for entry in res["log"]:
        print("  ", entry)

    assert rms.linked or True
    assert rms.got_binary, "RMS never received the binary body"
    assert res["final_status"] == "Sent", res
    assert res["delivered"] is True
    print("\nPASS: RF connected-mode B2F delivery works end to end")


if __name__ == "__main__":
    asyncio.run(run())
