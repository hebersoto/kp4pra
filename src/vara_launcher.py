"""
KP4PRA TNC - VARA launcher (varanny-compatible) for Xara.

RadioMail (iOS) and other VARA clients find VARA modems on the network the
way varanny (https://github.com/islandmagic/varanny) announces them, and
ask varanny to start the modem for a session. This service does the same
for Xara on KP4PRA TNC, so RadioMail finds the TNC by itself:

  DNS-SD (Avahi):  _vara-modem._tcp (and the older _varafm-modem._tcp /
                   _varahf-modem._tcp), port = VARA command port (8300),
                   TXT "type=fm;" "launchport=8273;"
  TCP 8273:        start <name>  -> switch the TNC to Xara (if it is not
                                    already), "OK" once Xara listens
                   stop          -> "OK", end of session
                   list / version / config, as varanny
                   monitor       -> not available ("ERROR ...")

When the session ends (stop, or the client closes the connection) and this
service was the one that switched to Xara, the TNC goes back to Dire Wolf.
If Xara was already chosen on the Services page, it stays on.

Settings (config.yaml, xara section): network (false = no announcement,
local clients only), auto_switch (false = "start" only works while Xara is
already on), port and mode (announced port and fm/hf type).
"""

import asyncio
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from common.config import reload_config          # noqa: E402
from common import xara_conf                     # noqa: E402

VERSION = "kp4pra-vara-launcher 1"
LAUNCH_PORT = int(os.environ.get("KP4PRA_VARA_LAUNCH_PORT", "8273"))
XARA_TOOL = "/usr/local/bin/kp4pra-xara"
XARA_BIN = "/usr/local/bin/xara"
XARA_UNIT = "xara@kp4pra.service"
READY_WAIT_S = 30


def log(msg):
    print(f"vara-launcher: {msg}", flush=True)


# ---------------------------------------------------------------------------
# What to announce

def modem_info(cfg: dict) -> dict:
    x = xara_conf.settings(cfg)
    band = "hf" if str(x.get("mode", "")).startswith("hf") else "fm"
    call = xara_conf.xara_call(cfg) or "KP4PRA"
    return {
        "name": f"{call} VARA {band.upper()}",
        "type": band,
        "port": int(x.get("port") or 8300),
        "network": bool(x.get("network", True)),
        "auto_switch": bool(x.get("auto_switch", True)),
    }


def txt_records(info: dict) -> list:
    # varanny: one TXT string per option, each ending in ";"
    return [f"launchport={LAUNCH_PORT};", f"type={info['type']};"]


def publish_commands(info: dict) -> list:
    txt = txt_records(info)
    legacy = "_varafm-modem._tcp" if info["type"] == "fm" else "_varahf-modem._tcp"
    return [["avahi-publish", "-s", info["name"], st, str(info["port"]), *txt]
            for st in ("_vara-modem._tcp", legacy)]


class Announcer:
    """Keeps avahi-publish running for the current settings."""

    def __init__(self):
        self.procs = []
        self.key = None

    def stop(self):
        for p in self.procs:
            if p.poll() is None:
                p.terminate()
        for p in self.procs:
            try:
                p.wait(5)
            except subprocess.TimeoutExpired:
                p.kill()
        self.procs = []

    def update(self, info: dict, installed: bool):
        want = installed and info["network"]
        key = (info["name"], info["type"], info["port"]) if want else None
        alive = all(p.poll() is None for p in self.procs)
        if key == self.key and alive:
            return
        self.stop()
        self.key = key
        if not want:
            log("not announcing (Xara not installed, or network access off)")
            return
        for cmd in publish_commands(info):
            try:
                self.procs.append(subprocess.Popen(cmd, stdout=subprocess.DEVNULL,
                                                   stderr=subprocess.DEVNULL))
            except FileNotFoundError:
                log("avahi-publish not found (install avahi-utils) - not announcing")
                return
        log(f"announcing '{info['name']}' (_vara-modem._tcp, port {info['port']}, "
            f"type {info['type']}, launchport {LAUNCH_PORT})")


# ---------------------------------------------------------------------------
# Talking to the rest of KP4PRA (replaceable in tests)

def port_listening(port: int) -> bool:
    """True when something listens on TCP `port` (from /proc, no connect:
    VARA programs do not like probe connections)."""
    want = f":{port:04X}"
    for f in ("/proc/net/tcp", "/proc/net/tcp6"):
        try:
            with open(f) as fh:
                next(fh, None)
                for line in fh:
                    parts = line.split()
                    if len(parts) > 3 and parts[1].endswith(want) and parts[3] == "0A":
                        return True
        except OSError:
            pass
    return False


