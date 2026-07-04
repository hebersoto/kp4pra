"""
KP4PRA TNC - RFCOMM KISS Bridge
No PyBluez. Uses Python built-in socket with AF_BLUETOOTH/BTPROTO_RFCOMM.
SDP registration via sdptool (bluez-tools).
"""
import os, sys, signal, socket, subprocess, time, threading
from typing import Optional
from common.config import load_config
from common.runtime_status import write_status, clear_status

PRODUCT_NAME = "KP4PRA TNC"
READ_CHUNK = 4096
RECONNECT_DELAY = 3.0
AF_BLUETOOTH = 31
BTPROTO_RFCOMM = 3

def _advertise_sdp(channel: int):
    try:
        r = subprocess.run(["sdptool", "add", "--channel", str(channel), "SP"],
                           capture_output=True, timeout=10)
        if r.returncode == 0:
            print(f"[KP4PRA TNC] SDP SP record registered on channel {channel}", flush=True)
        else:
            print(f"[KP4PRA TNC] sdptool: {r.stderr.decode(errors='replace').strip()}", flush=True)
    except FileNotFoundError:
        print("[KP4PRA TNC] sdptool not found - install bluez-tools", flush=True)
    except Exception as e:
        print(f"[KP4PRA TNC] SDP error: {e}", flush=True)

def _set_bt_name(name: str):
    try:
        subprocess.run(["bluetoothctl", "system-alias", name],
                       capture_output=True, timeout=5)
    except Exception:
        pass

