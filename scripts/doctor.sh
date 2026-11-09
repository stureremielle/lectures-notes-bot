#!/bin/sh
# One command that answers "is it working?" — prints PASS/WARN/FAIL per check.
#
#   sudo ./scripts/doctor.sh          # full check
#   sudo ./scripts/doctor.sh --logs   # ... then tail the container log
#
# Exit code: 0 = all good, 1 = at least one FAIL.
set -eu
cd "$(dirname "$0")/.."
. ./scripts/_lib.sh

FAILED=0
WARNED=0

ok()   { printf '  [ OK ] %s\n' "$*"; }
warn() { printf '  [WARN] %s\n' "$*"; WARNED=$((WARNED + 1)); }
bad()  { printf '  [FAIL] %s\n' "$*"; FAILED=$((FAILED + 1)); }
head_() { printf '\n== %s\n' "$*"; }

head_ "Docker"
if ! have docker; then
  bad "docker tidak ditemukan"
  exit 1
fi
if docker info >/dev/null 2>&1; then ok "docker daemon terjangkau"; else bad "tidak bisa menghubungi docker daemon (cek grup docker)"; fi

head_ "Image"
if IMG_CREATED=$(image_created) && [ -n "$IMG_CREATED" ]; then
  ok "$IMAGE dibuat $(printf '%s' "$IMG_CREATED" | cut -c1-19)"
else
  bad "$IMAGE belum ada — build: sudo $HOST_DIR/scripts/build.sh"
fi

head_ "Container"
if ! docker inspect "$APP_NAME" >/dev/null 2>&1; then
  bad "container $APP_NAME belum ada — deploy stack di Portainer"
  printf '\nHasil: %d gagal, %d peringatan\n' "$FAILED" "$WARNED"
  exit 1
fi
if container_running; then
  ok "$APP_NAME berjalan"
else
  bad "$APP_NAME tidak berjalan (crash-loop?)"
fi
RESTARTS=$(docker inspect -f '{{.RestartCount}}' "$APP_NAME" 2>/dev/null || echo 0)
if [ "${RESTARTS:-0}" -gt 3 ]; then
  warn "sudah restart $RESTARTS kali — lihat log di bawah"
else
  ok "restart count: $RESTARTS"
fi
C_CREATED=$(docker inspect -f '{{.Created}}' "$APP_NAME" | cut -c1-19)
C_IMAGE_ID=$(docker inspect -f '{{.Image}}' "$APP_NAME" 2>/dev/null || true)
IMG_ID=$(docker image inspect -f '{{.Id}}' "$IMAGE" 2>/dev/null || true)
if [ -n "$C_IMAGE_ID" ] && [ -n "$IMG_ID" ] && [ "$C_IMAGE_ID" = "$IMG_ID" ]; then
  ok "container memakai image terbaru ($(printf '%s' "$IMG_ID" | cut -c8-19))"
else
  warn "container memakai image lama — klik 'Update the stack' di Portainer"
  printf '         container: %s\n' "$(printf '%s' "$C_IMAGE_ID" | cut -c8-19)"
  printf '         %-9s %s\n' "latest:" "$(printf '%s' "$IMG_ID" | cut -c8-19)"
fi

