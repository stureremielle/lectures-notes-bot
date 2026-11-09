#!/bin/sh
# Build the image on the box and show what is there to start with.
#   ./scripts/build.sh [--no-cache]
set -eu
cd "$(dirname "$0")/.."
. ./scripts/_lib.sh

need_docker

say "Docker: $(docker version --format '{{.Server.Version}}') / arsitektur $(uname -m)"
say "Build image $IMAGE (aarch64 native, tanpa ffmpeg — caption saja)"
if [ "${1:-}" = "--no-cache" ]; then
  docker build --no-cache -t "$IMAGE" .
else
  docker build -t "$IMAGE" .
fi

say "Image lokal:"
docker images --format '  {{.Repository}}:{{.Tag}}\t{{.Size}}\t{{.CreatedSince}}' | grep "lecture-notes-bot" || true

say "Langkah berikutnya:"
printf '  1) buat stack di Portainer (Stacks -> Add stack) dengan %s\n' "$STACK_FILE"
printf '  2) isi config.yaml (lihat: ./scripts/config.sh path)\n'
