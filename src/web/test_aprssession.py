"""Lifecycle test for aprssession.AprsSession: interlock, RX loop, send,
auto-ack, stop -- with a fake Dire Wolf and fake systemctl (no socket/systemd)."""
import asyncio
import ax25_stub as ax25
import aprs
import aprssession as sess
from aprs_transports import UI


class FakeReader:
    def __init__(self): self.q = asyncio.Queue()
    async def read(self, n=1024): return await self.q.get()
    def push(self, data): self.q.put_nowait(data)


class FakeWriter:
    def __init__(self): self.sent = bytearray(); self.closed = False
    def write(self, b): self.sent.extend(b)
    async def drain(self): pass
    def close(self): self.closed = True


class FakeSystemd:
    """Tracks the RMS service state; records actions."""
    def __init__(self, active=True):
        self.active = active
        self.actions = []
    def svc(self, action):
        self.actions.append(action)
        if action == "stop": self.active = False
        elif action == "start": self.active = True
        return (True, "")
    def svc_active(self): return self.active


def kiss_frames(buf):
    """Decode all AX.25 frames from accumulated KISS bytes."""
    dec = ax25.KissDecoder()
    return dec.feed(bytes(buf))


async def main():
    reader, writer = FakeReader(), FakeWriter()
    async def opener(host, port): return reader, writer
    sysd = FakeSystemd(active=True)

    cfg = {
        "station": {"callsign": "KP3M"},
        "direwolf": {"host": "127.0.0.1", "port": 8001},
        "aprs": {"enabled": True, "tocall": "APKP41",
                 "digipath": ["WIDE1-1"], "retry_interval": 30,
                 "max_retries": 3, "aprsis": {"enabled": False}},
    }
    s = sess.AprsSession(ax25, cfg, opener=opener, svc=sysd.svc,
                         svc_active=sysd.svc_active, rms_busy=lambda: False,
                         retry_tick=0.05)

    # --- start: stops RMS, becomes active ---
    r = await s.start()
    assert r["ok"] and s.active, r
    assert "stop" in sysd.actions and sysd.active is False, sysd.actions
    print("PASS start stops RMS and activates")

    # --- send a message: a KISS frame hits the wire ---
    r = await s.send("KP4DOG", "Need status")
    assert r["ok"], r
    frames = kiss_frames(writer.sent)
    dest, src, digis, ctrl, pid, payload = ax25.split_frame(frames[-1])
    assert dest == "APKP41" and src == "KP3M" and digis == ["WIDE1-1"]
    assert (ctrl & 0xEF) == UI and pid == 0xF0
    assert payload.decode() == ":KP4DOG   :Need status{1"
    print("PASS send emits correct UI frame")

    # --- inbound message from a peer -> stored + auto-ack transmitted ---
    before = len(writer.sent)
    info = aprs.format_message("KP3M", "On my way", "7")
    inbound = ax25.kiss_encode(ax25.make_frame("APKP41", "KP4DOG-10", UI,
                                               info.encode(), ax25.PID_NO_L3))
    reader.push(inbound)
    await asyncio.sleep(0.1)                     # let RX loop process
    msgs = s.messages_since(0)
    incoming = [m for m in msgs if m["direction"] == "in"]
    assert incoming and incoming[-1]["text"] == "On my way", msgs
    assert incoming[-1]["peer"] == "KP4DOG-10"
    # auto-ack should have been transmitted
    new = kiss_frames(writer.sent[before:])
    ack_payloads = [ax25.split_frame(f)[5].decode() for f in new]
    assert ":KP4DOG-10:ack7" in ack_payloads, ack_payloads
    print("PASS inbound stored and auto-acked")

    # --- peer acks OUR message -> status flips to acked ---
    ackframe = ax25.kiss_encode(ax25.make_frame(
        "APKP41", "KP4DOG", UI, aprs.format_ack("KP3M", "1").encode(),
        ax25.PID_NO_L3))
    reader.push(ackframe)
    await asyncio.sleep(0.1)
    outs = [m for m in s.messages_since(0) if m["direction"] == "out"]
    assert outs[0]["status"] == "acked", outs
    print("PASS our message marked acked on peer ack")

    # --- status + threads ---
    st = s.status()
    assert st["active"] and st["mycall"] == "KP3M"
    th = s.threads()
    assert "KP4DOG" in th and "KP4DOG-10" in th
    print("PASS status/threads")

    # --- stop: restarts RMS, inactive ---
    r = await s.stop()
    assert r["ok"] and not s.active and sysd.active is True
    assert sysd.actions[-1] == "start"
    print("PASS stop restarts RMS and deactivates")

    # --- start refused while RMS is mid-relay ---
    s2 = sess.AprsSession(ax25, cfg, opener=opener, svc=sysd.svc,
                          svc_active=sysd.svc_active, rms_busy=lambda: True)
    r = await s2.start()
    assert not r["ok"] and "mid-relay" in r["error"], r
    print("PASS start refused while RMS busy")

    print("\nALL APRS SESSION TESTS PASSED")


