"""Caption retrieval + transcript assembly.

No video or audio is ever downloaded. Only the caption tracks are fetched:

* prefer manually authored ``subtitles`` over ``automatic_captions``;
* parse both WebVTT (``.vtt``) and YouTube json3 (``.json3``);
* collapse the duplicated lines that rolling auto-captions produce;
* merge cues into readable paragraphs with coarse ``[H:MM:SS]`` markers.

The output is audio-only text: timestamps let the notes model say *when*
something was said, never *what was shown*.
"""

from __future__ import annotations

import html
import json
import re
from typing import Iterable, Optional

from .logging_setup import get_logger

log = get_logger("transcript")

PREFERRED_LANGS = ["id", "en", "en-US", "en-GB", "en-orig"]
# English auto-caption variants carry translated tracks (``tlang=``); such a
# machine translation of a lecture is worse than no preference at all, so they
# are only used when every other candidate failed.
_TLANG_RE = re.compile(r"[?&]tlang=")
VTT_TIME_RE = re.compile(
    r"(?:(\d{1,2}):)?(\d{1,2}):(\d{2})[.,](\d{1,3})\s*-->\s*(?:(\d{1,2}):)?(\d{1,2}):(\d{2})[.,](\d{1,3})"
)
TAG_RE = re.compile(r"<[^>]+>")
INLINE_TS_RE = re.compile(r"<\d{2}:\d{2}:\d{2}\.\d{3}>")
MUSIC_RE = re.compile(r"[\[(](?:musik|music|applause|tepuk tangan|laughter|tertawa|noise|sings?)[^\])]*[\])]", re.I)
# Minimum shared prefix length before we treat a cue head as a repeated overlap.
MIN_OVERLAP = 5
_TRAIL_WORD_RE = re.compile(r"([^\s]+)$")


class TranscriptError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Track selection
# ---------------------------------------------------------------------------
def _norm_lang(code: str) -> str:
    return (code or "").strip().lower()


def _pick_track(info: dict) -> Optional[dict]:
    """Choose the best caption track: manual first, then automatic."""
    subs = info.get("subtitles") or {}
    autos = info.get("automatic_captions") or {}

    def ranked_formats(formats: list) -> list[dict]:
        return sorted(
            formats or [],
            key=lambda f: (
                {"json3": 0, "vtt": 1}.get(str(f.get("ext", "")).lower(), 5),
                1 if _TLANG_RE.search(str(f.get("url") or "")) else 0,
                0 if f.get("url") else 1,
            ),
        )

    def prefer_langs(order: Iterable[str], tracks: dict) -> list[str]:
        """Preferred languages first, a native original next, then the rest.

        The original speech language usually sits first in YouTube's caption
        dict; an English preference is still honoured first when a native
        English track exists.
        """
        out: list[str] = []
        for lang in PREFERRED_LANGS:
            out.extend(k for k in tracks if _norm_lang(k) == _norm_lang(lang) and k not in out)
        out.extend(k for k in tracks if k not in out)
        return out

    def first_usable(tracks: dict, kind: str, langs: Iterable[str]) -> Optional[dict]:
        # Native tracks (no ``tlang``) across all languages beat a machine
        # translation in a preferred language.
        for allow_tlang in (False, True):
            for lang in langs:
                for fmt in ranked_formats(tracks.get(lang)):
                    ext = str(fmt.get("ext", "")).lower()
                    if ext not in {"json3", "vtt", "srv3", "json"} or not fmt.get("url"):
                        continue
                    if not allow_tlang and _TLANG_RE.search(str(fmt.get("url"))):
                        continue
                    return {"lang": lang, "ext": ext, "url": fmt["url"], "kind": kind}
        return None

    track = first_usable(subs, "subtitles", prefer_langs(PREFERRED_LANGS, subs))
    if track is None:
        track = first_usable(autos, "automatic_captions", prefer_langs(PREFERRED_LANGS, autos))
    if track is None:
        return None
    # Extra candidates used only if the chosen track cannot be downloaded
    # (empty / 429), so one bad signed URL never costs us the whole video.
    alternates: list[dict] = []
    for kind, tracks in (("subtitles", subs), ("automatic_captions", autos)):
        for lang in prefer_langs(PREFERRED_LANGS, tracks):
            for fmt in ranked_formats(tracks.get(lang)):
                ext = str(fmt.get("ext", "")).lower()
                if ext in {"json3", "vtt", "srv3", "json"} and fmt.get("url"):
                    cand = {"lang": lang, "ext": ext, "url": fmt["url"], "kind": kind}
                    if cand["url"] != track["url"]:
                        alternates.append(cand)
                if len(alternates) >= 4:
                    break
            if len(alternates) >= 4:
                break
        if len(alternates) >= 4:
            break
    track["alternates"] = alternates
    return track


