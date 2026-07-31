# 🚫 Battle Arena 開發紅線

## 這個服務的身分
- 獨立 port: 8004
- 獨立目錄: /opt/battle_arena/
- 獨立 venv: /opt/battle_arena/venv/
- 用途: 投資人 demo 用的跨運動卡牌對戰原型

## ⛔ 絕對禁止動到以下系統

### Port 禁區
- 8000 (CSM 股市模擬交易所)
- 8001 (賽馬模擬 Turfenix)
- 8002 (PitLane F1 賽車模擬)
- 8003 (電競比賽模擬)

### 目錄禁區(只讀不寫)
- /opt/trading/          (CSM)
- /opt/racing/           (PitLane / 賽馬 / 電競相關)
- /etc/nginx/            (Nginx 設定)
- /etc/systemd/system/   (系統服務)

### 資料庫禁區
- 不動任何 MongoDB / PostgreSQL / Redis 的現有 database
- Battle Arena 不需要資料庫,戰鬥狀態純記憶體

### 共用模組禁區
- 不去 import /opt/trading/* 或 /opt/racing/* 裡的任何模組
- 如需要類似功能,在 /opt/battle_arena/ 內自己寫一份
- Battle Arena 完全自給自足

## ✅ 允許的操作
- /opt/battle_arena/ 內任何檔案的讀寫
- 用 /opt/battle_arena/venv/bin/pip 安裝套件
- 佔用 port 8004
- 讀取公開 CDN 上的圖片(外部 URL)

## 若要掛 Nginx 反向代理
暫時不做。先用 http://31.97.221.240:8004/ 直接訪問。
拿到投資後再做 nginx 路由整合。

## 失敗模式必須避免
1. 不小心 kill 掉 8000-8003 的其中一個 process
2. 不小心改到 /opt/trading/ 或 /opt/racing/ 下的檔案
3. 不小心污染到其他服務的 Python 環境
4. 不小心在 systemd 裡建立會影響其他服務的 unit

---

## 🌐 Charenix 是英文網站 — 絕對不能有中文 UI

**所有 lobby.html / arena.html 的可見文字必須全部英文。**
後端資料庫儲存中文（卡名、AI 名、天氣），前端必須映射成英文再顯示。

### AI 戰士名對照表（`static/lobby.html` → `AI_NAMES` 常數）

| strategy key | 英文顯示 |
|---|---|
| berserker | ⚔️ Berserker |
| fortress | 🛡️ Iron Fortress |
| drainer | 🫙 Soul Drainer |
| weatherman | 🌪️ Storm Caller |
| assassin | 🗡️ Assassin |
| gambler | 🎲 Gambler |
| counter | 🔄 Counter |
| healer | 💚 Immortal |
| tactician | 🧠 Tactician |
| veteran | 🌟 Veteran |

用法：`aiName(r.strategy_a)` — 不要直接用 `r.name_a`（中文）

### 天氣名對照表（`static/lobby.html` → `WEATHER_FLAVOR` 常數）

| weather.id | 英文顯示 |
|---|---|
| lava | 🌋 Lava Typhoon |
| sunny | ☀️ Clear Sky |
| tremor | 💫 Tremor |
| blizzard | ❄ Blizzard |
| typhoon | 🌊 Tsunami |
| thunder | ⚡ Thunder |
| earthquake | 🗻 Earthquake |
| ember | 🌫 Ember Ash |

用法：`WEATHER_FLAVOR[weather.id]` — 不要用 `weather.cn`（中文）

### 卡牌名稱
- `card.name_short` 可能是中文 — 用 `card.id`（如 `MLB_AROZARENA`）顯示
- Ticker 裡已改為使用 card ID

### Join 錯誤訊息
- 未登入：`'Please log in to join a battle'`
- 已過期：`'Session expired — please log in again'`
- 房間已滿：`'Room already taken — please choose another'`
- 自己的房：`'You cannot join your own room'`

### 新增 UI 文字的規則
1. 只能英文
2. 後端新增策略 → 同步更新 `AI_NAMES` 對照表
3. 後端新增天氣 → 同步更新 `WEATHER_FLAVOR` 對照表