head_ "Docker data root (deteksi drive yang belum ter-mount)"
ROOTDIR=$(docker info --format '{{.DockerRootDir}}' 2>/dev/null || true)
if [ -n "$ROOTDIR" ]; then
  MP=$(findmnt -n -o TARGET -T "$ROOTDIR" 2>/dev/null || echo '?')
  say "root: $ROOTDIR (mount: $MP)"
  case "$ROOTDIR" in
    /var/lib/docker|/var/lib/docker/*)
      ok "lokasi standar di disk internal" ;;
    *)
      if [ "$MP" = "/" ]; then
        bad "data root BUKAN di mount terpisah — drive eksternal kemungkinan belum ter-mount saat docker start"
        printf '         container/image lama masih di drive itu; cek: findmnt /extdrive/ext1 ; sudo systemctl restart docker\n'
      elif [ "$MP" = "?" ] || [ -z "$MP" ]; then
        warn "tidak bisa memeriksa mount dari $ROOTDIR (findmnt tidak tersedia?)"
      else
        ok "di mount $MP"
      fi ;;
  esac
fi
VOL_MP=$(docker volume inspect lecture-notes-data -f '{{.Mountpoint}}' 2>/dev/null || true)
if [ -n "$VOL_MP" ]; then
  ok "volume lecture-notes-data: $VOL_MP"
else
  warn "volume lecture-notes-data belum ada (state dimulai dari kosong)"
fi

head_ "Repositori ($HOST_DIR)"
INVOKER="${SUDO_USER:-$(id -un)}"
if [ -d "$HOST_DIR/.git" ]; then
  REPO_OWNER=$(stat -c '%U' "$HOST_DIR/.git" 2>/dev/null || echo '?')
  if [ "$REPO_OWNER" = "$INVOKER" ]; then
    ok "milik $INVOKER — 'git pull' bisa tanpa sudo"
  else
    bad ".git milik '$REPO_OWNER', bukan '$INVOKER' — 'git pull' akan: permission denied"
    printf '         perbaiki: sudo chown -R %s:%s %s\n' "$INVOKER" "$(id -gn "$INVOKER" 2>/dev/null || echo "$INVOKER")" "$HOST_DIR"
  fi
else
  warn "$HOST_DIR bukan clone git — update lewat git tidak tersedia"
fi

head_ "config.yaml (bind mount)"
CFG_SRC=$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/app/config.yaml"}}{{.Source}}{{end}}{{end}}' "$APP_NAME" 2>/dev/null || true)
if [ -z "$CFG_SRC" ]; then
  bad "tidak ada mount ke /app/config.yaml"
elif [ -d "$CFG_SRC" ]; then
  bad "$CFG_SRC adalah DIREKTORI — perbaiki: sudo $HOST_DIR/scripts/config.sh install"
elif [ -f "$CFG_SRC" ]; then
  ok "mount: $CFG_SRC ($(wc -c <"$CFG_SRC" | tr -d ' ') bytes)"
else
  bad "mount source $CFG_SRC tidak ada"
fi

head_ "Secret di dalam container"
ENV_DUMP=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$APP_NAME" 2>/dev/null || true)
MISSING=''
for k in OPENAI_BASE_URL OPENAI_API_KEY OPENAI_MODEL DISCORD_NOTES_WEBHOOK DISCORD_CALENDAR_WEBHOOK; do
  if printf '%s\n' "$ENV_DUMP" | grep -q "^$k=."; then
    ok "$k terisi"
  else
    bad "$k kosong/tidak ada (deploy lewat Portainer, bukan docker compose manual)"
    MISSING="$MISSING $k"
  fi
done

head_ "Config terbaca oleh aplikasi"
if container_running; then
  CFG_OUT=$(docker exec "$APP_NAME" python -c 'from src.config import load_config; c = load_config(); p = c.playlists(); print("playlists=%d urls=%d tz=%s" % (len(p), sum(1 for x in p if x["url"]), c.timezone_name))' 2>&1 || true)
  case "$CFG_OUT" in
    playlists=*)
      ok "$CFG_OUT" ;;
    *IsADirectory*|*"Is a directory"*)
      bad "config.yaml terbaca sebagai direktori — jalankan scripts/config.sh install lalu Update the stack" ;;
    *)
      bad "gagal memuat config: $(printf '%s' "$CFG_OUT" | tail -1)" ;;
  esac
else
  warn "container tidak berjalan, cek config dilewati"
fi

head_ "State (volume lecture-notes-data)"
if container_running; then
  docker exec "$APP_NAME" python -c 'from src.state import State; from src.config import load_config; print(State(load_config().state_path).load().summary())' 2>/dev/null \
    | sed 's/^/  [ OK ] /' || warn "state belum bisa dibaca (normal saat start pertama)"
else
  warn "container tidak berjalan, state dilewati"
fi

head_ "Log terakhir"
docker logs --tail 12 "$APP_NAME" 2>&1 | sed 's/^/  | /' || true

printf '\nHasil: %d gagal, %d peringatan\n' "$FAILED" "$WARNED"
if [ "$FAILED" -gt 0 ]; then
  exit 1
fi
say "Semua pemeriksaan penting lolos."

if [ "${1:-}" = "--logs" ]; then
  docker logs --tail 50 -f "$APP_NAME"
fi