# ---------------------------------------------------------------------------
# Parsers
# ---------------------------------------------------------------------------
def parse_vtt(text: str) -> list[tuple[float, str]]:
    """Return [(start_seconds, line_text), ...] from a WebVTT file."""
    cues: list[tuple[float, str]] = []
    block: list[str] = []
    text = text.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")

    def flush(lines: list[str]) -> None:
        if not lines:
            return
        idx = None
        for i, line in enumerate(lines):
            if "-->" in line:
                idx = i
                break
        if idx is None:
            return
        m = VTT_TIME_RE.search(lines[idx])
        if not m:
            return
        h = int(m.group(1) or 0)
        start = h * 3600 + int(m.group(2)) * 60 + int(m.group(3)) + int(m.group(4).ljust(3, "0")) / 1000
        # YouTube's rolling VTT repeats the previous line(s) inside each cue.
        # We keep only the text that is new in this cue (``_dedupe_cues`` then
        # handles the word-level overlap between cues).
        body_lines: list[str] = []
        for raw in lines[idx + 1 :]:
            cleaned = INLINE_TS_RE.sub("", raw)
            cleaned = TAG_RE.sub("", cleaned)
            cleaned = re.sub(r"\s+", " ", html.unescape(cleaned)).strip()
            if not cleaned:
                continue
            if body_lines and cleaned == body_lines[-1]:
                continue
            body_lines.append(cleaned)
        if len(body_lines) >= 2:
            body_lines = [body_lines[-1]]
        body = " ".join(body_lines).strip()
        if body:
            cues.append((start, body))

    for line in text.split("\n"):
        if line.strip() == "":
            flush(block)
            block = []
        elif line.strip().upper().startswith(("WEBVTT", "NOTE", "STYLE", "REGION")):
            continue
        else:
            block.append(line)
    flush(block)
    return cues


def parse_json3(text: str) -> list[tuple[float, str]]:
    """Return [(start_seconds, line_text), ...] from a YouTube json3 file."""
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise TranscriptError(f"invalid json3 payload: {exc}") from exc
    cues: list[tuple[float, str]] = []
    for event in data.get("events") or []:
        segs = event.get("segs") or []
        start_ms = event.get("tStartMs") or 0
        body = "".join(seg.get("utf8", "") for seg in segs)
        body = body.replace("\n", " ").strip()
        if body:
            cues.append((float(start_ms) / 1000.0, body))
    return cues


def _dedupe_cues(cues: Iterable[tuple[float, str]]) -> list[tuple[float, str]]:
    """Collapse rolling duplicates produced by auto-captions.

    Auto-captions often emit ``"so today we"``, ``"we will talk"`` where the
    previous line's tail is repeated at the head of the next. We drop lines
    that are a prefix/substring of the previous kept line and strip the
    repeated tail-word overlap.
    """
    out: list[tuple[float, str]] = []
    for start, text in cues:
        if not text:
            continue
        if not out:
            out.append((start, text))
            continue
        prev_start, prev_text = out[-1]
        if text == prev_text:
            continue
        if text in prev_text and len(text) < len(prev_text):
            continue
        if prev_text.endswith(text):
            continue
        overlap = 0
        max_check = len(text)
        for n in range(max_check, MIN_OVERLAP - 1, -1):
            prefix = text[:n]
            if not prev_text.endswith(prefix):
                continue
            if not (prefix[-1].isalnum() or text[n : n + 1] in ("",) or not text[n].isalnum()):
                continue
            overlap = n
            break
        if overlap:
            text = text[overlap:].strip(" ,.-")
            if not text:
                continue
        else:
            # Rolling captions often repeat the last word(s) of the previous cue
            # at the start of the next one ("so today we" / "we will talk").
            m = _TRAIL_WORD_RE.search(prev_text)
            tail = m.group(1) if m else ""
            head = re.match(r"^(\S+)(?:\s+|$)", text)
            fillers = {"dan", "yang", "untuk", "the", "a", "of", "in", "to", "and", "is", "itu", "ini"}
            if tail and head and tail.lower() == head.group(1).lower() and tail.lower() not in fillers:
                text = text[head.end():].strip()
                if not text:
                    continue
        out.append((start, text))
    return out


