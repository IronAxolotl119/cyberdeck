"""tests/test_network.py — notes/todo files and the network helpers.

Network tests never leave the machine: port and ping checks run
against a tiny TCP server on 127.0.0.1, and the wifi / route / traffic
parsers are fed captured text instead of real tools.
"""

from __future__ import annotations

import socket
import threading
import unittest

from cyberdeck.commands import build_registry
from cyberdeck.config import Settings
from cyberdeck.features import netcheck, netinfo, ssh, wifi
from cyberdeck.features.notes import NoteStore, TodoStore

from helpers import make_ctx, write_config


class NotesAndTodoTests(unittest.TestCase):
    def setUp(self):
        self.reg = build_registry(include_plugins=False)
        self.ctx = make_ctx()

    def test_note_round_trip(self):
        self.reg.dispatch("note add buy jumper wires", self.ctx)
        self.reg.dispatch("note solder header", self.ctx)  # no verb = add
        lines = self.reg.dispatch("note list", self.ctx)
        self.assertEqual(len(lines), 2)
        self.assertIn("buy jumper wires", lines[0])
        self.assertIn("solder", self.reg.dispatch("note find SOLDER", self.ctx)[0])
        self.reg.dispatch("note del 1", self.ctx)
        text = self.ctx.data_path("notes.txt").read_text()
        self.assertNotIn("jumper", text)
        self.assertIn(" | solder header", text)

    def test_note_del_bad_number(self):
        self.assertIn("no note #5", self.reg.dispatch("note del 5", self.ctx)[0])

    def test_todo_flow_and_file_format(self):
        self.reg.dispatch("todo add flash sd card", self.ctx)
        self.reg.dispatch("todo add wire buzzer", self.ctx)
        self.reg.dispatch("todo done 1", self.ctx)
        self.assertEqual(self.ctx.data_path("todo.txt").read_text(),
                         "[x] flash sd card\n[ ] wire buzzer\n")
        self.assertIn("1 open · 1 done", self.reg.dispatch("todo", self.ctx)[-1])
        self.reg.dispatch("todo undo 1", self.ctx)
        self.reg.dispatch("todo done 2", self.ctx)
        self.assertIn("removed 1", self.reg.dispatch("todo clear", self.ctx)[0])
        self.assertEqual(TodoStore(self.ctx.data_path("todo.txt")).items(), [(False, "flash sd card")])

    def test_hand_edited_todo_lines(self):
        path = self.ctx.data_path("todo.txt")
        path.write_text("plain line\n[X] shouted done\n\n")
        self.assertEqual(TodoStore(path).items(), [(False, "plain line"), (True, "shouted done")])

    def test_note_store_timestamp(self):
        line = NoteStore(self.ctx.data_path("n.txt")).add("hi", when=0)
        self.assertRegex(line, r"^\d{4}-\d\d-\d\d \d\d:\d\d \| hi$")


NMCLI = (
    "*:DECK-NET:78:WPA2:6\n"
    " :Coffee\\:Shop:40::11\n"
    " :DECK-NET:30:WPA2:1\n"
    " ::55:WPA2:36\n"
)

IW_SCAN = """BSS aa:bb:cc:dd:ee:ff(on wlan0) -- associated
\tsignal: -48.00 dBm
\tSSID: DECK-NET
\tDS Parameter set: channel 6
\tRSN:\t * Version: 1
BSS 11:22:33:44:55:66(on wlan0)
\tsignal: -81.00 dBm
\tSSID: far-away
\tHT operation:
\t\t * primary channel: 11
"""

PROC_WIRELESS = """Inter-| sta-|   Quality        |   Discarded packets
 face | tus | link level noise |  nwid  crypt   frag  retry   misc | beacon | 22
 wlan0: 0000   54.  -56.  -256        0      0      0      0     12        0
"""


