"""Turn spoken relative deadlines into real dates.

The lecturer says "minggu depan"; the transcript tells us *when* they said it,
because the video carries the class date (upload date, or the date in the title
such as "SPK M1 2026 09 28"). So:

    "minggu depan"      spoken 2026-09-28 (Monday)  ->  2026-10-05 (Monday)
    "dua minggu lagi"                               ->  2026-10-12
    "besok"                                         ->  2026-09-29
    "senin depan"                                   ->  next Monday after that
    "akhir bulan"                                   ->  2026-09-30

This is arithmetic on an explicit statement, not invention, but the result is
still marked as inferred so the notes and reminders can say so.
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Optional

from .logging_setup import get_logger

log = get_logger("relative_dates")

_NUM_WORDS = {
    "satu": 1,
    "se": 1,
    "dua": 2,
    "tiga": 3,
    "empat": 4,
    "lima": 5,
    "enam": 6,
    "tujuh": 7,
    "delapan": 8,
}

_WEEKDAYS = {
    "senin": 0,
    "selasa": 1,
    "rabu": 2,
    "kamis": 3,
    "jumat": 4,
    "jum'at": 4,
    "sabtu": 5,
}

# "minggu ketiga", "minggu ke-3": an ordinal week (third week of term), not a date.
_ORDINAL_WEEK_RE = re.compile(
    r"\bminggu\s+(?:ke\s*-?\s*\d+|pertama|kedua|ketiga|keempat|kelima|keenam|ketujuh)\b", re.I
)

# "minggu depan", "dua minggu lagi", "3 pekan ke depan", "minggu berikutnya"
_WEEKS_RE = re.compile(
    r"\b(?:(\d+|" + "|".join(_NUM_WORDS) + r")\s*)?"
    r"(?:minggu|pekan)\s*(?:lagi|kemudian|kedepan|ke\s*depan|depan|berikutnya)\b",
    re.I,
)
_DAYS_RE = re.compile(r"\b(besok|lusa|hari\s+ini)\b", re.I)
# "minggu" alone is ambiguous (Sunday vs week), so it only counts as a weekday
# when written "hari minggu".
_WEEKDAY_RE = re.compile(
    r"\b(?:hari\s+(minggu)|(?:hari\s+)?(" + "|".join(_WEEKDAYS) + r"))\b\s*(?:depan|ini|besok)?",
    re.I,
)
_END_MONTH_RE = re.compile(r"\bakhir\s+bulan\b", re.I)
_NEXT_MONTH_RE = re.compile(r"\bbulan\s+(?:depan|berikutnya)\b", re.I)


def _weeks_from(match: re.Match) -> int:
    raw = (match.group(1) or "").strip().lower()
    if not raw:
        return 1
    if raw.isdigit():
        return int(raw)
    return _NUM_WORDS.get(raw, 1)


def _last_day_of_month(d: dt.date) -> dt.date:
    if d.month == 12:
        nxt = dt.date(d.year + 1, 1, 1)
    else:
        nxt = dt.date(d.year, d.month + 1, 1)
    return nxt - dt.timedelta(days=1)


def resolve_relative_date(text: str, anchor: Optional[dt.date]) -> tuple[Optional[dt.date], str]:
    """Resolve one relative phrase against the lecture date.

    Returns ``(date, rule)``; ``(None, "")`` when it cannot be resolved safely.
    Only the *first* recognisable phrase is used, and weekday names are only
    trusted when they are not part of a weeks-expression ("minggu depan" must
    not be read as "Sunday next").
    """
    if not text or anchor is None:
        return None, ""
    lowered = text.lower()

    if _ORDINAL_WEEK_RE.search(lowered):
        # "minggu ketiga" = third week of term; a date cannot be derived safely.
        return None, ""

    m = _WEEKS_RE.search(lowered)
    if m:
        weeks = _weeks_from(m)
        if 1 <= weeks <= 12:
            return anchor + dt.timedelta(weeks=weeks), f"{weeks} minggu setelah kuliah"

    if _END_MONTH_RE.search(lowered):
        return _last_day_of_month(anchor), "akhir bulan kuliah"

    if _NEXT_MONTH_RE.search(lowered):
        first_next = dt.date(anchor.year + 1, 1, 1) if anchor.month == 12 else dt.date(anchor.year, anchor.month + 1, 1)
        return first_next, "bulan berikutnya"

    m = _DAYS_RE.search(lowered)
    if m:
        word = m.group(1).lower()
        if word == "besok":
            return anchor + dt.timedelta(days=1), "besok"
        if word == "lusa":
            return anchor + dt.timedelta(days=2), "lusa"
        return None, ""

    # Weekday names: drop weeks-expressions first so "minggu depan" is not Monday.
    cleaned = _WEEKS_RE.sub(" ", lowered)
    m = _WEEKDAY_RE.search(cleaned)
    if m:
        word = (m.group(1) or m.group(2) or "").lower()
        target = 6 if m.group(1) else _WEEKDAYS.get(word)
        if target is not None:
            delta = (target - anchor.weekday()) % 7
            if delta == 0:
                delta = 7  # "hari senin" said on a Monday means the next one
            return anchor + dt.timedelta(days=delta), f"hari {word or 'minggu'} berikutnya"

    return None, ""


def anchor_date(video: dict, meta: Optional[dict] = None) -> Optional[dt.date]:
    """The class date: upload date first, then the date inside the title."""
    meta = meta or {}
    for raw in (meta.get("upload_date"), video.get("upload_date"), video.get("title_date")):
        text = str(raw or "")
        if len(text) == 8 and text.isdigit():
            try:
                return dt.date(int(text[:4]), int(text[4:6]), int(text[6:8]))
            except ValueError:
                continue
    return None
