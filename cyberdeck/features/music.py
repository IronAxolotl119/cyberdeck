"""music.py — a tiny chiptune player for the GPIO buzzer.

  music                  list songs (also `music list`)
  music play <song>      play a song from the songs/ folder
  music stop             stop playing
  music now              what is playing
  music show <song>      print the notes as a little piano roll

SONG FILES (songs/*.txt) are plain text, one song per file:

    title: Ode to Joy
    tempo: 120          beats (quarter notes) per minute
    # lines starting with # are comments
    E4 E4 F4 G4 | G4 F4 E4 D4 | C4 C4 D4 E4 | E4/4. D4/8 D4/2

Each note is   LETTER [#|b] [OCTAVE] [LENGTH] [.]
  LETTER  A-G, or R for a rest
  OCTAVE  0-8 (default: the `octave:` header, normally 4; C4 = middle C)
  /LENGTH 1 whole, 2 half, 4 quarter, 8 eighth, 16, 32, always after a
          slash: E4/8 is an eighth-note E4 (default: the `length:`
          header, normally 4)
  .       dotted, 1.5x as long: E4/4.
  |       bar lines are ignored; they are just for reading

Playback runs in a background thread so the deck stays usable. Notes
go to the buzzer through the shared GPIO backend (sim mode just
pretends, and the visualizer / music panel show what is playing).
parse_song() is pure and unit-tested.
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from cyberdeck.activity import Row
from cyberdeck.config import Settings, repo_root
from cyberdeck.panes import register_pane

if TYPE_CHECKING:
    from cyberdeck.commands import Context, Registry
    from cyberdeck.features.hardware import Hardware

SEMITONES = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}
NOTE_RE = re.compile(r"^(?P<letter>[A-Ga-gRr])(?P<acc>[#b]?)(?P<oct>\d)?(?:/(?P<len>\d+))?(?P<dot>\.?)$")
VALID_LENGTHS = {1, 2, 4, 8, 16, 32}


class SongError(ValueError):
    """A song file has a note the parser does not understand."""


@dataclass(frozen=True)
class Note:
    """One note (or rest) ready to play."""

    name: str        # e.g. "C#5" or "R"
    freq: float      # Hz, 0 for a rest
    seconds: float   # how long it lasts


@dataclass
class Song:
    title: str
    tempo: int
    notes: list[Note] = field(default_factory=list)

    @property
    def seconds(self) -> float:
        return sum(n.seconds for n in self.notes)


def note_freq(letter: str, accidental: str, octave: int) -> float:
    """Equal temperament, A4 = 440 Hz."""
    semitone = SEMITONES[letter.upper()] + (1 if accidental == "#" else -1 if accidental == "b" else 0)
    midi = (octave + 1) * 12 + semitone
    return 440.0 * 2 ** ((midi - 69) / 12)


def parse_song(text: str, fallback_title: str = "untitled") -> Song:
    """Turn song text into a Song. Raises SongError on a bad token."""
    headers = {"title": fallback_title, "tempo": "120", "octave": "4", "length": "4"}
    tokens: list[str] = []
    for raw in text.splitlines():
        # Only whole-line comments: a '#' after a note letter means sharp (F#4).
        line = raw.strip() if not raw.lstrip().startswith("#") else ""
        if not line:
            continue
        key, sep, value = line.partition(":")
        if sep and key.strip().lower() in headers:
            headers[key.strip().lower()] = value.strip()
            continue
        tokens.extend(t for t in line.replace("|", " ").split() if t)
    try:
        tempo = max(20, min(400, int(headers["tempo"])))
        default_oct = int(headers["octave"])
        default_len = int(headers["length"])
    except ValueError as exc:
        raise SongError(f"bad header value: {exc}") from exc
    beat = 60.0 / tempo
    notes: list[Note] = []
    for token in tokens:
        match = NOTE_RE.match(token)
        if not match:
            raise SongError(f"cannot read note '{token}'")
        length = int(match.group("len") or default_len)
        if length not in VALID_LENGTHS:
            raise SongError(f"note '{token}': length must be one of 1 2 4 8 16 32")
        seconds = beat * 4 / length * (1.5 if match.group("dot") else 1.0)
        letter = match.group("letter").upper()
        if letter == "R":
            notes.append(Note("R", 0.0, seconds))
            continue
        octave = int(match.group("oct")) if match.group("oct") else default_oct
        acc = match.group("acc")
        notes.append(Note(f"{letter}{acc}{octave}", note_freq(letter, acc, octave), seconds))
    return Song(headers["title"], tempo, notes)


# --- song files ----------------------------------------------------------------

def songs_dir(ctx: "Context") -> Path:
    """config/hardware.ini [music] folder, default ./songs."""
    folder = Settings.load("hardware.ini", ctx.config_dir).get_str("music", "folder", "songs")
    path = Path(folder)
    return path if path.is_absolute() else repo_root() / path


def list_songs(ctx: "Context") -> list[str]:
    folder = songs_dir(ctx)
    return sorted(p.stem for p in folder.glob("*.txt")) if folder.is_dir() else []


def load_song(ctx: "Context", name: str) -> Song:
    path = songs_dir(ctx) / f"{name}.txt"
    if not path.is_file():
        raise SongError(f"no song '{name}' in {songs_dir(ctx)} (try `music list`)")
    return parse_song(path.read_text(encoding="utf-8"), name)


# --- the player ------------------------------------------------------------------

class MusicPlayer:
    """Plays a Song on the buzzer in a background thread."""

    def __init__(self, hw: "Hardware", gap: float = 0.1) -> None:
        self.hw = hw
        self.gap = gap  # fraction of each note left silent so repeats are distinct
        self.song: Song | None = None
        self.current: Note | None = None
        self.started_note = 0.0
        self.position = 0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._finished: str | None = None  # title, reported once by ui_tick
        self.quiet = False

    @property
    def playing(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def play(self, song: Song, block: bool = False, quiet: bool = False) -> bool:
        """Start a song. False if the buzzer is busy. quiet = no "finished" notice."""
        self.stop()
        self.quiet = quiet
        if not self.hw.buzzer_lock.acquire(blocking=False):
            return False
        self.song, self.position = song, 0
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, args=(song,), daemon=True)
        self._thread.start()
        if block:
            self._thread.join()
        return True

    def _run(self, song: Song) -> None:
        try:
            for i, note in enumerate(song.notes):
                if self._stop.is_set():
                    break
                self.position, self.current, self.started_note = i, note, time.time()
                sound = note.seconds * (1 - self.gap)
                self.hw.gpio.tone(self.hw.buzzer, note.freq, sound)
                if self._stop.wait(note.seconds - sound):
                    break
        except Exception as exc:  # e.g. a GPIO permission error
            self._finished = f"{song.title} stopped: {exc}"
        else:
            if not self._stop.is_set() and not self.quiet:
                self._finished = f"♪ finished: {song.title}"
        finally:
            self.current = None
            self.hw.buzzer_lock.release()

    def stop(self) -> None:
        if self.playing:
            self._stop.set()
            self.hw.gpio.stop_tone()
            self._thread.join(timeout=1.0)  # type: ignore[union-attr]
        self._thread = None

    def level(self, now: float) -> float:
        """0..1 'loudness' for the visualizer: a pluck that decays per note."""
        note = self.current
        if not self.playing or note is None or note.freq <= 0:
            return 0.0
        age = (now - self.started_note) / max(0.05, note.seconds)
        return max(0.15, 1.0 - 0.8 * age)

    def pitch(self) -> float:
        """0..1 position of the current note between C3 and C7 (for the visualizer)."""
        note = self.current
        if note is None or note.freq <= 0:
            return 0.0
        import math

        return max(0.0, min(1.0, (math.log2(note.freq) - math.log2(130.8)) / 4))

    def ui_tick(self, ctx: "Context", now: float) -> None:
        """Report a finished song on the ticker line."""
        if self._finished:
            ctx.notify(self._finished)
            self._finished = None


def get_player(ctx: "Context") -> MusicPlayer:
    from cyberdeck.features.hardware import get_hardware

    gap = Settings.load("hardware.ini", ctx.config_dir).get_float("music", "gap", 0.1)
    return ctx.service("music", lambda: MusicPlayer(get_hardware(ctx), gap))


# --- display helpers ------------------------------------------------------------------

def piano_roll(song: Song, width: int = 60, rows: int = 8) -> list[str]:
    """A tiny ASCII piano roll: pitch goes up, time goes right."""
    pitched = [n for n in song.notes if n.freq > 0]
    if not pitched:
        return ["(no notes)"]
    import math

    lo = min(math.log2(n.freq) for n in pitched)
    hi = max(math.log2(n.freq) for n in pitched)
    span = (hi - lo) or 1.0
    cols: list[int | None] = []
    for note in song.notes[:width]:
        cols.append(None if note.freq <= 0 else int(round((math.log2(note.freq) - lo) / span * (rows - 1))))
    grid = []
    for level in range(rows - 1, -1, -1):
        grid.append("".join("█" if c == level else ("·" if c is None and level == 0 else " ") for c in cols))
    # Skip pitch rows no note uses, so short tunes stay compact.
    return [row for row in grid if row.strip()]


def music_pane(ctx: "Context", width: int, height: int) -> list[Row]:
    """What is playing, with the current note."""
    player = ctx.services.get("music")
    if player is None or not player.playing or player.song is None:
        return [[("nothing playing", "dim")], [("music play <song>", "dim")]]
    song, note = player.song, player.current
    done = player.position + 1
    rows: list[Row] = [
        [("♪ ", "title"), (song.title, "text")],
        [("note ", "dim"), (note.name if note else "-", "prompt"),
         (f"  {note.freq:.0f}Hz" if note and note.freq else "", "dim")],
        [("prog ", "dim"), (f"{done}/{len(song.notes)}", "text")],
    ]
    return rows[:height]


# --- commands ------------------------------------------------------------------------

def register(reg: "Registry") -> None:
    """Add the music command and the music panel."""

    def complete(ctx: "Context", args: list[str]) -> list[str]:
        return list_songs(ctx) if len(args) == 1 and args[0] in {"play", "show"} else []

    @reg.command("music", "Chiptunes on the buzzer: list, play, stop, now, show.",
                 usage="music [list|play <song>|stop|now|show <song>]",
                 subcommands=("list", "play", "stop", "now", "show"), completer=complete)
    def cmd_music(ctx: "Context", args: list[str]) -> list[str]:
        action = args[0].lower() if args else "list"
        if action == "list":
            names = list_songs(ctx)
            if not names:
                return [f"no songs in {songs_dir(ctx)} — add .txt files (see CUSTOMIZING.md)"]
            lines = []
            for name in names:
                try:
                    song = load_song(ctx, name)
                    lines.append(f"  {name:<16} {song.title}  ({song.seconds:.0f}s, {song.tempo} bpm)")
                except SongError as exc:
                    lines.append(f"  {name:<16} (broken: {exc})")
            return lines
        if action in {"play", "show"}:
            if len(args) < 2:
                return [f"usage: music {action} <song>   (songs: {', '.join(list_songs(ctx))})"]
            try:
                song = load_song(ctx, args[1])
            except SongError as exc:
                return [str(exc)]
            if action == "show":
                return [f"{song.title} — {len(song.notes)} notes, {song.seconds:.1f}s"] + piano_roll(song)
            player = get_player(ctx)
            # In headless mode there is no UI loop, so play to the end before returning.
            if not player.play(song, block=not ctx.interactive):
                return ["buzzer busy — try again in a moment"]
            summary = f"♪ {song.title} ({song.seconds:.0f}s) on GPIO{player.hw.buzzer} [{player.hw.gpio.name}]"
            if not ctx.interactive:
                return [summary, *piano_roll(song), "done."]
            return [summary + " — `music stop` to stop"]
        if action == "stop":
            player = get_player(ctx)
            was = player.playing
            player.stop()
            return ["stopped." if was else "nothing was playing."]
        if action == "now":
            player = get_player(ctx)
            if not player.playing or player.song is None:
                return ["nothing playing."]
            note = player.current
            return [f"♪ {player.song.title}  note {player.position + 1}/{len(player.song.notes)}"
                    f"  {note.name if note else ''}"]
        return ["usage: music [list|play <song>|stop|now|show <song>]"]

    register_pane("music", "MUSIC", music_pane)
