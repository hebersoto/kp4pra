"""Direct tests of Gateway relay logic using fake CMS/KISS endpoints.
No real Direwolf or CMS connection is used.
"""
import asyncio
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'src'))

from rms.gateway import Gateway
from rms.ax25 import split_frame, is_i, is_s, ns, nr, LinkState, make_frame, iframe, PID_NO_L3, UA, DM


class FakeWriter:
    def __init__(self):
        self.sent = []
    def write(self, data):
        self.sent.append(data)
    async def drain(self):
        pass


class FakeCms:
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self.sent = bytearray()
        self.closed = False
    async def recv(self, n=1024):
        if self._chunks:
            return self._chunks.pop(0)
        return b''
    async def send(self, data):
        self.sent += data
    async def close(self):
        self.closed = True


def decode_kiss_i_frames(raw_frames):
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
    gw = make_gateway()
    gw.writer = FakeWriter()
    gw.link = LinkState('NP4JN')
    fake_cms = FakeCms([])
    gw.cms = fake_cms
    run(gw.cms_to_rf())
    assert gw.cms is None and gw.link is None
    assert fake_cms.closed


def test_handle_rejects_second_sabm_from_different_station():
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
    assert (ctrl & 0xEF) == DM and dest == 'W1AW'  # mask P/F bit
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
    assert gw.link.vr == 1


def test_cms_to_rf_blocks_when_window_full_and_resumes_on_ack():
    gw = make_gateway()
    gw.writer = FakeWriter()
    gw.link = LinkState('NP4JN')
    gw.cms = FakeCms([b'C' * (200 * 9)])

    async def scenario():
        task = asyncio.create_task(gw.cms_to_rf())
        await asyncio.sleep(0.2)
        sent_before = len(gw.writer.sent)
        await asyncio.sleep(0.3)
        sent_while_blocked = len(gw.writer.sent)
        gw.link.va = 2
        await asyncio.wait_for(task, timeout=2)
        sent_after = len(gw.writer.sent)
        return sent_before, sent_while_blocked, sent_after

    sent_before, sent_while_blocked, sent_after = run(scenario())
    assert sent_before == 7, f"expected exactly 7 frames sent before ack, got {sent_before}"
    assert sent_while_blocked == 7, f"loop must not send more while window is full, got {sent_while_blocked}"
    assert sent_after == 9, f"expected all 9 frames sent after ack freed the window, got {sent_after}"


def test_handle_disc_closes_session_and_sends_ua():
    gw = make_gateway()
    gw.writer = FakeWriter()
    gw.link = LinkState('NP4JN')
    fake_cms = FakeCms([])
    gw.cms = fake_cms
    disc_ctrl = 0x43
    frame = make_frame('KP3M-2', 'NP4JN', disc_ctrl)
    run(gw.handle(frame))
    from rms.ax25 import KissDecoder
    dec = KissDecoder()
    frames = []
    for raw in gw.writer.sent:
        frames.extend(dec.feed(raw))
    assert len(frames) == 1
    dest, src, path, ctrl, pid, payload = split_frame(frames[0])
    assert (ctrl & 0xEF) == UA and dest == 'NP4JN'  # mask P/F bit
    assert gw.link is None and gw.cms is None
    assert fake_cms.closed


def test_handle_resabm_from_same_station_resets_session():
    gw = make_gateway()
    gw.writer = FakeWriter()
    old_link = LinkState('NP4JN')
    old_link.vs = 5
    gw.link = old_link
    old_cms = FakeCms([])
    gw.cms = old_cms
    sabm_ctrl = 0x2F
    frame = make_frame('KP3M-2', 'NP4JN', sabm_ctrl)

    import rms.gateway as gwmod
    class StubCms:
        def __init__(self, *a, **k):
            self.connected = False
        async def connect(self):
            self.connected = True
        async def recv(self, n=1024):
            await asyncio.sleep(3600)
        async def send(self, data):
            pass
        async def close(self):
            pass
    orig_cms_cls = gwmod.CmsSession
    gwmod.CmsSession = StubCms

    async def scenario():
        await gw.handle(frame)
        from rms.ax25 import KissDecoder
        dec = KissDecoder()
        frames = []
        for raw in gw.writer.sent:
            frames.extend(dec.feed(raw))
        return frames, gw.link

    try:
        frames, link_snapshot = run(scenario())
    finally:
        gwmod.CmsSession = orig_cms_cls

    assert len(frames) == 1
    dest, src, path, ctrl, pid, payload = split_frame(frames[0])
    assert (ctrl & 0xEF) == UA and dest == 'NP4JN'  # mask P/F bit
    assert link_snapshot is not None
    assert link_snapshot.vs == 0
    assert link_snapshot is not old_link


def test_rapid_resabm_cancels_stale_cms_to_rf_task():
    # Simulates WP4DAS-style rapid retry: a second SABM arrives from the
    # SAME station while the first session's cms_to_rf() task is still
    # alive (blocked in cms.recv()). The old task must be cancelled, not
    # left to race the new task over the same/next CmsSession's socket.
    gw = make_gateway()
    gw.writer = FakeWriter()

    import rms.gateway as gwmod
    created = []

    class SlowCms:
        def __init__(self, *a, **k):
            self.closed = False
            created.append(self)
        async def connect(self):
            pass
        async def recv(self, n=1024):
            await asyncio.sleep(3600)
        async def send(self, data):
            pass
        async def close(self):
            self.closed = True

    orig_cms_cls = gwmod.CmsSession
    gwmod.CmsSession = SlowCms

    sabm = make_frame('KP3M-2', 'NP4JN', 0x2F)

    async def scenario():
        await gw.handle(sabm)
        first_task = gw.cms_task
        await asyncio.sleep(0.05)
        assert not first_task.done()
        await gw.handle(sabm)
        await asyncio.sleep(0.05)
        return first_task

    try:
        first_task = run(scenario())
    finally:
        gwmod.CmsSession = orig_cms_cls

    assert first_task.done(), "stale cms_to_rf task from the first session was not cancelled"
    assert len(created) == 2, f"expected exactly 2 CmsSession instances, got {len(created)}"
    assert created[0].closed, "first (stale) CmsSession was not closed"
