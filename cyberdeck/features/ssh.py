"""ssh.py — jump into your servers with a short name.

  ssh               list your shortcuts
  ssh <name>        connect (the deck screen pauses and comes back after)
  ssh user@host     connect to anything, no shortcut needed

Shortcuts live in config/hosts.ini [ssh]:

    nas    = pi@192.168.1.20
    router = admin@192.168.1.1 -p 2222

Everything after the "=" is passed to ssh as-is, so -p, -i keyfile, -J
jumphost and friends all work. config/network.ini [ssh] sets the ssh
program and options added to every connection.

How it works: the command only builds the argv list and puts it in
ctx.external. tui.py then suspends curses, runs ssh in the real
terminal, and redraws the deck when ssh exits. In headless mode ssh is
run directly. build_ssh_argv() is unit-tested without connecting.
"""

from __future__ import annotations

import shlex
import shutil
from typing import TYPE_CHECKING

from cyberdeck.config import Settings

if TYPE_CHECKING:
    from cyberdeck.commands import Context, Registry


def load_shortcuts(settings: Settings | None = None) -> dict[str, str]:
    """name -> ssh arguments from hosts.ini [ssh]."""
    s = settings or Settings.load("hosts.ini")
    return {name: value for name, value in s.section("ssh").items() if value.strip()}


def build_ssh_argv(target: str, shortcuts: dict[str, str], binary: str = "ssh",
                   options: str = "", extra: list[str] | None = None) -> list[str]:
    """The full command line for `ssh <target>`."""
    if target in shortcuts:
        dest = shlex.split(shortcuts[target])
    else:
        dest = [target]
    return [binary, *shlex.split(options), *dest, *(extra or [])]


def register(reg: "Registry") -> None:
    """Add the ssh command."""

    def complete(ctx: "Context", args: list[str]) -> list[str]:
        return list(load_shortcuts(Settings.load("hosts.ini", getattr(ctx, "config_dir", None))))

    @reg.command("ssh", "SSH into a saved host (hosts.ini [ssh]) or user@host.",
                 usage="ssh [name|user@host] [remote command]", completer=complete)
    def cmd_ssh(ctx: "Context", args: list[str]) -> list[str]:
        shortcuts = load_shortcuts(Settings.load("hosts.ini", ctx.config_dir))
        if not args:
            if not shortcuts:
                return ["no ssh shortcuts yet — add them to config/hosts.ini [ssh]"]
            return ["ssh shortcuts:"] + [f"  {n:<12} {v}" for n, v in shortcuts.items()]
        net = Settings.load("network.ini", ctx.config_dir)
        binary = net.get_str("ssh", "binary", "ssh")
        if not shutil.which(binary):
            return [f"'{binary}' is not installed (sudo apt install openssh-client)"]
        if args[0] not in shortcuts and "@" not in args[0] and "." not in args[0]:
            return [f"no ssh shortcut named '{args[0]}' (type `ssh` to list them)"]
        ctx.external = build_ssh_argv(args[0], shortcuts, binary, net.get_str("ssh", "options", ""), args[1:])
        return [f"connecting: {' '.join(ctx.external)}"]
