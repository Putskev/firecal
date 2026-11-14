#!/usr/bin/env bash
# FireCal: install app + API on an Ubuntu server (Hetzner etc.) and connect your Parquet folder.
#
#   curl -fsSL https://raw.githubusercontent.com/Putskev/firecal/main/deploy/setup_server.sh | sudo bash -s -- [DATA_FOLDER] [--domain DOMAIN]
#
#   DATA_FOLDER  folder with your Parquet/CSV files (searched recursively; several: a:b). Without it the script
#                looks for candidate folders and uses one only if it is unambiguous.
#   --domain     domain that points to this server (DNS A record), used for HTTPS.
#   --link-only  connect the data folder (shown in the app: files, rows, dates) but do not load its hotspots yet.
#   --load       load the hotspots of the data folder into the app. The first build runs in the background
#                (the app keeps serving the previous data), roughly 1 min per 15-20 million hotspots.
#                Default: load if there is enough free disk space, otherwise link only.
#
# Safe with other websites on the server: an existing Caddy gets an extra site via 'import', an existing
# nginx/apache is not touched (FireCal then listens on port 8080). Running it again updates everything.
set -euo pipefail

SRC_ARG=""; DOMAIN=""; LOAD=""
while [ $# -gt 0 ]; do
  case "$1" in
    --link-only) LOAD=0; shift ;;
    --load) LOAD=1; shift ;;
    --domain) DOMAIN="${2:-}"; shift 2 ;;
    --domain=*) DOMAIN="${1#*=}"; shift ;;
    *) SRC_ARG="$1"; shift ;;
  esac
done
REPO="https://github.com/Putskev/firecal.git"
DIR=/opt/firecal
ENVF=/etc/firecal.env
say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m%s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31mERROR: %s\033[0m\n' "$*"; exit 1; }
prev() { grep -E "^$1=" "$ENVF" 2>/dev/null | cut -d= -f2- || true; }

[ "$(id -u)" = 0 ] || die "please run with sudo"
. /etc/os-release
case "${ID:-}" in ubuntu|debian) ;; *) die "tested on Ubuntu/Debian only (found ${ID:-?})" ;; esac
[ -n "$DOMAIN" ] || DOMAIN="$(prev FIRECAL_DOMAIN)"
[ -n "$LOAD" ] || LOAD="$(prev FIRECAL_SOURCE_LOAD)"

say "1/7 System packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq git python3 python3-venv python3-pip curl ca-certificates gnupg debian-keyring debian-archive-keyring apt-transport-https openssl iproute2 >/dev/null
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' || die "Python >= 3.10 needed (Ubuntu 22.04 or newer)"

say "2/7 Code (from GitHub) and Python environment"
git config --global --add safe.directory "$DIR"
if [ -d "$DIR/.git" ]; then
  git -C "$DIR" fetch -q origin && git -C "$DIR" reset -q --hard origin/main
else
  git clone -q --depth 1 "$REPO" "$DIR"
fi
git -C "$DIR" log --oneline -1
python3 -m venv "$DIR/.venv"
"$DIR/.venv/bin/pip" install -q --upgrade pip
"$DIR/.venv/bin/pip" install -q -r "$DIR/requirements.txt"

say "3/7 Data folder"
SRC=""
if [ -n "$SRC_ARG" ]; then
  IFS=':' read -ra PARTS <<< "$SRC_ARG"
  for p in "${PARTS[@]}"; do [ -d "$p" ] || die "folder not found: $p"; SRC="${SRC:+$SRC:}$(realpath "$p")"; done
else
  SRC="$(prev FIRECAL_SOURCE)"
  if [ -z "$SRC" ]; then
    echo "no folder given, looking for Parquet files with hotspot columns ..."
    SRC="$("$DIR/.venv/bin/python" - <<'PY'
import os, sys
sys.path.insert(0, "/opt/firecal")
from backend import sources
roots = [r for r in ["/root", "/home", "/srv", "/data", "/mnt", "/opt", "/var/lib"] if os.path.isdir(r)]
found = {}
for r in roots:
    for root, dirs, files in os.walk(r):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in ("node_modules", "firecal", "site-packages", "docker", "containerd")]
        for f in files:
            if f.endswith(".parquet") and not f.startswith(sources.SKIP_PREFIXES):
                p = os.path.join(root, f)
                cols = sources._columns(p)
                if {"latitude", "longitude"} <= cols and ({"datetime_utc", "acq_date"} & cols):
                    top = root
                    while os.path.basename(top).split("=")[0] in ("year", "month", "y", "m"):
                        top = os.path.dirname(top)
                    found[top] = found.get(top, 0) + 1
