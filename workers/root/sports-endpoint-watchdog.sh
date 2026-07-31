#!/usr/bin/env bash
set -euo pipefail

LOG_DIR="/var/log/lobster-agents"
LOG_FILE="$LOG_DIR/sports-endpoint-watchdog.log"
mkdir -p "$LOG_DIR"

UA="Mozilla/5.0 SportsEndpointWatchdog/1.0"

log() {
  echo "[$(date -Is)] $*" >> "$LOG_FILE"
}

check_url() {
  local name="$1"
  local url="$2"
  local min_size="${3:-20}"
  local out code size
  out="$(curl -k -L -sS --max-time 15 -A "$UA" -o /tmp/sports_watchdog.out -w '%{http_code} %{size_download}' "$url" 2>&1 || true)"
  code="$(awk '{print $1}' <<<"$out")"
  size="$(awk '{print $2}' <<<"$out")"
  if [[ "$code" != "200" || -z "$size" || "$size" -lt "$min_size" ]]; then
    log "FAIL $name code=${code:-none} size=${size:-0} url=$url err=$out"
    return 1
  fi
  log "OK $name code=$code size=$size"
  return 0
}

restart_knocknix() {
  local pid
  pid="$(ss -ltnp | sed -n 's/.*:8005 .*pid=\([0-9][0-9]*\).*/\1/p' | head -1)"
  if [[ -n "$pid" ]]; then
    kill "$pid" || true
    sleep 2
  fi
  (cd /opt/fighting_club && nohup ./venv/bin/python main.py > nohup.out 2>&1 &)
  log "RESTART knocknix raw python"
}

check_url "turfenix-health" "https://charenix.com/turfenix/api/health" 40 || systemctl restart turfenix.service || true
check_url "turfenix-current" "https://charenix.com/turfenix/api/current-race" 200 || systemctl restart turfenix.service || true

check_url "throttenix-races" "https://charenix.com/throttenix/api/races" 100 || systemctl restart racing.service || true
check_url "throttenix-current" "https://charenix.com/throttenix/api/current-race" 100 || systemctl restart racing.service || true

check_url "ragnovex-matches" "https://charenix.com/ragnovex/api/matches" 100 || systemctl restart esports.service || true
check_url "pitchnix-matches" "https://charenix.com/pitchnix/api/matches" 100 || systemctl restart battnix.service || true
check_url "dunknix-matches" "https://charenix.com/dunknix/api/matches" 100 || systemctl restart dunknix.service || true
check_url "knocknix-next" "https://charenix.com/knocknix/api/fight/next" 100 || restart_knocknix

exit 0
