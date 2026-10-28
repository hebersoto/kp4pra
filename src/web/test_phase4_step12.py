#!/usr/bin/env python3
"""
Phase 4 step 1+2 self-test — run from the repo root:

    cd ~/kp4pra-tnc
    PYTHONPATH=src/web python3 src/web/test_phase4_step12.py

Proves the message builder and the LZHUF codec are correct on THIS
machine's Python. LZHUF is byte-sensitive, so this guards against any
copy/transfer corruption: if every check prints OK, the modules are
identical in behavior to the validated originals.

Nothing is transmitted and nothing touches the queue or the service.
"""
import random
import sys

import mailbuilder as mb
import lzhuf


def check(name, cond):
    print(("  OK   " if cond else "  FAIL ") + name)
    if not cond:
        check.failed += 1
check.failed = 0


print("== message builder ==")
check("base_callsign strips SSID", mb.base_callsign("kp3m-10") == "KP3M")
check("station_from_address", mb.station_from_address({"station": {"callsign": "KP3M-10"}}) == "KP3M@winlink.org")
try:
    mb.station_from_address({"station": {"callsign": "N0CALL"}}); ok = False
except mb.BuildError:
    ok = True
check("refuses N0CALL", ok)

cfg = {"station": {"callsign": "KP3M-10"}}
rec = {"to": "dest@example.com", "reply_to": "user@example.org",
       "subject": "Health and welfare", "body": "word " * 60 + "end",
       "created": 1750000000}
msg = mb.build_message(rec, cfg)
check("From is station@winlink.org", msg["from"] == "KP3M@winlink.org")
check("From != Reply-To", msg["from"] != msg["reply_to"])
check("headers present",
      all(h in msg["text"] for h in
          ("From: KP3M@winlink.org", "Reply-To: user@example.org",
           "To: dest@example.com", "Subject: Health and welfare")))
import mailvalidate as mv
check("body wrapped <=78", mv.max_line_length(msg["body"]) <= 78)
check("MID is 12 uppercase alnum", len(msg["mid"]) == 12 and msg["mid"].isalnum() and msg["mid"].upper() == msg["mid"])
pl = mb.proposal_line(msg, 123)
check("proposal line shape", pl.startswith("FC EM ") and pl.split()[2] == msg["mid"] and int(pl.split()[4]) == 123)

print("== LZHUF codec ==")
def rt(d):
    return lzhuf.decompress(lzhuf.compress(d))

cases = {
    "empty": b"", "one": b"A", "short": b"Hello, Winlink!",
    "repeat": b"A" * 32, "abc": b"ABCABCABCABCABCABCABC",
    "spaces": b"      spaces      ",
    "text": (b"Health and welfare message. All OK here. " * 8),
    "newlines": b"line1\r\nline2\r\nline3\r\n" * 5,
    "binary": bytes(range(256)) * 3,
    "msg": msg["text"].encode("utf-8"),
}
for name, d in cases.items():
    check("round-trip %-9s (in=%d comp=%d)" % (name, len(d), len(lzhuf.compress(d))), rt(d) == d)

# position codec: every 12-bit distance must invert exactly
bad = 0
for pos in range(lzhuf.N):
    bw = lzhuf._BitWriter()
    lzhuf._encode_position(bw, pos)
    got = lzhuf._decode_position(lzhuf._BitReader(bw.flush()))
    if got != pos:
        bad += 1
check("position codec inverts all %d distances" % lzhuf.N, bad == 0)

# fuzz
random.seed(7)
fails = 0
for _ in range(1000):
    n = random.randint(0, 800)
    if random.random() < 0.5:
        d = bytes(random.randint(0, 255) for _ in range(n))
    else:
        chunk = bytes(random.randint(32, 126) for _ in range(random.randint(1, 20)))
        d = (chunk * (n // max(1, len(chunk)) + 1))[:n]
    if rt(d) != d:
        fails += 1
check("fuzz 1000 mixed inputs", fails == 0)

big = b"The quick brown fox jumps over the lazy dog. " * 500
check("22KB round-trip", rt(big) == big)
check("checksum stable", lzhuf.compute_checksum(b"Hello, Winlink!") == 195)

print()
if check.failed == 0:
    print("ALL PHASE 4 STEP 1+2 CHECKS PASSED")
    sys.exit(0)
else:
    print("%d CHECK(S) FAILED — do not proceed; the transfer may be corrupt." % check.failed)
    sys.exit(1)
