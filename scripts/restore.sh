#!/bin/sh
# Restore a backup made by scripts/backup.sh into the state volume.
#
#   sudo ./scripts/restore.sh backups/state-20261110-210000
#
# Stops nothing and starts nothing: the file is copied into the volume, so the
# next run (or the next tick) picks it up. Take a backup of the current state
# first — this overwrites state.json.
set -eu
cd "$(dirname "$0")/.."
. ./scripts/_lib.sh

need_docker

SRC="${1:-}"
[ -n "$SRC" ] || die "pakai: sudo $HOST_DIR/scripts/restore.sh <folder-backup>"
[ -d "$SRC" ] || die "folder tidak ditemukan: $SRC"
if [ ! -f "$SRC/state.json" ]; then
  warn "$SRC tidak berisi state.json — tidak ada yang bisa dipulihkan"
  exit 1
fi

say "Backup state sekarang (agar bisa dibatalkan):"
"$HOST_DIR/scripts/backup.sh" || warn "backup gagal; lanjut memulihkan"

SRC_ABS=$(cd "$SRC" && pwd)
say "Memulihkan $SRC_ABS -> volume lecture-notes-data"
docker run --rm \
  -v lecture-notes-data:/data \
  -v "$SRC_ABS:/backup:ro" \
  "$IMAGE" sh -c "cp -a /backup/. /data/" >/dev/null

say "Selesai. Cek: ./scripts/bot.sh state"
