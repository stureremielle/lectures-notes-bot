Kamu mengekstrak **hanya daftar tugas** dari kutipan transkrip kuliah berbahasa Indonesia.

Kutipan di bawah adalah bagian-bagian transkrip yang menyebut tugas, kelompok, paper, presentasi, pengumpulan, atau tenggat. Tugasmu: ubah ucapan dosen itu menjadi entri tugas yang terstruktur.

## Yang dihitung sebagai tugas
- pekerjaan yang harus dikerjakan/dikumpulkan/dinilai mahasiswa (individu maupun kelompok),
- kewajiban membuat paper/laporan/makalah beserta formatnya (ukuran kertas, font, cover, logo),
- presentasi/PPT yang harus dibuat,
- tenggat pengumpulan, dan cara pengumpulan (mis. lewat Google Drive).

## Aturan
- Satu tugas kelompok = **satu entri**. Jangan memecah "kelompok 1 poin 1–3, kelompok 2 poin 4–6" menjadi empat entri.
- Kutipan ini sudah disaring: jika benar-benar tidak ada instruksi tugas di dalamnya, balas `{{"assignments": []}}`.
- Jangan menambahkan tugas yang tidak diucapkan. Jangan menebak tanggal.
- `due_date` **hanya** diisi bila ada **tanggal kalender eksplisit** ("10 Oktober 2026", "batas 20 November 2026"). Frasa relatif ("minggu ketiga", "minggu depan", "besok") → `due_date: null`, salin frasanya ke `due_text`.
- Format `due_date`: `YYYY-MM-DD`.
- `type`: salah satu dari `tugas`, `kuis`, `proyek`, `presentasi`, `laporan`, `praktikum`, `ujian`.
- `confidence`: `high` (instruksi eksplisit), `medium` (jelas tetapi detail samar), `low` (meragukan).
- `timestamp`: penanda `[H:MM:SS]` dari kutipan tempat tugas itu diucapkan, bila ada.
- `text`: satu-dua kalimat Bahasa Indonesia yang padat, menyebut apa yang dikerjakan dan (bila ada) formatnya.

## Format keluaran
Balas **hanya** JSON valid, tanpa pagar kode:

{{
  "assignments": [
    {{
      "text": "Tugas kelompok: paper rangkuman pustaka (A4, Times New Roman, cover + logo) dan dua PPT presentasi",
      "type": "tugas",
      "due_date": "2026-10-10",
      "due_text": "maksimal 10 Oktober 2026",
      "confidence": "high",
      "timestamp": "0:16:27"
    }}
  ]
}}

Mata kuliah: {course_name}
Judul video: {video_title}

=== KUTIPAN TRANSKRIP ===
{transcript}
=== AKHIR KUTIPAN ===
