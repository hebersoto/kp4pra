#!/usr/bin/env python3
"""Phase 4 step 4 self-test: drives b2fsend probe/send against a MOCK CMS
(no network). Run from repo root:
    PYTHONPATH=src/web python3 src/web/test_phase4_step4_mock.py
Requires prior steps' modules (b2f, lzhuf, mailbuilder, mailqueue) and
rms/cms.py present. Nothing connects to the network."""
import sys, types, tempfile, asyncio, importlib
sys.path.insert(0,"src/web")

d=tempfile.mkdtemp()
# Real common.config is fine on the board, but for an isolated test we stub it:
if "common.config" not in sys.modules:
    common=types.ModuleType("common"); common.__path__=[]
    cfgmod=types.ModuleType("common.config")
    cfgmod.load_config=lambda *a,**k: {"paths":{"data":d}}
    sys.modules["common"]=common; sys.modules["common.config"]=cfgmod

import mailqueue, b2fsend

CFG={"paths":{"data":d},"station":{"callsign":"KP3M"},
     "rms":{"cms_call":"KP3M-10","cms_password":"x","cms_host":"mock","cms_port":8772,"frequency_hz":0,"mode":"PACKET-1200"},
     "webmail":{"enabled":True,"delivery":{"dry_run":False,"method":"cms"}}}

class MockCMS:
    def __init__(self, fs=b"FS Y\r", drop=False):
        self.fs=fs; self.drop=drop; self.sent=bytearray(); self.stage="banner"
    async def connect(self):
        if self.drop: raise ConnectionError("closed during login")
        return b""
    async def recv(self,n=512):
        if self.stage=="banner": self.stage="await_prop"; return b"[WL2K-5.0-B2FWIHJM$]\r;PR: KP3M>\r"
        if self.stage=="after_prop": self.stage="after_fs"; return self.fs
        if self.stage=="after_body": self.stage="done"; return b"FF\r"
        return b""
    async def send(self,data):
        self.sent.extend(data)
        if self.stage=="await_prop" and b"F>" in bytes(self.sent): self.stage="after_prop"
        if b"\x04" in data: self.stage="after_body"
    async def close(self): pass

fail=0
def ck(n,c):
    global fail; print(("  OK   " if c else "  FAIL ")+n); fail+=(0 if c else 1)

async def main():
    rec=mailqueue.enqueue("np4jn@outlook.com","h@g.com","T","All OK."); mailqueue.set_status(rec["id"],"Approved"); rec=mailqueue.get(rec["id"])

    m=MockCMS(b"FS Y\r"); b2fsend._make_session=lambda c,r,g: m
    res=await b2fsend.probe(rec,CFG,"station")
    ck("probe FS Y: accepted, no body", res["ok"] and res["accepted"] and not res["delivered"] and b"\x04" not in bytes(m.sent))

    m=MockCMS(b"FS N\r"); b2fsend._make_session=lambda c,r,g: m
    res=await b2fsend.probe(rec,CFG,"gateway")
    ck("probe FS N: not accepted", res["ok"] and res["accepted"] is False)

    m=MockCMS(b"FS Y\r"); b2fsend._make_session=lambda c,r,g: m
    res=await b2fsend.send(rec,CFG,"station")
    ck("send FS Y: delivered, status Sent", res["delivered"] and res["final_status"]=="Sent" and b"\x04" in bytes(m.sent))
    ck("status persisted Sent", mailqueue.get(rec["id"])["status"]=="Sent")

    try:
        await b2fsend.send(mailqueue.get(rec["id"]),CFG); ck("dup guard",False)
    except b2fsend.DeliveryError as e: ck("dup guard refuses already-Sent", "already Sent" in str(e))

    CFG["webmail"]["delivery"]["dry_run"]=True
    try:
        await b2fsend.probe(rec,CFG); ck("dry_run gate probe",False)
    except b2fsend.DeliveryError as e: ck("dry_run gate blocks probe", "dry_run" in str(e))
    CFG["webmail"]["delivery"]["dry_run"]=False

    r2=mailqueue.enqueue("a@b.com","c@d.com","s","b"); mailqueue.set_status(r2["id"],"Approved")
    m=MockCMS(drop=True); b2fsend._make_session=lambda c,r,g: m
    res=await b2fsend.send(mailqueue.get(r2["id"]),CFG)
    ck("login drop -> Failed", res["final_status"]=="Failed")

    ids=b2fsend._identities(CFG)
    ck("identities", ids["station"]==("KP3M","KP3M") and ids["gateway"]==("KP3M","KP3M-10"))

    print()
    print("ALL PHASE 4 STEP 4 MOCK-CMS CHECKS PASSED" if fail==0 else "%d FAILED"%fail)
    sys.exit(0 if fail==0 else 1)

asyncio.run(main())
