"""_template.py — copy me to add a new command.

This file is NOT loaded automatically, because its name starts with an
underscore. To use it:

  1. Copy this file to plugins/mycommand.py (no leading underscore).
  2. Change NAME, SUMMARY, USAGE, and the run() function.
  3. Restart the deck and type `help` — your command should appear.

You do not need to register it anywhere else.
"""

NAME = "mycommand"
SUMMARY = "Describe what your command does in one short sentence."
USAGE = "mycommand [args]"
ALIASES = ()


def run(ctx, args):
    """Return a list of text lines to show in the output pane.

    Useful pieces of ctx:
      ctx.sensors.read()          current temp / battery / wifi
      ctx.companion.request(...)  talk to the pet board
      ctx.mode                    'sim' or 'real'
      ctx.should_quit = True      leave the app
      ctx.should_clear = True     clear the output pane
    """
    del ctx, args
    return ["replace this with your command's output"]
