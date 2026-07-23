"""
KP4PRA TNC - B2F (Winlink forwarding protocol) client, step 3: assembly
and DRY-RUN only.

This module builds every piece of the outbound B2F exchange a Winlink
client performs when *originating* a message, and can produce a complete
DRY-RUN transcript for one queued message WITHOUT opening a socket or
keying a transmitter. The pure assembly functions (SID, proposal block +
checksum, FS parsing, binary framing) are unit-tested; the live
conversation driver is added in step 4 and will reuse these same
functions plus the existing rms/cms.py transport.

Confidence notes (honest):
  * Message build + LZHUF compression + FC proposal line + F> proposal
    checksum + FS response parsing: high confidence, matches the B2F/FBB
    spec and is round-trip tested here.
  * The exact SOH header content and the EOT checksum SCOPE of the binary
    transfer are confirmed against the live CMS during the step-4 first
    send (the real oracle). The dry-run logs these explicitly and
    separately so any correction is surgical. Nothing is transmitted, so
    an imperfect frame here costs only an iteration, never a bad emission.
"""

import time

import lzhuf
import mailbuilder

# B2 binary transfer control bytes.
SOH = 0x01
STX = 0x02
EOT = 0x04

# Our advertised capabilities: B=basic ASCII, 2=B2 binary, F=FBB (LZHUF),
# H, M identifiers; trailing $ = FBB checksum support.
CAPABILITIES = "B2FHM$"


def client_sid(version: str = "1.0") -> str:
    """Our system identification line, e.g. [KP4PRA-1.4.4-B2FHM$]."""
    return "[KP4PRA-%s-%s]" % (version, CAPABILITIES)


def parse_sid(line: str) -> dict:
    """Parse a peer SID line like [WL2K-5.0-B2FWIHJM$] -> capabilities."""
    line = (line or "").strip()
    info = {"raw": line, "name": "", "version": "", "caps": "",
            "fbb": False, "b2": False, "checksum": False}
    if line.startswith("[") and line.endswith("]"):
        inner = line[1:-1]
        parts = inner.rsplit("-", 2)
        if len(parts) == 3:
            info["name"], info["version"], info["caps"] = parts
        elif parts:
            info["name"] = parts[0]
        caps = info["caps"].upper()
        info["fbb"] = "F" in caps
        info["b2"] = "2" in caps
        info["checksum"] = "$" in info["caps"]
    return info


def proposal_checksum(block: bytes) -> int:
    """Two's-complement checksum (mod 256) of the proposal block bytes,
    reported after F> as two hex digits."""
    return (-sum(block)) & 0xFF


def build_proposals(items) -> dict:
    """Build the FC proposal block for a list of (msg, compressed_size).

    Returns {"lines": [..], "block": <bytes>, "checksum": int,
             "text": <str incl. F> line>}.
    """
    lines = []
    for msg, csize in items:
        lines.append(mailbuilder.proposal_line(msg, csize))
    block = ("\r".join(lines) + "\r").encode("ascii")
    cksum = proposal_checksum(block)
    text = "\r".join(lines) + "\r" + "F> %02X\r" % cksum
    return {"lines": lines, "block": block, "checksum": cksum, "text": text}


def parse_fs(line: str) -> list:
    """Parse an FS response line into a per-proposal disposition list.

    FS <chars> where each char applies to the corresponding proposal:
      Y = send, N/L/R = do not send (rejected/already have),
      H/= = hold/defer, ! = send from offset (resume).
    """
    line = (line or "").strip()
    if not line.upper().startswith("FS"):
        return []
    payload = line[2:].strip()
    out = []
    for ch in payload:
        u = ch.upper()
        if u == "Y":
            out.append("send")
        elif u in ("N", "L", "R"):
            out.append("skip")
        elif u in ("H", "="):
            out.append("hold")
        elif u == "!":
            out.append("resume")
        else:
            out.append("unknown")
    return out


def frame_binary(mid: str, compressed: bytes) -> dict:
    """Frame one compressed message body for B2 binary transfer.

    Layout (STX data framing + EOT checksum are spec-confident; the SOH
    header content is best-effort and confirmed live in step 4):
      SOH <len> <header bytes>
      STX <len> <data chunk>   (repeated; chunk <= 250 bytes)
      ...
      EOT <checksum>           (2's complement of the compressed bytes)
    """
    header = mid.encode("ascii") + b"\x00" + b"0"   # title, NUL, offset "0"
    out = bytearray()
    out.append(SOH)
    out.append(len(header) & 0xFF)
    out += header
    i = 0
    n_data = 0
    while i < len(compressed):
        chunk = compressed[i:i + 250]
        out.append(STX)
        out.append(len(chunk) & 0xFF)
        out += chunk
        i += len(chunk)
        n_data += 1
    checksum = (-sum(compressed)) & 0xFF
    out.append(EOT)
    out.append(checksum)
    return {"bytes": bytes(out), "checksum": checksum,
            "data_frames": n_data, "header_len": len(header)}


