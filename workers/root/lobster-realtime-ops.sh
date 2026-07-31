#!/usr/bin/env bash
set -euo pipefail

LOG_DIR="/var/log/lobster-agents"
RUN_DIR="/run/lobster-ops"
LOG_FILE="$LOG_DIR/realtime-ops.log"
STATUS_FILE="$LOG_DIR/realtime-ops-status.json"
LOCK_FILE="$RUN_DIR/realtime-ops.lock"

mkdir -p "$LOG_DIR" "$RUN_DIR"

exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "[$(date -Is)] skip: previous realtime ops cycle still running" >> "$LOG_FILE"
  exit 0
fi

NOW_EPOCH="$(date +%s)"

log() {
  echo "[$(date -Is)] $*" >> "$LOG_FILE"
}

unit_state() {
  systemctl is-active "$1" 2>/dev/null || true
}

start_unit_once() {
  local unit="$1"
  local state
  state="$(unit_state "$unit")"
  if [[ "$state" == "active" || "$state" == "activating" ]]; then
    log "skip unit=$unit state=$state"
    return 0
  fi
  if systemctl start --no-block "$unit" >/dev/null 2>&1; then
    log "start unit=$unit"
  else
    log "fail start unit=$unit"
  fi
}

run_rate_limited() {
  local key="$1"
  local min_seconds="$2"
  local timeout_seconds="$3"
  shift 3

  local stamp="$RUN_DIR/${key}.stamp"
  local last=0
  if [[ -f "$stamp" ]]; then
    last="$(cat "$stamp" 2>/dev/null || echo 0)"
  fi

  if (( NOW_EPOCH - last < min_seconds )); then
    log "skip key=$key rate_limit_remaining=$(( min_seconds - (NOW_EPOCH - last) ))s"
    return 0
  fi

  echo "$NOW_EPOCH" > "$stamp"
  log "run key=$key command=$*"
  if timeout "$timeout_seconds" "$@" >> "$LOG_FILE" 2>&1; then
    log "ok key=$key"
  else
    log "fail key=$key"
  fi
}

write_status() {
  local tmp="${STATUS_FILE}.tmp"
  cat > "$tmp" <<JSON
{
  "updated_at": "$(date -Is)",
  "sports_endpoint_watchdog": "$(unit_state sports-endpoint-watchdog.service)",
  "esports_sync": "$(unit_state esports-sync.service)",
  "esports_live_watch": "$(unit_state esports-live-watch.service)",
  "cross_site_settlement": "$(unit_state lobster-cross-site-settlement.service)",
  "force_settle": "$(unit_state lobster-force-settle.service)",
  "force_bet_all": "$(unit_state lobster-force-bet-all.service)",
  "stock_settle": "$(unit_state lobster-stock-settle.service)",
  "turfenix": "$(unit_state turfenix.service)",
  "throttenix": "$(unit_state racing.service)",
  "ragnovex": "$(unit_state esports.service)",
  "knocknix": "$(unit_state knocknix.service)",
  "pitchnix": "$(unit_state battnix.service)",
  "dunknix": "$(unit_state dunknix.service)"
}
JSON
  mv "$tmp" "$STATUS_FILE"
}

log "cycle start"

# 1) Endpoint health: every minute. This catches broken public URLs fast.
start_unit_once sports-endpoint-watchdog.service

# 2) Match ingestion: every minute, but never overlap a still-running sync.
start_unit_once esports-sync.service
start_unit_once esports-live-watch.service

# 3) Settlements: cross-site worker every minute; heavier orchestrator every 5 minutes.
start_unit_once lobster-cross-site-settlement.service
run_rate_limited settlement_orchestrator 300 60 /bin/systemctl start --no-block lobster-settlement-orchestrator.service
run_rate_limited settlement_health 300 60 /bin/systemctl start --no-block lobster-settlement-health.service

# 4) Direct settle fallbacks. These are rate-limited to avoid DB/API stomping.
run_rate_limited force_settle 300 60 /bin/systemctl start --no-block lobster-force-settle.service
run_rate_limited stock_settle 900 60 /bin/systemctl start --no-block lobster-stock-settle.service

# 5) Force betting cadence. This makes the lobsters keep attempting bets without overlap.
if [[ "$(unit_state lobster-force-bet-all.service)" == "active" || "$(unit_state lobster-force-bet-all.service)" == "activating" ]]; then
  log "skip key=force_bet_all state=$(unit_state lobster-force-bet-all.service)"
else
  log "skip key=force_bet_all disabled_after_copy_template_incident_20260706"
fi

write_status
log "cycle done"
