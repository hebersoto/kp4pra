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

which are rewritten to UZ7HO tokens, e.g. "<SABM C P>", "<UA R F>",
"<RR R F R2>", "<I C P S0 R0>", "<UI C>", "<UNKN C P>" (UZ7HO shows XID,
TEST and anything unrecognised as UNKN).
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

# Frame types UZ7HO names itself; anything else is shown as UNKN.
_UZ7HO_TYPES = {"I", "RR", "RNR", "REJ", "SREJ", "SABM", "SABME", "DISC",
                "DM", "UA", "FRMR", "UI"}


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
    def short(self) -> str:
        """Traffic row: CALL-SSID <TOKEN>"""
        return "%s <%s>" % (self.src, self.token)

    @property
    def route(self) -> str:
        return "%s>%s" % (self.src, self.dst)


def uz7ho_token(desc_type: str, cr: Optional[str], pf: Optional[str],
                ns: Optional[str], nr: Optional[str]) -> str:
    """Build the UZ7HO-style token from ax25_frame_type() fields."""
    ftype = desc_type if desc_type in _UZ7HO_TYPES else "UNKN"
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
    if ftype == "I":
        if ns is not None:
            parts.append("S%s" % ns)
        if nr is not None:
            parts.append("R%s" % nr)
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