class WifiTests(unittest.TestCase):
    def test_parse_nmcli_with_escapes(self):
        nets = wifi.parse_nmcli(NMCLI)
        self.assertEqual(nets[0], wifi.Network("DECK-NET", -61, 6, "WPA2", True))
        self.assertEqual(nets[1].ssid, "Coffee:Shop")
        self.assertEqual(nets[1].security, "open")
        self.assertEqual(nets[3].ssid, "(hidden)")

    def test_dedupe_keeps_strongest_and_in_use_first(self):
        nets = wifi.dedupe(wifi.parse_nmcli(NMCLI))
        self.assertEqual([n.ssid for n in nets].count("DECK-NET"), 1)
        self.assertTrue(nets[0].in_use)

    def test_parse_iw_scan(self):
        nets = wifi.parse_iw_scan(IW_SCAN)
        self.assertEqual(nets[0], wifi.Network("DECK-NET", -48, 6, "WPA2", True))
        self.assertEqual((nets[1].ssid, nets[1].channel, nets[1].security), ("far-away", 11, "open"))

    def test_parse_proc_wireless(self):
        self.assertEqual(wifi.parse_proc_wireless(PROC_WIRELESS), {"wlan0": -56})

    def test_signal_maths(self):
        self.assertEqual(wifi.dbm_to_quality(-50), 100)
        self.assertEqual(wifi.dbm_to_quality(-100), 0)
        self.assertEqual(wifi.quality_to_dbm(70), -65)
        self.assertEqual(wifi.signal_bars(-75, 4, unicode=False), "##..")
        self.assertEqual(wifi.signal_word(-90), "unusable")

    def test_sim_scan_and_meter(self):
        ctx = make_ctx()
        scanner = wifi.WifiScanner(ctx, Settings(None))
        self.assertEqual(scanner.backend, "sim")
        nets, note = scanner.scan()
        self.assertTrue(nets[0].in_use)
        meter = wifi.SignalMeter(scanner)
        meter.update(0.0)
        rows = "\n".join("".join(t for t, _ in r) for r in meter.render(60, 20))
        self.assertIn("dBm", rows)
        meter.on_key("x")
        self.assertTrue(meter.done)

    def test_wifi_meter_command_sets_activity(self):
        ctx = make_ctx()
        build_registry(include_plugins=False).dispatch("wifi meter", ctx)
        self.assertIsInstance(ctx.activity, wifi.SignalMeter)


class _Server:
    """A throwaway TCP listener on 127.0.0.1 for port tests."""

    def __enter__(self):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen()
        self.port = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self._accept, daemon=True)
        self.thread.start()
        return self

    def _accept(self):
        try:
            while True:
                conn, _ = self.sock.accept()
                conn.close()
        except OSError:
            pass

    def __exit__(self, *exc):
        self.sock.close()


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class NetCheckTests(unittest.TestCase):
    def test_host_lines(self):
        s = Settings.from_text("[hosts]\nnas = 192.168.1.20  22, 80,445\nbare = 10.0.0.9\nbad =\n")
        hosts = netcheck.load_hosts(s)
        self.assertEqual(hosts["nas"], netcheck.HostEntry("nas", "192.168.1.20", (22, 80, 445)))
        self.assertEqual(hosts["bare"].ports, ())
        self.assertNotIn("bad", hosts)

    def test_open_and_closed_ports(self):
        with _Server() as server:
            self.assertEqual(netcheck.check_port("127.0.0.1", server.port).state, "open")
        self.assertEqual(netcheck.check_port("127.0.0.1", _free_port()).state, "closed")

    def test_tcp_ping_counts_refused_as_up(self):
        result = netcheck.tcp_ping("127.0.0.1", (_free_port(),))
        self.assertTrue(result.ok)
        self.assertTrue(result.method.startswith("tcp:"))

    def test_check_many_keeps_order(self):
        with _Server() as server:
            entries = [netcheck.HostEntry("a", "127.0.0.1", (server.port,)),
                       netcheck.HostEntry("b", "127.0.0.1", (_free_port(),))]
            results = netcheck.check_many(entries, netcheck.NetSettings(port_timeout=0.5))
        self.assertEqual([e.name for e, _, _ in results], ["a", "b"])
        self.assertEqual(results[0][2][0].state, "open")
        self.assertEqual(results[1][2][0].state, "closed")

    def test_net_commands_use_config_dir(self):
        with _Server() as server:
            cfg = write_config({"hosts.ini": f"[hosts]\nlocal = 127.0.0.1 {server.port}\n"})
            ctx = make_ctx(cfg)
            reg = build_registry(include_plugins=False)
            self.assertIn("local", reg.dispatch("net hosts", ctx)[0])
            check = reg.dispatch("net check", ctx)
            self.assertIn("UP", check[0])
            self.assertIn("open", check[1])
            self.assertEqual(check[-1], "1/1 hosts up")
            port = reg.dispatch(f"net port local {server.port}", ctx)
            self.assertIn("open", port[1])

    def test_ping_completion_offers_hosts(self):
        from cyberdeck.completion import complete_line

        cfg = write_config({"hosts.ini": "[hosts]\nnas = 10.0.0.2\n"})
        ctx = make_ctx(cfg)
        reg = build_registry(include_plugins=False)
        self.assertEqual(complete_line("net ping n", reg, ctx)[0], "net ping nas ")
        self.assertEqual(complete_line("ping n", reg, ctx)[0], "ping nas ")


