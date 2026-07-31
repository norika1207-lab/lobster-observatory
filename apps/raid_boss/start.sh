#!/bin/bash
cd /opt/raid_boss
pkill -f "uvicorn.*8015" 2>/dev/null
sleep 1
set -a
[ -f /opt/raid_boss/.env ] && source /opt/raid_boss/.env
set +a
setsid nohup /opt/raid_boss/venv/bin/python -m uvicorn app.main:app \
  --host 0.0.0.0 --port 8015 --log-level info \
  < /dev/null > /opt/raid_boss/logs/raid.log 2>&1 &
disown
echo "started (pid $!)"
