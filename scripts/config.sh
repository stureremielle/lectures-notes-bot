#!/bin/sh
# Work with the config the running stack actually mounts.
#
#   ./scripts/config.sh status   # what path, file or directory, is it valid?
#   ./scripts/config.sh path     # same, short form
#   ./scripts/config.sh install  # create/repair the file from config.example.yaml
#   ./scripts/config.sh edit     # edit it in place
#   ./scripts/config.sh check    # is every playlist URL filled in?
#
# The watcher re-reads config.yaml every tick, so edits apply without a restart.
set -eu
cd "$(dirname "$0")/.."
. ./scripts/_lib.sh

# Where the stack mounts it from. Order: live container -> compose file -> host dir.
config_target() {
  MOUNT=$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/app/config.yaml"}}{{.Source}}{{end}}{{end}}' "$APP_NAME" 2>/dev/null || true)
  if [ -n "$MOUNT" ]; then
    printf '%s\n' "$MOUNT"
    return 0
  fi
  COMPOSE=$(compose_config_path || true)
  if [ -n "$COMPOSE" ]; then
    printf '%s\n' "$COMPOSE"
    return 0
  fi
  printf '%s\n' "$HOST_DIR/config.yaml"
}

# What is actually at that path?
describe() {
  if [ -L "$1" ]; then
    printf 'symlink -> %s\n' "$(readlink "$1")"
  elif [ -d "$1" ]; then
    printf 'DIRECTORY (placeholder dari bind mount yang source-nya belum ada)\n'
  elif [ -f "$1" ]; then
    printf 'file (%s bytes)\n' "$(wc -c <"$1" | tr -d ' ')"
  elif [ -e "$1" ]; then
    printf 'ada, tapi bukan file biasa\n'
  else
    printf 'TIDAK ADA\n'
  fi
}

cmd="${1:-status}"
TARGET=$(config_target)

case "$cmd" in
  status|path)
    say "Container      : $APP_NAME ($(container_running && echo berjalan || echo tidak berjalan))"
    say "Path yang di-mount ke /app/config.yaml:"
    printf '                 %s\n' "$TARGET"
    say "Isi path itu   : $(describe "$TARGET")"
    if [ -f "$TARGET" ]; then
      say "Status         : OK — container membaca file ini"
      EMPTY=$(awk '/url:/{gsub(/[" ]/,"",$2); if ($2=="") c++} END{print c+0}' "$TARGET")
      if [ "$EMPTY" -gt 0 ]; then
        warn "$EMPTY playlist masih tanpa URL di file ini"
      else
        say "Playlist       : semua punya URL"
      fi
    elif [ -d "$TARGET" ]; then
      warn "Status         : RUSAK — perbaiki dengan: sudo $HOST_DIR/scripts/config.sh install"
      printf '  isi direktori: %s\n' "$(ls -A "$TARGET" 2>/dev/null | head -5 | tr '\n' ' ')"
    else
      warn "Status         : file belum ada — buat dengan: sudo $HOST_DIR/scripts/config.sh install"
    fi
    OTHER=$(find_portainer_config || true)
    [ -n "$OTHER" ] && printf '  catatan: file lain ditemukan di %s (tidak dipakai stack)\n' "$OTHER"
    ;;

  install)
    say "Target: $TARGET"
    # A directory here is Docker's placeholder for a bind mount whose host path
    # was missing at deploy time. `cp` into it would silently create
    # <dir>/config.example.yaml and leave the mount broken, so move it aside.
    if [ -d "$TARGET" ]; then
      BAK="$TARGET.dir-bak-$(date +%Y%m%d-%H%M%S)"
      warn "path ini adalah DIREKTORI — memindahkannya ke $BAK"
      mv "$TARGET" "$BAK"
      say "dipindahkan: $BAK"
    fi
    if [ -f "$TARGET" ]; then
      say "Sudah ada file: $TARGET (tidak ditimpa)"
      say "Status: OK"
      exit 0
    fi
    mkdir -p "$(dirname "$TARGET")"
    cp "$(dirname "$0")/../config.example.yaml" "$TARGET"
    chmod 644 "$TARGET"
    say "Dibuat: $TARGET ($(wc -c <"$TARGET" | tr -d ' ') bytes)"
    say "Status: OK — sekarang klik 'Update the stack' di Portainer (atau scripts/restart.sh)"
    ;;

  sync)
    # Refresh config.yaml from config.example.yaml (new defaults such as
    # watch.ignore_ids never reach an older copy otherwise). The old file is
    # always kept as a timestamped backup.
    [ -f "$TARGET" ] || die "belum ada file di $TARGET — jalankan dulu: config.sh install"
    if cmp -s "$TARGET" "$(dirname "$0")/../config.example.yaml"; then
      say "Sudah sama dengan config.example.yaml ($TARGET)"
      exit 0
    fi
    BAK="$TARGET.bak-$(date +%Y%m%d-%H%M%S)"
    cp "$TARGET" "$BAK"
    cp "$(dirname "$0")/../config.example.yaml" "$TARGET"
    chmod 644 "$TARGET"
    say "config.yaml disegarkan dari example; cadangan: $BAK"
    printf '  perbedaan lama vs baru: diff -u %s %s\n' "$BAK" "$TARGET"
    say "Berlaku pada tick berikutnya (tanpa restart). Cek: config.sh check"
    ;;

  edit)
    [ -f "$TARGET" ] || die "belum ada file di $TARGET — jalankan dulu: sudo $HOST_DIR/scripts/config.sh install"
    EDITOR_BIN="${EDITOR:-nano}"
    have "$EDITOR_BIN" || EDITOR_BIN=vi
    "$EDITOR_BIN" "$TARGET"
    say "Disimpan. Perubahan berlaku pada tick berikutnya (tanpa restart)."
    ;;

  check)
    [ -f "$TARGET" ] || die "tidak menemukan file config.yaml di $TARGET"
    EMPTY=$(awk '/url:/{gsub(/[" ]/,"",$2); if ($2=="") c++} END{print c+0}' "$TARGET")
    if [ "$EMPTY" -gt 0 ]; then
      warn "$EMPTY playlist masih tanpa URL di $TARGET"
      awk '/- name:/{name=$0} /url: ""/{print "   " name}' "$TARGET"
      exit 1
    fi
    say "Semua playlist punya URL ($TARGET)."
    ;;

  *)
    die "perintah tidak dikenal: $cmd (pakai: status|path|install|sync|edit|check)"
    ;;
esac
