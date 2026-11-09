#!/bin/sh
# Shared helpers for the box-side scripts (POSIX sh, runs on Armbian + Docker).
# Sourced, never executed.
#   . "$(dirname "$0")/_lib.sh"

set -eu

APP_NAME="lecture-notes-bot"
IMAGE="${IMAGE:-lecture-notes-bot:latest}"
STACK_FILE="docker-compose.portainer.yml"
HOST_DIR="${HOST_DIR:-/opt/lecture-notes-bot}"

# --- output ----------------------------------------------------------------
if [ -t 1 ]; then
  C_OK=$(printf '\033[32m'); C_WARN=$(printf '\033[33m'); C_ERR=$(printf '\033[31m'); C_OFF=$(printf '\033[0m')
else
  C_OK=''; C_WARN=''; C_ERR=''; C_OFF=''
fi
say()  { printf '%s==>%s %s\n' "$C_OK" "$C_OFF" "$*"; }
warn() { printf '%s[!]%s %s\n' "$C_WARN" "$C_OFF" "$*" >&2; }
die()  { printf '%s[x]%s %s\n' "$C_ERR" "$C_OFF" "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }

# --- preconditions ---------------------------------------------------------
need_docker() {
  have docker || die "docker tidak ditemukan. Pasang Docker dulu: curl -fsSL https://get.docker.com | sh"
  docker info >/dev/null 2>&1 || die "tidak bisa menghubungi Docker daemon (user ini masuk grup docker? coba: sudo usermod -aG docker \$USER lalu login ulang)"
}

need_git() {
  have git || die "git tidak ditemukan: sudo apt-get install -y git"
}

# --- config ----------------------------------------------------------------
# Stack-managed config lives next to Portainer's stack files. Portainer stores
# each repository stack under /data/compose/<id>/ (single-file stacks) and
# /data/compose/<id>/<repo>/ (multi-file stacks); it also runs under different
# container names depending on the install. We only *look*, never guess-write.
portainer_config_candidates() {
  printf '%s\n' \
    /data/compose/*/config.yaml \
    /data/compose/*/*/config.yaml \
    /var/lib/docker/volumes/portainer_data/_data/compose/*/config.yaml \
    /var/lib/docker/volumes/portainer_data/_data/compose/*/*/config.yaml
}

find_portainer_config() {
  for f in $(portainer_config_candidates); do
    [ -f "$f" ] || continue
    # Prefer a file that already contains our courses.
    if grep -q "Sistem Terdistribusi" "$f" 2>/dev/null; then
      printf '%s\n' "$f"
      return 0
    fi
  done
  for f in $(portainer_config_candidates); do
    [ -f "$f" ] && { printf '%s\n' "$f"; return 0; }
  done
  return 1
}

# The path the stack bind-mounts; read it from the compose file so the two stay
# in sync even if you change the stack id.
compose_config_path() {
  sed -n 's|^[[:space:]]*-[[:space:]]*\(/[^:]*\):/app/config\.yaml.*|\1|p' "$STACK_FILE" 2>/dev/null | head -n 1
}

# --- docker helpers --------------------------------------------------------
container_running() {
  [ "$(docker inspect -f '{{.State.Running}}' "$APP_NAME" 2>/dev/null || echo false)" = "true" ]
}

# Run a CLI command inside the running container (state lives in the volume).
bot_exec() {
  container_running || die "container $APP_NAME tidak berjalan. Cek: docker logs --tail 50 $APP_NAME"
  docker exec "$APP_NAME" python -m src.main "$@"
}

image_created() {
  docker image inspect -f '{{.Created}}' "$IMAGE" 2>/dev/null || true
}
