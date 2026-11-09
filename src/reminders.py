"""T-1 / T-0 deadline reminders (WITA).

A reminder fires for an assignment when::

    due_date - today_wita  in  reminders.days_before        (default [1, 0])
    AND now_wita >= reminders.send_time_local               (default 08:00)

``send_time_local`` must be earlier than the first ``watch.run_at_local`` entry,
otherwise the tick that would send the reminder runs after the window opened and
the reminder is late (or only sent by the following tick). It is documented in
config.example.yaml / config.yaml.

Dedupe key: ``<assignment_id>@T<offset>`` stored in ``state.reminded``. An
assignment with ``due_date: null`` can never be reminded — the count is logged.
"""

from __future__ import annotations

import datetime as _dt
from typing import Iterable, Optional

from . import assignments as A
from .logging_setup import get_logger
from .timeutil import WITA, format_date_id, get_tz, parse_date

log = get_logger("reminders")


def reminder_key(item: dict, offset: int) -> str:
    return f"{A.assignment_id(item)}@T{offset}"


def due_offsets(
    items: Iterable[dict],
    today: _dt.date,
    days_before: Iterable[int],
) -> list[tuple[dict, int]]:
    """All ``(assignment, offset)`` pairs due within the configured window."""
    offsets = [int(d) for d in days_before]
    out: list[tuple[dict, int]] = []
    for item in A.dedupe(items):
        d = parse_date(item.get("due_date"))
        if d is None:
            continue
        delta = (d - today).days
        for off in offsets:
            if delta == off:
                out.append((item, off))
                break
    return out


def pending(
    items: Iterable[dict],
    state,
    now: Optional[_dt.datetime] = None,
    days_before: Iterable[int] = (1, 0),
    send_time: Optional[_dt.time] = None,
    force: bool = False,
) -> tuple[list[tuple[dict, int]], dict]:
    """Return the reminders that should be sent now plus diagnostics."""
    now = now or _dt.datetime.now(WITA)
    today = now.date()
    diagnostics = {
        "total": 0,
        "undated": 0,
        "already_sent": 0,
        "before_send_time": False,
        "now": now.strftime("%Y-%m-%d %H:%M WITA"),
        "today": today.isoformat(),
    }
    items = list(items)
    diagnostics["total"] = len(A.dedupe(items))
    diagnostics["undated"] = A.undated_count(items)
    if diagnostics["undated"]:
        log.info(
            "%d tugas tanpa tanggal (due_date null) tidak bisa diingatkan",
            diagnostics["undated"],
        )
    if send_time is not None and now.time() < send_time:
        diagnostics["before_send_time"] = True
        log.info(
            "sebelum send_time_local %s — tidak ada pengingat yang dikirim",
            send_time.strftime("%H:%M"),
        )
        return [], diagnostics
    todays = due_offsets(items, today, days_before)
    candidates: list[tuple[dict, int]] = []
    for item, off in todays:
        key = reminder_key(item, off)
        if not force and state.is_reminded(key):
            diagnostics["already_sent"] += 1
            continue
        candidates.append((item, off))
    candidates.sort(key=lambda t: (-t[1], str(t[0].get("course") or "")))
    return candidates, diagnostics


def format_dry_run(candidates: list[tuple[dict, int]], diagnostics: dict) -> str:
    today = parse_date(diagnostics.get("today")) or _dt.datetime.now(WITA).date()
    lines = [
        f"Lokal: {diagnostics.get('now')} (tanggal {format_date_id(today)})",
        f"Total tugas: {diagnostics.get('total')} · tanpa tanggal: {diagnostics.get('undated')} · "
        f"sudah dikirim: {diagnostics.get('already_sent')}",
    ]
    if diagnostics.get("before_send_time"):
        lines.append("Waktu sekarang masih sebelum send_time_local: tidak ada yang dikirim.")
    if not candidates:
        lines.append("Tidak ada pengingat yang akan dikirim saat ini.")
        return "\n".join(lines)
    lines.append(f"{len(candidates)} pengingat akan dikirim:")
    for item, off in candidates:
        lines.append("")
        lines.append(A.render_reminder(item, off, today))
    return "\n".join(lines)


def mention_of(cfg) -> str:
    """The ping to prefix on a reminder: '@everyone', '@here', a role, or ''."""
    raw = cfg.get_str("reminders.mention", "").strip()
    if not raw or raw.lower() in {"none", "off", "false", "0"}:
        return ""
    if raw.lower() in {"everyone", "@everyone"}:
        return "@everyone"
    if raw.lower() in {"here", "@here"}:
        return "@here"
    # "role:12345" or a plain role name -> <@&id> only if it looks like an id.
    if raw.lower().startswith("role:"):
        role_id = raw.split(":", 1)[1].strip()
        return f"<@&{role_id}>" if role_id.isdigit() else ""
    return raw


def allowed_mentions_for(mention: str) -> dict:
    """Discord silently ignores mentions unless they are explicitly allowed."""
    if not mention:
        return {"parse": []}
    if mention in {"@everyone", "@here"}:
        return {"parse": [mention.lstrip("@")]}
    if mention.startswith("<@&"):
        return {"roles": [mention.removeprefix("<@&").rstrip(">")]}
    return {"parse": []}


def send(
    calendar_webhook,
    state,
    candidates: list[tuple[dict, int]],
    now: Optional[_dt.datetime] = None,
    force: bool = False,
    mention: str = "",
) -> int:
    """Post reminders and mark them as reminded. Returns the number sent."""
    now = now or _dt.datetime.now(WITA)
    today = now.date()
    allowed = allowed_mentions_for(mention)
    sent = 0
    for item, off in candidates:
        key = reminder_key(item, off)
        text = A.render_reminder(item, off, today, mention=mention)
        if calendar_webhook.send_text(text, allowed_mentions=allowed):
            # --force only bypasses the "already sent" check; it still records.
            state.mark_reminded(key)
            sent += 1
            log.info("reminder sent: %s (%s)", item.get("text", "")[:60], key)
        else:
            log.warning("reminder failed to post: %s", key)
    if sent:
        state.save()
    return sent


def run_reminder_pass(
    cfg,
    state,
    calendar_webhook,
    now: Optional[_dt.datetime] = None,
    force: bool = False,
    dry_run: bool = False,
) -> dict:
    """Full pass used by ``watch`` and the ``remind`` command."""
    tz = get_tz(cfg.get_str("reminders.timezone", "Asia/Makassar"))
    now = (now or _dt.datetime.now(WITA)).astimezone(tz)
    send_time = cfg.send_time()
    days_before = cfg.reminder_days_before()
    items = A.collect_from_videos(state.all_videos())
    candidates, diag = pending(
        items, state, now=now, days_before=days_before, send_time=send_time, force=force
    )
    result = {"candidates": len(candidates), "sent": 0, "diagnostics": diag}
    mention = mention_of(cfg)
    if dry_run:
        print(format_dry_run(candidates, diag))
        if mention:
            print(f"\n(mention yang akan dipakai: {mention})")
        return result
    if not candidates:
        log.info("reminder pass: nothing due (undated=%d)", diag["undated"])
        return result
    result["sent"] = send(
        calendar_webhook, state, candidates, now=now, force=force, mention=mention
    )
    return result
