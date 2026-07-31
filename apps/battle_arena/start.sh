#!/bin/bash
cd /opt/battle_arena
pkill -f "uvicorn.*8004" 2>/dev/null
sleep 1
set -a
[ -f /opt/battle_arena/.env ] && source /opt/battle_arena/.env
set +a
setsid nohup /opt/battle_arena/venv/bin/python -m uvicorn app.main:app \
  --host 0.0.0.0 --port 8004 --log-level info \
  < /dev/null > /opt/battle_arena/logs/arena.log 2>&1 &
disown
echo "started (pid $!)"
