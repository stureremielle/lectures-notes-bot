"""Compare two chat-completions models on one real lecture transcript.

Usage (key from the environment, nothing hard-coded):

    set OPENAI_BASE_URL, OPENAI_API_KEY
    python tests/_compare_models.py --model-a deepseek-v3.2 --model-b deepseek-v4.1-flash \
        --video ncFgJNgoc1E

Writes a side-by-side Markdown report (timings, token usage, note bodies) so the
decision is made on your own lectures, not on a benchmark.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import notes, transcript  # noqa: E402
from src.config import load_config  # noqa: E402
from src.llm import LLMClient  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-a", required=True)
    ap.add_argument("--model-b", required=True)
    ap.add_argument("--video", default="ncFgJNgoc1E", help="video id or URL")
    ap.add_argument("--out", default="model-compare.md")
    args = ap.parse_args()

    base = os.environ.get("OPENAI_BASE_URL", "https://api.cosmoshub.tech/v1")
    key = os.environ.get("OPENAI_API_KEY", "")
    if not key:
        print("OPENAI_API_KEY is empty", file=sys.stderr)
        return 2

    url = args.video if args.video.startswith("http") else f"https://www.youtube.com/watch?v={args.video}"
    cfg = load_config("config.example.yaml")
    text, meta = transcript.build_transcript(url)
    video = {
        "id": args.video,
        "title": meta.get("title") or "",
        "playlist_title": "PTA 2026/2027",
        "course": "Sistem Penunjang Keputusan",
        "webpage_url": url,
    }
    print(f"transcript: {len(text)} chars, {meta.get('cues')} cues, {meta.get('kind')}/{meta.get('lang')}")

    rows = []
    for model in (args.model_a, args.model_b):
        client = LLMClient(base_url=base, api_key=key, model=model, max_retries=3, max_tokens=8000)
        started = time.time()
        try:
            raw = notes.generate_notes(client, cfg, video, text, meta)
        except Exception as exc:  # noqa: BLE001
            rows.append((model, None, 0.0, client.usage, f"ERROR: {exc}", ""))
            continue
        elapsed = time.time() - started
        note = notes.normalise(raw, video)
        body = notes.render_notes_body(note) if note else "(unusable JSON)"
        rows.append((model, note, elapsed, client.usage, "", body))

    lines = ["# Model comparison", "", f"- transcript: {len(text)} chars, {meta.get('cues')} cues", ""]
    for model, note, elapsed, usage, err, body in rows:
        lines.append(f"## {model}")
        if err:
            lines.append(err)
            lines.append("")
            continue
        lines += [
            f"- time: {elapsed:.1f}s",
            f"- usage: prompt={usage.get('prompt_tokens')} completion={usage.get('completion_tokens')}",
            f"- assignments: {len(note['assignments']) if note else 0} "
            f"(dated: {sum(1 for a in (note or {}).get('assignments', []) if a.get('due_date'))})",
            "",
            "<details><summary>notes</summary>",
            "",
            body,
            "",
            "</details>",
            "",
        ]
    Path(args.out).write_text("\n".join(lines), encoding="utf-8")
    print(f"report: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
