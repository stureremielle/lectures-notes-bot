# lectures-notes-bot

Menonton playlist kuliah YouTube → mengambil **caption** tiap video (tanpa
mengunduh video/audio) → menyusun **rangkuman Bahasa Indonesia** + **daftar
tugas yang diucapkan dosen** lewat LLM apa pun yang kompatibel OpenAI →
mem-posting ke **Discord**. Sekaligus mengirim **PNG kalender mingguan**, daftar
tugas, berkas `.ics`, dan **pengingat T-1 / T-0**.

* **Tanpa ffmpeg, tanpa unduhan video/audio** — hanya caption.
* **Audio saja.** Model tidak pernah melihat slide atau teks di layar.
* **Zona waktu WITA** (`Asia/Makassar`) untuk semua perhitungan tanggal.
* Dijalankan sebagai stack Portainer di TV box (Amlogic S905X, aarch64, 2 GB RAM).
  Container **tidak membuka port**; semua trafik keluar.

---

## Memperbarui bot (workflow yang dipakai)

Kode hidup di GitHub, image dibangun **di box** — tidak ada registry, tidak ada
token di box. Folder kerja di box: `/opt/lecture-notes-bot` (clone repo).

```bash
ssh box
cd /opt/lecture-notes-bot
sudo ./scripts/update.sh      # git pull --ff-only + docker build -t lecture-notes-bot:latest .
sudo ./scripts/restart.sh     # docker compose up -d --force-recreate (atau "Update the stack" di Portainer)
```

Yang **tidak** pernah hilang saat update: `state.json`, PNG kalender, `.ics`, dan
`config.yaml` — state ada di named volume `lecture-notes-data`, config di file
milik stack. `update.sh` menampilkan ringkasan state sebelum dan sesudah build.

**Ubah konfigurasi (URL playlist, jadwal) tidak butuh update maupun restart:**
`watch` membaca ulang `config.yaml` setiap tick.

| Tugas | Perintah |
|---|---|
| Build pertama / setelah ubuntu upgrade | `sudo ./scripts/build.sh` (tambah `--no-cache` bila perlu) |
| Update kode | `sudo ./scripts/update.sh` lalu `sudo ./scripts/restart.sh` |
| Lihat file config yang dipakai container | `sudo ./scripts/config.sh path` |
| Buat config.yaml kalau belum ada | `sudo ./scripts/config.sh install` |
| Edit config.yaml | `sudo ./scripts/config.sh edit` |
| Cek semua playlist punya URL | `sudo ./scripts/config.sh check` |
| Jalankan CLI di container (state yang sama) | `sudo ./scripts/bot.sh run --playlist "<URL>" --force --limit 1` |
| Recreate container tanpa Portainer | `./scripts/recreate.sh` (memakai ulang env dari container lama) |
| Ringkasan state | `sudo ./scripts/bot.sh state` |
| Log | `docker logs --tail 50 -f lecture-notes-bot` |

### Pasang box baru dari nol

```bash
git clone https://github.com/stureremielle/lectures-notes-bot.git /tmp/lnb
sudo /tmp/lnb/scripts/setup-box.sh          # clone ke /opt/lecture-notes-bot, build, siapkan config
```

lalu ikuti langkah Portainer yang dicetak skrip itu (stack **Repository**,
`refs/heads/main`, compose path `docker-compose.portainer.yml`).

### Diagnosa cepat: `scripts/doctor.sh`

Portainer tidak menampilkan status apa pun setelah *Update the stack*, jadi cek
dari terminal:

```bash
sudo /opt/lecture-notes-bot/scripts/doctor.sh
```

Keluarannya baris `[ OK ]` / `[WARN]` / `[FAIL]` untuk: container & restart
count, umur image vs container, path bind mount `config.yaml` (file / direktori /
hilang), lima secret di dalam container, config yang benar-benar dibaca
aplikasi, ringkasan state di volume, dan 12 baris log terakhir. Exit code 1 bila
ada `[FAIL]`; tambahkan `--logs` untuk mengikuti log setelahnya.

`sudo scripts/config.sh status` hanya menampilkan path config yang dipakai
container beserta isi path tersebut (file / direktori / tidak ada).

### Soal file `config.yaml` di stack

