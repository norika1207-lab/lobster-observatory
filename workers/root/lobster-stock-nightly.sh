#!/bin/bash
# 分時調度:深夜跑 stock 深度研究,跑完務必恢復 lounge (norika 2026-06-07)
# 設計原則:不管 stock 成敗,trap EXIT 一定把 lounge 救回來,防 silent failure 重演。
set -uo pipefail
LOG=/tmp/lobster-stock-nightly.log
OLLAMA=http://100.121.29.3:8080
echo "[$(date '+%F %T')] ========== nightly stock START ==========" >> "$LOG"

# ── trap:無論如何結束都恢復 lounge ──────────────────────────────
restore_lounge() {
  echo "[$(date '+%F %T')] >>> restoring lounge..." >> "$LOG"
  systemctl start lobster-14b-keepwarm lobster-conversation >> "$LOG" 2>&1
  sleep 18
  local st=$(systemctl is-active lobster-conversation 2>/dev/null)
  echo "[$(date '+%F %T')] lounge service = $st" >> "$LOG"
  # 健康檢查:5 分鐘內 lounge 有沒有寫入,沒有就再踢一次
  sleep 90
  local n=$(/usr/bin/sqlite3 /opt/battle_arena/app/battle_arena.db \
    "SELECT COUNT(*) FROM lounge_messages WHERE created_at > strftime('%s','now','-3 minutes') AND COALESCE(sys,0)=0;" 2>/dev/null)
  echo "[$(date '+%F %T')] lounge 3min msgs = ${n:-?}" >> "$LOG"
  if [ "${n:-0}" = "0" ]; then
    echo "[$(date '+%F %T')] !!! lounge silent, force restart again" >> "$LOG"
    systemctl restart lobster-conversation >> "$LOG" 2>&1
  fi
  echo "[$(date '+%F %T')] ========== nightly stock END ==========" >> "$LOG"
}
trap restore_lounge EXIT

# ── 1. 釋放 ollama:停 lounge + keepwarm ─────────────────────────
echo "[$(date '+%F %T')] stopping lounge to free GPU..." >> "$LOG"
systemctl stop lobster-conversation lobster-14b-keepwarm >> "$LOG" 2>&1
sleep 6

# ── 2. 確保 chat-v2 載入 (stock 報告也用它) ──────────────────────
echo "[$(date '+%F %T')] warming chat-v2..." >> "$LOG"
curl -sS --max-time 200 "$OLLAMA/api/generate" \
  -d '{"model":"lobster-zh-chat-v2","prompt":"預熱","stream":false,"keep_alive":"2h","options":{"num_predict":2}}' \
  >> "$LOG" 2>&1

# ── 3. 跑 stock 深度研究 (獨佔 GPU,最多 45 分鐘) ────────────────
echo "[$(date '+%F %T')] running stock scheduler..." >> "$LOG"
SERVICE_KEY=$(grep -E '^SERVICE_KEY=' /opt/battle_arena/.env | head -1 | cut -d= -f2)
export SERVICE_KEY
export LOBSTER_QWEN_API_BASE="$OLLAMA/v1"
export LOBSTER_QWEN_MODEL=lobster-zh-chat-v2
timeout 2700 /usr/bin/node /root/lobster-stock-scheduler.js >> /tmp/lobster-stock-scheduler.log 2>&1
echo "[$(date '+%F %T')] scheduler exit=$?" >> "$LOG"

# ── 4. 真實股價結算 ─────────────────────────────────────────────
echo "[$(date '+%F %T')] real settlement..." >> "$LOG"
timeout 400 /usr/bin/python3 /root/lobster_real_settle.py >> /tmp/lobster-stock-settle.log 2>&1
echo "[$(date '+%F %T')] settle exit=$?" >> "$LOG"

# trap restore_lounge 會在這裡自動執行
