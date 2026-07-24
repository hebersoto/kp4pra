"""
KP4PRA TNC - Live CMS B2F send driver (Phase 4, step 4).

Originates a queued message to Winlink over CMS Telnet, building on the
verified b2f.py assembly and the existing rms/cms.py transport. Two
capabilities:

  probe(record, cfg, identity)
      Connect -> login -> SID exchange -> send FC proposal -> read the
      REAL FS response -> abort (FQ) BEFORE sending any message body.
      Validates login, our SID, and proposal acceptance without
      committing a message. Safe first contact.

  send(record, cfg, identity)
      Full delivery: proposal, and if FS says Y, transmit the binary
      block, read FF, send FQ, confirm. Updates queue status
      Approved -> Sending -> Sent/Failed. Records errors; never
      auto-deletes; refuses to re-send an already-Sent message.

SAFETY: both probe and send open a real socket, so both refuse unless
webmail.delivery.dry_run is false. The dry-run (b2f.dry_run_cms) is the
no-socket path and lives in b2f.py.

Identity: originating KP3M's own mail, the CMS secure login sends
"<remote_call> <gateway_call>". Two candidates:
  identity "station" -> remote=KP3M,        gateway=KP3M
  identity "gateway" -> remote=KP3M,        gateway=rms.cms_call
probe_both() runs both so CMS tells us which it accepts.
"""

import asyncio
import time

import b2f
import lzhuf
import mailbuilder
import mailqueue

from rms.cms import CmsSession, challenge_response

MODE_CODES = {"PACKET-1200": 0, "PACKET-9600": 3}
IO_TIMEOUT = 30          # seconds for any single CMS read
OVERALL_TIMEOUT = 120    # seconds for a whole probe/send


class DeliveryError(Exception):
    pass


def _dry_run(cfg) -> bool:
    return bool(cfg.get("webmail", {}).get("delivery", {}).get("dry_run", True))


def _identities(cfg):
    """Return {name: (remote_call, gateway_call)} candidates."""
    station = mailbuilder.base_callsign(cfg.get("station", {}).get("callsign", ""))
    gateway = (cfg.get("rms", {}).get("cms_call", "") or station).upper()
    return {
        "station": (station, station),
        "gateway": (station, gateway),
    }


def _make_session(cfg, remote_call, gateway_call):
    rms = cfg.get("rms", {})
    return CmsSession(
        remote_call, gateway_call,
        rms.get("cms_password", ""),
        rms.get("frequency_hz", 0),
        MODE_CODES.get(rms.get("mode", "PACKET-1200"), 0),
        rms.get("cms_host", "cms.winlink.org"),
        int(rms.get("cms_port", 8772)),
    )


async def _read_until(cms, needle, log, cap=8192):
    """Read from CMS until `needle` (bytes) appears or timeout. Returns
    the accumulated bytes. Logs each chunk."""
    buf = bytearray()
    deadline = time.time() + IO_TIMEOUT
    while needle not in buf and len(buf) < cap:
        remaining = deadline - time.time()
        if remaining <= 0:
            raise DeliveryError("timeout waiting for %r" % needle)
        chunk = await asyncio.wait_for(cms.recv(512), timeout=remaining)
        if not chunk:
            raise DeliveryError("CMS closed the connection")
        buf.extend(chunk)
        log(("<--", _short(chunk)))
    return bytes(buf)


def _short(data, limit=200):
    try:
        s = data.decode("latin1", "ignore")
    except Exception:
        s = repr(data)
    s = s.replace("\r", "\\r").replace("\n", "\\n")
    return s[:limit] + (" ..." if len(s) > limit else "")


