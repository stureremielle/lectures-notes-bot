"""Note generation, normalisation and Indonesian Discord rendering.

Two things are guaranteed here regardless of what the model says:

* output is Indonesian (``language`` is forced to ``id``);
* the notes body contains exactly two headers: ``## Rangkuman`` and ``## Tugas``.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Optional

from . import prompts
from .logging_setup import get_logger
from .timeutil import WITA, format_date_id, parse_date

log = get_logger("notes")


def _course_of(video: dict) -> str:
    """Course label: resolved course first, playlist name is only a fallback.

    A single playlist can hold several courses, so `course` is set by
    `courses.resolve_course` during the pipeline.
    """
    return (
        video.get("course")
        or video.get("course_name")
        or video.get("playlist_title")
        or "Kuliah"
    )

MAX_ASSIGNMENTS = 10
DISCLAIMER = (
    "_Catatan: hanya tugas yang **diucapkan secara lisan** dalam kuliah yang terdeteksi. "
    "Berkas, slide, dan pengumuman tertulis tidak dibaca — periksa kembali sumber resmi dosen._"
)
UNCLEAR = "(tidak jelas di transkrip)"
CAPTION_KIND_ID = {
    "subtitles": "subtitle manual",
    "automatic_captions": "caption otomatis",
}

_ASSIGNMENT_TYPES = {
    "tugas": "tugas",
    "kuis": "kuis",
    "quiz": "kuis",
    "proyek": "proyek",
    "project": "proyek",
    "presentasi": "presentasi",
    "presentation": "presentasi",
    "laporan": "laporan",
    "report": "laporan",
    "praktikum": "praktikum",
    "latihan": "praktikum",
    "ujian": "ujian",
    "exam": "ujian",
    "uts": "ujian",
    "uas": "ujian",
}
_CONFIDENCE = {"high", "medium", "low"}


# ---------------------------------------------------------------------------
# Chunking / map-reduce
# ---------------------------------------------------------------------------
def split_for_condense(text: str, chunk_chars: int = 18000) -> list[str]:
    """Split a long transcript on paragraph (and then sentence) boundaries."""
    if chunk_chars <= 0 or len(text) <= chunk_chars:
        return [text]
    chunks: list[str] = []
    current = ""
    paragraphs = text.split("\n\n")
    for para in paragraphs:
        if len(current) + len(para) + 2 <= chunk_chars:
            current = (current + "\n\n" + para) if current else para
            continue
        if current:
            chunks.append(current)
            current = ""
        if len(para) <= chunk_chars:
            current = para
            continue
        # Paragraph still too big: fall back to sentence packing.
        sentences = re.split(r"(?<=[.!?])\s+", para)
        buf = ""
        for sent in sentences:
            if len(buf) + len(sent) + 1 > chunk_chars and buf:
                chunks.append(buf)
                buf = ""
            buf = (buf + " " + sent).strip() if buf else sent
        if buf:
            current = buf
    if current:
        chunks.append(current)
    return [c for c in chunks if c.strip()]


def condense_transcript(client, text: str, chunk_chars: int = 18000) -> str:
    """Map-reduce: condense each chunk, then join."""
    chunks = split_for_condense(text, chunk_chars)
    if len(chunks) <= 1:
        return text
    log.info("condensing transcript of %d chars in %d chunks", len(text), len(chunks))
    condensed: list[str] = []
    for i, chunk in enumerate(chunks, start=1):
        body = prompts.render(
            "condense", chunk_index=i, chunk_total=len(chunks), chunk=chunk
        )
        try:
            result = client.complete_text(
                "Kamu adalah perangkum transkrip kuliah yang teliti dan ringkas.",
                body,
                temperature=0.1,
            )
        except Exception as exc:  # noqa: BLE001 - keep going with the raw chunk
            log.warning("condense chunk %d/%d failed (%s); using raw chunk", i, len(chunks), exc)
            result = chunk
        condensed.append(f"### Bagian {i}\n{result.strip()}")
    return "\n\n".join(condensed)


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------
def build_messages(cfg, video: dict, transcript: str, meta: Optional[dict] = None) -> list[dict]:
    meta = meta or {}
    course = _course_of(video)
    title = video.get("title") or meta.get("title") or ""
    kind = CAPTION_KIND_ID.get(str(meta.get("kind") or ""), str(meta.get("kind") or "tidak diketahui"))
    duration = meta.get("duration") or video.get("duration")
    dur_str = _format_duration(duration)
    date_str = _format_upload_date(video.get("upload_date") or meta.get("upload_date"))
    system = prompts.load_prompt("notes_system")
    user = prompts.render(
        "notes_user",
        course_name=course,
        video_title=title,
        video_date=date_str or "tidak diketahui",
        duration=dur_str,
        caption_kind=kind,
        caption_lang=meta.get("lang") or "tidak diketahui",
        transcript=transcript,
    )
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]


def _format_duration(seconds) -> str:
    try:
        total = int(seconds or 0)
    except (TypeError, ValueError):
        return "tidak diketahui"
    if total <= 0:
        return "tidak diketahui"
    h, rem = divmod(total, 3600)
    m, _ = divmod(rem, 60)
    return f"{h} jam {m} menit" if h else f"{m} menit"


def _format_upload_date(value) -> Optional[str]:
    if not value:
        return None
    text = str(value)
    d = parse_date(f"{text[:4]}-{text[4:6]}-{text[6:8]}") if len(text) == 8 and text.isdigit() else parse_date(text)
    return format_date_id(d) if d else None


def generate_notes(client, cfg, video: dict, transcript: str, meta: Optional[dict] = None) -> Optional[dict]:
    """Run the (possibly reduced) transcript through the model, return raw JSON."""
    budget = cfg.get_int("notes.max_transcript_chars", 60000)
    if cfg.get_bool("notes.condense", True) and len(transcript) > budget:
        transcript = condense_transcript(
            client, transcript, cfg.get_int("notes.condense_chunk_chars", 18000)
        )
        transcript = transcript[: cfg.get_int("notes.hard_cap_chars", 120000)]
    messages = build_messages(cfg, video, transcript, meta)
    data = client.complete_json(
        messages[0]["content"],
        messages[1]["content"],
        max_tokens=cfg.get_int("llm.max_tokens", 8000),
    )
    return data


# ---------------------------------------------------------------------------
# Normalisation
# ---------------------------------------------------------------------------
def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, dict):
        for key in ("text", "term", "name", "title", "explanation", "description"):
            if value.get(key):
                return str(value[key]).strip()
    return ""


def _clean_markers(text: str) -> str:
    text = re.sub(r"\(tidak jelas di transkrip\)", UNCLEAR, text, flags=re.I)
    return re.sub(r"\s+", " ", text).strip()


def normalise_key_concepts(raw: Any) -> list[dict]:
    out: list[dict] = []
    if isinstance(raw, dict):
        raw = [{"term": k, "explanation": v} for k, v in raw.items()]
    if not isinstance(raw, list):
        return out
    for item in raw:
        if isinstance(item, dict):
            term = _as_text(item.get("term") or item.get("name") or item.get("concept"))
            expl = _as_text(item.get("explanation") or item.get("description") or item.get("detail"))
        else:
            term, expl = _as_text(item), ""
        term = _clean_markers(term)
        expl = _clean_markers(expl)
        if not term and not expl:
            continue
        if not term:
            continue
        out.append({"term": term, "explanation": expl})
    return out[:12]


def normalise_tags(raw: Any) -> list[str]:
    tags: list[str] = []
    if isinstance(raw, str):
        raw = [t for t in re.split(r"[,;]", raw)]
    if not isinstance(raw, list):
        return tags
    for item in raw:
        text = _clean_markers(_as_text(item)).lstrip("#").strip()
        if text and text.lower() not in {t.lower() for t in tags}:
            tags.append(text)
    return tags[:8]


def normalise_assignment(item: Any, course: str, video: dict) -> Optional[dict]:
    if isinstance(item, str):
        item = {"text": item}
    if not isinstance(item, dict):
        return None
    text = _clean_markers(_as_text(item.get("text") or item.get("description") or item.get("task")))
    if not text or len(text) < 3:
        return None
    atype = str(item.get("type") or "tugas").strip().lower()
    atype = _ASSIGNMENT_TYPES.get(atype, "tugas")
    due_raw = item.get("due_date") or item.get("due") or item.get("deadline")
    due_date = parse_date(due_raw)
    due_text = _clean_markers(_as_text(item.get("due_text") or item.get("due_note"))) or None
    confidence = str(item.get("confidence") or "medium").strip().lower()
    if confidence not in _CONFIDENCE:
        confidence = "medium"
    timestamp = _as_text(item.get("timestamp"))
    if timestamp and not re.match(r"^\d{1,2}:\d{2}(:\d{2})?$", timestamp):
        timestamp = ""
    text_hash = hashlib.sha1(f"{course}|{text}".encode("utf-8")).hexdigest()[:10]
    return {
        "text": text,
        "type": atype,
        "due_date": due_date.isoformat() if due_date else None,
        "due_text": due_text or None,
        "confidence": confidence,
        "timestamp": timestamp or None,
        "course": course,
        "video_id": video.get("id") or video.get("video_id"),
        "video_title": video.get("title") or "",
        "video_url": video.get("webpage_url") or video.get("url") or "",
        "key": text_hash,
    }


def normalise(raw: Optional[dict], video: dict) -> Optional[dict]:
    """Turn the model's JSON into the canonical note record. None if unusable."""
    if not isinstance(raw, dict):
        return None
    # Some gateways wrap the payload, e.g. {"notes": {...}} or {"data": {...}}.
    for wrapper in ("notes", "result", "data", "output"):
        inner = raw.get(wrapper)
        if isinstance(inner, dict) and ("summary" in inner or "assignments" in inner):
            raw = inner
            break
    summary = _clean_markers(_as_text(raw.get("summary")))
    concepts = normalise_key_concepts(raw.get("key_concepts") or raw.get("keyConcepts"))
    if not summary and not concepts:
        log.warning("model response has neither summary nor key concepts")
        return None
    course = _course_of(video)
    title = _clean_markers(_as_text(raw.get("title"))) or _clean_markers(video.get("title") or "")
    assignments_raw = raw.get("assignments")
    if isinstance(assignments_raw, dict):
        assignments_raw = [assignments_raw]
    if not isinstance(assignments_raw, list):
        assignments_raw = []
    assignments: list[dict] = []
    seen: set[str] = set()
    for item in assignments_raw:
        norm = normalise_assignment(item, course, video)
        if norm is None:
            continue
        if norm["key"] in seen:
            continue
        seen.add(norm["key"])
        assignments.append(norm)
        if len(assignments) >= MAX_ASSIGNMENTS:
            break
    return {
        "title": title,
        "language": "id",
        "summary": summary,
        "key_concepts": concepts,
        "tags": normalise_tags(raw.get("tags")),
        "assignments": assignments,
        "dropped_assignments": max(0, len(assignments_raw) - len(assignments)),
    }


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------
def render_notes_body(note: dict) -> str:
    """Markdown body with exactly two headers: Rangkuman and Tugas."""
    summary = (note.get("summary") or "").strip()
    concepts = note.get("key_concepts") or []
    lines: list[str] = ["## Rangkuman", ""]
    if summary:
        lines.append(summary)
        lines.append("")
    elif concepts:
        # Keep Rangkuman non-empty even if the model only returned concepts.
        lines.append("Ringkasan lisan kuliah ini dipecah menjadi konsep kunci berikut.")
        lines.append("")
    if concepts:
        lines.append("**Konsep kunci**")
        lines.append("")
        for c in concepts:
            term = c.get("term") or ""
            expl = c.get("explanation") or ""
            if expl:
                lines.append(f"- **{term}** — {expl}")
            else:
                lines.append(f"- **{term}**")
        lines.append("")

    lines.append("## Tugas")
    lines.append("")
    assignments = note.get("assignments") or []
    if not assignments:
        lines.append("_Tidak ada tugas yang disebut secara lisan pada kuliah ini._")
    else:
        for a in assignments:
            lines.append(_render_assignment_line(a))
    lines.append("")
    lines.append(DISCLAIMER)
    return "\n".join(lines).strip()


