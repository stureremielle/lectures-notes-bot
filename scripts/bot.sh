#!/bin/sh
# Run any CLI command inside the running container — the *right* way, so it
# uses the same state volume and config as the scheduled ticks.
#
#   ./scripts/bot.sh run --playlist "<URL>" --force --limit 1
#   ./scripts/bot.sh run --all
#   ./scripts/bot.sh list --playlist "<URL>"
#   ./scripts/bot.sh remind --dry-run
#   ./scripts/bot.sh calendar-image --dry-run --out /tmp/cal.png
#   ./scripts/bot.sh state            # ringkasan state.json
#
# Harmless to run next to the watcher: state writes are atomic.
set -eu
cd "$(dirname "$0")/.."
. ./scripts/_lib.sh

[ "$#" -gt 0 ] || die "pakai: ./scripts/bot.sh <run|list|remind|calendar-image|watch> [opsi...]"

if [ "$1" = "state" ]; then
  container_running || die "container $APP_NAME tidak berjalan"
  docker exec "$APP_NAME" python -c "from src.state import State; from src.config import load_config; import json; print(json.dumps(State(load_config().state_path).load().summary(), indent=2))"
  exit 0
fi

bot_exec "$@"
