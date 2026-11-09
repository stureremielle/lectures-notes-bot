"""Discord webhook client: text, embeds, files.

Message rules:
* at most 1900 characters per chunk (Discord's hard limit is 2000);
* split on line boundaries, never inside a fenced code block — a fence that is
  open at the split point is closed at the end of the chunk and reopened at the
  start of the next one.
"""

from __future__ import annotations

import json
import mimetypes
import time
from pathlib import Path
from typing import Iterable, Optional

from .logging_setup import get_logger

log = get_logger("discord")

MAX_CHARS = 1900
FENCE = "```"
RETRY_STATUS = {429, 500, 502, 503, 504}


class DiscordError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Fence-safe splitting
# ---------------------------------------------------------------------------
def _split_long_line(line: str, limit: int) -> list[str]:
    parts: list[str] = []
    while len(line) > limit:
        cut = line.rfind(" ", 0, limit)
        if cut <= 0:
            cut = limit
        parts.append(line[:cut])
        line = line[cut:].lstrip()
    if line:
        parts.append(line)
    return parts


def split_message(text: str, limit: int = MAX_CHARS) -> list[str]:
    """Split *text* into chunks of <= limit characters at line boundaries.

    Fenced code blocks are kept balanced: a fence open at a split point is
    closed at the end of the chunk and reopened (with the same language tag) at
    the start of the next chunk.
    """
    text = (text or "").replace("\r\n", "\n").rstrip()
    if not text:
        return []
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    current: list[str] = []
    cur_len = 0
    open_fence: Optional[str] = None  # opening fence line, e.g. "```python"

    def close_len() -> int:
        return len(FENCE) + 1 if open_fence else 0

    def flush() -> None:
        nonlocal current, cur_len
        if not current:
            return
        body = "\n".join(current)
        if open_fence:
            body += "\n" + FENCE
        chunks.append(body)
        current = []
        cur_len = 0
        if open_fence:
            current = [open_fence]
            cur_len = len(open_fence)

    for line in text.split("\n"):
        stripped = line.strip()
        is_fence_line = stripped.startswith(FENCE)
        pieces = [line]
        if not is_fence_line and len(line) + 1 > limit - close_len() - 2:
            pieces = _split_long_line(line, max(20, limit - close_len() - 2))
        # A fence opener starts a block in later chunks too: reopen first.
        if is_fence_line and open_fence is None and stripped.startswith(FENCE):
            is_new_open = stripped.count(FENCE) == 1
        else:
            is_new_open = False

        for piece in pieces:
            if current and cur_len + len(piece) + 1 + close_len() > limit:
                flush()
            if current:
                cur_len += 1 + len(piece)
            else:
                cur_len = len(piece)
            current.append(piece)

        if is_fence_line:
            if is_new_open:
                open_fence = stripped if len(stripped) > 3 else FENCE
            elif open_fence is not None and stripped == FENCE:
                open_fence = None

    if current:
        body = "\n".join(current)
        if body.strip():
            chunks.append(body)

    # Final safety net: no chunk may exceed the hard limit.
    safe: list[str] = []
    for chunk in chunks:
        while len(chunk) > limit:  # pragma: no cover - only for pathological input
            safe.append(chunk[:limit])
            chunk = chunk[limit:]
        if chunk.strip():
            safe.append(chunk)
    return safe


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------
class DiscordWebhook:
    def __init__(
        self,
        url: Optional[str],
        label: str = "webhook",
        dry_run: bool = False,
        timeout: int = 30,
        max_retries: int = 3,
        username: Optional[str] = None,
        avatar_url: Optional[str] = None,
    ):
        self.url = (url or "").strip()
        self.label = label
        self.dry_run = dry_run
        self.timeout = timeout
        self.max_retries = max_retries
        self.username = username
        self.avatar_url = avatar_url

    @property
    def enabled(self) -> bool:
        return bool(self.url) and self.url.startswith("https://")

    # -- transport --------------------------------------------------------
    def _post(self, payload: dict, files: Optional[list[Path]] = None) -> bool:
        if self.dry_run:
            log.info("[dry-run] %s payload:\n%s", self.label, json.dumps(payload, ensure_ascii=False, indent=2))
            return True
        if not self.enabled:
            log.warning("%s: no valid webhook URL configured; message dropped", self.label)
            return False
        import requests

        attempt = 0
        while True:
            attempt += 1
            try:
                if files:
                    data = {"payload_json": json.dumps(payload, ensure_ascii=False)}
                    handles = []
                    file_map = {}
                    try:
                        for i, path in enumerate(files):
                            fh = open(path, "rb")  # noqa: SIM115 - closed below
                            handles.append(fh)
                            mime = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
                            file_map[f"files[{i}]"] = (path.name, fh, mime)
                        resp = requests.post(
                            self.url, data=data, files=file_map, timeout=max(self.timeout, 60)
                        )
                    finally:
                        for fh in handles:
                            fh.close()
                else:
                    resp = requests.post(self.url, json=payload, timeout=self.timeout)
            except Exception as exc:  # noqa: BLE001
                if attempt > self.max_retries:
                    log.error("%s: webhook failed after %d attempts: %s", self.label, attempt, exc)
                    return False
                time.sleep(min(2 ** attempt, 20))
                continue

            if resp.status_code in (200, 204):
                return True
            if resp.status_code == 429 or resp.status_code in RETRY_STATUS:
                if attempt > self.max_retries:
                    log.error("%s: webhook HTTP %s: %s", self.label, resp.status_code, resp.text[:300])
                    return False
                retry_after = 2.0 ** attempt
                try:
                    body = resp.json()
                    retry_after = float(body.get("retry_after", retry_after))
                except (ValueError, TypeError):
                    pass
                log.warning("%s: HTTP %s, retrying in %.1fs", self.label, resp.status_code, retry_after)
                time.sleep(min(retry_after, 60))
                continue
            log.error("%s: webhook HTTP %s: %s", self.label, resp.status_code, resp.text[:500])
            return False

    def _base_payload(self) -> dict:
        payload: dict = {}
        if self.username:
            payload["username"] = self.username
        if self.avatar_url:
            payload["avatar_url"] = self.avatar_url
        return payload

    # -- public API -------------------------------------------------------
    def send_text(self, content: str, allowed_mentions: Optional[dict] = None) -> bool:
        ok = True
        mentions = allowed_mentions if allowed_mentions is not None else {"parse": []}
        for chunk in split_message(content):
            payload = self._base_payload()
            payload["content"] = chunk
            payload["allowed_mentions"] = mentions
            ok = self._post(payload) and ok
        return ok

    def send_embed(self, embed: dict, content: Optional[str] = None) -> bool:
        payload = self._base_payload()
        if content:
            payload["content"] = content[:MAX_CHARS]
        payload["embeds"] = [embed]
        payload["allowed_mentions"] = {"parse": []}
        return self._post(payload)

    def send_files(
        self,
        files: Iterable[Path],
        content: Optional[str] = None,
        embeds: Optional[list[dict]] = None,
    ) -> bool:
        file_list = [Path(f) for f in files]
        payload = self._base_payload()
        if content:
            payload["content"] = content[:MAX_CHARS]
        if embeds:
            payload["embeds"] = embeds
        if file_list:
            # Referencing an attachment from an embed requires the name only.
            payload["attachments"] = [
                {"id": i, "filename": f.name} for i, f in enumerate(file_list)
            ]
        payload["allowed_mentions"] = {"parse": []}
        return self._post(payload, files=file_list or None)

    def send_text_and_embeds(self, content: str, embeds: list[dict]) -> bool:
        """Post text chunks; the embeds ride on the LAST chunk.

        Discord renders an embed underneath its message, so attaching it to the
        first chunk splits a long note in two around the card. Putting it on the
        last chunk keeps the note readable top-to-bottom.
        """
        chunks = split_message(content)
        if not chunks:
            return self.send_embed(embeds[0]) if embeds else True
        ok = True
        last = len(chunks) - 1
        for i, chunk in enumerate(chunks):
            payload = self._base_payload()
            payload["content"] = chunk
            if i == last and embeds:
                payload["embeds"] = embeds[:10]
            payload["allowed_mentions"] = {"parse": []}
            ok = self._post(payload) and ok
        return ok
