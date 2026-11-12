"""hello.py — an example user command.

This file is loaded automatically at startup because it lives in
plugins/ and does not start with an underscore. Type `hello` or
`hello yourname` at the prompt to try it.

Copy this file (or _template.py) to add your own command. You do not
need to edit any other file.
"""

# The word typed at the prompt.
NAME = "hello"

# One-line description shown by the `help` command.
SUMMARY = "Say hello (example user command)."

# Shown in the help listing; include any arguments here.
USAGE = "hello [name]"

# Optional extra names that run the same command.
ALIASES = ("hi",)


def run(ctx, args):
    """Called when the user types `hello`. Return a list of lines to print.

    ctx is the same context built-in commands get (sensors, companion,
    and so on). args is the list of words after the command name.
    """
    del ctx  # this example does not need sensors or the companion
    who = args[0] if args else "deck"
    return [f"hello, {who}."]