def _render_assignment_line(a: dict) -> str:
    text = a.get("text") or ""
    bits: list[str] = [f"- {text}"]
    meta: list[str] = [f"jenis: {a.get('type') or 'tugas'}"]
    if a.get("due_date"):
        meta.append(f"tenggat: {format_date_id(parse_date(a['due_date']))}")
    elif a.get("due_text"):
        meta.append(f"tenggat: {a['due_text']} (belum dipastikan)")
    else:
        meta.append("tenggat: tidak disebutkan")
    if (a.get("confidence") or "medium") == "low":
        meta.append("keyakinan: rendah")
    if a.get("timestamp"):
        meta.append(f"diucapkan pada {a['timestamp']}")
    bits.append("  _" + " · ".join(meta) + "_")
    return "\n".join(bits)


def build_embed(note: dict, video: dict) -> dict:
    course = _course_of(video)
    title = note.get("title") or video.get("title") or "Catatan Kuliah"
    url = video.get("webpage_url") or video.get("url")
    n_assign = len(note.get("assignments") or [])
    if n_assign:
        task_line = f"{n_assign} tugas terdeteksi (lisan)"
    else:
        task_line = "tidak ada tugas yang disebut secara lisan"
    tags = note.get("tags") or []
    embed: dict = {
        "title": _truncate(title, 250),
        "color": 0x2ECC71 if n_assign else 0x3498DB,
        "fields": [
            {"name": "Mata kuliah", "value": _truncate(course, 250) or "-", "inline": True},
            {"name": "Tugas", "value": _truncate(task_line, 250), "inline": True},
        ],
        "footer": {"text": _truncate(f"Catatan otomatis dari transkrip audio · {course}", 200)},
    }
    if url:
        embed["url"] = url
    summary = (note.get("summary") or "").strip()
    if summary:
        embed["description"] = _truncate(summary, 700)
    if tags:
        embed["fields"].append(
            {"name": "Tag", "value": _truncate(" · ".join(f"`{t}`" for t in tags), 250), "inline": False}
        )
    return embed


