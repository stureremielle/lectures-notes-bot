"""Weekly calendar PNG: 900×1020 month grid with assignment dots.

Layout
    header (month + year, WITA note)
    7 weekday columns, Monday-first, Indonesian labels ("Sen", "Sel", ...)
    month grid cells (rounded boxes); dated assignments show one coloured dot
    per assignment (type-coloured); today's cell is outlined
    a dated list below the grid for the items visible in this month

Posted once per ISO week on ``calendar.image.weekday`` (default Monday) via
``attachment://calendar.png``.
"""

from __future__ import annotations

import calendar as _cal
import datetime as _dt
import math
from pathlib import Path
from typing import Iterable, Optional

from . import assignments as A
from .logging_setup import get_logger
from .timeutil import BULAN_ID, HARI_ID_SHORT, WITA, parse_date

log = get_logger("calendar_image")

WIDTH = 900
HEIGHT = 1020
MARGIN = 24
BG = (248, 249, 251)
CARD = (255, 255, 255)
LINE = (222, 226, 232)
TEXT = (33, 37, 41)
MUTED = (120, 128, 140)
TODAY_RING = (231, 76, 60)
ACCENT = (41, 128, 185)

WEEKDAYS = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]

FONT_CANDIDATES = {
    "regular": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
    ],
    "bold": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "C:/Windows/Fonts/segoeuib.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    ],
}


def _font(size: int, bold: bool = False):
    from PIL import ImageFont

    for path in FONT_CANDIDATES["bold" if bold else "regular"]:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _text_w(draw, text: str, font) -> int:
    try:
        return int(draw.textlength(text, font=font))
    except AttributeError:  # pragma: no cover - very old Pillow
        return len(text) * (font.size if hasattr(font, "size") else 6)


def _fit_text(draw, text: str, font, max_width: int) -> str:
    if _text_w(draw, text, font) <= max_width:
        return text
    ell = "…"
    out = text
    while out and _text_w(draw, out + ell, font) > max_width:
        out = out[:-1]
    return (out.rstrip() + ell) if out else ell


def month_grid(year: int, month: int) -> list[list[Optional[int]]]:
    """Monday-first weeks for the month; ``None`` pads leading/trailing days."""
    first = _dt.date(year, month, 1)
    days_in_month = _cal.monthrange(year, month)[1]
    weeks: list[list[Optional[int]]] = []
    week: list[Optional[int]] = [None] * first.weekday()
    for day in range(1, days_in_month + 1):
        week.append(day)
        if len(week) == 7:
            weeks.append(week)
            week = []
    if week:
        week += [None] * (7 - len(week))
        weeks.append(week)
    while len(weeks) < 6:
        weeks.append([None] * 7)
    return weeks


def _items_in_month(items: Iterable[dict], year: int, month: int) -> list[dict]:
    out: list[dict] = []
    for item in items:
        d = parse_date(item.get("due_date"))
        if d and d.year == year and d.month == month:
            out.append(item)
    return A.sort_assignments(out)


