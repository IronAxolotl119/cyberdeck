"""Rule logic tests for the dashboard (no hardware, no Rich needed).

Covers: Wi-Fi anomalies and evil-twin flags, Bluetooth dwell alerts,
LAN host changes, RFID unknown-tag alerts and presence, the cooldown and
rate limiter, the field-mode alert filter, XP/move rules, and the parsers
for the real backends (nmcli, iw, bluetoothctl, /proc/net/arp, /proc/net/dev).
"""

import random
import unittest

from cyberdeck_dash.diag import parse_ip_addr, parse_ping, parse_proc_net_dev, valid_target
from cyberdeck_dash.events import HIGH, LOW, MEDIUM, EventBus, Finding
from cyberdeck_dash.field import AlertFilter
from cyberdeck_dash.pet import LocalPet, apply_discovery
from cyberdeck_dash.ratelimit import Cooldown, RateLimiter
from cyberdeck_dash.rules import (BtTracker, LanTracker, RfidTracker, WifiTracker, XpRules, activity_mood,
                                  move_scanner)
from cyberdeck_dash.scanners.base import AccessPoint, BtDevice, LanHost
from cyberdeck_dash.scanners.bluetooth import parse_bluetoothctl
from cyberdeck_dash.scanners.lan import parse_proc_arp, sweep_targets
from cyberdeck_dash.scanners.rfid import format_uid
from cyberdeck_dash.scanners.wifi import parse_iw, parse_nmcli


def events(findings):
    return [(f.event, f.id) for f in findings]


class WifiRules(unittest.TestCase):
    def setUp(self):
        self.t = WifiTracker(rssi_jump_db=15, gone_after=2, patterns=["free wifi", "guest"],
                             trusted={"HOME": {"aa:aa:aa:aa:aa:01"}})

    def test_new_networks_and_new_ssid(self):
        out = self.t.update([AccessPoint("CAFE", "11:11:11:11:11:11", -60, "WPA2")], now=0)
        self.assertEqual(events(out), [("new_ssid", "11:11:11:11:11:11")])
        self.assertTrue(out[0].first)
        out = self.t.update([AccessPoint("CAFE", "11:11:11:11:11:11", -60, "WPA2"),
                             AccessPoint("CAFE", "22:22:22:22:22:22", -70, "WPA2")], now=1)
        self.assertEqual(events(out), [("new", "22:22:22:22:22:22")])

    def test_open_network_flagged_once(self):
        ap = AccessPoint("Lobby", "33:33:33:33:33:33", -60, "open")
        self.assertIn("open_network", [f.event for f in self.t.update([ap], 0)])
        self.assertNotIn("open_network", [f.event for f in self.t.update([ap], 1)])

    def test_rssi_jump(self):
        self.t.update([AccessPoint("X", "44:44:44:44:44:44", -80, "WPA2")], 0)
        self.assertEqual([], self.t.update([AccessPoint("X", "44:44:44:44:44:44", -70, "WPA2")], 1))
        out = self.t.update([AccessPoint("X", "44:44:44:44:44:44", -50, "WPA2")], 2)
        self.assertEqual(out[0].event, "rssi_jump")
        self.assertEqual(out[0].detail, {"from": -70, "to": -50})

    def test_gone_and_back(self):
        ap = AccessPoint("X", "55:55:55:55:55:55", -60, "WPA2")
        self.t.update([ap], 0)
        self.assertEqual([], self.t.update([], 1))
        self.assertEqual(["gone"], [f.event for f in self.t.update([], 2)])
        self.assertEqual(["back"], [f.event for f in self.t.update([ap], 3)])

    def test_suspicious_name(self):
        out = self.t.update([AccessPoint("FREE WiFi Airport", "66:66:66:66:66:66", -60, "WPA2")], 0)
        self.assertIn(("suspicious_name", MEDIUM), [(f.event, f.interest) for f in out])

    def test_evil_twin_open_copy_of_encrypted(self):
        real = AccessPoint("CoffeeNet", "77:77:77:77:77:01", -60, "WPA2")
        fake = AccessPoint("CoffeeNet", "77:77:77:77:77:99", -55, "open")
        out = self.t.update([real, fake], 0)
        twins = [f for f in out if f.event == "evil_twin"]
        self.assertEqual([f.id for f in twins], [fake.bssid])     # the original is NOT flagged
        self.assertEqual(twins[0].interest, HIGH)

    def test_evil_twin_trusted_ssid_from_unknown_radio(self):
        ok = AccessPoint("HOME", "aa:aa:aa:aa:aa:01", -40, "WPA2")
        odd = AccessPoint("HOME", "de:ad:be:ef:00:01", -50, "WPA2")
        out = self.t.update([ok, odd], 0)
        self.assertEqual([f.id for f in out if f.event == "evil_twin"], ["de:ad:be:ef:00:01"])

    def test_history_kept(self):
        for i, r in enumerate((-70, -68, -66)):
            self.t.update([AccessPoint("H", "88:88:88:88:88:88", r, "WPA2")], i)
        self.assertEqual(list(self.t.aps["88:88:88:88:88:88"].history), [-70, -68, -66])