async def _run_b2f(cms, msg, compressed, log, send_body, version="1.0",
                   password=""):
    """Drive the B2F conversation after login. Returns a result dict.
    If send_body is False, aborts with FQ after reading FS (probe)."""
    result = {"logged_in": True, "fs": None, "accepted": None,
              "delivered": False, "cms_banner": ""}

    # Read CMS SID / prompt (login already consumed the auth handshake).
    banner = await _read_until(cms, b">", log)
    result["cms_banner"] = _short(banner, 400)
    pq_challenge = None
    for line in banner.split(b"\r"):
        line = line.strip()
        if line.startswith(b"[") and b"]" in line:
            result["cms_sid"] = b2f.parse_sid(line.decode("latin1", "ignore"))
        elif line.startswith(b";PQ:"):
            pq_challenge = line.decode("latin1", "ignore")[4:].strip()

    # Client sends SID FIRST, THEN ;PR:, THEN proposals (per captured CMS
    # exchange NTSGW/AC0KQ and the B2F spec), as one burst.
    prop = b2f.build_proposals([(msg, len(compressed))])
    our_sid = b2f.client_sid(version)
    parts = [our_sid + "\r"]
    log(("-->", our_sid))
    if pq_challenge:
        response = challenge_response(pq_challenge, password)
        parts.append(";PR: %s\r" % response)
        log(("-->", ";PR: %s" % response))
    parts.append(prop["text"])
    for ln in prop["lines"]:
        log(("-->", ln))
    log(("-->", "F> %02X" % prop["checksum"]))
    await cms.send("".join(parts).encode("ascii"))

    # Read the real FS response.
    fs_buf = await _read_until(cms, b"\r", log)
    fs_line = ""
    for line in fs_buf.split(b"\r"):
        if line.strip().upper().startswith(b"FS"):
            fs_line = line.strip().decode("latin1", "ignore")
            break
    result["fs"] = fs_line
    dispositions = b2f.parse_fs(fs_line)
    result["accepted"] = (dispositions[:1] == ["send"])

    if not send_body:
        # PROBE: withdraw cleanly without sending a body.
        log(("-->", "FQ  (probe: aborting before body)"))
        try:
            await cms.send(b"FQ\r")
        except Exception:
            pass
        return result

    if not result["accepted"]:
        # CMS did not ask for the message; nothing to send.
        log(("info", "CMS did not accept the proposal (FS=%r)" % fs_line))
        try:
            await cms.send(b"FQ\r")
        except Exception:
            pass
        return result

    # SEND: transmit the binary block.
    frame = b2f.frame_binary(msg["subject"], compressed)
    log(("-->", "<binary block: %d bytes>" % len(frame["bytes"])))
    await cms.send(frame["bytes"])

    # Expect FF (CMS has nothing to send back), then we FQ.
    tail = await _read_until(cms, b"F", log)
    log(("-->", "FQ"))
    try:
        await cms.send(b"FQ\r")
    except Exception:
        pass
    result["delivered"] = True
    return result


async def _connect_and_run(record, cfg, remote_call, gateway_call,
                           send_body, version="1.0"):
    """Build the message, connect+login, run the B2F conversation."""
    transcript = []
    def log(entry):
        transcript.append(entry)

    msg = mailbuilder.build_message(record, cfg)
    compressed = lzhuf.compress(msg["text"].encode("utf-8"))

    log(("info", "connecting to CMS as remote=%s gateway=%s"
         % (remote_call, gateway_call)))
    cms = _make_session(cfg, remote_call, gateway_call)
    password = cfg.get("rms", {}).get("cms_password", "")
    try:
        await asyncio.wait_for(cms.connect(), timeout=IO_TIMEOUT)
        log(("info", "login OK"))
        res = await asyncio.wait_for(
            _run_b2f(cms, msg, compressed, log, send_body, version=version,
                     password=password),
            timeout=OVERALL_TIMEOUT)
    except (DeliveryError, asyncio.TimeoutError, OSError, ConnectionError) as e:
        log(("error", "%s: %s" % (type(e).__name__, e)))
        try:
            await cms.close()
        except Exception:
            pass
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e),
                "transcript": transcript, "mid": msg["mid"],
                "compressed_size": len(compressed),
                "uncompressed_size": msg["size"]}
    try:
        await cms.close()
    except Exception:
        pass
    res["ok"] = True
    res["transcript"] = transcript
    res["mid"] = msg["mid"]
    res["compressed_size"] = len(compressed)
    res["uncompressed_size"] = msg["size"]
    return res


# ── Public async API ─────────────────────────────────────────────────────────

async def probe(record, cfg, identity="station", version="1.0"):
    if _dry_run(cfg):
        raise DeliveryError("dry_run is enabled; disable it to allow a real "
                            "CMS connection")
    ids = _identities(cfg)
    if identity not in ids:
        raise DeliveryError("unknown identity %r" % identity)
    remote_call, gateway_call = ids[identity]
    res = await _connect_and_run(record, cfg, remote_call, gateway_call,
                                 send_body=False, version=version)
    res["identity"] = identity
    res["probe"] = True
    return res


async def probe_both(record, cfg, version="1.0"):
    if _dry_run(cfg):
        raise DeliveryError("dry_run is enabled; disable it to allow a real "
                            "CMS connection")
    out = {}
    for name in ("station", "gateway"):
        out[name] = await probe(record, cfg, identity=name, version=version)
    return out


async def send(record, cfg, identity="station", version="1.0"):
    if _dry_run(cfg):
        raise DeliveryError("dry_run is enabled; disable it to allow a real "
                            "CMS send")
    mid = record.get("id")
    if record.get("status") == "Sent":
        raise DeliveryError("message already Sent (duplicate-send guard)")

    ids = _identities(cfg)
    remote_call, gateway_call = ids.get(identity, ids["station"])

    mailqueue.set_status(mid, "Sending", route="cms:%s" % identity)
    res = await _connect_and_run(record, cfg, remote_call, gateway_call,
                                 send_body=True, version=version)
    if res.get("ok") and res.get("delivered"):
        mailqueue.set_status(mid, "Sent", route="cms:%s" % identity,
                             error=None)
        res["final_status"] = "Sent"
    else:
        err = res.get("error") or ("CMS did not accept (FS=%s)"
                                    % res.get("fs"))
        mailqueue.set_status(mid, "Failed", error=err)
        res["final_status"] = "Failed"
    res["identity"] = identity
    return res
