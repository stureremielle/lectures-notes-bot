"""Which course does a video belong to?

A single playlist can hold several courses of the same semester (e.g.
"PTA 2026/2027" with ``M4 TIS …``, ``SPK M5 …``, ``AKS M1 …`` uploads). Using
the playlist name as the course would label everything with the semester, so the
course is resolved per video, in this order:

1. ``playlists[].course_aliases`` from config.yaml, matched anywhere in the
   video title (``TIS``, ``Akuntansi Kinerja Sistem``, …);
2. the name of another configured playlist that the title starts with;
3. the title prefix before a separator, e.g. ``Sistem Multimedia - Pertemuan 3``
   → ``Sistem Multimedia``;
4. ``pipeline.course_fallback`` (default ``"Lainnya"``).

The resolver only labels; it never invents a course that was not configured
unless the title itself carries it.
"""

from __future__ import annotations

import re
from typing import Iterable, Optional

from .logging_setup import get_logger

log = get_logger("courses")

_ALIAS_SPLIT_RE = re.compile(r"[,;]")
# "M4 TIS 2026 10 29", "M5\\M3 …", "ST M1 2026 10 02" -> the course token is the
# first alphabetic token after an optional "M<n>" marker.
_COURSE_TOKEN_RE = re.compile(r"^(?:m\d+[\s\\/|,]*)?([A-Za-z][A-Za-z&]{1,9})\b")
_SEPARATORS = (" - ", " – ", " — ", " | ", ": ", "; ", ", ")


def parse_aliases(raw: str) -> list[str]:
    return [a.strip() for a in _ALIAS_SPLIT_RE.split(raw or "") if a.strip()]


class AliasMap:
    """Case-insensitive alias -> canonical course name."""

    def __init__(self, entries: Iterable[tuple[str, str]] = ()):
        # longest alias first so "AKS" does not shadow "AKS 2"
        self._map: dict[str, str] = {
            alias.lower(): course for alias, course in entries if alias
        }
        self._keys = sorted(self._map, key=len, reverse=True)

    def __bool__(self) -> bool:
        return bool(self._map)

    def match(self, text: str) -> Optional[str]:
        """Whole-word alias match (``tis`` matches "M6 TIS 2026", not "artist")."""
        lowered = (text or "").lower()
        for alias in self._keys:
            if re.search(rf"(?<![a-z0-9]){re.escape(alias)}(?![a-z0-9])", lowered):
                return self._map[alias]
        return None

    def canonical(self, value: str) -> str:
        return self._map.get((value or "").lower(), value or "")

    def as_dict(self) -> dict:
        return dict(self._map)

    @classmethod
    def from_config(cls, cfg) -> "AliasMap":
        entries: list[tuple[str, str]] = []
        for pl in cfg.playlists():
            for alias in parse_aliases(str(pl.get("course_aliases") or "")):
                entries.append((alias, pl["name"]))
        return cls(entries)


def _prefix_before_separator(title: str) -> Optional[str]:
    for sep in _SEPARATORS:
        if sep in title:
            head = title.split(sep, 1)[0].strip()
            if 2 <= len(head) <= 40:
                return head
    return None


_NOISE_TOKENS = {
    "rekaman", "kuliah", "video", "pertemuan", "kelas", "materi", "rapat",
    "kelompok", "tugas", "part", "bagian", "sesi", "kuliah ke", "ujian",
    "presentasi", "pr", "sesi", "recording", "zoom",
}


def _code_from_title(title: str) -> Optional[str]:
    """Course code as it appears first in the title.

    Accepts ``M4 TIS …`` and ``PPSI M5\\M3 …``; single words (``KELOMPOK 1 …``)
    only count when written like a code (all caps, <= 5 letters).
    """
    stripped = (title or "").strip()
    m = _COURSE_TOKEN_RE.match(stripped)
    if not m:
        return None
    token = m.group(1)
    if token.lower() in _NOISE_TOKENS:
        return None
    is_code_shape = token.isupper() and len(token) <= 5
    if not is_code_shape:
        return None
    return token


def _looks_like_code(token: str) -> bool:
    return token.isupper() and 2 <= len(token) <= 5 and token.lower() not in _NOISE_TOKENS


def resolve_course(
    video: dict,
    aliases: Optional[AliasMap] = None,
    playlist_names: Iterable[str] = (),
    fallback: str = "Lainnya",
) -> str:
    """Best-effort course label for one video. Never raises."""
    title = str(video.get("title") or "").strip()
    aliases = aliases or AliasMap()

    hit = aliases.match(title)
    if hit:
        return hit

    lowered = title.lower()
    for name in sorted({n for n in playlist_names if n}, key=len, reverse=True):
        if lowered.startswith(name.lower()):
            return name

    prefix = _prefix_before_separator(title)
    if prefix:
        hit = aliases.match(prefix)
        if hit:
            return hit
        # "Sistem Multimedia - Pertemuan 3" -> "Sistem Multimedia"
        if len(prefix.split()) >= 2:
            return prefix

    code = _code_from_title(title)
    if code:
        log.debug("course code %s not in course_aliases (title: %s)", code, title[:80])
        return code

    return fallback
