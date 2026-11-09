#!/bin/sh
# Pull the latest code and rebuild the image. This is the whole "update" step.
#
#   ./scripts/update.sh            # git pull (fast-forward only) + rebuild
#   ./scripts/update.sh --tag      # also tag the image with today's date
#   ./scripts/update.sh --reset    # discard local edits to tracked files first
#                                  # (config.yaml and data/ are never touched)
#
# Afterwards click "Update the stack" in Portainer (or run scripts/restart.sh).
set -eu
cd "$(dirname "$0")/.."
. ./scripts/_lib.sh

need_docker
need_git

TAG_DATE=0
RESET=0
for arg in "$@"; do
  case "$arg" in
    --tag) TAG_DATE=1 ;;
    --reset) RESET=1 ;;
    --help) sed -n '2,10p' "$0"; exit 0 ;;
    *) die "opsi tidak dikenal: $arg" ;;
  esac
done

# Tracked files (scripts/, src/, prompts/) belong to the repository: if one was
# touched locally, `git pull --ff-only` refuses to run. config.yaml and data/
# are gitignored, so this reset never touches them.
if [ -n "$(git status --porcelain --untracked-files=no)" ]; then
  warn "ada perubahan lokal pada file yang dilacak:"
  git --no-pager diff --stat | sed 's/^/    /'
  if [ "$RESET" = "1" ]; then
    git checkout -- .
    say "perubahan lokal dibuang (config.yaml dan data/ tidak tersentuh)"
  else
    die "jalankan '$HOST_DIR/scripts/update.sh --reset' untuk membuangnya, atau 'git checkout -- <file>' satu per satu"
  fi
fi

# Show the state summary before rebuilding: the volume is untouched by updates.
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
printf '  Portainer -> stack -> "Update the stack"   (cara paling aman)\n'
printf '  atau: %s/scripts/restart.sh                 (docker restart, env utuh)\n' "$HOST_DIR"
printf '  Config dibaca ulang setiap tick, jadi ubah config.yaml TIDAK butuh restart.\n'
