#!/bin/sh
# Point a fresh Amlogic Armbian box at this repo and print the exact Portainer
# steps. Safe to re-run.
#
#   sudo ./scripts/setup-box.sh                      # /opt/lecture-notes-bot
#   sudo HOST_DIR=/srv/lb ./scripts/setup-box.sh     # custom location
set -eu
cd "$(dirname "$0")/.."
. ./scripts/_lib.sh

REPO_URL="${REPO_URL:-https://github.com/stureremielle/lectures-notes-bot.git}"

need_docker

if [ "$(id -u)" != "0" ]; then
  warn "disarankan dijalankan dengan sudo (agar bisa menulis $HOST_DIR)"
fi

if [ -d "$HOST_DIR/.git" ]; then
  say "Repositori sudah ada di $HOST_DIR"
elif [ -e "$HOST_DIR" ]; then
  die "$HOST_DIR sudah ada tapi bukan clone git. Pindahkan dulu atau set HOST_DIR."
else
  need_git
  say "Clone $REPO_URL -> $HOST_DIR"
  mkdir -p "$(dirname "$HOST_DIR")"
  git clone "$REPO_URL" "$HOST_DIR"
fi

say "Build image (native $(uname -m))"
"$HOST_DIR/scripts/build.sh"

say "Folder konfigurasi"
mkdir -p "$HOST_DIR"
if [ ! -f "$HOST_DIR/config.yaml" ]; then
  cp "$HOST_DIR/config.example.yaml" "$HOST_DIR/config.yaml"
  say "config.yaml dibuat dari template — isi 7 URL playlist di dalamnya"
else
  say "config.yaml sudah ada, tidak ditimpa"
fi

say "Isi konfigurasi (termasuk TANPA nomor ruang / jam kelas):"
printf '  sudo ${EDITOR:-nano} %s/config.yaml\n\n' "$HOST_DIR"

cat <<EOF
${C_OK}Langkah Portainer${C_OFF}
  1. Stacks -> Add stack -> Repository
       Repository   : stureremielle/lectures-notes-bot  (tambah Git credentials
                      bila privat; PAT fine-grained read-only cukup)
       Reference    : refs/heads/main
       Compose path : $STACK_FILE
  2. Environment variables (tekan "Add environment variable" 6x):
       TZ=Asia/Makassar
       OPENAI_BASE_URL=...        OPENAI_API_KEY=...        OPENAI_MODEL=...
       DISCORD_NOTES_WEBHOOK=...  DISCORD_CALENDAR_WEBHOOK=...
  3. Setelah deploy, pastikan file konfigurasi yang di-mount ada:
       sudo $HOST_DIR/scripts/config.sh path
       sudo $HOST_DIR/scripts/config.sh install   # bila belum ada
     (atau ganti bind mount di compose menjadi $HOST_DIR:/app/config:ro
      + LECTURE_BOT_CONFIG=/app/config/config.yaml)
  4. Cek log: baseline stamped, skipping all N existing videos
       docker logs --tail 20 $APP_NAME

${C_OK}Update berikutnya${C_OFF}
  sudo $HOST_DIR/scripts/update.sh      # git pull + rebuild
  Portainer -> stack -> "${C_OK}Update the stack${C_OFF}"
EOF
