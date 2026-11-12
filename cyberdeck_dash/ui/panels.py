"""One render function per dashboard panel, plus the PANELS registry.

In plain English: each function gets the Dashboard and a `big` flag
(False = small tile on the grid, True = full-screen view) and returns
something Rich can draw. Panels only READ state; actions live in app.py.

Add a panel: write `def render_mything(dash, big)`, add it to PANELS
below with a title and icon, and list its key in [panels] order in
config/dash.ini. docs/DASHBOARD.md walks through an example.
"""

from __future__ import annotations

import time
from datetime import datetime

from rich.console import Group
from rich.table import Table
from rich.text import Text

from ..events import INTEREST_NAMES
from ..rules import move_scanner
from .theme import bar, clip, interest_style, rssi_style, sparkline


def _table(dash, *cols, big=False) -> Table:
    t = Table(box=None, show_header=big, header_style=f"bold {dash.theme.accent}", pad_edge=False,
              padding=(0, 1, 0, 0), expand=True)
    for c in cols:
        if isinstance(c, tuple):
            t.add_column(c[0], **c[1])
        else:
            t.add_column(c)
    return t


def _age(seconds: float) -> str:
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}"


def _error(dash, name) -> Text | None:
    err = dash.scan_errors.get(name)
    return Text(f"⚠ {err}", style=dash.theme.bad) if err else None


# ---------------------------------------------------------------------------
# 1. Wi-Fi: networks, flags, RSSI sparkline history
# ---------------------------------------------------------------------------
def wifi_flags(dash, st) -> Text:
    th = dash.theme
    t = Text()
    if "twin" in st.flags:
        t.append("TWIN ", style=f"bold {th.bad}")
    if "suspicious" in st.flags:
        t.append("BAIT ", style=f"bold {th.warn}")
    if "open" in st.flags:
        t.append("OPEN ", style=th.warn)
    return t


def render_wifi(dash, big: bool):
    th = dash.theme
    rows = dash.wifi.visible()
    if big:
        t = _table(dash, "SSID", "BSSID", ("CH", {"justify": "right"}), "SEC", ("dBm", {"justify": "right"}),
                   "HISTORY", "FLAGS", ("AGE", {"justify": "right"}), big=True)
        for st in rows:
            ap = st.ap
            t.add_row(Text(clip(ap.ssid or "<hidden>", 22), style=th.text), Text(ap.bssid, style=th.dim),
                      str(ap.channel), ap.security, Text(str(ap.rssi), style=rssi_style(ap.rssi, th)),
                      Text(sparkline(st.history, 24), style=th.accent), wifi_flags(dash, st),
                      _age(dash.now - st.first_seen))
        recent = [a for a in dash.journal.recent(60) if a["kind"] == "wifi" and a["event"] not in ("new", "back")][:6]
        notes = Text("\nrecent anomalies\n", style=f"bold {th.accent2}")
        for e in recent:
            notes.append(f" {e['ts'][11:19]} ", style=th.dim)
            notes.append(f"{e['event']:<15}", style=interest_style(e["interest"], th))
            notes.append(f"{e['label']}  {e['detail'].get('reason') or e['detail'].get('pattern') or ''}\n",
                         style=th.text)
        return Group(t, notes, Text("Awareness only: a flag is a hint to look closer, not proof.", style=th.dim))
    t = _table(dash, "ssid", ("h", {"no_wrap": True}), ("db", {"justify": "right"}))
    for st in rows:
        flags = wifi_flags(dash, st)
        tag = flags.plain.split()[0] if flags.plain else ""
        name = Text(clip(st.ap.ssid or "<hidden>", 9 if tag else 14), style=th.text)
        if tag:
            name.append(" " + tag, style=f"bold {th.bad}" if tag == "TWIN" else th.warn)
        t.add_row(name, Text(sparkline(st.history, 8), style=th.accent),
                  Text(str(st.ap.rssi), style=rssi_style(st.ap.rssi, th)))
    err = _error(dash, "wifi")
    return Group(err, t) if err else t


