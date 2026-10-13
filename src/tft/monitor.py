"""
KP4PRA TNC - turn Dire Wolf's packet log into UZ7HO Soundmodem style lines.

Source: Dire Wolf's own console output (journal of direwolf.service). It is
the only place that shows EVERY frame in both directions - received frames
AND our own transmissions, including connected-mode frames (SABM, UA, RR,
I ...). Dire Wolf's AGW monitor drops own-transmitted non-UI frames, which
would hide exactly the Winlink/connected traffic we want to show.

Dire Wolf line formats (src/direwolf.c, src/xmit.c, src/ax25_pad.c):

    received     [0.4 20:07:16] KP4CE-10>KP3M-10:(SABM cmd, p=1)
    transmitted  [0L 20:07:17] KP3M-10>KP4CE-10:(UA res, f=1)
    APRS (UI)    [0.3 20:08:15] N0CALL>APRS,WIDE1-1:!1824.17N/06603.90W-Hi

(the "HH:MM:SS" part is there because direwolf.service runs with -T; L/H
after the channel marks a transmission). Non-APRS frames carry an
ax25_frame_type() description in parentheses:

    I cmd, n(s)=0, n(r)=0, p=0, pid=0xf0     RR res, n(r)=2, f=1
    SABM cmd, p=1    UA res, f=1    DM res, f=1    DISC cmd, p=1
    UI cmd, p=0      XID cmd, p=1   TEST ...       U other???

which are rewritten to short monitor tokens (no Pid / Len, to keep lines
short on the 240 px screen):

    <UI C>   <I C [P] Rn Sn>   <RR|RNR|REJ|SREJ C|R [P|F] Rn>
    <SABM C P>  <SABME C P>  <DISC C P>  <UA R F>  <DM R F>  <FRMR R F>
    <XID C|R [P|F]>  <TEST C|R [P|F]>  <UNKN> (anything else)

Screen lines start with the callsigns: "KP3M-11>WP4DMD-2 <RR R F R3>".
UI frames leave out the destination (the TOCALL, e.g. APRS/APDW17) and show
the frame text instead, with runs of spaces collapsed and GPS position data
(coordinates, symbol, course/speed, altitude, raw NMEA) removed:
"NP4JM-10 <UI C> Hello from Ponce".
"""

import dataclasses
import re
import time
from typing import Optional

TX = "TX"
RX = "RX"

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_LINE = re.compile(
    r"^\[(?P<chan>\d+)(?P<kind>[.\d]*|[LH])(?:\s+(?P<ts>[0-9:]{5,8}))?\]\s+"
    r"(?P<src>[A-Z0-9]{1,6}(?:-\d{1,2})?)\*?>(?P<dst>[A-Z0-9]{1,6}(?:-\d{1,2})?)"
    r"(?P<via>(?:,[^:]*)?):(?P<rest>.*)$")
_DESC = re.compile(
    r"^\((?P<type>U other\?\?\?|[A-Z]+)"
    r"(?:\s+(?P<cr>cmd|res|cc=11|cc=00))?"
    r"(?:,\s*n\(s\)=(?P<ns>\d+))?"
    r"(?:,\s*n\(r\)=(?P<nr>\d+))?"
    r"(?:,\s*(?P<pfn>p|f|p/f)=(?P<pf>\d))?"
    r"[^)]*\)\s?(?P<info>.*)$")

# Frame types shown by name; anything else is shown as UNKN.
_TYPES = {"I", "RR", "RNR", "REJ", "SREJ", "SABM", "SABME", "DISC",
          "DM", "UA", "FRMR", "UI", "XID", "TEST"}


@dataclasses.dataclass
class Frame:
    direction: str          # TX or RX
    src: str
    dst: str
    token: str              # UZ7HO token without <>, e.g. "SABM C P"
    info: str = ""
    ts: str = ""            # HH:MM:SS from Dire Wolf, or local time
    aprs: bool = False

    @property
    def is_ui(self) -> bool:
        return self.token.startswith("UI")

    @property
    def route(self) -> str:
        """FROM>TO, or just FROM for UI frames (the TO is only a TOCALL)."""
        return self.src if self.is_ui else "%s>%s" % (self.src, self.dst)

    @property
    def text(self) -> str:
        """UI frame text without position data (empty for other frames)."""
        return ui_text(self.info) if self.is_ui else ""

    @property
    def short(self) -> str:
        """Traffic row: FROM>TO <TOKEN>  or  FROM <UI C> text"""
        line = "%s <%s>" % (self.route, self.token)
        t = self.text
        return "%s %s" % (line, t) if t else line