class BluetoothRules(unittest.TestCase):
    def test_dwell_alert_for_new_lingering_device(self):
        t = BtTracker(dwell_s=600, forget_after=300)
        dev = BtDevice("aa:bb:cc:dd:ee:ff", "Tile", -70)
        self.assertEqual(["new"], [f.event for f in t.update([dev], 0)])
        self.assertEqual([], t.update([dev], 300))
        out = t.update([dev], 600)
        self.assertEqual([("long_stay", HIGH)], [(f.event, f.interest) for f in out])
        self.assertEqual([], t.update([dev], 900))             # only once

    def test_known_devices_never_alert(self):
        t = BtTracker(dwell_s=10, known=["11:22:33:44:55:66"])
        dev = BtDevice("11:22:33:44:55:66", "my phone", -40)
        self.assertEqual([], t.update([dev], 0) + t.update([dev], 20))

    def test_leaving_resets_dwell(self):
        t = BtTracker(dwell_s=100, forget_after=60)
        dev = BtDevice("aa:bb:cc:dd:ee:01", "", -80)
        t.update([dev], 0)
        t.update([dev], 30)
        t.update([], 100)                                      # gone 70 s > 60: forgotten
        self.assertEqual(["new"], [f.event for f in t.update([dev], 110)])
        self.assertEqual([], t.update([dev], 160))             # dwell restarted at 110
        self.assertEqual(["long_stay"], [f.event for f in t.update([dev], 215)])
        self.assertEqual(2, t.unique_total)


class LanRules(unittest.TestCase):
    def test_new_host_and_mac_change(self):
        t = LanTracker()
        out = t.update([LanHost("192.168.1.5", "aa:aa:aa:aa:aa:aa")], 0)
        self.assertEqual([("new_host", True)], [(f.event, f.first) for f in out])
        out = t.update([LanHost("192.168.1.5", "bb:bb:bb:bb:bb:bb")], 1)
        self.assertEqual([("mac_changed", HIGH)], [(f.event, f.interest) for f in out])

    def test_sweep_only_private_small_subnets(self):
        self.assertEqual(len(sweep_targets("192.168.1.0/24")), 254)
        for bad in ("8.8.8.0/24", "10.0.0.0/16"):
            with self.assertRaises(ValueError):
                sweep_targets(bad)


class RfidRules(unittest.TestCase):
    def test_unknown_tag_alert_and_known_tag(self):
        t = RfidTracker({"04:a2:19:7f": "door fob"}, absent_after=2)
        out = t.update(["04:A2:19:7F"], 0)
        self.assertEqual([("tag_seen", LOW, "door fob")], [(f.event, f.interest, f.label) for f in out])
        out = t.update(["de:ad:10:ca"], 1)
        self.assertEqual(("unknown_tag", HIGH, True), (out[0].event, out[0].interest, out[0].first))

    def test_presence_session_logged(self):
        t = RfidTracker(absent_after=2)
        t.update(["01:02"], 0)
        t.update(["01:02"], 1)
        out = t.update([], 4)
        self.assertEqual(["tag_left"], [f.event for f in out])
        self.assertEqual((t.log[0].reads, t.log[0].last - t.log[0].start), (2, 1))

    def test_unknown_tag_xp_only_first_time(self):
        t = RfidTracker(absent_after=1)
        first = t.update(["aa"], 0)[0]
        t.update([], 5)
        again = [f for f in t.update(["aa"], 6) if f.event == "unknown_tag"][0]
        self.assertTrue(first.first)
        self.assertFalse(again.first)

    def test_note_makes_tag_known(self):
        t = RfidTracker()
        t.note("AA:BB", "gym locker")
        self.assertEqual(t.update(["aa:bb"], 0)[0].event, "tag_seen")