# ---------------------------------------------------------------------------
# 2. Bluetooth: unique devices and dwell timers
# ---------------------------------------------------------------------------
def render_bt(dash, big: bool):
    th = dash.theme
    now = dash.now
    devs = dash.bt.visible(now)
    t = _table(dash, "device", *(["MAC"] if big else []), ("dBm", {"justify": "right"}),
               ("here", {"justify": "right"}), big=big)
    for st in devs:
        name = st.dev.name or "· unnamed"
        style = f"bold {th.bad}" if st.dwell_alerted else th.text if st.dev.name else th.dim
        row = [Text(clip(name, 26 if big else 13), style=style)]
        if big:
            row.append(Text(st.dev.mac, style=th.dim))
        row += [Text(str(st.dev.rssi if st.dev.rssi is not None else "?"), style=rssi_style(st.dev.rssi, th)),
                Text(_age(now - st.first_seen), style=th.warn if st.dwell_alerted else th.dim)]
        t.add_row(*row)
    head = Text(f"{len(devs)} near · {dash.bt.unique_total} seen · dwell {dash.bt.dwell_s / 60:.0f}m",
                style=th.dim, no_wrap=True, overflow="ellipsis")
    parts = [x for x in (_error(dash, "bt"), head, t) if x is not None]
    if big:
        parts.append(Text("\nMany phones rotate their Bluetooth MAC; one person can look like several devices.",
                          style=th.dim))
    return Group(*parts)


# ---------------------------------------------------------------------------
# 3. LAN: hosts on your own subnet
# ---------------------------------------------------------------------------
def render_lan(dash, big: bool):
    th = dash.theme
    names = getattr(dash.scanners.get("lan"), "names", {})
    t = _table(dash, "ip", *(["MAC", "IFACE"] if big else []), "name", ("seen", {"justify": "right"}), big=big)
    now = dash.now
    for ip in sorted(dash.lan.hosts, key=lambda s: tuple(int(p) for p in s.split(".") if p.isdigit())):
        h = dash.lan.hosts[ip]
        row = [Text(ip, style=th.accent)]
        if big:
            row += [Text(h.mac, style=th.dim), h.iface]
        row += [Text(clip(names.get(ip, "?"), 20 if big else 11), style=th.text),
                Text(_age(now - dash.lan.first_seen.get(ip, now)), style=th.dim)]
        t.add_row(*row)
    sweeper = getattr(dash.scanners.get("lan"), "sweeper", None)
    status = Text(f"{len(dash.lan.hosts)} hosts", style=th.dim)
    if sweeper:
        status.append(f" · sweep {sweeper.status} {sweeper.done}/{sweeper.total}", style=th.dim)
    parts = [x for x in (_error(dash, "lan"), status, t) if x is not None]
    return Group(*parts)


# ---------------------------------------------------------------------------
# 4. RFID / NFC: last read, presence, inventory with notes
# ---------------------------------------------------------------------------
def render_rfid(dash, big: bool):
    th = dash.theme
    r = dash.rfid
    out = []
    err = _error(dash, "rfid")
    if err:
        out.append(err)
    if r.last_uid:
        known = r.known.get(r.last_uid)
        line = Text("last ", style=th.dim)
        line.append(r.last_uid, style=f"bold {th.accent}")
        line.append(f"  {known}" if known else "  UNKNOWN", style=th.good if known else f"bold {th.bad}")
        out.append(line)
    else:
        out.append(Text("waiting for a tag… (r = sim tap)", style=th.dim))
    on = ", ".join(r.present) or "—"
    out.append(Text(f"on reader: {clip(on, 40 if big else 22)}", style=th.text))
    t = _table(dash, "uid", "note", big=big)
    for uid, note in list(r.known.items())[: 20 if big else 3]:
        t.add_row(Text(uid, style=th.accent), Text(clip(note, 40 if big else 14), style=th.text))
    out.append(Text(f"inventory {len(r.known)} · n = add note", style=th.dim))
    out.append(t)
    if big:
        out.append(Text("\npresence log", style=f"bold {th.accent2}"))
        for p in list(r.log)[:8]:
            out.append(Text(f" {datetime.fromtimestamp(p.start):%H:%M:%S}  {p.uid:<22} "
                            f"{p.last - p.start:5.1f}s  {p.reads} reads  {r.known.get(p.uid, 'unknown')}",
                            style=th.text))
        out.append(Text("\nReads the tag's UID only. Not a skimmer detector.", style=th.dim))
    return Group(*out)


