"""Timezone helpers. Every date computation in this project is WITA-based."""

from __future__ import annotations

import datetime as _dt
from typing import Optional

from zoneinfo import ZoneInfo

WITA_NAME = "Asia/Makassar"
# WITA has been a fixed UTC+8 offset with no DST for decades; the fixed zone
# keeps the bot working even when the tz database is unavailable (needs
# `tzdata` on Windows and on slim images).
_WITA_FALLBACK = _dt.timezone(_dt.timedelta(hours=8), WITA_NAME)


def _load_wita() -> ZoneInfo | _dt.tzinfo:
    try:
        return ZoneInfo(WITA_NAME)
    except Exception:  # noqa: BLE001 - ZoneInfoNotFoundError or missing tzdata
        return _WITA_FALLBACK


WITA = _load_wita()

HARI_ID = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
HARI_ID_SHORT = ["Sen", "Sel", "Rab", "Kam", "Jum", "Sab", "Min"]
BULAN_ID = [
    "Januari",
    "Februari",
    "Maret",
    "April",
    "Mei",
    "Juni",
    "Juli",
    "Agustus",
    "September",
    "Oktober",
    "November",
    "Desember",
]


def get_tz(name: Optional[str] = None):
    """Return the configured zone; falls back to WITA when the name is unknown."""
    if not name:
        return WITA
    if name.strip().upper() in {"WITA", "ASIA/MAKASSAR"}:
        return WITA
    try:
        return ZoneInfo(name)
    except Exception:  # noqa: BLE001 - unknown tz name should not crash
        return WITA


def now(tz=None) -> _dt.datetime:
    return _dt.datetime.now(tz or WITA)


def today(tz=None) -> _dt.date:
    return now(tz).date()


def parse_hhmm(value: str) -> _dt.time:
    """Parse "HH:MM" (also "H:MM" / "HH:MM:SS")."""
    parts = str(value).strip().split(":")
    if len(parts) < 2:
        raise ValueError(f"invalid time of day: {value!r}")
    hour, minute = int(parts[0]), int(parts[1])
    second = int(parts[2]) if len(parts) > 2 else 0
    return _dt.time(hour, minute, second)


def parse_date(value) -> Optional[_dt.date]:
    """Parse an ISO date string (YYYY-MM-DD) coming from the LLM. Never guesses."""
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    text = str(value).strip()
    if not text or text.lower() in {"null", "none", "tidak ada", "-"}:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return _dt.datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


def format_date_id(date: Optional[_dt.date]) -> str:
    """'2026-11-09' -> 'Senin, 9 November 2026'."""
    if date is None:
        return "tanpa tanggal"
    return f"{HARI_ID[date.weekday()]}, {date.day} {BULAN_ID[date.month - 1]} {date.year}"


def format_date_short_id(date: _dt.date) -> str:
    """'2026-11-09' -> '9 Nov 2026'."""
    return f"{date.day} {BULAN_ID[date.month - 1][:3]} {date.year}"


def now_stamp(tz=None) -> str:
    return now(tz).isoformat(timespec="seconds")