def uz7ho_token(desc_type: str, cr: Optional[str], pf: Optional[str],
                ns: Optional[str], nr: Optional[str]) -> str:
    """Build the UZ7HO-style token from ax25_frame_type() fields."""
    ftype = desc_type if desc_type in _TYPES else "UNKN"
    parts = [ftype]
    if cr == "cmd":
        parts.append("C")
    elif cr == "res":
        parts.append("R")
    if pf == "1":
        if cr == "cmd":
            parts.append("P")
        elif cr == "res":
            parts.append("F")
    if ftype == "I":                       # <I C [P] Rn Sn>
        if nr is not None:
            parts.append("R%s" % nr)
        if ns is not None:
            parts.append("S%s" % ns)
    elif nr is not None and ftype in ("RR", "RNR", "REJ", "SREJ"):
        parts.append("R%s" % nr)
    return " ".join(parts)


def parse_line(line: str) -> Optional[Frame]:
    """Parse one Dire Wolf log line; None if it is not a frame line."""
    line = _ANSI.sub("", line).strip()
    m = _LINE.match(line)
    if not m:
        return None
    direction = TX if m.group("kind") in ("L", "H") else RX
    ts = m.group("ts") or time.strftime("%H:%M:%S")
    rest = m.group("rest")
    d = _DESC.match(rest) if rest.startswith("(") else None
    if d:
        token = uz7ho_token(d.group("type"), d.group("cr"), d.group("pf"),
                            d.group("ns"), d.group("nr"))
        return Frame(direction, m.group("src"), m.group("dst"), token,
                     d.group("info").strip(), ts, aprs=False)
    # No description: an APRS frame (UI, pid F0, always a command).
    return Frame(direction, m.group("src"), m.group("dst"), "UI C",
                 rest.strip(), ts, aprs=True)


# ------------------------------------------------- UI text (no GPS data) ----
_UNCOMP_POS = re.compile(r"^[0-9 ]{4}\.[0-9 ]{2}[NS].[0-9 ]{5}\.[0-9 ]{2}[EW].")
_COMP_POS = re.compile(r"^[/\\A-Za-j][!-{]{8}.[!-{ ]{2}.")
_CSE_SPD = re.compile(r"^\d{3}/\d{3}")
_ALT = re.compile(r"/A=-?\d{5,6}")
_DAO = re.compile(r"![\w!\"#$%&'()*+,\-./:;<=>?@\[\]^_`{|}~]{3}!$")


def _strip_position(s: str) -> str:
    if _UNCOMP_POS.match(s):
        s = s[19:]
    elif _COMP_POS.match(s):
        s = s[13:]
    s = _CSE_SPD.sub("", s, count=1)
    s = _ALT.sub("", s)
    return _DAO.sub("", s)


def ui_text(info: str) -> str:
    """APRS/UI info field -> readable text without GPS location data."""
    s = (info or "").strip()
    if not s:
        return ""
    dti, body = s[0], s[1:]
    if dti in "!=":                                   # position, no timestamp
        s = _strip_position(body)
    elif dti in "/@":                                 # position with timestamp
        s = _strip_position(body[7:])
    elif dti == ";" and len(body) >= 17:              # object: name*DDHHMMz pos
        s = body[:9].strip() + " " + _strip_position(body[17:])
    elif dti == ")":                                  # item: name!pos
        m = re.match(r"^([^!_]{3,9})[!_](.*)$", body)
        s = (m.group(1) + " " + _strip_position(m.group(2))) if m else body
    elif dti in "'`\x1c\x1d":                       # Mic-E: 8 encoded bytes
        s = body[8:]
        s = re.sub(r"^[>\]`'^]", "", s)                # Mic-E type byte
        s = re.sub(r"^.{3}\}", "", s)                  # Mic-E altitude
    elif dti == "$":                                  # raw NMEA from a GPS
        return ""
    elif dti == ":" and len(body) >= 10 and body[9] == ":":   # message
        text = re.sub(r"\{[A-Za-z0-9]{1,5}\}?$", "", body[10:])
        s = "%s: %s" % (body[:9].strip(), text)
    elif dti == ">":                                  # status
        s = body
    return " ".join(s.split())