Stack meng-bind-mount `/opt/lecture-notes-bot/config.yaml` (folder clone repo).
File itu **harus sudah ada sebelum stack di-deploy**: kalau source bind mount
tidak ada, Docker membuat **direktori kosong** dan container gagal dengan
`IsADirectoryError: Is a directory: '/app/config.yaml'`.

```bash
sudo /opt/lecture-notes-bot/scripts/config.sh install   # buat dari config.example.yaml
```

`config.yaml` masuk `.gitignore`, jadi `git pull` tidak pernah menimpanya.
Kalau terlanjur ada direktori di path itu, hapus dulu lalu Deploy/Update stack:

```bash
docker inspect lecture-notes-bot --format '{{range .Mounts}}{{.Source}} -> {{.Destination}}{{"\n"}}{{end}}'
docker rm -f lecture-notes-bot
```

**Jangan** menjalankan `docker compose up -d` manual dari folder clone: compose
akan mensubstitusi `${OPENAI_API_KEY}` dsb. menjadi string kosong, karena
variabel itu diberikan Portainer ke *container*, bukan ke shell Anda — bot akan
hidup tanpa secret. Pakai `scripts/restart.sh` (docker restart, env utuh) atau
tombol **Update the stack** di Portainer.

### Kenapa image di-build di box, bukan di GitHub Actions

* box ini hanya menarik kode publik, tanpa token registry, tanpa package GHCR;
* `docker build` native di aarch64 cepat (base `python:3.12-slim` + Pillow + yt-dlp);
* kalau nanti mau CI: job `docker buildx --platform linux/arm64` yang push ke
  GHCR bisa ditambahkan dan baris `image:` diubah ke `ghcr.io/…`.

---


### Channel `#notes`

1. **Embed** per kuliah: judul, mata kuliah, jumlah tugas, tag, potongan rangkuman.
2. **Pesan catatan** (dipecah otomatis bila > 1900 karakter) yang isinya **tepat
   dua header**:

```markdown
# 📚 Heap dan Hash Table
**Sistem Multimedia** · [tonton](https://youtu.be/…)

## Rangkuman

Kuliah membahas cara kerja heap sebagai struktur pohon near-complete …

**Konsep kunci**

- **heap** — struktur pohon yang menjaga urutan prioritas.
- **hash table** — tabel fungsi hash dengan penanganan collision.

## Tugas

- Implementasikan hash table dengan chaining
  _jenis: tugas · tenggat: minggu depan (belum dipastikan) · diucapkan pada 0:12:30_

_Catatan: hanya tugas yang **diucapkan secara lisan** dalam kuliah yang terdeteksi.
Berkas, slide, dan pengumuman tertulis tidak dibaca — periksa kembali sumber resmi dosen._
```

Tidak ada header lain: tidak ada tabel definisi, tidak ada bagian rumus, tidak
ada soal review.

### Channel `#calendar`

Default: **pengingat saja** — satu pesan berisi ping saat ada tenggat **besok**
(T-1), lengkap dengan mata kuliah, tanggal, dan menit transkrip sumbernya.

```yaml
reminders:
  days_before: [1]        # hanya sehari sebelum
  mention: "@everyone"    # "", "@here", atau "role:<id>" bila tak ingin ping
calendar:
  post_assignment_lists: false   # daftar tugas per kuliah
  post_ics: false                # lampiran .ics
  image:
    enabled: false               # PNG bulanan (statis, bukan kalender hidup)
```

Yang bisa dinyalakan kembali kapan saja:

* `post_assignment_lists: true` — daftar tugas per kuliah saat catatan diproses,
* `post_ics: true` — lampiran `.ics` untuk diimpor ke kalender pribadi,
* `image.enabled: true` — PNG grid bulanan 900×1020, **sekali per minggu ISO**
  pada `image.weekday`. Ini gambar statis: hanya diposting seminggu sekali dan
  tidak berubah sampai minggu berikutnya, jadi tidak bisa dijadikan kalender
  hidup. Kalau butuh yang selalu terbaru, pakai `.ics` (diimpor ke Google
  Calendar, ikut berubah saat tugas baru masuk) atau tombol
  `calendar-image` untuk membuat PNG kapan saja.

> Catatan: dengan satu kali tick 19:00, pengingat T-1 keluar **19:00 sehari
> sebelumnya** (≈29 jam sebelum tenggat 23.59 WITA).

---

## Aturan yang tidak bisa dilanggar

