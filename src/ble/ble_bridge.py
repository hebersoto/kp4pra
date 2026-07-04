"""
KP4PRA TNC - BLE KISS Bridge
Implements BLE peripheral / GATT server compatible with KISS over BLE specification.

Service UUID:  00000001-ba2a-46c9-ae49-01b0961f68bb
TX char (app->TNC): 00000002-ba2a-46c9-ae49-01b0961f68bb
RX char (TNC->app): 00000003-ba2a-46c9-ae49-01b0961f68bb

Bridges raw KISS bytes between iPhone aprs.fi and Dire Wolf TCP 127.0.0.1:8001.
No pairing required from iOS side.
No persistent writes on connect/disconnect.
No KISS traffic saved to disk.
"""

import asyncio
import os
import sys
import signal
import socket
import time
from typing import Optional

from dbus_next.aio import MessageBus
from dbus_next import BusType, Variant

from common.config import load_config
from common.runtime_status import write_status, clear_status

# ── KISS over BLE UUIDs ──────────────────────────────────────────────────────
KISS_BLE_SERVICE_UUID   = "00000001-ba2a-46c9-ae49-01b0961f68bb"
KISS_BLE_TX_UUID        = "00000002-ba2a-46c9-ae49-01b0961f68bb"  # app writes
KISS_BLE_RX_UUID        = "00000003-ba2a-46c9-ae49-01b0961f68bb"  # TNC notifies

BLE_MTU = 512      # negotiated MTU limit; chunk outgoing data to BLE_CHUNK_SIZE
BLE_CHUNK_SIZE = 500
READ_CHUNK = 4096
RECONNECT_DELAY = 3.0

# D-Bus BlueZ paths
BLUEZ_SERVICE = "org.bluez"
DBUS_OM_IFACE = "org.freedesktop.DBus.ObjectManager"
DBUS_PROP_IFACE = "org.freedesktop.DBus.Properties"
GATT_MGR_IFACE = "org.bluez.GattManager1"
GATT_SVC_IFACE = "org.bluez.GattService1"
GATT_CHAR_IFACE = "org.bluez.GattCharacteristic1"
LE_ADV_MGR_IFACE = "org.bluez.LEAdvertisingManager1"
LE_ADV_IFACE = "org.bluez.LEAdvertisement1"


# ─────────────────────────────────────────────────────────────────────────────
# BLE GATT server using dbus-next
# ─────────────────────────────────────────────────────────────────────────────

