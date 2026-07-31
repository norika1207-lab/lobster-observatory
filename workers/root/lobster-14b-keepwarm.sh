#!/bin/bash
set -euo pipefail
while true; do
  curl -sS --max-time 120 http://100.118.221.12:11434/api/generate \
    -H 'Content-Type: application/json' \
    -d '{"model":"lobster-zh-chat-v2","prompt":"keep warm。只輸出：好。","stream":false,"keep_alive":"24h","options":{"num_predict":1,"temperature":0.1}}' \
    >/tmp/lobster-14b-keepwarm.last 2>/tmp/lobster-14b-keepwarm.err || true
  sleep 60
done