# merge sub folders into their common parent
tops = sorted(found)
merged = {}
for t in tops:
    parent = next((m for m in merged if t.startswith(m + os.sep)), None)
    if parent: merged[parent] += found[t]
    else: merged[t] = found[t]
for t, n in merged.items():
    print(f"  candidate: {t} ({n} hotspot files)", file=sys.stderr)
if len(merged) == 1:
    print(next(iter(merged)))
PY
)"
    if [ -n "$SRC" ]; then echo "using: $SRC"; else warn "no unique data folder found: using only the data in the repo. Run again with the folder as argument."; fi
  fi
fi
echo "FIRECAL_SOURCE=${SRC:-<none>}"

# service user: 'firecal', or the owner of the data folder if 'firecal' cannot read it
id firecal >/dev/null 2>&1 || useradd --system --create-home --home-dir /var/lib/firecal --shell /usr/sbin/nologin firecal
RUN_USER=firecal
if [ -n "$SRC" ]; then
  FIRST="${SRC%%:*}"
  if ! runuser -u firecal -- test -r "$FIRST" -a -x "$FIRST"; then
    RUN_USER="$(stat -c %U "$FIRST")"; echo "user 'firecal' cannot read $FIRST, the service runs as its owner: $RUN_USER"
  fi
fi
chown -R "$RUN_USER": "$DIR"

if [ -n "$SRC" ]; then
  set +e
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
import shutil
mem = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") / 1e9
free = shutil.disk_usage("/opt/firecal").free / 1e9
need = 3 * size + 1
minutes = max(1, round(rows / 20e6 * 1.2))
print(f"RAM: {mem:.1f} GB (the build works month by month with a memory limit, RAM is not the limit)")
print(f"disk: {free:.0f} GB free, about {need:.0f} GB needed while building; build time roughly {minutes} min")
if free < need:
    print("Not enough free disk space to load the folder.")
    sys.exit(3)
PY
  RC=$?
  set -e
  if [ -z "$LOAD" ]; then
    if [ "$RC" = 3 ]; then LOAD=0; else LOAD=1; fi
  fi
  if [ "$LOAD" = 0 ]; then
    warn "Data folder is LINKED ONLY: the app shows what is in it, but uses the data in the repo. Load later with --load."
  else
    echo "Data folder will be LOADED into the app (built in the background, see: journalctl -u firecal -f)."
  fi
fi
LOAD="${LOAD:-1}"

say "4/7 Web server"
IP="$(curl -fsS4 --max-time 5 https://api.ipify.org || hostname -I | awk '{print $1}')"
holder() { ss -ltnpH "( sport = :$1 )" 2>/dev/null | grep -o 'users:(("[^"]*' | head -1 | cut -d'"' -f2 || true; }
H80="$(holder 80)"; H443="$(holder 443)"
MODE=caddy; BIND=127.0.0.1; PORT=8000
for h in "$H80" "$H443"; do
  if [ -n "$h" ] && [ "$h" != caddy ]; then MODE=direct; fi
done
if [ "$MODE" = direct ]; then
  warn "ports 80/443 are used by '${H80:-$H443}' (another website?). Not touching it: FireCal runs on port 8080."
  BIND=0.0.0.0; PORT=8080
fi
if [ -n "$DOMAIN" ]; then
  DIP="$(getent ahostsv4 "$DOMAIN" | awk 'NR==1{print $1}' || true)"
  if [ "$DIP" != "$IP" ]; then warn "$DOMAIN points to ${DIP:-nothing}, this server is $IP: HTTPS for the domain may fail."; fi
fi

say "5/7 Service (systemd) + automatic updates"
TOKEN="$(prev FIRECAL_RELOAD_TOKEN)"; [ -n "$TOKEN" ] || TOKEN="$(openssl rand -hex 16)"
cat > "$ENVF" <<EOF
FIRECAL_SOURCE=$SRC
FIRECAL_SOURCE_LOAD=$LOAD
FIRECAL_RELOAD_TOKEN=$TOKEN
FIRECAL_RUN_USER=$RUN_USER
FIRECAL_DOMAIN=$DOMAIN
FIRECAL_PORT=$PORT
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
ExecStart=$DIR/.venv/bin/uvicorn backend.main:app --host $BIND --port $PORT --proxy-headers
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

