#!/usr/bin/env bash
# Run by firecal-update.timer every 15 minutes (as root):
#   new code on GitHub  -> pull + restart the service
#   otherwise            -> ask the API to rebuild if the data files changed (/api/reload)
set -uo pipefail
DIR=/opt/firecal
RUN_USER="${FIRECAL_RUN_USER:-firecal}"
git config --global --add safe.directory "$DIR" 2>/dev/null
OLD="$(git -C "$DIR" rev-parse HEAD)"
runuser -u "$RUN_USER" -- git -C "$DIR" fetch -q origin main && runuser -u "$RUN_USER" -- git -C "$DIR" reset -q --hard origin/main
NEW="$(git -C "$DIR" rev-parse HEAD)"
if [ "$OLD" != "$NEW" ]; then
  if ! git -C "$DIR" diff --quiet "$OLD" "$NEW" -- requirements.txt; then
    runuser -u "$RUN_USER" -- "$DIR/.venv/bin/pip" install -q -r "$DIR/requirements.txt"
  fi
  echo "code updated $OLD -> $NEW, restarting"
  systemctl restart firecal
else
  curl -fsS --max-time 1800 -X POST "http://127.0.0.1:${FIRECAL_PORT:-8000}/api/reload?token=${FIRECAL_RELOAD_TOKEN}" || true
fi