def render_month(
    items: Iterable[dict],
    year: int,
    month: int,
    today: Optional[_dt.date] = None,
    out_path: Optional[Path] = None,
) -> Path:
    """Draw the PNG and return the path written."""
    from PIL import Image, ImageDraw

    today = today or _dt.datetime.now(WITA).date()
    items = list(items)
    month_items = _items_in_month(items, year, month)

    img = Image.new("RGB", (WIDTH, HEIGHT), BG)
    draw = ImageDraw.Draw(img)

    f_month = _font(38, bold=True)
    f_sub = _font(17)
    f_head = _font(17, bold=True)
    f_day = _font(19, bold=True)
    f_legend = _font(15)
    f_item = _font(16)
    f_item_bold = _font(16, bold=True)
    f_small = _font(14)

    # ---- header ---------------------------------------------------------
    title = f"{BULAN_ID[month - 1]} {year}"
    draw.text((MARGIN, MARGIN), title, font=f_month, fill=TEXT)
    sub = "Tenggat tugas kuliah · zona waktu WITA (Asia/Makassar)"
    draw.text((MARGIN, MARGIN + 50), sub, font=f_sub, fill=MUTED)
    right = f"Hari ini: {today.day} {BULAN_ID[today.month - 1][:3]} {today.year}"
    draw.text(
        (WIDTH - MARGIN - _text_w(draw, right, f_sub), MARGIN + 18), right, font=f_sub, fill=MUTED
    )

    # ---- grid -----------------------------------------------------------
    grid_top = MARGIN + 92
    hdr_h = 30
    cell_w = (WIDTH - 2 * MARGIN) / 7.0
    weeks = month_grid(year, month)
    cell_h = 76
    grid_h = hdr_h + cell_h * len(weeks)

    draw.rounded_rectangle(
        (MARGIN, grid_top, WIDTH - MARGIN, grid_top + grid_h),
        radius=14,
        fill=CARD,
        outline=LINE,
        width=1,
    )

    by_day: dict[int, list[dict]] = {}
    for item in month_items:
        d = parse_date(item.get("due_date"))
        if d:
            by_day.setdefault(d.day, []).append(item)

    for col, label in enumerate(HARI_ID_SHORT):
        cx = MARGIN + cell_w * col + cell_w / 2
        color = (200, 60, 60) if col >= 5 else MUTED
        draw.text(
            (cx - _text_w(draw, label, f_head) / 2, grid_top + 7), label, font=f_head, fill=color
        )
    draw.line(
        (MARGIN + 8, grid_top + hdr_h, WIDTH - MARGIN - 8, grid_top + hdr_h), fill=LINE, width=1
    )

    for r, week in enumerate(weeks):
        for c, day in enumerate(week):
            if day is None:
                continue
            x0 = MARGIN + cell_w * c
            y0 = grid_top + hdr_h + cell_h * r
            pad = 4
            is_today = (day == today.day and today.year == year and today.month == month)
            box = (x0 + pad, y0 + pad, x0 + cell_w - pad, y0 + cell_h - pad)
            if is_today:
                draw.rounded_rectangle(box, radius=10, outline=TODAY_RING, width=3, fill=(255, 246, 245))
            elif c >= 5:
                draw.rounded_rectangle(box, radius=10, fill=(250, 250, 252))
            dnum = str(day)
            draw.text((x0 + 10, y0 + 8), dnum, font=f_day, fill=TEXT if not is_today else TODAY_RING)
            entries = by_day.get(day, [])
            if entries:
                if len(entries) <= 3:
                    dots_y = y0 + cell_h - 18
                    total = len(entries)
                    for i, item in enumerate(entries):
                        cx = x0 + cell_w / 2 + (i - (total - 1) / 2) * 16
                        rad = 5
                        draw.ellipse(
                            (cx - rad, dots_y - rad, cx + rad, dots_y + rad),
                            fill=A.type_color(item.get("type")),
                            outline=(255, 255, 255),
                        )
                else:
                    txt = f"{len(entries)} tugas"
                    tw = _text_w(draw, txt, f_small)
                    draw.rounded_rectangle(
                        (
                            x0 + cell_w / 2 - tw / 2 - 6,
                            y0 + cell_h - 27,
                            x0 + cell_w / 2 + tw / 2 + 6,
                            y0 + cell_h - 9,
                        ),
                        radius=8,
                        fill=(236, 240, 244),
                    )
                    draw.text(
                        (x0 + cell_w / 2 - tw / 2, y0 + cell_h - 26), txt, font=f_small, fill=TEXT
                    )

    # ---- legend ---------------------------------------------------------
    legend_top = grid_top + grid_h + 14
    types = [t for t in A.TYPE_EMOJI]
    x = MARGIN
    for t in types:
        color = A.type_color(t)
        draw.ellipse((x, legend_top + 4, x + 10, legend_top + 14), fill=color)
        label = t
        draw.text((x + 16, legend_top), label, font=f_legend, fill=MUTED)
        x += 20 + _text_w(draw, label, f_legend) + 16
    draw.ellipse(
        (x, legend_top + 4, x + 10, legend_top + 14), outline=TODAY_RING, width=2
    )
    draw.text((x + 16, legend_top), "hari ini", font=f_legend, fill=MUTED)

    # ---- dated list -----------------------------------------------------
    list_top = legend_top + 34
    draw.line((MARGIN, list_top - 8, WIDTH - MARGIN, list_top - 8), fill=LINE, width=1)
    if not month_items:
        draw.text((MARGIN, list_top + 4), "Tidak ada tenggat tercatat bulan ini.", font=f_item, fill=MUTED)
    else:
        row_h = 26
        max_rows = int((HEIGHT - MARGIN - list_top) // row_h)
        shown = month_items[: max(0, max_rows)]
        for i, item in enumerate(shown):
            d = parse_date(item.get("due_date"))
            y = list_top + 2 + i * row_h
            day_txt = f"{d.day:>2} {BULAN_ID[d.month - 1][:3]}"
            draw.rounded_rectangle(
                (MARGIN + 2, y + 6, MARGIN + 22, y + 20), radius=4, fill=A.type_color(item.get("type"))
            )
            draw.text((MARGIN + 30, y + 2), day_txt, font=f_item_bold, fill=TEXT)
            course = item.get("course") or ""
            text = item.get("text") or ""
            label = f"{text}"
            max_w = WIDTH - MARGIN * 2 - 300
            label = _fit_text(draw, label, f_item, max_w)
            draw.text((MARGIN + 110, y + 2), label, font=f_item, fill=TEXT)
            if course:
                course = _fit_text(draw, course, f_small, 170)
                draw.text((WIDTH - MARGIN - _text_w(draw, course, f_small), y + 4), course, font=f_small, fill=MUTED)
        remaining = len(month_items) - len(shown)
        if remaining > 0:
            draw.text(
                (MARGIN + 2, list_top + 2 + len(shown) * row_h),
                f"… dan {remaining} tugas lainnya",
                font=f_small,
                fill=MUTED,
            )

    # footer
    foot = "Terdeteksi otomatis dari ucapan dosen (tanpa visual). Periksa sumber resmi."
    draw.text((MARGIN, HEIGHT - MARGIN - 4), foot, font=f_small, fill=MUTED)

    out_path = Path(out_path) if out_path else Path("calendar.png")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, format="PNG", optimize=True)
    log.info("calendar PNG written: %s (%d items)", out_path, len(month_items))
    return out_path


