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
