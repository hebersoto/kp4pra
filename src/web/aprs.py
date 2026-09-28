"""APRS text-messaging format, acknowledgment handling, and APRS-IS helpers.

Pure logic, no I/O -- the APRS counterpart of b2f.py. Covers only what an
APRS *messaging* (text chat) client needs:

  * Encode/parse the APRS message info field  :ADDRESSEE:text{msgNo
  * Acknowledgments (ackNNNNN) and rejects (rejNNNNN)
  * A monotonic message-number generator for outgoing-ack tracking
  * The APRS-IS login passcode derived from a callsign
  * TNC2 monitor-line build/parse for the APRS-IS (internet) path

References: APRS Protocol Reference 1.0.1, chapter 14 (Message Format).
The AX.25 UI-frame wrapping for the RF path lives in the session manager
(aprschat.py) and reuses rms/ax25.py -- this module only produces the
information field that goes inside such a frame, or the TNC2 line for
APRS-IS.

Field rules enforced here:
  - Addressee: 9 characters, left-justified, space-padded (callsign[-SSID]).
  - Message text: <= 67 characters; the characters | ~ { are not allowed in
    text and are stripped/replaced before sending.
  - Message number: 1-5 characters (we generate numeric ids). Present only
    when the sender wants an acknowledgment.
"""

import re

DTI = ":"                      # message data type identifier
ADDRESSEE_LEN = 9
MAX_TEXT = 67
MSGNO_MAX_LEN = 5

# Characters that may not appear in APRS message text.
_FORBIDDEN_TEXT = ("|", "~", "{")

# A callsign is 1-6 alphanumerics with an optional -SSID (0-15, or an
# alphanumeric tactical SSID up to a total of 9 characters for the addressee).
_CALL_RE = re.compile(r"^[A-Z0-9]{1,6}(-[A-Z0-9]{1,3})?$")


def normalize_call(call: str) -> str:
    """Upper-case and trim a callsign; no validation (see valid_call)."""
    return (call or "").strip().upper()


def valid_call(call: str) -> bool:
    c = normalize_call(call)
    return bool(_CALL_RE.match(c)) and len(c) <= ADDRESSEE_LEN


def format_addressee(call: str) -> str:
    """9-char, left-justified, space-padded addressee field."""
    c = normalize_call(call)[:ADDRESSEE_LEN]
    return c.ljust(ADDRESSEE_LEN)


def sanitize_text(text: str) -> str:
    """Remove characters not permitted in APRS message text and clamp
    to MAX_TEXT. Newlines/CR are dropped (APRS messages are single line)."""
    t = (text or "").replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    for ch in _FORBIDDEN_TEXT:
        t = t.replace(ch, "")
    return t[:MAX_TEXT]


def format_message(addressee: str, text: str, msgno=None) -> str:
    """Build an APRS message information field.

      :ADDRESSEE:message text{msgNo

    msgno is optional; include it (a short id) when an ack is wanted.
    Returns a str (ASCII); the caller encodes/wraps it for the transport.
    """
    body = sanitize_text(text)
    field = "%s%s:%s" % (DTI, format_addressee(addressee), body)
    if msgno not in (None, ""):
        m = str(msgno)[:MSGNO_MAX_LEN]
        field += "{" + m
    return field


def format_ack(addressee: str, msgno) -> str:
    """Build an acknowledgment for a received message: :ADDR     :ackNNNNN
    (addressee is the ORIGINAL SENDER of the message being acked)."""
    return "%s%s:ack%s" % (DTI, format_addressee(addressee),
                           str(msgno)[:MSGNO_MAX_LEN])


def format_reject(addressee: str, msgno) -> str:
    return "%s%s:rej%s" % (DTI, format_addressee(addressee),
                           str(msgno)[:MSGNO_MAX_LEN])


# Parse an info field: :ADDRESSEE:text[{msgno]
#   addressee is exactly 9 chars then a ':'.
_MSG_RE = re.compile(r"^:(.{9}):(.*)$", re.S)
_ACK_RE = re.compile(r"^ack([A-Za-z0-9]{1,5})\}?\s*$")
_REJ_RE = re.compile(r"^rej([A-Za-z0-9]{1,5})\}?\s*$")


def parse_message(info: str) -> dict:
    """Parse an APRS message information field.

    Returns a dict:
      {"kind": "message"|"ack"|"reject"|None,
       "addressee": <trimmed callsign the msg is TO>,
       "text": <message text, msgno stripped>,
       "msgno": <str or None>}

    'kind' is None when the field is not an APRS message (e.g. position,
    status). The SENDER of the message is NOT in the field; the caller
    supplies it from the AX.25/TNC2 source address.
    """
    out = {"kind": None, "addressee": "", "text": "", "msgno": None}
    if not info or info[0] != DTI:
        return out
    m = _MSG_RE.match(info)
    if not m:
        return out
    addressee = m.group(1).strip().upper()
    rest = m.group(2)
    out["addressee"] = addressee

    # ack / reject?
    a = _ACK_RE.match(rest)
    if a:
        out["kind"] = "ack"
        out["msgno"] = a.group(1)
        return out
    r = _REJ_RE.match(rest)
    if r:
        out["kind"] = "reject"
        out["msgno"] = r.group(1)
        return out

    # plain message, optionally with a trailing {msgno
    out["kind"] = "message"
    if "{" in rest:
        body, _, msgno = rest.rpartition("{")
        # msgno is 1-5 chars; anything longer means the { was literal text
        msgno = msgno.strip()
        if 1 <= len(msgno) <= MSGNO_MAX_LEN and re.match(r"^[A-Za-z0-9]+$", msgno):
            out["text"] = body
            out["msgno"] = msgno
            return out
    out["text"] = rest
    return out


