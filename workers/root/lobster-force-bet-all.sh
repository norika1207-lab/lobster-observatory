#!/usr/bin/env bash
set -euo pipefail

LOG_DIR="/var/log/lobster-agents"
mkdir -p "$LOG_DIR"
LOCK_FILE="/run/lobster-force-bet-all.lock"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "[$(date -Is)] force-bet-all skip: another run is active" >> "$LOG_DIR/force-bet-all.log"
  exit 0
fi
DB_LOCK_FILE="/run/lobster-worker-guard/global-db-writer.lock"
mkdir -p "$(dirname "$DB_LOCK_FILE")"
exec 8>"$DB_LOCK_FILE"
if ! flock -w 240 8; then
  echo "[$(date -Is)] force-bet-all skip: global DB writer lock timeout" >> "$LOG_DIR/force-bet-all.log"
  exit 0
fi

AGENTS_WAS_ACTIVE="$(systemctl is-active lobster-agents.service 2>/dev/null || true)"
cleanup() {
  if [[ "${AGENTS_WAS_ACTIVE:-}" == "active" ]]; then
    systemctl start lobster-agents.service >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

if [[ "$AGENTS_WAS_ACTIVE" == "active" ]]; then
  echo "[$(date -Is)] force-bet-all pausing lobster-agents.service to avoid memory overwrite" >> "$LOG_DIR/force-bet-all.log"
  systemctl stop lobster-agents.service >/dev/null 2>&1 || true
fi

if [[ -f /opt/battle_arena/.env ]]; then
  set -a
  # shellcheck disable=SC1091
  source /opt/battle_arena/.env
  set +a
fi

export LOBSTER_FORCE_BET_ALL=1
export LOBSTER_FORCE_ACTIVITY="${LOBSTER_FORCE_ACTIVITY:-bet_only}"
export LOBSTER_AGENT_MOLTBOOK_ENABLED="${LOBSTER_AGENT_MOLTBOOK_ENABLED:-0}"
export LOBSTER_NO_POST="${LOBSTER_NO_POST:-1}"

PERSONA_TOTAL="${LOBSTER_FORCE_PERSONA_TOTAL:-30}"
BATCH_SIZE="${LOBSTER_FORCE_BATCH_SIZE:-5}"
TIMEOUT_SECONDS="${LOBSTER_FORCE_TIMEOUT_SECONDS:-120}"
NODE_BIN="${NODE_BIN:-node}"
AGENT_JS="${AGENT_JS:-/root/lobster-agent.js}"
LOG_FILE="$LOG_DIR/force-bet-all.log"

echo "[$(date -Is)] force-bet-all start total=$PERSONA_TOTAL batch=$BATCH_SIZE activity=$LOBSTER_FORCE_ACTIVITY" >> "$LOG_FILE"

start=0
while [[ "$start" -lt "$PERSONA_TOTAL" ]]; do
  end=$((start + BATCH_SIZE))
  pids=()
  for ((i=start; i<end && i<PERSONA_TOTAL; i++)); do
    (
      export PERSONA_INDEX="$i"
      timeout "$TIMEOUT_SECONDS" "$NODE_BIN" "$AGENT_JS"
    ) >> "$LOG_FILE" 2>&1 &
    pids+=("$!")
  done
  for pid in "${pids[@]}"; do
    wait "$pid" || true
  done
  start="$end"
done

echo "[$(date -Is)] force-bet-all done" >> "$LOG_FILE"
