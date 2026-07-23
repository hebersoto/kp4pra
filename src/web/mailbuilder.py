"""
KP4PRA TNC - Winlink message builder (Phase 4, step 1).

Turns a held/approved queue record into a Winlink-format plain-text
message ready for a B2F proposal. Pure logic: no I/O, nothing transmitted.

Design decisions (from project review):
  * From:  <station-callsign>@winlink.org  (SSID stripped, uppercased).
    Not a reply address -- the recipient's replies go to Reply-To.
  * Reply-To: the user's personal address (from the public form).
  * Body wrapped to <=78 columns via mailvalidate.wrap_body.
  * If the station callsign is not configured we REFUSE to build (the
    caller marks the message Failed with a clear error) rather than emit
    a malformed From.

The Winlink "message" is an RFC822-ish text block. The MID (message id)
is the Winlink message identifier used in the B2F proposal line; it is a
distinct concept from the queue's on-disk id, so we generate a Winlink
MID here.
"""

import random
import string
import time

import mailvalidate


class BuildError(Exception):
    pass


_MID_ALPHABET = string.ascii_uppercase + string.digits


def base_callsign(call: str) -> str:
    """Uppercase and strip any SSID: 'kp3m-10' -> 'KP3M'."""
    return (call or "").upper().strip().split("-", 1)[0]


def station_from_address(cfg: dict) -> str:
    """Return '<BASECALL>@winlink.org' or raise BuildError if unset."""
    call = base_callsign(cfg.get("station", {}).get("callsign", ""))
    if not call or call == "N0CALL":
        raise BuildError("station callsign not configured")
    return call + "@winlink.org"


def generate_mid() -> str:
    """Winlink message identifiers are 12 uppercase alphanumeric chars."""
    return "".join(random.choice(_MID_ALPHABET) for _ in range(12))


def _winlink_date(ts: int = None) -> str:
    # Winlink uses UTC 'YYYY/MM/DD HH:MM' in the Date header of the
    # message block.
    return time.strftime("%Y/%m/%d %H:%M", time.gmtime(ts if ts else time.time()))


def build_message(rec: dict, cfg: dict, mid: str = None) -> dict:
    """Build a Winlink message from a queue record.

    Returns a dict:
      {
        "mid": <12-char Winlink MID>,
        "from": "<CALL>@winlink.org",
        "to": "<destination>",
        "reply_to": "<user address>",
        "subject": "<subject>",
        "date": "YYYY/MM/DD HH:MM",
        "body": "<wrapped plain-text body>",
        "text": "<full RFC822-ish message block>",
        "size": <len(text bytes, utf-8)>,
      }

    Raises BuildError if the message cannot be built (e.g. no station
    callsign, missing destination).
    """
    from_addr = station_from_address(cfg)

    to_addr = (rec.get("to") or "").strip()
    if not to_addr:
        raise BuildError("destination address missing")
    reply_to = (rec.get("reply_to") or "").strip()

    subject = (rec.get("subject") or "").replace("\n", " ").strip()
    body = mailvalidate.wrap_body(rec.get("body") or "")
    mid = mid or generate_mid()
    date = _winlink_date(rec.get("created"))

    # RFC822-style header block followed by the wrapped body. Winlink
    # message bodies are plain text; headers use CRLF line endings.
    headers = [
        ("Mid", mid),
        ("Date", date),
        ("From", from_addr),
        ("To", to_addr),
    ]
    if reply_to:
        headers.append(("Reply-To", reply_to))
    if subject:
        headers.append(("Subject", subject))
    headers.append(("Mbo", from_addr.split("@", 1)[0]))
    headers.append(("Body", str(len(body.encode("utf-8")))))

    header_text = "\r\n".join("%s: %s" % (k, v) for k, v in headers)
    text = header_text + "\r\n\r\n" + body.replace("\n", "\r\n") + "\r\n"

    return {
        "mid": mid,
        "from": from_addr,
        "to": to_addr,
        "reply_to": reply_to,
        "subject": subject,
        "date": date,
        "body": body,
        "text": text,
        "size": len(text.encode("utf-8")),
    }


def proposal_line(msg: dict, compressed_size: int) -> str:
    """Build the B2F proposal line for one message (FC record).

    Winlink B2 proposal format:  FC EM <MID> <uncompressed> <compressed> 0
    (EM = encapsulated message). Returned WITHOUT trailing CR so the
    sender can batch multiple proposals.
    """
    return "FC EM %s %d %d 0" % (msg["mid"], msg["size"], int(compressed_size))
