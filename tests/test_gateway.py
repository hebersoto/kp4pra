"""Direct tests of Gateway relay logic using fake CMS/KISS endpoints.
No real Direwolf or CMS connection is used.
"""
import asyncio
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))

from rms.gateway import Gateway
from rms.ax25 import split_frame, is_i, is_s, ns, nr, LinkState, make_frame, iframe, PID_NO_L3, UA, DM


class FakeWriter:
    """Stands in for the asyncio.StreamWriter the gateway uses to send KISS frames."""
    def __init__(self):
        self.sent = []  # raw KISS-encoded bytes, in order
    def write(self, data):
        self.sent.append(data)
    async def drain(self):
        pass


class FakeCms:
    """Stands in for CmsSession: yields queued bytes on recv(), records sent bytes."""
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.sent = bytearray()
        self.closed = False
    async def recv(self, n=1024):
        if self._chunks:
            return self._chunks.pop(0)
        return b''  # simulates CMS closing the stream
    async def send(self, data):
        self.sent += data
    async def close(self):
        self.closed = True


def decode_kiss_i_frames(raw_frames):
    """Given a list of raw AX.25 frames (already de-KISS'd), return list of (ns, nr, payload)."""
    out = []
    for f in raw_frames:
        dest, src, path, ctrl, pid, payload = split_frame(f)
        if is_i(ctrl):
            out.append((ns(ctrl), nr(ctrl), payload))
    return out


def make_gateway():
    cfg = {
        'rms': {'enabled': True, 'cms_call': 'KP3M-2', 'cms_password': 'x',
                 'frequency_hz': 145090000, 'mode': 'PACKET-1200'},
        'direwolf': {'host': '127.0.0.1', 'port': 8001},
    }
    return Gateway(cfg)


def run(coro):
    return asyncio.run(coro)


def test_cms_to_rf_chunks_into_200_byte_frames():
    # 450 bytes from CMS should split into 200 + 200 + 50
    gw = make_gateway()
    gw.writer = FakeWriter()
    gw.link = LinkState('NP4JN')
    gw.cms = FakeCms([b'A' * 450])

    run(gw.cms_to_rf())

    from rms.ax25 import KissDecoder
    dec = KissDecoder()
    frames = []
    for raw in gw.writer.sent:
        frames.extend(dec.feed(raw))
    iframes = decode_kiss_i_frames(frames)

    sizes = [len(p) for _, _, p in iframes]
    assert sizes == [200, 200, 50], f"unexpected chunk sizes: {sizes}"


def test_cms_to_rf_increments_ns_correctly():
    gw = make_gateway()
    gw.writer = FakeWriter()
    gw.link = LinkState('NP4JN')
    gw.cms = FakeCms([b'B' * 450])

    run(gw.cms_to_rf())

    from rms.ax25 import KissDecoder
    dec = KissDecoder()
    frames = []
    for raw in gw.writer.sent:
        frames.extend(dec.feed(raw))
    iframes = decode_kiss_i_frames(frames)

    seqs = [n for n, _, _ in iframes]
    assert seqs == [0, 1, 2], f"N(S) did not increment as expected: {seqs}"


def test_cms_to_rf_stops_cleanly_when_cms_closes():
    # recv() returning b'' should end the loop and close the session
    # (self.cms/self.link become None afterward).
    gw = make_gateway()
    gw.writer = FakeWriter()
    gw.link = LinkState('NP4JN')
    fake_cms = FakeCms([])  # immediately returns b''
    gw.cms = fake_cms

    run(gw.cms_to_rf())

    assert gw.cms is None and gw.link is None
    assert fake_cms.closed


def test_handle_rejects_second_sabm_from_different_station():
    # While a session with NP4JN is active, a SABM from a different
    # callsign must be answered with DM, not accepted.
    gw = make_gateway()
    gw.writer = FakeWriter()
    gw.link = LinkState('NP4JN')
    gw.cms = FakeCms([])

    sabm_ctrl = 0x2F
    frame = make_frame('KP3M-2', 'W1AW', sabm_ctrl)
    run(gw.handle(frame))

    from rms.ax25 import KissDecoder
    dec = KissDecoder()
    frames = []
    for raw in gw.writer.sent:
        frames.extend(dec.feed(raw))
    assert len(frames) == 1
    dest, src, path, ctrl, pid, payload = split_frame(frames[0])
    assert ctrl == DM and dest == 'W1AW'
    # existing session must be untouched
    assert gw.link.remote == 'NP4JN'


def test_handle_forwards_iframe_payload_to_cms():
    gw = make_gateway()
    gw.writer = FakeWriter()
    gw.link = LinkState('NP4JN')
    fake_cms = FakeCms([])
    gw.cms = fake_cms

    ctrl = iframe(send=0, recv=0)
    frame = make_frame('KP3M-2', 'NP4JN', ctrl, b'hello cms', PID_NO_L3)
    run(gw.handle(frame))

    assert bytes(fake_cms.sent) == b'hello cms'
    assert gw.link.vr == 1  # N(R) advanced after accepting in-sequence I-frame
def test_cms_to_rf_blocks_when_window_full_and_resumes_on_ack():
    # Feed enough data for 9 frames (>7). The 8th frame must not be sent
    # until something external advances link.va (simulating an RR from
    # the RF station). We advance va from a background task after a short
    # delay and confirm the frame count before/after proves the block
    # actually happened, not just that it eventually finished.
    gw = make_gateway()
    gw.writer = FakeWriter()
    gw.link = LinkState('NP4JN')
    gw.cms = FakeCms([b'C' * (200 * 9)])  # 9 full 200-byte frames

    async def scenario():
        task = asyncio.create_task(gw.cms_to_rf())
        # Give cms_to_rf time to send the first 7 frames and hit the gate.
        await asyncio.sleep(0.2)
        sent_before = len(gw.writer.sent)

        # Give it more time WITHOUT acking -- it must still be stuck at 7.
        await asyncio.sleep(0.3)
        sent_while_blocked = len(gw.writer.sent)

        # Now simulate peer ack: advance va, which should free the gate.
        gw.link.va = 2
        await asyncio.wait_for(task, timeout=2)
        sent_after = len(gw.writer.sent)
        return sent_before, sent_while_blocked, sent_after

    sent_before, sent_while_blocked, sent_after = run(scenario())

    assert sent_before == 7, f"expected exactly 7 frames sent before ack, got {sent_before}"
    assert sent_while_blocked == 7, f"loop must not send more while window is full, got {sent_while_blocked}"
    assert sent_after == 9, f"expected all 9 frames sent after ack freed the window, got {sent_after}"
