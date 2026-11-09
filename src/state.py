"""Persistent state: ``data/state.json`` with atomic writes.

Schema (all keys always present after load)::

    {
      "videos":          { "<video_id>": { ...processed record... } },
      "reminded":        ["<assignment_key>@T-1", ...],
      "calendar_last_week": "2026-W45" | null,
      "baselines":       { "<playlist_url>": "<ISO timestamp>" },
      "skipped":         ["<video_id>", ...]
    }

A corrupt file is renamed aside and a fresh state is used — the bot never
crashes because of it.
"""

from __future__ import annotations

import copy
import datetime as _dt
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, Iterable, Optional

from .logging_setup import get_logger

log = get_logger("state")


def _empty() -> dict:
    return {
        "videos": {},
        "reminded": [],
        "calendar_last_week": None,
        "baselines": {},
        "skipped": [],
    }


class State:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.data: dict = _empty()

    # -- load / save -------------------------------------------------------
    def load(self) -> "State":
        if not self.path.exists():
            self.data = _empty()
            return self
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("state root is not an object")
        except (OSError, ValueError) as exc:
            self._quarantine(exc)
            self.data = _empty()
            return self
        merged = _empty()
        videos = raw.get("videos")
        merged["videos"] = videos if isinstance(videos, dict) else {}
        reminded = raw.get("reminded")
        merged["reminded"] = [str(x) for x in reminded] if isinstance(reminded, list) else []
        clw = raw.get("calendar_last_week")
        merged["calendar_last_week"] = str(clw) if clw else None
        baselines = raw.get("baselines")
        merged["baselines"] = (
            {str(k): str(v) for k, v in baselines.items()} if isinstance(baselines, dict) else {}
        )
        skipped = raw.get("skipped")
        merged["skipped"] = [str(x) for x in skipped] if isinstance(skipped, list) else []
        self.data = merged
        return self

    def _quarantine(self, exc: Exception) -> None:
        stamp = _dt.datetime.now().strftime("%Y%m%dT%H%M%S")
        backup = self.path.with_name(f"{self.path.name}.corrupt-{stamp}")
        try:
            shutil.copy2(self.path, backup)
            log.warning("state file is corrupt (%s); kept a copy at %s and starting fresh", exc, backup)
        except OSError:
            log.warning("state file is corrupt (%s); starting fresh (copy failed)", exc)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.data, ensure_ascii=False, indent=2, sort_keys=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=self.path.name + ".", suffix=".tmp", dir=str(self.path.parent)
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp_name, self.path)
        except BaseException:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    # -- videos ------------------------------------------------------------
    def has_video(self, video_id: str) -> bool:
        return video_id in self.data["videos"]

    def get_video(self, video_id: str) -> Optional[dict]:
        return self.data["videos"].get(video_id)

    def add_video(self, video_id: str, record: dict) -> None:
        if not video_id:
            raise ValueError("add_video() needs a video id (refusing to store an empty key)")
        record = dict(record)
        record.setdefault("added_at", _dt.datetime.now().isoformat(timespec="seconds"))
        self.data["videos"][video_id] = record

    def all_videos(self) -> list[dict]:
        return list(self.data["videos"].values())

    # -- skipped -----------------------------------------------------------
    def is_skipped(self, video_id: str) -> bool:
        return video_id in self.data["skipped"]

    def mark_skipped(self, video_ids: Iterable[str]) -> None:
        existing = set(self.data["skipped"])
        for vid in video_ids:
            if vid and vid not in existing:
                self.data["skipped"].append(vid)
                existing.add(vid)

    # -- baselines ---------------------------------------------------------
    def has_baseline(self, playlist_url: str) -> bool:
        return bool(playlist_url) and playlist_url in self.data["baselines"]

    def baseline(self, playlist_url: str) -> Optional[str]:
        return self.data["baselines"].get(playlist_url)

    def stamp_baseline(self, playlist_url: str, when: Optional[_dt.datetime] = None) -> str:
        when = when or _dt.datetime.now()
        value = when.isoformat(timespec="seconds")
        self.data["baselines"][playlist_url] = value
        return value

    # -- reminded ----------------------------------------------------------
    def is_reminded(self, key: str) -> bool:
        return key in self.data["reminded"]

    def mark_reminded(self, key: str) -> None:
        if key not in self.data["reminded"]:
            self.data["reminded"].append(key)

    # -- calendar ----------------------------------------------------------
    def calendar_week(self) -> Optional[str]:
        return self.data.get("calendar_last_week")

    def set_calendar_week(self, week: str) -> None:
        self.data["calendar_last_week"] = week

    # -- convenience -------------------------------------------------------
    def mark_all_seen(self, video_ids: Iterable[str]) -> None:
        """Mark IDs as seen *without* processing them (baseline / skip path)."""
        for vid in video_ids:
            if self.has_video(vid):
                continue
            self.add_video(vid, {"skipped": True, "source": "baseline"})
            self.mark_skipped([vid])

    def known_ids(self) -> set[str]:
        return set(self.data["videos"]) | set(self.data["skipped"])

    def snapshot(self) -> dict:
        """Deep copy of the state, for rollbacks inside a --force run."""
        return copy.deepcopy(self.data)

    def restore(self, snapshot: dict) -> None:
        self.data = copy.deepcopy(snapshot)

    def summary(self) -> dict[str, Any]:
        return {
            "videos": len(self.data["videos"]),
            "skipped": len(self.data["skipped"]),
            "reminded": len(self.data["reminded"]),
            "baselines": len(self.data["baselines"]),
            "calendar_last_week": self.data.get("calendar_last_week"),
        }
