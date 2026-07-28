"""Outbound RF delivery of webmail to a remote Winlink RMS gateway.

This is the RF counterpart to b2fsend.py (which delivers over a TCP socket to
CMS). It drives the SAME B2F protocol from b2f.py, but the transport is AX.25
connected mode over a Dire Wolf KISS-TCP link instead of a raw TCP stream.

Design:
  * AX25Stream is a reliable byte-stream adapter over AX.25 connected mode. It
    presents the same async .send(bytes) / .recv(n) / .close() surface as
    rms.cms.CmsSession, so the b2f driver logic can be reused unchanged.
  * We are the INITIATOR: we send SABM to the remote RMS (optionally via up to
    two digipeaters), await UA, exchange numbered I-frames with go-back-N style
    ARQ (modulo-8 window), and tear the link down with DISC/UA.
  * Radio contention with the inbound RMS listener (kp4pra-tnc-rms.service,
    which owns the same Dire Wolf connection) is avoided by stopping that
    service for the duration of the send and restarting it afterward
    (mutually-exclusive modes). The listener is never interrupted mid-relay:
    we refuse to start if its runtime status shows an active session.

Only pure-stdlib + existing project modules are used (no third-party deps).
"""

import asyncio
import time

import b2f
import lzhuf
import mailbuilder
import mailqueue

from rms.ax25 import (
    kiss_encode, KissDecoder, make_frame, split_frame,
    is_i, is_s, is_ua, is_disc, is_sabm, ns, nr, rr, iframe, poll_bit,
    UA, DM, DISC,
)
try:
    from rms.ax25 import PID_NO_L3
except Exception:                       # pragma: no cover - constant fallback
    PID_NO_L3 = 0xF0

from rms.cms import challenge_response
from common.runtime_status import read_status

SABM = 0x2F                             # matches is_sabm: (c & 0xEF) == 0x2F
POLL = 0x10
WINDOW = 7                              # modulo-8 max outstanding I-frames


class RfError(Exception):
    pass


