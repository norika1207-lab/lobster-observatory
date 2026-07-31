#!/bin/bash
set -a
. /opt/battle_arena/.env
set +a

while true; do
  timeout 70s node /root/lobster-channel-coverage-worker.js >> /tmp/lobster-channel-coverage-worker.log 2>&1
  sleep 90
done
