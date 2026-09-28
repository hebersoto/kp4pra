"""Integration tests: RfTransport over a faithful ax25 stub, end to end
through aprschat, plus AprsIsTransport line handling. No real sockets."""
import asyncio
import ax25_stub as ax25
import aprs
import aprschat as ac
from aprs_transports import RfTransport, AprsIsTransport, UI


def run(coro): return asyncio.get_event_loop().run_until_complete(coro)
def t(name, cond):
    print(("PASS " if cond else "FAIL ") + name); assert cond, name


# ---- RF: build a UI frame, decode it back, verify src + info ----
def test_rf_frame_roundtrip():
    tr = RfTransport(ax25, "KP3M-7", tocall="APKP41", digipath=["WIDE1-1", "WIDE2-1"])
    sent = []
    class W:
        def write(self, b): sent.append(b)
        async def drain(self): pass
    tr._writer = W()
    info = aprs.format_message("KP4DOG", "Need status", "5")
    run(tr.send_info(info))
    # sent[0] is a KISS frame; decode it through a fresh decoder + split_frame
    dec = ax25.KissDecoder()
    frames = dec.feed(sent[0])
    t("one kiss frame emitted", len(frames) == 1)
    dest, src, digis, ctrl, pid, payload = ax25.split_frame(frames[0])
    t("dest is tocall", dest == "APKP41")
    t("src is our call+ssid", src == "KP3M-7")
    t("digipath emitted", digis == ["WIDE1-1", "WIDE2-1"])
    t("control is UI", (ctrl & 0xEF) == UI)
    t("pid is F0", pid == 0xF0)
    t("payload is the info field", payload.decode() == info)


# ---- RF: feed() extracts (src, info) from an inbound UI frame ----
def test_rf_feed_inbound():
    tr = RfTransport(ax25, "KP3M", tocall="APKP41")
    # a peer (KP4DOG-10) sends us a message; build the on-air frame with the
    # peer as src, APKP41 as dest, UI/F0, info addressed to KP3M
    info = aprs.format_message("KP3M", "Roger en route", "17")
    frame = ax25.make_frame("APKP41", "KP4DOG-10", UI, info.encode(), ax25.PID_NO_L3)
    kiss = ax25.kiss_encode(frame)
    got = tr.feed(kiss)
    t("feed returns one packet", len(got) == 1)
    t("feed src is peer", got[0][0] == "KP4DOG-10")
    t("feed info is message", aprs.parse_message(got[0][1])["text"] == "Roger en route")
    # non-UI frame (e.g. an I-frame, ctrl even) is ignored
    iframe = ax25.make_frame("APKP41", "KP4DOG-10", 0x00, b"x", ax25.PID_NO_L3)
    t("non-UI ignored", tr.feed(ax25.kiss_encode(iframe)) == [])


# ---- RF end-to-end through AprsChat: incoming msg -> auto-ack frame on wire ----
def test_rf_end_to_end():
    tr = RfTransport(ax25, "KP3M", tocall="APKP41", digipath=["WIDE1-1"])
    sent = []
    class W:
        def write(self, b): sent.append(b)
        async def drain(self): pass
    tr._writer = W()
    chat = ac.AprsChat("KP3M", tr, tocall="APKP41", digipath=["WIDE1-1"])
    # peer sends us a message needing ack
    info = aprs.format_message("KP3M", "status?", "9")
    inbound = ax25.kiss_encode(
        ax25.make_frame("APKP41", "KP4DOG", UI, info.encode(), ax25.PID_NO_L3))
    for src, i in tr.feed(inbound):
        run(chat.on_frame(src, i))
    # chat stored the incoming and auto-acked -> a KISS frame went to the wire
    t("incoming stored", len(chat.store.since(0)) == 1)
    t("auto-ack transmitted", len(sent) == 1)
    dec = ax25.KissDecoder()
    _, _, _, _, _, payload = ax25.split_frame(dec.feed(sent[0])[0])
    t("auto-ack info correct", payload.decode() == ":KP4DOG   :ack9")


# ---- APRS-IS: send builds a proper TNC2 line ----
def test_aprsis_send_line():
    tr = AprsIsTransport("KP3M", tocall="APKP41")
    sent = []
    class W:
        def write(self, b): sent.append(b)
        async def drain(self): pass
    tr._writer = W()
    run(tr.send_info(aprs.format_message("KP4DOG", "hi", "3")))
    line = sent[0].decode().rstrip("\r\n")
    t("aprsis line", line == "KP3M>APKP41,TCPIP*::KP4DOG   :hi{3")


# ---- APRS-IS: parse_line extracts (src, info); comments ignored ----
def test_aprsis_parse():
    tr = AprsIsTransport("KP3M")
    got = tr.parse_line("KP4DOG-10>APKP41,TCPIP*::KP3M     :ping{4")
    t("aprsis parse src", got[0] == "KP4DOG-10")
    t("aprsis parse info", aprs.parse_message(got[1])["msgno"] == "4")
    t("aprsis comment ignored", tr.parse_line("# aprsc server") is None)


# ---- APRS-IS default filter targets our base call ----
def test_aprsis_filter():
    tr = AprsIsTransport("KP3M-5")
    t("default filter is base-call group", tr.filt == "g/KP3M*")


for fn in [test_rf_frame_roundtrip, test_rf_feed_inbound, test_rf_end_to_end,
           test_aprsis_send_line, test_aprsis_parse, test_aprsis_filter]:
    fn()
print("\nALL APRS TRANSPORT TESTS PASSED")
