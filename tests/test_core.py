"""Fast, network-free tests: python -m unittest discover -s tests -v"""

from __future__ import annotations

import datetime as dt
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src import assignments as A  # noqa: E402
from src import calendar_image, notes, reminders, transcript  # noqa: E402
from src.config import Config, load_config  # noqa: E402
from src.discord_webhook import split_message  # noqa: E402
from src.llm import LLMClient, extract_json  # noqa: E402
from src.state import State  # noqa: E402
from src.timeutil import WITA  # noqa: E402


class TestSplit(unittest.TestCase):
    def test_short(self):
        self.assertEqual(split_message("halo"), ["halo"])

    def test_limit_and_lines(self):
        lines = [f"baris ke-{i} " + "x" * 60 for i in range(200)]
        text = "\n".join(lines)
        chunks = split_message(text)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c), 1900)
        rejoined = "\n".join(chunks)
        for line in lines:
            self.assertIn(line, rejoined)

    def test_fence_balance(self):
        body = "```python\n" + "\n".join(f"print({i})" for i in range(600)) + "\n```"
        text = "Sebelum\n\n" + body + "\n\nSesudah"
        chunks = split_message(text)
        self.assertGreater(len(chunks), 1)
        for c in chunks:
            self.assertLessEqual(len(c), 1900)
            self.assertEqual(c.count("```") % 2, 0, f"unbalanced fence:\n{c[-120:]}")
        self.assertTrue(any("```python" in c for c in chunks[1:]))

    def test_long_single_line(self):
        text = "a" * 5000
        chunks = split_message(text)
        self.assertTrue(all(len(c) <= 1900 for c in chunks))