def render_notes_message(note: dict, video: dict) -> str:
    """The full "note message": header line + body (two headers)."""
    course = _course_of(video)
    title = note.get("title") or video.get("title") or "Catatan Kuliah"
    url = video.get("webpage_url") or video.get("url") or ""
    head = f"# 📚 {title}\n**{course}**"
    if url:
        head += f" · [tonton]({url})"
    return f"{head}\n\n{render_notes_body(note)}"


def render_dry_run(note: dict, video: dict) -> str:
    """Printable preview used by ``--dry-run`` (shows both messages)."""
    embed = build_embed(note, video)
    out = ["=== EMBED (#notes) ===", json.dumps(embed, ensure_ascii=False, indent=2), ""]
    out.append("=== NOTES MESSAGE (#notes) ===")
    out.append(render_notes_message(note, video))
    return "\n".join(out)


def _truncate(text: str, limit: int) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def headers_of(note: dict) -> list[str]:
    return re.findall(r"^##\s+(.+)$", render_notes_body(note), flags=re.M)


__all__ = [
    "DISCLAIMER",
    "build_embed",
    "condense_transcript",
    "generate_notes",
    "headers_of",
    "normalise",
    "render_dry_run",
    "render_notes_body",
    "render_notes_message",
    "split_for_condense",
    "WITA",
]
