"""VARA launcher (varanny-compatible, for RadioMail auto-discovery).

Run: PYTHONPATH=src python3 -m unittest discover -s tests
"""
import asyncio
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import vara_launcher as vl          # noqa: E402

CFG = {"station": {"callsign": "KP3M", "ssid": "2"}, "xara": {"mode": "fm-narrow"}}


class FakeHooks:
    def __init__(self, running=False, installed=True, switch_ok=True, cfg=CFG):
        self.is_running = running
        self.is_installed = installed
        self.switch_ok = switch_ok
        self.cfg = cfg
        self.calls = []

    def installed(self):
        return self.is_installed

    def running(self):
        return self.is_running

    def listening(self, port):
        return self.is_running

    async def switch(self, modem):
        self.calls.append(modem)
        if self.switch_ok:
            self.is_running = modem == "xara"
            return True, "ok"
        return False, "kp4pra-xara: WARNING: Xara did not start"

    def config(self):
        return self.cfg


class Announce(unittest.TestCase):
    def test_info_and_txt(self):
        info = vl.modem_info(CFG)
        self.assertEqual(info["name"], "KP3M-2 VARA FM")
        self.assertEqual((info["type"], info["port"]), ("fm", 8300))
        self.assertEqual(vl.txt_records(info), ["launchport=8273;", "type=fm;"])

    def test_publish_commands(self):
        cmds = vl.publish_commands(vl.modem_info(CFG))
        self.assertEqual(cmds[0], ["avahi-publish", "-s", "KP3M-2 VARA FM", "_vara-modem._tcp",
                                   "8300", "launchport=8273;", "type=fm;"])
        self.assertEqual(cmds[1][3], "_varafm-modem._tcp")
        hf = vl.modem_info({"station": {"callsign": "KP3M"}, "xara": {"mode": "hf-2300", "port": 8400}})
        self.assertEqual(vl.publish_commands(hf)[1][3:5], ["_varahf-modem._tcp", "8400"])

    def test_not_announced_without_xara_or_network(self):
        a = vl.Announcer()
        a.update(vl.modem_info(CFG), installed=False)
        self.assertEqual(a.procs, [])
        a.update(vl.modem_info({"station": {"callsign": "KP3M"}, "xara": {"network": False}}), True)
        self.assertEqual(a.procs, [])


class Protocol(unittest.TestCase):
    def session(self, hooks, lines, close_early=False):
        """Run the launcher on a free port, send `lines`, return replies."""
        async def run():
            launcher = vl.Launcher(hooks)
            server = await asyncio.start_server(launcher.handle, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            r, w = await asyncio.open_connection("127.0.0.1", port)
            out = []
            for line in lines:
                w.write((line + "\n").encode())
                await w.drain()
                await asyncio.sleep(0.05)
                try:
                    data = await asyncio.wait_for(r.read(1024), 2)
                except asyncio.TimeoutError:
                    data = b""
                out.append(data.decode())
                if not data:
                    break
            w.close()
            await asyncio.sleep(0.2)          # let the server run its cleanup
            server.close()
            await server.wait_closed()
            return out, launcher
        return asyncio.run(run())

    def test_start_switches_and_stop_switches_back(self):
        h = FakeHooks(running=False)
        out, _ = self.session(h, ["version", "list", "start KP3M-2 VARA FM", "stop"])
        self.assertTrue(out[0].startswith("OK\n"))
        self.assertEqual(out[1], "OK\nKP3M-2 VARA FM\n")
        self.assertEqual(out[2], "OK\n")
        self.assertEqual(out[3], "OK\n")
        self.assertEqual(h.calls, ["xara", "packet"])

    def test_disconnect_also_switches_back(self):
        h = FakeHooks(running=False)
        out, _ = self.session(h, ["start any name"])
        self.assertEqual(out, ["OK\n"])
        self.assertEqual(h.calls, ["xara", "packet"])

    def test_xara_already_on_stays_on(self):
        h = FakeHooks(running=True)
        out, _ = self.session(h, ["start KP3M-2 VARA FM", "stop"])
        self.assertEqual(out, ["OK\n", "OK\n"])
        self.assertEqual(h.calls, [])
        self.assertTrue(h.is_running)

    def test_auto_switch_off(self):
        cfg = {"station": {"callsign": "KP3M"}, "xara": {"auto_switch": False}}
        h = FakeHooks(running=False, cfg=cfg)
        out, _ = self.session(h, ["start x"])
        self.assertIn("ERROR Xara is not on", out[0])
        self.assertEqual(h.calls, [])

    def test_not_installed(self):
        out, _ = self.session(FakeHooks(installed=False), ["start x"])
        self.assertIn("ERROR Xara is not installed", out[0])

    def test_switch_fails(self):
        h = FakeHooks(switch_ok=False)
        out, launcher = self.session(h, ["start x"])
        self.assertIn("ERROR", out[0])
        self.assertEqual(h.calls, ["xara"])           # nothing to switch back
        self.assertFalse(launcher.busy)

    def test_other_commands(self):
        out, _ = self.session(FakeHooks(), ["monitor x", "shutdown", "bogus"])
        self.assertIn("ERROR audio monitor", out[0])
        self.assertIn("ERROR remote shutdown disabled", out[1])
        self.assertEqual(out[2], "Invalid command\n")

    def test_one_session_at_a_time(self):
        async def run():
            h = FakeHooks(running=True)
            launcher = vl.Launcher(h)
            server = await asyncio.start_server(launcher.handle, "127.0.0.1", 0)
            port = server.sockets[0].getsockname()[1]
            r1, w1 = await asyncio.open_connection("127.0.0.1", port)
            w1.write(b"start a\n"); await w1.drain()
            first = await asyncio.wait_for(r1.read(100), 2)
            r2, w2 = await asyncio.open_connection("127.0.0.1", port)
            w2.write(b"start b\n"); await w2.drain()
            second = await asyncio.wait_for(r2.read(100), 2)
            w1.close(); w2.close()
            await asyncio.sleep(0.1)
            server.close()
            return first, second
        first, second = asyncio.run(run())
        self.assertEqual(first, b"OK\n")
        self.assertIn(b"already running", second)

    def test_port_listening_parser(self):
        self.assertIsInstance(vl.port_listening(1), bool)


if __name__ == "__main__":
    unittest.main()
