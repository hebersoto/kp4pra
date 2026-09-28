"""APRS text-chat session logic: threaded message store, ack tracking with
retries, auto-acknowledgment, and a transport abstraction.

Transport-agnostic: the APRS message *semantics* live here (format info
fields via aprs.py, parse incoming frames, match acks, drive retries,
auto-ack received messages). The wire lives behind a Transport object:

  * RfTransport  -- APRS UI frames over a Dire Wolf KISS link (rms/ax25.py)
  * AprsIsTransport -- TNC2 lines over an APRS-IS TCP connection

Both present the same async surface used here:
    await transport.send_info(info_field)      # send one APRS packet
    transport.on_frame = chat.on_frame         # RX callback (async)
so this module and its tests never touch a socket.

The session manager (start/stop, the Dire Wolf connection, the systemd
interlock that stops kp4pra-tnc-rms while chat owns the radio) is a thin
wrapper added when wiring into the web app; it is intentionally not here so
this core stays unit-testable.
"""

import time

import aprs

# Message status values (map to WhatsApp-style ticks in the UI):
#   sending -> single tick (transmitted, awaiting ack)
#   acked   -> double tick (peer acknowledged)
#   sent    -> single tick (no ack was requested)
#   failed  -> retries exhausted, no ack
#   received-> incoming bubble
ST_SENDING = "sending"
ST_ACKED = "acked"
ST_SENT = "sent"
ST_FAILED = "failed"
ST_RECEIVED = "received"


class Message:
    __slots__ = ("seq", "direction", "peer", "text", "msgno", "ts",
                 "status", "path", "retries", "info")

    def __init__(self, seq, direction, peer, text, msgno, ts, status,
                 path, info=""):
        self.seq = seq
        self.direction = direction          # "in" | "out"
        self.peer = peer                    # the other station's callsign
        self.text = text
        self.msgno = msgno
        self.ts = ts
        self.status = status
        self.path = path                    # "rf" | "aprsis"
        self.retries = 0
        self.info = info                    # raw info field (for resend)

    def as_dict(self):
        return {"seq": self.seq, "direction": self.direction,
                "peer": self.peer, "text": self.text, "msgno": self.msgno,
                "ts": self.ts, "status": self.status, "path": self.path,
                "retries": self.retries}


class ChatStore:
    """In-memory threaded message store with a global monotonic sequence so
    the web UI can poll 'give me everything after seq N'. Not persisted
    (chat is a live session); persistence can be layered later."""

    def __init__(self, clock=time.time):
        self._seq = 0
        self._messages = []                 # all messages, in arrival order
        self._threads = {}                  # peer -> [Message]
        self._clock = clock

    def _next_seq(self):
        self._seq += 1
        return self._seq

    def add(self, direction, peer, text, msgno, status, path, info=""):
        peer = aprs.normalize_call(peer)
        m = Message(self._next_seq(), direction, peer, text, msgno,
                    self._clock(), status, path, info)
        self._messages.append(m)
        self._threads.setdefault(peer, []).append(m)
        return m

    def find_outgoing(self, peer, msgno):
        peer = aprs.normalize_call(peer)
        for m in self._threads.get(peer, ()):
            if m.direction == "out" and m.msgno == msgno:
                return m
        return None

    def threads(self):
        """peer -> list of message dicts (chronological)."""
        return {p: [m.as_dict() for m in msgs]
                for p, msgs in self._threads.items()}

    def since(self, seq):
        """All message dicts with seq > the given value (for polling)."""
        return [m.as_dict() for m in self._messages if m.seq > seq]

    @property
    def head(self):
        return self._seq


class AprsChat:
    """APRS messaging session core. Holds the store and a transport, formats
    outgoing messages, matches incoming acks, and auto-acks inbound
    messages. Retries are surfaced via due_retries()/mark_retried() so the
    driving loop stays in control (and tests stay deterministic)."""

    def __init__(self, mycall, transport, store=None, tocall="APKP41",
                 digipath=None, path_label="rf", retry_interval=30,
                 max_retries=3, match_ssid=False, clock=time.time):
        self.mycall = aprs.normalize_call(mycall)
        self.transport = transport
        self.store = store or ChatStore(clock=clock)
        self.tocall = tocall
        self.digipath = list(digipath or [])
        self.path_label = path_label        # label recorded on messages
        self.retry_interval = retry_interval
        self.max_retries = max_retries
        self.match_ssid = match_ssid        # require exact SSID to be "for us"
        self.clock = clock
        self._msgno = aprs.MsgNoGen()
        transport.on_frame = self.on_frame  # wire RX callback

    # ---- addressed-to-us test ----
    def _for_us(self, addressee):
        a = aprs.normalize_call(addressee)
        if self.match_ssid:
            return a == self.mycall
        return a.split("-")[0] == self.mycall.split("-")[0]

    # ---- outbound ----
    async def send_text(self, peer, text, want_ack=True):
        """Send an APRS message to peer. Returns the stored Message."""
        peer = aprs.normalize_call(peer)
        msgno = self._msgno.next() if want_ack else None
        info = aprs.format_message(peer, text, msgno)
        status = ST_SENDING if want_ack else ST_SENT
        m = self.store.add("out", peer, aprs.sanitize_text(text), msgno,
                           status, self.path_label, info)
        await self.transport.send_info(info)
        return m

    # ---- inbound (called by the transport RX loop) ----
    async def on_frame(self, src, info):
        """Handle one decoded APRS packet. src is the AX.25/TNC2 source
        callsign; info is the information field. Returns the stored Message
        for a message/ack we cared about, else None."""
        parsed = aprs.parse_message(info)
        kind = parsed["kind"]
        if kind is None:
            return None                      # not a message (position/status)

        if kind == "ack":
            if self._for_us(parsed["addressee"]):
                m = self.store.find_outgoing(src, parsed["msgno"])
                if m and m.status in (ST_SENDING, ST_SENT, ST_FAILED):
                    m.status = ST_ACKED
                return m
            return None

        if kind == "reject":
            if self._for_us(parsed["addressee"]):
                m = self.store.find_outgoing(src, parsed["msgno"])
                if m:
                    m.status = ST_FAILED
                return m
            return None

        # kind == "message"
        if not self._for_us(parsed["addressee"]):
            return None                      # addressed to someone else
        m = self.store.add("in", src, parsed["text"], parsed["msgno"],
                           ST_RECEIVED, self.path_label, info)
        # auto-ack if the sender asked for one
        if parsed["msgno"]:
            ack = aprs.format_ack(src, parsed["msgno"])
            await self.transport.send_info(ack)
        return m

    # ---- retries (driven by the session loop) ----
    def due_retries(self, now=None):
        """Return outgoing messages awaiting ack whose retry_interval has
        elapsed. Messages past max_retries are marked failed (and not
        returned). The caller resends each returned message's .info and then
        calls mark_retried(m)."""
        now = self.clock() if now is None else now
        due = []
        for m in self.store._messages:
            if m.direction != "out" or m.status != ST_SENDING:
                continue
            if now - m.ts < self.retry_interval * (m.retries + 1):
                continue
            if m.retries >= self.max_retries:
                m.status = ST_FAILED
                continue
            due.append(m)
        return due

    async def resend_due(self, now=None):
        """Convenience: resend everything due and mark it. Returns count."""
        due = self.due_retries(now)
        for m in due:
            await self.transport.send_info(m.info)
            m.retries += 1
        return len(due)