class MsgNoGen:
    """Monotonic outgoing message-number generator. APRS message numbers are
    1-5 chars; we cycle 1..99999 as decimal strings (compact, human-legible
    in logs). Persist `value` if you want ids to survive a restart."""

    def __init__(self, start: int = 1):
        self.value = max(1, int(start)) % 100000

    def next(self) -> str:
        n = self.value
        self.value = (self.value % 99999) + 1
        return str(n)


# --------------------------------------------------------------------------
# APRS-IS
# --------------------------------------------------------------------------
def passcode(callsign: str) -> int:
    """APRS-IS login passcode for a callsign (SSID ignored). Standard
    algorithm: seed 0x73E2, XOR successive character pairs (hi<<8, lo),
    masked to 15 bits."""
    call = normalize_call(callsign).split("-")[0]
    code = 0x73E2
    i = 0
    while i < len(call):
        code ^= (ord(call[i]) << 8)
        if i + 1 < len(call):
            code ^= ord(call[i + 1])
        i += 2
    return code & 0x7FFF


def aprsis_login(callsign: str, appname: str = "KP4PRA",
                 version: str = "1.0", filt: str = None) -> str:
    """APRS-IS login line (no trailing CRLF). If filt is given, a
    server-side filter is appended (e.g. 'g/KP3M*' or 'm/50')."""
    line = "user %s pass %d vers %s %s" % (
        normalize_call(callsign), passcode(callsign), appname, version)
    if filt:
        line += " filter %s" % filt
    return line


def tnc2_line(source: str, tocall: str, info: str, path=None) -> str:
    """Build a TNC2 monitor line for APRS-IS transmit:
        SOURCE>TOCALL,PATH:info
    For APRS-IS the conventional path element is TCPIP* -- callers usually
    pass path=["TCPIP*"]."""
    hdr = "%s>%s" % (normalize_call(source), normalize_call(tocall))
    for p in (path or []):
        hdr += "," + p
    return "%s:%s" % (hdr, info)


_TNC2_RE = re.compile(r"^([^>]+)>([^,:]+)((?:,[^:]+)*):(.*)$", re.S)


def parse_tnc2(line: str) -> dict:
    """Parse a TNC2 monitor line (an APRS-IS feed line) into
    {source, dest, path:[...], info}. Returns None if it isn't TNC2
    (e.g. an APRS-IS server comment beginning with '#')."""
    if not line or line.startswith("#"):
        return None
    m = _TNC2_RE.match(line.rstrip("\r\n"))
    if not m:
        return None
    path = [p for p in m.group(3).split(",") if p]
    return {"source": m.group(1).strip().upper(),
            "dest": m.group(2).strip().upper(),
            "path": path,
            "info": m.group(4)}


# --------------------------------------------------------------------------
# Position / beacon
# --------------------------------------------------------------------------
def maidenhead_to_latlon(grid: str):
    """Center lat/lon (decimal degrees) of a 4- or 6-char Maidenhead locator.
    Returns (lat, lon) or None if the grid is malformed."""
    g = (grid or "").strip()
    if len(g) < 4:
        return None
    G = g.upper()
    try:
        A = ord("A")
        lon = (ord(G[0]) - A) * 20 - 180
        lat = (ord(G[1]) - A) * 10 - 90
        if not (G[2].isdigit() and G[3].isdigit()):
            return None
        lon += int(G[2]) * 2
        lat += int(G[3]) * 1
        if len(G) >= 6:
            lon += (ord(G[4]) - A) * (2.0 / 24) + (2.0 / 24) / 2   # + subsquare center
            lat += (ord(G[5]) - A) * (1.0 / 24) + (1.0 / 24) / 2
        else:
            lon += 1.0        # square center (half of 2 deg)
            lat += 0.5        # half of 1 deg
        if not (-90 <= lat <= 90 and -180 <= lon <= 180):
            return None
        return (round(lat, 5), round(lon, 5))
    except (IndexError, ValueError):
        return None


def format_lat(lat: float) -> str:
    """APRS latitude field: DDMM.mmN/S."""
    hemi = "N" if lat >= 0 else "S"
    a = abs(lat)
    d = int(a)
    m = (a - d) * 60
    return "%02d%05.2f%s" % (d, m, hemi)


def format_lon(lon: float) -> str:
    """APRS longitude field: DDDMM.mmE/W."""
    hemi = "E" if lon >= 0 else "W"
    a = abs(lon)
    d = int(a)
    m = (a - d) * 60
    return "%03d%05.2f%s" % (d, m, hemi)


def position_report(lat, lon, symbol_table="/", symbol_code="-",
                    comment="", messaging=True) -> str:
    """APRS position report info field (no timestamp):
        =DDMM.mmN<sym_table>DDDMM.mmW<sym_code><comment>
    '=' advertises messaging capability; '!' is position-only. symbol_table is
    '/' (primary), '\\' (alternate) or an overlay char; symbol_code selects the
    icon (e.g. '-' house, '>' car, '&' gateway, '#' digipeater)."""
    dti = "=" if messaging else "!"
    st = (symbol_table or "/")[0]
    sc = (symbol_code or "-")[0]
    cmt = (comment or "").replace("\r", " ").replace("\n", " ")[:43]
    return "%s%s%s%s%s%s" % (dti, format_lat(lat), st, format_lon(lon), sc, cmt)