class RFCOMMBridge:
    def __init__(self, config: dict):
        self.dw_host = config["direwolf"]["host"]
        self.dw_port = config["direwolf"]["port"]
        self.bt_name = config["rfcomm"]["device_name"]
        self.channel = config["rfcomm"].get("channel", 1)
        self.verbose = config["debug"].get("verbose_stdout", False)
        self._running = False
        self._client_sock = None
        self._server_sock = None

    def _log(self, msg):
        if self.verbose:
            print(f"[KP4PRA TNC RFCOMM] {msg}", flush=True)

    def _status(self, connected, client_addr=""):
        write_status("rfcomm", {
            "running": self._running, "connected": connected,
            "client_addr": client_addr, "dw_host": self.dw_host,
            "dw_port": self.dw_port, "bt_name": self.bt_name,
            "channel": self.channel,
        })

    def start(self):
        self._running = True
        self._status(False)
        _set_bt_name(self.bt_name)
        _advertise_sdp(self.channel)
        print(f"[KP4PRA TNC] RFCOMM bridge starting as '{self.bt_name}' channel {self.channel}", flush=True)
        try:
            self._server_sock = socket.socket(AF_BLUETOOTH, socket.SOCK_STREAM, BTPROTO_RFCOMM)
            self._server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._server_sock.bind(("00:00:00:00:00:00", self.channel))
            self._server_sock.listen(2)
            print(f"[KP4PRA TNC] RFCOMM listening on channel {self.channel}", flush=True)
        except Exception as e:
            print(f"[KP4PRA TNC] RFCOMM setup failed: {e}", flush=True)
            sys.exit(1)

        while self._running:
            try:
                self._server_sock.settimeout(1.0)
                try:
                    client_sock, client_info = self._server_sock.accept()
                except socket.timeout:
                    continue
                client_addr = client_info[0] if client_info else "unknown"
                print(f"[KP4PRA TNC] RFCOMM connected: {client_addr}", flush=True)
                self._client_sock = client_sock
                self._status(True, client_addr)
                try:
                    self._handle_client(client_sock)
                except Exception as e:
                    self._log(f"Client error: {e}")
                finally:
                    try:
                        client_sock.close()
                    except Exception:
                        pass
                    self._client_sock = None
                    print(f"[KP4PRA TNC] RFCOMM disconnected: {client_addr}", flush=True)
                    self._status(False)
            except Exception as e:
                if self._running:
                    print(f"[KP4PRA TNC] RFCOMM loop error: {e}", flush=True)
                time.sleep(RECONNECT_DELAY)
        self._cleanup()

    def _handle_client(self, client_sock):
        """
        Bridge raw KISS bytes between RFCOMM client and Dire Wolf TCP.
        The Dire Wolf connection auto-reconnects without dropping the
        RFCOMM client. Only an RFCOMM-side disconnect ends the session.
        """
        stop_event = threading.Event()
        dw_lock = threading.Lock()
        dw_sock_holder = [None]

        # Blocking reads on the RFCOMM side, no artificial timeout
        client_sock.settimeout(None)

        def connect_dw():
            """(Re)connect to Dire Wolf. Returns socket or None if stopping."""
            while not stop_event.is_set():
                try:
                    s = socket.create_connection((self.dw_host, self.dw_port), timeout=5.0)
                    s.settimeout(None)  # CRITICAL: clear connect timeout for recv/send
                    print(f"[KP4PRA TNC] Connected to Dire Wolf {self.dw_host}:{self.dw_port}", flush=True)
                    return s
                except Exception as e:
                    self._log(f"Dire Wolf connect failed: {e}, retrying")
                    stop_event.wait(RECONNECT_DELAY)
            return None

        with dw_lock:
            dw_sock_holder[0] = connect_dw()
        if dw_sock_holder[0] is None:
            return

        def r2d():
            """RFCOMM -> Dire Wolf. RFCOMM disconnect ends the session."""
            try:
                while not stop_event.is_set():
                    data = client_sock.recv(READ_CHUNK)
                    if not data:
                        break  # RFCOMM client disconnected
                    while not stop_event.is_set():
                        with dw_lock:
                            s = dw_sock_holder[0]
                        if s is None:
                            time.sleep(0.2)
                            continue
                        try:
                            s.sendall(data)
                            break
                        except Exception:
                            # DW died mid-send; reader thread will reconnect
                            time.sleep(0.2)
            except Exception as e:
                self._log(f"RFCOMM read error: {e}")
            finally:
                stop_event.set()

        def d2r():
            """Dire Wolf -> RFCOMM. DW disconnect triggers reconnect, not teardown."""
            while not stop_event.is_set():
                with dw_lock:
                    s = dw_sock_holder[0]
                if s is None:
                    time.sleep(0.2)
                    continue
                try:
                    data = s.recv(READ_CHUNK)
                except Exception as e:
                    data = b""
                if not data:
                    # Dire Wolf closed or errored - reconnect, keep RFCOMM alive
                    print("[KP4PRA TNC] Dire Wolf connection lost, reconnecting...", flush=True)
                    try:
                        s.close()
                    except Exception:
                        pass
                    with dw_lock:
                        dw_sock_holder[0] = None
                    new_s = connect_dw()
                    if new_s is None:
                        break
                    with dw_lock:
                        dw_sock_holder[0] = new_s
                    continue
                try:
                    client_sock.sendall(data)
                except Exception as e:
                    self._log(f"RFCOMM write error: {e}")
                    stop_event.set()
                    break

        t1 = threading.Thread(target=r2d, daemon=True)
        t2 = threading.Thread(target=d2r, daemon=True)
        t1.start(); t2.start()
        t1.join(); t2.join()

        with dw_lock:
            if dw_sock_holder[0]:
                try:
                    dw_sock_holder[0].close()
                except Exception:
                    pass

    def stop(self, *args):
        print("[KP4PRA TNC] RFCOMM stopping...", flush=True)
        self._running = False
        self._cleanup()

    def _cleanup(self):
        clear_status("rfcomm")
        for s in [self._client_sock, self._server_sock]:
            if s:
                try:
                    s.close()
                except Exception:
                    pass

def main():
    config = load_config()
    bridge = RFCOMMBridge(config)
    signal.signal(signal.SIGTERM, bridge.stop)
    signal.signal(signal.SIGINT, bridge.stop)
    bridge.start()

if __name__ == "__main__":
    main()