# ---------------------------------------------------------------------------
# 5. Discovery journal
# ---------------------------------------------------------------------------
KIND_ICON = {"wifi": "≋", "bt": "✱", "lan": "⌂", "rfid": "◈", "pet": "◆", "system": "•"}


def render_journal(dash, big: bool):
    th = dash.theme
    lines = []
    for e in dash.journal.recent(24 if big else 8):
        line = Text(no_wrap=True, overflow="ellipsis")
        line.append(f"{e['ts'][11:19 if big else 16]} ", style=th.dim)
        line.append(f"{KIND_ICON.get(e['kind'], '•')} ", style=th.accent)
        line.append(f"{e['event'][:15 if big else 11]:<{16 if big else 12}}", style=interest_style(e.get("interest", 1), th))
        line.append(str(e["label"]), style=th.text)
        lines.append(line)
    if big:
        lines.append(Text(f"\nsaved to {dash.journal.path}", style=th.dim))
    return Group(*lines)


# ---------------------------------------------------------------------------
# 6. Field mode
# ---------------------------------------------------------------------------
def render_field(dash, big: bool):
    th = dash.theme
    f = dash.filter
    on = f.field_mode
    head = Text(" FIELD ON " if on else " FIELD OFF ",
                style=f"bold {th.title_bg} on {th.good}" if on else f"bold {th.dim} on {th.title_bg}")
    head.append(f"  alerts ≥ {INTEREST_NAMES.get(f.threshold, f.threshold)}", style=th.text)
    lines = [head,
             Text(f"scan every {dash.interval('wifi'):.0f}s · cooldown {f.cooldown.seconds:.0f}s", style=th.dim),
             Text(f"rate {f.limiter.tokens:.0f}/{f.limiter.capacity:.0f} · muted {f.muted} · capped "
                  f"{f.limiter.dropped}", style=th.dim, no_wrap=True, overflow="ellipsis")]
    for a in list(dash.alerts)[: 10 if big else 3]:
        line = Text(f"{datetime.fromtimestamp(a.ts):%H:%M} ", style=th.dim, no_wrap=True, overflow="ellipsis")
        line.append(f"{a.event[:11]:<11} ", style=interest_style(a.interest, th))
        line.append(clip(a.label, 40 if big else 12), style=th.text)
        lines.append(line)
    if big:
        lines.append(Text("\nf toggles field mode · s scans now · alerts also blink the LED if wired", style=th.dim))
    return Group(*lines)


# ---------------------------------------------------------------------------
# 7. Environment checklist
# ---------------------------------------------------------------------------
def render_checklist(dash, big: bool):
    from rich.columns import Columns

    th = dash.theme
    done = sum(1 for _, ok in dash.checklist if ok)
    head = Text(f"{done}/{len(dash.checklist)} ready", style=th.good if done == len(dash.checklist) else th.warn)
    head.append("   ↑↓ select · space tick · x reset", style=th.dim)
    focused = dash.panels[dash.focus] == "checklist"
    items = []
    for i, (item, ok) in enumerate(dash.checklist):
        line = Text("▸" if focused and i == dash.check_index else " ", style=th.accent2)
        line.append(f"{'■' if ok else '□'} ", style=th.good if ok else th.dim)
        line.append(clip(item, 60 if big else 26), style=th.dim if ok else th.text)
        items.append(line)
    # Items flow into as many columns as the tile is wide.
    return Group(head, Columns(items, padding=(0, 3)) if not big else Group(*items))


# ---------------------------------------------------------------------------
# 8. PULSE companion
# ---------------------------------------------------------------------------
FACES = {"calm": "(-‿-)", "curious": "(o_o)?", "buzzing": "\\(^o^)/", "alert": "(!_!)", "bored": "(-_- )"}


