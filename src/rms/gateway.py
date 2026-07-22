"""KP4PRA native Python Winlink RMS gateway.

Dire Wolf KISS TCP -> minimal AX.25 connected mode -> authenticated CMS stream.
Also serves a raw TCP ("Telnet Winlink") entry point on all interfaces, using
the same CMS-relay logic as the RF path, minus AX.25 framing.
One RF/Telnet session is supported at a time by design for small SBC deployments.
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

    async def _start_new_session(self,remote_call,mycall):
        """Shared by the RF SABM path: cancel any stale relay task/CmsSession
        from a previous session, then open a fresh authenticated CMS session
        for remote_call using the gateway's OWN approved credentials."""
        if self.cms_task and not self.cms_task.done():
            log("session: cancelling stale relay task from a previous session")
            self.cms_task.cancel()
            try:
                await self.cms_task
            except asyncio.CancelledError:
                pass
        if self.cms:
            await self.cms.close()
        self.cms=CmsSession(remote_call,mycall,self.rms['cms_password'],self.rms['frequency_hz'],MODE_CODES.get(self.rms.get('mode','PACKET-1200'),0),self.rms.get('cms_host','cms.winlink.org'),self.rms.get('cms_port',8772))
        await self.cms.connect()

    def _busy_with_other(self,src):
        """True if an RF session is active and it is NOT simply the same
        station reconnecting (which is allowed to reset its own session)."""
        return self.cms is not None and not (self.link and self.link.remote==src)

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
            if self._busy_with_other(src):
                busy_desc = self.link.remote if self.link else "busy"
                log(f"handle: rejecting SABM from {src}, busy with {busy_desc}")
                await self.tx(make_frame(src,mycall,DM|(0x10 if pf_in else 0),response=True)); return
            self.link=LinkState(src)
            await self.tx(make_frame(src,mycall,UA|(0x10 if pf_in else 0),response=True))
            self.status('connecting_cms',remote=src)
            log(f"handle: UA(response,F={pf_in}) sent to {src}, opening CMS session")
            try:
                await self._start_new_session(src,mycall)
                self.status('connected',remote=src)
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

    async def handle_telnet(self,reader,writer):
        # "Network Post Office" access: treat this exactly like a network
        # cable to the real CMS. No local login logic -- we cannot validate
        # an arbitrary connecting station's own Winlink account password
        # ourselves (only the real CMS knows it), so the connecting client
        # performs its ENTIRE secure-login exchange directly against real
        # CMS's actual protocol, byte for byte, through us. This never
        # touches self.cms/self.link (the gateway's OWN authenticated
        # session used for RF), so Telnet and RF sessions are fully
        # independent and do not contend with each other.
        peer=writer.get_extra_info('peername')
        remote_desc=f"{peer[0]}:{peer[1]}" if peer else "telnet client"
        log(f"telnet: connection from {remote_desc}")
        cms_host=self.rms.get('cms_host','cms.winlink.org')
        cms_port=int(self.rms.get('cms_port',8772))
        try:
            cms_reader,cms_writer=await asyncio.open_connection(cms_host,cms_port)
            log(f"telnet: proxying {remote_desc} directly to {cms_host}:{cms_port}")
        except Exception as e:
            log(f"telnet: could not reach CMS for {remote_desc}: {type(e).__name__}: {e}")
            try: writer.close()
            except Exception: pass
            return
        async def cms_to_client():
            try:
                while True:
                    data=await cms_reader.read(1024)
                    if not data:
                        log(f"telnet: CMS closed the stream for {remote_desc}")
                        break
                    log(f"telnet: {len(data)} bytes CMS->{remote_desc}: {data!r}")
                    writer.write(data); await writer.drain()
            except Exception as e:
                log(f"telnet: cms_to_client EXCEPTION for {remote_desc}: {type(e).__name__}: {e}")
        async def client_to_cms():
            try:
                while True:
                    data=await reader.read(1024)
                    if not data:
                        log(f"telnet: {remote_desc} closed the connection")
                        break
                    log(f"telnet: {len(data)} bytes {remote_desc}->CMS: {data!r}")
                    cms_writer.write(data); await cms_writer.drain()
            except Exception as e:
                log(f"telnet: client_to_cms EXCEPTION for {remote_desc}: {type(e).__name__}: {e}")
        try:
            t1=asyncio.create_task(cms_to_client())
            t2=asyncio.create_task(client_to_cms())
            done,pending=await asyncio.wait([t1,t2],return_when=asyncio.FIRST_COMPLETED)
            for p in pending:
                p.cancel()
                try: await p
                except asyncio.CancelledError: pass
        finally:
            try: cms_writer.close()
            except Exception: pass
            try: writer.close()
            except Exception: pass
            log(f"telnet: connection from {remote_desc} closed")

    async def _run_rf(self):
        log(f"run: connecting to Dire Wolf at {self.dw['host']}:{self.dw['port']}")
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
            except Exception as e:
                log(f"run: EXCEPTION {type(e).__name__}: {e}")
                self.status('kiss_error',message=str(e)); await asyncio.sleep(5)

    async def _run_telnet(self):
        port=int(self.rms.get('telnet_port',8772))
        server=await asyncio.start_server(self.handle_telnet,'0.0.0.0',port)
        log(f"run: telnet (Winlink over TCP) listening on 0.0.0.0:{port}")
        async with server:
            await server.serve_forever()

    async def run(self):
        if not self.rms.get('enabled'): self.status('disabled'); return
        self.status('starting')
        tasks=[asyncio.create_task(self._run_rf())]
        if self.rms.get('telnet_enabled',True):
            tasks.append(asyncio.create_task(self._run_telnet()))
        await asyncio.gather(*tasks)

async def main(): await Gateway(load_config()).run()
if __name__=='__main__': asyncio.run(main())