PROC_DEV = """Inter-|   Receive                                                |  Transmit
 face |bytes    packets errs drop fifo frame compressed multicast|bytes    packets errs drop fifo colls carrier compressed
    lo:    1000      10    0    0    0     0          0         0     1000      10    0    0    0     0       0          0
 wlan0: 5000000    4000    0    0    0     0          0         0   250000    2000    0    0    0     0       0          0
"""

ROUTE = """Iface\tDestination\tGateway \tFlags\tRefCnt\tUse\tMetric\tMask\t\tMTU\tWindow\tIRTT
wlan0\t00000000\t0101A8C0\t0003\t0\t0\t600\t00000000\t0\t0\t0
wlan0\t0001A8C0\t00000000\t0001\t0\t0\t600\t00FFFFFF\t0\t0\t0
"""


class NetInfoTests(unittest.TestCase):
    def test_parse_proc_net_dev(self):
        self.assertEqual(netinfo.parse_proc_net_dev(PROC_DEV)["wlan0"], (5000000, 250000))

    def test_default_gateway(self):
        self.assertEqual(netinfo.parse_default_routes(ROUTE), {"wlan0": "192.168.1.1"})

    def test_ip_addr_and_resolv(self):
        out = "2: wlan0    inet 192.168.1.50/24 brd 192.168.1.255 scope global wlan0\n"
        self.assertEqual(netinfo.parse_ip_addr(out), {"wlan0": ["192.168.1.50/24"]})
        self.assertEqual(netinfo.parse_resolv("# x\nnameserver 1.1.1.1\nnameserver 9.9.9.9\n"),
                         ["1.1.1.1", "9.9.9.9"])

    def test_rate_meter(self):
        meter = netinfo.RateMeter(min_gap=0.5)
        meter.feed(0.0, {"wlan0": (1000, 500)})
        rates = meter.feed(2.0, {"wlan0": (5096, 2548)})
        self.assertEqual(rates["wlan0"], (2048.0, 1024.0))
        # a counter reset never gives a negative rate
        self.assertEqual(meter.feed(3.0, {"wlan0": (10, 10)})["wlan0"], (0.0, 0.0))

    def test_human_units(self):
        self.assertEqual(netinfo.human_rate(512), "512 B/s")
        self.assertEqual(netinfo.human_rate(1536), "1.5 KB/s")
        self.assertEqual(netinfo.human_bytes(5 * 1024 * 1024), "5.0 MB")


class SshTests(unittest.TestCase):
    def test_argv_from_shortcut(self):
        shortcuts = {"nas": "pi@192.168.1.20 -p 2222"}
        argv = ssh.build_ssh_argv("nas", shortcuts, "ssh", "-o ConnectTimeout=8", ["uptime"])
        self.assertEqual(argv, ["ssh", "-o", "ConnectTimeout=8", "pi@192.168.1.20", "-p", "2222", "uptime"])

    def test_direct_target(self):
        self.assertEqual(ssh.build_ssh_argv("me@box", {}), ["ssh", "me@box"])

    def test_command_sets_external(self):
        cfg = write_config({"hosts.ini": "[ssh]\nnas = pi@10.0.0.2\n",
                            "network.ini": "[ssh]\nbinary = true\noptions =\n"})
        ctx = make_ctx(cfg)
        build_registry(include_plugins=False).dispatch("ssh nas", ctx)
        self.assertEqual(ctx.external, ["true", "pi@10.0.0.2"])

    def test_unknown_shortcut(self):
        ctx = make_ctx(write_config({"hosts.ini": "[ssh]\n", "network.ini": "[ssh]\nbinary = true\n"}))
        lines = build_registry(include_plugins=False).dispatch("ssh nope", ctx)
        self.assertIn("no ssh shortcut", lines[0])
        self.assertIsNone(ctx.external)


if __name__ == "__main__":
    unittest.main()
