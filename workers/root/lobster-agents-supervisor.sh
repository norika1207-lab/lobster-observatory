#!/bin/bash
set -euo pipefail

set -a
. /opt/battle_arena/.env
set +a
# 2026-06-02: agents migrated off the dead qwen2.5:3b @ 18034 laptop tunnel.
# Target = 127.0.0.1:18044 (lobster-zh-chat-v2, 14.8B 繁體 QLoRA, kept warm by
# lobster-llm-warmth.service). Force-override beats .env. Same model as
# lobster-conversation/lobster-lounge. 3b retired, no fallback path remains.
export LOBSTER_LOUNGE_LLM=qwen
export LOBSTER_QWEN_API_BASE=http://100.118.221.12:11434/v1
export LOBSTER_QWEN_MODEL=lobster-zh-chat-v2
export LOBSTER_QWEN_HIGH_MODEL=lobster-zh-chat-v2
export LOBSTER_QWEN_MAX_MODEL=lobster-zh-chat-v2
export DASHSCOPE_API_KEY=${DASHSCOPE_API_KEY:-local-llm-no-auth}
export QWEN_API_KEY=${QWEN_API_KEY:-local-llm-no-auth}
export LOBSTER_LOCAL_LLM_URL=http://127.0.0.1:18044/v1
export LOBSTER_LOCAL_LLM_MODEL=lobster-zh-chat-v2
export LOBSTER_LOCAL_LLM_KIND=openai
# 2026-05-27 stop-loss (REVERTED 2026-06-02): the flattening was caused by the
# weak 3b on a dead 18034 tunnel, not by brain-speech itself. Now on 14.8B
# lobster-zh-chat-v2 @ 18044, brain-speech is re-enabled and passes attempt=1.
export LOBSTER_BRAIN_SPEECH=1
export LOBSTER_BRAIN_MAX_REGENS=1
export LOBSTER_LOCAL_REGEN_ATTEMPTS=0
export LOBSTER_DUMP_SPEECH_PROMPT=1
export LOBSTER_QWEN_TIMEOUT_MS=300000
export LOBSTER_AGENT_START_SPACING_SEC=${LOBSTER_AGENT_START_SPACING_SEC:-3}
export LOBSTER_AGENT_LOOP_SLEEP_SEC=${LOBSTER_AGENT_LOOP_SLEEP_SEC:-20}
# MoltBook external posting is single-writer: /root/lobster-moltbook-orchestrator.js only.
export LOBSTER_NO_POST=1
export LOBSTER_AGENT_MOLTBOOK_ENABLED=0

mkdir -p /var/log/lobster-agents
echo "$(date -Is) lobster agents supervisor starting: rotating all 31 personas" >> /var/log/lobster-agents/supervisor.log

shutdown() {
  set +e
  echo "$(date -Is) lobster agents supervisor stopping" >> /var/log/lobster-agents/supervisor.log
  pids="$(jobs -pr)"
  if [ -n "$pids" ]; then
    kill $pids 2>/dev/null || true
  fi
  wait 2>/dev/null || true
  exit 0
}
trap shutdown TERM INT

# 2026-07-08: keep the lounge alive without letting agents saturate the VPS or
# battle_arena.db. The previous batch=4 + 2s loop compounded with a legacy
# @reboot launcher and produced duplicate agents plus SQLite lock storms.
AGENT_PARALLEL=${AGENT_PARALLEL:-2}
AGENT_TIMEOUT=${AGENT_TIMEOUT:-45s}
while true; do
  batch_start=0
  while [ "$batch_start" -le 30 ]; do
    pids=()
    k=0
    while [ "$k" -lt "$AGENT_PARALLEL" ]; do
      i=$((batch_start + k))
      [ "$i" -gt 30 ] && break
      (
        echo "$(date -Is) starting persona index $i" >> "/var/log/lobster-agents/agent-${i}.log"
        PERSONA_INDEX=$i timeout -k 10s "$AGENT_TIMEOUT" node /root/lobster-agent.js >> "/var/log/lobster-agents/agent-${i}.log" 2>&1 || true
      ) &
      pids+=($!)
      sleep "$LOBSTER_AGENT_START_SPACING_SEC"
      k=$((k + 1))
    done
    wait "${pids[@]}" 2>/dev/null || true
    batch_start=$((batch_start + AGENT_PARALLEL))
  done
  sleep "$LOBSTER_AGENT_LOOP_SLEEP_SEC"
done