def render_pet(dash, big: bool):
    th = dash.theme
    v = dash.pet.view()
    face = ((v.get("avatar") or {}).get("text") if isinstance(v.get("avatar"), dict) else None) \
        or FACES.get(v["env_mood"], "(^_^)")
    head = Text(f"{face} ", style=f"bold {th.accent2}")
    head.append(f"{v.get('name', 'PULSE')} ", style=f"bold {th.text}")
    head.append(f"{v.get('species', '?')} L{v.get('level', '?')}", style=th.accent)
    src = Text(f" {v['source']}", style=th.good if v["source"] == "LINK" else th.dim)
    lines = [head + src]
    xp, nxt = v.get("xp", 0), v.get("xp_next")
    if nxt:
        lines.append(Text("xp  ", style=th.dim) + bar(xp / nxt, 10, th, invert=True) + Text(f" {xp}/{nxt}", style=th.dim))
    else:
        lines.append(Text(f"xp {xp}  dash {v.get('dash_xp', '')}", style=th.dim))
    lines.append(Text("nrg ", style=th.dim) + bar(v.get("energy", 0) / 100, 10, th, invert=True))
    # Environment: when the companion is linked, show its busy meter and buff.
    env_style = th.warn if v["env_mood"] in ("alert", "bored") else th.text
    busy = v.get("env_busy")
    if busy is not None:
        try:
            frac = max(0.0, min(1.0, float(busy)))
        except (TypeError, ValueError):
            frac = 0.0
        env = Text("env ", style=th.dim) + bar(frac, 8, th) + Text(f" {frac:.2f}  ", style=th.dim)
        env.append(v.get("buff") or v["env_mood"], style=env_style)
        env.overflow = "ellipsis"
        env.no_wrap = not big
        lines.append(env)
    else:
        lines.append(Text(f"env {v['env_mood']}: {v['buff']}", style=env_style,
                          no_wrap=not big, overflow="ellipsis"))
    mv = dash.pet.selected_move()
    if mv:
        lines.append(Text(f"move ▸ {mv} → {move_scanner(mv)}", style=th.accent2))
    recent = list(dash.pet.recent_moves)
    if recent:
        lines.append(Text("new  " + ", ".join(recent[: 6 if big else 2]), style=th.good, no_wrap=not big,
                          overflow="ellipsis"))
    reaction = v.get("last_reaction") or {}
    if reaction.get("reaction") and (big or reaction.get("source") == "companion"):
        lines.append(Text(str(reaction["reaction"]), style=th.accent2, no_wrap=not big, overflow="ellipsis"))
    if big:
        lines.append(Text(f"\nstage {v.get('stage', '?')} · mood {v.get('mood', v['env_mood'])} · "
                          f"link {dash.link.status if dash.link else 'disabled'}", style=th.dim))
        # Prefer combat-stats rows (power, rank, bonus) when the companion sent them.
        combat_moves = (v.get("combat") or {}).get("moves") if isinstance(v.get("combat"), dict) else None
        if combat_moves:
            bits = []
            for m in combat_moves:
                name = m.get("name", "?")
                extra = ""
                if m.get("rank"):
                    extra = f" r{m['rank']}"
                    if m.get("bonus"):
                        extra += f" +{m['bonus']}"
                bits.append(f"{name}{extra}")
            lines.append(Text("all moves: " + ", ".join(bits), style=th.text))
        else:
            lines.append(Text("all moves: " + ", ".join(dash.pet.moves()), style=th.text))
        lines.append(Text("\ncompanion alerts", style=f"bold {th.accent2}"))
        for ev in list(dash.pet.alerts)[:8]:
            lines.append(Text(f" {ev.get('event')}: {ev.get('detail') or ''}", style=th.text))
        lines.append(Text("\nu use move (runs a fresh scan) · v next move", style=th.dim))
    return Group(*lines)


