#!/bin/sh
# Restart / verify the container after an image rebuild.
#
#   ./scripts/restart.sh          # check the container and (re)start it
#   ./scripts/restart.sh --logs   # tail the logs afterwards
set -eu
cd "$(dirname "$0")/.."
. ./scripts/_lib.sh

need_docker

if ! docker inspect "$APP_NAME" >/dev/null 2>&1; then
  die "container $APP_NAME belum ada. Deploy stack dulu di Portainer (Stacks -> Add stack -> $STACK_FILE)."
fi

CURRENT_IMAGE=$(docker inspect -f '{{.Config.Image}}' "$APP_NAME")
say "Container berjalan dengan image: $CURRENT_IMAGE"
say "Image lokal terbaru dibuat: $(image_created | cut -c1-19)"

# Same result as "Update the stack" without the Portainer UI.
say "Recreate container (docker compose --force-recreate), state tetap di volume"
docker compose --project-name "$APP_NAME" -f "$STACK_FILE" up -d --force-recreate </dev/null \
  || die "docker compose gagal; buat ulang lewat Portainer (stack -> Update the stack)"

sleep 3
if container_running; then
  say "Container berjalan."
else
  warn "Container tidak berjalan. Log terakhir:"
  docker logs --tail 40 "$APP_NAME" || true
  exit 1
fi

say "State (dari volume lecture-notes-data):"
docker exec "$APP_NAME" python -c "from src.state import State; from src.config import load_config; print(State(load_config().state_path).load().summary())" || true

if [ "${1:-}" = "--logs" ]; then
  docker logs --tail 50 -f "$APP_NAME"
fi
