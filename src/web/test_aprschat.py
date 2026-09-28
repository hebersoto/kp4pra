"""Unit tests for aprschat.py using a mock transport (no radio, no socket)."""
import asyncio
import aprs
import aprschat as ac


class MockTransport:
    """Records outgoing info fields; lets the test inject inbound frames."""
    def __init__(self):
        self.sent = []          # list of info fields we transmitted
        self.on_frame = None    # set by AprsChat

    async def send_info(self, info):
        self.sent.append(info)

    async def rx(self, src, info):
        """Simulate receiving a packet from `src`."""
        return await self.on_frame(src, info)


def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def t(name, cond):
    print(("PASS " if cond else "FAIL ") + name)
    assert cond, name


# fixed clock we can advance
class Clock:
    def __init__(self, t0=1000.0): self.t = t0
    def __call__(self): return self.t


# ---- outgoing with ack, then peer acks ----
def test_send_and_ack():
    tr = MockTransport()
    chat = ac.AprsChat("KP3M", tr, tocall="APKP41", digipath=["WIDE1-1"])
    m = run(chat.send_text("KP4DOG", "Need status"))
    t("send stored as out/sending", m.direction == "out" and m.status == ac.ST_SENDING)
    t("send assigned msgno", m.msgno == "1")
    t("send transmitted info", tr.sent[-1] == ":KP4DOG   :Need status{1")
    # peer sends ack back (ack addressee is US, source is the peer)
    run(tr.rx("KP4DOG", aprs.format_ack("KP3M", "1")))
    t("our msg marked acked", m.status == ac.ST_ACKED)


# ---- incoming message triggers store + auto-ack ----
def test_incoming_autoack():
    tr = MockTransport()
    chat = ac.AprsChat("KP3M", tr)
    m = run(tr.rx("KP4DOG-10", ":KP3M     :Roger, en route{17"))
    t("incoming stored", m is not None and m.direction == "in")
    t("incoming peer is source", m.peer == "KP4DOG-10")
    t("incoming text", m.text == "Roger, en route")
    t("auto-ack sent", tr.sent[-1] == ":KP4DOG-10:ack17")


# ---- incoming without msgno: stored, no ack ----
def test_incoming_no_ack():
    tr = MockTransport()
    chat = ac.AprsChat("KP3M", tr)
    run(tr.rx("W1AW", ":KP3M     :hello no ack"))
    t("no auto-ack when no msgno", tr.sent == [])


# ---- message addressed to someone else is ignored ----
def test_not_for_us():
    tr = MockTransport()
    chat = ac.AprsChat("KP3M", tr)
    m = run(tr.rx("W1AW", ":N0CALL   :not for you{3"))
    t("foreign message ignored", m is None and tr.sent == [])


# ---- addressed to our base call but different SSID (default: match base) ----
def test_ssid_matching():
    tr = MockTransport()
    chat = ac.AprsChat("KP3M-7", tr)       # our SSID is -7
    m = run(tr.rx("W1AW", ":KP3M     :base call match{4"))
    t("base-callsign match accepts", m is not None and m.direction == "in")
    # strict SSID mode rejects a mismatch
    tr2 = MockTransport()
    chat2 = ac.AprsChat("KP3M-7", tr2, match_ssid=True)
    m2 = run(tr2.rx("W1AW", ":KP3M     :needs exact ssid{5"))
    t("strict ssid rejects mismatch", m2 is None)


# ---- reject marks our message failed ----
def test_reject():
    tr = MockTransport()
    chat = ac.AprsChat("KP3M", tr)
    m = run(chat.send_text("KP4DOG", "please copy"))
    run(tr.rx("KP4DOG", aprs.format_reject("KP3M", m.msgno)))
    t("reject marks failed", m.status == ac.ST_FAILED)


# ---- retries then failure ----
def test_retries():
    tr = MockTransport()
    clk = Clock(1000.0)
    chat = ac.AprsChat("KP3M", tr, retry_interval=30, max_retries=3, clock=clk)
    m = run(chat.send_text("KP4DOG", "no answer"))
    t("initially not due", chat.due_retries() == [])
    # advance past first interval -> one resend due
    clk.t = 1000.0 + 31
    n = run(chat.resend_due())
    t("first retry fires", n == 1 and m.retries == 1)
    t("resent same info", tr.sent[-1] == m.info)
    # need to pass interval*(retries+1) each time
    clk.t = 1000.0 + 61
    run(chat.resend_due())
    clk.t = 1000.0 + 200
    run(chat.resend_due())
    t("retries capped at max", m.retries == 3)
    # next check marks it failed
    clk.t = 1000.0 + 1000
    run(chat.resend_due())
    t("failed after max retries", m.status == ac.ST_FAILED)


# ---- ack after some retries still marks acked ----
def test_ack_after_retry():
    tr = MockTransport()
    clk = Clock(1000.0)
    chat = ac.AprsChat("KP3M", tr, retry_interval=30, clock=clk)
    m = run(chat.send_text("KP4DOG", "hang in there"))
    clk.t = 1031
    run(chat.resend_due())
    run(tr.rx("KP4DOG", aprs.format_ack("KP3M", m.msgno)))
    t("late ack still acks", m.status == ac.ST_ACKED)
    clk.t = 2000
    t("acked msg no longer due", chat.due_retries() == [])


# ---- threading + polling ----
def test_threads_and_polling():
    tr = MockTransport()
    chat = ac.AprsChat("KP3M", tr)
    run(chat.send_text("KP4DOG", "msg A"))
    run(tr.rx("W1AW", ":KP3M     :hi from W1AW{9"))   # +incoming +auto-ack stored? ack is sent not stored
    run(chat.send_text("W1AW", "reply to W1AW"))
    threads = chat.store.threads()
    t("two peer threads", set(threads.keys()) == {"KP4DOG", "W1AW"})
    t("kp4dog thread len", len(threads["KP4DOG"]) == 1)
    t("w1aw thread len", len(threads["W1AW"]) == 2)   # incoming + our reply
    # polling: everything since 0
    allmsgs = chat.store.since(0)
    t("since(0) returns all", len(allmsgs) == 3)
    head = chat.store.head
    run(chat.send_text("KP4DOG", "another"))
    newer = chat.store.since(head)
    t("since(head) returns only new", len(newer) == 1 and newer[0]["text"] == "another")


# ---- no-ack send stays 'sent', never retried ----
def test_no_ack_send():
    tr = MockTransport()
    clk = Clock(1000.0)
    chat = ac.AprsChat("KP3M", tr, clock=clk)
    m = run(chat.send_text("KP4DOG", "fire and forget", want_ack=False))
    t("no-ack has no msgno", m.msgno is None and m.status == ac.ST_SENT)
    t("no-ack info has no brace", "{" not in tr.sent[-1])
    clk.t = 5000
    t("no-ack never due for retry", chat.due_retries() == [])


for fn in [test_send_and_ack, test_incoming_autoack, test_incoming_no_ack,
           test_not_for_us, test_ssid_matching, test_reject, test_retries,
           test_ack_after_retry, test_threads_and_polling, test_no_ack_send]:
    fn()

print("\nALL APRS CHAT-LOGIC TESTS PASSED")
