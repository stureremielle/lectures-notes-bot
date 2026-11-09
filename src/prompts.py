"""Prompt template loading and rendering (``str.format`` with ``{{`` escaping)."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from .logging_setup import get_logger

log = get_logger("prompts")


class PromptError(RuntimeError):
    """A prompt file is missing or contains braces that break rendering."""

_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"
_cache: dict[str, str] = {}

FALLBACKS: dict[str, str] = {
    "notes_system": (
        "Kamu adalah asisten akademik yang membuat catatan kuliah berbahasa Indonesia "
        "dari transkrip audio saja. Kamu tidak melihat slide atau teks di layar. "
        "Jangan pernah mengarang isi visual."
    ),
    "notes_user": (
        "Mata kuliah: {course_name}\nJudul video: {video_title}\n\n"
        "Transkrip:\n{transcript}\n\nBalas dengan JSON saja."
    ),
    "condense": (
        "Ringkas bagian transkrip kuliah berikut menjadi poin-poin padat berbahasa Indonesia. "
        "Pertahankan istilah teknis, dan semua penyebutan tugas, deadline, serta tanggal.\n\n"
        "{chunk}"
    ),
}


def prompt_dir() -> Path:
    return _PROMPT_DIR


def load_prompt(name: str, directory: Optional[Path] = None) -> str:
    key = f"{directory or _PROMPT_DIR}|{name}"
    if key in _cache:
        return _cache[key]
    path = (directory or _PROMPT_DIR) / f"{name}.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        log.warning("prompt file %s missing; using built-in fallback", path)
        text = FALLBACKS.get(name, "")
    _cache[key] = text
    return text


def render(name: str, directory: Optional[Path] = None, **values: Any) -> str:
    """Render a template; unknown placeholders are left untouched."""
    template = load_prompt(name, directory)

    class _Safe(dict):
        def __missing__(self, key):  # leave unknown placeholders untouched
            return "{" + key + "}"

    try:
        return template.format_map(_Safe(**values))
    except (ValueError, IndexError, KeyError) as exc:
        raise PromptError(
            f"prompt template {name!r} cannot be rendered (stray braces?): {exc}"
        ) from exc


def available(directory: Optional[Path] = None) -> list[str]:
    d = directory or _PROMPT_DIR
    if not d.exists():
        return []
    return sorted(p.stem for p in d.glob("*.md"))
