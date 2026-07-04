"""
KP4PRA TNC - Dire Wolf KISS TCP Client
Connects to Dire Wolf KISS TCP server (default 127.0.0.1:8001).
Passes raw binary KISS frames only. No decoding. No logging to disk.
Supports reconnect on Dire Wolf restart.
"""

import asyncio
import time
from typing import Optional, Callable, Awaitable

KISS_FEND = 0xC0
RECONNECT_DELAY = 3.0   # seconds between reconnect attempts
READ_CHUNK = 4096


class DireWolfKissClient:
    """
    Asyncio-based KISS TCP client for Dire Wolf.
    - Connects to host:port
    - Forwards raw bytes from the bridge in both directions
    - Reconnects automatically if Dire Wolf restarts
    - Never writes to disk
    """

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8001,
        on_data: Optional[Callable[[bytes], Awaitable[None]]] = None,
        verbose: bool = False,
    ):
        self.host = host
        self.port = port
        self.on_data = on_data   # called with raw KISS bytes from Dire Wolf
        self.verbose = verbose
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._connected = False
        self._running = False
        self._send_queue: asyncio.Queue = asyncio.Queue()
        self._lock = asyncio.Lock()

    @property
    def connected(self) -> bool:
        return self._connected

    async def start(self):
        """Start the connection loop (runs forever until stop() is called)."""
        self._running = True
        await asyncio.gather(
            self._connection_loop(),
            self._send_loop(),
        )

    async def stop(self):
        """Stop the client."""
        self._running = False
        await self._disconnect()

    async def send(self, data: bytes):
        """Queue raw KISS bytes to be sent to Dire Wolf."""
        await self._send_queue.put(data)

    async def _connect(self) -> bool:
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=5.0,
            )
            self._reader = reader
            self._writer = writer
            self._connected = True
            if self.verbose:
                print(f"[KP4PRA TNC] Connected to Dire Wolf KISS TCP {self.host}:{self.port}", flush=True)
            return True
        except Exception as e:
            if self.verbose:
                print(f"[KP4PRA TNC] Dire Wolf connect failed: {e}", flush=True)
            return False

    async def _disconnect(self):
        self._connected = False
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        self._reader = None
        self._writer = None

    async def _connection_loop(self):
        """Maintain connection to Dire Wolf; reconnect if lost."""
        while self._running:
            if not await self._connect():
                await asyncio.sleep(RECONNECT_DELAY)
                continue
            try:
                await self._receive_loop()
            except Exception as e:
                if self.verbose:
                    print(f"[KP4PRA TNC] Dire Wolf receive error: {e}", flush=True)
            await self._disconnect()
            if self._running:
                if self.verbose:
                    print(f"[KP4PRA TNC] Reconnecting to Dire Wolf in {RECONNECT_DELAY}s...", flush=True)
                await asyncio.sleep(RECONNECT_DELAY)

    async def _receive_loop(self):
        """Read raw KISS bytes from Dire Wolf and pass to on_data callback."""
        while self._running and self._reader:
            data = await self._reader.read(READ_CHUNK)
            if not data:
                break   # connection closed
            if self.on_data:
                await self.on_data(data)

    async def _send_loop(self):
        """Drain the send queue and write to Dire Wolf."""
        while self._running:
            try:
                data = await asyncio.wait_for(self._send_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            if self._connected and self._writer:
                try:
                    self._writer.write(data)
                    await self._writer.drain()
                except Exception as e:
                    if self.verbose:
                        print(f"[KP4PRA TNC] Dire Wolf send error: {e}", flush=True)
                    # Data is dropped if not connected; no buffering to disk
