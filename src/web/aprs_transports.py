"""Concrete APRS transports for aprschat.AprsChat.

Both expose the surface AprsChat expects:
    transport.on_frame = chat.on_frame        # async RX callback(src, info)
    await transport.send_info(info_field)     # send one APRS packet

RfTransport   -- APRS UI frames over a Dire Wolf KISS-TCP link. Reuses the
                 station's rms/ax25.py (injected, so this stays testable):
                 make_frame(dest=TOCALL, src=mycall, ctrl=UI, payload=info,
                 pid=0xF0, digis=path), kiss_encode, KissDecoder, split_frame.
AprsIsTransport -- TNC2 lines over an APRS-IS TCP connection (aprs.py helpers).

The session manager (systemd interlock, connect/disconnect lifecycle) drives
these; connection setup is provided but network I/O is only exercised live.
"""

import asyncio
import aprs

UI = 0x03                       # AX.25 UI control byte (P/F bit is 0x10)


class RfTransport:
    """APRS over Dire Wolf KISS. `ax25` is the rms.ax25 module (injected)."""

    def __init__(self, ax25, mycall, tocall="APKP41", digipath=None,
                 host="127.0.0.1", port=8001, opener=None):
        self.ax25 = ax25
        self.mycall = aprs.normalize_call(mycall)
        self.tocall = aprs.normalize_call(tocall)
        self.digipath = list(digipath or [])
        self.host = host
        self.port = port
        self.on_frame = None
        self._reader = None
        self._writer = None
        self._dec = ax25.KissDecoder()
        self._running = False
        self._opener = opener or asyncio.open_connection

    async def connect(self):
        self._reader, self._writer = await self._opener(self.host, self.port)

    async def send_info(self, info):
        payload = info.encode("ascii", "replace")
        frame = self.ax25.make_frame(self.tocall, self.mycall, UI, payload,
                                     self.ax25.PID_NO_L3, digis=self.digipath)
        self._writer.write(self.ax25.kiss_encode(frame))
        await self._writer.drain()

    def feed(self, data):
        """Decode KISS bytes and yield (src, info) for APRS UI frames.
        Pure/synchronous so it is unit-testable without a socket."""
        out = []
        for kf in self._dec.feed(data):
            try:
                dest, src, digis, ctrl, pid, payload = self.ax25.split_frame(kf)
            except ValueError:
                continue
            if (ctrl & 0xEF) != UI:          # only UI frames carry APRS
                continue
            out.append((src, payload.decode("ascii", "replace")))
        return out

    async def run_rx(self):
        self._running = True
        while self._running:
            data = await self._reader.read(1024)
            if not data:
                break
            for src, info in self.feed(data):
                if self.on_frame:
                    await self.on_frame(src, info)

    async def close(self):
        self._running = False
        if self._writer:
            try:
                self._writer.close()
            except Exception:
                pass


class AprsIsTransport:
    """APRS-IS (internet) transport over TCP using TNC2 lines."""

    def __init__(self, mycall, tocall="APKP41", host="rotate.aprs2.net",
                 port=14580, appname="KP4PRA", version="1.0", filt=None,
                 opener=None):
        self.mycall = aprs.normalize_call(mycall)
        self.tocall = aprs.normalize_call(tocall)
        self.host = host
        self.port = port
        self.appname = appname
        self.version = version
        # default server-side filter: messages addressed to our base call
        self.filt = filt or ("g/%s*" % self.mycall.split("-")[0])
        self.on_frame = None
        self._reader = None
        self._writer = None
        self._running = False
        self._opener = opener or asyncio.open_connection

    async def connect(self):
        self._reader, self._writer = await self._opener(self.host, self.port)
        login = aprs.aprsis_login(self.mycall, self.appname, self.version,
                                  self.filt)
        self._writer.write((login + "\r\n").encode("ascii"))
        await self._writer.drain()

    async def send_info(self, info):
        line = aprs.tnc2_line(self.mycall, self.tocall, info, path=["TCPIP*"])
        self._writer.write((line + "\r\n").encode("ascii", "replace"))
        await self._writer.drain()

    def parse_line(self, line):
        """(src, info) for a message-bearing TNC2 line, else None. Pure."""
        d = aprs.parse_tnc2(line)
        if not d:
            return None
        return (d["source"], d["info"])

    async def run_rx(self):
        self._running = True
        while self._running:
            raw = await self._reader.readline()
            if not raw:
                break
            line = raw.decode("ascii", "replace").rstrip("\r\n")
            got = self.parse_line(line)
            if got and self.on_frame:
                await self.on_frame(got[0], got[1])

    async def close(self):
        self._running = False
        if self._writer:
            try:
                self._writer.close()
            except Exception:
                pass
