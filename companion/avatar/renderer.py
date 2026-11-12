"""Display-agnostic rendering of sprite pixel arrays.

pixels is always a plain 2D list: pixels[y][x] is an (r, g, b) tuple or None
for a transparent cell. TerminalRenderer turns that into either ANSI
truecolor half-blocks (▀) so a 16x16 sprite is 16 columns by 8 rows, or a
plain-ASCII luminance fallback. An optional RichRenderer does the same half-
block picture through the third-party 'rich' library when it is installed;
the core path never requires it.

A future SPI, e-ink, or OLED driver should implement Renderer and consume
the same 2D array from avatar.sprites.sprite_pixels().
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

from avatar.sprites import SPRITE_SIZE, list_forms, lookup_form, sprite_pixels

# Upper and lower half blocks. Combining a top color (foreground) with a
# bottom color (background) on ▀ packs two pixels into one terminal cell.
UPPER_HALF = "▀"  # U+2580
LOWER_HALF = "▄"  # U+2584

# Characters used when color is not available. Dark ink becomes '#' so the
# outline stays readable; everything else is a luminance ramp.
ASCII_RAMP = " .,:;+*oO@"
ASCII_INK_LUMINANCE = 28


Pixel = tuple[int, int, int] | None
Pixels = list[list[Pixel]]


class Renderer(ABC):
    """Something that can turn a 2D pixel array into a printable string."""

    @abstractmethod
    def render(self, pixels: Pixels) -> str:
        """Return a multi-line string for this image, with no trailing spaces."""
        raise NotImplementedError


def _luminance(color: tuple[int, int, int]) -> float:
    r, g, b = color
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _ascii_char(pixel: Pixel) -> str:
    if pixel is None:
        return " "
    lum = _luminance(pixel)
    if lum <= ASCII_INK_LUMINANCE:
        return "#"
    index = int(round((lum / 255.0) * (len(ASCII_RAMP) - 1)))
    index = max(0, min(len(ASCII_RAMP) - 1, index))
    return ASCII_RAMP[index]


def _fg(color: tuple[int, int, int]) -> str:
    r, g, b = color
    return f"\x1b[38;2;{r};{g};{b}m"


def _bg(color: tuple[int, int, int]) -> str:
    r, g, b = color
    return f"\x1b[48;2;{r};{g};{b}m"


_RESET = "\x1b[0m"


class TerminalRenderer(Renderer):
    """Built-in renderer. ascii_mode=True disables all ANSI color."""

    def __init__(self, ascii_mode: bool = False) -> None:
        self.ascii_mode = ascii_mode

    def render(self, pixels: Pixels) -> str:
        if not pixels:
            return ""
        if self.ascii_mode:
            return self._render_ascii(pixels)
        return self._render_halfblocks(pixels)

    def _render_ascii(self, pixels: Pixels) -> str:
        lines = ["".join(_ascii_char(pixel) for pixel in row).rstrip() for row in pixels]
        # Keep a consistent 16-row picture: don't drop trailing blank rows
        # entirely, but do allow rstrip of the whole block's last newline only
        # at the join step. Pad visually empty rows as empty strings.
        while lines and lines[-1] == "":
            lines.pop()
        return "\n".join(lines)

    def _render_halfblocks(self, pixels: Pixels) -> str:
        height = len(pixels)
        width = max((len(row) for row in pixels), default=0)
        lines: list[str] = []
        for y in range(0, height, 2):
            cells: list[str] = []
            top_row = pixels[y]
            bottom_row = pixels[y + 1] if y + 1 < height else []
            for x in range(width):
                top = top_row[x] if x < len(top_row) else None
                bottom = bottom_row[x] if x < len(bottom_row) else None
                cells.append(self._half_cell(top, bottom))
            # rstrip would eat trailing spaces (transparent cells), which is
            # fine at the end of a line. ANSI codes sit on the visible pixels
            # so this will not clip a colored glyph.
            line = "".join(cells).rstrip()
            # If rstrip removed a reset at the end after spaces... actually
            # rstrip only removes whitespace, not ESC. Good.
            lines.append(line)
        while lines and lines[-1] == "":
            lines.pop()
        return "\n".join(lines)

    @staticmethod
    def _half_cell(top: Pixel, bottom: Pixel) -> str:
        if top is None and bottom is None:
            return " "
        if top is not None and bottom is not None:
            return f"{_fg(top)}{_bg(bottom)}{UPPER_HALF}{_RESET}"
        if top is not None:
            return f"{_fg(top)}{UPPER_HALF}{_RESET}"
        assert bottom is not None
        return f"{_fg(bottom)}{LOWER_HALF}{_RESET}"


def rich_is_available() -> bool:
    """True when the optional 'rich' package can be imported."""
    try:
        import rich  # noqa: F401

        return True
    except ImportError:
        return False


class RichRenderer(Renderer):
    """Optional half-block renderer that goes through the 'rich' library.

    Only constructed when rich is installed. Output is still a plain string
    of ANSI (captured from a rich Console) so callers can print() it.
    """

    def __init__(self) -> None:
        if not rich_is_available():
            raise RuntimeError(
                "The optional 'rich' package is not installed. "
                "Use TerminalRenderer, or pip install rich."
            )

    def render(self, pixels: Pixels) -> str:
        from io import StringIO

        from rich.console import Console
        from rich.text import Text

        if not pixels:
            return ""
        height = len(pixels)
        width = max((len(row) for row in pixels), default=0)
        text = Text(no_wrap=True)
        for y in range(0, height, 2):
            if y:
                text.append("\n")
            top_row = pixels[y]
            bottom_row = pixels[y + 1] if y + 1 < height else []
            # Track trailing transparent cells so we can omit them (like rstrip).
            row_parts: list[tuple[str, str | None]] = []
            for x in range(width):
                top = top_row[x] if x < len(top_row) else None
                bottom = bottom_row[x] if x < len(bottom_row) else None
                row_parts.append(self._styled_cell(top, bottom))
            while row_parts and row_parts[-1] == (" ", None):
                row_parts.pop()
            for glyph, style in row_parts:
                if style:
                    text.append(glyph, style=style)
                else:
                    text.append(glyph)
        buffer = StringIO()
        console = Console(
            file=buffer,
            force_terminal=True,
            color_system="truecolor",
            highlight=False,
            width=max(width, 16) + 4,
            legacy_windows=False,
        )
        console.print(text, end="")
        return buffer.getvalue()

    @staticmethod
    def _styled_cell(top: Pixel, bottom: Pixel) -> tuple[str, str | None]:
        if top is None and bottom is None:
            return (" ", None)
        if top is not None and bottom is not None:
            tr, tg, tb = top
            br, bg, bb = bottom
            return (UPPER_HALF, f"rgb({tr},{tg},{tb}) on rgb({br},{bg},{bb})")
        if top is not None:
            tr, tg, tb = top
            return (UPPER_HALF, f"rgb({tr},{tg},{tb})")
        assert bottom is not None
        br, bg, bb = bottom
        return (LOWER_HALF, f"rgb({br},{bg},{bb})")


def get_renderer(ascii_mode: bool = False, use_rich: bool = False) -> Renderer:
    """Pick a renderer. Falls back to TerminalRenderer if rich is missing."""
    if ascii_mode:
        return TerminalRenderer(ascii_mode=True)
    if use_rich:
        if rich_is_available():
            return RichRenderer()
        # Caller can print a warning; we still give them a working renderer.
        return TerminalRenderer(ascii_mode=False)
    return TerminalRenderer(ascii_mode=False)


def blit(dest: Pixels, src: Pixels, x: int, y: int) -> None:
    """Copy non-transparent src pixels onto dest at (x, y)."""
    dest_height = len(dest)
    dest_width = len(dest[0]) if dest else 0
    for iy, row in enumerate(src):
        dy = y + iy
        if dy < 0 or dy >= dest_height:
            continue
        for ix, pixel in enumerate(row):
            if pixel is None:
                continue
            dx = x + ix
            if 0 <= dx < dest_width:
                dest[dy][dx] = pixel


def _blank(width: int, height: int) -> Pixels:
    return [[None] * width for _ in range(height)]


# Horizontal gap between sprites on the catalog sheet, in pixels.
SHEET_GAP = 3
SHEET_MOODS: tuple[tuple[str, str], ...] = (
    ("neutral", "neutral (idle frame 0)"),
    ("happy", "happy"),
    ("hungry", "hungry / tired"),
    ("sad", "sad"),
)


def format_catalog(ascii_mode: bool = False, use_rich: bool = False, include_frame1: bool = True) -> str:
    """All forms and moods, grouped by evolution stage, as a multi-line string."""
    forms = list_forms()
    renderer = get_renderer(ascii_mode=ascii_mode, use_rich=use_rich and not ascii_mode)

    stage_order = ("baby", "in-training", "rookie", "champion", "ultimate", "mega")
    groups: dict[str, list[str]] = {stage: [] for stage in stage_order}
    other: list[str] = []
    for form_id in forms:
        stage = _stage_for(form_id)
        if stage in groups:
            groups[stage].append(form_id)
        else:
            other.append(form_id)

    sections: list[str] = []
    sections.append("Cyberdeck Pet — sprite catalog")
    sections.append("Each form has two idle frames; moods are a face overlay.")
    sections.append("Forms are grouped by stage so later evolutions stay readable.")
    sections.append("")

    rows_spec: list[tuple[str, int, str]] = [
        (mood, 0, title) for mood, title in SHEET_MOODS
    ]
    if include_frame1:
        rows_spec.append(("neutral", 1, "neutral (idle frame 1)"))

    first_section = True
    for stage in list(stage_order) + ["other"]:
        chunk = other if stage == "other" else groups[stage]
        if not chunk:
            continue
        if not first_section:
            sections.append("")
        first_section = False
        label = stage if stage != "other" else "other"
        sections.append(f"— {label} —")
        sections.extend(_catalog_block(chunk, renderer, rows_spec))

    return "\n".join(sections).rstrip() + "\n"


def _catalog_block(
    forms: list[str],
    renderer,
    rows_spec: list[tuple[str, int, str]],
) -> list[str]:
    """One stage's column headers plus the mood/frame rows."""
    col_width = SPRITE_SIZE + SHEET_GAP
    names = [lookup_form(form_id)["name"].center(SPRITE_SIZE) for form_id in forms]
    stages = [_stage_for(form_id).center(SPRITE_SIZE) for form_id in forms]
    gap = " " * SHEET_GAP
    out = [gap.join(names).rstrip(), gap.join(stages).rstrip()]
    for mood, frame, title in rows_spec:
        width = col_width * len(forms) - SHEET_GAP
        canvas = _blank(max(1, width), SPRITE_SIZE)
        for index, form_id in enumerate(forms):
            blit(canvas, sprite_pixels(form_id, frame=frame, mood=mood), index * col_width, 0)
        out.append("")
        out.append(title)
        out.append(renderer.render(canvas))
    return out


def _stage_for(form_id: str) -> str:
    try:
        from pet.evolution import get_species

        return get_species(form_id).stage
    except (ImportError, KeyError):
        return form_id


def write_sprite_sheets(directory: str | Path) -> tuple[Path, Path]:
    """Write sheet.ansi (truecolor half-blocks) and sheet.txt (plain ASCII)."""
    dest = Path(directory)
    dest.mkdir(parents=True, exist_ok=True)
    ansi_path = dest / "sheet.ansi"
    txt_path = dest / "sheet.txt"
    ansi_path.write_text(format_catalog(ascii_mode=False), encoding="utf-8")
    txt_path.write_text(format_catalog(ascii_mode=True), encoding="utf-8")
    return ansi_path, txt_path


__all__ = [
    "Renderer",
    "RichRenderer",
    "TerminalRenderer",
    "blit",
    "format_catalog",
    "get_renderer",
    "rich_is_available",
    "write_sprite_sheets",
]
