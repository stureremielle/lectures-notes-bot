"""End-to-end smoke test with no network: mock LLM server + fake transcripts.

Runs the real CLI in a subprocess:
    python -m src.main run --playlist URL --limit 1 --dry-run
    python -m src.main watch --all           (first start: baseline, zero LLM)
    python -m src.main remind --dry-run
    python -m src.main calendar-image --dry-run --out <tmp>.png

The yt-dlp calls are stubbed by sitecustomize-style monkeypatching inside a
small driver, so this exercises the real code paths on real JSON/VTT data.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

PLAYLIST_URL = "https://www.youtube.com/playlist?list=PLTEST123456"

MODEL_JSON = {
    "title": "Heap dan Hash Table",
    "language": "en",
    "summary": (
        "Kuliah membahas cara kerja heap sebagai struktur pohon yang menjaga "
        "urutan prioritas, lalu membandingkannya dengan hash table untuk pencarian O(1)."
    ),
    "key_concepts": [
        {"term": "heap", "explanation": "struktur pohon near-complete dengan aturan urutan."},
        {"term": "hash table", "explanation": "tabel dengan fungsi hash dan penanganan collision."},
    ],
    "tags": ["struktur data", "kompleksitas"],
    "assignments": [
        {
            "text": "Implementasikan hash table dengan chaining",
            "type": "tugas",
            "due_date": None,
            "due_text": "minggu depan",
            "confidence": "medium",
            "timestamp": "0:12:30",
        }
    ],
}

DRIVER = textwrap.dedent(
    """
    import sys, json, datetime as dt
    sys.path.insert(0, {root!r})
    import src.transcript as transcript
    import src.playlist as playlist

    VTT = '''WEBVTT
    Kind: captions

    00:00:01.000 --> 00:00:04.000
    Selamat pagi hari ini kita membahas heap

    00:04:00.000 --> 00:04:05.000
    heap adalah struktur pohon

    00:12:30.000 --> 00:12:40.000
    Jadi untuk minggu depan kalian kerjakan implementasi hash table dengan chaining
    '''

    def fake_build_transcript(url, lang=None, with_markers=True):
        return transcript.merge_paragraphs(transcript.parse_vtt(VTT)), {{
            "lang": "en", "kind": "automatic_captions", "title": "Video", "duration": 1500,
            "uploader": "Dosen", "chars": 200, "cues": 3,
        }}

    transcript.build_transcript = fake_build_transcript

    def fake_enumerate(url, limit=None):
        return [
            {{"id": "vid00000001", "title": "Pertemuan 1 - Heap", "url": "https://www.youtube.com/watch?v=vid00000001",
              "webpage_url": "https://www.youtube.com/watch?v=vid00000001", "duration": 1500,
              "uploader": "Dosen", "upload_date": "20260901", "index": 1}},
            {{"id": "vid00000002", "title": "Pertemuan 2 - Hash", "url": "https://www.youtube.com/watch?v=vid00000002",
              "webpage_url": "https://www.youtube.com/watch?v=vid00000002", "duration": 1500,
              "uploader": "Dosen", "upload_date": "20260908", "index": 2}},
        ]

    playlist.enumerate_playlist = fake_enumerate
    import src.main as main
    sys.exit(main.main(sys.argv[1:]))
    """
)


class MockLLM(BaseHTTPRequestHandler):
    calls = 0

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length) or b"{}")
        MockLLM.calls += 1
        assert body.get("model"), "model missing"
        content = "```json\n" + json.dumps(MODEL_JSON) + "\n```"
        payload = {"choices": [{"message": {"role": "assistant", "content": content}}]}
        data = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):  # silence
        pass


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), MockLLM)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        MockLLM.calls = 0
        (self.dir / "config.yaml").write_text(
            textwrap.dedent(
                f"""
                llm:
                  base_url: "http://127.0.0.1:{self.port}/v1"
                  api_key: "test-key"
                  model: "mock-model"
                  max_retries: 1
                discord:
                  notes_webhook: ""
                  calendar_webhook: ""
                reminders:
                  timezone: "Asia/Makassar"
                  days_before: [1, 0]
                  send_time_local: "08:00"
                watch:
                  run_at_local: "19:00"
                  backfill_on_first_run: false
                paths:
                  data_dir: "{self.dir.as_posix()}/data"
                  state_file: "{self.dir.as_posix()}/data/state.json"
                playlists:
                  - name: "Sistem Multimedia"
                    url: "{PLAYLIST_URL}"
                  - name: "Sistem Terdistribusi"
                    url: "https://www.youtube.com/playlist?list=PLTEST999"
                """
            ),
            encoding="utf-8",
        )
        self.driver = self.dir / "driver.py"
        self.driver.write_text(DRIVER.format(root=str(ROOT)), encoding="utf-8")
        self.state_path = self.dir / "data" / "state.json"

    def tearDown(self):
        self.tmp.cleanup()

    def cli(self, *args, timeout=120):
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONPATH"] = str(ROOT)
        # No webhooks, but do not "simulate" posting: --no-discord is added and
        # not treated as a dry run (state must persist as in production).
        return subprocess.run(
            [sys.executable, str(self.driver), "--config", str(self.dir / "config.yaml"), *args],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=timeout,
        )

    def test_dry_run_prints_embed_and_notes(self):
        res = self.cli("run", "--playlist", PLAYLIST_URL, "--limit", "1", "--dry-run")
        out = res.stdout + res.stderr
        self.assertEqual(res.returncode, 0, out)
        self.assertIn("EMBED", out)
        self.assertIn("## Rangkuman", out)
        self.assertIn("## Tugas", out)
        self.assertIn("minggu depan", out)
        self.assertIn("Implementasikan hash table", out)
        self.assertEqual(MockLLM.calls, 1)
        self.assertFalse(self.state_path.exists(), "dry-run must not write state")

    def test_watch_baseline_zero_llm(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps(
                {"videos": {}, "skipped": [], "reminded": [], "baselines": {}, "calendar_last_week": None}
            ),
            encoding="utf-8",
        )
        # A full watch tick would wait for 19:00; drive one scan directly instead.
        code = textwrap.dedent(
            f"""
            import sys
            sys.path.insert(0, {str(ROOT)!r})
            import src.transcript as transcript, src.playlist as playlist
            playlist.enumerate_playlist = lambda url, limit=None: [
                {{"id": "vid00000001", "title": "a", "url": "u1", "webpage_url": "u1"}},
                {{"id": "vid00000002", "title": "b", "url": "u2", "webpage_url": "u2"}},
            ]
            def boom(*a, **k):
                raise AssertionError("LLM must not be called on the first encounter")
            import src.transcript as transcript2
            transcript2.build_transcript = boom
            from src.config import load_config
            from src.logging_setup import setup_logging
            setup_logging("INFO")
            from src.main import Context, scan_playlist
            cfg = load_config({str(self.dir / 'config.yaml')!r})
            ctx = Context(cfg, dry_run=False, no_discord=True)
            ctx.client.complete_json = boom
            res = scan_playlist(ctx, {{"name": "Sistem Multimedia", "url": {PLAYLIST_URL!r}}}, seen=True)
            print("RESULT", res)
            """
        )
        out = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(ROOT)},
        )
        text = out.stdout + out.stderr
        self.assertIn("baseline stamped, skipping all 2 existing videos", text)
        self.assertEqual(MockLLM.calls, 0)
        data = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertIn(PLAYLIST_URL, data["baselines"])
        self.assertEqual(sorted(data["skipped"]), ["vid00000001", "vid00000002"])
        # Second run: still zero LLM calls, nothing new.
        out2 = subprocess.run(
            [sys.executable, "-c", code],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONPATH": str(ROOT)},
        )
        self.assertIn("no new videos", out2.stdout + out2.stderr)
        self.assertEqual(MockLLM.calls, 0)

    def test_force_processes_and_rerun_is_idempotent(self):
        res = self.cli("run", "--playlist", PLAYLIST_URL, "--force", "--limit", "1", "--no-discord")
        out = res.stdout + res.stderr
        self.assertEqual(res.returncode, 0, out)
        self.assertEqual(MockLLM.calls, 1)
        data = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertIn("vid00000001", data["videos"])
        self.assertIn(PLAYLIST_URL, data["baselines"])
        self.assertEqual(len(data["videos"]["vid00000001"]["note"]["assignments"]), 1)
        self.assertIsNone(data["videos"]["vid00000001"]["note"]["assignments"][0]["due_date"])
        # Same video again without --force: never processed twice.
        MockLLM.calls = 0
        res2 = self.cli("run", "--playlist", PLAYLIST_URL, "--no-discord")
        self.assertEqual(MockLLM.calls, 0, res2.stdout + res2.stderr)
        # --force truncated by --limit marks the rest as history, so the daily
        # loop stays incremental: nothing new, nothing posted.
        self.assertIn("no new videos", res2.stdout + res2.stderr)

    def test_remind_dry_run_and_calendar_png(self):
        res = self.cli("run", "--playlist", PLAYLIST_URL, "--force", "--limit", "1", "--no-discord")
        self.assertEqual(res.returncode, 0)
        res = self.cli("remind", "--dry-run")
        out = res.stdout + res.stderr
        self.assertEqual(res.returncode, 0, out)
        self.assertIn("WITA", out)
        res = self.cli("calendar-image", "--dry-run", "--out", str(self.dir / "cal.png"))
        out = res.stdout + res.stderr
        self.assertEqual(res.returncode, 0, out)
        png = self.dir / "cal.png"
        self.assertTrue(png.exists())
        self.assertEqual(png.read_bytes()[:8], b"\x89PNG\r\n\x1a\n")

    def test_list(self):
        res = self.cli("list", "--playlist", PLAYLIST_URL)
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        self.assertIn("vid00000001", res.stdout)


if __name__ == "__main__":
    unittest.main()