class Limits(unittest.TestCase):
    def test_cooldown(self):
        c = Cooldown(10)
        self.assertTrue(c.ready("a", 0))
        self.assertFalse(c.ready("a", 5))
        self.assertTrue(c.ready("b", 5))
        self.assertTrue(c.ready("a", 10))
        self.assertAlmostEqual(c.remaining("a", 13), 7)

    def test_rate_limiter_burst_and_refill(self):
        r = RateLimiter(6, per=60, burst=3)       # 1 token / 10 s
        self.assertEqual([r.allow(0) for _ in range(4)], [True, True, True, False])
        self.assertFalse(r.allow(5))
        self.assertTrue(r.allow(15))
        self.assertEqual(r.dropped, 2)

    def test_alert_filter_field_mode(self):
        f = AlertFilter(alert_interest=3, normal_interest=2, cooldown=60, per_minute=60, burst=10)
        med = Finding("wifi", "open_network", "x", interest=MEDIUM)
        high = Finding("bt", "long_stay", "y", interest=HIGH)
        self.assertTrue(f.check(med, 0))
        f.field_mode = True
        self.assertFalse(f.check(Finding("wifi", "open_network", "z", interest=MEDIUM), 1))
        self.assertTrue(f.check(high, 2))
        self.assertFalse(f.check(high, 3))         # cooldown
        self.assertEqual(f.muted, 1)


class PetRules(unittest.TestCase):
    def test_xp_for_first_sightings_only(self):
        x = XpRules({"wifi": 4, "rfid": 8}, interest_bonus=3)
        self.assertEqual(x.xp_for(Finding("wifi", "new", "a", first=True)), 4)
        self.assertEqual(x.xp_for(Finding("rfid", "unknown_tag", "b", interest=HIGH, first=True)), 14)
        self.assertEqual(x.xp_for(Finding("wifi", "rssi_jump", "a", interest=MEDIUM)), 0)

    def test_learn_then_strengthen(self):
        x = XpRules(learn_chance=1.0)
        rng = random.Random(1)
        known = {}
        f = Finding("lan", "new_host", "ip", first=True)
        for _ in range(2):
            kind, name = x.roll_move(f, known, rng)
            self.assertEqual(kind, "learned")
            known[name] = 1
        self.assertEqual(x.roll_move(f, known, rng)[0], "strengthened")
        self.assertIsNone(XpRules(learn_chance=0.0).roll_move(f, known, rng))

    def test_local_pet_levels_up(self):
        pet = LocalPet()
        res = apply_discovery(pet, Finding("rfid", "unknown_tag", "t", interest=HIGH, first=True),
                              XpRules({"rfid": 30}, learn_chance=0), random.Random(0))
        self.assertTrue(res["level_up"])
        self.assertEqual(pet.level, 2)

    def test_moves_map_to_scanners(self):
        self.assertEqual(move_scanner("Signal Sniff"), "wifi")
        self.assertEqual(move_scanner("Tag Bite"), "rfid")
        self.assertEqual(move_scanner("Static Snap"), "wifi")      # companion move by keyword
        self.assertEqual(move_scanner("Mystery"), "all")

    def test_activity_mood(self):
        self.assertEqual(activity_mood(0, 0)[0], "calm")
        self.assertEqual(activity_mood(4, 0)[0], "curious")
        self.assertEqual(activity_mood(9, 0)[0], "buzzing")
        self.assertEqual(activity_mood(3, 2)[0], "alert")


