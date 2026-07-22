"""Winlink CMS gateway login and byte-stream relay."""
import asyncio, hashlib

CMS_SALT=bytes([77,197,101,206,190,249,93,200,51,243,93,237,71,94,239,138,68,108,70,185,225,137,217,16,51,122,193,48,194,195,198,175,172,169,70,84,61,62,104,186,114,52,61,168,66,129,192,208,187,249,232,193,41,113,41,45,240,16,29,228,208,228,61,20])

def challenge_response(challenge:str,password:str)->str:
    # Winlink secure login, per paclink-unix compute_secure_login_response:
    # MD5(challenge + password + 64-byte salt); password is NOT uppercased.
    d=hashlib.md5(challenge.encode('ascii')+password.encode('ascii')+CMS_SALT).digest()
    pr=((d[3]&0x3F)<<24)+(d[2]<<16)+(d[1]<<8)+d[0]
    s=f"{pr:08d}"
    return s[-8:] if len(s)>8 else s

class CmsSession:
    def __init__(self,remote_call,gateway_call,password,frequency_hz,mode,host='cms.winlink.org',port=8772):
        self.remote_call=remote_call.upper(); self.gateway_call=gateway_call.upper(); self.password=password
        self.frequency_hz=int(frequency_hz); self.mode=int(mode); self.host=host; self.port=int(port)
        self.reader=None; self.writer=None; self.ready=False

    async def connect(self):
        self.reader,self.writer=await asyncio.wait_for(asyncio.open_connection(self.host,self.port),20)
        buf=bytearray()
        while not self.ready:
            chunk=await asyncio.wait_for(self.reader.read(512),20)
            if not chunk: raise ConnectionError('CMS closed during login')
            buf.extend(chunk)
            text=buf.decode('latin1','ignore')
            if 'Callsign :' in text:
                self.writer.write(f'{self.remote_call} {self.gateway_call}\r'.encode()); await self.writer.drain(); buf.clear()
            elif ';SQ: ' in text:
                line=next((x for x in text.replace('\n','\r').split('\r') if x.startswith(';SQ: ')),None)
                if line:
                    response=challenge_response(line[5:],self.password)
                    self.writer.write(f';SR: {response} {self.frequency_hz} {self.mode}\r'.encode()); await self.writer.drain(); buf.clear(); self.ready=True
            elif 'Password :' in text:
                self.writer.write(f'CMSTELNET {self.gateway_call} {self.frequency_hz} {self.mode}\r'.encode()); await self.writer.drain(); buf.clear(); self.ready=True
        return bytes(buf)

    async def send(self,data:bytes):
        if self.writer is None: raise ConnectionError('CMS not connected')
        self.writer.write(data); await self.writer.drain()
    async def recv(self,n=1024): return await self.reader.read(n)
    async def close(self):
        if self.writer:
            self.writer.close()
            try: await self.writer.wait_closed()
            except Exception: pass
