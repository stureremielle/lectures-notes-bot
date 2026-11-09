#!/bin/sh
# Work with the config the running stack actually mounts.
#
#   ./scripts/config.sh path     # which file does the container read?
#   ./scripts/config.sh edit     # edit it in place (keeps the same inode)
#   ./scripts/config.sh install  # create it from config.example.yaml if missing
#   ./scripts/config.sh check    # is every playlist URL filled in?
#
# Note: the watcher re-reads config.yaml on every tick, so edits apply without
# recreating the container.
set -eu
cd "$(dirname "$0")/.."
. ./scripts/_lib.sh

say "config.yaml di dalam container: /app/config.yaml"
if container_running; then
  MOUNT=$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/app/config.yaml"}}{{.Source}}{{end}}{{end}}' "$APP_NAME" 2>/dev/null || true)
else
  MOUNT=''
fi
COMPOSE_PATH=$(compose_config_path || true)

cmd="${1:-path}"
case "$cmd" in
  path)
    if [ -n "$MOUNT" ]; then
      say "Dibind-mount dari: $MOUNT"
      [ -f "$MOUNT" ] || warn "file belum ada — jalankan: ./scripts/config.sh install"
    else
      say "Container belum berjalan; stack seharusnya memakai: ${COMPOSE_PATH:-/data/compose/1/config.yaml}"
    fi
    FOUND=$(find_portainer_config || true)
    [ -n "$FOUND" ] && printf '  kandidat di disk: %s\n' "$FOUND"
    ;;
  install)
    TARGET="$MOUNT"
    [ -n "$TARGET" ] || TARGET="${COMPOSE_PATH:-/data/compose/1/config.yaml}"
    if [ -f "$TARGET" ]; then
      say "Sudah ada: $TARGET (tidak ditimpa)"
      exit 0
    fi
    mkdir -p "$(dirname "$TARGET")"
    cp config.example.yaml "$TARGET"
    say "Dibuat: $TARGET"
    say "Buka Portainer -> stack -> Editor -> klik ikon 'Editor'/'Copy from file'? Cukup isi URL playlist, atau edit di sini:"
    printf '  sudo nano %s\n' "$TARGET"
    ;;
  edit)
    TARGET="$MOUNT"
    [ -n "$TARGET" ] || TARGET="${COMPOSE_PATH:-/data/compose/1/config.yaml}"
    [ -f "$TARGET" ] || die "belum ada $TARGET — jalankan dulu: ./scripts/config.sh install"
    EDITOR_BIN="${EDITOR:-nano}"
    have "$EDITOR_BIN" || EDITOR_BIN=vi
    "$EDITOR_BIN" "$TARGET"
    say "Disimpan. Perubahan berlaku pada tick berikutnya (tanpa restart)."
    ;;
  check)
    TARGET="$MOUNT"
    [ -n "$TARGET" ] || TARGET="${COMPOSE_PATH:-/data/compose/1/config.yaml}"
    [ -f "$TARGET" ] || die "tidak menemukan config.yaml"
    EMPTY=$(awk '/url:/{gsub(/[" ]/,"",$2); if ($2=="" ) c++} END{print c+0}' "$TARGET")
    if [ "$EMPTY" -gt 0 ]; then
      warn "$EMPTY playlist masih tanpa URL di $TARGET"
      awk '/- name:/{name=$0} /url: ""/{print "   " name}' "$TARGET"
      exit 1
    fi
    say "Semua playlist punya URL ($TARGET)."
    ;;
  *)
    die "perintah tidak dikenal: $cmd (pakai: path|edit|install|check)"
    ;;
esac
