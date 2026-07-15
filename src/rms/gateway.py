"""KP4PRA native Python Winlink RMS gateway.

Dire Wolf KISS TCP -> minimal AX.25 connected mode -> authenticated CMS stream.
One RF session is supported at a time by design for small SBC deployments.
"""
import asyncio, os, signal, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from common.config import load_config
from common.runtime_status import write_status
from rms.ax25 import KissDecoder,kiss_encode,split_frame,make_frame,is_sabm,is_disc,is_i,is_s,ns,nr,rr,iframe,UA,DM,PID_NO_L3,LinkState
from rms.cms import CmsSession

MODE_CODES={'PACKET-1200':0,'PACKET-9600':3}

class Gateway:
    def __init__(self,cfg):
        self.cfg=cfg; self.rms=cfg['rms']; self.dw=cfg['direwolf']; self.link=None; self.cms=None
        self.writer=None; self.lock=asyncio.Lock(); self.cms_task=None
    def status(self,state,**extra): write_status('rms',{'state':state,**extra})
    async def tx(self,frame):
        async with self.lock:
            self.writer.write(kiss_encode(frame)); await self.writer.drain()
    async def cms_to_rf(self):
        try:
            while self.link and self.cms:
                data=await self.cms.recv(220)
                if not data: break
                for pos in range(0,len(data),200):
                    # Flow control: modulo-8 allows at most 7 outstanding
                    # unacknowledged I-frames; wait for peer RR/I N(R) acks.
                    while self.link and ((self.link.vs-self.link.va)&7)>=7:
                        await asyncio.sleep(0.05)
                    if not self.link: break
                    chunk=data[pos:pos+200]
                    c=iframe(self.link.vs,self.link.vr); self.link.vs=(self.link.vs+1)&7
                    await self.tx(make_frame(self.link.remote,self.rms['cms_call'],c,chunk,PID_NO_L3))
        except Exception as e: self.status('cms_error',message=str(e))
        finally: await self.close_session()
    async def close_session(self):
        if self.cms: await self.cms.close()
        self.cms=None; self.link=None; self.status('listening')
    async def handle(self,raw):
        try: dest,src,path,ctrl,pid,payload=split_frame(raw)
        except ValueError: return
        mycall=self.rms['cms_call'].upper()
        if dest.upper()!=mycall: return
        if is_sabm(ctrl):
            if self.link and self.link.remote!=src:
                await self.tx(make_frame(src,mycall,DM)); return
            self.link=LinkState(src); await self.tx(make_frame(src,mycall,UA)); self.status('connecting_cms',remote=src)
            self.cms=CmsSession(src,mycall,self.rms['cms_password'],self.rms['frequency_hz'],MODE_CODES.get(self.rms.get('mode','PACKET-1200'),0),self.rms.get('cms_host','cms.winlink.org'),self.rms.get('cms_port',8772))
            try:
                await self.cms.connect(); self.status('connected',remote=src)
                self.cms_task=asyncio.create_task(self.cms_to_rf())
            except Exception as e:
                self.status('cms_error',remote=src,message=str(e)); await self.tx(make_frame(src,mycall,DM)); await self.close_session()
            return
        if not self.link or src!=self.link.remote: return
        if is_disc(ctrl): await self.tx(make_frame(src,mycall,UA)); await self.close_session(); return
        if is_i(ctrl) or is_s(ctrl): self.link.va=nr(ctrl)
        if is_i(ctrl):
            if ns(ctrl)==self.link.vr:
                self.link.vr=(self.link.vr+1)&7
                await self.tx(make_frame(src,mycall,rr(self.link.vr)))
                if self.cms and payload: await self.cms.send(payload)
            else: await self.tx(make_frame(src,mycall,rr(self.link.vr,True)))
    async def run(self):
        if not self.rms.get('enabled'): self.status('disabled'); return
        self.status('starting')
        while True:
            try:
                reader,self.writer=await asyncio.open_connection(self.dw['host'],int(self.dw['port'])); self.status('listening')
                dec=KissDecoder()
                while True:
                    data=await reader.read(4096)
                    if not data: raise ConnectionError('Dire Wolf KISS disconnected')
                    for frame in dec.feed(data): await self.handle(frame)
            except asyncio.CancelledError: break
            except Exception as e: self.status('kiss_error',message=str(e)); await asyncio.sleep(5)

async def main(): await Gateway(load_config()).run()
if __name__=='__main__': asyncio.run(main())
