"""Avatars for each creature form: text-grid sprites and a Renderer interface.

The core of this package is standard-library only. TerminalRenderer draws
ANSI truecolor half-blocks (or a plain-ASCII fallback). If the optional
'rich' package happens to be installed, RichRenderer can be used instead;
nothing here imports it unless you ask.
"""

from avatar.renderer import (
    Renderer,
    RichRenderer,
    TerminalRenderer,
    format_catalog,
    get_renderer,
    rich_is_available,
    write_sprite_sheets,
)
from avatar.sprites import (
    FORMS,
    MOODS,
    PALETTE,
    SPRITE_SIZE,
    lookup_form,
    mood_for_creature,
    sprite_grid,
    sprite_pixels,
)

__all__ = [
    "FORMS",
    "MOODS",
    "PALETTE",
    "SPRITE_SIZE",
    "Renderer",
    "RichRenderer",
    "TerminalRenderer",
    "format_catalog",
    "get_renderer",
    "lookup_form",
    "mood_for_creature",
    "rich_is_available",
    "sprite_grid",
    "sprite_pixels",
    "write_sprite_sheets",
]
