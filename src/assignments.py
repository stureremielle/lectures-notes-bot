"""Assignment ordering, Indonesian rendering and the ``.ics`` builder.

Deadline semantics: when the lecturer only gives a calendar date, the deadline
is that day **23:59 WITA**. The calendar entries are therefore all-day events
with an exclusive DTEND of the following day, which is what Google Calendar and
Apple Calendar expect.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import re
from typing import Iterable, Optional

from .timeutil import BULAN_ID, WITA, format_date_id, parse_date

DISCLAIMER_CAL = (
    "_Hanya tugas yang diucapkan secara lisan dalam kuliah yang terdeteksi otomatis._"
)

TYPE_EMOJI = {
    "tugas": "📝",
    "kuis": "❓",
    "proyek": "🏗️",
    "presentasi": "🎤",
    "laporan": "📄",
    "praktikum": "🧪",
    "ujian": "🎓",
}
TYPE_COLOR = {
    "tugas": (52, 152, 219),    # blue
    "kuis": (241, 196, 15),     # yellow
    "proyek": (155, 89, 182),   # purple
    "presentasi": (230, 126, 34),  # orange
    "laporan": (39, 174, 96),   # green
    "praktikum": (22, 160, 133),  # teal
    "ujian": (231, 76, 60),     # red
}
CONFIDENCE_ORDER = {"high": 0, "medium": 1, "low": 2}


def type_emoji(atype: Optional[str]) -> str:
    return TYPE_EMOJI.get(str(atype or "tugas"), "📝")


def type_color(atype: Optional[str]) -> tuple[int, int, int]:
    return TYPE_COLOR.get(str(atype or "tugas"), TYPE_COLOR["tugas"])


# ---------------------------------------------------------------------------
# Collection / ordering
# ---------------------------------------------------------------------------
def collect_from_videos(videos: Iterable[dict]) -> list[dict]:
    """Flatten assignment lists of processed video records."""
    out: list[dict] = []
    for video in videos:
        note = video.get("note") or {}
        for a in note.get("assignments") or []:
            item = dict(a)
            item.setdefault(
                "course",
                video.get("course") or video.get("course_name") or video.get("playlist_title") or "",
            )
            item.setdefault("video_title", video.get("title") or "")
            item.setdefault("video_id", video.get("id") or video.get("video_id"))
            item.setdefault("video_url", video.get("webpage_url") or video.get("url") or "")
            out.append(item)
    return out


def sort_assignments(items: Iterable[dict]) -> list[dict]:
    """Dated first (earliest first), then undated by course/video order.

    Low-confidence items sink to the bottom of each group.
    """
    dated: list[tuple[_dt.date, int, dict]] = []
    undated: list[dict] = []
    for item in items:
        d = parse_date(item.get("due_date"))
        if d:
            dated.append((d, CONFIDENCE_ORDER.get(str(item.get("confidence")), 1), item))
        else:
            undated.append(item)
    dated.sort(key=lambda t: (t[0], t[1]))
    order = {"high": 0, "medium": 1, "low": 2}
    undated.sort(key=lambda i: (order.get(str(i.get("confidence")), 1), str(i.get("course") or "")))
    return [t[2] for t in dated] + undated


def dedupe(items: Iterable[dict]) -> list[dict]:
    seen: set[str] = set()
    out: list[dict] = []
    for item in items:
        key = str(item.get("key") or "")
        if not key:
            key = hashlib.sha1(
                f"{item.get('course')}|{item.get('text')}".encode("utf-8")
            ).hexdigest()[:10]
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def assignment_id(item: dict) -> str:
    """Stable dedupe key: ``(assignment, offset)`` uses ``id@T<n>``."""
    key = item.get("key")
    if key:
        return str(key)
    return hashlib.sha1(
        f"{item.get('course')}|{item.get('text')}".encode("utf-8")
    ).hexdigest()[:10]


# ---------------------------------------------------------------------------
# Indonesian rendering
# ---------------------------------------------------------------------------
def due_label(item: dict) -> str:
    d = parse_date(item.get("due_date"))
    if d:
        label = f"{format_date_id(d)} (23.59 WITA)"
        if item.get("due_date_inferred"):
            label += f" · ditafsirkan dari \u201c{item.get('due_text')}\u201d"
        return label
    text = item.get("due_text")
    if text:
        return f"{text} (tenggat belum dipastikan)"
    return "tanpa tenggat yang disebutkan"


def render_line(item: dict, show_course: bool = True) -> str:
    parts = [f"{type_emoji(item.get('type'))} {item.get('text') or ''}".strip()]
    if show_course and item.get("course"):
        parts.append(f"({item['course']})")
    line = " ".join(p for p in parts if p)
    meta = [f"tenggat: {due_label(item)}"]
    if str(item.get("confidence")) == "low":
        meta.append("keyakinan: rendah")
    if item.get("timestamp"):
        meta.append(f"diucapkan pada {item['timestamp']}")
    return f"- {line}\n  _" + " · ".join(meta) + "_"


def render_list(
    items: Iterable[dict],
    title: str = "Daftar Tugas",
    limit: Optional[int] = None,
    today: Optional[_dt.date] = None,
) -> str:
    """Discord-ready assignment list (Indonesian). Caller splits if needed."""
    today = today or _dt.datetime.now(WITA).date()
    ordered = sort_assignments(dedupe(items))
    lines: list[str] = [f"## {title}", ""]
    if not ordered:
        lines.append("_Belum ada tugas yang terdeteksi._")
        lines.append("")
        lines.append(DISCLAIMER_CAL)
        return "\n".join(lines)

    if limit and len(ordered) > limit:
        ordered = ordered[:limit]
    overdue: list[dict] = []
    upcoming: list[dict] = []
    for item in ordered:
        d = parse_date(item.get("due_date"))
        if d and d < today:
            overdue.append(item)
        else:
            upcoming.append(item)

    if upcoming:
        by_date: dict[Optional[_dt.date], list[dict]] = {}
        for item in upcoming:
            by_date.setdefault(parse_date(item.get("due_date")), []).append(item)
        for d in sorted([k for k in by_date if k is not None]):
            count = len(by_date[d])
            delta = (d - today).days
            if delta == 0:
                when = "hari ini"
            elif delta == 1:
                when = "besok"
            else:
                when = f"{delta} hari lagi"
            lines.append(f"### {format_date_id(d)} · {when}")
            lines.append("")
            for item in by_date[d]:
                lines.append(render_line(item))
            lines.append("")
        if by_date.get(None):
            lines.append("### Tanpa tanggal")
            lines.append("")
            for item in by_date[None]:
                lines.append(render_line(item))
            lines.append("")

    if overdue:
        lines.append("### Lewat tenggat")
        lines.append("")
        for item in overdue:
            lines.append(render_line(item))
        lines.append("")

    total = len(ordered)
    lines.append(f"**Total:** {total} tugas")
    lines.append("")
    lines.append(DISCLAIMER_CAL)
    return "\n".join(lines).strip()


def render_reminder(item: dict, offset: int, today: _dt.date, mention: str = "") -> str:
    d = parse_date(item.get("due_date"))
    if offset == 0:
        head = "⏰ **Tenggat HARI INI**"
    elif offset == 1:
        head = "⏰ **Tenggat BESOK**"
    else:
        head = f"⏰ **Tenggat {offset} hari lagi**"
    lines: list[str] = []
    if mention:
        lines.append(mention)
    lines.append(head)
    lines.append("")
    lines.append(f"{type_emoji(item.get('type'))} **{item.get('text')}**")
    if item.get("course"):
        lines.append(f"Mata kuliah: **{item['course']}**")
    if d:
        lines.append(f"Tenggat: **{format_date_id(d)}** pukul 23.59 WITA")
        lines.append(f"_{d.isoformat()} · sisa {(d - today).days} hari_")
        if item.get("due_date_inferred"):
            lines.append(
                f"_Tanggal ditafsirkan dari ucapan dosen \u201c{item.get('due_text')}\u201d "
                f"pada tanggal kuliah._"
            )
    elif item.get("due_text"):
        lines.append(f"Tenggat menurut dosen: **{item['due_text']}** (tanggal belum dipastikan)")
    if item.get("video_url"):
        src = f"Sumber: {item['video_url']}"
        if item.get("timestamp"):
            src += f" (menit {item['timestamp']})"
        lines.append(src)
    if str(item.get("confidence")) == "low":
        lines.append("_Keyakinan rendah — tugas ini mungkin tidak benar-benar ditugaskan._")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# .ics builder
# ---------------------------------------------------------------------------
def _escape_ics(text: str) -> str:
    return (
        str(text or "")
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
    )


def _fold_ics_line(line: str, limit: int = 73) -> str:
    """RFC 5545 line folding (75 octets max; we fold at 73 to stay safe)."""
    if len(line.encode("utf-8")) <= limit:
        return line
    segments: list[str] = []
    acc = ""
    for ch in line:
        if len((acc + ch).encode("utf-8")) > limit:
            segments.append(acc)
            acc = ch
        else:
            acc += ch
    if acc:
        segments.append(acc)
    return "\r\n ".join(segments)


def build_ics(
    items: Iterable[dict],
    product: str = "lecture-notes-bot",
    timezone_name: str = "Asia/Makassar",
    now: Optional[_dt.datetime] = None,
) -> str:
    """Build an RFC 5545 calendar with one all-day VEVENT per dated assignment."""
    now = now or _dt.datetime.now(WITA)
    stamp = now.astimezone(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines: list[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        f"PRODID:-//{product}//ID",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:Tenggat Tugas Kuliah",
        f"X-WR-TIMEZONE:{timezone_name}",
    ]
    for item in sort_assignments(dedupe(items)):
        d = parse_date(item.get("due_date"))
        if not d:
            continue
        summary = f"{type_emoji(item.get('type'))} {item.get('text') or 'Tugas'}".replace("\n", " ")
        course = item.get("course") or ""
        if course and course not in summary:
            summary = f"[{course}] {summary}"
        desc_bits = []
        if item.get("due_text"):
            desc_bits.append(f"Tenggat menurut dosen: {item['due_text']}")
        if item.get("video_title"):
            desc_bits.append(f"Kuliah: {item['video_title']}")
        if item.get("video_url"):
            desc_bits.append(f"Video: {item['video_url']}")
        if item.get("timestamp"):
            desc_bits.append(f"Diucapkan pada menit {item['timestamp']}")
        if item.get("confidence"):
            desc_bits.append(f"Keyakinan deteksi: {item['confidence']}")
        desc_bits.append(
            "Terdeteksi otomatis dari transkrip audio (hanya ucapan lisan). Periksa sumber resmi dosen."
        )
        uid = f"{assignment_id(item)}-{d.isoformat()}@{product}"
        can_edit = "TRUE" if str(item.get("confidence")) in {"high", "medium"} else "FALSE"
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{uid}",
                f"DTSTAMP:{stamp}",
                f"DTSTART;VALUE=DATE:{d.strftime('%Y%m%d')}",
                f"DTEND;VALUE=DATE:{(d + _dt.timedelta(days=1)).strftime('%Y%m%d')}",
                f"SUMMARY:{_escape_ics(summary)}",
                f"DESCRIPTION:{_escape_ics(chr(10).join(desc_bits))}",
                f"CATEGORIES:{_escape_ics(str(item.get('type') or 'tugas').upper())}",
                "TRANSP:TRANSPARENT",
                f"X-LECTURE-NOTES-CONFIDENCE:{str(item.get('confidence') or 'medium').upper()}",
                f"X-LECTURE-NOTES-EDITABLE:{can_edit}",
                "BEGIN:VALARM",
                "TRIGGER:-PT1H",
                "ACTION:DISPLAY",
                f"DESCRIPTION:{_escape_ics('Tenggat hari ini: ' + (item.get('text') or ''))}",
                "END:VALARM",
                "END:VEVENT",
            ]
        )
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold_ics_line(line) for line in lines) + "\r\n"


def ics_filename(today: Optional[_dt.date] = None) -> str:
    today = today or _dt.datetime.now(WITA).date()
    return f"tugas-{today.isoformat()}.ics"


def dated_items(items: Iterable[dict]) -> list[dict]:
    return [i for i in items if parse_date(i.get("due_date"))]


def undated_count(items: Iterable[dict]) -> int:
    return sum(1 for i in items if not parse_date(i.get("due_date")))


def month_label(year: int, month: int) -> str:
    return f"{BULAN_ID[month - 1]} {year}"


__all__ = [
    "assignment_id",
    "build_ics",
    "collect_from_videos",
    "dated_items",
    "dedupe",
    "due_label",
    "ics_filename",
    "month_label",
    "render_line",
    "render_list",
    "render_reminder",
    "sort_assignments",
    "type_color",
    "type_emoji",
    "undated_count",
    "_escape_ics",
    "_fold_ics_line",
]