# ---------------------------------------------------------------------------
# Weekly trigger
# ---------------------------------------------------------------------------
def iso_week_key(date: _dt.date) -> str:
    year, week, _ = date.isocalendar()
    return f"{year}-W{week:02d}"


def weekday_index(name: str) -> int:
    """'monday'/'senin' -> 0 (Monday-first)."""
    key = str(name or "monday").strip().lower()
    mapping = {
        "monday": 0, "senin": 0,
        "tuesday": 1, "selasa": 1,
        "wednesday": 2, "rabu": 2,
        "thursday": 3, "kamis": 3,
        "friday": 4, "jumat": 4, "jum'at": 4,
        "saturday": 5, "sabtu": 5,
        "sunday": 6, "minggu": 6,
        "7": 6,
    }
    if key.isdigit():
        return max(0, min(6, int(key) - 1))
    return mapping.get(key, 0)


def should_post_weekly(cfg, state, now: Optional[_dt.datetime] = None) -> tuple[bool, str]:
    now = (now or _dt.datetime.now(WITA)).astimezone(cfg.tz)
    target = weekday_index(cfg.get_str("calendar.image.weekday", "monday"))
    week = iso_week_key(now.date())
    if now.weekday() < target:
        return False, week
    if state.calendar_week() == week:
        return False, week
    if not cfg.get_bool("calendar.image.enabled", True):
        return False, week
    return True, week


def target_month(cfg, now: Optional[_dt.datetime] = None) -> tuple[int, int]:
    """Month to draw: current, or next when we are in the last days of this one."""
    now = (now or _dt.datetime.now(WITA)).astimezone(cfg.tz)
    lead = cfg.get_int("calendar.image.lead_days", 7)
    d = now.date()
    days_in = _cal.monthrange(d.year, d.month)[1]
    if lead > 0 and (days_in - d.day) < lead:
        nxt = d.replace(day=28) + _dt.timedelta(days=4)
        return nxt.year, nxt.month
    return d.year, d.month


def run_calendar_pass(
    cfg,
    state,
    calendar_webhook,
    now: Optional[_dt.datetime] = None,
    force_week: bool = False,
) -> dict:
    """Post the weekly PNG (and nothing else). Returns a small result dict."""
    now = (now or _dt.datetime.now(WITA)).astimezone(cfg.tz)
    due, week = should_post_weekly(cfg, state, now)
    if force_week:
        due = True
    if not due:
        return {"posted": False, "week": week}
    year, month = target_month(cfg, now)
    items = A.collect_from_videos(state.all_videos())
    out_dir = Path(cfg.get_str("calendar.image.output_dir", "data/calendar"))
    path = render_month(items, year, month, today=now.date(), out_path=out_dir / "calendar.png")
    ok = calendar_webhook.send_files(
        [path],
        content=f"🗓️ **Kalender tugas {A.month_label(year, month)}** — minggu {week} (WITA)",
        embeds=[
            {
                "image": {"url": "attachment://calendar.png"},
                "color": ACCENT_HEX,
                "footer": {"text": "Hanya tugas yang diucapkan secara lisan yang terdeteksi."},
            }
        ],
    )
    if ok and not calendar_webhook.dry_run:
        state.set_calendar_week(week)
        state.save()
    return {"posted": ok, "week": week, "path": str(path)}


ACCENT_HEX = (ACCENT[0] << 16) + (ACCENT[1] << 8) + ACCENT[2]


def weekday_label(idx: int) -> str:
    return WEEKDAYS[max(0, min(6, idx))]


def cell_count() -> int:
    return int(math.ceil(WIDTH / 7))
