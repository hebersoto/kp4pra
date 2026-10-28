#!/usr/bin/env python3
"""Phase 4 step 3 self-test (b2f assembly + dry-run). Run from repo root:
       PYTHONPATH=src/web python3 src/web/test_phase4_step3.py
Requires step 1+2 modules (mailbuilder.py, lzhuf.py) already in src/web.
Nothing is transmitted."""
import sys
import b2f, mailbuilder, lzhuf

fail=0
def ck(name,c):
    global fail
    print(("  OK   " if c else "  FAIL ")+name); fail+= (0 if c else 1)

ck("client SID", b2f.client_sid("1.4.4")=="[KP4PRA-1.4.4-B2FHM$]")
p=b2f.parse_sid("[WL2K-5.0-B2FWIHJM$]")
ck("parse peer SID caps", p["fbb"] and p["b2"] and p["checksum"])
ck("FS parse Y/N/H/!", b2f.parse_fs("FS YNH!")==["send","skip","hold","resume"])

cfg={"station":{"callsign":"KP3M"}}
msg=mailbuilder.build_message({"to":"d@e.com","reply_to":"u@e.org","subject":"T","body":"Body.","created":1750000000},cfg,mid="ABC123DEF456")
prop=b2f.build_proposals([(msg,99)])
ck("proposal line", prop["lines"]==["FC EM ABC123DEF456 %d 99 0"%msg["size"]])
ck("proposal checksum deterministic", prop["checksum"]==b2f.proposal_checksum(prop["block"]))

for n in [0,1,250,251,600,5000]:
    blob=bytes((i*7)&0xFF for i in range(n))
    fr=b2f.frame_binary("MIDMIDMIDMID",blob)
    ck("frame/unframe n=%d"%n, b2f.unframe_binary(fr["bytes"])==blob)

dr=b2f.dry_run_cms({"to":"d@e.com","reply_to":"u@e.org","subject":"T","body":"All OK. 73.","created":1750000000},cfg,version="1.4.4")
ck("dry-run from=station@winlink.org", dr["from"]=="KP3M@winlink.org")
ck("dry-run has FC proposal", any(t[1].startswith("FC EM") for t in dr["transcript"]))
ck("dry-run marked DRY RUN", any("DRY RUN" in t[1] for t in dr["transcript"]))
try:
    b2f.dry_run_cms({"to":"d@e.com","reply_to":"u@e.org","body":"x"},{"station":{"callsign":""}}); ck("refuse no-callsign",False)
except mailbuilder.BuildError: ck("refuse no-callsign",True)

print()
print("ALL PHASE 4 STEP 3 CHECKS PASSED" if fail==0 else "%d CHECK(S) FAILED"%fail)
sys.exit(0 if fail==0 else 1)
