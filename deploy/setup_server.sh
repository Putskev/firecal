#!/usr/bin/env bash
# FireCal: install app + API on an Ubuntu server (Hetzner etc.) and connect your Parquet folder.
#
#   curl -fsSL https://raw.githubusercontent.com/Putskev/firecal/main/deploy/setup_server.sh | sudo bash -s -- /path/to/parquet
#
# The path is optional (folder with your Parquet/CSV files, searched recursively; several folders separated by ':').
# Running it again updates everything (safe to repeat). Result:
#   http://<server-ip>               app + API (always)
#   https://<ip-with-dashes>.sslip.io  same with HTTPS (automatic certificate, if ports 80/443 are open)
set -euo pipefail

SRC_ARG="${1:-}"
REPO="https://github.com/Putskev/firecal.git"
DIR=/opt/firecal
ENVF=/etc/firecal.env
say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31mERROR: %s\033[0m\n' "$*"; exit 1; }

[ "$(id -u)" = 0 ] || die "please run with sudo"
. /etc/os-release
[ "${ID:-}" = ubuntu ] || [ "${ID_LIKE:-}" = debian ] || [ "${ID:-}" = debian ] || die "tested on Ubuntu/Debian only (found ${ID:-?})"

say "1/7 System packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git python3 python3-venv python3-pip curl ca-certificates gnupg debian-keyring debian-archive-keyring apt-transport-https openssl >/dev/null
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || die "Python >= 3.10 needed (Ubuntu 22.04 or newer)"

say "2/7 Data folder"
SRC=""
if [ -n "$SRC_ARG" ]; then
  IFS=':' read -ra PARTS <<< "$SRC_ARG"
  for p in "${PARTS[@]}"; do
    [ -d "$p" ] || die "folder not found: $p"
    SRC="${SRC:+$SRC:}$(realpath "$p")"
  done
elif [ -f "$ENVF" ]; then
  SRC="$(grep -E '^FIRECAL_SOURCE=' "$ENVF" | cut -d= -f2- || true)"
fi
echo "FIRECAL_SOURCE=${SRC:-<none, only the data in the repo>}"

# service user: dedicated user 'firecal', or the owner of the data folder if 'firecal' cannot read it
id firecal >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/firecal --shell /usr/sbin/nologin firecal
RUN_USER=firecal
if [ -n "$SRC" ]; then
  FIRST="${SRC%%:*}"
  if ! runuser -u firecal -- test -r "$FIRST" -a -x "$FIRST"; then
    RUN_USER="$(stat -c %U "$FIRST")"
    echo "user 'firecal' cannot read $FIRST, running the service as its owner: $RUN_USER"
  fi
fi

say "3/7 Code (from GitHub)"
if [ -d "$DIR/.git" ]; then
  git config --global --add safe.directory "$DIR"
  git -C "$DIR" fetch -q origin && git -C "$DIR" reset -q --hard origin/main
else
  git clone -q --depth 1 "$REPO" "$DIR"
fi
chown -R "$RUN_USER": "$DIR"
git -C "$DIR" log --oneline -1

say "4/7 Python environment"
runuser -u "$RUN_USER" -- python3 -m venv "$DIR/.venv"
runuser -u "$RUN_USER" -- "$DIR/.venv/bin/pip" install -q --upgrade pip
runuser -u "$RUN_USER" -- "$DIR/.venv/bin/pip" install -q -r "$DIR/requirements.txt"

say "5/7 Check of the data folder"
if [ -n "$SRC" ]; then
  runuser -u "$RUN_USER" -- env FIRECAL_SOURCE="$SRC" "$DIR/.venv/bin/python" - <<'PY'
import os, sys
sys.path.insert(0, "/opt/firecal")
import pyarrow.parquet as pq
from backend import sources
hot, cal, team = sources.hotspot_files(), sources.calibration_files(), sources.team_files()
allf = sources._walk((".parquet", ".csv", ".csv.gz"))
rows = sum(pq.ParquetFile(f).metadata.num_rows for f in hot if f.endswith(".parquet"))
size = sum(os.path.getsize(f) for f in allf) / 1e9
print(f"files found: {len(allf)} ({size:.2f} GB)")
print(f"  hotspot tables (Tabelle A / FIRMS): {len(hot)} files, {rows:,} rows in Parquet")
print(f"  calibration (Tabelle C): {len(cal)}   team cells (Tabelle B): {len(team)}")
other = [f for f in allf if f not in hot + cal + team]
if other:
    print(f"  not used ({len(other)}), e.g.: " + ", ".join(os.path.basename(f) for f in other[:5]))
if hot:
    print("  columns of the first hotspot file:", ", ".join(sorted(sources._columns(hot[0]))))
mem = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9
need = 0.5 + rows / 1e6 * 0.45
print(f"RAM: {mem:.1f} GB, estimated for the build step: {need:.1f} GB")
if need > mem * 0.85:
    print("WARNING: probably too little RAM for the build step. Add swap, a bigger server, or fewer years.")
PY
else
  echo "no data folder given: the app uses the data inside the repo"
fi

say "6/7 Service (systemd) + automatic updates"
TOKEN="$(grep -E '^FIRECAL_RELOAD_TOKEN=' "$ENVF" 2>/dev/null | cut -d= -f2- || true)"
[ -n "$TOKEN" ] || TOKEN="$(openssl rand -hex 16)"
cat > "$ENVF" <<EOF
FIRECAL_SOURCE=$SRC
FIRECAL_RELOAD_TOKEN=$TOKEN
FIRECAL_RUN_USER=$RUN_USER
EOF
chmod 640 "$ENVF"; chown root:"$RUN_USER" "$ENVF"

cat > /etc/systemd/system/firecal.service <<EOF
[Unit]
Description=FireCal (FastAPI + DuckDB)
After=network-online.target
Wants=network-online.target

[Service]
User=$RUN_USER
WorkingDirectory=$DIR
EnvironmentFile=$ENVF
ExecStart=$DIR/.venv/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000 --proxy-headers
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/firecal-update.service <<EOF
[Unit]
Description=FireCal: pull new code from GitHub, pick up new data files
[Service]
Type=oneshot
EnvironmentFile=$ENVF
ExecStart=/bin/bash $DIR/deploy/update.sh
EOF
cat > /etc/systemd/system/firecal-update.timer <<EOF
[Unit]
Description=FireCal update every 15 minutes
[Timer]
OnBootSec=5min
OnUnitActiveSec=15min
[Install]
WantedBy=timers.target
EOF
systemctl daemon-reload
systemctl enable -q firecal firecal-update.timer
systemctl restart firecal
systemctl start firecal-update.timer

say "7/7 Web server (Caddy) with HTTPS"
if ! command -v caddy >/dev/null; then
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
  apt-get update -qq && apt-get install -y -qq caddy >/dev/null
fi
IP="$(curl -fsS4 --max-time 5 https://api.ipify.org || hostname -I | awk '{print $1}')"
HOST="${IP//./-}.sslip.io"
cat > /etc/caddy/Caddyfile <<EOF
# FireCal (written by deploy/setup_server.sh)
$HOST {
	encode gzip
	reverse_proxy 127.0.0.1:8000
}
http://$IP {
	encode gzip
	reverse_proxy 127.0.0.1:8000
}
EOF
systemctl enable -q caddy
systemctl reload caddy 2>/dev/null || systemctl restart caddy
if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
  ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null; echo "ufw: ports 80 and 443 opened"
