#!/usr/bin/env bash
TOWN=/root/_town/growth
export PATH=/usr/bin:/usr/local/bin:$PATH
export HOME=/root
python3 /root/lobster-mayor/growth_monitor.py >> "$TOWN/monitor.log" 2>&1
PROMPT="Read /root/_town/growth/MAYOR_GROWTH_BRIEF.md then /root/_town/growth/brief.json, and carry out your standing orders within every stated limit. You may only use the growth_actions.py tool plus Read. You run on the Max subscription, never any paid API. Keep it brief."
cd /root/lobster-mayor/growth-agent
echo "===== WAKE $(date "+%F %T") =====" >> "$TOWN/wake.log"
timeout 600 claude -p "$PROMPT" >> "$TOWN/wake.log" 2>&1
echo "===== DONE $(date "+%F %T") rc=$? =====" >> "$TOWN/wake.log"
