#!/bin/sh
# Back up the bot's state volume (and config) before anything destructive.
#
#   sudo ./scripts/backup.sh              # -> ./backups/state-<timestamp>/
#   sudo ./scripts/backup.sh --list       # show existing backups
#
# The backup runs *inside* the bot image, so no extra tooling is needed on the
# host. state.json holds baselines/skipped/videos/reminded, which is what makes
# the bot incremental; losing it only costs a re-baseline, but a backup is free.
set -eu
cd "$(dirname "$0")/.."
. ./scripts/_lib.sh

need_docker

BACKUP_ROOT="${BACKUP_ROOT:-$HOST_DIR/backups}"

if [ "${1:-}" = "--list" ]; then
  if [ -d "$BACKUP_ROOT" ]; then
    ls -1dt "$BACKUP_ROOT"/state-* 2>/dev/null | while read -r d; do
      printf '%s  %s\n' "$(du -sh "$d" 2>/dev/null | cut -f1)" "$(basename "$d")"
    done
  else
    say "belum ada backup di $BACKUP_ROOT"
  fi
  exit 0
fi

if ! docker volume inspect lecture-notes-data >/dev/null 2>&1; then
  warn "volume lecture-notes-data tidak ada — tidak ada yang dibackup"
  exit 0
fi
[ -n "$(image_created)" ] || die "image $IMAGE belum ada; backup butuh image itu untuk cp"

STAMP=$(date +%Y%m%d-%H%M%S)
TARGET="$BACKUP_ROOT/state-$STAMP"
mkdir -p "$TARGET"
chmod 700 "$BACKUP_ROOT" "$TARGET"

say "Menyalin /app/data -> $TARGET"
docker run --rm \
  -v lecture-notes-data:/data:ro \
  -v "$BACKUP_ROOT:/backup" \
  "$IMAGE" sh -c "cp -a /data/. /backup/state-$STAMP/" >/dev/null

if [ -f "$TARGET/state.json" ]; then
  SIZE=$(wc -c <"$TARGET/state.json" | tr -d ' ')
  say "state.json tersimpan ($SIZE bytes)"
else
  warn "state.json belum ada di volume (normal pada instalasi baru)"
fi

# config.yaml is the other thing worth keeping (it is not in git).
if [ -f "$HOST_DIR/config.yaml" ]; then
  cp "$HOST_DIR/config.yaml" "$TARGET/config.yaml"
  chmod 600 "$TARGET/config.yaml"
  say "config.yaml ikut disalin"
fi

say "Selesai: $TARGET"
printf '  daftar backup: sudo %s/scripts/backup.sh --list\n' "$HOST_DIR"
printf '  pulihkan     : sudo ./scripts/restore.sh %s\n' "$TARGET"