fi

echo
echo "Waiting for the first data build (can take a few minutes with large data) ..."
for i in $(seq 1 180); do
  if curl -fsS --max-time 3 http://127.0.0.1:8000/api/health 2>/dev/null | grep -q '"ok":true'; then break; fi
  if ! systemctl is-active -q firecal; then journalctl -u firecal -n 30 --no-pager; die "service stopped, see log above"; fi
  sleep 5
done
META="$(curl -fsS --max-time 10 http://127.0.0.1:8000/api/meta || true)"
[ -n "$META" ] || { journalctl -u firecal -n 30 --no-pager; die "API does not answer yet. Log: journalctl -u firecal -f"; }
python3 - "$META" <<'PY'
import json, sys
m = json.loads(sys.argv[1])
print(f"data: {m['rows']:,} hotspots, {m['first_date']} -> {m['last_date']}, version {m['data_version']}")
PY

echo
printf '\033[1;32mFireCal runs.\033[0m Send your friends:\n'
echo "   https://$HOST     (HTTPS; certificate is fetched automatically, takes up to a minute)"
echo "   http://$IP        (works always)"
echo
echo "If the links do not open from outside: allow ports 80 and 443 in the Hetzner Cloud firewall."
echo "Log: journalctl -u firecal -f    |    new data: put files into the data folder, picked up within 15 min"