def xara_running() -> bool:
    return subprocess.run(["systemctl", "is-active", "--quiet", XARA_UNIT]).returncode == 0


async def switch(modem: str) -> tuple:
    proc = await asyncio.create_subprocess_exec(
        "sudo", "-n", XARA_TOOL, "modem", modem,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), 120)
    except asyncio.TimeoutError:
        proc.kill()
        return False, "timed out"
    return proc.returncode == 0, out.decode(errors="replace").strip()


class Hooks:
    installed = staticmethod(lambda: os.path.exists(XARA_BIN))
    running = staticmethod(xara_running)
    listening = staticmethod(port_listening)
    switch = staticmethod(switch)
    config = staticmethod(reload_config)


# ---------------------------------------------------------------------------
# varanny protocol

class Launcher:
    def __init__(self, hooks=Hooks):
        self.h = hooks
        self.busy = False          # one session at a time, as varanny

    async def wait_ready(self, port: int) -> bool:
        for _ in range(READY_WAIT_S * 2):
            if self.h.listening(port):
                return True
            await asyncio.sleep(0.5)
        return False

    async def command(self, cmd, info, send, state):
        """One varanny command. Returns "close" to end the connection."""
        word = cmd.split(" ")[0]
        if word == "start":
            if state["session"]:
                send("OK\n")
                return None
            if self.busy:
                send(f"ERROR modem {info['name']} is already running\n")
                return None
            if not self.h.installed():
                send("ERROR Xara is not installed on this TNC\n")
                return "close"
            self.busy = state["session"] = True
            if not self.h.running():
                if not info["auto_switch"]:
                    send("ERROR Xara is not on - choose Xara on the KP4PRA Services page\n")
                    return "close"
                log("switching the TNC to Xara for this session")
                ok, out = await self.h.switch("xara")
                if not ok:
                    send("ERROR " + (out.splitlines()[-1] if out else "Xara did not start") + "\n")
                    return "close"
                state["switched"] = True
            if not await self.wait_ready(info["port"]):
                send("ERROR Xara is not listening on port %d\n" % info["port"])
                return "close"
            send("OK\n")
        elif word == "monitor":
            send("ERROR audio monitor is not available on KP4PRA TNC\n")
        elif cmd == "stop":
            send("OK\n")
            return "close"
        elif cmd == "version":
            send(f"OK\n{VERSION}\n")
        elif cmd == "list":
            send(f"OK\n{info['name']}\n")
        elif cmd == "config":
            send("OK\nConfig path: KP4PRA TNC (Config page, Xara card)\n"
                 f"{info['name']}\n  Type: {info['type']}\n  Cmd: xara (KP4PRA TNC)\n")
        elif cmd == "shutdown":
            send("ERROR remote shutdown disabled\n")
        else:
            send("Invalid command\n")
        return None

    async def handle(self, reader, writer):
        peer = writer.get_extra_info("peername") or ("?", 0)
        info = modem_info(self.h.config())
        local = str(peer[0]) in ("127.0.0.1", "::1", "::ffff:127.0.0.1")
        if not info["network"] and not local:
            writer.close()
            return
        # session: this connection holds the modem; switched: this session
        # switched the TNC to Xara (and switches it back at the end)
        state = {"session": False, "switched": False}

        def send(text):
            writer.write(text.encode())

        try:
            while True:
                data = await reader.read(1024)
                if not data:
                    break
                for cmd in data.decode(errors="replace").splitlines():
                    cmd = cmd.strip()
                    if not cmd:
                        continue
                    log(f"{peer[0]}: {cmd}")
                    result = await self.command(cmd, info, send, state)
                    await writer.drain()
                    if result == "close":
                        return
        except (ConnectionError, OSError):
            pass
        finally:
            try:
                await writer.drain()
            except (ConnectionError, OSError):
                pass
            writer.close()
            if state["session"]:
                self.busy = False
            if state["switched"]:
                log("session over - back to Dire Wolf (packet)")
                ok, out = await self.h.switch("packet")
                if not ok:
                    log("could not switch back to Dire Wolf: " + out)


async def main():
    launcher = Launcher()
    announcer = Announcer()
    server = await asyncio.start_server(launcher.handle, "0.0.0.0", LAUNCH_PORT)
    log(f"listening on port {LAUNCH_PORT}")
    try:
        while True:
            try:
                announcer.update(modem_info(reload_config()), Hooks.installed())
            except Exception as e:          # keep serving even if config is odd
                log(f"announce error: {e}")
            await asyncio.sleep(30)
    finally:
        announcer.stop()
        server.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