say "6/7 HTTPS"
URLS=()
if [ "$MODE" = caddy ]; then
  if ! command -v caddy >/dev/null; then
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
    curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' > /etc/apt/sources.list.d/caddy-stable.list
    apt-get update -qq && apt-get install -y -qq caddy >/dev/null
    printf '# Caddy main config\n' > /etc/caddy/Caddyfile     # replace the default welcome page
  fi
  SSLIP="${IP//./-}.sslip.io"
  HOSTS="$SSLIP"
  # use the domain only if no other site in the Caddy config already serves it
  if [ -n "$DOMAIN" ] && ! grep -rqs --exclude=firecal.caddy --exclude='*.before-firecal' -E "(^|[ ,])(https?://)?$DOMAIN([ ,:{]|$)" /etc/caddy/; then
    HOSTS="$DOMAIN, $SSLIP"
  elif [ -n "$DOMAIN" ]; then
    warn "$DOMAIN is already used by another site in Caddy: FireCal gets https://$SSLIP instead."
  fi
  cat > /etc/caddy/firecal.caddy <<EOF
# FireCal (written by /opt/firecal/deploy/setup_server.sh)
$HOSTS {
	encode gzip
	reverse_proxy 127.0.0.1:$PORT
}
http://$IP {
	encode gzip
	reverse_proxy 127.0.0.1:$PORT
}
EOF
  [ -f /etc/caddy/Caddyfile ] || printf '# Caddy main config\n' > /etc/caddy/Caddyfile
  cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.before-firecal
  grep -qs 'import /etc/caddy/firecal.caddy' /etc/caddy/Caddyfile || printf '\nimport /etc/caddy/firecal.caddy\n' >> /etc/caddy/Caddyfile
  if caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile >/tmp/caddy-validate.log 2>&1; then
    systemctl enable -q caddy; systemctl reload caddy 2>/dev/null || systemctl restart caddy
    for h in ${HOSTS//,/ }; do URLS+=("https://$h"); done
    URLS+=("http://$IP")
  else
    cp /etc/caddy/Caddyfile.before-firecal /etc/caddy/Caddyfile
    warn "Caddy config invalid, restored the old one (details: /tmp/caddy-validate.log). FireCal: port 8000 locally only."
  fi
  PORTS="80 443"
else
  URLS+=("http://${DOMAIN:-$IP}:$PORT")
  PORTS="$PORT"
fi
if command -v ufw >/dev/null && ufw status | grep -q "Status: active"; then
  for p in $PORTS; do ufw allow "$p/tcp" >/dev/null; done; echo "ufw: opened $PORTS"
fi

say "7/7 Start and first data build (a few minutes with large data)"
for _ in $(seq 1 240); do
  if curl -fsS --max-time 3 "http://127.0.0.1:$PORT/api/health" 2>/dev/null | grep -q '"ok":true'; then break; fi
  if ! systemctl is-active -q firecal; then journalctl -u firecal -n 30 --no-pager; die "service stopped, see log above"; fi
  sleep 5
done
META="$(curl -fsS --max-time 10 "http://127.0.0.1:$PORT/api/meta" || true)"
[ -n "$META" ] || { journalctl -u firecal -n 30 --no-pager; die "API does not answer yet. Follow the log: journalctl -u firecal -f"; }
python3 -c 'import json,sys; m=json.loads(sys.argv[1]); print("data: {:,} hotspots, {} -> {}, version {}".format(m["rows"], m["first_date"], m["last_date"], m["data_version"]))' "$META"

echo
printf '\033[1;32mFireCal runs.\033[0m Links for your friends:\n'
for u in "${URLS[@]}"; do echo "   $u"; done
echo
echo "HTTPS certificates are fetched automatically on the first visit (up to a minute)."
echo "If a link does not open from outside: allow the ports ($PORTS) in the Hetzner Cloud firewall."
echo "Log: journalctl -u firecal -f   |   new data files are picked up within 15 minutes"