| Aturan | Implementasi |
|---|---|
| Output selalu Bahasa Indonesia | `notes.language: "id"` dipaksa, bukan auto-detect |
| Istilah teknis tetap Inggris | heap, hash table, big-O, deadlock, thread, pointer … (boleh diberi padanan) |
| Tidak mengarang isi visual | `prompts/notes_system.md` melarangnya; hal tak jelas ditandai `(tidak jelas di transkrip)` |
| Konservatif soal tugas | maksimal 10 per kuliah; ragu → hilangkan atau `confidence: low` |
| Tidak pernah mengarang tanggal | "minggu depan" → `due_date: null`, teks aslinya masuk `due_text` |
| WITA, bukan WIB | `ZoneInfo("Asia/Makassar")`, semua date math lewat `reminders.timezone` |
| Pesan ≤ 1900 karakter | `discord_webhook.split_message`, potong di batas baris, balok kode ditutup & dibuka ulang |
| LLM tidak boleh mematikan proses | retry 429/5xx + backoff, JSON toleran, video gagal → lanjut |
| State aman | `data/state.json`, tulis atomik (`tmp` + `os.replace`), file rusak → peringatan + mulai baru |

---

## Jadwal harian (bukan sleep loop)

```yaml
watch:
  run_at_local: "19:00"    # atau list: ["07:00", "19:00"]
reminders:
  timezone: "Asia/Makassar"
  send_time_local: "08:00"
```

* `watch` langsung menjalankan satu tick saat start, lalu tidur sampai tick
  berikutnya.
* Tick berikutnya dihitung dari **akhir run sebelumnya** (bukan dari "sekarang"),
  jadi jitter bangunnya container tidak menumpuk.
* Urutan setiap tick: **pengingat → gambar kalender → scan playlist**.
* Tidak ada flag `--interval`.

### ⚠️ `send_time_local` harus lebih awal dari `run_at_local` pertama

Pengingat hanya dikirim bila waktu sekarang ≥ `send_time_local`. Kalau
`send_time_local` lebih lambat/lebih awal dari `run_at_local` pertama, pengingat
akan terlambat. Dengan satu tick 19:00, `send_time_local` **wajib** 08:00 (atau
lebih awal). Konfigurasi ini juga diberi komentar di `config.example.yaml`.

### T-0 dengan satu tick = baru ±5 jam sebelum tenggat

Tenggat dihitung pukul **23.59 WITA**. Dengan satu tick 19:00, pengingat T-0
baru keluar sekitar 5 jam sebelum tenggat. Kalau ingin pemberitahuan lebih awal,
pakai dua tick:

```yaml
watch:
  run_at_local: ["07:00", "19:00"]
```

Hampir tanpa biaya: tick kedua hanya memindai ulang playlist, dan hanya upload
baru yang dikirim ke LLM.

---

## Tanpa backfill riwayat

* **Pertemuan pertama** dengan sebuah URL playlist: playlist dibaca, baseline
  dicatat di `state.baselines[url]`, semua video yang ada sekarang dimasukkan ke
  `state.skipped`, lalu keluar. **Nol panggilan LLM.** Log-nya:
  `baseline stamped, skipping all N existing videos`.
* **Run berikutnya** hanya memproses video yang ID-nya tidak ada di `videos`
  maupun `skipped`.
* `watch.backfill_on_first_run: true` → video lama diproses sekali saat
  pertemuan pertama, bukan dilewati.
* `run --force` memproses ulang semuanya **dan** menstempel ulang baseline,
  jadi loop harian tetap inkremental sesudahnya. Bila `--limit` dipakai bersama
  `--force`, sisa video ditandai sebagai riwayat (tidak dianggap upload baru).
* `run` tanpa `--force` tidak pernah menstempel baseline.
* `--dry-run` tidak mengubah state sama sekali (tidak menstempel baseline,
  tidak menulis `state.json`).

---

## Pemasangan

### 1. Build image (di TV box atau di mesin lain)

```bash
docker build -t lecture-notes-bot:latest .
```

`python:3.12-slim` punya build arm64; untuk mesin build amd64 pakai
`docker buildx build --platform linux/arm64 -t lecture-notes-bot:latest --load .`.

Build di laptop lalu pindahkan:

```bash
docker save lecture-notes-bot:latest | gzip > lecture-notes-bot.tgz
scp lecture-notes-bot.tgz user@box:/tmp/
ssh user@box 'gunzip -c /tmp/lecture-notes-bot.tgz | docker load'
```

