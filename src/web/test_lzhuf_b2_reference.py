#!/usr/bin/env python3
"""
Step 3.5 verification: prove lzhuf.py's B2 framing + CRC-16 match the
Winlink reference (la5nta/wl2k-go lzhuf), byte-for-byte, plus round-trips.
Run:  PYTHONPATH=src/web python3 src/web/test_lzhuf_b2_reference.py
Nothing transmitted.
"""
import random, sys
import lzhuf

fail=0
def ck(n,c):
    global fail; print(("  OK   " if c else "  FAIL ")+n); fail+=(0 if c else 1)

# CRC table matches the reference table (crc.go) at known rows
lzhuf._CRC16_TAB=lzhuf._build_crc16_tab()
ck("CRC table row0", lzhuf._CRC16_TAB[:8]==[0x0000,0x1021,0x2042,0x3063,0x4084,0x50a5,0x60c6,0x70e7])
ck("CRC table [16]/[255]", lzhuf._CRC16_TAB[16]==0x1231 and lzhuf._CRC16_TAB[255]==0x1ef0)

# crc16() identical to reference crc() (udpCRC16 + append 0,0)
def ref_udp(cp,sm): return (((sm<<8)&0xff00) ^ lzhuf._CRC16_TAB[(sm>>8)&0xff] ^ cp)&0xffff
def ref_crc(p):
    sm=0
    for c in list(p)+[0,0]: sm=ref_udp(c,sm)
    return sm
random.seed(0); mm=0
for _ in range(3000):
    d=bytes(random.randint(0,255) for _ in range(random.randint(0,80)))
    if lzhuf.crc16(d)!=ref_crc(d): mm+=1
ck("crc16 byte-identical to reference (3000 inputs)", mm==0)

# B2 stream layout: [CRC16 LE][fileSize LE][data]; round-trips; tamper caught
def rt(d): return lzhuf.decompress(lzhuf.compress(d))
ok=True
for d in [b"", b"A", b"Hello, Winlink!", b"A"*40, (b"Health & welfare. "*10), bytes(range(256))*2]:
    blob=lzhuf.compress(d)
    if int.from_bytes(blob[2:6],"little")!=len(d): ok=False
    if rt(d)!=d: ok=False
ck("B2 framing + round-trips", ok)
blob=bytearray(lzhuf.compress(b"Health and welfare")); blob[7]^=0xFF
try: lzhuf.decompress(bytes(blob)); ck("tamper -> CRC error", False)
except ValueError: ck("tamper -> CRC error", True)

random.seed(9); f=0
for _ in range(300):
    n=random.randint(0,600)
    d=bytes(random.randint(0,255) for _ in range(n)) if random.random()<.5 else (bytes(random.randint(32,126) for _ in range(random.randint(1,15)))*(n//8+1))[:n]
    if rt(d)!=d: f+=1
ck("fuzz 300", f==0)

print()
print("ALL LZHUF B2 REFERENCE CHECKS PASSED" if fail==0 else "%d FAILED"%fail)
sys.exit(0 if fail==0 else 1)
