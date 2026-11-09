#!/bin/sh
# Pull the latest code and rebuild the image. This is the whole "update" step.
#
#   ./scripts/update.sh            # git pull (fast-forward only) + rebuild
#   ./scripts/update.sh --tag      # also tag the image with today's date
#
# Afterwards click "Update the stack" in Portainer (or run scripts/restart.sh).
set -eu
cd "$(dirname "$0")/.."
. ./scripts/_lib.sh

need_docker
need_git

TAG_DATE=0
for arg in "$@"; do
  case "$arg" in
    --tag) TAG_DATE=1 ;;
    --help) sed -n '2,9p' "$0"; exit 0 ;;
    *) die "opsi tidak dikenal: $arg" ;;
  esac
done

# Make sure the state volume survives: it is a named volume, not a bind mount,
# so rebuilding the image never touches it. We still show the summary first.
if container_running; then
  say "State sebelum update:"
  docker exec "$APP_NAME" python -c "from src.state import State; from src.config import load_config; print(State(load_config().state_path).load().summary())" \
    || warn "tidak bisa membaca ringkasan state (lanjut saja)"
fi

BEFORE=$(git rev-parse --short HEAD)
say "git pull (fast-forward saja) — sekarang di $BEFORE"
git pull --ff-only
AFTER=$(git rev-parse --short HEAD)

if [ "$BEFORE" = "$AFTER" ]; then
  say "Tidak ada perubahan kode ($AFTER). Tetap rebuild karena dependency bisa ikut berubah."
else
  say "Perubahan:"
  git --no-pager log --oneline "$BEFORE..$AFTER" | sed 's/^/  /'
fi

say "Build image $IMAGE"
docker build -t "$IMAGE" .

if [ "$TAG_DATE" = "1" ]; then
  DATED="$IMAGE-$(date +%Y%m%d)"
  docker tag "$IMAGE" "$DATED"
  say "Ditandai juga sebagai $DATED"
fi

say "Selesai. Terapkan ke container yang berjalan:"
printf '  sudo %s/scripts/restart.sh        # = docker compose up -d --force-recreate\n' "$HOST_DIR"
printf '  atau di Portainer: stack -> "Update the stack" (hasilnya sama)\n'
printf '  Config dibaca ulang setiap tick, jadi ubah config.yaml TIDAK butuh restart.\n'
