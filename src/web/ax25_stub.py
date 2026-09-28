"""Faithful stub of rms/ax25.py (current 1.4.7 API) for transport tests.

Matches the real signatures used by RfTransport:
  PID_NO_L3, kiss_encode, KissDecoder.feed, encode_call/decode_call,
  split_frame, make_frame(dest,src,ctrl,payload,pid,response,digis).
Digipeater emit mirrors the 1.4.7 change (last address carries the HDLC
extension bit; src not last when digis follow).
"""
from typing import Tuple, List

FEND = 0xC0; FESC = 0xDB; TFEND = 0xDC; TFESC = 0xDD
PID_NO_L3 = 0xF0


def kiss_encode(frame: bytes, port: int = 0) -> bytes:
    data = bytes([(port & 0x0F) << 4]) + frame
    data = data.replace(bytes([FESC]), bytes([FESC, TFESC])).replace(
        bytes([FEND]), bytes([FESC, TFEND]))
    return bytes([FEND]) + data + bytes([FEND])


class KissDecoder:
    def __init__(self): self.buf = bytearray(); self.esc = False
    def feed(self, data: bytes):
        out = []
        for b in data:
            if b == FEND:
                if self.buf:
                    raw = bytes(self.buf); self.buf.clear()
                    if (raw[0] & 0x0F) == 0: out.append(raw[1:])
                self.esc = False; continue
            if self.esc:
                self.buf.append(FEND if b == TFEND else FESC if b == TFESC else b)
                self.esc = False
            elif b == FESC: self.esc = True
            else: self.buf.append(b)
        return out


def encode_call(call: str, last: bool = False, command: bool = False) -> bytes:
    call = call.upper().strip(); base, _, ssid_s = call.partition('-')
    ssid = int(ssid_s or 0); base = base[:6].ljust(6)
    a = bytearray((ord(c) << 1) & 0xFE for c in base)
    a.append(0x60 | ((ssid & 0x0F) << 1) | (0x80 if command else 0) | (1 if last else 0))
    return bytes(a)


def decode_call(a: bytes) -> str:
    base = ''.join(chr(x >> 1) for x in a[:6]).strip(); ssid = (a[6] >> 1) & 0x0F
    return f"{base}-{ssid}" if ssid else base


def split_frame(frame: bytes) -> Tuple[str, str, List[str], int, int, bytes]:
    if len(frame) < 15: raise ValueError('short AX.25 frame')
    addrs = []; i = 0
    while True:
        if i + 7 > len(frame): raise ValueError('truncated AX.25 address')
        a = frame[i:i + 7]; addrs.append(decode_call(a)); i += 7
        if a[6] & 1: break
        if len(addrs) > 10: raise ValueError('too many AX.25 addresses')
    if len(addrs) < 2 or i >= len(frame): raise ValueError('missing control')
    ctrl = frame[i]; i += 1; pid = -1
    if (ctrl & 1) == 0 or ctrl == 0x03:
        if i >= len(frame): raise ValueError('missing PID')
        pid = frame[i]; i += 1
    return addrs[0], addrs[1], addrs[2:], ctrl, pid, frame[i:]


def make_frame(dest: str, src: str, ctrl: int, payload: bytes = b'',
               pid: int = -1, response: bool = False, digis=None) -> bytes:
    digis = [d for d in (digis or []) if d and d.strip()]
    out = bytearray()
    out += encode_call(dest, last=False, command=not response)
    if digis:
        out += encode_call(src, last=False, command=response)
        for k, d in enumerate(digis):
            out += encode_call(d, last=(k == len(digis) - 1), command=False)
    else:
        out += encode_call(src, last=True, command=response)
    out.append(ctrl)
    if pid >= 0: out.append(pid)
    out += payload
    return bytes(out)