def unframe_binary(framed: bytes) -> bytes:
    """Inverse of frame_binary (for self-consistency tests): recover the
    compressed payload from SOH/STX/EOT framing."""
    out = bytearray()
    i = 0
    n = len(framed)
    while i < n:
        c = framed[i]
        i += 1
        if c == SOH:
            ln = framed[i]
            i += 1 + ln
        elif c == STX:
            ln = framed[i]
            i += 1
            out += framed[i:i + ln]
            i += ln
        elif c == EOT:
            i += 1  # checksum byte
            break
        else:
            raise ValueError("bad control byte 0x%02X at %d" % (c, i - 1))
    return bytes(out)


def hex_preview(data: bytes, limit: int = 64) -> str:
    show = data[:limit]
    s = " ".join("%02X" % b for b in show)
    if len(data) > limit:
        s += " ... (%d bytes total)" % len(data)
    return s


def dry_run_cms(record: dict, cfg: dict, version: str = "1.0") -> dict:
    """Assemble the full CMS-path B2F exchange for one message and return
    a DRY-RUN transcript. Opens no socket; transmits nothing.

    Raises mailbuilder.BuildError if the message cannot be built (e.g. the
    station callsign is not configured) so the caller can surface it.
    """
    msg = mailbuilder.build_message(record, cfg)
    raw = msg["text"].encode("utf-8")
    compressed = lzhuf.compress(raw)
    msg_cksum = lzhuf.compute_checksum(raw)

    prop = build_proposals([(msg, len(compressed))])
    frame = frame_binary(msg["mid"], compressed)

    our_sid = client_sid(version)
    # Simulated CMS side (illustrative; the real values come from the live
    # server in step 4). Marked clearly as simulated in the transcript.
    cms_sid = "[WL2K-5.0-B2FWIHJM$]"
    cms_caps = parse_sid(cms_sid)

    transcript = [
        ("info", "DRY RUN - no socket opened, nothing transmitted"),
        ("<--", "(CMS, simulated) %s" % cms_sid),
        ("<--", "(CMS, simulated) ;PR: prompt >"),
        ("-->", our_sid),
        ("-->", "; %d proposal(s)" % 1),
    ]
    for ln in prop["lines"]:
        transcript.append(("-->", ln))
    transcript.append(("-->", "F> %02X" % prop["checksum"]))
    transcript.append(("<--", "(CMS, simulated) FS Y"))
    transcript.append(("-->", "<binary block: %d bytes, %d data frame(s)>"
                       % (len(frame["bytes"]), frame["data_frames"])))
    transcript.append(("-->", "  SOH/STX*%d/EOT  EOT-checksum=%02X"
                       % (frame["data_frames"], frame["checksum"])))
    transcript.append(("<--", "(CMS, simulated) FF"))
    transcript.append(("-->", "FQ  (no more to send)"))

    return {
        "dry_run": True,
        "method": "cms",
        "at": time.strftime("%Y-%m-%d %H:%M:%SZ", time.gmtime()),
        "version": version,
        "from": msg["from"],
        "to": msg["to"],
        "reply_to": msg["reply_to"],
        "mid": msg["mid"],
        "uncompressed_size": msg["size"],
        "compressed_size": len(compressed),
        "message_checksum": "%02X" % msg_cksum,
        "our_sid": our_sid,
        "cms_sid_simulated": cms_sid,
        "cms_supports_fbb": cms_caps["fbb"],
        "proposal_text": prop["text"].replace("\r", "\n"),
        "proposal_checksum": "%02X" % prop["checksum"],
        "binary_total_bytes": len(frame["bytes"]),
        "binary_data_frames": frame["data_frames"],
        "binary_hex_preview": hex_preview(frame["bytes"]),
        "message_headers_preview": msg["text"].replace("\r\n", "\n"),
        "transcript": transcript,
        "notes": [
            "No socket was opened and nothing was transmitted.",
            "CMS responses shown are simulated; real values arrive in the "
            "live send (step 4).",
            "SOH header content and EOT checksum scope are confirmed "
            "against the live CMS during the first real send.",
        ],
    }