# ---------------------------------------------------------------------------
# 9. System status
# ---------------------------------------------------------------------------
def render_system(dash, big: bool):
    th = dash.theme
    s = dash.sysinfo or {}
    t = _table(dash, ("k", {"style": th.dim}), "v")
    t.add_row("cpu", bar(s.get("cpu", 0) / 100, 10, th) + Text(f" {s.get('cpu', 0):3.0f}%", style=th.text))
    t.add_row("mem", bar(s.get("mem", 0) / 100, 10, th) + Text(f" {s.get('mem', 0):3.0f}%", style=th.text))
    temp = s.get("temp", 0)
    t.add_row("tmp", bar((temp - 30) / 55, 10, th) + Text(f" {temp:.0f}C{'~' if s.get('temp_sim') else ''}",
                                                            style=th.text))
    now = dash.now
    for k in ("wifi", "bt", "lan", "rfid"):
        if not big:
            break
        age = _age(now - dash.last_scan[k]) if k in dash.last_scan else "–"
        t.add_row(k, Text(f"last scan {age} ago", style=th.text))
    if not big:
        t.add_row("load", Text(f"{s.get('load', 0):.2f}  up {_age(s.get('uptime', 0))}", style=th.text))
        t.add_row("scan", Text(" ".join(f"{dash.scan_counts.get(k, 0)}" for k in ("wifi", "bt", "lan", "rfid"))
                               + " (w/b/l/r)", style=th.dim))
    if big:
        t.add_row("load", f"{s.get('load', 0):.2f}")
        t.add_row("up", _age(s.get("uptime", 0)))
        t.add_row("disk", f"{s.get('disk', 0):.0f}% used")
        t.add_row("host", s.get("host", "?"))
        t.add_row("mode", dash.mode.upper())
        for k, sc in dash.scanners.items():
            t.add_row(k, f"{sc.describe()} · every {dash.interval(k):.0f}s · {dash.scan_counts.get(k, 0)} scans")
    return t


# ---------------------------------------------------------------------------
# Diagnostics: ping, DNS lookup, interface stats + IPs
# ---------------------------------------------------------------------------
def render_diag(dash, big: bool):
    from ..diag import human_bytes

    th = dash.theme
    d = dash.diag
    lines = []
    t = _table(dash, "iface", *(["addresses"] if big else ["ip"]), ("rx/s", {"justify": "right"}),
               ("tx/s", {"justify": "right"}), *(["rx total", "tx total"] if big else []), big=big)
    for name, st in sorted(d.ifaces.items(), key=lambda kv: kv[0] == "lo"):
        if not big and name == "lo":
            continue
        v4 = [a for a in st["addrs"] if ":" not in a]
        addr = ", ".join(st["addrs"]) if big else (v4[0].split("/")[0] if v4 else "—")
        row = [Text(name, style=th.accent), Text(clip(addr, 60 if big else 16), style=th.text),
               human_bytes(st["rx_rate"]), human_bytes(st["tx_rate"])]
        if big:
            row += [human_bytes(st["rx"]), human_bytes(st["tx"])]
        t.add_row(*row)
    lines.append(t)
    for r in list(d.results)[: 8 if big else 2]:
        line = Text(f"{r.kind} ", style=th.accent2, no_wrap=not big, overflow="ellipsis")
        line.append(f"{r.target} ", style=th.text)
        line.append(r.summary, style=th.good if r.ok else th.bad)
        lines.append(line)
    if d.busy:
        lines.append(Text(f"running: {', '.join(sorted(d.busy))} …", style=th.warn))
    hint = "p ping · d dns" + (" (sim)" if d.simulated else "")
    lines.append(Text(hint if not big else f"\n{hint} · interface counters from /proc/net/dev", style=th.dim))
    return Group(*lines)


# ---------------------------------------------------------------------------
# Registry: key -> (title, icon, render function, scanner name or None)
# ---------------------------------------------------------------------------
PANELS = {
    "wifi": ("WI-FI", "≋", render_wifi, "wifi"),
    "bt": ("BLUETOOTH", "✱", render_bt, "bt"),
    "lan": ("LAN MAP", "⌂", render_lan, "lan"),
    "rfid": ("RFID/NFC", "◈", render_rfid, "rfid"),
    "journal": ("JOURNAL", "▤", render_journal, None),
    "diag": ("DIAGNOSTICS", "⌁", render_diag, None),
    "field": ("FIELD MODE", "⌖", render_field, None),
    "checklist": ("CHECKLIST", "☐", render_checklist, None),
    "pet": ("PULSE", "◆", render_pet, None),
    "system": ("SYSTEM", "▣", render_system, None),
}
