#!/bin/sh
# Restart the container after an image rebuild — safely.
#
#   ./scripts/restart.sh            # docker restart (env & mounts preserved)
#   ./scripts/restart.sh --logs     # then tail the logs
#
# `docker restart` keeps the container's environment, mounts and labels, so the
# variables Portainer injected stay intact.
#
# IMPORTANT: `docker restart` relaunches the SAME container from its OLD image.
# To adopt a newly built image, use Portainer -> stack -> "Update the stack"
# (or "Recreate" with "Re-pull image" off). This script tells you when that is
# needed.
#
# Why never `docker compose up -d` here? The compose file interpolates
# ${OPENAI_API_KEY} etc. Those are supplied by Portainer to the container, not
# to your shell, so compose would substitute EMPTY strings and start the bot
# without secrets.
set -eu
cd "$(dirname "$0")/.."
. ./scripts/_lib.sh

need_docker

if ! docker inspect "$APP_NAME" >/dev/null 2>&1; then
  die "container $APP_NAME belum ada. Deploy stack dulu di Portainer (Stacks -> Add stack -> $STACK_FILE)."
fi

CFG_SRC=$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/app/config.yaml"}}{{.Source}}{{end}}{{end}}' "$APP_NAME" 2>/dev/null || true)
if [ -n "$CFG_SRC" ] && [ -d "$CFG_SRC" ]; then
  die "config.yaml di-mount dari DIREKTORI ($CFG_SRC) — file di host belum ada,
    sehingga Docker membuat folder kosong. Perbaiki dulu:
      sudo $HOST_DIR/scripts/config.sh install
    lalu Portainer -> stack -> Update the stack."
fi

ENV_COUNT=$(docker inspect -f '{{len .Config.Env}}' "$APP_NAME")
say "Environment variable di container: $ENV_COUNT"
if [ "$ENV_COUNT" -lt 6 ]; then
  warn "terlihat kosong — deploy/Update lewat Portainer agar secret dari stack dipakai."
fi

CREATED=$(docker inspect -f '{{.Created}}' "$APP_NAME" | cut -c1-19)
IMAGE_BUILT=$(image_created | cut -c1-19)
say "Container dibuat : $CREATED"
say "Image dibangun   : $IMAGE_BUILT"
case "$IMAGE_BUILT" in
  "")
    warn "image $IMAGE belum ada; build dulu: sudo $HOST_DIR/scripts/update.sh" ;;
  *)
    if [ "$IMAGE_BUILT" \> "$CREATED" ]; then
      warn "image lebih baru dari container: klik 'Update the stack' di Portainer"
      warn "agar container memakai image baru (docker restart tetap memakai image lama)."
    fi ;;
esac

say "docker restart $APP_NAME"
docker restart "$APP_NAME" >/dev/null
sleep 3

if container_running; then
  say "Container berjalan."
else
  warn "Container tidak berjalan. Log terakhir:"
  docker logs --tail 40 "$APP_NAME" || true
  exit 1
fi

say "Log awal:"
docker logs --tail 15 "$APP_NAME" 2>&1 || true

if [ "${1:-}" = "--logs" ]; then
  docker logs --tail 50 -f "$APP_NAME"
fi