class BLEKISSBridge:
    """
    BLE GATT peripheral that bridges KISS over BLE to Dire Wolf KISS TCP.
    Uses dbus-next to talk to BlueZ.
    """

    def __init__(self, config: dict):
        self.config = config
        self.dw_host = config["direwolf"]["host"]
        self.dw_port = config["direwolf"]["port"]
        self.device_name = config["ble"]["device_name"]
        self.verbose = config["debug"].get("verbose_stdout", False)
        self._running = False
        self._ble_client_connected = False
        self._dw_sock: Optional[socket.socket] = None
        self._bus: Optional[MessageBus] = None
        # Asyncio queue for data coming from Dire Wolf -> BLE client
        self._dw_to_ble_queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        # Asyncio queue for data from BLE client -> Dire Wolf
        self._ble_to_dw_queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        # BlueZ notify callback will be set by GATT service object
        self._notify_callback = None

    def _log(self, msg: str):
        if self.verbose:
            print(f"[KP4PRA TNC BLE] {msg}", flush=True)

    def _status(self):
        write_status("ble", {
            "running": self._running,
            "client_connected": self._ble_client_connected,
            "device_name": self.device_name,
            "service_uuid": KISS_BLE_SERVICE_UUID,
            "dw_host": self.dw_host,
            "dw_port": self.dw_port,
        })

    async def start(self):
        """Start BLE KISS bridge."""
        self._running = True
        self._status()
        print(f"[KP4PRA TNC] BLE KISS bridge starting, advertising as '{self.device_name}'", flush=True)

        try:
            self._bus = await MessageBus(bus_type=BusType.SYSTEM).connect()
        except Exception as e:
            print(f"[KP4PRA TNC] BLE: Cannot connect to D-Bus system bus: {e}", flush=True)
            sys.exit(1)

        # Register GATT app and advertisement, then run bridge loops
        try:
            await self._register_gatt_app()
            await self._start_advertisement()
        except Exception as e:
            print(f"[KP4PRA TNC] BLE: GATT/advertisement registration failed: {e}", flush=True)
            print("[KP4PRA TNC] BLE: Ensure BlueZ >= 5.50 and bluetooth service is running.", flush=True)
            sys.exit(1)

        print(f"[KP4PRA TNC] BLE KISS advertising as '{self.device_name}'", flush=True)

        await asyncio.gather(
            self._direwolf_loop(),
            self._ble_to_dw_loop(),
        )

    async def _direwolf_loop(self):
        """Connect to Dire Wolf and shuttle bytes to/from BLE queue."""
        while self._running:
            try:
                self._dw_sock = socket.create_connection(
                    (self.dw_host, self.dw_port), timeout=5.0
                )
                self._dw_sock.settimeout(None)  # clear connect timeout for recv/send
                self._log(f"Connected to Dire Wolf {self.dw_host}:{self.dw_port}")
                self._status()

                loop = asyncio.get_event_loop()
                # Run blocking recv in executor
                while self._running:
                    data = await loop.run_in_executor(
                        None, self._dw_sock.recv, READ_CHUNK
                    )
                    if not data:
                        break
                    # Chunk data to BLE MTU size and queue for notification
                    for i in range(0, len(data), BLE_CHUNK_SIZE):
                        chunk = data[i:i + BLE_CHUNK_SIZE]
                        try:
                            self._dw_to_ble_queue.put_nowait(chunk)
                        except asyncio.QueueFull:
                            self._log("BLE TX queue full, dropping chunk")
            except Exception as e:
                self._log(f"Dire Wolf connection lost: {e}")
            finally:
                if self._dw_sock:
                    try:
                        self._dw_sock.close()
                    except Exception:
                        pass
                    self._dw_sock = None
                self._status()
            if self._running:
                self._log(f"Reconnecting to Dire Wolf in {RECONNECT_DELAY}s...")
                await asyncio.sleep(RECONNECT_DELAY)

    async def _ble_to_dw_loop(self):
        """Forward bytes from BLE queue to Dire Wolf socket."""
        while self._running:
            try:
                data = await asyncio.wait_for(self._ble_to_dw_queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            if self._dw_sock:
                try:
                    loop = asyncio.get_event_loop()
                    await loop.run_in_executor(None, self._dw_sock.sendall, data)
                except Exception as e:
                    self._log(f"Send to Dire Wolf failed: {e}")

    async def _register_gatt_app(self):
        """
        Register a minimal GATT application with BlueZ using dbus-next.
        Due to complexity of full GATT object manager registration, this
        implementation uses the BlueZ GATT application D-Bus API.
        In a full deployment, use the provided gatt_app.py helper.
        """
        # Signal bridge is ready; actual GATT object manager registration
        # is handled by gatt_app.py (see companion module) for production.
        # Here we record that GATT setup was attempted.
        self._log("GATT application registration placeholder - see gatt_app.py")

    async def _start_advertisement(self):
        """Start BLE advertisement via BlueZ LEAdvertisementManager."""
        self._log("BLE advertisement placeholder - see gatt_app.py")

    def on_ble_write(self, data: bytes):
        """Called by GATT TX characteristic write handler with app->TNC data."""
        try:
            self._ble_to_dw_queue.put_nowait(data)
        except asyncio.QueueFull:
            self._log("BLE RX queue full, dropping data")

    def get_next_notification(self) -> Optional[bytes]:
        """Called by GATT RX characteristic to get next notification chunk."""
        try:
            return self._dw_to_ble_queue.get_nowait()
        except asyncio.QueueEmpty:
            return None

    def stop(self, *args):
        print("[KP4PRA TNC] BLE KISS bridge stopping...", flush=True)
        self._running = False
        clear_status("ble")


# ─────────────────────────────────────────────────────────────────────────────
# Full BlueZ GATT app using dbus-next ServiceInterface
# This is the production implementation
# ─────────────────────────────────────────────────────────────────────────────

GATT_APP_PATH = "/com/kp4praTnc/GattApp"
GATT_SVC_PATH = "/com/kp4praTnc/GattApp/Service0"
GATT_TX_PATH  = "/com/kp4praTnc/GattApp/Service0/Char0"  # app writes KISS
GATT_RX_PATH  = "/com/kp4praTnc/GattApp/Service0/Char1"  # TNC notifies KISS
ADV_PATH      = "/com/kp4praTnc/Advertisement0"

GATT_APP_XML = """
<!DOCTYPE node PUBLIC "-//freedesktop//DTD D-BUS Object Introspection 1.0//EN"
  "http://www.freedesktop.org/standards/dbus/1.0/introspect.dtd">
<node>
  <interface name="org.freedesktop.DBus.ObjectManager">
    <method name="GetManagedObjects">
      <arg type="a{oa{sa{sv}}}" direction="out"/>
    </method>
  </interface>
</node>
"""


async def run_ble_bridge(config: dict):
    """
    Full BLE KISS bridge using BlueZ D-Bus GATT API.
    Registers GATT service, characteristics, and BLE advertisement.
    Bridges KISS frames between BLE client and Dire Wolf TCP.
    """
    from dbus_next.aio import MessageBus
    from dbus_next import BusType, Variant
    from dbus_next.service import ServiceInterface, method, dbus_property
    from dbus_next.constants import PropertyAccess

    dw_host = config["direwolf"]["host"]
    dw_port = config["direwolf"]["port"]
    device_name = config["ble"]["device_name"]
    verbose = config["debug"].get("verbose_stdout", False)

    def log(msg):
        if verbose:
            print(f"[KP4PRA TNC BLE] {msg}", flush=True)

    bus = await MessageBus(bus_type=BusType.SYSTEM).connect()

    # Queues for async bridging
    ble_to_dw = asyncio.Queue(maxsize=256)
    dw_to_ble = asyncio.Queue(maxsize=256)
    notify_enabled = [False]
    ble_connected = [False]

    # ── GATT Characteristic: TX (app writes KISS to TNC) ────────────────────
    class TXCharacteristic(ServiceInterface):
        def __init__(self):
            super().__init__(GATT_CHAR_IFACE)

        @method()
        def WriteValue(self, value: 'ay', options: 'a{sv}'):
            data = bytes(value)
            log(f"BLE RX (app->TNC): {len(data)} bytes")
            try:
                ble_to_dw.put_nowait(data)
            except asyncio.QueueFull:
                log("ble_to_dw queue full, dropping")

        @method()
        def GetManagedObjects(self) -> 'a{oa{sa{sv}}}':
            return {}

        @dbus_property(access=PropertyAccess.READ)
        def UUID(self) -> 's':
            return KISS_BLE_TX_UUID

        @dbus_property(access=PropertyAccess.READ)
        def Service(self) -> 'o':
            return GATT_SVC_PATH

        @dbus_property(access=PropertyAccess.READ)
        def Flags(self) -> 'as':
            return ['write', 'write-without-response']

        @dbus_property(access=PropertyAccess.READ)
        def Value(self) -> 'ay':
            return bytes()

    # ── GATT Characteristic: RX (TNC notifies KISS to app) ──────────────────
    class RXCharacteristic(ServiceInterface):
        def __init__(self):
            super().__init__(GATT_CHAR_IFACE)
            self._notifying = False
            self._value = b""

        @method()
        def StartNotify(self):
            self._notifying = True
            notify_enabled[0] = True
            ble_connected[0] = True
            write_status("ble", {
                "running": True,
                "client_connected": True,
                "device_name": device_name,
                "service_uuid": KISS_BLE_SERVICE_UUID,
            })
            log("BLE client started notifications (connected)")

        @method()
        def StopNotify(self):
            self._notifying = False
            notify_enabled[0] = False
            ble_connected[0] = False
            write_status("ble", {
                "running": True,
                "client_connected": False,
                "device_name": device_name,
                "service_uuid": KISS_BLE_SERVICE_UUID,
            })
            log("BLE client stopped notifications (disconnected)")

        @method()
        def ReadValue(self, options: 'a{sv}') -> 'ay':
            return bytes()

        @dbus_property(access=PropertyAccess.READ)
        def UUID(self) -> 's':
            return KISS_BLE_RX_UUID

        @dbus_property(access=PropertyAccess.READ)
        def Service(self) -> 'o':
            return GATT_SVC_PATH

        @dbus_property(access=PropertyAccess.READ)
        def Flags(self) -> 'as':
            return ['notify', 'read']

        @dbus_property(access=PropertyAccess.READ)
        def Value(self) -> 'ay':
            return self._value

    # ── GATT Service ─────────────────────────────────────────────────────────
    class KISSGattService(ServiceInterface):
        def __init__(self):
            super().__init__(GATT_SVC_IFACE)

        @dbus_property(access=PropertyAccess.READ)
        def UUID(self) -> 's':
            return KISS_BLE_SERVICE_UUID

        @dbus_property(access=PropertyAccess.READ)
        def Primary(self) -> 'b':
            return True

        @dbus_property(access=PropertyAccess.READ)
        def Characteristics(self) -> 'ao':
            return [GATT_TX_PATH, GATT_RX_PATH]

    # ── BLE Advertisement ────────────────────────────────────────────────────
    class KISSAdvertisement(ServiceInterface):
        def __init__(self):
            super().__init__(LE_ADV_IFACE)

        @method()
        def Release(self):
            log("Advertisement released")

        @dbus_property(access=PropertyAccess.READ)
        def Type(self) -> 's':
            return 'peripheral'

        @dbus_property(access=PropertyAccess.READ)
        def LocalName(self) -> 's':
            return device_name

        @dbus_property(access=PropertyAccess.READ)
        def ServiceUUIDs(self) -> 'as':
            return [KISS_BLE_SERVICE_UUID]

        @dbus_property(access=PropertyAccess.READ)
        def Includes(self) -> 'as':
            return ['tx-power']

    # ── Object Manager ────────────────────────────────────────────────────────
    class GattApplication(ServiceInterface):
        def __init__(self):
            super().__init__(DBUS_OM_IFACE)

        @method()
        def GetManagedObjects(self) -> 'a{oa{sa{sv}}}':
            return {
                GATT_SVC_PATH: {
                    GATT_SVC_IFACE: {
                        'UUID': Variant('s', KISS_BLE_SERVICE_UUID),
                        'Primary': Variant('b', True),
                        'Characteristics': Variant('ao', [GATT_TX_PATH, GATT_RX_PATH]),
                    }
                },
                GATT_TX_PATH: {
                    GATT_CHAR_IFACE: {
                        'UUID': Variant('s', KISS_BLE_TX_UUID),
                        'Service': Variant('o', GATT_SVC_PATH),
                        'Flags': Variant('as', ['write', 'write-without-response']),
                        'Value': Variant('ay', b''),
                    }
                },
                GATT_RX_PATH: {
                    GATT_CHAR_IFACE: {
                        'UUID': Variant('s', KISS_BLE_RX_UUID),
                        'Service': Variant('o', GATT_SVC_PATH),
                        'Flags': Variant('as', ['notify', 'read']),
                        'Value': Variant('ay', b''),
                    }
                },
            }

    # Export D-Bus objects
    gatt_app = GattApplication()
    kiss_svc = KISSGattService()
    tx_char = TXCharacteristic()
    rx_char = RXCharacteristic()
    adv = KISSAdvertisement()

    bus.export(GATT_APP_PATH, gatt_app)
    bus.export(GATT_SVC_PATH, kiss_svc)
    bus.export(GATT_TX_PATH, tx_char)
    bus.export(GATT_RX_PATH, rx_char)
    bus.export(ADV_PATH, adv)

    # Find BlueZ adapter
    bluez = bus.get_proxy_object(BLUEZ_SERVICE, '/', await bus.introspect(BLUEZ_SERVICE, '/'))
    om = bluez.get_interface(DBUS_OM_IFACE)
    objects = await om.call_get_managed_objects()

    adapter_path = None
    for path, ifaces in objects.items():
        if GATT_MGR_IFACE in ifaces:
            adapter_path = path
            break

    if not adapter_path:
        print("[KP4PRA TNC] BLE: No BlueZ adapter with GattManager1 found", flush=True)
        sys.exit(1)

    log(f"Using adapter: {adapter_path}")

    # Register GATT application
    adapter = bus.get_proxy_object(
        BLUEZ_SERVICE, adapter_path,
        await bus.introspect(BLUEZ_SERVICE, adapter_path)
    )
    gatt_mgr = adapter.get_interface(GATT_MGR_IFACE)
    await gatt_mgr.call_register_application(GATT_APP_PATH, {})
    log("GATT application registered")

    # Start advertisement
    adv_mgr = adapter.get_interface(LE_ADV_MGR_IFACE)
    await adv_mgr.call_register_advertisement(ADV_PATH, {})
    log(f"BLE advertisement started as '{device_name}'")

    print(f"[KP4PRA TNC] BLE KISS advertising as '{device_name}'", flush=True)
    write_status("ble", {
        "running": True,
        "client_connected": False,
        "device_name": device_name,
        "service_uuid": KISS_BLE_SERVICE_UUID,
        "dw_host": dw_host,
        "dw_port": dw_port,
    })

    # ── Dire Wolf connection loop ─────────────────────────────────────────────
    async def direwolf_loop():
        while True:
            dw_sock = None
            try:
                dw_sock = socket.create_connection((dw_host, dw_port), timeout=5.0)
                dw_sock.settimeout(None)  # clear connect timeout for recv/send
                log(f"Connected to Dire Wolf {dw_host}:{dw_port}")
                loop = asyncio.get_event_loop()

                async def recv_from_dw():
                    while True:
                        data = await loop.run_in_executor(None, dw_sock.recv, 4096)
                        if not data:
                            break
                        if notify_enabled[0]:
                            for i in range(0, len(data), BLE_CHUNK_SIZE):
                                chunk = data[i:i + BLE_CHUNK_SIZE]
                                try:
                                    dw_to_ble.put_nowait(chunk)
                                except asyncio.QueueFull:
                                    log("dw_to_ble queue full, dropping")

                async def send_to_dw():
                    while True:
                        try:
                            data = await asyncio.wait_for(ble_to_dw.get(), timeout=1.0)
                        except asyncio.TimeoutError:
                            continue
                        await loop.run_in_executor(None, dw_sock.sendall, data)

                await asyncio.gather(recv_from_dw(), send_to_dw())
            except Exception as e:
                log(f"Dire Wolf error: {e}")
            finally:
                if dw_sock:
                    try:
                        dw_sock.close()
                    except Exception:
                        pass
            log(f"Reconnecting to Dire Wolf in {RECONNECT_DELAY}s...")
            await asyncio.sleep(RECONNECT_DELAY)

    # ── Notify loop: send queued BLE data to connected client ─────────────────
    async def notify_loop():
        while True:
            try:
                chunk = await asyncio.wait_for(dw_to_ble.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            if notify_enabled[0]:
                # Emit PropertiesChanged on OUR exported RX characteristic.
                # BlueZ relays this to the BLE client as a GATT notification.
                try:
                    rx_char._value = bytes(chunk)
                    rx_char.emit_properties_changed({'Value': bytes(chunk)})
                    log(f"BLE TX (TNC->app): {len(chunk)} bytes")
                except Exception as e:
                    log(f"BLE notify error: {e}")

    await asyncio.gather(direwolf_loop(), notify_loop())


def main():
    config = load_config()
    loop = asyncio.get_event_loop()

    def handle_signal(*args):
        print("[KP4PRA TNC] BLE KISS bridge stopping...", flush=True)
        clear_status("ble")
        # Exit immediately: executor threads blocked in socket recv would
        # otherwise keep the process alive until systemd's SIGKILL timeout.
        os._exit(0)

    signal.signal(signal.SIGTERM, handle_signal)
    signal.signal(signal.SIGINT, handle_signal)

    try:
        loop.run_until_complete(run_ble_bridge(config))
    except KeyboardInterrupt:
        pass
    finally:
        clear_status("ble")


if __name__ == "__main__":
    main()
