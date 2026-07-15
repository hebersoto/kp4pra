"""Minimal AX.25 connected-mode and KISS helpers used by the RMS gateway.

Implements the subset required to accept one-hop SABM connections, exchange
I-frames with modulo-8 sequencing, acknowledge with RR, and close with DISC.
"""
from dataclasses import dataclass
from typing import List, Tuple

FEND=0xC0; FESC=0xDB; TFEND=0xDC; TFESC=0xDD
PID_NO_L3=0xF0


def kiss_encode(frame: bytes, port: int=0) -> bytes:
    data=bytes([(port & 0x0F)<<4])+frame
    data=data.replace(bytes([FESC]), bytes([FESC,TFESC])).replace(bytes([FEND]), bytes([FESC,TFEND]))
    return bytes([FEND])+data+bytes([FEND])

class KissDecoder:
    def __init__(self): self.buf=bytearray(); self.esc=False
    def feed(self,data:bytes):
        out=[]
        for b in data:
            if b==FEND:
                if self.buf:
                    raw=bytes(self.buf); self.buf.clear()
                    if (raw[0]&0x0F)==0: out.append(raw[1:])
                self.esc=False; continue
            if self.esc:
                self.buf.append(FEND if b==TFEND else FESC if b==TFESC else b); self.esc=False
            elif b==FESC: self.esc=True
            else: self.buf.append(b)
        return out

def encode_call(call:str,last:bool=False,command:bool=False)->bytes:
    call=call.upper().strip(); base,_,ssid_s=call.partition('-'); ssid=int(ssid_s or 0)
    base=base[:6].ljust(6)
    a=bytearray((ord(c)<<1)&0xFE for c in base)
    a.append(0x60|((ssid&0x0F)<<1)|(0x80 if command else 0)|(1 if last else 0))
    return bytes(a)

def decode_call(a:bytes)->str:
    base=''.join(chr(x>>1) for x in a[:6]).strip(); ssid=(a[6]>>1)&0x0F
    return f"{base}-{ssid}" if ssid else base

def split_frame(frame:bytes)->Tuple[str,str,List[str],int,int,bytes]:
    if len(frame)<15: raise ValueError('short AX.25 frame')
    addrs=[]; i=0
    while True:
        if i+7>len(frame): raise ValueError('truncated AX.25 address')
        a=frame[i:i+7]; addrs.append(decode_call(a)); i+=7
        if a[6]&1: break
        if len(addrs)>10: raise ValueError('too many AX.25 addresses')
    if len(addrs)<2 or i>=len(frame): raise ValueError('missing control')
    ctrl=frame[i]; i+=1; pid=-1
    if (ctrl&1)==0 or ctrl==0x03:
        if i>=len(frame): raise ValueError('missing PID')
        pid=frame[i]; i+=1
    return addrs[0],addrs[1],addrs[2:],ctrl,pid,frame[i:]

def make_frame(dest:str,src:str,ctrl:int,payload:bytes=b'',pid:int=-1)->bytes:
    out=bytearray(); out+=encode_call(dest,last=False,command=True); out+=encode_call(src,last=True)
    out.append(ctrl)
    if pid>=0: out.append(pid)
    out+=payload; return bytes(out)

def is_s(c): return (c&0x03)==0x01
def is_sabm(c): return (c&0xEF)==0x2F
def is_disc(c): return (c&0xEF)==0x43
def is_ua(c): return (c&0xEF)==0x63
def is_i(c): return (c&1)==0
def ns(c): return (c>>1)&7
def nr(c): return (c>>5)&7
def rr(n:int,pf=False): return 0x01|((n&7)<<5)|(0x10 if pf else 0)
def iframe(send:int,recv:int,pf=False): return ((send&7)<<1)|((recv&7)<<5)|(0x10 if pf else 0)
UA=0x63; DM=0x0F; DISC=0x43

@dataclass
class LinkState:
    remote:str
    vs:int=0   # next N(S) we will send
    vr:int=0   # next N(S) we expect to receive
    va:int=0   # oldest unacknowledged N(S) (updated from peer N(R))
