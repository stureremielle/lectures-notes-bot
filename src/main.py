"""CLI: ``run`` / ``watch`` / ``list`` / ``remind`` / ``calendar-image``.

``watch`` is the Docker entrypoint. It runs once immediately, then sleeps until
the next tick. Each tick is computed from the **end** of the previous run so
container wake-up jitter never accumulates. Each tick runs, in order:
reminder pass → calendar-image pass → playlist scan.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import logging
import os
import sys
import time
import traceback
from pathlib import Path
from typing import Iterable, Optional

from . import assignments as A
from . import calendar_image, courses, notes, playlist, prompts, reminders, transcript
from .config import Config, ConfigError, load_config
from .discord_webhook import DiscordWebhook, split_message
from .llm import LLMError, make_client
from .logging_setup import get_logger, setup_logging
from .state import State
from .timeutil import WITA, format_date_id, get_tz, parse_date

log = get_logger("main")

EXIT_OK = 0
EXIT_ERROR = 1


# ---------------------------------------------------------------------------
# Wiring helpers
# ---------------------------------------------------------------------------
class Context:
    """Everything the commands need, built once from the config."""

    def __init__(
        self,
        cfg: Config,
        dry_run: bool = False,
        no_discord: bool = False,
        reload_config: bool = False,
    ):
        self.cfg = cfg
        # Only the long-running watcher re-reads config.yaml between ticks.
        self.reload_config = reload_config
        self.dry_run = dry_run
        self.no_discord = no_discord
        self.tz = get_tz(cfg.timezone_name)
        self.state = State(cfg.state_path).load()
        self.notes_hook = DiscordWebhook(
            cfg.get_str("discord.notes_webhook", ""),
            label="notes",
            dry_run=dry_run or no_discord,
            username=cfg.get_str("discord.username", "Catatan Kuliah"),
        )
        self.calendar_hook = DiscordWebhook(
            cfg.get_str("discord.calendar_webhook", ""),
            label="calendar",
            dry_run=dry_run or no_discord,
            username=cfg.get_str("discord.username", "Catatan Kuliah"),
        )
        self._client = None

    @property
    def client(self):
        if self._client is None:
            self._client = make_client(self.cfg)
        return self._client

    @property
    def aliases(self) -> courses.AliasMap:
        """Course aliases from config.yaml (``playlists[].course_aliases``)."""
        return courses.AliasMap.from_config(self.cfg)

    def now(self) -> _dt.datetime:
        return _dt.datetime.now(self.tz)


def build_context(args, reload_config: bool = False) -> Context:
    cfg = load_config(getattr(args, "config", None))
    setup_logging(cfg.get_str("logging.level", "INFO"))
    for warning in cfg.validate_for_run():
        log.warning(warning)
    # Dry-run previews show payloads; keep the log readable.
    if getattr(args, "dry_run", False):
        logging.getLogger("discord").setLevel(logging.WARNING)
    return Context(
        cfg,
        dry_run=getattr(args, "dry_run", False),
        no_discord=getattr(args, "no_discord", False),
        reload_config=reload_config,
    )


# ---------------------------------------------------------------------------
# Selecting playlists
# ---------------------------------------------------------------------------
def select_playlists(cfg: Config, only_url: Optional[str], use_all: bool) -> list[dict]:
    configured = [p for p in cfg.playlists() if p.get("enabled", True)]
    if only_url:
        for p in configured:
            if p["url"] == only_url:
                return [p]
        return [{"name": only_url, "url": only_url, "max_videos": None, "enabled": True}]
    if not configured:
        raise ConfigError("no playlists configured (config.playlists) and no --playlist given")
    if use_all or len(configured) == 1:
        return configured
    raise ConfigError(
        "multiple playlists configured: pass --all or --playlist URL "
        f"({len(configured)} playlists)"
    )


# ---------------------------------------------------------------------------
# Per-video pipeline
# ---------------------------------------------------------------------------
def process_video(ctx: Context, video: dict, playlist_name: str, limit_hit: bool = False) -> Optional[dict]:
    """Transcript -> LLM -> normalise -> post. Returns the stored record or None."""
    video = dict(video)
    video["playlist_title"] = playlist_name or video.get("playlist_title") or ""
    # One playlist can hold several courses: resolve the course per video
    # (title aliases -> configured names -> title prefix), never from the
    # playlist name alone.
    course = courses.resolve_course(
        video,
        ctx.aliases,
        [p["name"] for p in ctx.cfg.playlists()],
        ctx.cfg.get_str("pipeline.course_fallback", "Lainnya"),
    )
    video["course"] = course
    url = video.get("webpage_url") or video.get("url")
    vid = video.get("id")
    log.info("processing: %s [%s / %s] — %s", vid or "?", playlist_name, course, video.get("title") or url)
    try:
        text, meta = transcript.build_transcript(url)
    except transcript.TranscriptError as exc:
        log.warning("no transcript for %s: %s", vid, exc)
        return None
    except Exception as exc:  # noqa: BLE001 - one bad video must not kill the run
        log.warning("transcript error for %s: %s", vid, exc)
        return None
    if not vid and meta.get("video_id"):
        vid = meta["video_id"]
        video["id"] = vid
    if meta.get("title") and not video.get("title"):
        video["title"] = meta["title"]
        course = courses.resolve_course(
            video,
            ctx.aliases,
            [p["name"] for p in ctx.cfg.playlists()],
            ctx.cfg.get_str("pipeline.course_fallback", "Lainnya"),
        )
        video["course"] = course
    log.info(
        "transcript: %d chars, %d cues, %s/%s",
        len(text),
        meta.get("cues", 0),
        meta.get("kind"),
        meta.get("lang"),
    )
    try:
        raw = notes.generate_notes(ctx.client, ctx.cfg, video, text, meta)
    except LLMError as exc:
        log.error("LLM failed for %s: %s", vid, exc)
        return None
    note = notes.normalise(raw, video)
    if note is None:
        saved = notes.dump_unusable(ctx.cfg.cache_dir, vid, raw)
        log.error(
            "model returned unusable notes for %s; skipping this video (jawaban mentah: %s)",
            vid,
            saved,
        )
        return None
    if note.get("dropped_assignments"):
        log.info("dropped %d low-quality/duplicate assignment(s) for %s", note["dropped_assignments"], vid)
    # Summarise-style models often fold assignment instructions into the prose
    # instead of emitting them as items. When none came back, run a focused
    # second pass over just the assignment-like paragraphs.
    if not note.get("assignments") and ctx.cfg.get_bool("notes.assignment_second_pass", True):
        try:
            extra = notes.extract_assignments(ctx.client, ctx.cfg, video, text)
        except Exception as exc:  # noqa: BLE001 - never lose the note over this
            log.warning("assignment second pass error for %s: %s", vid, exc)
            extra = []
        added = notes.merge_assignments(note, extra)
        if added:
            log.info("assignment second pass added %d tugas for %s", added, vid)
        else:
            log.info(
                "assignment second pass found nothing new for %s (raw keys: %s)",
                vid,
                ", ".join(sorted(raw)) if isinstance(raw, dict) else "-",
            )

    record = dict(video)
    record.update(
        {
            "id": vid,
            "playlist_title": playlist_name,
            "course": course,
            "caption_lang": meta.get("lang"),
            "caption_kind": meta.get("kind"),
            "transcript_chars": len(text),
            "processed_at": ctx.now().isoformat(timespec="seconds"),
            "note": note,
        }
    )

    if ctx.dry_run:
        # Preview only: no posts, and the state file is left untouched so the
        # daily baseline rules behave exactly as if this run never happened.
        print(notes.render_dry_run(note, record))
        if note.get("assignments"):
            print("\n=== ASSIGNMENTS (#calendar) ===")
            print(A.render_list(note["assignments"]))
        return record

    posted = post_note(ctx, record)
    record["posted"] = posted
    ctx.state.add_video(vid, record)
    ctx.state.save()
    if posted:
        post_assignments_for(ctx, record)
        try:
            post_calendar_snapshot(ctx)
        except Exception as exc:  # noqa: BLE001 - the .ics is a bonus, not a requirement
            log.warning("could not build/post the .ics: %s", exc)
    return record


def post_note(ctx: Context, record: dict) -> bool:
    note = record["note"]
    embed = notes.build_embed(note, record)
    message = notes.render_notes_message(note, record)
    ok = ctx.notes_hook.send_text_and_embeds(message, [embed])
    if ok:
        log.info("notes posted for %s", record.get("id"))
    else:
        log.warning("notes post failed for %s (state still records it)", record.get("id"))
    return ok


def post_assignments_for(ctx: Context, record: dict) -> None:
    items = (record.get("note") or {}).get("assignments") or []
    if not items:
        return
    course = record.get("course") or record.get("playlist_title")
    for item in items:
        item.setdefault("course", course)
        item.setdefault("video_title", record.get("title"))
        item.setdefault("video_url", record.get("webpage_url") or record.get("url"))
    label = " · ".join(x for x in (course, record.get("title")) if x)
    ctx.calendar_hook.send_text(A.render_list(items, title=f"Tugas dari: {label or 'kuliah'}"))


def post_calendar_snapshot(ctx: Context) -> None:
    """All dated assignments as an .ics attachment (same channel as reminders)."""
    items = A.collect_from_videos(ctx.state.all_videos())
    dated = A.dated_items(items)
    if not dated:
        return
    out_dir = ctx.cfg.cache_dir / "ics"
    out_dir.mkdir(parents=True, exist_ok=True)
    name = A.ics_filename(ctx.now().date())
    path = out_dir / "tugas.ics"
    path.write_text(
        A.build_ics(items, timezone_name=ctx.cfg.timezone_name, now=ctx.now()), encoding="utf-8"
    )
    embed = {
        "title": f"📅 {len(dated)} tenggat tercatat",
        "description": f"{A.undated_count(items)} tugas tanpa tanggal tidak dimasukkan.",
        "color": 0x2980B9,
        "footer": {"text": "Impor berkas .ics ini ke kalender pribadi Anda."},
    }
    ctx.calendar_hook.send_files([path], content=f"Berkas kalender `{name}`", embeds=[embed])


# ---------------------------------------------------------------------------
# Playlist scan
# ---------------------------------------------------------------------------
def _is_new(ctx: Context, video: dict) -> bool:
    """Only videos whose ID is in neither ``videos`` nor ``skipped``."""
    vid = video["id"]
    return not ctx.state.is_skipped(vid) and not ctx.state.has_video(vid)


def scan_playlist(
    ctx: Context,
    entry: dict,
    limit: Optional[int] = None,
    force: bool = False,
    seen: bool = False,
) -> dict:
    """Enumerate a playlist, apply baseline rules, process new videos.

    ``seen=True`` (the daily ``watch`` tick) records baseline skips in the state
    file but does not advance the baseline stamp of an already-known playlist.
    """
    url = entry.get("url") or ""
    name = entry.get("name") or url
    result = {"playlist": name, "enumerated": 0, "processed": 0, "failed": 0, "baseline": False}
    if not url:
        log.warning("playlist %s has no URL configured; skipping", name)
        return result
    try:
        videos = playlist.enumerate_playlist(url)
        if not videos:
            raise playlist.PlaylistError("no videos returned")
    except playlist.PlaylistError as exc:
        log.warning("%s: %s", name, exc)
        videos = []
    result["enumerated"] = len(videos)

    for v in videos:
        v["playlist_url"] = url
        v["playlist_title"] = name
    if not videos and not force:
        return result

    # Videos that must never be processed (wrong uploads, announcements, ...).
    ignored = {str(x) for x in ctx.cfg.get_list("watch.ignore_ids", [])}
    if ignored:
        dropped = [v for v in videos if v["id"] in ignored]
        videos = [v for v in videos if v["id"] not in ignored]
        if dropped:
            log.info("%s: ignoring %d video(s) from watch.ignore_ids", name, len(dropped))

    # The daily run processes only brand-new uploads, oldest first: the oldest
    # of today's batch is the lecture whose transcript next week's session
    # builds on. Older videos in `skipped` are never touched automatically.
    videos = playlist.oldest_first(videos)
    order_new_first = ctx.cfg.get_str("watch.backfill_order", "oldest").lower().startswith("new")

    backfill = ctx.cfg.get_bool("watch.backfill_on_first_run", False)
    known_playlist = ctx.state.has_baseline(url)
    catch_up = bool(force) and limit is not None

    # --dry-run must not change anything: no baseline stamp, no skip list.
    # The baseline pass runs ONLY when this playlist has never been seen; a
    # slide of history left by a --force --limit catch-up must never be picked
    # up as "new uploads" by the next plain run.
    if ctx.dry_run and not known_playlist:
        log.info(
            "%s: first encounter — normally the baseline would be stamped and all %d "
            "existing video(s) skipped; dry-run processes %s instead",
            name,
            len(videos),
            "the requested video(s)" if limit else "nothing",
        )
        candidates = videos[:limit] if limit else []
    elif force:
        # A forced run re-stamps the baseline and processes what it was asked
        # for. Anything it did NOT process becomes known history immediately,
        # otherwise the next plain run would treat it as a new upload and spend
        # tokens on it.
        ctx.state.stamp_baseline(url, ctx.now())
        candidates = (playlist.newest_first(videos) if (order_new_first and limit) else videos)
        chosen = candidates[:limit] if limit is not None else candidates
        left_over = [v["id"] for v in candidates if v not in chosen]
        if left_over:
            ctx.state.mark_skipped(left_over)
            log.info(
                "%s: --force --limit %d — %d lecture(s) left as known history (%s)",
                name,
                limit,
                len(left_over),
                ", ".join(left_over[:8]),
            )
        candidates = chosen
    elif not known_playlist:
        # True first encounter (also the --force catch-up path): no history
        # backfill unless explicitly asked. Zero LLM calls by default.
        ctx.state.stamp_baseline(url, ctx.now())
        # Every current video becomes known history right away, so nothing is
        # ever picked up later by accident.
        ctx.state.mark_skipped([v["id"] for v in videos])
        ctx.state.save()
        if backfill:
            log.info(
                "%s: first run with backfill_on_first_run=true — processing %d existing video(s)",
                name,
                len(videos),
            )
            candidates = videos[:limit] if limit is not None else videos
        else:
            result["baseline"] = True
            log.info("baseline stamped, skipping all %d existing videos (%s)", len(videos), name)
            if not catch_up:
                log.info('%s: watch mode — existing videos stay skipped, waiting for new uploads', name)
                return result
            # Explicit catch-up on a slice: newest (or oldest) first, rest stays skipped.
            candidates = (playlist.newest_first(videos) if order_new_first else videos)[:limit]
            log.info(
                "%s: catch-up on %d lecture(s) (%s first); %d remain skipped",
                name,
                len(candidates),
                "newest" if order_new_first else "oldest",
                len(videos) - len(candidates),
            )
    else:
        candidates = [v for v in videos if _is_new(ctx, v)]
        if limit is not None and not ctx.dry_run and len(candidates) > limit:
            # An explicit --limit on new uploads: keep the rest out of "new" so a
            # later run does not silently pay for them; list them explicitly.
            rest = candidates[limit:]
            ctx.state.mark_skipped([v["id"] for v in rest])
            ctx.state.save()
            log.info(
                "%s: --limit %d — %d other new video(s) marked as skipped (%s)",
                name,
                limit,
                len(rest),
                ", ".join(v["id"] for v in rest[:8]),
            )
        if backfill:
            # Backfill only matters on the first encounter (handled above); later
            # runs stay incremental even with the flag on.
            log.info("backfill_on_first_run=true but baseline already exists; staying incremental")

    # NOTE: candidates are already limited inside each branch. A blanket
    # `candidates[:limit]` here would turn a plain run into a processing run
    # and spend tokens on history that the baseline said to skip.
    if not candidates:
        log.info("%s: no new videos (%d in playlist)", name, len(videos))
        return result

    log.info("%s: processing %d new video(s) of %d", name, len(candidates), len(videos))
    for v in candidates:
        rec = process_video(ctx, v, name)
        if rec is None:
            result["failed"] += 1
        else:
            result["processed"] += 1
    return result


def run_playlists(
    ctx: Context,
    entries: Iterable[dict],
    limit: Optional[int],
    force: bool,
    seen: bool = False,
) -> list[dict]:
    results = []
    for entry in entries:
        try:
            results.append(scan_playlist(ctx, entry, limit=limit, force=force, seen=seen))
        except Exception as exc:  # noqa: BLE001 - continue with the next playlist
            log.error("playlist %s crashed: %s", entry.get("name"), exc)
            log.debug(traceback.format_exc())
            results.append({"playlist": entry.get("name"), "failed": 1, "error": str(exc)})
    return results


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------
def cmd_run(args) -> int:
    ctx = build_context(args)
    try:
        entries = select_playlists(ctx.cfg, args.playlist, args.all)
    except ConfigError as exc:
        log.error("%s", exc)
        return EXIT_ERROR
    log.info(
        "run: %d playlist(s), limit=%s, force=%s, dry_run=%s, no_discord=%s",
        len(entries),
        args.limit,
        args.force,
        args.dry_run,
        args.no_discord,
    )
    results = run_playlists(ctx, entries, limit=args.limit, force=args.force)
    total = sum(r.get("processed", 0) for r in results)
    failed = sum(r.get("failed", 0) for r in results)
    log.info("run finished: %d processed, %d failed", total, failed)
    return EXIT_OK if failed == 0 or total else EXIT_ERROR if failed else EXIT_OK


def cmd_list(args) -> int:
    cfg = load_config(getattr(args, "config", None))
    setup_logging(cfg.get_str("logging.level", "INFO"))
    url = args.playlist
    if not url:
        configured = [p for p in cfg.playlists() if p.get("enabled", True)]
        if len(configured) != 1:
            print("usage: python -m src.main list --playlist URL", file=sys.stderr)
            return EXIT_ERROR
        url = configured[0]["url"]
    try:
        videos = playlist.list_playlist(url)
    except playlist.PlaylistError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR
    state = State(cfg.state_path).load()
    print(f"{len(videos)} video(s) in {url}")
    for v in videos:
        marks = []
        if state.has_baseline(url):
            if state.has_video(v["id"]):
                marks.append("seen")
            elif state.is_skipped(v["id"]):
                marks.append("skipped")
            else:
                marks.append("new")
        dur = v.get("duration")
        dur_txt = ""
        if isinstance(dur, (int, float)) and dur:
            m, s = divmod(int(dur), 60)
            dur_txt = f" [{m}:{s:02d}]"
        print(f"  {v['id']}  {v.get('title') or ''}{dur_txt}  {' '.join(marks)}".rstrip())
    return EXIT_OK


def cmd_remind(args) -> int:
    ctx = build_context(args)
    result = reminders.run_reminder_pass(
        ctx.cfg,
        ctx.state,
        ctx.calendar_hook,
        now=ctx.now(),
        force=args.force,
        dry_run=args.dry_run,
    )
    if not args.dry_run:
        log.info("remind: %d candidate(s), %d sent", result["candidates"], result["sent"])
    return EXIT_OK


def cmd_calendar_image(args) -> int:
    cfg = load_config(getattr(args, "config", None))
    setup_logging(cfg.get_str("logging.level", "INFO"))
    ctx = Context(cfg, dry_run=args.dry_run, no_discord=args.no_discord)
    now = ctx.now()
    if args.month:
        try:
            year, month = [int(x) for x in str(args.month).split("-")[:2]]
            _dt.date(year, month, 1)
        except (ValueError, IndexError):
            print(f"error: invalid --month {args.month!r}, expected YYYY-MM", file=sys.stderr)
            return EXIT_ERROR
    else:
        year, month = calendar_image.target_month(cfg, now)
    items = A.collect_from_videos(ctx.state.all_videos())
    out = Path(args.out) if args.out else Path(cfg.get_str("calendar.image.output_dir", "data/calendar")) / "calendar.png"
    path = calendar_image.render_month(items, year, month, today=now.date(), out_path=out)
    print(f"calendar PNG: {path} ({A.month_label(year, month)}, {len(A.dated_items(items))} dated assignment(s) total)")
    if not ctx.dry_run and not ctx.no_discord:
        ok = ctx.calendar_hook.send_files(
            [path],
            content=f"🗓️ **Kalender tugas {A.month_label(year, month)}** (WITA)",
            embeds=[{"image": {"url": "attachment://calendar.png"}, "color": calendar_image.ACCENT_HEX}],
        )
        print("posted to #calendar" if ok else "post to #calendar failed")
    return EXIT_OK


# ---------------------------------------------------------------------------
# watch
# ---------------------------------------------------------------------------
def _parse_hhmm_local(value: str, tz) -> _dt.datetime:
    """Next occurrence (today or tomorrow) of HH:MM in the configured zone."""
    hour, minute = [int(x) for x in value.split(":")[:2]]
    now = _dt.datetime.now(tz)
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += _dt.timedelta(days=1)
    return target


def next_tick(schedule: list[str], last_end: _dt.datetime, tz) -> _dt.datetime:
    """Earliest scheduled time strictly after ``last_end`` (in ``tz``)."""
    candidates: list[_dt.datetime] = []
    base = last_end.astimezone(tz)
    for hhmm in schedule:
        hour, minute = [int(x) for x in hhmm.split(":")[:2]]
        for delta in (0, 1):
            day = base.date() + _dt.timedelta(days=delta)
            cand = _dt.datetime.combine(day, _dt.time(hour, minute), tzinfo=tz)
            if cand > base:
                candidates.append(cand)
                break
    return min(candidates)


def run_tick(ctx: Context, force: bool) -> None:
    now = ctx.now()
    log.info("tick: %s (WITA)", now.strftime("%Y-%m-%d %H:%M:%S"))
    # The daily loop is long-lived: re-read the config every tick so edits (a
    # filled-in playlist URL, a new schedule) apply without a restart. Only the
    # watcher does this — a one-shot `run` must keep the config it was started
    # with, otherwise playlist paths/settings can drift under it. A bad file
    # keeps the config this process started with.
    if ctx.reload_config:
        try:
            from .config import load_config

            ctx.cfg = load_config(ctx.cfg.path)
        except Exception as exc:  # noqa: BLE001
            log.warning("config reload failed (%s); using the previous config", exc)
    entries = [p for p in ctx.cfg.playlists() if p.get("enabled", True)]
    try:
        reminders.run_reminder_pass(ctx.cfg, ctx.state, ctx.calendar_hook, now=now, force=force)
    except Exception as exc:  # noqa: BLE001 - a failing pass must not stop the loop
        log.error("reminder pass failed: %s", exc)
        log.debug(traceback.format_exc())
    try:
        res = calendar_image.run_calendar_pass(ctx.cfg, ctx.state, ctx.calendar_hook, now=now)
        if res.get("posted"):
            log.info("calendar image posted for week %s", res.get("week"))
    except Exception as exc:  # noqa: BLE001
        log.error("calendar image pass failed: %s", exc)
        log.debug(traceback.format_exc())
    try:
        run_playlists(ctx, entries, limit=None, force=force, seen=True)
    except Exception as exc:  # noqa: BLE001
        log.error("playlist scan failed: %s", exc)
        log.debug(traceback.format_exc())


def wait_until(target: _dt.datetime, stop_check=lambda: False) -> None:
    while True:
        now = _dt.datetime.now(target.tzinfo)
        remaining = (target - now).total_seconds()
        if remaining <= 0:
            return
        time.sleep(min(remaining, 60))


def cmd_watch(args) -> int:
    ctx = build_context(args, reload_config=True)
    try:
        schedule = ctx.cfg.watch_times()
    except ConfigError as exc:
        log.error("%s", exc)
        return EXIT_ERROR
    entries = [p for p in ctx.cfg.playlists() if p.get("enabled", True)]
    if not entries:
        log.warning("no playlists configured yet; the loop keeps running and will pick them up after an edit")
    send_time = ctx.cfg.send_time()
    log.info(
        "watch: schedule %s (%s), send_time %s, %d playlist(s)",
        ", ".join(schedule),
        ctx.cfg.timezone_name,
        send_time.strftime("%H:%M"),
        len(entries),
    )
    last_end = _dt.datetime.now(ctx.tz)
    first = True
    while True:
        try:
            if first:
                log.info("watch: initial run on startup")
                first = False
            else:
                schedule = ctx.cfg.watch_times()
                target = next_tick(schedule, last_end, ctx.tz)
                log.info("watch: next tick at %s (WITA)", target.strftime("%Y-%m-%d %H:%M:%S"))
                wait_until(target)
            run_tick(ctx, force=args.force)
        except KeyboardInterrupt:
            log.info("watch: interrupted, exiting")
            return EXIT_OK
        except Exception as exc:  # noqa: BLE001 - the loop must survive anything
            log.error("watch: tick crashed: %s", exc)
            log.debug(traceback.format_exc())
        finally:
            last_end = _dt.datetime.now(ctx.tz)


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lecture-notes-bot",
        description="YouTube lecture playlists -> Indonesian notes + assignments -> Discord.",
    )
    parser.add_argument("--config", help="path to config.yaml (default: ./config.yaml)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_run = sub.add_parser("run", help="process playlists once")
    p_run.add_argument("--playlist", help="only this playlist URL")
    p_run.add_argument("--all", action="store_true", help="all configured playlists")
    p_run.add_argument("--limit", type=int, default=None, help="max videos per playlist")
    p_run.add_argument("--force", action="store_true", help="reprocess everything (incl. baseline videos)")
    p_run.add_argument("--no-discord", action="store_true", help="do not post to Discord")
    p_run.add_argument("--dry-run", action="store_true", help="print what would be posted, change nothing")
    p_run.set_defaults(func=cmd_run)

    p_watch = sub.add_parser("watch", help="run on a daily schedule (Docker entrypoint)")
    p_watch.add_argument("--all", action="store_true", help="all configured playlists")
    p_watch.add_argument("--dry-run", action="store_true", help="print instead of posting")
    p_watch.add_argument("--force", action="store_true", help="force every tick")
    p_watch.add_argument("--no-discord", action="store_true", help="do not post to Discord")
    p_watch.set_defaults(func=cmd_watch)

    p_list = sub.add_parser("list", help="list videos of a playlist")
    p_list.add_argument("--playlist", help="playlist URL")
    p_list.set_defaults(func=cmd_list)

    p_rem = sub.add_parser("remind", help="send due reminders now")
    p_rem.add_argument("--dry-run", action="store_true")
    p_rem.add_argument("--force", action="store_true", help="ignore the dedupe list")
    p_rem.add_argument("--no-discord", action="store_true")
    p_rem.set_defaults(func=cmd_remind)

    p_cal = sub.add_parser("calendar-image", help="render (and post) the monthly calendar PNG")
    p_cal.add_argument("--month", help="YYYY-MM (default: current/next month)")
    p_cal.add_argument("--out", help="output PNG path (default: data/calendar/calendar.png)")
    p_cal.add_argument("--dry-run", action="store_true")
    p_cal.add_argument("--no-discord", action="store_true")
    p_cal.set_defaults(func=cmd_calendar_image)
    return parser


def container_config_report() -> str:
    """One-line diagnosis of /app/config.yaml + /app/data, for failed starts."""
    cfg_path = Path(os.environ.get("LECTURE_BOT_CONFIG", "/app/config.yaml"))
    data_dir = Path("/app/data")
    bits = [f"config={cfg_path} " + (
        "DIRECTORY (host file missing)" if cfg_path.is_dir()
        else "file ok" if cfg_path.is_file()
        else "missing"
    )]
    bits.append(f"data={data_dir} " + ("writable" if os.access(data_dir, os.W_OK) else "NOT writable"))
    env_keys = [k for k in ("OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_MODEL",
                            "DISCORD_NOTES_WEBHOOK", "DISCORD_CALENDAR_WEBHOOK") if os.environ.get(k)]
    bits.append(f"secrets={len(env_keys)}/5 present")
    return "; ".join(bits)


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or EXIT_OK)
    except ConfigError as exc:
        # A misconfigured container would otherwise restart-loop with a one-line
        # fatal message in the json log; print the full diagnosis ONCE and exit.
        log.error("config error — %s", exc)
        log.error(
            "container configuration: %s",
            container_config_report(),
        )
        return EXIT_ERROR
    except KeyboardInterrupt:
        return EXIT_OK
    except Exception as exc:  # noqa: BLE001 - never dump a traceback to the user
        log.error("fatal: %s", exc)
        log.debug(traceback.format_exc())
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