# --------------------------------------------------------------------------
# Reliable byte stream over AX.25 connected mode
# --------------------------------------------------------------------------
class AX25Stream:
    """Reliable, ordered byte stream over one AX.25 connected-mode link.

    Presents .send()/.recv()/.close() like rms.cms.CmsSession so the b2f
    driver can treat it exactly like the CMS socket. Internally it chunks
    outbound bytes into I-frames (<= block_size) with a modulo-8 send window
    and go-back-N retransmission, and reassembles in-sequence inbound I-frame
    payloads into a receive buffer.
    """

    def __init__(self, reader, writer, mycall, remote, digis, log,
                 block_size=250, ack_timeout=10, connect_retries=3):
        self._reader = reader
        self._writer = writer
        self.mycall = mycall
        self.remote = remote
        self.digis = list(digis or [])
        self.log = log
        self.block_size = max(1, min(255, int(block_size)))
        self.ack_timeout = ack_timeout
        self.connect_retries = connect_retries

        self.vs = 0        # next N(S) we will send
        self.vr = 0        # next N(S) we expect from peer
        self.va = 0        # oldest unacked N(S) (from peer N(R))
        self._unacked = {}  # N(S) -> payload bytes awaiting ack
        self._rxbuf = bytearray()
        self._dec = KissDecoder()
        self._closed = False

    # ---- low-level frame I/O ------------------------------------------
    async def _tx(self, ctrl, payload=b"", pid=-1, response=False):
        frame = make_frame(self.remote, self.mycall, ctrl, payload, pid,
                           response=response, digis=self.digis)
        self._writer.write(kiss_encode(frame))
        await self._writer.drain()

    async def _read_frame(self, timeout):
        """Return the next decoded AX.25 frame addressed to us, or None on
        timeout. KISS frames from Dire Wolf are decoded and filtered so we
        only act on frames whose source is our remote peer."""
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            try:
                data = await asyncio.wait_for(self._reader.read(1024),
                                              timeout=remaining)
            except asyncio.TimeoutError:
                return None
            if not data:
                raise RfError("Dire Wolf connection closed")
            for kf in self._dec.feed(data):
                try:
                    dest, src, digis, ctrl, pid, payload = split_frame(kf)
                except ValueError:
                    continue
                if src.upper() != self.remote.upper():
                    continue
                return (ctrl, pid, payload)

    # ---- link establishment / teardown --------------------------------
    async def connect(self):
        """Send SABM, await UA. Retries up to connect_retries."""
        for attempt in range(1, self.connect_retries + 1):
            self.log(("-->", "SABM to %s%s (try %d)" % (
                self.remote,
                (" via " + ",".join(self.digis)) if self.digis else "",
                attempt)))
            await self._tx(SABM | POLL)
            fr = await self._read_frame(self.ack_timeout)
            if fr is None:
                continue
            ctrl, _pid, _payload = fr
            if is_ua(ctrl):
                self.log(("<--", "UA (link established)"))
                self.vs = self.vr = self.va = 0
                return b""
            if (ctrl & 0xEF) == DM:
                raise RfError("remote refused connection (DM)")
        raise RfError("no UA after %d SABM attempts" % self.connect_retries)

    async def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            await self._flush_acks(quick=True)
            self.log(("-->", "DISC"))
            await self._tx(DISC | POLL)
            fr = await self._read_frame(self.ack_timeout)
            if fr and is_ua(fr[0]):
                self.log(("<--", "UA (link closed)"))
        except Exception:
            pass

    # ---- reliable send ------------------------------------------------
    async def send(self, data: bytes):
        """Queue bytes for reliable delivery as one or more I-frames, then
        drive the window until they are all acknowledged."""
        i = 0
        while i < len(data):
            chunk = data[i:i + self.block_size]
            i += len(chunk)
            await self._send_chunk(chunk)
        # ensure everything queued so far is acknowledged before returning
        await self._drain_window()

    async def _send_chunk(self, chunk):
        # Block while the send window is full, pumping incoming acks.
        while ((self.vs - self.va) & 7) >= WINDOW:
            if not await self._pump_once():
                await self._retransmit()
        ctrl = iframe(self.vs, self.vr)
        self._unacked[self.vs] = chunk
        self.log(("-->", "I N(S)=%d N(R)=%d len=%d" % (self.vs, self.vr, len(chunk))))
        await self._tx(ctrl, chunk, PID_NO_L3)
        self.vs = (self.vs + 1) & 7

    async def _drain_window(self):
        """Wait until all sent I-frames are acknowledged (va catches vs)."""
        tries = 0
        while self.va != self.vs:
            if not await self._pump_once():
                await self._retransmit()
                tries += 1
                if tries > self.connect_retries * (WINDOW + 1):
                    raise RfError("no ack for outstanding I-frames")

    async def _pump_once(self):
        """Read one frame; process ack/RR/incoming I-frame. Returns False on
        timeout (nothing received)."""
        fr = await self._read_frame(self.ack_timeout)
        if fr is None:
            return False
        ctrl, _pid, payload = fr
        if is_i(ctrl) or is_s(ctrl):
            self._ack_upto(nr(ctrl))
        if is_i(ctrl):
            await self._accept_iframe(ctrl, payload)
        elif is_s(ctrl) and poll_bit(ctrl):
            # peer polled us; answer with our current N(R)
            await self._tx(rr(self.vr, True), response=True)
        return True

    def _ack_upto(self, peer_nr):
        # Remove all frames with N(S) < peer_nr (modulo 8) from unacked.
        while self.va != peer_nr:
            self._unacked.pop(self.va, None)
            self.va = (self.va + 1) & 7

    async def _accept_iframe(self, ctrl, payload):
        if ns(ctrl) == self.vr:
            self.vr = (self.vr + 1) & 7
            if payload:
                self._rxbuf += payload
            await self._tx(rr(self.vr, poll_bit(ctrl)), response=True)
        else:
            # out of sequence: reject / re-request expected
            await self._tx(rr(self.vr, True), response=True)

    async def _retransmit(self):
        """Go-back-N: resend everything from va..vs-1."""
        n = self.va
        while n != self.vs:
            chunk = self._unacked.get(n)
            if chunk is not None:
                self.log(("-->", "reI N(S)=%d N(R)=%d" % (n, self.vr)))
                await self._tx(iframe(n, self.vr), chunk, PID_NO_L3)
            n = (n + 1) & 7

    async def _flush_acks(self, quick=False):
        # best-effort: drain any pending inbound frames briefly
        try:
            fr = await self._read_frame(0.2 if quick else self.ack_timeout)
            while fr is not None:
                ctrl, _pid, payload = fr
                if is_i(ctrl) or is_s(ctrl):
                    self._ack_upto(nr(ctrl))
                if is_i(ctrl):
                    await self._accept_iframe(ctrl, payload)
                fr = await self._read_frame(0.2)
        except Exception:
            pass

    # ---- reliable recv ------------------------------------------------
    async def recv(self, n=4096):
        """Return up to n buffered bytes, pumping the link until some arrive
        or the ack_timeout elapses (mirrors a socket recv well enough for the
        line-oriented b2f handshake)."""
        if self._rxbuf:
            out = bytes(self._rxbuf[:n]); del self._rxbuf[:n]; return out
        got = await self._pump_once()
        if not got:
            return b""
        out = bytes(self._rxbuf[:n]); del self._rxbuf[:n]
        return out