### 2. Siapkan konfigurasi di host

```bash
sudo mkdir -p /opt/lecture-notes-bot
sudo cp config.example.yaml /opt/lecture-notes-bot/config.yaml
sudo nano /opt/lecture-notes-bot/config.yaml    # isi URL playlist
```

Isi kolom `url:` untuk 7 mata kuliah berikut (nama sudah terisi):

| Mata kuliah |
|---|
| Sistem Penunjang Keputusan |
| Sistem Multimedia |
| Pengelolaan Proyek Sistem Informasi |
| Testing dan Implementasi Sistem |
| Business Intelligence |
| Sistem Terdistribusi |
| Analisis Kinerja Sistem |

Nomor ruang dan jam kuliah sengaja tidak di-encode di mana pun.

### 3. Stack di Portainer

Portainer → **Stacks → Add stack → Web editor**, tempel isi
[`docker-compose.portainer.yml`](docker-compose.portainer.yml), lalu isi
**Environment variables**:

| Variabel | Isi |
|---|---|
| `OPENAI_BASE_URL` | mis. `https://api.openai.com/v1` (atau endpoint kompatibel lain) |
| `OPENAI_API_KEY` | API key |
| `OPENAI_MODEL` | mis. `gpt-4o-mini` |
| `DISCORD_NOTES_WEBHOOK` | webhook channel `#notes` |
| `DISCORD_CALENDAR_WEBHOOK` | webhook channel `#calendar` |

Batasan yang sengaja dipatuhi:

* `image: lecture-notes-bot:latest`, **tanpa** `build:` (Portainer me-resolve
  context build di `/data/compose/<id>/`);
* **tanpa** `env_file:` — rahasia ada di blok `environment:` stack;
* `config.yaml` di-bind-mount dari path absolut
  `/opt/lecture-notes-bot/config.yaml`;
* `/app/data` di named volume `lecture-notes-data`;
* `restart: unless-stopped`, log json-file `10m × 2`;
* command `["python", "-m", "src.main", "watch", "--all"]`.

State (`baselines`, `skipped`, `videos`, `reminded`, `calendar_last_week`)
tersimpan di volume, jadi aman saat container di-kill dan dibuat ulang.

`config.yaml` dibaca ulang setiap tick — mengisi URL playlist atau mengubah
jadwal tidak perlu restart container.

### 4. Manual acceptance run (first time)

Dengan stack sudah jalan sekali dan `data/state.json` baru:

```bash
# 1) baseline: tick pertama mencatat baseline, nol panggilan LLM
docker logs -f lecture-notes-bot        # -> baseline stamped, skipping all N existing videos

# 2) proses satu video lama, tanpa --all, posting ke dua channel
docker exec lecture-notes-bot python -m src.main run --playlist "<URL>" --force --limit 1

# 3) jalankan lagi tanpa --force -> tidak ada yang baru
docker exec lecture-notes-bot python -m src.main run --playlist "<URL>"

# 4) cek pengingat & kalender tanpa posting
docker exec lecture-notes-bot python -m src.main remind --dry-run
docker exec lecture-notes-bot python -m src.main calendar-image --dry-run --out /tmp/cal.png
```

State tersimpan di named volume `lecture-notes-data` (`/app/data`), jadi aman
saat container di-kill dan dibuat ulang.

### 5. Pengembangan lokal

```bash
python -m venv .venv && . .venv/bin/activate     # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp config.example.yaml config.yaml
cp .env.example .env        # lalu export variabelnya (mis. python-dotenv manual / setx)

python -m src.main run --playlist "URL" --limit 1 --dry-run
python -m src.main list --playlist "URL"
python -m src.main remind --dry-run
python -m src.main calendar-image --dry-run --out /tmp/cal.png
python -m unittest discover -s tests -v
```

`docker-compose.yml` (bukan Portainer) dipakai untuk build/test lokal dan
boleh memakai `env_file: .env`.

---

## CLI

```text
run [--playlist URL] [--all] [--limit N] [--force]
    [--no-discord] [--dry-run]
watch [--all] [--dry-run] [--force] [--no-discord]
list --playlist URL
remind [--dry-run] [--force] [--no-discord]
calendar-image [--month YYYY-MM] [--out PATH] [--dry-run] [--no-discord]
```

`--config PATH` bisa dipakai sebelum subcommand (default `config.yaml`,
atau `LECTURE_BOT_CONFIG`).

