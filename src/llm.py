"""Minimal OpenAI-compatible ``/chat/completions`` client.

* retries 429 / 5xx / connection errors with exponential backoff;
* tolerant JSON extraction (fenced blocks, prose around the object);
* never raises on a malformed *model* response — the caller decides what to
  do with ``None`` (normally: fail that video, continue the playlist).
"""

from __future__ import annotations

import json
import random
import re
import time
from typing import Any, Optional

from .logging_setup import get_logger

log = get_logger("llm")

RETRY_STATUS = {408, 409, 425, 429, 500, 502, 503, 504, 522, 524}
_FENCE_RE = re.compile(r"```(?:json|JSON)?\s*(.*?)```", re.S)


class LLMError(RuntimeError):
    """Transport-level failure after retries (safe to retry the whole video later)."""


class LLMClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: int = 180,
        max_retries: int = 4,
        temperature: float = 0.2,
        max_tokens: int = 4096,
        extra_body: Optional[dict] = None,
    ):
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        if self.base_url.endswith("/chat/completions"):
            self.base_url = self.base_url[: -len("/chat/completions")]
        self.api_key = api_key or ""
        self.model = model or "gpt-4o-mini"
        self.timeout = timeout
        self.max_retries = max(0, int(max_retries))
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.extra_body = extra_body or {}
        self.last_error: Optional[str] = None

    # -- low level ---------------------------------------------------------
    def _headers(self) -> dict:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def chat(
        self,
        messages: list[dict],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        json_mode: bool = False,
    ) -> str:
        """Send one chat completion and return the assistant text."""
        import requests

        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature if temperature is None else temperature,
            "max_tokens": self.max_tokens if max_tokens is None else max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        payload.update(self.extra_body)

        url = f"{self.base_url}/chat/completions"
        attempt = 0
        while True:
            attempt += 1
            try:
                resp = requests.post(
                    url, headers=self._headers(), json=payload, timeout=self.timeout
                )
            except Exception as exc:  # noqa: BLE001 - requests raises many subclasses
                self.last_error = str(exc)
                if attempt > self.max_retries:
                    raise LLMError(f"LLM request failed after {attempt} attempts: {exc}") from exc
                self._sleep(attempt, f"network error: {exc}")
                continue

            if resp.status_code == 200:
                try:
                    data = resp.json()
                    return data["choices"][0]["message"]["content"] or ""
                except (ValueError, KeyError, IndexError, TypeError) as exc:
                    # A 200 with a broken body: retry once via json_mode fallback,
                    # otherwise surface it as an error.
                    self.last_error = f"unexpected response shape: {exc}"
                    if json_mode and attempt <= 1:
                        json_mode = False
                        continue
                    raise LLMError(self.last_error) from exc

            body = (resp.text or "")[:500]
            self.last_error = f"HTTP {resp.status_code}: {body}"
            if resp.status_code in RETRY_STATUS and attempt <= self.max_retries:
                retry_after = resp.headers.get("Retry-After")
                self._sleep(attempt, f"HTTP {resp.status_code}", retry_after=retry_after)
                continue
            if resp.status_code == 400 and json_mode:
                # Some gateways reject response_format; retry without it.
                json_mode = False
                continue
            raise LLMError(self.last_error)

    def _sleep(self, attempt: int, reason: str, retry_after: Optional[str] = None) -> None:
        delay = min(2 ** attempt + random.random(), 60)
        if retry_after:
            try:
                delay = max(delay, min(float(retry_after), 120))
            except (TypeError, ValueError):
                pass
        log.warning("LLM retry in %.1fs (attempt %d): %s", delay, attempt, reason)
        time.sleep(delay)

    # -- JSON helpers ------------------------------------------------------
    def complete_json(
        self,
        system: str,
        user: str,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> Optional[dict]:
        """One call that should answer with a JSON object. None if unusable."""
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        text = self.chat(messages, temperature=temperature, max_tokens=max_tokens, json_mode=True)
        data = extract_json(text)
        if data is not None:
            return data
        # One repair attempt: ask the model to emit only the JSON.
        log.warning("LLM answered non-JSON; asking once for a JSON-only reply")
        repair_messages = messages + [
            {"role": "assistant", "content": (text or "")[:4000]},
            {
                "role": "user",
                "content": "Balas HANYA dengan objek JSON valid sesuai skema, tanpa teks lain.",
            },
        ]
        try:
            text2 = self.chat(repair_messages, temperature=0.0, max_tokens=max_tokens, json_mode=True)
        except LLMError as exc:
            log.warning("JSON repair call failed: %s", exc)
            return None
        return extract_json(text2)

    def complete_text(
        self,
        system: str,
        user: str,
        max_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> str:
        messages = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        return self.chat(messages, temperature=temperature, max_tokens=max_tokens)


# ---------------------------------------------------------------------------
# Tolerant JSON extraction
# ---------------------------------------------------------------------------
def _balanced_objects(text: str):
    """Yield candidate ``{...}`` substrings using brace balancing (string aware)."""
    depth = 0
    start = -1
    in_str = False
    escape = False
    for i, ch in enumerate(text):
        if in_str:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth:
                depth -= 1
                if depth == 0 and start >= 0:
                    yield text[start : i + 1]
                    start = -1


def _try_loads(candidate: str) -> Optional[dict]:
    for text in (candidate, _fix_common(candidate)):
        try:
            data = json.loads(text)
        except ValueError:
            continue
        if isinstance(data, dict):
            return data
    return None


def _fix_common(text: str) -> str:
    # Trailing commas before } or ]
    text = re.sub(r",\s*([}\]])", r"\1", text)
    # Smart quotes used as JSON delimiters would break json.loads; leave them.
    return text


def extract_json(text: Optional[str]) -> Optional[dict]:
    """Extract the first JSON object from an LLM reply, tolerating fences/prose."""
    if not text:
        return None
    text = text.strip()
    direct = _try_loads(text)
    if direct is not None:
        return direct
    for block in _FENCE_RE.findall(text):
        found = _try_loads(block.strip())
        if found is not None:
            return found
    for candidate in _balanced_objects(text):
        found = _try_loads(candidate)
        if found is not None:
            return found
    return None


def make_client(cfg) -> LLMClient:
    """Build a client from the ``llm`` config section."""
    return LLMClient(
        base_url=cfg.get_str("llm.base_url", "https://api.openai.com/v1"),
        api_key=cfg.get_str("llm.api_key", ""),
        model=cfg.get_str("llm.model", "gpt-4o-mini"),
        timeout=cfg.get_int("llm.timeout", 180),
        max_retries=cfg.get_int("llm.max_retries", 4),
        temperature=float(cfg.get("llm.temperature", 0.2) or 0.2),
        max_tokens=cfg.get_int("llm.max_tokens", 4096),
        extra_body=cfg.get("llm.extra_body", {}) if isinstance(cfg.get("llm.extra_body", {}), dict) else {},
    )
