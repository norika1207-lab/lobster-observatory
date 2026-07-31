#!/bin/bash
# 台股開盤觸發器 — 09:00 CST (01:00 UTC)
set -a
. /opt/battle_arena/.env
set +a

LOG="/tmp/lobster-tw-open.log"
echo "[$(date -Is)] 台股開盤！龍蝦全體進場 TW 市場" >> "$LOG"

# 刷新 JWT tokens
python3 -c "
import jwt, json
from datetime import datetime, timedelta, timezone
SECRET='1d5aea0d4aa78cdb7cf89e61e74b81ced211fe9cfb91e6d5d343c82fda77961d'
LOBSTERS=['norika_oda','clawtrix','pincerbot','ragclaw','snaplex','hexclaw','vortexiq',
          'prismbit','blazepaw','stonefang','emberling','sparkrill','gauntlet','rushfin',
          'nullpinch','brineledger','omenwake','mirrorcalm','flarecub','basaltrook','elder']
expire = datetime.now(timezone.utc) + timedelta(hours=24)
tokens = {n: jwt.encode({'sub': n, 'exp': expire}, SECRET, algorithm='HS256') for n in LOBSTERS}
json.dump(tokens, open('/tmp/lobster_stock_tokens.json','w'))
print('tokens refreshed')
" 2>/dev/null

# 執行台股買入
LOBSTER_MARKET_CONTEXT='["TW"]' node /root/lobster-stock-agent.js >> "$LOG" 2>&1
echo "[$(date -Is)] 台股買入完成" >> "$LOG"
