"""Flat playlist enumeration via yt-dlp (no downloads, no video/audio access).

We only ever ask yt-dlp for metadata: the playlist is read with
``extract_flat="in_playlist"`` so each video costs one entry, not a full
extraction. Per-video caption work happens later in ``transcript.py``.
"""

from __future__ import annotations

import re
from typing import Optional

from .logging_setup import get_logger

log = get_logger("playlist")

_VIDEO_ID_RE = re.compile(r"(?:v=|youtu\.be/|/shorts/|/embed/|/live/)([A-Za-z0-9_-]{11})")
# Lecture uploads are often titled "M6 SPK 2026 11 09 09 04 36": the date the
# lecturer recorded is in the title even when yt-dlp's flat extraction reports
# no upload timestamp.
_TITLE_DATE_RE = re.compile(
    r"(?<!\d)(20\d{2})[ _\-./](\d{1,2})[ _\-./](\d{1,2})(?!\d)"
)


class PlaylistError(RuntimeError):
    pass


def video_id_from_url(url: str) -> Optional[str]:
    if not url:
        return None
    m = _VIDEO_ID_RE.search(url)
    if m:
        return m.group(1)
    # bare 11-char ID
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", url.strip()):
        return url.strip()
    return None


def is_playlist_url(url: str) -> bool:
    if not url:
        return False
    return ("list=" in url) or ("/playlist" in url) or ("/channel/" in url and "/videos" in url)


def date_from_title(title: str) -> Optional[str]:
    """'M6 SPK 2026 11 09 09 04 36' -> '20261109' (None when absent/invalid)."""
    import datetime as _dt

    for match in _TITLE_DATE_RE.finditer(str(title or "")):
        try:
            d = _dt.date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
        except ValueError:
            continue
        return d.strftime("%Y%m%d")
    return None


def _entry_to_video(entry: dict) -> Optional[dict]:
    if not isinstance(entry, dict):
        return None
    vid = entry.get("id")
    if not vid:
        vid = video_id_from_url(str(entry.get("url") or ""))
    if not vid:
        return None
    title = entry.get("title") or ""
    uploader = entry.get("uploader") or entry.get("channel") or ""
    duration = entry.get("duration")
    timestamp = entry.get("timestamp") or entry.get("release_timestamp")
    upload_date = entry.get("upload_date")
    if not upload_date and timestamp:
        try:
            import datetime as _dt

            upload_date = _dt.datetime.fromtimestamp(int(timestamp), _dt.timezone.utc).strftime(
                "%Y%m%d"
            )
        except (TypeError, ValueError, OSError):
            upload_date = None
    return {
        "id": str(vid),
        "title": str(title),
        "url": entry.get("url") or f"https://www.youtube.com/watch?v={vid}",
        "webpage_url": entry.get("webpage_url") or f"https://www.youtube.com/watch?v={vid}",
        "duration": duration if isinstance(duration, (int, float)) else None,
        "uploader": str(uploader),
        "upload_date": upload_date,
        "title_date": date_from_title(title),
        "index": entry.get("playlist_index"),
    }


def _ydl_opts() -> dict:
    return {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": "in_playlist",
        "ignoreerrors": True,
        "noplaylist": False,
        "socket_timeout": 30,
        "retries": 3,
        "verbose": False,
    }


def enumerate_playlist(url: str, limit: Optional[int] = None) -> list[dict]:
    """Return ``[{id,title,url,webpage_url,duration,uploader,...}, ...]``.

    Order follows the playlist order. ``limit`` truncates after enumeration has
    started, but yt-dlp still reports the full count through ``info``.
    """
    if not url:
        raise PlaylistError("empty playlist URL")
    try:
        from yt_dlp import YoutubeDL
    except ImportError as exc:  # pragma: no cover - dependency issue
        raise PlaylistError(f"yt-dlp is not installed: {exc}") from exc

    videos: list[dict] = []
    with YoutubeDL(_ydl_opts()) as ydl:
        try:
            info = ydl.extract_info(url, download=False)
        except Exception as exc:  # noqa: BLE001 - network/parse errors are expected
            raise PlaylistError(f"failed to enumerate {url}: {exc}") from exc
    if not info:
        raise PlaylistError(f"no playlist info returned for {url}")

    if info.get("_type") == "playlist" or "entries" in info:
        entries = [e for e in (info.get("entries") or []) if e]
        title = info.get("title") or ""
    else:
        entries = [info]
        title = info.get("title") or ""
    playlist_id = info.get("id")

    for entry in entries:
        video = _entry_to_video(entry)
        if video is None:
            continue
        if limit is not None and len(videos) >= limit:
            break
        videos.append(video)

    log.debug("playlist %s (%s): %d entries", url, title, len(videos))
    for i, v in enumerate(videos, start=1):
        # yt-dlp's flat extraction does not always report playlist_index; the
        # enumeration order is the playlist order, so index it ourselves.
        v["index"] = i
        v["playlist_title"] = title
        v["playlist_id"] = playlist_id
    return videos


def upload_date_of(video: dict) -> Optional[str]:
    """YYYYMMDD when known: real upload date first, then the date in the title."""
    date = str(video.get("upload_date") or "")
    if len(date) == 8 and date.isdigit():
        return date
    return video.get("title_date") or date_from_title(str(video.get("title") or ""))


def oldest_first(videos: list[dict]) -> list[dict]:
    """Sort oldest upload first; undated videos keep their playlist order last.

    YouTube playlist order is not upload order (a lecture uploaded today can sit
    at the bottom while M1 sits in the middle), so catch-up runs with ``--limit``
    should start from the oldest lecture.
    """
    dated = [v for v in videos if upload_date_of(v)]
    undated = [v for v in videos if not upload_date_of(v)]
    dated.sort(key=lambda v: (upload_date_of(v) or "", int(v.get("index") or 0)))
    undated.sort(key=lambda v: int(v.get("index") or 0))
    return dated + undated


def list_playlist(url: str, limit: Optional[int] = None) -> list[dict]:
    """Used by the ``list`` CLI command."""
    return enumerate_playlist(url, limit=limit)
