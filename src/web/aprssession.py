"""APRS chat session lifecycle -- the wiring around the tested core.

A single APRS chat session per box, mutually exclusive with the RMS gateway:
while a session is active it OWNS the radio, so starting one stops
kp4pra-tnc-rms (already allowed in sudoers) and stopping the session restarts
it. The session opens the Dire Wolf KISS connection, runs the RX loop and a
periodic retry loop, and exposes send/poll for the web routes.

Dependency-injected (opener + systemctl callables + clock) so the whole
lifecycle is unit-testable without a socket or systemd. In production the web
app constructs it with the real rms.ax25 module and asyncio.open_connection.
"""

import asyncio
import subprocess
import time

import aprs
import aprschat
from aprs_transports import RfTransport, AprsIsTransport

RMS_SERVICE = "kp4pra-tnc-rms.service"


# ---- default (production) systemd interlock helpers ----------------------
def _default_svc(action):
    try:
        r = subprocess.run(["sudo", "/bin/systemctl", action, RMS_SERVICE],
                           capture_output=True, text=True, timeout=20)
        return (r.returncode == 0, (r.stderr or r.stdout or "").strip())
    except Exception as e:                          # pragma: no cover
        return (False, "%s: %s" % (type(e).__name__, e))


def _default_svc_active():
    try:
        r = subprocess.run(["/bin/systemctl", "is-active", RMS_SERVICE],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip() == "active"
    except Exception:
        return False


def _default_rms_busy():
    try:
        from common.runtime_status import read_status
        st = read_status("rms") or {}
    except Exception:
        st = {}
    return st.get("state") in ("connecting_cms", "connected")


class AprsSession:
    def __init__(self, ax25, cfg, opener=None, svc=None, svc_active=None,
                 rms_busy=None, clock=time.time, retry_tick=5):
        self.ax25 = ax25
        self.cfg = cfg or {}
        self._opener = opener or asyncio.open_connection
        self._svc = svc or _default_svc
        self._svc_active = svc_active or _default_svc_active
        self._rms_busy = rms_busy or _default_rms_busy
        self.clock = clock
        self.retry_tick = retry_tick

        self.store = aprschat.ChatStore(clock=clock)
        self.chat = None
        self.rf = None
        self.aprsis = None
        self._rx_tasks = []
        self._retry_task = None
        self._beacon_task = None
        self._rms_was_running = False
        self.active = False
        self.started_at = None
        self.log = []

    # ---- config helpers ----
    def _acfg(self):
        return self.cfg.get("aprs", {}) or {}

    def _mycall(self):
        a = self._acfg()
        return aprs.normalize_call(
            a.get("mycall") or self.cfg.get("station", {}).get("callsign") or "")

    # ---- lifecycle ----
    async def start(self):
        if self.active:
            return {"ok": True, "active": True, "note": "already running"}
        a = self._acfg()
        if not a.get("enabled", True):
            return {"ok": False, "error": "APRS chat is disabled in config"}
        mycall = self._mycall()
        if not mycall:
            return {"ok": False, "error": "station callsign not configured"}
        if self._rms_busy():
            return {"ok": False, "error": "RMS gateway is mid-relay; try again shortly"}

        # take the radio: stop the RMS listener
        self._rms_was_running = self._svc_active()
        if self._rms_was_running:
            ok, detail = self._svc("stop")
            if not ok:
                return {"ok": False, "error": "could not stop RMS listener: %s" % detail}
            for _ in range(20):
                if not self._svc_active():
                    break
                await asyncio.sleep(0.25)
            self.log.append(("info", "stopped RMS listener for APRS chat"))

        try:
            dw = self.cfg.get("direwolf", {})
            host = dw.get("host", "127.0.0.1")
            port = int(dw.get("port", 8001))
            tocall = a.get("tocall", "APKP41")
            digipath = a.get("digipath", []) or []

            self.rf = RfTransport(self.ax25, mycall, tocall=tocall,
                                  digipath=digipath, host=host, port=port,
                                  opener=self._opener)
            await self.rf.connect()
            self.chat = aprschat.AprsChat(
                mycall, self.rf, store=self.store, tocall=tocall,
                digipath=digipath, path_label="rf",
                retry_interval=int(a.get("retry_interval", 30)),
                max_retries=int(a.get("max_retries", 3)), clock=self.clock)
            self._rx_tasks.append(asyncio.ensure_future(self.rf.run_rx()))

            # optional APRS-IS (internet) path, additive
            isc = a.get("aprsis", {}) or {}
            if isc.get("enabled"):
                self.aprsis = AprsIsTransport(
                    mycall, tocall=tocall,
                    host=isc.get("host", "rotate.aprs2.net"),
                    port=int(isc.get("port", 14580)),
                    appname="KP4PRA", version=str(self.cfg.get("_version", "1.0")),
                    opener=self._opener)
                # APRS-IS RX also feeds the same chat (its own path label)
                self.aprsis.on_frame = self._aprsis_on_frame
                await self.aprsis.connect()
                self._rx_tasks.append(asyncio.ensure_future(self.aprsis.run_rx()))

            self._retry_task = asyncio.ensure_future(self._retry_loop())
            b = a.get("beacon", {}) or {}
            if b.get("enabled") and int(b.get("interval_min", 0)) > 0:
                self._beacon_task = asyncio.ensure_future(self._beacon_loop())
            self.active = True
            self.started_at = self.clock()
            self.log.append(("info", "APRS chat active as %s (tocall %s)" % (mycall, tocall)))
            return {"ok": True, "active": True, "mycall": mycall, "tocall": tocall}
        except Exception as e:
            # roll back: restart RMS if we stopped it
            await self._teardown()
            if self._rms_was_running:
                self._svc("start")
            return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}

    async def _aprsis_on_frame(self, src, info):
        # tag path as aprsis by temporarily switching the chat label
        prev = self.chat.path_label
        self.chat.path_label = "aprsis"
        try:
            return await self.chat.on_frame(src, info)
        finally:
            self.chat.path_label = prev

    # ---- position beacon ----
    def _beacon_position(self):
        """(lat, lon) for a beacon: beacon lat/lon if set, else station
        lat/lon, else the station Maidenhead grid. Returns None if none set."""
        b = self._acfg().get("beacon", {}) or {}
        st = self.cfg.get("station", {}) or {}
        lat = b.get("lat") or st.get("lat") or 0
        lon = b.get("lon") or st.get("lon") or 0
        if lat or lon:
            return (float(lat), float(lon))
        ll = aprs.maidenhead_to_latlon(st.get("mygrid") or "")
        return ll  # None or (lat, lon)

    async def beacon_now(self):
        """Transmit one APRS position beacon over the active session."""
        if not self.active or not self.rf:
            return {"ok": False, "error": "APRS chat is not running"}
        b = self._acfg().get("beacon", {}) or {}
        pos = self._beacon_position()
        if not pos:
            return {"ok": False,
                    "error": "no position: set station grid or lat/lon in config"}
        info = aprs.position_report(pos[0], pos[1],
                                    b.get("symbol_table", "/"),
                                    b.get("symbol_code", "-"),
                                    b.get("comment", ""))
        await self.rf.send_info(info)
        if self.aprsis:
            try:
                await self.aprsis.send_info(info)
            except Exception:
                pass
        self.log.append(("info", "beacon: %s" % info))
        return {"ok": True, "info": info, "lat": pos[0], "lon": pos[1]}

    async def _beacon_loop(self):
        b = self._acfg().get("beacon", {}) or {}
        interval = int(b.get("interval_min", 0)) * 60
        if interval <= 0:
            return
        try:
            while self.active:
                await self.beacon_now()          # announce on start, then every N min
                await asyncio.sleep(interval)
        except asyncio.CancelledError:
            pass

    async def _retry_loop(self):
        try:
            while self.active:
                await asyncio.sleep(self.retry_tick)
                if self.chat:
                    await self.chat.resend_due()
        except asyncio.CancelledError:
            pass

    async def _teardown(self):
        for tsk in self._rx_tasks:
            tsk.cancel()
        self._rx_tasks = []
        if self._retry_task:
            self._retry_task.cancel()
            self._retry_task = None
        if self._beacon_task:
            self._beacon_task.cancel()
            self._beacon_task = None
        for tr in (self.rf, self.aprsis):
            if tr:
                try:
                    await tr.close()
                except Exception:
                    pass
        self.rf = self.aprsis = None
        self.chat = None

    async def stop(self):
        if not self.active:
            return {"ok": True, "active": False}
        self.active = False
        await self._teardown()
        restarted = False
        if self._rms_was_running:
            ok, _ = self._svc("start")
            restarted = ok
        self.log.append(("info", "APRS chat stopped; RMS restarted=%s" % restarted))
        return {"ok": True, "active": False, "rms_restarted": restarted}

    # ---- operations for the web routes ----
    async def send(self, peer, text, want_ack=True):
        if not self.active or not self.chat:
            return {"ok": False, "error": "APRS chat is not running"}
        peer = aprs.normalize_call(peer)
        if not aprs.valid_call(peer):
            return {"ok": False, "error": "invalid destination callsign"}
        m = await self.chat.send_text(peer, text, want_ack=want_ack)
        return {"ok": True, "message": m.as_dict()}

    def status(self):
        return {"active": self.active,
                "mycall": self._mycall(),
                "tocall": self._acfg().get("tocall", "APKP41"),
                "aprsis": bool(self.aprsis),
                "started_at": self.started_at,
                "head": self.store.head}

    def messages_since(self, seq):
        return self.store.since(int(seq or 0))

    def threads(self):
        return self.store.threads()
