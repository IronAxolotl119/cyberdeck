"""netcache.py — fetch things from the internet politely, with a cache.

Weather and ISS data come from free web services. To stay light on a
Pi (and on those services), every download is saved in data/cache/
with the time it was fetched:

  * younger than `max_age` seconds  -> used straight from the cache
  * older, and the network works    -> downloaded again and re-saved
  * older, and the network is down  -> the old copy is used anyway
                                       ("stale"), so the deck still
                                       shows something when offline
  * nothing cached and offline      -> a clear "offline" error

fetch() never raises for network problems; it returns a Fetched with
`text` (or None) and a short `status` you can show on screen.
Standard library only (urllib).
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

USER_AGENT = "cyberdeck-main/0.2 (hobby project; stdlib urllib)"


@dataclass(frozen=True)
class Fetched:
    text: str | None
    status: str          # live, cached, stale, offline
    fetched_at: float | None = None
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.text is not None

    def age_label(self, now: float | None = None) -> str:
        """'live', 'cached 4m ago', 'offline — data from 3h ago'…"""
        if self.fetched_at is None:
            return self.status
        age = max(0, (now or time.time()) - self.fetched_at)
        human = f"{age / 3600:.1f}h" if age >= 3600 else f"{age / 60:.0f}m"
        if self.status == "live":
            return "live"
        if self.status == "stale":
            return f"offline — data from {human} ago"
        return f"cached {human} ago"


def _cache_file(cache_dir: Path, url: str) -> Path:
    return cache_dir / (hashlib.sha1(url.encode()).hexdigest()[:16] + ".json")


def read_cache(cache_dir: Path, url: str) -> tuple[str, float] | None:
    try:
        data = json.loads(_cache_file(cache_dir, url).read_text(encoding="utf-8"))
        return data["body"], float(data["fetched_at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def write_cache(cache_dir: Path, url: str, body: str, when: float) -> None:
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        _cache_file(cache_dir, url).write_text(json.dumps({"url": url, "fetched_at": when, "body": body}),
                                               encoding="utf-8")
    except OSError:
        pass


def download(url: str, timeout: float = 8.0) -> str:
    """Plain GET. Raises OSError/URLError on failure."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 — fixed https URLs from config
        return response.read().decode("utf-8", errors="replace")


def fetch(url: str, cache_dir: Path, max_age: float, timeout: float = 8.0, force: bool = False,
          now: float | None = None, downloader=download) -> Fetched:
    """Cached GET. See the module docstring for the rules."""
    now = time.time() if now is None else now
    cached = read_cache(cache_dir, url)
    if cached and not force and now - cached[1] < max_age:
        return Fetched(cached[0], "cached", cached[1])
    try:
        body = downloader(url, timeout)
    except (OSError, urllib.error.URLError, ValueError) as exc:
        reason = str(getattr(exc, "reason", exc))
        if cached:
            return Fetched(cached[0], "stale", cached[1], reason)
        return Fetched(None, "offline", None, reason)
    write_cache(cache_dir, url, body, now)
    return Fetched(body, "live", now)