# --------------------------------------------------------------------------
# Systemd interlock: stop the inbound RMS listener for the duration of the
# outbound RF send so the two never key the radio at once.
# --------------------------------------------------------------------------
import subprocess

RMS_SERVICE = "kp4pra-tnc-rms.service"


def _rms_busy():
    """True if the RMS gateway is mid-relay (must not be interrupted)."""
    try:
        st = read_status("rms") or {}
    except Exception:
        st = {}
    return st.get("state") in ("connecting_cms", "connected")


def _svc(action):
    """Run an allowlisted systemctl action on the RMS service. Returns
    (ok, detail)."""
    try:
        r = subprocess.run(
            ["sudo", "/bin/systemctl", action, RMS_SERVICE],
            capture_output=True, text=True, timeout=20)
        return (r.returncode == 0, (r.stderr or r.stdout or "").strip())
    except Exception as e:                          # pragma: no cover
        return (False, "%s: %s" % (type(e).__name__, e))


def _svc_active():
    try:
        r = subprocess.run(["/bin/systemctl", "is-active", RMS_SERVICE],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() == "active"
    except Exception:
        return False


async def _stop_listener(log):
    if not _svc_active():
        log(("info", "RMS listener not active; nothing to stop"))
        return False
    log(("info", "stopping RMS listener for RF send"))
    ok, detail = _svc("stop")
    if not ok:
        raise RfError("could not stop RMS listener: %s" % detail)
    # wait for it to release the Dire Wolf connection
    for _ in range(20):
        if not _svc_active():
            break
        await asyncio.sleep(0.25)
    return True


async def _start_listener(log):
    log(("info", "restarting RMS listener"))
    ok, detail = _svc("start")
    if not ok:
        log(("warn", "could not restart RMS listener: %s" % detail))


# --------------------------------------------------------------------------
# Public driver
# --------------------------------------------------------------------------
def _rf_cfg(cfg):
    d = (cfg.get("webmail", {}).get("delivery", {}) or {})
    return d.get("rf", {}) or {}


def _dry_run(cfg):
    return bool(cfg.get("webmail", {}).get("delivery", {}).get("dry_run", True))


async def send(record, cfg, version="1.0"):
    """Deliver one queued message to the configured remote RMS over RF.

    Mirrors b2fsend.send() but over AX.25 connected mode. Honors dry_run
    (no radio contact when true). Always attempts to restore the RMS
    listener afterward if it was stopped.
    """
    import b2fsend                                  # reuse the B2F driver

    rf = _rf_cfg(cfg)
    session_log = []

    def log(entry):
        session_log.append(entry)

    result = {"route": "rf", "final_status": "Failed", "delivered": False,
              "ok": False, "log": session_log, "error": None}

    remote = (rf.get("remote_rms") or "").strip()
    if not remote:
        result["error"] = "no remote_rms configured"
        return result

    mycall = (rf.get("mycall") or cfg.get("station", {}).get("callsign")
              or "").strip()
    if not mycall:
        result["error"] = "no station callsign / mycall configured"
        return result

    digis = [d.strip() for d in (rf.get("digipeaters") or []) if d and d.strip()]
    if len(digis) > 2:
        result["error"] = "at most 2 digipeaters supported"
        return result

    # Build + compress the message (same path as CMS send).
    try:
        msg = mailbuilder.build_message(record, cfg)
        compressed = lzhuf.compress(msg["text"].encode("utf-8"))
    except Exception as e:
        result["error"] = "build/compress failed: %s: %s" % (type(e).__name__, e)
        return result

    if _dry_run(cfg):
        log(("info", "dry_run: not keying radio"))
        log(("info", "would connect to %s%s and send %d compressed bytes"
             % (remote, (" via " + ",".join(digis)) if digis else "",
                len(compressed))))
        result["final_status"] = "Holding"
        result["error"] = "dry_run enabled"
        return result

    if _rms_busy():
        result["error"] = "RMS gateway is mid-relay; try again shortly"
        return result

    dw = cfg.get("direwolf", {})
    host = dw.get("host", "127.0.0.1")
    port = int(dw.get("port", 8001))

    stopped = False
    stream = None
    try:
        stopped = await _stop_listener(log)
        reader, writer = await asyncio.open_connection(host, port)
        stream = AX25Stream(
            reader, writer, mycall, remote, digis, log,
            block_size=int(rf.get("block_size", 250)),
            ack_timeout=int(rf.get("ack_timeout", 10)),
            connect_retries=int(rf.get("connect_retries", 3)))
        await stream.connect()
        # Reuse the exact B2F driver used for CMS. The RMS presents the same
        # SID/;PQ:/proposal conversation; password answers a ;PQ: challenge
        # if the RMS issues one (most simplex RMS links do not).
        password = cfg.get("rms", {}).get("cms_password", "")
        b2f_result = await b2fsend._run_b2f(
            stream, msg, compressed, log, send_body=True,
            version=version, password=password, cfg=cfg)
        result["delivered"] = bool(b2f_result.get("delivered"))
        result["accepted"] = b2f_result.get("accepted")
        result["fs"] = b2f_result.get("fs")
        result["final_status"] = "Sent" if result["delivered"] else "Failed"
        result["ok"] = bool(result["delivered"])
        if not result["delivered"] and not result["error"]:
            result["error"] = "RMS did not confirm delivery (FS=%r)" % b2f_result.get("fs")
    except Exception as e:
        result["error"] = "%s: %s" % (type(e).__name__, e)
    finally:
        if stream is not None:
            try:
                await stream.close()
            except Exception:
                pass
            try:
                stream._writer.close()
            except Exception:
                pass
        if stopped and bool(rf.get("restart_listener", True)):
            await _start_listener(log)

    return result


async def probe(record, cfg, version="1.0"):
    """Establish the link and read the RMS SID/proposal exchange WITHOUT
    sending a body (RF counterpart of b2fsend.probe)."""
    import b2fsend
    rf = _rf_cfg(cfg)
    session_log = []
    def log(entry): session_log.append(entry)
    result = {"route": "rf", "log": session_log, "error": None, "accepted": None}

    remote = (rf.get("remote_rms") or "").strip()
    mycall = (rf.get("mycall") or cfg.get("station", {}).get("callsign") or "").strip()
    if not remote or not mycall:
        result["error"] = "remote_rms and station callsign required"
        return result
    if _dry_run(cfg):
        result["error"] = "dry_run enabled"
        return result
    if _rms_busy():
        result["error"] = "RMS gateway is mid-relay; try again shortly"
        return result

    digis = [d.strip() for d in (rf.get("digipeaters") or []) if d and d.strip()]
    try:
        msg = mailbuilder.build_message(record, cfg)
        compressed = lzhuf.compress(msg["text"].encode("utf-8"))
    except Exception as e:
        result["error"] = "build/compress failed: %s" % e
        return result

    dw = cfg.get("direwolf", {})
    stopped = False
    stream = None
    try:
        stopped = await _stop_listener(log)
        reader, writer = await asyncio.open_connection(
            dw.get("host", "127.0.0.1"), int(dw.get("port", 8001)))
        stream = AX25Stream(reader, writer, mycall, remote, digis, log,
                            block_size=int(rf.get("block_size", 250)),
                            ack_timeout=int(rf.get("ack_timeout", 10)),
                            connect_retries=int(rf.get("connect_retries", 3)))
        await stream.connect()
        password = cfg.get("rms", {}).get("cms_password", "")
        b2f_result = await b2fsend._run_b2f(
            stream, msg, compressed, log, send_body=False,
            version=version, password=password, cfg=cfg)
        result["accepted"] = b2f_result.get("accepted")
        result["fs"] = b2f_result.get("fs")
    except Exception as e:
        result["error"] = "%s: %s" % (type(e).__name__, e)
    finally:
        if stream is not None:
            try: await stream.close()
            except Exception: pass
            try: stream._writer.close()
            except Exception: pass
        if stopped and bool(rf.get("restart_listener", True)):
            await _start_listener(log)
    return result