class Bus(unittest.TestCase):
    def test_publish_and_error_isolation(self):
        bus, got, logs = EventBus(), [], []
        bus.subscribe("x", lambda p: 1 / 0)
        bus.subscribe("x", got.append)
        bus.subscribe("log", logs.append)
        bus.publish("x", 5)
        self.assertEqual(got, [5])
        self.assertIn("handler error", logs[0])


class Parsers(unittest.TestCase):
    def test_nmcli(self):
        aps = parse_nmcli("Home\\:Net:AA\\:BB\\:CC\\:DD\\:EE\\:FF:80:WPA2:6\n:11\\:22\\:33\\:44\\:55\\:66:20::11\n")
        self.assertEqual((aps[0].ssid, aps[0].bssid, aps[0].rssi, aps[0].channel), ("Home:Net", "aa:bb:cc:dd:ee:ff", -60, 6))
        self.assertTrue(aps[1].is_open)

    def test_iw(self):
        text = ("BSS aa:bb:cc:dd:ee:ff(on wlan0)\n\tsignal: -52.00 dBm\n\tSSID: Lab\n\tDS Parameter set: channel 11\n"
                "\tRSN:\t * Version: 1\nBSS 11:22:33:44:55:66(on wlan0)\n\tcapability: ESS\n\tsignal: -80.00 dBm\n"
                "\tSSID: Open\n")
        aps = parse_iw(text)
        self.assertEqual([(a.ssid, a.rssi, a.security, a.channel) for a in aps],
                         [("Lab", -52, "WPA2", 11), ("Open", -80, "open", 0)])

    def test_bluetoothctl(self):
        text = ("[NEW] Device AA:BB:CC:DD:EE:FF JBL Flip\n[CHG] Device AA:BB:CC:DD:EE:FF RSSI: 0xffffffc4 (-60)\n"
                "[NEW] Device 11:22:33:44:55:66 11-22-33-44-55-66\n[CHG] Device 11:22:33:44:55:66 RSSI: -77\n")
        devs = {d.mac: d for d in parse_bluetoothctl(text)}
        self.assertEqual((devs["aa:bb:cc:dd:ee:ff"].name, devs["aa:bb:cc:dd:ee:ff"].rssi), ("JBL Flip", -60))
        self.assertEqual((devs["11:22:33:44:55:66"].name, devs["11:22:33:44:55:66"].rssi), ("", -77))

    def test_proc_arp(self):
        text = ("IP address       HW type     Flags       HW address            Mask     Device\n"
                "192.168.1.1      0x1         0x2         AA:BB:CC:00:00:01     *        wlan0\n"
                "192.168.1.9      0x1         0x0         00:00:00:00:00:00     *        wlan0\n")
        self.assertEqual([(h.ip, h.mac) for h in parse_proc_arp(text)], [("192.168.1.1", "aa:bb:cc:00:00:01")])

    def test_diag_parsers(self):
        dev = ("Inter-|   Receive\n face |bytes packets\n"
               "  wlan0: 1000 10 0 0 0 0 0 0 2000 20 0 0 0 0 0 0\n")
        self.assertEqual(parse_proc_net_dev(dev), {"wlan0": (1000, 2000)})
        self.assertEqual(parse_ip_addr("3: wlan0    inet 192.168.1.5/24 brd x\n3: wlan0    inet6 fe80::1/64 scope\n"),
                         {"wlan0": ["192.168.1.5/24", "fe80::1/64"]})
        self.assertEqual(parse_ping("3 packets transmitted, 2 received, 33% packet loss\n"
                                    "rtt min/avg/max/mdev = 1.0/2.5/4.0/1.2 ms"), (3, 2, 2.5))
        self.assertTrue(valid_target("router.lan"))
        self.assertFalse(valid_target("-f 1.1.1.1"))
        self.assertFalse(valid_target("a;rm"))

    def test_uid_format(self):
        self.assertEqual(format_uid(0x04A2197F), "04:a2:19:7f")
        self.assertEqual(format_uid([1, 255]), "01:ff")


if __name__ == "__main__":
    unittest.main()