`--dry-run` mencetak embed + pesan catatan + daftar tugas alih-alih mem-posting,
dan tidak menulis state. `--no-discord` menjalankan pipeline penuh tanpa
mengirim ke Discord.

---

## Pipeline per video

1. **Enumerasi playlist** dengan yt-dlp (flat, `extract_flat="in_playlist"`, tanpa unduh).
2. **Caption terbaik**: `subtitles` manual diutamakan daripada
   `automatic_captions`; parse **VTT** dan **json3**; duplikat bergulir
   dihapus; cue digabung jadi paragraf dengan penanda `[H:MM:SS]`.
3. **Map-reduce** bila transkrip > `notes.max_transcript_chars` (default 60 000):
   setiap potongan diringkas dulu dengan `prompts/condense.md`.
4. **Satu panggilan LLM** dengan skema JSON ketat →
   `{title, language, summary, key_concepts, tags, assignments}`.
5. **Normalisasi** (bahasa dipaksa `id`, maks 10 tugas, tanggal tak valid → `null`,
   tipe tugas dipetakan) lalu **posting** ke Discord.

Video yang gagal (tanpa caption, LLM error, JSON rusak) dilaporkan di log dan
playlist dilanjutkan.

## Pengaturan lain

```yaml
reminders:
  days_before: [1, 0]       # T-1 dan T-0
calendar:
  image:
    enabled: true
    weekday: "monday"       # PNG diposting sekali per minggu ISO
    lead_days: 7            # akhir bulan -> gambar bulan berikutnya
    output_dir: "data/calendar"
```

* Tugas dengan `due_date: null` tidak bisa diingatkan; jumlahnya dicatat di log.
* Dedupe pengingat per `(tugas, offset)` di `state.reminded`.
* Kalender PNG: 900×1020, grid bulanan Senin-dulu, label hari Indonesia,
  satu titik berwarna per tugas di dalam sel (warna selon jenis), hari ini
  diberi garis tepi, di bawah grid ada daftar bertanggal.

## Struktur berkas

```text
src/
  config.py            # YAML + ${ENV} + akses bertitik
  transcript.py        # caption yt-dlp, parse VTT/json3, gabung paragraf
  playlist.py          # enumerasi playlist flat
  llm.py               # klien OpenAI-compatible, ekstraksi JSON, retry
  prompts.py           # pemuat template prompt
  notes.py             # generasi, normalisasi, render Discord Indonesia
  assignments.py       # urutan, render Indonesia, pembuat .ics
  discord_webhook.py   # send_text/send_embed/send_files, split aman-fence
  state.py             # JSON atomik, baseline, skipped, reminded
  reminders.py         # scan tenggat, WITA, dedupe
  calendar_image.py    # grid Pillow, pemicu mingguan
  main.py              # CLI: run / watch / list / remind / calendar-image
  timeutil.py          # tambahan: zona WITA, tanggal Indonesia
  logging_setup.py     # tambahan: logging ter-timestamp ke stdout
prompts/{notes_system,notes_user,condense}.md
tests/test_core.py     # unit test tanpa jaringan
tests/test_e2e.py      # CLI end-to-end dengan LLM tiruan
scripts/             # helper untuk box: build / update / restart / config / bot / setup
```

Modul tambahan di luar struktur saran: `timeutil.py` (zona WITA bersama,
format tanggal Indonesia) dan `logging_setup.py` (log ber-timestamp ke stdout).

## Kriteria penerimaan (sudah diverifikasi)

| Kriteria | Cara cek |
|---|---|
| `docker build -t lecture-notes-bot:latest .` | base `python:3.12-slim` (arm64 tersedia) |
| `import` semua `src/*.py` | `python -c "import src.main"` |
| `run --playlist URL --limit 1 --dry-run` | mencetak embed + catatan 2 header, tidak posting |
| `watch --all` start pertama | `baseline stamped, skipping all N existing videos`, 0 panggilan LLM |
| `run --playlist URL --force --limit 1` | memproses 1 video, posting ke dua channel |
| rerun tanpa `--force` | tidak ada yang baru |
| `remind --dry-run` | daftar pengingat sekarang (WITA) tanpa posting |
| `calendar-image --dry-run --out /tmp/cal.png` | PNG valid, tanpa posting |
| container di-kill & dibuat ulang | volume `/app/data` menyimpan seluruh state |