async def beacon_tests():
    """Position beacon: grid->coords, explicit lat/lon override, not-active guard."""
    # --- not active: beacon refused ---
    cfg0 = {"station": {"callsign": "KP3M", "mygrid": "FK68wd"},
            "aprs": {"enabled": True, "beacon": {}}}
    s0 = sess.AprsSession(ax25, cfg0, opener=lambda h, p: None,
                          svc=lambda a: (True, ""), svc_active=lambda: False,
                          rms_busy=lambda: False)
    r = await s0.beacon_now()
    assert not r["ok"] and "not running" in r["error"], r
    print("PASS beacon refused when session inactive")

    reader, writer = FakeReader(), FakeWriter()
    async def opener(host, port): return reader, writer
    sysd = FakeSystemd(active=False)

    # --- grid-derived position beacon ---
    cfg = {
        "station": {"callsign": "KP3M", "mygrid": "FK68wd"},
        "direwolf": {"host": "127.0.0.1", "port": 8001},
        "aprs": {"enabled": True, "tocall": "APKP41",
                 "digipath": ["WIDE1-1", "WIDE2-2"],
                 "aprsis": {"enabled": False},
                 "beacon": {"enabled": False, "interval_min": 0,
                            "symbol_table": "/", "symbol_code": "-",
                            "comment": "KP4PRA EOC"}},
    }
    s = sess.AprsSession(ax25, cfg, opener=opener, svc=sysd.svc,
                         svc_active=sysd.svc_active, rms_busy=lambda: False,
                         retry_tick=0.05)
    r = await s.start()
    assert r["ok"] and s.active, r

    before = len(writer.sent)
    r = await s.beacon_now()
    assert r["ok"], r
    # position from grid FK68wd
    ll = aprs.maidenhead_to_latlon("FK68wd")
    assert abs(r["lat"] - ll[0]) < 1e-6 and abs(r["lon"] - ll[1]) < 1e-6, r
    # a UI frame carrying the position report went out to the wire
    frames = kiss_frames(writer.sent[before:])
    dest, src, digis, ctrl, pid, payload = ax25.split_frame(frames[-1])
    assert dest == "APKP41" and src == "KP3M"
    assert digis == ["WIDE1-1", "WIDE2-2"], digis          # multi-hop path
    assert (ctrl & 0xEF) == UI and pid == 0xF0
    body = payload.decode()
    assert body.startswith("=") and body.endswith("KP4PRA EOC"), body
    assert body == r["info"], (body, r["info"])
    print("PASS beacon transmits grid-derived position frame:", body)

    # --- explicit beacon lat/lon overrides grid ---
    cfg2 = dict(cfg)
    cfg2["aprs"] = dict(cfg["aprs"])
    cfg2["aprs"]["beacon"] = dict(cfg["aprs"]["beacon"])
    cfg2["aprs"]["beacon"]["lat"] = 18.4655
    cfg2["aprs"]["beacon"]["lon"] = -66.1057
    s.cfg = cfg2
    r = await s.beacon_now()
    assert r["ok"] and abs(r["lat"] - 18.4655) < 1e-6 and abs(r["lon"] - (-66.1057)) < 1e-6, r
    print("PASS beacon uses explicit lat/lon over grid")

    await s.stop()
    print("\nALL APRS BEACON TESTS PASSED")


loop = asyncio.get_event_loop()
loop.run_until_complete(main())
loop.run_until_complete(beacon_tests())
