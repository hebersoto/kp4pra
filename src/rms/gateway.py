"""KP4PRA native Python Winlink RMS gateway.

Dire Wolf KISS TCP -> minimal AX.25 connected mode -> authenticated CMS stream.
One RF session is supported at a time by design for small SBC deployments.
"""
import asyncio, os, signal, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.config import load_config
from common.runtime_status import write_status
from rms.ax25 import KissDecoder,kiss_encode,split_frame,make_frame,is_sabm,is_disc,is_i,is_s,ns,nr,rr,iframe,poll_bit,UA,DM,PID_NO_L3,LinkState
from rms.cms import CmsSession

MODE_CODES={'PACKET-1200':0,'PACKET-9600':3}

def log(msg):
    print(f"[RMS] {msg}", flush=True)

class Gateway:
    def __init__(self,cfg):
        self.cfg=cfg; self.rms=cfg['rms']; self.dw=cfg['direwolf']; self.link=None; self.cms=None
        self.writer=None; self.lock=asyncio.Lock(); self.cms_task=None
    def status(self,state,**extra): write_status('rms',{'state':state,**extra})
    async def tx(self,frame):
        async with self.lock:
            self.writer.write(kiss_encode(frame)); await self.writer.drain()
    async def cms_to_rf(self):
        log("cms_to_rf: started")
        try:
            while self.link and self.cms:
                data=await self.cms.recv(220)
                if not data:
                    log("cms_to_rf: cms.recv() returned empty -- CMS closed the stream")
                    break
                log(f"cms_to_rf: got {len(data)} bytes from CMS: {data!r}")
                for pos in range(0,len(data),200):
                    # Flow control: modulo-8 allows at most 7 outstanding
                    # unacknowledged I-frames; wait for peer RR/I N(R) acks.
                    while self.link and ((self.link.vs-self.link.va)&7)>=7:
                        log(f"cms_to_rf: window full (vs={self.link.vs} va={self.link.va})")
                        await asyncio.sleep(0.05)
                    if not self.link: break
                    chunk=data[pos:pos+200]
                    c=iframe(self.link.vs,self.link.vr)
                    log(f"cms_to_rf: sending I-frame N(S)={self.link.vs} N(R)={self.link.vr} len={len(chunk)}")
                    self.link.vs=(self.link.vs+1)&7
                    await self.tx(make_frame(self.link.remote,self.rms['cms_call'],c,chunk,PID_NO_L3))
        except Exception as e:
            log(f"cms_to_rf: EXCEPTION {type(e).__name__}: {e}")
            self.status('cms_error',message=str(e))
        finally:
            log("cms_to_rf: exiting, closing session")
            await self.close_session()
    async def close_session(self):
        if self.cms: await self.cms.close()
        self.cms=None; self.link=None; self.status('listening')
    async def handle(self,raw):
        try: dest,src,path,ctrl,pid,payload=split_frame(raw)
        except ValueError:
            log(f"handle: split_frame failed on {raw!r}")
            return
        mycall=self.rms['cms_call'].upper()
        if dest.upper()!=mycall:
            return
        # AX.25 spec: a station receiving a Poll-bit (P=1) SABM/DISC/I frame
        # MUST reply with the Final bit (F=1) set on its Response frame, or
        # the peer's data-link state machine will not consider the
        # exchange confirmed. pf_in mirrors whatever Poll bit we just saw.
        pf_in = poll_bit(ctrl)
        if is_sabm(ctrl):
            log(f"handle: SABM from {src} (poll={pf_in})")
            if self.link and self.link.remote!=src:
                log(f"handle: rejecting SABM from {src}, busy with {self.link.remote}")
                await self.tx(make_frame(src,mycall,DM|(0x10 if pf_in else 0),response=True)); return
            # Tear down any previous CMS session/task cleanly before starting
            # a new one. Without this, a rapid re-SABM (e.g. a peer retrying
            # because it didn't recognize our prior UA) leaves the old
            # cms_to_rf() task still running -- it reads self.cms fresh each
            # loop iteration, so once self.cms is reassigned below to the
            # NEW session, both the old (orphaned) task and the new task end
            # up calling .recv() on the same CmsSession concurrently, which
            # crashes with 'read() called while another coroutine is
            # already waiting for incoming data'.
            if self.cms_task and not self.cms_task.done():
                log("handle: cancelling stale cms_to_rf task from a previous session")
                self.cms_task.cancel()
                try:
                    await self.cms_task
                except asyncio.CancelledError:
                    pass
            if self.cms:
                await self.cms.close()
            self.link=LinkState(src)
            await self.tx(make_frame(src,mycall,UA|(0x10 if pf_in else 0),response=True))
            self.status('connecting_cms',remote=src)
            log(f"handle: UA(response,F={pf_in}) sent to {src}, opening CMS session")
            self.cms=CmsSession(src,mycall,self.rms['cms_password'],self.rms['frequency_hz'],MODE_CODES.get(self.rms.get('mode','PACKET-1200'),0),self.rms.get('cms_host','cms.winlink.org'),self.rms.get('cms_port',8772))
            try:
                await self.cms.connect(); self.status('connected',remote=src)
                log("handle: CMS connected, starting cms_to_rf task")
                self.cms_task=asyncio.create_task(self.cms_to_rf())
            except Exception as e:
                log(f"handle: CMS connect FAILED: {type(e).__name__}: {e}")
                self.status('cms_error',remote=src,message=str(e))
                await self.tx(make_frame(src,mycall,DM,response=True)); await self.close_session()
            return
        if not self.link or src!=self.link.remote: return
        if is_disc(ctrl):
            log(f"handle: DISC from {src} (poll={pf_in})")
            await self.tx(make_frame(src,mycall,UA|(0x10 if pf_in else 0),response=True))
            await self.close_session(); return
        if is_i(ctrl) or is_s(ctrl):
            log(f"handle: frame from {src} N(R)={nr(ctrl)} (was va={self.link.va})")
            self.link.va=nr(ctrl)
        if is_i(ctrl):
            log(f"handle: I-frame N(S)={ns(ctrl)} expected={self.link.vr} payload={payload!r}")
            if ns(ctrl)==self.link.vr:
                self.link.vr=(self.link.vr+1)&7
                await self.tx(make_frame(src,mycall,rr(self.link.vr,pf_in),response=True))
                if self.cms and payload:
                    log(f"handle: forwarding {len(payload)} bytes to CMS")
                    await self.cms.send(payload)
            else:
                log("handle: out-of-sequence I-frame")
                await self.tx(make_frame(src,mycall,rr(self.link.vr,True),response=True))
    async def run(self):
        if not self.rms.get('enabled'): self.status('disabled'); return
        self.status('starting')
        while True:
            try:
                reader,self.writer=await asyncio.open_connection(self.dw['host'],int(self.dw['port'])); self.status('listening')
                log("run: connected to Dire Wolf KISS TCP, listening")
                dec=KissDecoder()
                while True:
                    data=await reader.read(4096)
                    if not data: raise ConnectionError('Dire Wolf KISS disconnected')
                    for frame in dec.feed(data): await self.handle(frame)
            except asyncio.CancelledError: break
            except Exception as e: self.status('kiss_error',message=str(e)); await asyncio.sleep(5)

async def main(): await Gateway(load_config()).run()
if __name__=='__main__': asyncio.run(main())
