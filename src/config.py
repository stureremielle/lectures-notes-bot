"""Configuration loading: YAML + `${ENV}` expansion + dotted access.

The config file is intentionally small. Everything that depends on *when* the
bot runs (timers, reminder offsets, timezone) lives here so a deployment can be
changed by editing one bind-mounted file.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Iterable, Optional, Union

import yaml

from .timeutil import get_tz, parse_hhmm

DEFAULT_CONFIG_PATH = "config.yaml"
_ENV_PATTERN = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")


class ConfigError(RuntimeError):
    pass


def _expand_env(value: Any) -> Any:
    """Recursively expand ${VAR} and ${VAR:-default} inside strings."""
    if isinstance(value, str):

        def repl(match: re.Match) -> str:
            name, default = match.group(1), match.group(2)
            found = os.environ.get(name)
            if found is None:
                return default or ""
            return found

        return _ENV_PATTERN.sub(repl, value)
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    return value


class Section:
    """Dict wrapper with dotted access and helpful defaults."""

    def __init__(self, data: Optional[dict] = None, path: str = ""):
        self._data = dict(data or {})
        self._path = path

    # -- mapping behaviour -------------------------------------------------
    def __getitem__(self, key):
        return self._data[key]

    def __contains__(self, key) -> bool:
        return key in self._data

    def __iter__(self):
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)

    def items(self):
        return self._data.items()

    def keys(self):
        return self._data.keys()

    def as_dict(self) -> dict:
        return dict(self._data)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"Section({self._path!r}, {self._data!r})"

    # -- dotted access -----------------------------------------------------
    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self._data
        parts = [p for p in dotted.split(".") if p]
        for i, part in enumerate(parts):
            if isinstance(node, Section):
                node = node._data
            if isinstance(node, dict):
                if part not in node:
                    return default
                node = node[part]
            elif isinstance(node, list):
                try:
                    node = node[int(part)]
                except (ValueError, IndexError):
                    return default
            else:
                return default
        if isinstance(node, dict):
            return Section(node, ".".join([self._path] + parts).lstrip("."))
        return node

    def section(self, dotted: str) -> "Section":
        value = self.get(dotted, {})
        if isinstance(value, Section):
            return value
        if isinstance(value, dict):
            return Section(value, dotted)
        return Section({}, dotted)

    def get_str(self, dotted: str, default: str = "") -> str:
        value = self.get(dotted, default)
        return default if value is None else str(value)

    def get_bool(self, dotted: str, default: bool = False) -> bool:
        value = self.get(dotted, default)
        if isinstance(value, bool):
            return value
        if value is None:
            return default
        return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}

    def get_int(self, dotted: str, default: int = 0) -> int:
        try:
            value = self.get(dotted, default)
            if value is None or value == "":
                return default
            return int(value)
        except (TypeError, ValueError):
            return default

    def get_list(self, dotted: str, default: Optional[Iterable[Any]] = None) -> list:
        value = self.get(dotted, None)
        if value is None:
            return list(default or [])
        if isinstance(value, (list, tuple)):
            return list(value)
        if isinstance(value, str):
            return [part.strip() for part in value.split(",") if part.strip()]
        return [value]


def env_bindings() -> dict:
    """Map well-known environment variables onto config paths.

    Values from ``environment:`` in the Portainer stack win over config.yaml,
    so secrets never have to live in the bind-mounted file.
    """
    mapping = {
        "OPENAI_BASE_URL": "llm.base_url",
        "OPENAI_API_KEY": "llm.api_key",
        "OPENAI_MODEL": "llm.model",
        "DISCORD_NOTES_WEBHOOK": "discord.notes_webhook",
        "DISCORD_CALENDAR_WEBHOOK": "discord.calendar_webhook",
        "TZ": "reminders.timezone",
    }
    return {path: os.environ[name] for name, path in mapping.items() if os.environ.get(name)}


def _set_dotted(target: dict, dotted: str, value: Any) -> None:
    node = target
    parts = dotted.split(".")
    for part in parts[:-1]:
        nxt = node.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            node[part] = nxt
        node = nxt
    current = node.get(parts[-1])
    # Environment bindings never clobber an existing non-empty file value
    # unless the file used a ${ENV} placeholder (already expanded to "").
    if current not in (None, "") and isinstance(value, str) and not value.strip():
        return
    node[parts[-1]] = value


def _alias_list(raw) -> list[str]:
    if isinstance(raw, str):
        return [a.strip() for a in raw.split(",") if a.strip()]
    if isinstance(raw, (list, tuple)):
        return [str(a).strip() for a in raw if str(a).strip()]
    return []


def _normalise_aliases(raw, default_course: str) -> dict[str, list[str]]:
    """Normalise ``course_aliases`` into ``{course_name: [alias, ...]}``.

    Accepted forms::

        course_aliases: "SPK, SM"                  # aliases of this playlist's label
        course_aliases: ["SPK", "SM"]
        course_aliases:                            # one playlist, several courses
          Sistem Penunjang Keputusan: [SPK]
          Sistem Multimedia: [SM]
    """
    if not raw:
        return {}
    if isinstance(raw, dict):
        out: dict[str, list[str]] = {}
        for course, aliases in raw.items():
            items = _alias_list(aliases)
            if items:
                out[str(course).strip()] = items
        return out
    items = _alias_list(raw)
    return {default_course: items} if items else {}


class Config(Section):
    def __init__(self, data: Optional[dict] = None, path: Optional[Path] = None):
        super().__init__(data or {}, "")
        self.path = path

    # -- typed helpers -----------------------------------------------------
    @property
    def timezone_name(self) -> str:
        return self.get_str("reminders.timezone", "Asia/Makassar")

    @property
    def tz(self):
        return get_tz(self.timezone_name)

    @property
    def data_dir(self) -> Path:
        return Path(self.get_str("paths.data_dir", "data"))

    @property
    def state_path(self) -> Path:
        return Path(self.get_str("paths.state_file", str(self.data_dir / "state.json")))

    @property
    def cache_dir(self) -> Path:
        return Path(self.get_str("paths.cache_dir", str(self.data_dir / "cache")))

    def watch_times(self) -> list[str]:
        """Normalised list of "HH:MM" strings from watch.run_at_local."""
        raw = self.get_list("watch.run_at_local", ["19:00"])
        out: list[str] = []
        for item in raw:
            if isinstance(item, (int, float)):
                item = f"{int(item):02d}:00"
            text = str(item).strip()
            if not text:
                continue
            t = parse_hhmm(text)
            out.append(t.strftime("%H:%M"))
        out = sorted(set(out))
        if not out:
            raise ConfigError("watch.run_at_local is empty; give at least one 'HH:MM'")
        return out

    def send_time(self):
        return parse_hhmm(self.get_str("reminders.send_time_local", "08:00"))

    def reminder_days_before(self) -> list[int]:
        values: list[int] = []
        for item in self.get_list("reminders.days_before", [1, 0]):
            try:
                values.append(int(item))
            except (TypeError, ValueError):
                continue
        return sorted(set(values), reverse=True)

    def playlists(self) -> list[dict]:
        raw = self.get("playlists", [])
        result: list[dict] = []
        if isinstance(raw, Section):
            raw = raw.as_dict()
        if isinstance(raw, dict):
            raw = [dict(v or {}, name=k) for k, v in raw.items()]
        if not isinstance(raw, list):
            return []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            name = str(entry.get("name") or "").strip()
            url = str(entry.get("url") or "").strip()
            if not name and not url:
                continue
            result.append(
                {
                    "name": name or url,
                    "url": url,
                    "max_videos": entry.get("max_videos"),
                    "course_aliases": _normalise_aliases(
                        entry.get("course_aliases"), name or url
                    ),
                    "enabled": bool(entry.get("enabled", True)),
                }
            )
        return result

    def validate_for_run(self) -> list[str]:
        """Return a list of human-readable warnings (never raises for content)."""
        warns: list[str] = []
        try:
            times = self.watch_times()
        except ConfigError as exc:
            return [str(exc)]
        send = self.send_time()
        if times and send >= parse_hhmm(times[0]):
            warns.append(
                "reminders.send_time_local "
                f"({send.strftime('%H:%M')}) is not earlier than the first "
                f"watch.run_at_local entry ({times[0]}); reminders will fire late "
                "or on the following tick only. Set send_time_local earlier "
                "(e.g. 08:00 for a 19:00 tick)."
            )
        if not self.get_str("llm.api_key"):
            warns.append("llm.api_key is empty (set OPENAI_API_KEY or llm.api_key).")
        if not self.get_str("discord.notes_webhook"):
            warns.append("discord.notes_webhook is empty; notes will not be posted.")
        if not self.get_str("discord.calendar_webhook"):
            warns.append("discord.calendar_webhook is empty; calendar output disabled.")
        return warns


def _report_bad_mount(p: Path) -> None:
    """A directory where a file is expected is almost always a bind mount
    created by Docker from a host path that did not exist at deploy time."""
    try:
        listing = ", ".join(sorted(x.name for x in p.iterdir())[:10]) or "empty"
    except OSError:
        listing = "unreadable"
    raise ConfigError(
        f"{p} is a directory, not a file ({listing}).\n"
        f"This is Docker's placeholder for a bind mount whose HOST path did not\n"
        f"exist when the stack was deployed. Fix it on the host:\n"
        f"  sudo /opt/lecture-notes-bot/scripts/config.sh install   # create the file\n"
        f"  OR upload config.yaml in Portainer's stack editor,\n"
        f"then redeploy the stack. Check the mounted source with:\n"
        f"  docker inspect lecture-notes-bot --format '{{{{range .Mounts}}}}{{{{.Source}}}} -> "
        f"{{{{.Destination}}}}{{{{\"\\n\"}}}}{{{{end}}}}'"
    )


def load_config(path: Union[str, Path, None] = None) -> Config:
    """Load config.yaml (or the path given). Missing file -> safe defaults."""
    env_path = os.environ.get("LECTURE_BOT_CONFIG")
    candidate = path or env_path or DEFAULT_CONFIG_PATH
    p = Path(candidate)
    data: dict = {}
    if p.is_dir():
        _report_bad_mount(p)
    if p.exists():
        try:
            loaded = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            if not isinstance(loaded, dict):
                raise ConfigError(f"{p} does not contain a YAML mapping")
            data = loaded
        except yaml.YAMLError as exc:
            raise ConfigError(f"invalid YAML in {p}: {exc}") from exc
    data = _expand_env(data)
    for dotted, value in env_bindings().items():
        _set_dotted(data, dotted, value)
    cfg = Config(data, p if p.exists() else None)
    return cfg
