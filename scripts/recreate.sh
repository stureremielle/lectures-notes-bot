#!/bin/sh
# Recreate the bot container WITHOUT Portainer (and without losing secrets).
#
#   sudo ./scripts/recreate.sh          # recreate from the local image
#
# Why this exists: Portainer is only needed to *recreate* the container. The
# stack's environment variables are already baked into the running container,
# so we dump them to a root-only file and hand them to `docker run
# --env-file`. That keeps secrets off the command line (no `ps`/history leak)
# and never runs `docker compose`, which would substitute ${...} as empty.
#
# Safe: the state volume (lecture-notes-data) is untouched; only the container
# is replaced.
set -eu
cd "$(dirname "$0")/.."
. ./scripts/_lib.sh

need_docker

ENVFILE="$HOST_DIR/.env.runtime"
MOUNT_CFG="$HOST_DIR/config.yaml"

# Optional: use a specific env file (e.g. after a fresh clone, when no container
# exists to read the environment from).
while [ "$#" -gt 0 ]; do
  case "$1" in
    --env-file)
      shift
      [ "$#" -gt 0 ] || die "--env-file butuh sebuah path"
      ENVFILE="$1" ;;
    --help)
      sed -n '2,12p' "$0"
      exit 0 ;;
    *)
      die "opsi tidak dikenal: $1 (pakai: --env-file PATH)" ;;
  esac
  shift
done

if [ ! -f "$MOUNT_CFG" ]; then
  die "$MOUNT_CFG tidak ada — jalankan dulu: sudo $HOST_DIR/scripts/config.sh install"
fi
if [ -d "$MOUNT_CFG" ]; then
  die "$MOUNT_CFG adalah direktori — jalankan: sudo $HOST_DIR/scripts/config.sh install"
fi
[ -n "$(image_created)" ] || die "image $IMAGE belum ada — build dulu: $HOST_DIR/scripts/build.sh"

# 1. Capture the environment from the existing container (if any).
if docker inspect "$APP_NAME" >/dev/null 2>&1; then
  say "Membaca environment dari container yang ada"
  ENV_COUNT=$(docker inspect -f '{{len .Config.Env}}' "$APP_NAME" 2>/dev/null || echo 0)
  case "$ENV_COUNT" in ''|*[!0-9]*) ENV_COUNT=0 ;; esac
  if [ "$ENV_COUNT" -lt 6 ]; then
    warn "container lama hanya punya $ENV_COUNT env var (mungkin dibuat tanpa secret)."
    warn "Pastikan $ENVFILE sudah benar sebelum melanjutkan."
  fi
  umask 077
  docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$APP_NAME" >"$ENVFILE"
  # PATH/HOSTNAME/PYTHON_* from the image are harmless to repeat, but keep the
  # file readable and free of image-injected noise where possible.
  grep -v -E '^(PATH|HOSTNAME|HOME|LANG|GPG_KEY|PYTHON_|PIP_|PATH=)' "$ENVFILE" >"$ENVFILE.tmp" || true
  mv "$ENVFILE.tmp" "$ENVFILE"
  chmod 600 "$ENVFILE"
  say "Environment disimpan di $ENVFILE (600, tidak masuk git)"
elif [ -f "$ENVFILE" ]; then
  warn "container $APP_NAME tidak ada; memakai $ENVFILE yang sudah tersimpan"
else
  die "tidak ada container $APP_NAME dan tidak ada $ENVFILE.
    Buat dari template lalu isi:
      cp $HOST_DIR/.env.runtime.example $ENVFILE && chmod 600 $ENVFILE
      nano $ENVFILE
    (6 baris: TZ, OPENAI_BASE_URL, OPENAI_API_KEY, OPENAI_MODEL,
     DISCORD_NOTES_WEBHOOK, DISCORD_CALENDAR_WEBHOOK)"
fi

# 2. Show what will be used, without printing the values.
say "Variabel yang dipakai:"
grep -o '^[A-Z_]*' "$ENVFILE" | sed 's/^/    /'

# 3. Remember the compose labels BEFORE removing the container: they are what
#    Portainer matches a stack against, so a terminal-recreated container still
#    appears inside its stack instead of making the stack look broken.
LABEL_ARGS=$(docker inspect -f '{{range $k, $v := .Config.Labels}}{{$k}}={{$v}}
{{end}}' "$APP_NAME" 2>/dev/null | grep '^com\.docker\.compose\.' | sed '/^$/d;s/^/--label /' | tr '\n' ' ' || true)
if [ -n "$LABEL_ARGS" ]; then
  say "Label stack Portainer disalin dari container lama"
else
  warn "container lama tidak punya label compose (bukan dari stack Portainer)"
fi

# 4. Replace the container.
say "Menghapus container lama ($APP_NAME) — volume state tetap"
docker rm -f "$APP_NAME" >/dev/null 2>&1 || true

say "Membuat container baru dari $IMAGE"
docker run -d \
  --name "$APP_NAME" \
  --restart unless-stopped \
  --env-file "$ENVFILE" \
  -v "$MOUNT_CFG:/app/config.yaml:ro" \
  -v lecture-notes-data:/app/data \
  --log-driver json-file --log-opt max-size=10m --log-opt max-file=2 \
  $LABEL_ARGS \
  "$IMAGE" python -m src.main watch --all >/dev/null

sleep 4
if container_running; then
  say "Container berjalan."
else
  warn "Container tidak berjalan. Log:"
  docker logs --tail 30 "$APP_NAME" || true
  exit 1
fi

say "Log awal:"
docker logs --tail 15 "$APP_NAME" 2>&1 | sed 's/^/  | /'

say "Cek lengkap: $HOST_DIR/scripts/doctor.sh"