class TestJson(unittest.TestCase):
    def test_fenced(self):
        self.assertEqual(extract_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_surrounded(self):
        text = 'Berikut hasilnya:\n{"a": {"b": [1,2]}, "c": "x}y"}\nSemoga membantu.'
        self.assertEqual(extract_json(text), {"a": {"b": [1, 2]}, "c": "x}y"})

    def test_garbage(self):
        self.assertIsNone(extract_json("tidak ada json di sini"))

    def test_trailing_comma(self):
        self.assertEqual(extract_json('{"a": 1,}'), {"a": 1})


class TestTranscript(unittest.TestCase):
    VTT = """WEBVTT
Kind: captions
Language: en

00:00:01.000 --> 00:00:03.000
so today we

00:00:03.000 --> 00:00:05.500
we will talk about the heap

00:00:05.500 --> 00:00:07.000
about the heap and hash table
"""

    def test_vtt(self):
        cues = transcript.parse_vtt(self.VTT)
        self.assertEqual(len(cues), 3)
        self.assertAlmostEqual(cues[0][0], 1.0)
        deduped = transcript._dedupe_cues(cues)
        joined = transcript.merge_paragraphs(deduped)
        self.assertIn("[0:00:01]", joined)
        self.assertNotIn("we we will", joined)
        self.assertEqual(joined.count("about the heap"), 1)

    def test_json3(self):
        payload = {
            "events": [
                {"tStartMs": 1500, "segs": [{"utf8": "halo "}, {"utf8": "semua"}]},
                {"tStartMs": 3000, "segs": [{"utf8": "halo semua"}, {"utf8": " kita mulai"}]},
                {"tStartMs": 9000, "segs": [{"utf8": "\n"}]},
            ]
        }
        cues = transcript.parse_json3(json.dumps(payload))
        self.assertEqual(cues[0], (1.5, "halo semua"))
        deduped = transcript._dedupe_cues(cues)
        self.assertEqual(len(deduped), 2)

    def test_ts(self):
        self.assertEqual(transcript.format_ts(3725), "1:02:05")

    def test_rolling_vtt(self):
        vtt = (
            "WEBVTT\nKind: captions\n\n"
            "00:00:01.000 --> 00:00:03.000 align:start position:0%\nso today we\n\n"
            "00:00:03.000 --> 00:00:03.010 align:start position:0%\nso today we\nwe will talk about the heap\n\n"
            "00:00:03.010 --> 00:00:05.500 align:start position:0%\nwe will talk about the heap\nand hash table\n\n"
            "00:00:05.500 --> 00:00:07.000 align:start position:0%\nand hash table\nyeah\n"
        )
        cues = transcript.parse_vtt(vtt)
        self.assertEqual([c[1] for c in cues], ["so today we", "we will talk about the heap", "and hash table", "yeah"])
        merged = transcript.merge_paragraphs(transcript._dedupe_cues(cues))
        self.assertEqual(merged, "[0:00:01] so today we will talk about the heap and hash table yeah")


class TestNotes(unittest.TestCase):
    RAW = {
        "title": "Heap dan Hash Table",
        "language": "en",
        "summary": "Kuliah membahas heap dan hash table.",
        "key_concepts": [
            {"term": "heap", "explanation": "struktur pohon dengan aturan urutan."},
            "hash table",
        ],
        "tags": ["data structure", "heap"],
        "assignments": [
            {
                "text": "Buat implementasi hash table",
                "type": "tugas",
                "due_date": "2026-11-20",
                "due_text": None,
                "confidence": "high",
                "timestamp": "0:10:00",
            },
            {
                "text": "Baca bab berikutnya",
                "type": "membaca",
                "due_date": "minggu depan",
                "due_text": "minggu depan",
                "confidence": "low",
            },
        ]
        + [{"text": f"Tugas tambahan {i}", "type": "tugas"} for i in range(15)],
    }

    def test_normalise(self):
        video = {"id": "abc", "title": "Video", "playlist_title": "Struktur Data"}
        note = notes.normalise(self.RAW, video)
        self.assertEqual(note["language"], "id")
        self.assertLessEqual(len(note["assignments"]), notes.MAX_ASSIGNMENTS)
        first = note["assignments"][0]
        self.assertEqual(first["due_date"], "2026-11-20")
        self.assertEqual(first["type"], "tugas")
        # "minggu depan" must never become a date.
        self.assertIsNone(note["assignments"][1]["due_date"])
        self.assertEqual(note["assignments"][1]["due_text"], "minggu depan")

    def test_two_headers(self):
        note = notes.normalise(self.RAW, {"id": "x", "playlist_title": "C"})
        body = notes.render_notes_body(note)
        self.assertEqual(notes.headers_of(note), ["Rangkuman", "Tugas"])
        self.assertIn("diucapkan secara", body)
        self.assertIn("slide", body)
        self.assertEqual(body.count("## "), 2)

    def test_empty_assignments(self):
        raw = dict(self.RAW, assignments=[])
        note = notes.normalise(raw, {"id": "x"})
        body = notes.render_notes_body(note)
        self.assertIn("Tidak ada tugas", body)
        self.assertEqual(notes.headers_of(note), ["Rangkuman", "Tugas"])

    def test_bad_payload(self):
        self.assertIsNone(notes.normalise(None, {"id": "x"}))
        self.assertIsNone(notes.normalise({"foo": "bar"}, {"id": "x"}))

    def test_condense_split(self):
        text = "\n\n".join("kalimat. " * 200 for _ in range(10))
        chunks = notes.split_for_condense(text, 5000)
        self.assertGreater(len(chunks), 1)
        self.assertTrue(all(len(c) <= 5000 for c in chunks))


def make_item(text, due, course="Sistem Multimedia", atype="tugas", conf="high", key=None):
    return {
        "text": text,
        "due_date": due,
        "due_text": None,
        "type": atype,
        "course": course,
        "confidence": conf,
        "key": key or text.replace(" ", "_"),
        "video_url": "https://youtu.be/x",
    }


class TestAssignments(unittest.TestCase):
    def test_order(self):
        items = [
            make_item("b", "2026-11-20"),
            make_item("a", None),
            make_item("c", "2026-11-10"),
        ]
        ordered = A.sort_assignments(items)
        self.assertEqual([i["text"] for i in ordered], ["c", "b", "a"])

    def test_ics(self):
        items = [make_item("Laporan akhir, bab 1; bab 2", "2026-11-20"), make_item("x", None)]
        ics = A.build_ics(items, now=dt.datetime(2026, 11, 9, 10, 0, tzinfo=WITA))
        self.assertIn("BEGIN:VCALENDAR", ics)
        self.assertEqual(ics.count("BEGIN:VEVENT"), 1)
        self.assertIn("DTSTART;VALUE=DATE:20261120", ics)
        self.assertIn("DTEND;VALUE=DATE:20261121", ics)
        self.assertIn("Laporan akhir\\, bab 1\\; bab 2", ics)
        self.assertNotIn("\n", ics.replace("\r\n", ""))
        for line in ics.split("\r\n"):
            self.assertLessEqual(len(line.encode("utf-8")), 75)

    def test_render_list_indonesian(self):
        text = A.render_list([make_item("Tugas A", "2026-11-20")], today=dt.date(2026, 11, 9))
        self.assertIn("Jumat, 20 November 2026", text)
        self.assertIn("11 hari lagi", text)


class TestTrackPreference(unittest.TestCase):
    def test_manual_beats_automatic(self):
        info = {
            "subtitles": {"de": [{"ext": "vtt", "url": "u-de"}]},
            "automatic_captions": {"de": [{"ext": "json3", "url": "u-de-auto"}]},
        }
        track = transcript._pick_track(info)
        self.assertEqual(track["kind"], "subtitles")
        self.assertEqual(track["url"], "u-de")

    def test_native_beats_translated(self):
        info = {
            "subtitles": {},
            "automatic_captions": {
                "es": [{"ext": "json3", "url": "u-es"}],
                "en": [{"ext": "json3", "url": "u-en?tlang=es"}],
            },
        }
        self.assertEqual(transcript._pick_track(info)["url"], "u-es")

    def test_prefers_english_when_native(self):
        info = {
            "subtitles": {},
            "automatic_captions": {
                "fr": [{"ext": "json3", "url": "u-fr"}],
                "en": [{"ext": "json3", "url": "u-en"}],
                "ja": [{"ext": "json3", "url": "u-ja"}],
            },
        }
        self.assertEqual(transcript._pick_track(info)["lang"], "en")

    def test_no_captions(self):
        self.assertIsNone(transcript._pick_track({"subtitles": {}, "automatic_captions": {}}))


class TestLLMClient(unittest.TestCase):
    def test_retry_then_success(self):
        import threading
        from http.server import BaseHTTPRequestHandler, HTTPServer

        state = {"n": 0}

        class H(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                self.rfile.read(int(self.headers.get("Content-Length", 0)))
                state["n"] += 1
                if state["n"] == 1:
                    self.send_response(429)
                    self.send_header("Retry-After", "0")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                body = json.dumps(
                    {"choices": [{"message": {"content": "```json\n{\"summary\": \"ok\"}\n```"}}]}
                ).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            client = LLMClient(
                base_url=f"http://127.0.0.1:{srv.server_address[1]}/v1",
                api_key="k",
                model="m",
                max_retries=2,
            )
            client._sleep = lambda *a, **k: None
            data = client.complete_json("sys", "user")
            self.assertEqual(data, {"summary": "ok"})
            self.assertEqual(state["n"], 2)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_malformed_never_raises(self):
        from src.llm import LLMClient as C

        c = C(base_url="http://127.0.0.1:1/v1", api_key="k", model="m", max_retries=0)
        c.chat = lambda *a, **k: "maaf, saya tidak bisa"  # type: ignore[assignment]
        self.assertIsNone(c.complete_json("sys", "user"))


class TestCourseResolution(unittest.TestCase):
    """One playlist, many courses: the course must come from the title."""

    NAMES = [
        "Sistem Penunjang Keputusan",
        "Sistem Multimedia",
        "Pengelolaan Proyek Sistem Informasi",
        "Testing dan Implementasi Sistem",
        "Business Intelligence",
        "Sistem Terdistribusi",
        "Analisis Kinerja Sistem",
    ]
    RAW_ALIASES = {
        "Sistem Penunjang Keputusan": "SPK",
        "Sistem Multimedia": "SM",
        "Pengelolaan Proyek Sistem Informasi": "PPSI",
        "Testing dan Implementasi Sistem": "TIS, TDIS",
        "Business Intelligence": "BI",
        "Sistem Terdistribusi": "ST",
        "Analisis Kinerja Sistem": "AKS",
    }

    def setUp(self):
        from src.courses import AliasMap, parse_aliases

        self.am = AliasMap(
            (a, course)
            for course, raw in self.RAW_ALIASES.items()
            for a in parse_aliases(raw)
        )

    def resolve(self, title):
        from src.courses import resolve_course

        return resolve_course({"title": title}, self.am, self.NAMES, "Lainnya")

    def test_codes_in_real_titles(self):
        cases = {
            "M4 TIS 2026 10 29": "Testing dan Implementasi Sistem",
            "TDIS M1 2026 09 30": "Testing dan Implementasi Sistem",
            "PPSI M5\\M3 2026 10 27 11 57 46": "Pengelolaan Proyek Sistem Informasi",
            "SPK M5 2026 10 26 09 29 41": "Sistem Penunjang Keputusan",
            "AKS M2 2026 10 10 14 04 59": "Analisis Kinerja Sistem",
            "M3 SM 2026 10 13 11 15 43": "Sistem Multimedia",
            "ST M1 2026 10 02 14 32 08": "Sistem Terdistribusi",
            "BI M2 2026 10 16 11 01 34": "Business Intelligence",
        }
        for title, expected in cases.items():
            self.assertEqual(self.resolve(title), expected, title)

    def test_no_false_positives(self):
        # "Kelompok 1 ANIMASI" used to resolve to a bogus "Kelompok" course.
        self.assertEqual(self.resolve("KELOMPOK 1 ANIMASI UDP & TCP"), "Lainnya")
        self.assertEqual(self.resolve("2026 10 09 14 43 23"), "Lainnya")
        self.assertEqual(self.resolve(""), "Lainnya")
        self.assertEqual(self.resolve("artist interview about systems"), "Lainnya")

    def test_alias_needs_word_boundary(self):
        self.assertIsNone(self.am.match("M6 TISSUE 2026"))
        self.assertEqual(self.am.match("m6 tis 2026"), "Testing dan Implementasi Sistem")

    def test_readable_title_prefix(self):
        self.assertEqual(
            self.resolve("Sistem Multimedia - Pertemuan 3"), "Sistem Multimedia"
        )

    def test_skip_is_never_a_course(self):
        from src.courses import resolve_course

        self.assertEqual(resolve_course({"title": "Rekaman 1"}, None, [], "Lainnya"), "Lainnya")


class TestSchemaDrift(unittest.TestCase):
    """The model may answer with Indonesian or wrapped keys; never lose the answer."""

    VIDEO = {"id": "v1", "title": "SPK M1", "playlist_title": "PTA"}

    def norm(self, raw):
        return notes.normalise(raw, self.VIDEO)

    def test_indonesian_keys(self):
        n = self.norm(
            {
                "judul": "Heap",
                "ringkasan": "Rangkuman panjang kuliah.",
                "konsep_kunci": [{"term": "heap", "explanation": "struktur pohon"}],
                "tag": ["struktur data"],
                "tugas": [{"text": "Buat laporan", "type": "tugas"}],
            }
        )
        self.assertIsNotNone(n)
        self.assertEqual(n["summary"], "Rangkuman panjang kuliah.")
        self.assertEqual(n["key_concepts"][0]["term"], "heap")
        self.assertEqual(n["tags"], ["struktur data"])
        self.assertEqual(n["assignments"][0]["text"], "Buat laporan")

    def test_wrapped_payload(self):
        n = self.norm({"catatan": {"summary": "S.", "konsep_kunci": ["heap", "hash"]}})
        self.assertIsNotNone(n)
        self.assertEqual(len(n["key_concepts"]), 2)

    def test_single_unknown_wrapper(self):
        n = self.norm({"hasilnya": {"ringkasan": "R.", "konsep_kunci": [{"term": "a"}]}})
        self.assertIsNotNone(n)
        self.assertEqual(n["summary"], "R.")

    def test_list_summary_and_leading_list(self):
        self.assertEqual(self.norm({"summary": ["satu", "dua"]})["summary"], "satu dua")
        self.assertIsNotNone(self.norm([{"summary": "x"}]))

    def test_english_schema_still_works(self):
        n = self.norm(
            {
                "title": "T",
                "summary": "S.",
                "key_concepts": [{"term": "heap", "explanation": "x"}],
                "tags": ["a"],
                "assignments": [],
            }
        )
        self.assertEqual(n["language"], "id")
        self.assertEqual(n["title"], "T")

    def test_unknown_keys_are_reported(self):
        with self.assertLogs("notes", level="WARNING") as cap:
            self.assertIsNone(self.norm({"foo": "bar"}))
        self.assertIn("foo", "\n".join(cap.output))

    def test_dump_unusable(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as d:
            path = notes.dump_unusable(Path(d) / "cache", "v1", {"foo": "bar"})
            self.assertTrue(Path(path).exists())
            self.assertIn("foo", Path(path).read_text(encoding="utf-8"))


class TestAssignmentSecondPass(unittest.TestCase):
    """A summary-style model may fold assignments into prose; pass two recovers them."""

    TRANSCRIPT = "\n\n".join(
        [
            "[0:01:00] Selamat pagi, kita mulai dengan kontrak kuliah.",
            "[0:05:00] Materi minggu pertama membahas manajer dan keputusan.",
            "[0:16:27] Rangkuman dibuat dalam bentuk paper ukuran A4, buat dua PPT.",
            "[0:17:58] Kelompok satu membahas poin satu sampai tiga.",
            "[0:22:00] Manajer adalah pihak yang mengatur sumber daya.",
            "[0:55:05] Sebelum minggu ketiga tugasnya dikumpulkan lewat Google Drive.",
        ]
    )

    def test_context_selection(self):
        ctx = notes.select_assignment_context(self.TRANSCRIPT, max_chars=4000)
        self.assertIn("paper ukuran A4", ctx)
        self.assertIn("Google Drive", ctx)
        self.assertIn("Kelompok satu", ctx)
        self.assertNotIn("kontrak kuliah", ctx)  # unrelated paragraph dropped

    def test_extract_and_merge(self):
        from src.config import Config

        class FakeClient:
            def complete_json(self, system, user, max_tokens=None, temperature=None):
                self.seen = user
                return {
                    "assignments": [
                        {
                            "text": "Tugas kelompok: paper A4 dan dua PPT",
                            "type": "tugas",
                            "due_date": "2026-10-10",
                            "due_text": "maksimal 10 Oktober 2026",
                            "confidence": "high",
                            "timestamp": "0:16:27",
                        }
                    ]
                }

        client = FakeClient()
        cfg = Config({"notes": {"assignment_context_chars": 4000}})
        video = {"id": "v1", "title": "SPK M1", "course": "Sistem Penunjang Keputusan"}
        extra = notes.extract_assignments(client, cfg, video, self.TRANSCRIPT)
        self.assertEqual(len(extra), 1)
        self.assertEqual(extra[0]["due_date"], "2026-10-10")
        self.assertIn("Google Drive", client.seen)

        note = {"assignments": []}
        self.assertEqual(notes.merge_assignments(note, extra), 1)
        # Merging again must not duplicate.
        self.assertEqual(notes.merge_assignments(note, extra), 0)
        self.assertEqual(len(note["assignments"]), 1)

    def test_extract_empty_when_no_cues(self):
        from src.config import Config

        class Boom:
            def complete_json(self, *a, **k):
                raise AssertionError("must not call the LLM without assignment cues")

        cfg = Config({})
        self.assertEqual(
            notes.extract_assignments(Boom(), cfg, {"course": "X"}, "[0:01] halo semuanya"),
            [],
        )


class TestReminderMention(unittest.TestCase):
    """#calendar is reminder-only; the ping must be explicit and allowed."""

    ITEM = {
        "text": "Tugas kelompok: paper A4 + dua PPT",
        "type": "tugas",
        "course": "Sistem Penunjang Keputusan",
        "due_date": "2026-10-10",
        "confidence": "high",
        "key": "k1",
    }

    def test_render_with_mention(self):
        text = A.render_reminder(self.ITEM, 1, dt.date(2026, 10, 9), mention="@everyone")
        self.assertTrue(text.startswith("@everyone"))
        self.assertIn("Tenggat BESOK", text)
        self.assertIn("Sabtu, 10 Oktober 2026", text)
        self.assertIn("sisa 1 hari", text)
        self.assertIn("Sistem Penunjang Keputusan", text)

    def test_no_mention(self):
        text = A.render_reminder(self.ITEM, 1, dt.date(2026, 10, 9))
        self.assertFalse(text.startswith("@"))

    def test_mention_config(self):
        from src.config import Config

        cases = {
            "@everyone": ("@everyone", {"parse": ["everyone"]}),
            "everyone": ("@everyone", {"parse": ["everyone"]}),
            "here": ("@here", {"parse": ["here"]}),
            "role:42": ("<@&42>", {"roles": ["42"]}),
            "none": ("", {"parse": []}),
            "": ("", {"parse": []}),
        }
        for value, (mention, allowed) in cases.items():
            cfg = Config({"reminders": {"mention": value}})
            got = reminders.mention_of(cfg)
            self.assertEqual(got, mention, value)
            self.assertEqual(reminders.allowed_mentions_for(got), allowed, value)


class TestCalendarFlags(unittest.TestCase):
    def test_extras_disabled_by_default(self):
        from src.config import load_config

        cfg = load_config(Path(__file__).resolve().parent.parent / "config.example.yaml")
        self.assertFalse(cfg.get_bool("calendar.image.enabled", True))
        self.assertFalse(cfg.get_bool("calendar.post_assignment_lists", True))
        self.assertFalse(cfg.get_bool("calendar.post_ics", True))
        self.assertEqual(cfg.reminder_days_before(), [1])


class TestRelativeDueDates(unittest.TestCase):
    """'minggu depan' spoken at a Monday lecture means the following Monday."""

    MONDAY = dt.date(2026, 9, 28)  # SPK M1

    def test_phrases(self):
        from src.relativedates import resolve_relative_date

        cases = {
            "minggu depan": dt.date(2026, 10, 5),
            "minggu depan kalian kerjakan": dt.date(2026, 10, 5),
            "dua minggu lagi": dt.date(2026, 10, 12),
            "2 minggu ke depan": dt.date(2026, 10, 12),
            "tiga pekan kemudian": dt.date(2026, 10, 19),
            "besok": dt.date(2026, 9, 29),
            "lusa": dt.date(2026, 9, 30),
            "senin depan": dt.date(2026, 10, 5),
            "akhir bulan": dt.date(2026, 9, 30),
        }
        for phrase, expected in cases.items():
            got, rule = resolve_relative_date(phrase, self.MONDAY)
            self.assertEqual(got, expected, f"{phrase} -> {got} ({rule})")

    def test_ambiguous_phrases_stay_null(self):
        from src.relativedates import resolve_relative_date

        for phrase in ("minggu ketiga", "minggu ke-3", "minggu ini", "akhir minggu ini", ""):
            got, _ = resolve_relative_date(phrase, self.MONDAY)
            self.assertIsNone(got, phrase)

    def test_weekday_said_on_that_day_is_next_week(self):
        from src.relativedates import resolve_relative_date

        got, _ = resolve_relative_date("hari senin", self.MONDAY)
        self.assertEqual(got, dt.date(2026, 10, 5))

    def test_notes_mark_the_date_as_inferred(self):
        from src.config import Config

        cfg = Config({"assignments": {"resolve_relative_dates": True}})
        video = {"id": "v", "title": "SPK M1 2026 09 28", "upload_date": "20260928"}
        raw = {
            "summary": "S.",
            "assignments": [
                {"text": "Tugas kelompok", "due_date": None, "due_text": "minggu depan", "confidence": "high"}
            ],
        }
        note = notes.normalise(raw, video, cfg)
        item = note["assignments"][0]
        self.assertEqual(item["due_date"], "2026-10-05")
        self.assertTrue(item["due_date_inferred"])
        body = notes.render_notes_body(note)
        self.assertIn("5 Oktober 2026", body)
        self.assertIn("ditafsirkan", body)

    def test_can_be_disabled(self):
        from src.config import Config

        cfg = Config({"assignments": {"resolve_relative_dates": False}})
        video = {"id": "v", "title": "SPK M1 2026 09 28", "upload_date": "20260928"}
        raw = {"summary": "S.", "assignments": [{"text": "Tugas", "due_text": "minggu depan"}]}
        note = notes.normalise(raw, video, cfg)
        self.assertIsNone(note["assignments"][0]["due_date"])

    def test_no_anchor_no_date(self):
        from src.config import Config

        cfg = Config({"assignments": {"resolve_relative_dates": True}})
        raw = {"summary": "S.", "assignments": [{"text": "Tugas", "due_text": "minggu depan"}]}
        note = notes.normalise(raw, {"id": "v", "title": "tanpa tanggal"}, cfg)
        self.assertIsNone(note["assignments"][0]["due_date"])


class TestScheduleAnchoring(unittest.TestCase):
    """The weekly schedule fixes deadlines when a lecture is uploaded late."""

    MONDAY = dt.date(2026, 9, 28)
    TUESDAY = dt.date(2026, 9, 29)

    def test_parse_weekday(self):
        from src.relativedates import parse_weekday

        self.assertEqual(parse_weekday("Senin"), 0)
        self.assertEqual(parse_weekday("rabu"), 2)
        self.assertEqual(parse_weekday("Jumat"), 4)
        self.assertEqual(parse_weekday("Minggu"), 6)  # schedule context = Sunday
        self.assertEqual(parse_weekday("1"), 0)
        self.assertIsNone(parse_weekday("someday"))

    def test_schedule_from_example_config(self):
        from src.config import load_config

        cfg = load_config(Path(__file__).resolve().parent.parent / "config.example.yaml")
        self.assertEqual(cfg.class_weekday("Sistem Penunjang Keputusan"), 0)  # Senin
        self.assertEqual(cfg.class_weekday("Testing dan Implementasi Sistem"), 2)  # Rabu
        self.assertEqual(cfg.class_weekday("Analisis Kinerja Sistem"), 5)  # Sabtu
        self.assertIsNone(cfg.class_weekday("Kuliah Tidak Dikenal"))

    def test_next_weekday_after(self):
        from src.relativedates import next_weekday_after

        self.assertEqual(next_weekday_after(self.MONDAY, 0), dt.date(2026, 10, 5))
        self.assertEqual(next_weekday_after(self.TUESDAY, 0), dt.date(2026, 10, 5))
        self.assertEqual(next_weekday_after(self.MONDAY, 2), dt.date(2026, 9, 30))

    def test_late_upload_keeps_the_class_day(self):
        from src.relativedates import resolve_relative_date

        monday = resolve_relative_date("minggu depan", self.MONDAY, 0)[0]
        late = resolve_relative_date("minggu depan", self.TUESDAY, 0)[0]
        self.assertEqual(monday, dt.date(2026, 10, 5))
        self.assertEqual(late, dt.date(2026, 10, 5))  # same Monday, not Tuesday
        self.assertEqual(late.weekday(), 0)

    def test_two_weeks_with_schedule(self):
        from src.relativedates import resolve_relative_date

        got = resolve_relative_date("dua minggu lagi", self.TUESDAY, 0)[0]
        self.assertEqual(got, dt.date(2026, 10, 12))
        self.assertEqual(got.weekday(), 0)

    def test_notes_use_the_course_weekday(self):
        from src.config import Config

        cfg = Config(
            {
                "assignments": {"resolve_relative_dates": True},
                "schedule": {"Sistem Penunjang Keputusan": ["Senin"]},
            }
        )
        # Lecture recorded on a Tuesday, but SPK meets on Monday.
        video = {
            "id": "v",
            "title": "SPK M1 2026 09 29",
            "upload_date": "20260929",
            "course": "Sistem Penunjang Keputusan",
        }
        raw = {"summary": "S.", "assignments": [{"text": "Tugas", "due_text": "minggu depan"}]}
        note = notes.normalise(raw, video, cfg)
        due = dt.date.fromisoformat(note["assignments"][0]["due_date"])
        self.assertEqual(due, dt.date(2026, 10, 5))
        self.assertEqual(due.weekday(), 0)


class TestReminderWindow(unittest.TestCase):
    """Rolling 7-day window: every evening while due within a week; past = done."""

    def setUp(self):
        import tempfile

        self.tmp = tempfile.TemporaryDirectory()
        self.state = State(Path(self.tmp.name) / "state.json")

    def tearDown(self):
        self.tmp.cleanup()

    @staticmethod
    def item(text, due):
        return {
            "text": text,
            "due_date": due,
            "type": "tugas",
            "course": "SPK",
            "confidence": "high",
            "key": text,
        }

    def items(self):
        return [
            self.item("lewat 3 hari", "2026-11-07"),
            self.item("hari ini", "2026-11-10"),
            self.item("besok", "2026-11-11"),
            self.item("3 hari", "2026-11-13"),
            self.item("7 hari", "2026-11-17"),
            self.item("8 hari", "2026-11-18"),
            self.item("tanpa tanggal", None),
        ]

    def at(self, day, hour=19):
        return dt.datetime(2026, 11, day, hour, 0, tzinfo=WITA)

    def test_window_contents(self):
        cands, diag = reminders.pending(
            self.items(), self.state, now=self.at(10), send_time=dt.time(8, 0), window_days=7
        )
        texts = [c[0]["text"] for c in cands]
        self.assertIn("hari ini", texts)
        self.assertIn("besok", texts)
        self.assertIn("7 hari", texts)
        self.assertNotIn("8 hari", texts)      # outside the window
        self.assertNotIn("lewat 3 hari", texts)  # passed = assumed done
        self.assertEqual(diag["undated"], 1)
        # most urgent first
        self.assertEqual([c[1] for c in cands], sorted(c[1] for c in cands))

    def test_fires_every_day_until_due(self):
        # Day 10: "3 hari" (13 Nov) is D-3, so it fires.
        c1, _ = reminders.pending(
            self.items(), self.state, now=self.at(10), send_time=dt.time(8, 0), window_days=7
        )
        self.assertIn("3 hari", [c[0]["text"] for c in c1])
        for item, off in c1:
            self.state.mark_reminded(reminders.reminder_key(item, off))
        # Same day again -> nothing (deduped).
        c2, diag2 = reminders.pending(
            self.items(), self.state, now=self.at(10), send_time=dt.time(8, 0), window_days=7
        )
        self.assertEqual(c2, [])
        self.assertGreater(diag2["already_sent"], 0)
        # Next day it fires again, with a smaller remaining-days count.
        c3, _ = reminders.pending(
            self.items(), self.state, now=self.at(11), send_time=dt.time(8, 0), window_days=7
        )
        again = dict((c[0]["text"], c[1]) for c in c3)
        self.assertEqual(again.get("3 hari"), 2)

    def test_before_send_time_sends_nothing(self):
        cands, diag = reminders.pending(
            self.items(), self.state, now=self.at(10, hour=6), send_time=dt.time(8, 0), window_days=7
        )
        self.assertEqual(cands, [])
        self.assertTrue(diag["before_send_time"])

    def test_exact_offsets_still_available(self):
        cands, _ = reminders.pending(
            self.items(), self.state, now=self.at(10), send_time=dt.time(8, 0), days_before=[1]
        )
        self.assertEqual([c[0]["text"] for c in cands], ["besok"])


class TestReminders(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.state = State(Path(self.tmp.name) / "state.json")

    def tearDown(self):
        self.tmp.cleanup()

    def test_offsets_and_dedupe(self):
        now = dt.datetime(2026, 11, 9, 9, 0, tzinfo=WITA)
        items = [
            make_item("besok", "2026-11-10"),
            make_item("hari ini", "2026-11-09"),
            make_item("jauh", "2026-11-30"),
            make_item("tanpa", None),
        ]
        cands, diag = reminders.pending(items, self.state, now=now, days_before=[1, 0], send_time=dt.time(8, 0))
        self.assertEqual(len(cands), 2)
        self.assertEqual(diag["undated"], 1)
        key = reminders.reminder_key(cands[0][0], cands[0][1])
        self.state.mark_reminded(key)
        cands2, diag2 = reminders.pending(items, self.state, now=now, days_before=[1, 0], send_time=dt.time(8, 0))
        self.assertEqual(len(cands2), 1)
        self.assertEqual(diag2["already_sent"], 1)

    def test_before_send_time(self):
        now = dt.datetime(2026, 11, 9, 6, 0, tzinfo=WITA)
        cands, diag = reminders.pending(
            [make_item("hari ini", "2026-11-09")], self.state, now=now, days_before=[1, 0], send_time=dt.time(8, 0)
        )
        self.assertEqual(cands, [])
        self.assertTrue(diag["before_send_time"])


class TestState(unittest.TestCase):
    def test_roundtrip_and_corrupt(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / "state.json"
            st = State(path).load()
            st.stamp_baseline("https://www.youtube.com/playlist?list=X")
            st.mark_skipped(["a", "b"])
            st.add_video("v1", {"id": "v1", "note": {}})
            st.mark_reminded("k@T1")
            st.set_calendar_week("2026-W45")
            st.save()
            st2 = State(path).load()
            self.assertTrue(st2.has_baseline("https://www.youtube.com/playlist?list=X"))
            self.assertEqual(st2.data["skipped"], ["a", "b"])
            self.assertTrue(st2.has_video("v1"))
            self.assertTrue(st2.is_reminded("k@T1"))
            self.assertEqual(st2.calendar_week(), "2026-W45")
            path.write_text("{not json", encoding="utf-8")
            st3 = State(path).load()
            self.assertEqual(st3.data["videos"], {})
            self.assertEqual(list(Path(d).glob("state.json.corrupt-*")), list(Path(d).glob("state.json.corrupt-*")))
            self.assertTrue(list(Path(d).glob("state.json.corrupt-*")))


class TestConfig(unittest.TestCase):
    def test_env_expansion_and_dotted(self):
        os.environ["TEST_BOT_KEY"] = "secret"
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "c.yaml"
            p.write_text(
                "llm:\n  api_key: ${TEST_BOT_KEY}\n  model: ${NOPE_MISSING:-gpt-x}\n"
                "watch:\n  run_at_local: ['19:00', '07:00']\n",
                encoding="utf-8",
            )
            cfg = load_config(p)
            self.assertEqual(cfg.get_str("llm.api_key"), "secret")
            self.assertEqual(cfg.get_str("llm.model"), "gpt-x")
            self.assertEqual(cfg.watch_times(), ["07:00", "19:00"])
            self.assertTrue(isinstance(cfg.section("llm"), Config) or hasattr(cfg.section("llm"), "get"))

    def test_example_loads(self):
        cfg = load_config(Path(__file__).resolve().parent.parent / "config.example.yaml")
        playlists = cfg.playlists()
        self.assertEqual(len(playlists), 1)
        self.assertEqual(playlists[0]["name"], "PTA 2026/2027")
        self.assertIn("list=", playlists[0]["url"])
        aliases = playlists[0]["course_aliases"]
        self.assertIsInstance(aliases, dict)
        self.assertIn("TIS", aliases["Testing dan Implementasi Sistem"])
        self.assertIn("SPK", aliases["Sistem Penunjang Keputusan"])
        self.assertEqual(cfg.reminder_days_before(), [1])  # #calendar = "besok ada tenggat"
        self.assertEqual(cfg.get_str("pipeline.course_fallback"), "Lainnya")

    def test_aliases_map_to_courses_not_playlist_name(self):
        from src.courses import AliasMap, resolve_course

        cfg = Config(
            {
                "playlists": [
                    {
                        "name": "PTA 2026/2027",
                        "url": "u",
                        "course_aliases": {
                            "Sistem Penunjang Keputusan": ["SPK"],
                            "Testing dan Implementasi Sistem": ["TIS", "TDIS"],
                        },
                    }
                ]
            }
        )
        am = AliasMap.from_config(cfg)
        names = [p["name"] for p in cfg.playlists()]
        self.assertEqual(
            resolve_course({"title": "SPK M1 2026 09 28"}, am, names, "Lainnya"),
            "Sistem Penunjang Keputusan",
        )
        self.assertEqual(
            resolve_course({"title": "TDIS M1 2026 09 30"}, am, names, "Lainnya"),
            "Testing dan Implementasi Sistem",
        )

    def test_send_time_warning_only_when_no_tick_can_send(self):
        base = {"llm": {"api_key": "x"},
                "discord": {"notes_webhook": "https://x", "calendar_webhook": "https://y"}}
        # Later than every tick -> never fires -> warn
        bad = Config({**base, "reminders": {"send_time_local": "20:00"},
                      "watch": {"run_at_local": "19:00"}})
        self.assertTrue(any("never fire" in w for w in bad.validate_for_run()))
        # Several ticks, send_time between them -> fine, and it names the tick
        ok = Config({**base, "reminders": {"send_time_local": "14:00"},
                     "watch": {"run_at_local": ["07:00", "13:00", "19:00"]}})
        self.assertEqual(ok.validate_for_run(), [])
        self.assertEqual(ok.send_tick(), "19:00")
        early = Config({**base, "reminders": {"send_time_local": "08:00"},
                        "watch": {"run_at_local": ["07:00", "13:00", "19:00"]}})
        self.assertEqual(early.send_tick(), "13:00")


class TestCalendar(unittest.TestCase):
    def test_grid_monday_first(self):
        weeks = calendar_image.month_grid(2026, 11)
        self.assertEqual(weeks[0][0], None)  # 1 Nov 2026 is a Sunday
        self.assertEqual(weeks[0][6], 1)
        self.assertEqual(len(weeks), 6)

    def test_render_png(self):
        items = [make_item(f"Tugas {i}", f"2026-11-{10 + i:02d}") for i in range(4)]
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "cal.png"
            path = calendar_image.render_month(items, 2026, 11, today=dt.date(2026, 11, 9), out_path=out)
            self.assertTrue(path.exists())
            with open(path, "rb") as fh:
                head = fh.read(8)
            self.assertEqual(head, b"\x89PNG\r\n\x1a\n")
            from PIL import Image

            with Image.open(path) as img:
                self.assertEqual(img.size, (900, 1020))


class TestPrompts(unittest.TestCase):
    def test_system_prompt_constraints(self):
        from src import prompts

        system = prompts.load_prompt("notes_system")
        self.assertIn("tidak jelas di transkrip", system)
        self.assertIn("JANGAN PERNAH mengarang isi visual", system)
        self.assertIn("minggu depan", system)  # null date rule
        self.assertIn("10", system)  # cap

    def test_render(self):
        from src import prompts

        user = prompts.render(
            "notes_user",
            course_name="Sistem Multimedia",
            video_title="Pertemuan 1",
            video_date="Senin, 1 September 2026",
            duration="25 menit",
            caption_kind="caption otomatis",
            caption_lang="id",
            transcript="[0:00:01] halo semua",
        )
        self.assertIn("Sistem Multimedia", user)
        self.assertIn("[0:00:01] halo semua", user)
        self.assertNotIn("{transcript}", user)
        cond = prompts.render("condense", chunk_index=1, chunk_total=2, chunk="teks")
        self.assertIn("bagian 1 dari 2", cond.lower())


class TestPlaylistOrder(unittest.TestCase):
    def test_oldest_first_ignores_playlist_order(self):
        from src.playlist import oldest_first

        videos = [
            {"id": "new", "upload_date": "20261109", "index": 1},
            {"id": "old", "upload_date": "20260928", "index": 2},
            {"id": "mid", "upload_date": "20261019", "index": 3},
            {"id": "undated", "index": 4},
        ]
        self.assertEqual([v["id"] for v in oldest_first(videos)], ["old", "mid", "new", "undated"])


class TestWatchSchedule(unittest.TestCase):
    def test_next_tick_is_after_last_end(self):
        from src.main import next_tick

        tz = WITA
        last_end = dt.datetime(2026, 11, 9, 19, 0, 7, tzinfo=tz)  # run ended 7s after the tick
        nxt = next_tick(["19:00"], last_end, tz)
        self.assertEqual(nxt, dt.datetime(2026, 11, 10, 19, 0, tzinfo=tz))
        nxt2 = next_tick(["07:00", "19:00"], last_end, tz)
        self.assertEqual(nxt2, dt.datetime(2026, 11, 10, 7, 0, tzinfo=tz))
        # A run that ends early still waits for the next slot.
        nxt3 = next_tick(["07:00", "19:00"], dt.datetime(2026, 11, 9, 7, 3, tzinfo=tz), tz)
        self.assertEqual(nxt3, dt.datetime(2026, 11, 9, 19, 0, tzinfo=tz))


if __name__ == "__main__":
    unittest.main()