def _join_words(parts: Iterable[str]) -> str:
    text = ""
    for part in parts:
        if not text:
            text = part
        elif re.search(r"[A-Za-z0-9]$", text) and re.match(r"^[A-Za-z0-9]", part):
            text += " " + part
        else:
            text += " " + part
    # Auto-caption punctuation often misses spaces before these.
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def format_ts(seconds: float) -> str:
    total = max(0, int(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


def merge_paragraphs(
    cues: Iterable[tuple[float, str]],
    paragraph_seconds: int = 90,
    words_per_paragraph: int = 170,
    with_markers: bool = True,
) -> str:
    """Group cues into paragraphs of ~90 s / ~170 words with ``[H:MM:SS]`` markers."""
    paragraphs: list[tuple[float, list[str]]] = []
    current: list[str] = []
    current_start = 0.0
    current_words = 0
    last_start = 0.0
    for start, text in cues:
        words = len(text.split())
        if not current:
            current_start = start
        elif (start - current_start >= paragraph_seconds) or current_words >= words_per_paragraph:
            paragraphs.append((current_start, current))
            current = []
            current_start = start
            current_words = 0
        current.append(text)
        current_words += words
        last_start = start
    if current:
        paragraphs.append((current_start, current))

    blocks: list[str] = []
    for start, parts in paragraphs:
        body = _join_words(parts)
        body = MUSIC_RE.sub("", body).strip()
        if not body:
            continue
        if with_markers:
            blocks.append(f"[{format_ts(start)}] {body}")
        else:
            blocks.append(body)
    return "\n\n".join(blocks)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def _http_get(url: str, timeout: int = 30) -> str:
    import requests

    resp = requests.get(
        url,
        timeout=timeout,
        headers={"User-Agent": "Mozilla/5.0 (lecture-notes-bot)"},
    )
    resp.raise_for_status()
    return resp.text


def fetch_caption_cues(video_url: str, lang: Optional[str] = None) -> tuple[list[tuple[float, str]], dict]:
    """Fetch and parse the best caption track for one video.

    Returns ``(cues, meta)`` where meta has ``lang`` and ``kind``
    ("subtitles" or "automatic_captions").
    """
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:  # pragma: no cover
        raise TranscriptError(f"yt-dlp is not installed: {exc}") from exc

    opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "writesubtitles": False,
        "writeautomaticsub": False,
        "noplaylist": True,
        "socket_timeout": 30,
        "ignoreerrors": False,
    }
    with YoutubeDL(opts) as ydl:
        try:
            info = ydl.extract_info(video_url, download=False)
        except Exception as exc:  # noqa: BLE001
            raise TranscriptError(f"failed to read video info: {exc}") from exc
    if not info:
        raise TranscriptError("empty video info")

    track = _pick_track(info) if not lang else None
    if lang:
        subs = info.get("subtitles") or {}
        autos = info.get("automatic_captions") or {}
        for source, kind in ((subs, "subtitles"), (autos, "automatic_captions")):
            for code, formats in source.items():
                if _norm_lang(code) != _norm_lang(lang):
                    continue
                for fmt in formats:
                    if str(fmt.get("ext", "")).lower() in {"json3", "vtt"} and fmt.get("url"):
                        track = {"lang": code, "ext": fmt["ext"], "url": fmt["url"], "kind": kind}
                        break
                if track:
                    break
            if track:
                break
    if not track:
        raise TranscriptError("no usable caption track (manual or automatic)")

    candidates = [track] + list(track.get("alternates") or [])
    cues: list[tuple[float, str]] = []
    errors: list[str] = []
    used = track
    for cand in candidates:
        try:
            raw = _http_get(cand["url"])
        except Exception as exc:  # noqa: BLE001 - 429 / expired signature etc.
            errors.append(f"{cand.get('lang')}/{cand['ext']}: {exc}")
            continue
        ext = str(cand["ext"]).lower()
        if ext in {"json3", "json", "srv3"}:
            try:
                parsed = parse_json3(raw)
            except TranscriptError:
                parsed = parse_vtt(raw)
        else:
            parsed = parse_vtt(raw)
        parsed = _dedupe_cues(parsed)
        if parsed:
            cues, used = parsed, cand
            break
        errors.append(f"{cand.get('lang')}/{cand['ext']}: no text")
    if not cues:
        raise TranscriptError("failed to obtain captions: " + "; ".join(errors[:3]))
    track = used
    meta = {
        "lang": track.get("lang"),
        "kind": track.get("kind"),
        "title": info.get("title") or "",
        "description": (info.get("description") or "")[:1500],
        "duration": info.get("duration"),
        "uploader": info.get("uploader") or info.get("channel") or "",
        "upload_date": info.get("upload_date"),
        "video_id": info.get("id") or "",
    }
    return cues, meta


def build_transcript(
    video_url: str,
    lang: Optional[str] = None,
    with_markers: bool = True,
) -> tuple[str, dict]:
    """Full pipeline for one video: cues -> paragraphs -> text."""
    cues, meta = fetch_caption_cues(video_url, lang=lang)
    text = merge_paragraphs(cues, with_markers=with_markers)
    if not text.strip():
        raise TranscriptError("transcript empty after merging")
    meta["chars"] = len(text)
    meta["cues"] = len(cues)
    return text, meta


def word_count(text: str) -> int:
    return len(re.findall(r"\S+", text or ""))
