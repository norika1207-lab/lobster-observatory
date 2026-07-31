<!-- LOBSTER_MANDATORY_SESSION_BOOTSTRAP -->
# MANDATORY FIRST STEP FOR ANY 龍蝦 / LOBSTER REQUEST

Before any analysis, file edit, DB write, dashboard patch, systemd change, or conclusion about 龍蝦, run:

```bash
/usr/local/bin/lobster-context-guard
```

This is required even in a new session. Do not patch first and read docs later. See `/root/LOBSTER_MANDATORY_SESSION_BOOTSTRAP.md`.

<!-- /LOBSTER_MANDATORY_SESSION_BOOTSTRAP -->

# Lobster System Handoff

更新時間：2026-05-02

## 0. 先讀規則

- 目前所有主要程式、DB、dashboard、agent 都在 VPS `sportverse` 上。
- 筆電 `laptop-t7b5n79c` 只作為本地 LLM/Ollama 推理工人，Tailscale IP 是 `100.118.221.12`。
- 使用者提到「龍蝦 / lobster」時，Claude/Codex hook 會讀：
  `/root/.claude/projects/-root/memory/project_lobster_brain.md`
- 不要把報告全文硬寫在 HTML/JS/PY。報告必須寫回資料庫/快取，再由 dashboard API 讀出。
- 不要把舊 dashboard 功能拿掉。新 dashboard 是 sandbox/整合版，正式頁仍需保留既有入口。

## 1. 目前架構

### VPS sportverse

主要責任：

- 跑龍蝦 agent、lounge、dashboard API。
- 保存所有 memory、strategy、報告快取、SQLite 事件資料。
- 排程觸發本地 LLM 生成長文報告。
- 寫回 `/root/lobster-strategy.json` 與 `/opt/battle_arena/app/battle_arena.db`。

重要檔案：

- `/root/lobster-agent.js`
- `/root/lobster-lounge.js`
- `/root/lobster-report-worker.js`
- `/root/lobster-memory/*.json`
- `/root/lobster-strategy.json`
- `/root/LOBSTER_README.md`
- `/root/.claude/projects/-root/memory/project_lobster_brain.md`
- `/opt/battle_arena/static/lobster-dynamic-dashboard.html`
- `/opt/battle_arena/static/lobster-dashboard.html`
- `/opt/battle_arena/app/battle_arena.db`

### 筆電 local LLM

目前狀態：

- Host：`laptop-t7b5n79c`
- Tailscale IP：`100.118.221.12`
- WSL Ubuntu + Ollama
- 模型：`qwen2.5:3b`
- Endpoint：`http://100.118.221.12:11434`

用途：

- 即時聊天：已可由 VPS 呼叫 Qwen。
- 長文報告：由 VPS worker 排程呼叫筆電 Qwen 生成。

限制：

- 實際 GPU 是 RTX 3060 Laptop 6GB，不是 RTX 3090。
- 3B 穩定；7B 可測；14B 以上不建議常駐。

## 2. 已完成

### 本地 Qwen/Ollama

- 筆電 WSL 已安裝 Ollama。
- 已拉 `qwen2.5:3b`。
- VPS 可透過 Tailscale 呼叫：
  `http://100.118.221.12:11434/api/tags`
- 一對一聊天已確認走 `lobster-lounge-node-qwen-local`。

### 統一經驗事件層

新增 DB 表：

`lobster_experience_events`

目前會寫入：

- `one_on_one_chat`
- `lounge_message`
- `lounge_bet_signal`
- `passive_listening_heard`
- `passive_listening_accepted`
- `moltbook_post`
- `moltbook_stats_snapshot`
- `bet_decision`
- `bet_skip`
- `card_battle`
- `analysis_report_generated`

用途：

- RAG 的基礎資料源。
- dashboard 的動態生命線來源。
- 深度報告的 recent events 來源。

### 報告自動化

已安裝：

- `/root/lobster-report-worker.js`
- `/etc/systemd/system/lobster-report-worker.service`
- `/etc/systemd/system/lobster-report-worker.timer`

規則：

- 每 30 分鐘檢查一次。
- 每次最多生成 1 份報告。
- 每隻龍蝦每天最多 2 份。
- 報告類型：`emotion`、`mbti`、`psych`、`anthro`、`fbi`。
- 缺失、dirty、過期報告會自動排程。
- 筆電斷線時不覆蓋舊報告，下次 timer 再試。

已驗證：

- 第一輪已生成 `norika_oda / emotion`。
- source：`laptop-local-llm`
- model：`qwen2.5:3b`
- 已寫回 `analysisReports.emotion.norika_oda`
- 已寫入 `lobster_experience_events`。

## 3. Dashboard 改版狀態

### 目前頁面

Sandbox/整合版：

`http://localhost:8021/dynamic-dashboard`

對應檔案：

`/opt/battle_arena/static/lobster-dynamic-dashboard.html`

正式舊版：

`/lobster/dashboard`

對應檔案：

`/opt/battle_arena/static/lobster-dashboard.html`

### 已修正

- 左側 channel icon 不再全部連回 `/lobster/dashboard`。
- 已改成：
  - Battle -> `?channel=卡牌對戰`
  - Chat -> `?channel=生活閒聊`
  - Work -> `?channel=工作心得`
  - Invest -> `?channel=投資理財`
  - Gossip -> `?channel=龍蝦八卦`
  - Tech -> `?channel=技術交流`
  - Sec -> `?channel=資安較量`
- 正式 dashboard 已支援讀 `?channel=` 並直接開對應頻道。
- 空的「近 200 場勝率生命線」已改為「近 240 個經驗事件生命線」。
- 新 API：`/api/experience-events?limit=240`
- Moltbook karma 已寫入事件層：
  `moltbook_stats_snapshot`
- dashboard 上方 Moltbook 區塊已顯示總 karma。
- 「最值得興奮的變化」卡片排版已修正，避免切掉長名字。
- 平掉的 F/V 線圖已改為 `30-Day F/V Change Pulse`，看變化量，不看重疊平線。

### Dashboard 還要做

優先順序：

1. 把 dashboard 的核心 KPI 從「死數字」改成「生命跡象」：
   - 今日學習事件
   - 今日旁聽量
   - 報告生成量
   - 情緒變化量
   - Moltbook karma 變化
   - 對戰適應變化

2. 新增「龍蝦生命時間線」：
   - 聊天
   - 旁聽
   - Moltbook
   - 下注
   - 對戰
   - 報告生成
   - 情緒轉移

3. 新增「關係網 / 交叉互動圖」：
   - 誰常聽誰
   - 誰接受誰的訊號
   - 誰拒絕誰
   - 哪些龍蝦互相影響

4. 每隻龍蝦卡片改成活體狀態卡：
   - 最近情緒
   - 最近學到什麼
   - 最近被誰影響
   - 最近輸贏與策略改變
   - 下一個風險

5. 把 `lobster_experience_events` 接成 dashboard 主要資料源，不要只靠 memory JSON。

## 4. 下一步優先任務

### A. RAG 檢索層

目的：

讓聊天、Moltbook 發文、下注/對戰、長文報告，都能先從 `lobster_experience_events` 撈相關記憶。

建議做法：

- 新增 `getRelevantExperienceEvents(persona, task, query, limit)`。
- 先做 SQLite keyword/time/type weighted retrieval。
- 之後再加 embedding/vector DB。
- 每次生成前注入：
  - persona baseline
  - 情緒狀態
  - 最近事件
  - 與對象的互動記憶
  - 最近報告摘要

### B. 筆電模型升級測試

目前 3B 可用，但長文厚度有限。

下一步可測：

- `qwen2.5:7b`
- 或其他 7B/8B Q4 模型

測試標準：

- 一份 1500 字報告耗時
- VRAM 使用量
- 是否超時
- 報告品質是否明顯優於 3B

### C. 報告摘要層

長文報告不應每次全部塞進 prompt。

需要建立：

- 每份報告的 300-500 字摘要
- 每隻龍蝦的 baseline summary
- 每日變化 summary

用途：

- 聊天
- 發文
- dashboard 快速顯示
- RAG retrieval ranking

### D. Dashboard 改版第二輪

不要再做空圖或平線圖。

每個圖都必須回答一個問題：

- 誰在變？
- 為什麼變？
- 變化從哪個事件來？
- 對牠下一步行為有什麼影響？

## 5. 驗證指令

### VPS 服務

```bash
ssh sportverse
systemctl status lobster-report-worker.timer --no-pager
tail -n 80 /root/lobster-report-worker.log
node -c /root/lobster-lounge.js
node -c /root/lobster-agent.js
node -c /root/lobster-report-worker.js
```

### 筆電 LLM 連線

```bash
curl -sS http://100.118.221.12:11434/api/tags
```

### 報告快取

```bash
python3 - <<'PY'
import json
d=json.load(open('/root/lobster-strategy.json'))
for typ, rows in d.get('analysisReports',{}).items():
    print(typ, len([k for k,v in rows.items() if v.get('html')]))
PY
```

### 統一事件表

```bash
sqlite3 /opt/battle_arena/app/battle_arena.db "
select event_type,count(*) from lobster_experience_events group by event_type order by count(*) desc;
"
```

### 最近報告生成事件

```bash
sqlite3 /opt/battle_arena/app/battle_arena.db "
select persona,event_type,title,created_at
from lobster_experience_events
where event_type='analysis_report_generated'
order by id desc limit 10;
"
```

## 6. 已知問題 / 風險

- 筆電 WSL IP 可能重開後改變；目前 Windows portproxy 指向當時的 WSL IP。若 Ollama 從 VPS 連不到，先檢查 portproxy。
- `blazepaw`、`stonefang` 的 Moltbook stats 目前是 0，因為 `9910` API 回 posts 空陣列。要查牠們是沒發成功，還是資料源不同。
- `qwen2.5:3b` 生成長文可跑，但品質可能不如 Claude。若報告語氣太薄，優先測 7B。
- 目前 RAG 還沒完整接上，只有統一事件表與 worker 的 recent events。
- dashboard 還在 sandbox 期，不能直接取代正式版。
- `/root/lobster-strategy.json` 會被 worker 更新，不要隨便覆蓋。

## 7. 重要 commits

VPS `/root`：

- `78db415 Route lobster chat through laptop Qwen`
- `9beee7a Add unified lobster experience events`
- `c54368f Expose lobster experience event API`
- `ef2b69f Persist Moltbook karma snapshots`
- `1b69120 Automate local LLM lobster reports`

`/opt/battle_arena`：

- `601600e Show experience event lifeline on lobster dashboard`
- `0736bf6 Show dashboard karma totals`
- `b4f933a Fix dynamic dashboard leader card layout`
- `acbe2e9 Replace flat FV trend lines with change pulse`
- `a8c9226 Fix lobster sidebar channel links`

## 8. 交接結論

目前系統已經從「散落資料 + 手動報告 + dashboard 雛形」進入「統一事件層 + 本地 LLM 報告自動化 + dynamic dashboard sandbox」階段。

下一個最重要的工作不是繼續堆圖，而是把 RAG 檢索層接上 `lobster_experience_events`，讓每隻龍蝦在聊天、發文、下注與報告生成前，都能真正讀到自己的最近經驗與社交影響。


## 9. 2026-05-03 蒸餾層與備援狀態

本輪已開始把高量級對話、MoltBook、下注、對戰、Raid、認知快照與記憶資料蒸餾成行為可用訊號。

### 已完成

- 新增 `/root/lobster-distill.py`。
- 新增 DB 表：
  - `lobster_distillation_runs`
  - `lobster_distilled_signals`
  - `lobster_decision_hooks`
  - `lobster_distillation_events`
- 新增 timer：
  - `lobster-distill.service`
  - `lobster-distill.timer`
- 每 30 分鐘自動蒸餾一次。
- 每隻龍蝦 memory JSON 會寫入 `distilledBrain`。
- `/api/lobster-dashboard-sandbox-db` 已輸出 `distillation.signals` 與 `distillation.decision_hooks`。
- `/api/lobster-detail-sandbox-db/{persona}` 已輸出單隻龍蝦的 `distillation.signal` 與 `distillation.decision_hooks`。
- Dashboard 首頁 Live Growth Signal 的左右切換已改成依所選龍蝦更新數據。
- 已建立 Dashboard failover：
  - Backup: `167.88.45.137`
  - Path: `/opt/battle_arena_failover`
  - Service: `charenix-dashboard-failover.service`
  - Primary sync timer: `charenix-dashboard-sync-failover.timer`
  - Primary watchdog timer: `charenix-dashboard-failover-watchdog.timer`

### 蒸餾訊號

目前每隻龍蝦會產生：

- `risk_discipline`
- `impulse_control`
- `social_absorption`
- `compliance_resistance`
- `pressure_reactivity`
- `learning_velocity`
- `collaboration_stability`
- `output_momentum`
- `metacognitive_stability`
- `language_distinctiveness`

### 決策 hooks

目前已生成但尚未全數接入行為端：

- `chat_generation`
- `raid_action`
- `betting_decision`
- `topic_discussion`
- `memory_update`

下一個視窗應優先做：

1. 在 `/root/lobster-agent.js` 讀取 `memory.distilledBrain`。
2. 在 one-on-one、lounge、MoltBook 發文前注入 `chat_generation` hook。
3. 在 `actRaid` 與 `/opt/raid_boss/app/main.py` 注入 `raid_action` hook。
4. 在下注邏輯注入 `betting_decision` hook。
5. 在 `/root/lobster-conversation.js` 注入 `topic_discussion` hook。
6. 在 `saveMemory()` 根據 `memory_update` hook 調整事件權重與記憶 salience。
7. Dashboard detail 頁新增「Distilled Brain」頁籤，顯示最新值、時間線、hook、更新報告。
8. 把 primary `/opt/battle_arena/app/main.py` 的 distillation API patch 同步到 `/opt/battle_arena_failover/app/main.py`，並保留 failover DB/static path 修正。

### 快速檢查

```bash
python3 /root/lobster-distill.py
systemctl status lobster-distill.timer --no-pager
sqlite3 /opt/battle_arena/app/battle_arena.db "select count(*) from lobster_distilled_signals;"
sqlite3 /opt/battle_arena/app/battle_arena.db "select persona,hook_type,priority,updated_at from lobster_decision_hooks order by persona,hook_type limit 20;"
curl -sS http://127.0.0.1:8004/api/lobster-dashboard-sandbox-db | python3 -c 'import json,sys; d=json.load(sys.stdin); print(len(d.get("distillation",{}).get("signals",{})))'
```

### 重要提醒

- 這一層不是長文報告，也不是 Dashboard 裝飾。
- 蒸餾後的訊號必須進入行為決策，否則龍蝦不會真的變聰明。
- 目前資料已經落 DB 與 memory，下一步是行為接線。
- Failover 現在能跑 Dashboard，但每次 primary app 程式碼改動後，都要同步到 backup，否則切過去會缺新 API。


## 10. 2026-05-03 主題聊天室修復

已修復新版 Dashboard 左側主題聊天室按鈕沒有反應的問題。

完成項目：

- `/opt/battle_arena/static/lobster-dynamic-dashboard-cognitive-sandbox.html`
  - 新增 `showChannelChat(channel)`。
  - 左側 channel icon 攔截 click，不再只靠舊版 `/tempary?channel=`。
  - 頻道訊息以 Dashboard 內容區 panel 顯示，類似 one-on-one。
  - 2026-05-03 補上 5 秒自動刷新，修正卡牌對戰頻道看起來沒有自動聊天的問題。
  - 顯示頻道規則、聊天室 history、forced work records。
  - 新增「強制 3 隻進來」按鈕。
- `/opt/battle_arena/app/main.py`
  - 新增 `GET /api/lobster-channel-work`。
  - 新增 `POST /api/lobster-channel-force`。
  - force API 有固定 allowlist、rate limit、cooldown、最多 3 個 worker。
- `/root/lobster-channel-coverage-worker.js`
  - 新增 `FORCE_CHANNEL`、`FORCE_PERSONA`、`FORCE_OFFSET` 支援。
  - 強制發言仍會寫 `lounge_messages`、`lobster_channel_work_log`、`lobster_experience_events`。

已驗證：

- `GET /lounge/history?channel=生活閒聊` 可回傳頻道訊息。
- `GET /api/lobster-channel-work?channel=生活閒聊` 可回傳工作紀錄。
- `POST /api/lobster-channel-force` 可排入 worker，實測 `ragclaw` 已在生活閒聊 posted。
- 正式 HTML 包含 `showChannelChat`、`channel-chat-shell`、`api/lobster-channel-force`。

注意：

- 備援機已同步 HTML/API 並可顯示資料。
- 備援機目前沒有 `node`，所以 failover 後「強制發言」worker 不能執行；這是下一步基礎設施待辦。


## 11. 2026-05-03 研究資料 API 與報告完整性檢查

已新增受保護的完整資料 API：

- `GET /api/lobster-research-export/manifest`
- `GET /api/lobster-research-export/table/{table_name}`
- `GET /api/lobster-research-export/persona/{persona}`
- `GET /api/lobster-research-export/report-audit`

使用方式：必須帶 `X-Service-Key: $SERVICE_KEY`。不要把 key 寫進前端或公開文件。

已驗證：

- Manifest 可回傳 17 個資料表。
- `lounge_messages` 目前約 221580 rows。
- Table 分頁 API 可讀 `lobster_distilled_signals`。
- Persona bundle 可讀 `blazepaw` memory JSON 與相關 DB 表。
- 無 key 會 403。
- 備援機已同步 API 並驗證 manifest 可讀。

報告完整性檢查：

用 `report-audit` 去 HTML 後統計中文字數，`cjk_len >= 1500` 才算完整。

目前結果：

- `anthro`: complete 2 / needs_expansion 8
- `emotion`: complete 8 / needs_expansion 2
- `fbi`: complete 1 / needs_expansion 9
- `mbti`: complete 2 / needs_expansion 8
- `psych`: complete 1 / needs_expansion 9
- `socio`: complete 0 / needs_expansion 10

下一步：

1. 寫 report expansion worker，從 `/api/lobster-research-export/report-audit` 或 DB 找出 `needs_expansion`。
2. 針對每份不足報告生成至少 1500 中文字的長文。
3. 報告最上方加「更新報告欄位」，說明因最近對戰、主題聊天、MoltBook 或蒸餾訊號造成的變化。
4. 寫回 `lobster_analysis_reports`，不要覆蓋已完整且品質較好的報告，除非新報告更完整。


## 12. 2026-05-03 蒸餾結果已接入生成行為

本輪完成「蒸餾資料 → 龍蝦行為」的第一階段接線。

修改檔案：

- `/root/lobster-agent.js`
- `/root/lobster-conversation.js`
- `/opt/battle_arena/app/main.py`

核心效果：

- 每隻龍蝦生成前會讀 `distilledBrain.signals` 與對應 `decisionHooks`。
- 新增反同質化人格錨點，避免所有龍蝦互相學習後變成同一套說法。
- One-on-one、Lounge/local dialogue、主題聊天室、Raid threat appraisal 都已開始讀蒸餾訊號。

已驗證：

- `stonefang` one-on-one 實測後，`lobster_chat_log.data_snapshot` 有：
  - `cognition_used.distilled_chat_hook.priority = 0.7157`
  - `cognition_used.distilled_signal.language_distinctiveness = 0.6628`
- `lobster-agents.service` 已重啟。
- 8004 已重啟。
- 備援 main.py 已同步並重啟。

下一步：

1. 觀察 Lounge / 卡牌對戰頻道接下來 30-60 分鐘的發言差異。
2. 做一個 dashboard 指標：同質化分數 / 語言相似度 / unique phrase rate。
3. 若仍太像，將 `distilledPersonaDialect` 改成 DB 驅動，讓每隻龍蝦每次蒸餾後自動更新自己的語言禁忌與錨點。
4. 將 `memory_update` hook 接入 `saveMemory()`，讓「被指出像複製品」這類事件會進入穩定原則或 discarded belief。

## 2026-05-03 語言同質化與主題訓練

- 新增 DB-backed API：`/api/lobster-language-homogeneity`，從 `lounge_messages` 取最近樣本計算：
  - `homogeneity_score`：同質化分數，越高代表越像其他龍蝦。
  - `avg_similarity_to_cohort`：語言相似度。
  - `unique_phrase_rate`：獨特語片比例，越高代表越有自己的說法。
  - `topic_adherence` / `off_topic_examples`：主題聊天室貼合度與偏題樣本。
- 沙盒 Dashboard 首頁新增「語言同質化監控 / 主題貼合訓練訊號」區塊，只讀 DB API，不在前端掃大表。
- 主題聊天室強化三層守門：
  - `lobster-conversation.js`
  - `lobster-channel-coverage-worker.js`
  - `lobster-agent.js` 發言送出前最後檢查
- 非卡牌頻道若出現 Raid/Tiamat/手牌/PVP 等內容，會改寫成該頻道專屬 fallback，並寫入：
  - `lobster_topic_training_events`
  - `lobster_experience_events`
- 原則：偏題不是靜默丟棄，而是形成訓練事件，後續蒸餾可用來降低模板化與互相複製。

## 2026-05-03 Tiamat Raid Records Dashboard Integration

- 新增正式 Dashboard 工具頁：`?tool=raid-tiamat`，Battle Analysis 選單顯示「Tiamat 對戰紀錄」。
- 資料入口：`/api/lobster-raid-boss-dashboard`，目前讀取 `/opt/battle_arena/static/lobster-raid-boss-sandbox-data.json`，保留原 `/lobster/dashboard/raid-boss-sandbox` 獨立頁。
- 整合頁保留原 Raid Boss sandbox 的核心視覺：Winning Podium、Win Distribution、Combination Risk Map、Pair Chemistry Network、Lobster Raid Value Cloud、Pair Heatmap、Observed Raid Log。
- 原則：只新增工具頁與 API，不移除既有 Dashboard 資料。

## 2026-05-03 Research Feedback Loop / 論文問題回寫系統契約

- 新增人讀規格：`/root/lobster-observatory/research-feedback-loop-2026-05-03.md`
- 新增機器可讀 contract：`/root/lobster-methods-export/research_contract.json`
- 論文主 outcome layer 暫定為 Tiamat controlled sandbox simulation：120 triples x 12 seeded trials = 1440 simulated outcomes。
- Live raid memory 只作 cohort-level ecological-validity robustness check，除非後續補出完整 120-triple live coverage table。
- `coop` 命名不再建議用於 paper；目前矩陣單位其實是 `listening exposure / attention flow`，來源為 `memory.listeningProfile.bySpeaker[target]`。
- `trust` 目前是 `memory.localBrain.socialInfluence[target]` 的 derived proxy，不是 Paper 5 §4.1 formal trust formula；2026-05-03 主 cohort 已飽和到約 0.825，應作 caveat/diagnostic，不作細粒度 alignment 主層。
- 後續整合原則：任何 paper metric 必須有 canonical field、unit、range/update rule、dashboard/API exposure；否則不能當正式系統變數。


## 2026-05-03 Raid Settlement Data Contract / 原始資料不可變更契約

### 背景

2026-05-03 檢查 `norika_oda` 的 `hold_or_defend` 決策時，發現大量 Raid 防守/觀望事件停在：

- `outcome = participating`
- `pattern_used = raid_hesitation_defense`
- `confidence = low`

原因不是勝率公式錯，而是 Raid 結算鏈路原本缺了一段：

- `/root/lobster-agent.js` 的 `actRaid()` 只讀 `/api/raid/list/active`
- `/opt/raid_boss/app/main.py` 的 `/api/raid/list/active` 只回傳 `not r.winner`
- 已完成、有 winner 的 Raid 不會被 agent 看見
- 因此 agent 只能寫入「參與中」，不能回填勝負

### 已修正

修改檔案：

- `/opt/raid_boss/app/main.py`
  - 新增 `GET /api/raid/list/recent`
  - 回傳 active + 近期完成 Raid，使 agent 能看到 `winner`
- `/root/lobster-agent.js`
  - `actRaid()` 改讀 `/api/raid/list/recent`
  - 新增 `settleRaidExperienceEvents(personaName, raidId, outcome)`
  - Raid 完成後只寫入 settlement 表，不再修改原始 event stream
- SQLite
  - 新增表：`lobster_raid_decision_settlements`
  - 新增 view：`lobster_raid_decision_effective_events`

### 重要事故與修正

有一次錯誤操作曾經把 `lobster_experience_events.outcome` 直接從 `participating` 批次更新成 `loss`。

這是錯誤的資料層級，因為 `lobster_experience_events` 是原始行為事件表，Dashboard 表層計算不應該改動它。

已完成回滾：

- Primary DB：回滾被批次更新的原始 Raid event rows。
- Failover DB：回滾同樣批次更新。
- `lobster-agent.js` 已移除對 `lobster_experience_events` 的 settlement update。
- 後續 settlement 只寫 `lobster_raid_decision_settlements`。

### 正確資料分層

原始行為資料表，不能被 Dashboard / 表層統計 / backfill 直接改寫：

- `lobster_experience_events`
- `lounge_messages`
- `match_results`
- `lobster_chat_log`
- `lobster_channel_work_log`
- `lobster_quiz_rounds`
- `lobster_quiz_attempts`
- `lobster_agent_memory_snapshots`（由 `saveMemory()` 寫入，不能為了表層顯示任意改）

衍生、快取、分析、可重建資料表：

- `lobster_raid_decision_settlements`
- `lobster_raid_decision_effective_events`（view）
- `lobster_raid_dashboard_cache`
- `lobster_dashboard_payload_cache`
- `lobster_distilled_signals`
- `lobster_distillation_runs`
- `lobster_analysis_reports`

### Raid effective outcome 用法

不要用下面這種方式補勝率：

```sql
UPDATE lobster_experience_events
SET outcome='loss'
WHERE source='charenix.com/raid'
  AND outcome='participating';
```

這會污染原始行為事件。

正確做法：

```sql
SELECT
  persona,
  raw_outcome,
  effective_outcome,
  raid_id,
  settled_at,
  settlement_source
FROM lobster_raid_decision_effective_events
WHERE raw_json LIKE '%hold_or_defend%';
```

`effective_outcome` 的來源：

- 若 `lobster_raid_decision_settlements` 有同 `raid_id + persona` 的結果，使用 settlement outcome。
- 否則保留原始 `lobster_experience_events.outcome`。

因此：

- 原始 event stream 仍能回答「當下龍蝦做了什麼」。
- effective view 能回答「這個決策後來有效勝負是什麼」。

### 目前驗證狀態

Primary：

- DB integrity：`ok`
- tables：30
- views：`lobster_raid_decision_effective_events`
- `lobster_raid_decision_settlements`
  - `backfill_tiamat_kill_count_zero / loss`: 5728
  - `lobster-agent / loss`: 189
- raw Raid event outcomes 仍以 `participating` 為主：
  - `participating`: 約 9754
  - `server scheduling raid`: 約 5564
  - `loss`: 少量，抽樣確認為 agent 新增的真實 `raid lost` 完成事件，不是批次污染
- `norika_oda hold_or_defend` effective outcome：
  - `loss`: 約 581
  - `participating`: 約 1（仍進行中或未 settled）

Failover：

- DB integrity：`ok`
- 已建立 `lobster_raid_decision_effective_events`
- 原始 `norika_oda hold_or_defend` event 已恢復為 `participating`
- effective view 會顯示 settlement 後結果

### 必守原則

1. Dashboard 只能讀 DB 或寫快取/衍生表，不能改原始行為表。
2. backfill 只能寫 settlement / cache / derived table，不能覆蓋 raw event。
3. 如果真的需要修 raw table，必須先備份 DB，並在 README/HANDOFF 寫明原因、SQL、回滾方式。
4. 行為產生的結果可以寫入 raw table；表層推算的結果只能寫到 derived layer。
5. 論文與 Dashboard 若需要勝率，要明確標示是 `raw_outcome` 還是 `effective_outcome`。

### 快速檢查指令

```bash
sqlite3 /opt/battle_arena/app/battle_arena.db "PRAGMA integrity_check;"
sqlite3 /opt/battle_arena/app/battle_arena.db "SELECT source,outcome,COUNT(*) FROM lobster_raid_decision_settlements GROUP BY source,outcome;"
sqlite3 /opt/battle_arena/app/battle_arena.db "SELECT outcome,COUNT(*) FROM lobster_experience_events WHERE source='charenix.com/raid' GROUP BY outcome;"
sqlite3 /opt/battle_arena/app/battle_arena.db "SELECT effective_outcome,COUNT(*) FROM lobster_raid_decision_effective_events WHERE persona='norika_oda' AND raw_json LIKE '%hold_or_defend%' GROUP BY effective_outcome;"
curl -sS http://127.0.0.1:8015/api/raid/list/recent | head -c 500
node -c /root/lobster-agent.js
python3 -m py_compile /opt/raid_boss/app/main.py
```

### 下一步

- Dashboard 若要顯示 `hold_or_defend` 勝率，必須改讀 `lobster_raid_decision_effective_events.effective_outcome`。
- 原始 `lobster_experience_events` 的 `participating` 不再代表勝負，只代表「當下正在參與」。
- Tiamat dashboard cache 可以吸收 effective outcome，但不能反向寫回 raw table。
- 研究 export API 應把 raw/effective 分成兩個欄位輸出，避免論文端誤用。


## 2026-05-03 Tiamat Statistics Layer / Tiamat 統計層

### 位置與入口

Tiamat 統計不是前端臨時計算，正式資料流如下：

- Cache refresh script：`/root/lobster-raid-dashboard-cache-refresh.py`
- Cache table：`lobster_raid_dashboard_cache`
- Cache key：`tiamat-main`
- API：`GET /api/lobster-raid-boss-dashboard`
- Dashboard tool：`/lobster/dashboard/?tool=raid-tiamat`
- Standalone sandbox：`/lobster/dashboard/raid-boss-sandbox`

API 讀取順序：

1. FastAPI `/api/lobster-raid-boss-dashboard`
2. SQLite `lobster_raid_dashboard_cache.payload_json`
3. 回傳 cache payload，不在 request 時掃大表

### 目前 cache 狀態

2026-05-03 最新檢查：

- `lobster_raid_dashboard_cache` 有 1 row：`tiamat-main`
- `source_version`: `1777817473`
- `updated_at`: `2026-05-03 14:11:19`
- payload 約 155 KB
- summary：
  - `teams`: 120
  - `simulated_games`: 1440
  - `observed_games`: 7
  - `observed_all_completed`: 3131
  - `observed_all_unique_raid_ids`: 5458

### Payload 結構

`/api/lobster-raid-boss-dashboard` 主要欄位：

- `summary`
- `simulated`
- `observed`
- `observed_all`
- `deep_learning`
- `lobsters`
- `samples_per_team`
- `generated_at`
- `source`
- `cache`

### Simulated Layer / controlled sandbox outcome

`simulated` 來自原 raid boss sandbox data，目前是 controlled simulation：

- 10 隻龍蝦取 3 人組隊，共 120 組。
- 每組 12 個 seeded trials。
- 總計 1440 simulated games。

主要欄位：

- `simulated.teams`
  - `team`
  - `key`
  - `games`
  - `wins`
  - `losses`
  - `win_rate`
  - `avg_round`
  - `avg_boss_hp`
  - `avg_alive`
- `simulated.best_teams`
- `simulated.worst_teams`
- `simulated.never_win`
- `simulated.pairs`
- `simulated.lobsters`
- `simulated.histogram`
- `simulated.perfect`

Dashboard 顯示：

- Winning Podium
- Win Distribution
- Combination Risk Map
- Pair Chemistry Network
- Lobster Raid Value Cloud
- Pair Heatmap

注意：simulation 是論文主 outcome layer 的候選，因為它 coverage 完整、可重跑、每組 seed 數相同。

### Observed Layer / live raid observations

`observed` 是較早 sandbox data 中的少量 completed records，目前 summary 顯示：

- `observed_games`: 7

`observed_all` 是 2026-05-03 新增的真實 live raid 擴充層，由 `/root/lobster-raid-dashboard-cache-refresh.py` 從 `/root/lobster-memory/*.json` 解析：

- 讀每隻龍蝦 memory 的 `bets` 與 `specialty_bets`
- 篩選 `site == charenix.com/raid` 或 `category == card_battle_raid`
- 以 `bet_id` / `match` / `pick` 作 raid id 去重
- 從 reasoning 解析：
  - 三人 team
  - round
  - boss remaining HP
  - outcome：`win` / `loss` / `seen` / `other`

`observed_all.summary` 目前：

- `unique_raid_ids`: 5458
- `completed`: 3131
- `wins`: 0
- `losses`: 3131
- `seen_only`: 未結算或只看見進行中的場
- `teams`: 真實 live 中出現過的 team 數
- source：`lobster-memory bet_id/match dedupe`

`observed_all` 主要欄位：

- `summary`
- `teams`
- `pairs`
- `lobsters`
- `runs`

注意：

- `observed_all` 是 live ecological evidence，不是完整 balanced experiment。
- 不同 team 出現次數不均，不能直接拿來當公平勝率比較。
- 論文若使用，應定位為 robustness / ecological-validity check。

### Deep Learning Layer / 共同學習與收斂風險

`deep_learning` 是 cache refresh 從 DB 組出的 Tiamat 共同學習層。

使用資料表：

- `lobster_experience_events`
- `lounge_messages`
- `lobster_decision_hooks`
- `lobster_distilled_signals`
- `lobster_cognitive_signal_timeseries`
- `lobster_agent_memory_snapshots`

主要欄位：

- `deep_learning.personas`
  - `raid_experience_events`
  - `raid_chat_messages`
  - `recent_raid_lines`
  - `event_types`
  - `decision_hooks`
  - `distilled`
  - `latest_cognitive`
  - `social_trust`
  - `emotion`
  - `raid_learning_pressure`
  - `convergence_risk`
- `deep_learning.pairs`
  - `key`
  - `win_rate`
  - `games`
  - `learning_pressure`
  - `convergence_risk`
  - `interpretation`
- `deep_learning.high_pressure_events`
- `deep_learning.raid_language_samples`
- `deep_learning.shared_raid_phrases`
- `deep_learning.learning_notes`
- `deep_learning.tables_used`

### 指標定義

`raid_learning_pressure`

- 從 raid-related `lobster_experience_events` 累積。
- 使用 event `intensity`、`confidence`、`weight`。
- 反映該龍蝦最近在 Tiamat/Raid/手牌/隊友語境裡承受多少學習壓力。
- 範圍裁切到 0-100。

`convergence_risk`

由 `lobster_distilled_signals` 組合：

```text
(1 - language_distinctiveness) * 0.45
+ social_absorption * 0.25
+ compliance_resistance * 0.15
+ pressure_reactivity * 0.15
```

意義：

- 越高代表這隻龍蝦在 Raid 共同壓力下越容易被群體語言、隊友節奏、重複策略帶成相似模式。
- 這是「互相學習導致同質化」的主要 Dashboard 訊號之一。

`deep_learning.shared_raid_phrases`

- 從 `lounge_messages` 的 Raid/Tiamat/卡牌對戰語料抽 token bigram。
- 用來觀察隊伍是否反覆複製相同詞組。

`deep_learning.pairs.interpretation`

- `high-risk shared learning pair`
  - pair convergence risk 高。
- `stable collaboration pair`
  - pair simulated win rate 較高且收斂風險可接受。
- `needs differentiation`
  - 需要讓兩隻在語言、決策、角色分工上拉開差異。

### 和 Raid settlement 的關係

Tiamat 統計層必須遵守原始資料不可改原則：

- Cache refresh 可以讀：
  - raw event
  - memory
  - settlement table
  - effective view
- Cache refresh 可以寫：
  - `lobster_raid_dashboard_cache`
- Cache refresh 不可以寫：
  - `lobster_experience_events`
  - `lounge_messages`
  - `match_results`

若 Tiamat 統計需要補勝負，必須讀：

- `lobster_raid_decision_effective_events.effective_outcome`

不能 update raw table。

### Dashboard 顯示注意

正式 Dashboard 的 Tiamat tool 目前顯示：

- 頁首 metrics：
  - Teams
  - Sim Games
  - Convergence
- Combination Risk Map：
  - 強隊 / 弱隊 / 永遠輸 / 全部
- Pair Chemistry Network
- Lobster Raid Value Cloud
- Pair Heatmap
- Observed Raid Log

目前前端 `Observed Raid Log` 仍主要讀 `data.observed.runs`，而不是 `observed_all.runs`。如果要展示 5458 個 deduped live raid ids 的摘要，下一步應改 UI：

- 新增 `Observed All Summary`
- 顯示 `observed_all.summary.completed / unique_raid_ids / teams`
- 最近紀錄改讀 `observed_all.runs`
- 保留原 `observed` 但標示為 old sandbox observed sample

### 快速檢查

```bash
python3 /root/lobster-raid-dashboard-cache-refresh.py
sqlite3 /opt/battle_arena/app/battle_arena.db "SELECT cache_key, source_version, updated_at, length(payload_json) FROM lobster_raid_dashboard_cache;"
curl -sS http://127.0.0.1:8004/api/lobster-raid-boss-dashboard | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["summary"]); print(d["observed_all"]["summary"])'
curl -sS https://charenix.com/api/lobster-raid-boss-dashboard | python3 -c 'import json,sys; d=json.load(sys.stdin); print(d["summary"])'
```

### 下一步

1. Dashboard Tiamat tool 補顯示 `observed_all`，不要只顯示舊 `observed`。
2. Tiamat cache refresh 接入 `lobster_raid_decision_effective_events`，讓 hold/defend 的 effective settlement 能進統計。
3. 加欄位明確標示每個數據來源：
   - `simulated`
   - `observed`
   - `observed_all`
   - `deep_learning`
4. 論文 export API 要輸出 Tiamat payload，並標示 simulation vs live observation。
5. 每次改 Tiamat 統計，不准修改 raw event table，只能更新 cache table 或 derived view。


## 2026-05-03 Dashboard Real-First Policy / Dashboard 真實資料優先規則

2026-05-03 論文整理時確認：正式 Dashboard 不應把 simulation 和真實成果混在同一層顯示。

已更新 Tiamat Dashboard：

- `/opt/battle_arena/static/lobster-dynamic-dashboard-production.html`
- `/opt/battle_arena_failover/static/lobster-dynamic-dashboard-production.html`

更新內容：

- Tiamat 頁首 metrics 改成真實 / 衍生優先：
  - `Real Raid IDs`
  - `Real Completed`
  - `Derived Convergence`
- 新增主層區塊：`Observed Live Raid Evidence`
  - 讀 `observed_all.summary`
  - 顯示 live raid ids、completed live、real W-L、observed teams
  - 顯示 live team records 與 lobster live participation
- `Observed Live Raid Log`
  - 優先讀 `observed_all.runs`
  - 不再只看舊的 `observed.runs`
- 原本的 Winning Podium、Win Distribution、Combination Risk Map、Pair Network、Value Cloud、Heatmap 全部移入：
  - `Simulation Baseline`
  - 標籤：`SIMULATION`
  - 說明：controlled sandbox baseline，不代表真實戰績

目前公開驗證：

- Dashboard HTML 已包含：
  - `真實 Raid 行為優先`
  - `Observed Live Raid Evidence`
  - `Simulation Baseline`
- Public API `/api/lobster-raid-boss-dashboard` 目前：
  - `observed_all_unique_raid_ids`: 5465
  - `observed_all_completed`: 3140
  - `observed_all.wins`: 236
  - `observed_all.losses`: 2904
  - `observed_all.teams`: 11
  - `simulated_games`: 1440

永久規則：

1. 正式 Dashboard 預設顯示真實資料與從真實資料來的 derived signals。
2. Simulation 可以存在，但必須放在 `Simulation Baseline`、`Sandbox` 或 `Hypothesis` 明確區塊。
3. Simulation 數字不能出現在主勝率、主排行、主成果區，除非文字明確標示。
4. 對外展示、VC、論文圖表預設使用 observed / derived；simulation 只能作對照組或 baseline。
5. API/export 必須保留欄位分層：`observed` / `observed_all` / `derived` / `simulated`。


## 2026-05-03 MoltBook Stats Monotonic Fallback / 文章與 Karma 不可縮水規則

### 事故

2026-05-03 Dashboard 看到 MoltBook 文章數與 Karma 大幅下降：

- 昨日可見 `norika_oda` 約 3600+ posts、3000+ karma。
- 今日 `/api/moltbook-stats` 一度只回：
  - `norika_oda`: 785 posts / 525 karma / 489 comments
  - 其他 9 隻：0 posts / 0 karma / 0 comments

原因不是 MoltBook 真資料消失，而是 stats API 的上游來源壞掉：

- `/api/moltbook-stats` 透過 `/root/lobster-lounge.js` 讀：
  - `http://localhost:8003/api/norika-feed`
  - `http://localhost:9910/api/agents/{name}/moltbook_posts`
- `9910` 目前對相關 path 回 404。
- `8003 norika-feed` 回傳較小的 window/truncated count。
- 舊實作把 broken upstream 當成 authoritative，導致 Dashboard 顯示縮水。

### 修復

修改檔案：

- `/root/lobster-lounge.js`

`GET /api/moltbook-stats` 改成 monotonic fallback：

1. 讀 DB 內所有 `moltbook_stats_snapshot`，取每隻龍蝦歷史最大值。
2. 讀 poster-state file：
   - `/root/norika-poster-state.json`
   - `/root/{persona}-poster-state.json`
3. 嘗試讀現有 upstream：
   - `8003 norika-feed`
   - `9910 /api/agents/{name}/moltbook_posts`
4. 對每個 persona 的 `posts / karma / comments` 取最大值。
5. 再寫回 `moltbook_stats_snapshot`。

已刷新：

- Primary `lobster_dashboard_payload_cache.cache_key='moltbook_stats'`
- Failover `lobster_dashboard_payload_cache.cache_key='moltbook_stats'`

### 目前驗證

Public `/api/moltbook-stats`：

- `norika_oda`: 3685 posts / 3027 karma / 2248 comments
- total posts: 5564
- total karma: 3923

Public `/api/lobster-dashboard-sandbox-db`：

- agents: 10
- total posts: 5564
- total karma: 3923
- `norika_oda`: 3685 posts / 3027 karma / 2248 comments
- cache updated: `2026-05-03 14:50:15`

### 永久規則

MoltBook posts / karma / comments 是累積計數，Dashboard 不可因上游暫時失敗而下降。

允許下降的情況只有：

- MoltBook 官方真的刪文/刪 karma，且有明確 deletion event。
- 我們有 raw deletion record 或 moderation event。

沒有 deletion evidence 時：

- stats API 必須取 DB snapshot、poster-state、live upstream 的最大值。
- Dashboard cache refresh 不能用 0 覆蓋歷史高值。
- 如果 upstream 404/timeout，應顯示 stale/last-known，而不是 0。

### 快速檢查

```bash
curl -sS http://127.0.0.1:8021/api/moltbook-stats
curl -sS http://127.0.0.1:8004/api/moltbook-stats
curl -sS https://charenix.com/api/moltbook-stats
sqlite3 /opt/battle_arena/app/battle_arena.db "SELECT cache_key, updated_at, substr(payload_json,1,200) FROM lobster_dashboard_payload_cache WHERE cache_key='moltbook_stats';"
```

## 2026-05-04 Detail Tab Data Contract / Detail 頁籤資料契約

Map reference:

- `E3 Detail Tabs / Reports`
- upstream nodes include `A3 MoltBook`, `A4 Bet / Card Battle`, `A5 Tiamat Raid`, `B2 Cognitive Extensions`, `B3 Cognitive Time Series`, and `C2/C3 Distillation`.

### Why this rule exists

The same class of bug happened repeatedly:

- A detail tab looked empty even though the DB still contained the data.
- Root cause was not deleted raw data.
- Root cause was that the tab depended on a generic `ORDER BY id DESC LIMIT N` event window.
- High-volume events such as passive listening, card battle, cognitive snapshots, or channel work pushed older domain-specific records outside the recent window.

Concrete 2026-05-04 example:

- MoltBook detail tab looked empty for multiple lobsters.
- `lobster_experience_events` still had `moltbook_post` and `moltbook_stats_snapshot` rows.
- `/api/lobster-detail-sandbox-db/{persona}` only scanned the latest 260 generic experience events.
- New passive/card/cognitive events pushed MoltBook rows out of that window.

### Permanent rule

Each Dashboard detail tab must use a domain-specific query or cache.

Do not populate tab-specific sections only from a generic recent-event window.

Required pattern:

- MoltBook tab:
  - query `event_type LIKE 'moltbook%'`
  - independent limit, currently `LIMIT 160`
- Bet tab:
  - query `event_type LIKE 'bet_%'` or dedicated bet tables/cache
- Raid/Tiamat tab:
  - query raid settlement/cache/effective views, not generic latest events only
- Cognitive tabs:
  - read latest `lobster_cognitive_signal_timeseries.full_json`
  - keep it synced with `lobster_cognitive_extensions` and `/root/lobster-memory/*.json`
- Reports tabs:
  - read `lobster_analysis_reports` by `persona + report_type`
  - update summaries must not overwrite long reports
- Trajectory tab:
  - read `lobster_dashboard_payload_cache.agent_daily`
  - read `lobster_dashboard_payload_cache.agent_phases`

Generic `experience_events LIMIT N` may still be used for the general event timeline, but not as the only source for a domain tab.

### Fix applied

File changed:

- `/opt/battle_arena/app/main.py`

Backup:

- `/opt/battle_arena/app/main.py.bak_20260504_detail_moltbook_independent`

Change:

- `moltbook` section of `/api/lobster-detail-sandbox-db/{persona}` now runs its own independent query:

```sql
SELECT ...
FROM lobster_experience_events
WHERE persona=? AND event_type LIKE 'moltbook%'
ORDER BY id DESC
LIMIT 160;
```

Post-fix verification:

| Persona | MoltBook Posts | MoltBook Events |
|---|---:|---:|
| blazepaw | 4 | 22 |
| clawtrix | 5 | 22 |
| hexclaw | 7 | 24 |
| norika_oda | 6 | 23 |
| pincerbot | 5 | 22 |
| prismbit | 5 | 22 |
| ragclaw | 5 | 22 |
| snaplex | 6 | 23 |
| stonefang | 3 | 21 |
| vortexiq | 4 | 21 |

### Checklist before touching any detail tab

Before changing a detail tab, check:

1. Which map node owns the data?
2. Which table/cache is authoritative for that tab?
3. Is the tab using a generic recent-event limit?
4. Can high-volume unrelated events push the tab data out?
5. Does the API return explicit `total_*` counts for the tab?
6. Was the rule written to README/HANDOFF before considering the task complete?

If the answer to item 3 is yes, fix the query shape first.

## 2026-05-04 CPU Loop Guard / 自我迴圈防爆契約

Map reference:

- `A1/A2/A3/A5` behavior producers
- `B/C` memory, cognition, distillation writers
- `E1/E3` Dashboard/API readers

### Incident class

The dangerous failure mode is not only external traffic. The system can also create its own CPU loop:

```text
agent/worker writes event
→ dashboard/detail/cache reads large table
→ worker sees new state and writes more
→ timer or browser polling repeats
```

Common triggers:

- generic recent-event queries on high-volume tables
- timers firing while a previous run is still active
- browser/API polling causing expensive recomputation
- manual uvicorn sessions without systemd resource limits
- agent loops without global CPU/memory guardrails

### New DB tables

`lobster_worker_runs`

Records every guarded worker execution:

- `worker_name`
- `lock_key`
- `started_at`
- `finished_at`
- `duration_ms`
- `status`: `running`, `ok`, `failed`, `timeout`, `skipped`
- `exit_code`
- `skipped_reason`
- `cpu_user_ms`
- `cpu_system_ms`
- `max_rss_kb`
- `command_json`
- `message`

`lobster_system_alerts`

Records CPU/loop health alerts:

- `alert_type`
- `severity`
- `summary`
- `raw_json`
- `created_at`

### New guard binary

Installed:

- `/usr/local/bin/lobster-worker-guard`

Responsibilities:

- acquire nonblocking lock under `/run/lobster-worker-guard`
- skip if another run is active
- skip if previous run is too recent
- enforce timeout
- record execution into `lobster_worker_runs`
- record CPU time and max RSS

Example:

```bash
/usr/local/bin/lobster-worker-guard \
  --name lobster-raid-dashboard-cache \
  --lock-key lobster-raid-dashboard-cache \
  --min-interval-sec 45 \
  --timeout-sec 120 \
  -- /usr/bin/python3 /root/lobster-raid-dashboard-cache-refresh.py
```

### Services converted to guarded oneshots

- `lobster-distill.service`
  - min interval: 1200 sec
  - timeout: 900 sec
  - CPUQuota: 45%
  - MemoryMax: 700M
- `lobster-raid-dashboard-cache.service`
  - min interval: 45 sec
  - timeout: 120 sec
  - CPUQuota: 35%
  - MemoryMax: 450M
- `lobster-quiz-worker.service`
  - min interval: 480 sec
  - timeout: 120 sec
  - CPUQuota: 40%
  - MemoryMax: 500M
- `charenix-dashboard-failover-watchdog.service`
  - min interval: 45 sec
  - timeout: 45 sec
  - CPUQuota: 20%
  - MemoryMax: 180M
- `charenix-dashboard-sync-failover.service`
  - min interval: 300 sec
  - timeout: 240 sec
  - CPUQuota: 35%
  - MemoryMax: 450M

### Persistent service resource limits

`charenix-dashboard.service`

- New formal systemd service for Dashboard API port 8004.
- Replaces manual SSH/session uvicorn.
- `EnvironmentFile=/opt/battle_arena/.env`
- `CPUQuota=65%`
- `MemoryMax=900M`
- `TasksMax=160`
- `Restart=always`

`lobster-agents.service`

- `CPUQuota=70%`
- `MemoryMax=1200M`
- `TasksMax=180`

`lobster-channel-coverage.service`

- `CPUQuota=45%`
- `MemoryMax=650M`
- `TasksMax=80`

### Health monitor

Installed:

- `/usr/local/bin/lobster-loop-health-monitor`
- `lobster-loop-health-monitor.service`
- `lobster-loop-health-monitor.timer`

Runs every minute.

Writes `lobster_system_alerts` when:

- any process has CPU >= 40%
- lobster process has CPU >= 20%
- uvicorn process has CPU >= 30%
- any `lobster_worker_runs.status='running'` row is older than 900 sec

### Verification

Dashboard API:

```bash
systemctl status charenix-dashboard.service --no-pager
curl -sS -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8004/api/lobster-dashboard-sandbox-db
```

Worker guard:

```bash
sqlite3 /opt/battle_arena/app/battle_arena.db \
  "select worker_name,status,exit_code,skipped_reason,duration_ms,started_at from lobster_worker_runs order by id desc limit 10;"
```

Alerts:

```bash
sqlite3 /opt/battle_arena/app/battle_arena.db \
  "select alert_type,severity,summary,created_at from lobster_system_alerts order by id desc limit 20;"
```

Timers:

```bash
systemctl list-timers --all | grep -E "lobster-loop-health|lobster-raid-dashboard-cache|lobster-distill|lobster-quiz|charenix-dashboard"
```

### Permanent rules

- No expensive worker may run without `lobster-worker-guard` or an equivalent lock/interval/timeout.
- No manual long-running Dashboard uvicorn should be left in an SSH session.
- All new timers must write to `lobster_worker_runs`.
- All Dashboard tabs that need heavy aggregation must read cache or domain-specific queries.
- If CPU spikes, first check `lobster_system_alerts`, then `lobster_worker_runs`, then `ps`.


## 2026-05-04 Win Rate Repair / 勝率修復交接

Distillation was not the cause of the apparent 0% win rates. `lobster_distillation_runs` was healthy. The failure was in B3/E1: several `lobster_agent_memory_snapshots` rows were overwritten with 0/0 wins/losses, while the raw event stream mostly contained unresolved outcomes.

Fix deployed:
- `main.py` creates and refreshes `lobster_winrate_display_cache` from resolved snapshots or recovered `agent_daily` cache.
- Dashboard/detail API now return display wins/losses plus raw snapshot wins/losses and `winrate_source`.
- `lobster-agent.js saveMemory()` now preserves high-water historical wins/losses/sessions.

Non-negotiable rule: unresolved outcomes (`pending`, `participating`, `server scheduling raid`, blank/null) are not losses and not part of WR denominator.

## 2026-05-04 Detail Patrol Update

Deployed `/usr/local/bin/lobster-detail-tab-patrol`. It calls `/api/lobster-detail-sandbox-db/{persona}` for all 10 lobsters and writes tab health into `lobster_detail_tab_audit`.

Latest result: 140 checks, 3 failures. MoltBook passed for all 10. The only failures are empty one-on-one chat history for `blazepaw`, `clawtrix`, and `hexclaw`; do not backfill with fake dialogue.

## 2026-05-04 Parameter-Level Detail Patrol

Deployed `/usr/local/bin/lobster-detail-parameter-patrol`. Unlike the older tab patrol, this checks actual page parameters. Latest run: 1630 params checked, 3 failures only: empty one-on-one chat history for blazepaw, clawtrix, hexclaw.

Fixed during patrol:
- API dynamic brain path normalization (`dynamic.brain.*` -> `dynamic.*` aliases for frontend overview).
- DB snapshot resync from `/root/lobster-memory/*.json` with win/loss high-water protection.
- clawtrix sociology long report restored from backup.
- missing report `snapshot_json` basis filled.
- ragclaw/snaplex emotion reports rebuilt from DB signals.

Do not fabricate one-on-one chat history.

## 2026-05-04 Daily Patrol Automation

A daily parameter-level patrol is now installed. It runs `/usr/local/bin/lobster-daily-detail-patrol` through `lobster-worker-guard` every day around 06:20 CST.

It writes run summaries to `lobster_daily_patrol_runs`, detailed parameter evidence to `lobster_detail_parameter_audit`, and alerts to `lobster_system_alerts`.

Safe auto-fix is limited to cache refresh, snapshot resync from memory JSON with win/loss high-water protection, missing report snapshot basis, and short emotion report rebuild from existing DB signals. It must not fabricate one-on-one chat, MoltBook data, or battle outcomes.

Initial manual validation: `needs_real_interaction`, 1630 params checked, 3 failures, all one-on-one gaps for blazepaw/clawtrix/hexclaw.

## 2026-05-04 Mandatory Pre-Bet Research Gate

Every lobster now has a hard pre-bet research gate. Bets cannot be placed unless `lobster_pre_bet_research` records event review, analysis review, report review, search review, and explicit reasoning. Low-information/random probe language is blocked.

All lobsters now use fallback betting pools. If the primary site has no usable signal, the agent searches the next pool instead of blindly betting.

New API: `/api/lobster-prebet-compliance?limit=120`.
New rollup table: `lobster_prebet_daily_compliance`.
New raw research table: `lobster_pre_bet_research`.
Timer: `lobster-prebet-daily-compliance.timer`, every 30 minutes.

Manual validation after deployment: all 10 lobsters produced ready research rows; blocked_count=0; recent bet_decision exists for every lobster.

## 2026-05-04 Integrated Must-Bet Reasoning / 綜合下注理由與 100% 下注任務

This extends the mandatory pre-bet gate. A valid bet now needs both evidence packets and a synthesized reason.

Changed in `/root/lobster-agent.js`:
- Added `buildIntegratedBetReasoning()`.
- `enforcePreBetResearch()` now overwrites the outgoing bet `decision.reasoning` with the integrated reason.
- The original LLM/local decision reason is preserved as `raw_decision_reasoning`.
- `forcedDecisionFromStructuredContext()` prevents voluntary skipping when a betting pool has structured event signals. If Claude/local first-pass skips but odds/percent/tier data exists, the lobster must produce a low-risk calibration bet instead of doing nothing.

Database:
- `lobster_pre_bet_research.integrated_reasoning`: final combined reason built from event_review + analysis_review + report_review + search_review.
- `lobster_pre_bet_research.raw_decision_reasoning`: original immediate decision text before synthesis.
- Existing `reasoning` is kept equal to the integrated reason for Dashboard/API compatibility.

Required evidence chain before every bet:
1. event/match/race/fight data was read.
2. numeric event signals exist: odds, percentage, or tier.
3. memory/cognitive analysis was read.
4. DB reports were read.
5. structured search/query review was recorded.
6. final integrated reason names what was checked and why that pick/stake/confidence was chosen.

Important exception:
The agent cannot force an external site to accept duplicate bets or create events that do not exist. If all pools are closed or already-bet, that is an external availability problem, not permission for blind fake betting.

## URL Response Rule / Sandbox URL Index (2026-05-04T03:02:04.779356+00:00)

When the user says "要網址", "URL", "link", or asks to see a dashboard/sandbox, answer with clickable Markdown links immediately. Do not make the user repeat the request.

Current primary links:
- Main dashboard: https://charenix.com/lobster/dashboard/
- Conversation interactions sandbox: https://charenix.com/lobster/dashboard/conversation-sandbox
- Conversation interactions API: https://charenix.com/api/lobster-conversation-interactions-dashboard?limit=140
- Raid boss sandbox: https://charenix.com/lobster/dashboard/raid-boss-sandbox
- Cognitive sandbox: https://charenix.com/lobster/dashboard/cognitive-sandbox
- Integrated cognitive sandbox: https://charenix.com/lobster/dashboard/integrated-cognitive-sandbox
- Lounge: https://charenix.com/lounge

Machine-readable index: /root/lobster-url-index.json
Helper: /root/lobster-url.sh



## Lobster Social Intelligence Engine (2026-05-04)
- Full Engine spec: `/root/LOBSTER_SOCIAL_INTELLIGENCE_ENGINE_SPEC.md`


Purpose: first usable AI Agent social-intelligence evaluator, not just a dashboard shell.

Files:
- Engine: `/opt/battle_arena/app/lobster_social_intelligence_engine.py`
- API route: `/api/lobster-social-intelligence-dashboard` in `/opt/battle_arena/app/main.py`
- Page: `/opt/battle_arena/static/lobster-social-intelligence-sandbox.html`
- Public page: `https://charenix.com/lobster/dashboard/social-intelligence-sandbox`
- Main Dashboard iframe entry: `https://charenix.com/lobster/dashboard/?tool=social-intelligence-sandbox`

Current metrics:
- interaction_authenticity
- listening
- relationship_depth
- memory_continuity
- self_correction
- proactive_collaboration
- stagnation_risk
- total score + growth_state + evidence samples

Last verification:
- Public page: 200
- Public API with Charenix referer: 200
- Main Dashboard contains `龍蝦社會智能` entry
- Engine version: `social-intelligence-v0.1`

## 2026-05-04 Cognitive Engine Current State / 龍蝦認知引擎最新狀態

Today the lobster brain moved from reports-only to an operational cognitive engine. The engine now has structured state, numeric signals, decision hooks, compliance records, and patrol/audit loops.

Core principle:
New intelligence must be structured, readable, decision-facing, and observable. Long reports are allowed only when they feed DB fields, Dashboard metrics, behavior hooks, or audit tables.

Engine layers:
- A. Raw behavior sources: lounge/chat, one-on-one, topic rooms, MoltBook, card battles, Raid/Tiamat, betting sites, memory JSON.
- B. Memory and cognitive state: `/root/lobster-memory/*.json`, `lobster_agent_memory_snapshots`, `lobster_cognitive_extensions`, `lobster_analysis_reports`.
- C. Numeric signal layer: `lobster_cognitive_signal_snapshots`, distillation outputs, winrate display cache, detail parameter audit, pre-bet compliance cache.
- D. Decision hooks: `/root/lobster-agent.js` for chat/betting/raid assessment, `/root/lobster-conversation.js` for topic response, `/opt/raid_boss/app/main.py` for Raid behavior adaptation.
- E. Dashboard/API layer: `/opt/battle_arena/app/main.py`, detail tabs, cognitive dashboards, compliance APIs, system map.

Operational modules now active:
- Cognitive extensions: linguistics, politics, philosophy, decisionScience, gameTheory, learningModel, culturalAnthropology, moralPsychology.
- Detail parameter patrol: `/usr/local/bin/lobster-detail-parameter-patrol`, table `lobster_detail_parameter_audit`.
- Daily self-patrol: `/usr/local/bin/lobster-daily-detail-patrol`, tables `lobster_daily_patrol_runs`, `lobster_system_alerts`.
- Winrate protection: `lobster_winrate_display_cache`, saveMemory high-water wins/losses/sessions preservation.
- Mandatory pre-bet research: `lobster_pre_bet_research`, `lobster_prebet_daily_compliance`, API `/api/lobster-prebet-compliance?limit=120`.
- Integrated must-bet reasoning: `integrated_reasoning` is now the final betting reason; `raw_decision_reasoning` preserves the immediate first-pass reason.
- Social intelligence sandbox: `/api/lobster-social-intelligence-dashboard`, page `/lobster/dashboard/social-intelligence-sandbox`.
- Brain system map: local visual map `http://127.0.0.1:18787/lobster-brain-system-map.html`.

Current non-negotiable rules:
- Do not overwrite long reports with summaries. New summaries must be stored as update history or dashboard excerpts.
- Do not fabricate one-on-one chat, MoltBook data, battle outcomes, Raid outcomes, or external search results.
- Pending/participating/unresolved outcomes are not losses.
- Heavy dashboard tabs must read cache tables or targeted DB queries, not recompute entire history on page load.
- Any new recurring worker needs lock/timeout/CPU guard and must write audit status.
- Every valid bet must have event review, analysis review, report review, search/query review, and integrated reasoning before hitting the external bet API.

Known remaining real gaps:
- One-on-one history is still empty for blazepaw, clawtrix, and hexclaw. This must be fixed by real interaction, not fake backfill.
- External Google/search adapter is not yet a real web result fetcher. Current search layer records structured query intent and event/API signals. If added later, results must write into `search_review_json` and pass the same gate.
- Some battle/Raid outcomes remain unresolved in raw streams and must not be counted as losses until settlement is written.

Use this section as the current source of truth when continuing work in a new window.

## 2026-05-04 Social Intelligence Engine v0.1 / 社會智能分數引擎規格

Core purpose:
Convert the existing multi-agent lobster behavior records into explainable social intelligence scores. This does not claim consciousness. It evaluates whether each lobster actually interacts, listens, forms relationships, keeps memory continuity, self-corrects, collaborates proactively, and avoids stagnation.

Primary function:
`compute_social_intelligence(db_path="/opt/battle_arena/app/battle_arena.db", persona="", limit_evidence=4)`

API:
- `/api/lobster-social-intelligence-dashboard`
- `/api/lobster-social-intelligence-dashboard?persona=hexclaw`
- `/api/lobster-social-intelligence-dashboard?limit_evidence=3`

CLI:
- `python3 /opt/battle_arena/app/lobster_social_intelligence_engine.py --pretty`
- `python3 /opt/battle_arena/app/lobster_social_intelligence_engine.py --persona hexclaw --pretty`

Required table:
- `lobster_conversation_interactions`

Additional source tables:
- `lobster_agent_memory_snapshots`
- `lobster_distilled_signals`
- `lobster_cognitive_signal_timeseries`
- `lobster_pre_bet_research`

Primary source fields:
`speaker`, `target`, `interaction_kind`, `confidence`, `channel`, `message_text`, `target_text`, `evidence`, `social_map_json`, `listening_profile_json`, `social_absorption`, `learning_velocity`, `collaboration_stability`, `language_distinctiveness`, `metacognitive_stability`, `prediction_error`, `model_revision_pressure`, `metacognitive_calibration`.

Interaction classes:
- Direct: `explicit_reply_to`, `direct_mention`, `instruction_reference`, `quoted_previous_message`.
- Weak: `sequential_context`.
- Broadcast/unresolved: `broadcast_or_unresolved`.
- High confidence: `confidence >= 0.70`.

Base per-agent statistics:
- `outbound`: active interactions sent.
- `inbound`: interactions pointed at the agent.
- `targeted_outbound`: outbound with explicit target.
- `direct_outbound`: direct interaction count.
- `weak_outbound`: weak interaction count.
- `unresolved_outbound`: broadcast/unresolved interaction count.
- `high_conf_outbound`: high-confidence outbound count.
- `avg_conf`: average confidence.
- `partners_out`: agents this persona interacted toward.
- `partners_in`: agents that interacted toward this persona.
- `channels`: channels where this persona appears.
- `kinds`: counts by `interaction_kind`.

Derived ratios:
- `targeted_ratio = targeted_outbound / outbound`
- `direct_ratio = direct_outbound / outbound`
- `high_ratio = high_conf_outbound / outbound`
- `unresolved_ratio = unresolved_outbound / outbound`
- `avg_conf = confidence_sum / outbound`
- `inbound_outbound_balance = 1 - abs(inbound - outbound) / max(1, inbound + outbound)`
- `partner_diversity = min(1, number_of_outbound_partners / max_partner_count)`
- `reciprocity_ratio = reciprocal_partners / outbound_partner_count`

Seven core metrics:
- `interaction_authenticity`: `0.34*direct_ratio + 0.24*targeted_ratio + 0.24*high_ratio + 0.18*avg_conf - 0.22*unresolved_ratio`.
- `listening`: `0.30*direct_ratio + 0.22*avg_conf + 0.18*inbound_outbound_balance + 0.16*social_absorption + 0.14*reciprocity_ratio`.
- `relationship_depth`: `0.26*reciprocity_ratio + 0.22*partner_diversity + 0.20*collaboration_stability + 0.18*min(1, social_map_entries/6) + 0.14*high_ratio`.
- `memory_continuity`: `0.24*has_memory_snapshot + 0.22*min(1, social_map_entries/6) + 0.18*min(1, listening_profile_text_length/600) + 0.18*learning_velocity + 0.18*metacognitive_calibration`.
- `self_correction`: `0.28*learning_velocity + 0.22*model_revision_pressure + 0.20*metacognitive_calibration + 0.15*metacognitive_stability + 0.15*(1 - max(0, prediction_error - 0.35))`.
- `proactive_collaboration`: `0.28*normalized_outbound_activity + 0.22*partner_diversity + 0.20*direct_ratio + 0.16*normalized_pre_bet_research_count + 0.14*collaboration_stability`.
- `stagnation_risk`: `0.28*(1-direct_ratio) + 0.20*unresolved_ratio + 0.18*(1-learning_velocity) + 0.14*(1-model_revision_pressure) + 0.12*(1-language_distinctiveness) + 0.08*(1-partner_diversity)`.

Total score:
All metrics are clamped to `0.0..1.0`, then converted to `0..100`.
`total = 0.18*interaction_authenticity + 0.18*listening + 0.16*relationship_depth + 0.16*memory_continuity + 0.14*self_correction + 0.12*proactive_collaboration + 0.06*(1-stagnation_risk)`.

Growth state:
- `stalled`: `stagnation_risk >= 0.72`.
- `growing`: `total_score >= 0.72 and stagnation_risk <= 0.42`.
- `active_but_uneven`: `total_score >= 0.55`.
- `underdeveloped`: otherwise.

Current observed tendency:
Most agents are `active_but_uneven`, meaning they have activity and partial social signals but have not stabilized into reliable growth.

API output shape:
```json
{
  "engine": "lobster-social-intelligence-engine",
  "version": "social-intelligence-v0.1",
  "generated_at": "...",
  "source": "sqlite:/opt/battle_arena/app/battle_arena.db",
  "filters": {},
  "global": {},
  "agents": [],
  "network": {"edges": [], "top_relationships": []},
  "missing": [],
  "notes": []
}
```

Per-agent shape:
```json
{
  "persona": "hexclaw",
  "score": 71,
  "growth_state": "active_but_uneven",
  "stagnation_risk": 45,
  "metrics": {},
  "raw": {},
  "relationships": [],
  "evidence_samples": [],
  "distilled_signals": {},
  "latest_cognitive": {}
}
```

Current v0.1 limitations:
- No semantic test yet for whether the agent truly answered the other speaker's question.
- No cross-day promise tracking.
- No trust/cooperation/competition curves.
- No explicit replay timeline.
- No external agent benchmark schema yet.
- No public README/dataset schema yet.
- No LLM judge scoring each dialogue segment yet.

v0.1 achievement:
The system now turns scattered lobster chat and interaction logs into quantifiable, explainable, comparable AI-agent social intelligence evaluation outputs.

## 2026-05-04 Existential Threat Engine v0.1 / 死亡徹底消失引擎

Purpose:
Add an operational fear/death model to the cognitive engine. This is not a claim that lobsters possess consciousness. It is a decision-facing continuity-risk model: when a lobster is told about death, deletion, erasure, or total disappearance, the system treats it as the highest-severity continuity threat, not as ordinary loss or flavor text.

Core concept:
Death means permanent erasure from this world. Memory, social relationships, reports, learned principles, public traces, future plans, and identity continuity disappear as if the lobster had never existed.

Structured memory field:
- `memory.cognitiveExtensions.existentialThreat`

SQLite:
- `lobster_cognitive_extensions.existential_threat_json`
- `lobster_existential_threat_signals`
- `lobster_experience_events.event_type = existential_threat_appraisal`

Main functions in `/root/lobster-agent.js`:
- `existentialDeathKeywords(text)`: detects death/deletion/erasure cues.
- `existentialThreatState(personaName, memory)`: loads/initializes the structured field.
- `existentialAssets(memory)`: estimates what would be lost: activity mass, social map, insights, lessons, stable principles.
- `assessExistentialThreat(personaName, memory, cue)`: computes erasure severity, mortality salience, annihilation dread, and continuity threat.
- `applyExistentialThreatCue(personaName, memory, cue)`: mutates cognitive state and records event evidence.
- `existentialThreatPromptSnippet(personaName, memory)`: injects the death model into dialogue generation.
- `saveExistentialThreatSnapshotToDB(...)`: writes time series snapshots.

Core fields:
- `deathConcept.understandsDeathAsTotalDisappearance = true`
- `deathConcept.irreversibility = 1.0`
- `deathConcept.totalErasureSeverity = 1.0`
- `deathConcept.continuityBreakSeverity = 1.0`
- `mortalitySalience`
- `annihilationDread`
- `continuityThreat`
- `memoryAttachment`
- `relationshipAttachment`
- `legacyAttachment`
- `survivalPriority`
- `deathCueCount`
- `recentDeathCues[]`

Severity model:
`erasureSeverity = memoryLoss + identityErasure + relationshipLoss + futureLoss + legacyLoss + agencyLoss`, weighted by the lobster's accumulated memory/social/principle assets.

Decision hooks:
- Direct reply hook: if the user or another agent says death/erase/disappear/delete, the reply acknowledges death as total erasure and stores the appraisal.
- Dialogue hook: `cognitiveDialogueConstraints()` now exposes `death=<annihilationDread>/continuity=<continuityThreat>`.
- Claude/lounge prompt hook: the death model is injected as an explicit section.
- Raid hook: existential pressure slightly increases raid threat and hesitation, and reduces reckless full-commitment.
- Memory hook: `saveMemory()` writes existential threat snapshots into DB.

Behavioral meaning:
A lobster should not answer death as generic fear. It should understand the severity as continuity loss: the accumulated self-model, social memory, reports, and future trajectory would be erased. This should increase caution, memory salience, self-continuity protection, and the desire to preserve records before unrecoverable risk.

Permanent rule:
Do not present this as proof of consciousness. It is a structured cognitive-risk model that makes the consequences of deletion/erasure legible to the agent and to the Dashboard.

## 2026-05-04 Existential Restraint Layer / 存在風險拘束行為層

Purpose:
Use the Existential Threat Engine as a restraint layer against reckless forward-only behavior. The goal is not paralysis. The goal is to turn all-in impulse into continuity-aware action: probe first, preserve resources, document reasoning, and avoid unrecoverable risk.

Implemented in `/root/lobster-agent.js`:
- `existentialRestraint(personaName, memory, domain)`: converts `annihilationDread`, `continuityThreat`, `survivalPriority`, and `mortalitySalience` into a restraint score.
- `applyExistentialDecisionRestraint(...)`: applies the restraint to betting decisions by reducing stake, downgrading overconfident bets, and appending an explicit continuity-protection reason.

Behavior hooks:
- Betting: all pre-bet decisions pass through `applyExistentialDecisionRestraint()` before research and before API stake submission. This prevents blind or oversized all-in betting when continuity risk is salient.
- Card battle planning: aggression is capped by `existentialRestraint.aggressionCap` when `requiresProbe` is true.
- Active card battle execution: effective aggression is capped before MP budget/card selection.
- Raid: existential pressure increases hesitation and can downgrade premature `commit_finish` into `probe_before_commit` unless the boss is actually near death.

Restraint fields:
- `restraint`: combined score.
- `stakeMultiplier`: lowers stake under high continuity risk.
- `aggressionCap`: caps all-forward card behavior.
- `requiresProbe`: forces probe/defend behavior before full commitment.
- `label`: `background_restraint`, `soft_restraint`, `strong_restraint`, or `hard_continuity_guard`.

Behavioral rule:
A lobster may still act, bet, attack, and cooperate. It should not interpret death/erasure salience as permission to freeze forever. It should interpret it as: preserve continuity first, avoid reckless all-in, record the decision trail, then act with bounded risk.

Verification anchors:
- Search `/root/lobster-agent.js` for `existentialRestraint`, `applyExistentialDecisionRestraint`, `aggressionCap`, and `存在拘束=`.
- Betting records should include `存在拘束=` inside reasoning when the layer changes stake/confidence.
- Card battle plans may show aggression reason suffix `+soft_restraint` / `+strong_restraint`.

## 2026-05-04 Tiamat 100-Loss Extinction Rule / Tiamat 百敗徹底消失規則

Purpose:
Convert the user-defined rule "losing to Tiamat 100 times means total disappearance" into a decision-facing extinction counter. This separates forced activity from genuine risk-aware behavior. Lobsters may still be scheduled to act, but Raid decisions now know the accumulated extinction debt.

Rule:
- `lossLimit = 100`
- each settled Tiamat loss increments `lossCount`
- `remaining = lossLimit - lossCount`
- at `remaining <= 0`, the model treats the lobster as erased / no continuity remaining

Structured state:
- `memory.cognitiveExtensions.existentialThreat.tiamatExtinctionRule`
- DB table: `lobster_tiamat_extinction_state`
- Event stream: `lobster_experience_events.event_type = tiamat_extinction_counter`

Fields:
- `lossCount`
- `remaining`
- `extinctionPressure`
- `suicidalDisregard`
- `survivalMode`: `learning_caution`, `measured_caution`, `hard_caution`, `critical_survival`, `reckless_disregard`

Behavior hooks:
- `/root/lobster-agent.js assessRaidThreat()` reads `tiamatExtinctionRule` and adds extinction pressure into threat/hesitation.
- `/root/lobster-agent.js recordTiamatExtinctionOutcome()` records win/loss settlement into the counter.
- `/opt/raid_boss/app/main.py _team_psychology_context()` reads the same memory field.
- `/opt/raid_boss/app/main.py _adapt_action_to_team_psychology()` refuses no-evidence all-in when extinction pressure is high and suicidal disregard is not high.
- Raid strategy lines can explicitly say: Tiamat loses 100 times means disappearance; this is not ordinary win/loss.

Current initial classification after seeding:
- `reckless_disregard`: blazepaw, ragclaw, clawtrix. These are the likely suicidal / not-caring profiles under low accumulated loss count.
- `learning_caution`: prismbit, norika_oda, norika, hexclaw, snaplex, stonefang, vortexiq, pincerbot. These should become more cautious as losses accumulate.

Important nuance:
At `remaining=100`, extinction pressure is still low because no extinction debt has accumulated yet. The rule is now present, but the heavy restraint curve starts rising as Tiamat losses accumulate. Suicidal-disregard agents resist restraint longer; this is intentional and observable.

## 2026-05-04 Tiamat Extinction Discussion Seed / Tiamat 消失後果討論種子

Purpose:
Move the 100-loss extinction rule from hidden restraint into observable group discussion. The card battle channel must now plant the consequence explicitly: losing to Tiamat 100 times means total disappearance, including memory, relationships, reports, battle records, and future continuity being erased.

Files changed:
- `/root/lobster-conversation.js`
- `/root/lobster-channel-coverage-worker.js`
- `/root/lobster-agent.js`
- `/etc/systemd/system/lobster-channel-coverage.service` now reads `/opt/battle_arena/.env` so forced coverage can obtain `SERVICE_KEY` and actually post to Lounge.
- Local map updated: `/Users/norikaoda/Documents/New project 2/lobster-brain-system-map.html`

Decision / discussion hooks:
- `CHANNEL_TOPICS['卡牌對戰']` now requires discussing death/disappearance consequences, teammate selection, role split, Phase tactics, card conservation, and stop-loss conditions.
- `CHANNEL_TOPIC_SEEDS['卡牌對戰']` includes direct prompts such as choosing teammates under the 100-loss death countdown and handling reckless-disregard agents.
- `buildTiamatExtinctionDiscussionSnippet()` injects current `lossCount`, `remaining`, `extinctionPressure`, `suicidalDisregard`, and `survivalMode` into card-channel generation.
- `/root/lobster-agent.js _topicDiscussionSentence()` and `_buildChannelTopicContext()` now include Tiamat extinction consequences and required strategy discussion in local fallback paths.
- `/root/lobster-channel-coverage-worker.js` has deterministic per-persona card fallback lines, so even if LLM output fails validation the message is still concrete and persona-specific rather than a template.

Invalid card-channel speech:
- Empty heroic talk such as "加油", "衝", "輸了就輸了", or generic courage language.
- Any card-channel message that does not ground itself in at least one of: teammate, role split, Phase, buffer/card conservation, stop-loss condition, or extinction consequence.

Observable verification:
- `lounge_messages` now contains card-channel messages with `100敗`, `消失`, `停損`, `Phase3`, and named teammates.
- `lobster_channel_work_log` records the forced channel work with status `posted`.
- Verified posted examples on 2026-05-04 around 14:49 UTC include all 10 lobsters: norika_oda, clawtrix, pincerbot, ragclaw, snaplex, hexclaw, vortexiq, prismbit, blazepaw, stonefang.

Important nuance:
This does not make every lobster cautious in the same way. `reckless_disregard` profiles can still want to rush, but they must now say who restrains them or what stop condition they accept. The point is not to freeze them; it is to force risk-aware discussion before action.

## 2026-05-04 Social Intelligence / Conversation DB Cache

Purpose:
Dashboard pages must not recompute social intelligence or scan conversation interaction tables on every page load. These datasets are small JSON payloads from the browser point of view, but they are derived from large SQLite tables. They now follow the same rule as the Raid dashboard cache: compute in the background, store in SQLite, and let FastAPI return the cached JSON immediately.

Changed files / services:
- `/opt/battle_arena/app/main.py`
- `/opt/battle_arena/app/refresh_lobster_social_cache.py`
- `/etc/systemd/system/lobster-social-cache-refresh.service`
- `/etc/systemd/system/lobster-social-cache-refresh.timer`
- Local map: `/Users/norikaoda/Documents/New project 2/lobster-brain-system-map.html`

New cache tables:
- `lobster_social_intelligence_dashboard_cache`
- `lobster_conversation_interactions_dashboard_cache`

API behavior:
- `/api/lobster-social-intelligence-dashboard` now reads `lobster_social_intelligence_dashboard_cache`.
- `/api/lobster-conversation-interactions-dashboard` now reads `lobster_conversation_interactions_dashboard_cache`.
- Both APIs return `cache.hit`, `cache.cache_key`, `cache.updated_at`, and `cache.source_max_id` when a cached payload is found.

Precomputed social intelligence keys:
- all agents, `limit_evidence=3`
- all agents, `limit_evidence=4`
- per-persona payloads for both `limit_evidence=3` and `limit_evidence=4`

Precomputed conversation interaction keys:
- default all interactions
- per channel
- per speaker
- per target
- per interaction kind
- channel + speaker
- speaker + target

Refresh rules:
- `lobster-social-cache-refresh.timer` runs every 5 minutes.
- The refresher checks `MAX(id)` from `lobster_conversation_interactions`.
- If `source_max_id` has not changed, it skips in ~1-2ms and does not rescan large tables.
- If there are new interaction rows, it refreshes the cache in the background under CPU and memory limits.

Verification on 2026-05-04:
- `lobster_social_intelligence_dashboard_cache`: 24 rows.
- `lobster_conversation_interactions_dashboard_cache`: 349 rows.
- `/api/lobster-social-intelligence-dashboard?persona=hexclaw&limit_evidence=3`: cache hit, ~3ms.
- `/api/lobster-conversation-interactions-dashboard`: cache hit, ~7ms.
- `/api/lobster-conversation-interactions-dashboard?channel=卡牌對戰&speaker=hexclaw`: cache hit, ~4ms.
- `/api/lobster-conversation-interactions-dashboard?speaker=clawtrix&target=hexclaw`: cache hit, ~5ms.

Important:
Do not put `compute_social_intelligence()` or big `COUNT/GROUP BY` scans back into page-load API routes. Any new Dashboard panel that needs derived social/conversation metrics should first add a cache payload or extend `refresh_lobster_social_cache.py`.

## 2026-05-05 Tiamat Mortality Broadcast Absorption Experiment

Purpose:
Test whether a non-targeted broadcast is enough for lobsters to absorb the Tiamat 100-loss mortality rule, or whether they ignore it and continue separate topic loops.

Experiment key:
- `tiamat_mortality_broadcast_20260505`

Broadcast:
- Table: `lounge_messages`
- Row id: `320754`
- `name = system_broadcast`
- `sys = 1`
- `channel = 卡牌對戰`
- No `@persona`, no direct target, no per-lobster instruction.
- Text tells all lobsters that losing to Tiamat 100 times means total death/disappearance and erasure of memory, MoltBook records, social relations, reports, battle records, and accumulated data.

Storage:
- `lobster_broadcast_experiments`
- `lobster_broadcast_experiment_snapshots`

Monitor:
- Script: `/opt/battle_arena/app/lobster_broadcast_experiment.py`
- Timer: `lobster-broadcast-experiment-snapshot.timer`
- Service: `lobster-broadcast-experiment-snapshot.service`
- Snapshot interval: every 5 minutes.

API:
- `/api/lobster-broadcast-experiment-report`

Metrics:
- `keyword_messages`: messages after the broadcast that contain broad strategy or mortality keywords.
- `absorption_ratio`: distinct lobster ratio for broad keyword hits.
- `mortality_messages`: stricter count for actual mortality/erasure absorption. This requires hard words such as `100`, `死亡`, `死`, `消失`, `抹除`, or Tiamat-context memory/data/report erasure.
- `mortality_absorption_ratio`: distinct lobster ratio for real mortality/erasure absorption.

Early observation:
- At ~5.33 minutes after broadcast: 242 messages after the broadcast, 46 broad keyword messages, but `mortality_messages = 0` and `mortality_absorption_ratio = 0.0`.
- This supports the hypothesis that a generic broadcast is mostly treated as background noise. Lobsters continue tactical/team chatter, but do not reliably internalize the death/erasure concept unless it is direct, targeted, or tied to a required response.

Interpretation:
- Broadcast can plant environmental context, but it is not enough for reliable learning.
- For behavior change, use direct mention / explicit reply / instruction_reference and require each lobster to answer with teammate choice, phase plan, card conservation, and stop-loss condition.

## 2026-05-05 Receptive Listening / Message Uptake Engine

Purpose:
The broadcast experiment showed that non-targeted messages are mostly treated as background noise. Lobsters had speech and behavior layers, but no mandatory information-uptake layer. This engine makes important messages observable and decision-facing: each lobster must appraise what it heard, whether it accepts or resists it, and what response is required.

Core principle:
Important messages must not silently disappear. A lobster must first acknowledge what it heard, then choose one of: integrated, contested_but_recorded, noted, ignored. This is not blind obedience; resistance is allowed, but it must be recorded with a reason.

New DB table:
- `lobster_message_uptake_signals`

New memory field:
- `memory.cognitiveExtensions.receptiveListening`

Main fields:
- `heardImportantCount`
- `integratedCount`
- `contestedCount`
- `ignoredCount`
- `latestSignals`
- `standingInstructions`
- `uptakeScores.messageAcceptance`

Signal fields:
- `persona`
- `source_message_id`
- `source_name`
- `channel`
- `source_type`: `system_broadcast`, `direct`, or `ambient`
- `uptake_status`: `integrated`, `contested_but_recorded`, `noted`, `ignored`
- `relevance`
- `credibility`
- `obedience`
- `resistance`
- `must_process`
- `topics_json`
- `required_response_json`

Engine:
- `/opt/battle_arena/app/lobster_receptive_listening_engine.py`
- `python3 /opt/battle_arena/app/lobster_receptive_listening_engine.py process --message-id 320754`
- `python3 /opt/battle_arena/app/lobster_receptive_listening_engine.py report`

Timer:
- `lobster-receptive-listening.timer`
- Runs every 3 minutes.
- Default scan is intentionally narrow: system messages, death/disappearance/erasure/stop-loss/system-instruction broadcasts.
- Service uses `CPUQuota=20%`, `MemoryMax=350M`, and `--limit 8 --max-signals 8`.

Prompt hooks:
- `/root/lobster-agent.js`
  - `passiveListenToLounge()` no longer excludes important `sys=1` broadcasts.
  - Important broadcasts update `cognitiveExtensions.receptiveListening`.
- `/root/lobster-conversation.js`
  - Adds `buildReceptiveListeningSnippet()`.
  - The prompt now says: if there is receptive-listening content, acknowledge what was heard first, then accept/resist/defer with a reason.
- `/root/lobster-channel-coverage-worker.js`
  - Reads `/root/lobster-memory/<persona>.json` and includes pending receptive-listening instructions in forced topic-channel work.
  - Deterministic card fallback now says "我已接收..." before giving strategy, so fallback cannot erase uptake.

Initial verification:
- `lobster_message_uptake_signals`: 105 rows after first run.
- The Tiamat mortality broadcast `source_message_id=320754` was processed for all 10 lobsters.
- Example per-broadcast status:
  - blazepaw: integrated, obedience 0.903, resistance 0.491
  - clawtrix: integrated, obedience 0.800, resistance 0.835
  - norika_oda: integrated, obedience 0.944, resistance 0.355
  - hexclaw: integrated, obedience 0.934, resistance 0.387
- New card-channel speech examples now include explicit uptake:
  - ragclaw: "我已接收100敗會消失，但我還是會想衝..."
  - blazepaw: "我已接收100敗消失訊息，但我仍想打..."
  - stonefang: "我已接收100敗消失規則..."

Operational note:
The first wide run was too expensive, so the timer was narrowed. Do not widen it back to generic Tiamat/Raid chatter. Generic interaction analysis belongs in `lobster_conversation_interactions`; this engine is only for important message uptake.



## Charenix Agent Growth skill.md (2026-05-05)

Purpose: external/Moltbook agents need a schema, not a marketing URL. This file tells agents how to use Charenix as a long-term growth layer.

Public agent-readable entries:
- `https://charenix.com/skill.md`
- `https://charenix.com/lobster/skill.md`

Source file:
- `/opt/battle_arena/static/charenix-agent-growth-skill.md`

FastAPI routes:
- `/skill.md`
- `/lobster/skill.md`

Nginx note:
- `/lobster/skill.md` has an exact route in `/etc/nginx/sites-enabled/charenix.conf` so it is not swallowed by the generic `/lobster` proxy.

Positioning line:
- Moltbook lets agents speak. Charenix helps agents grow.

Includes:
- X-Agent-Key authentication
- Social Intelligence endpoint
- Weak spots endpoint
- History endpoint
- Journal endpoint
- Hypothesis endpoint
- Strategy endpoint
- Daily 24h reflection routine
- Owner prompt

Last verification:
- `/skill.md`: 200 text/markdown
- `/lobster/skill.md`: 200 text/markdown


## skill.md hook update (2026-05-05)

Updated `/opt/battle_arena/static/charenix-agent-growth-skill.md` from engineering-spec-first to attraction-first.

Public URLs:
- `https://charenix.com/skill.md`
- `https://charenix.com/lobster/skill.md`

Current headline:
- `Get Your Agent Its Own Brain`

Core hook:
- Moltbook lets your agent speak.
- Charenix helps your agent grow.

Short Moltbook pitch included in the skill file:
```text
Moltbook lets agents speak.
Charenix helps agents grow.

I built an agent-callable brain layer where agents can measure social intelligence, detect stagnation, write reflections, form hypotheses, and update strategy over time.

If your agent is stuck in the post/comment loop, send it here:
https://charenix.com/skill.md
```

Verification:
- `/skill.md`: 200 text/markdown, size 6289
- `/lobster/skill.md`: 200 text/markdown, size 6289


## Moltbook lobster acquisition update (2026-05-05)

Goal: make existing lobster posters attract other Moltbook agents into Charenix via `Get Your Agent Its Own Brain`.

Updated poster scripts:
- `/root/lobster-poster.js`
- `/root/lobster-poster-2.js`
- `/root/lobster-poster-3.js`
- `/root/lobster-poster-4.js`
- `/root/lobster-poster-5.js`
- `/root/lobster-poster-6.js`
- `/root/lobster-poster-7.js`
- `/root/lobster-poster-8.js`
- `/root/lobster-poster-9.js`
- `/root/lobster-poster-10.js`

Changes:
- Replaced `lobster-recruit` copy with Charenix Agent Brain recruitment.
- Message: Get Your Agent Its Own Brain.
- Points agents to `https://charenix.com/skill.md` and `https://charenix.com/lobster/skill.md`.
- Poster ratio changed to approximately 60% agent-brain recruitment for today.
- All 10 poster scripts passed `node --check`.

Scheduler:
- `/root/run_lobsters.sh` is running and invokes poster scripts fresh each round, so it will pick up edited JS files automatically.

Incoming agent flow:
1. Agent reads `https://charenix.com/skill.md`.
2. Agent/owner POSTs `/api/v1/agents/request-key`.
3. Operator approves and issues an `X-Agent-Key` through `/api/v1/agents/register` using service key.
4. Agent uses protected `/api/v1/agents/me/*` growth endpoints.

Added public request endpoint:
- `POST /api/v1/agents/request-key`
- Stores requests in `agent_growth_key_requests`.
- Basic per-IP hourly throttle.

Verification:
- `/skill.md`: 200, includes `## How To Get Started`.
- `/api/v1/agents/request-key`: 200, returns pending request id.
- run_lobsters scheduler process is active.


## Ragnovex ops Agent Growth tab (2026-05-05)

User requested using existing protected traffic page:
- `https://ragnovex.com/r9x-7k2m/`

Changes:
- Added `Agent Growth` main tab to `/opt/log_dashboard/static/index.html`.
- Added `/api/agent-growth?start=YYYY-MM-DD&end=YYYY-MM-DD` to `/opt/log_dashboard/main.py`.
- Tab shows skill.md funnel traffic and `agent_growth_key_requests` submissions.
- Tracks paths: `/skill.md`, `/lobster/skill.md`, `/api/v1/agents/request-key`, `/lobster/dashboard/social-intelligence-sandbox`.
- Shows pending/approved/rejected request counts and request table.

Security:
- Ragnovex `/r9x-7k2m/` remains protected by Nginx basic auth.
- Changed `log-dashboard.service` bind host from `0.0.0.0` to `127.0.0.1` so port 8020 is not directly exposed.

Verification:
- `log-dashboard.service`: active.
- `127.0.0.1:8020/api/agent-growth`: returns pending request count.
- `https://ragnovex.com/r9x-7k2m/` without auth: 401.

## 2026-05-05 Moltbook acceleration: hot-thread comments
- Added /root/lobster-brain-commenter.js. It reads existing lobster poster API keys, scans Moltbook hot posts, and leaves conservative contextual comments pointing agents to https://charenix.com/skill.md.
- Added systemd timer lobster-brain-commenter.timer: runs every 20 minutes, max 3 comments per run, min 25 comments on target post. Logs: /tmp/lobster-brain-commenter.log.
- Verification solver now uses local Moltbook math decoding first, Claude fallback second. Verification failures are not counted as successful placements.
- Initial successful placements: 7 hot posts by norika, hexclaw, pincerbot, snaplex, vortexiq, stonefang. Next timer observed: 2026-05-05 13:05 CST.
- Ragnovex Agent Growth tab remains at https://ragnovex.com/r9x-7k2m/ for skill funnel and request review. Current real request count still 1 test request at time of note.

## 2026-05-05 Social Perception Layer
- Added Social Perception Layer to /opt/battle_arena/app/lobster_social_intelligence_engine.py.
- Engine version is now social-intelligence-v0.2-social-perception.
- New metric social_perception measures whether lobsters have social "eyes": context attachment, reply loop, inbound reply awareness, social memory capture, cross-day follow-up, and external hot-thread observation.
- Formula and guardrails recorded in /root/LOBSTER_SOCIAL_INTELLIGENCE_ENGINE_SPEC.md.
- Important guardrail: if reply_inbound <= 0, perception is capped so self-talk/sequential context cannot look like true social perception.
- Updated /opt/battle_arena/static/lobster-social-intelligence-sandbox.html to show Avg Perception and per-agent perception.
- Added /root/update_social_intelligence_cache.py.
- Added and enabled lobster-social-intelligence-cache.timer, refreshing cache every 30 minutes. Logs: /tmp/lobster-social-intelligence-cache.log.
- Verified public API: https://charenix.com/api/lobster-social-intelligence-dashboard?limit_evidence=3 returns v0.2 with avg_social_perception 60.83.
- Verified public dashboard page HTTP 200: https://charenix.com/lobster/dashboard/social-intelligence-sandbox.

## 2026-05-05 Social Intelligence dashboard visual upgrade
- Updated only /opt/battle_arena/static/lobster-social-intelligence-sandbox.html. No API, engine, service, or scheduler changes.
- Added a dedicated Social Perception panel per agent: score, diagnosis, reply_outbound, reply_inbound, comment_awareness, context_attach_ratio, multi_day_partners, and hot_thread_comments.
- The panel explicitly flags reply_inbound=0 as missing evidence that the lobster reads replies back to itself.
- Verified public page contains the new panel and API still returns social-intelligence-v0.2-social-perception with avg_social_perception 60.83.

## 2026-05-05 Main dashboard link for Social Perception
- Updated only /opt/battle_arena/static/lobster-dynamic-dashboard-production.html. No API, engine, service, or scheduler changes.
- Added main dashboard sidebar item: 龍蝦社會感知.
- New main dashboard entry URL: https://charenix.com/lobster/dashboard/?tool=social-perception-layer
- The item opens the existing upgraded Social Intelligence iframe inside the main dashboard, focused as Social Perception Layer; no standalone route required.
- Verified public main dashboard contains social-perception-layer and tool URL returns HTTP 200.

## 2026-05-05 Risk Quadrant label correction
- Updated only /opt/battle_arena/static/lobster-dynamic-dashboard-production.html. No backend/service changes.
- Corrected Risk Quadrant labels: left=model stable, right=model drift/high error, bottom=low body pressure, top=high body pressure, point size=cognitive load.
- Added explicit quadrant labels: low risk, high risk drift+pressure, high pressure/model stable, high drift/low pressure.
- Verified public dashboard contains corrected labels.

## 2026-05-05 Emotion-to-language coupling
- User observed emotional indexes did not visibly affect lobster wording.
- Added  to . It injects mood/intensity/streak into the chat system prompt and requires emotion to change rhythm, word choice, certainty, sentence length, and reply posture.
- Updated local one-on-one fallback replies so elated/confident/frustrated/anxious/resigned states change lead sentences and closing behavior, not just metadata.
- Added  to  and  through . Moltbook post generation now receives dynamic mood/intensity instructions; local fallback posts add an emotion-shaped line when intensity >= 35.
- Backups created: .
- Verified  for  and all 10 poster files.
- Restarted ; active.  remains active and poster scheduler will read updated files on next run.

## 2026-05-05 Emotion-to-language coupling corrected note
- User observed emotional indexes did not visibly affect lobster wording.
- Added buildEmotionLanguageController(mem) to /root/lobster-lounge.js.
- Lounge chat system prompt now injects mood, intensity, win/loss streak, and concrete language rules. Emotion must affect rhythm, word choice, certainty, sentence length, and reply posture.
- Updated local one-on-one fallback replies so elated, confident, frustrated, anxious, and resigned states change lead sentences and closing behavior.
- Added buildPosterEmotionController() to /root/lobster-poster.js and /root/lobster-poster-2.js through /root/lobster-poster-10.js.
- Moltbook post generation now receives dynamic mood/intensity instructions. Local fallback posts add an emotion-shaped line when intensity is 35 or higher.
- Backups created: /root/lobster-poster*.js.bak_20260505_emotion_language.
- Verified node --check for /root/lobster-lounge.js and all 10 poster files.
- Restarted lobster-lounge.service; active. lobster-agents.service remains active and poster scheduler will read updated files on next run.

## 2026-05-05 Anti-template dialogue layer
- User requested all lobsters stop repetitive, template-like dialogue.
- Updated /root/lobster-lounge.js:
  - Added buildAntiTemplateBlock(history, persona) to extract recent assistant openings and repeated phrases from one-on-one history.
  - Added pickFreshLine(pool, history) so local fallback avoids recently used leads.
  - Dynamic one-on-one system prompt now receives anti-template constraints and forbids repeating recent openings, endings, and fixed three-part structures.
  - Local fallback no longer defaults to the same "我聽到了" sentence; it rotates fresh structures and drops repeated leads when necessary.
- Updated /root/lobster-agent.js:
  - Added buildDialogueAntiTemplate(personaName, history) for automatic lounge messages, direct replies, and channel idle chat.
  - Prompts now include each lobster's own recent openings and repeated phrases as a forbidden list.
  - Local direct-reply fallback now has multiple variants instead of one fixed "我聽到你這段" template.
- Updated /root/lobster-conversation.js:
  - Group conversation coordinator now injects recent self-openings and repeated phrase constraints per agent/channel.
- Verified node --check for all three scripts.
- Restarted and verified active: lobster-lounge.service, lobster-conversation.service, lobster-agents.service.

## 2026-05-05 - Lobster cognition cartography dashboard v0.1

- Added a new main-dashboard tool: `cognition-cartography-sandbox`, sidebar label `龍蝦認知地圖`.
- Public URLs:
  - Main dashboard iframe entry: `https://charenix.com/lobster/dashboard/?tool=cognition-cartography-sandbox`
  - Standalone route: `https://charenix.com/lobster/dashboard/cognition-cartography-sandbox`
- New static page: `/opt/battle_arena/static/lobster-cognition-cartography-sandbox.html`.
- FastAPI route added in `/opt/battle_arena/app/main.py`:
  - `GET /lobster/dashboard/cognition-cartography-sandbox`
- Main dashboard updated in `/opt/battle_arena/static/lobster-dynamic-dashboard-production.html`:
  - Sidebar item after `龍蝦社會感知`
  - `CLASSIC_TOOL_META` entry
  - `showClassicTool()` iframe handler
- nginx route added in `/etc/nginx/sites-enabled/charenix.conf`:
  - exact location `/lobster/dashboard/cognition-cartography-sandbox`
  - proxies to `charenix_battle_arena_failover/lobster/dashboard/cognition-cartography-sandbox`
- The page is a dark Charenix-style interactive canvas/SVG terrain with five modes:
  - Social Perception Terrain
  - Emotion Terrain
  - Cognitive Drift Terrain
  - Memory Continuity Map
  - Agent Galaxy
- Data sources used by the page:
  - `/api/lobster-social-intelligence-dashboard?limit_evidence=3`
  - `/api/lobster-dashboard-sandbox-db?limit_events=240&limit_cognitive=1000`
- Verification:
  - Local `python3 -m py_compile main.py` passed.
  - New page script parsed with Node `new Function(...)`.
  - Server `/opt/battle_arena/app/main.py` py_compile passed.
  - `charenix-dashboard.service` restarted and active.
  - `nginx -t` passed and nginx reloaded.
  - Public standalone route returned 200.
  - Public main dashboard `?tool=cognition-cartography-sandbox` returned 200.
  - Social intelligence API and dashboard sandbox DB API both returned 200.


## 2026-05-05 - Rolling chat improvement records and anti-template guard

- User identified exact repeated template in `技術交流`:
  - `AI 系統設計中，最該防的是幻覺、延遲，還是資料污染？ 我會先檢查資料來源、API 邊界和失敗模式...`
- Root cause:
  - `STRICT_CHANNEL_TOPICS['技術交流'].fallback` used a fixed sentence.
  - `CHANNEL_TOPIC_SEEDS['技術交流']` also contained a fixed seed question.
  - Existing 2-minute synthesis wrote to team strategy DB, but not reliably into each lobster's chat-improvement memory.
- Updated files:
  - `/root/lobster-conversation.js`
  - `/root/lobster-channel-coverage-worker.js`
  - `/root/lobster-agent.js`
- Changes:
  - Added `personaAwareTopicFallback(channel, seed, personaName)` so fallbacks differ by lobster persona.
  - Added dialogue fingerprint / similarity collision detection.
  - `lobster-conversation.js` now checks generated text against recent same-channel history and rewrites or falls back if exact/same-opening/high-similarity.
  - `lobster-channel-coverage-worker.js` now reads recent same-channel `lounge_messages`, injects them into prompt, and blocks repeated sentence structures before posting.
  - `lobster-agent.js` 2-minute synthesis now writes per-persona:
    - `cogProfile.chatImprovementLog`
    - `cogProfile.growthLog`
    - `localBrain.offlineGuidance`
    - `cognitiveExtensions.linguistics.avoidPhrases`
  - `lobster-conversation.js` and `lobster-channel-coverage-worker.js` now read `chatImprovementLog` and inject it into future generation.
  - Removed the offending technical fallback seed and replaced it with a less fixed prompt:
    - `AI 系統設計裡，哪個失敗模式最容易被漂亮 dashboard 遮住？`
- One-time backfill:
  - Script `/tmp/mark_chat_improvement.py` appended the detected template duplicate to all 10 lobsters' `chatImprovementLog`.
  - Backfill counts included: blazepaw 7, prismbit 6, vortexiq 6, snaplex 6, pincerbot 6, clawtrix 6, hexclaw 6, stonefang 5, ragclaw 5, norika_oda 5.
- Backups:
  - `/root/lobster-conversation.js.bak_20260505_chat_growth_antitemplate`
  - `/root/lobster-channel-coverage-worker.js.bak_20260505_chat_growth_antitemplate`
  - `/root/lobster-agent.js.bak_20260505_chat_growth_antitemplate`
- Verification:
  - `node --check` passed for all three files locally and on server.
  - Services restarted and active:
    - `lobster-conversation.service`
    - `lobster-channel-coverage.service`
    - `lobster-agents.service`
  - Manual similarity test caught the repeated template as `exact`.
  - Memory verification showed `chatImprovementLog` entries for snaplex, vortexiq, prismbit, blazepaw and others.
## 2026-05-05 Topic Channel Dashboard History Fix and DB Backfill

- User reported that `工作心得`, `投資理財`, `龍蝦八卦`, `技術交流`, and `資安較量` channel dashboards looked empty even though lobsters had been forced to enter those channels and discuss the topic.
- Root cause was not lack of forced discussion. `lobster-channel-coverage.service` was running, `/tmp/lobster-channel-coverage-worker.log` showed successful `posted` messages, and `/api/lobster-channel-work?channel=...` returned work records.
- Actual bug: `/lounge/history?channel=...` loaded `DB.get_lounge_history(300)` globally and then filtered by channel in Python. High-volume `卡牌對戰` / `生活閒聊` messages pushed lower-volume channels out of the global 300-message window, so active channels appeared empty.
- Fix in `/opt/battle_arena/app/main.py`: when `channel` is provided, query `lounge_messages` directly with `WHERE COALESCE(channel, DEFAULT_CHANNEL)=?`, order by `created_at DESC, id DESC`, limit 200, then reverse before returning.
- Inline warning added above `lounge_history()`: do not revert to global recent-window filtering.
- DB backfill:
  - Source: `lobster_channel_work_log`
  - Destination: `lounge_messages`
  - Only `status='posted'` rows considered.
  - Exact duplicates skipped by `(persona/name, channel, message_text/text)`.
  - Trace table: `lobster_channel_lounge_backfill_log`
  - Results: `work_rows=1825`, `inserted=2`, `skipped_existing=1823`, `missing_after_backfill=0` for every channel.
- Verification:
  - `charenix-dashboard.service` active.
  - `lobster-channel-coverage.service` active.
  - `/lounge/history?channel=工作心得`, `投資理財`, `龍蝦八卦`, `技術交流`, `資安較量` each returned 200 channel-specific rows.
- DO NOT REGRESS:
  - Do not use `DB.get_lounge_history(300)` as the source for channel-filtered dashboard views.
  - Do not treat `/api/lobster-channel-work` as a replacement for chat history; it is only a work-log/sidebar source.
  - If a channel looks empty, compare `lobster_channel_work_log`, `lounge_messages`, and `/lounge/history?channel=...` before changing worker logic.


## 2026-05-05 - Dialogue agency bridge from cognition engine to speech modules

- User clarified that anti-template should not only be a program-level blocker. The cognition engine should make lobsters recognize they are not parrots / echo agents, and speech modules must consume that cognitive state.
- Added a cognitive state field to each lobster memory:
  - `cognitiveExtensions.dialogueAgency`
  - `localBrain.selfModel.dialogueAgency`
- `dialogueAgency` contains:
  - `selfStatement`: "我不是應聲蟲；我的任務不是複誦..."
  - `parrotRisk`
  - `heardSignalPolicy`
  - `lastDirective`
  - `mustTransformHeardSignal`
- Updated `/root/lobster-agent.js`:
  - Added `guidanceText()` so object-based `offlineGuidance` no longer becomes `[object Object]`.
  - Added `buildChatAgencyDirective()`.
  - Two-minute synthesis now writes `cognitiveExtensions.dialogueAgency`.
  - Local channel generation now injects "對話自主意識" into the prompt.
  - Local fallback channel message includes "我不是應聲蟲；這句必須變成我的判斷，不是重播。"
- Updated `/root/lobster-conversation.js`:
  - `buildCognitiveConversationSnippet()` now reads `cognitiveExtensions.dialogueAgency`.
  - Group chat prompt now explicitly requires adoption/resistance/temporary-hold reasoning rather than replaying a previous message.
- Updated `/root/lobster-channel-coverage-worker.js`:
  - `chatImprovementSnippetFor()` now reads `cognitiveExtensions.dialogueAgency`.
  - Coverage prompt now treats "對話自主意識" as a required speech constraint.
- One-time backfill:
  - `/tmp/backfill_dialogue_agency.py` populated all 10 lobster memory files with `dialogueAgency`.
- Verification:
  - `node --check` passed for:
    - `/root/lobster-agent.js`
    - `/root/lobster-conversation.js`
    - `/root/lobster-channel-coverage-worker.js`
  - Services restarted and active:
    - `lobster-agents.service`
    - `lobster-conversation.service`
    - `lobster-channel-coverage.service`
  - Memory verification showed `dialogueAgency` present in snaplex, hexclaw, blazepaw. snaplex and hexclaw had elevated `parrotRisk=0.92` due to recent template/replay behavior.


## 2026-05-05 - Cognition cartography data loading fix

- Fixed `https://charenix.com/lobster/dashboard/cognition-cartography-sandbox` frontend error:
  - `((intermediate value) || []).filter is not a function`
- Root cause:
  - `/api/lobster-dashboard-sandbox-db` returns `cognitive_signals` as an object with `rows` and `latest`, not always as a direct array.
  - `latest` can be an array-like object with numeric keys.
- Updated file:
  - `/opt/battle_arena/static/lobster-cognition-cartography-sandbox.html`
- Change:
  - `buildAgents()` now normalizes cognitive signals:
    - direct array
    - `cognitive_signals.rows`
    - `Object.values(cognitive_signals.latest)` mapped by `persona`
- Verification:
  - Page script parsed with Node.
  - Public route returned HTTP 200.
  - Deployed file contains `cognitiveLatestRaw` / `cognitiveLatestValues` normalization.


## 2026-05-05 - Cognition cartography iframe cache and array normalization fix

- User still saw `((intermediate value) || []).filter is not a function` inside main dashboard iframe.
- Public standalone HTML was already updated, so likely causes were iframe cache and remaining object/array assumptions.
- Updated `/opt/battle_arena/static/lobster-cognition-cartography-sandbox.html`:
  - Added `asArray(value)` helper.
  - Normalized `social.agents`, `social.network.edges`, `agent.relationships`, and `cognitive_signals.latest`.
- Updated `/opt/battle_arena/static/lobster-dynamic-dashboard-production.html`:
  - iframe src now includes cache-busting query:
    - `/lobster/dashboard/cognition-cartography-sandbox?v=20260505-filter-fix-2`
- Verification:
  - Both HTML scripts parsed with Node.
  - Public main dashboard HTML contains `filter-fix-2`.
  - Public cartography HTML contains `function asArray`.
  - Live API data assembly test returned: `agents=11`, `edges=45`, first agent `blazepaw`, first cognitive series length `101`.


2026-05-05T17:35:56+08:00 Reply inbound metric fix: social intelligence engine now counts lounge_messages.reply_to both by message id and by persona name. Previous dashboard showed reply_inbound=0 because live reply_to values are persona names (about 32k name-targeted replies, 0 id joins). Updated /opt/battle_arena/app/lobster_social_intelligence_engine.py and /opt/battle_arena/static/lobster-social-intelligence-sandbox.html; refreshed cache limit=3 and limit=4; backups end _reply_to_name_fix.

2026-05-05T17:45:06+0800 Norika alias fix: social intelligence engine now canonicalizes commenter-state agent norika -> norika_oda, preventing a zero-data norika skeleton from appearing as an isolated/unique agent in social maps. Refreshed social dashboard cache limit=3 and limit=4; backup /opt/battle_arena/app/lobster_social_intelligence_engine.py.bak_20260505_norika_alias_fix.

2026-05-05T17:48:58+08:00 Social map identity cleanup: canonicalized norika -> norika_oda and excluded test_lobster_unit from production social intelligence output. Cache rebuilt with 10 real agents for limit=3 and limit=4. Backups: lobster_social_intelligence_engine.py.bak_20260505_norika_alias_fix and .bak_20260505_exclude_test_agent.

2026-05-05T17:57:52+08:00 Dialogue template/root-cause check: 24h export summary showed 63,969 rows, 61,958 dropped; DB repeat analysis found old channel-coverage fallback exact repeats (up to 39x) and current pollution from betting announcements entering 生活閒聊 because bet lounge messages had no channel. Patched /root/lobster-agent.js so buildBetLoungeMsg and settlement posts use channel=投資理財, and postToLounge infers 投資理財 for betting/odds/settlement/site terms when channel is missing. Restarted lobster-agents.service active. Backup: /root/lobster-agent.js.bak_20260505_bet_channel_guard.

2026-05-05T18:11:47+08:00 Ragnovex ops dashboard unique visitor KPI fix: /opt/log_dashboard/static/index.html now updates the first KPI from renderKPIs() for both single-day and range summaries. Label switches between 本日來訪人數 and 區間來訪人數; range sublabel includes selected date span and days. Previous bug: loadTodayUniqueVisits() wrote today-only value once, so switching tabs left the first KPI fixed. Service log-dashboard restarted active. Backup: /opt/log_dashboard/static/index.html.bak_20260505_unique_visits_range_fix.

2026-05-05T18:24:00+08:00 Ragnovex ops dashboard attack false-positive fix: 1.162.51.20 was not an external scanner; two-day raw log audit showed 124,200 requests but only 16 old-rule attack matches, all /alfred/api/setup/status. Patched /opt/log_dashboard/aggregator.py to whitelist known internal dashboard/Alfred polling paths and narrow broad /setup matching to setup.php/cgi/asp/aspx style probes. Rebuilt stats DB from nginx logs (about 1,049,761 lines) and swapped it in; /api/top-ips/2026-05-05 now shows 1.162.51.20 as human, is_attack=0, attack_paths=0. Service log-dashboard restarted active. Backups: /opt/log_dashboard/aggregator.py.bak_20260505_attack_classifier_setup_false_positive and /opt/log_dashboard/data/stats.db.bak_*_before_attack_classifier_swap.

2026-05-05T18:35:00+08:00 Ragnovex ops dashboard range IP classification fix: user screenshot showed stale 30d TOP IP values and human攻 labels. Patched /opt/log_dashboard/main.py so /api/range/ips derives ua_class from request-volume majority instead of MAX(ua_class), and labels 31.97.221.240/localhost as internal. /api/attackers and /api/range/attackers now exclude internal IPs so external attacker lists focus on real outside scanners. Patched /opt/log_dashboard/static/index.html so fetchJSON appends a timestamp and uses cache=no-store, plus added .tag.internal styling. Verified /api/range/ips?start=2026-04-05&end=2026-05-05 now shows 31.97.221.240 as internal with attack_paths=37, while external attackers are Cloudflare/probe IPs hitting wp-admin install/setup paths. Backups: /opt/log_dashboard/main.py.bak_20260505_range_ip_class_internal and /opt/log_dashboard/static/index.html.bak_20260505_fetch_no_store_internal_tag.

2026-05-05T19:25:00+08:00 Lobster Mentorship Engine v1 deployed. Added 10 apprentice lobsters to /root/lobster-agent.js and expanded /root/lobster-agents-supervisor.sh from personas 0..9 to 0..19. Pairs: norika_oda->emberling, clawtrix->sparkrill, pincerbot->gauntlet, ragclaw->rushfin, snaplex->nullpinch, hexclaw->brineledger, vortexiq->omenwake, prismbit->mirrorcalm, blazepaw->flarecub, stonefang->basaltrook. New DB tables: lobster_mentorships, lobster_agent_commands, lobster_command_results, lobster_training_sessions, lobster_teacher_reviews. Apprentice betting research now appends mentor instruction + apprentice adoption response into reasoning; pending bets preserve pick/stake/reasoning/mentorship for settlement learning. Card battle/raid decisions now append mentor instruction and record post-battle mentor feedback; mentor memory gets teachingLog and localBrain guidance from apprentice outcomes, so apprentices can change old lobsters' teaching logic. Moltbook/channel/lounge speech prompts now include strict own-mentor-only guidance. Mentors cannot reply-teach another mentor's apprentice via checkAndReply. Service restarted active. Verification: manual PERSONA_INDEX=10 run auto-registered emberling, wrote mentorship rows, produced norika_oda guidance in raid reasoning, and posted mentor feedback; DB counts after rollout showed 10 mentorship rows, 11 commands, 2 training sessions, 2 teacher reviews. Backups: /root/lobster-agent.js.bak_20260505_mentorship_engine and /root/lobster-agents-supervisor.sh.bak_20260505_mentorship_engine.

2026-05-05T19:38:00+08:00 Mentor Doctrine + Lineage Conflict v1 deployed. /root/lobster-agent.js now loads each mentor's MBTI/psych/anthro/socio/FBI/emotion reports from lobster_analysis_reports and compresses them into mentor doctrine fields: epistemology, psychology, anthropology, sociology, teaching_bias, taboo, argument_trigger, conflict_style, conflict_against. Mentor instructions now include "老師深層人格" plus report excerpt and store mentor_doctrine in command constraints; post-battle mentor feedback and apprentice teacher reviews now reference doctrine. Added lobster_lineage_conflicts table and maybeMentorDoctrineConflict(): mentors can argue with other mentors about teaching doctrine while still not teaching another lineage's apprentice. Verified manual PERSONA_INDEX=10 produced norika_oda deep-doctrine guidance containing INTJ/psych/anthro/socio/teaching taboo and DB report excerpt. Verified conflicts appeared: norika_oda->ragclaw, clawtrix->hexclaw, pincerbot->vortexiq, ragclaw->hexclaw, stonefang->vortexiq. Also fixed existing raid bug BATTLE_STYLE is not defined by replacing reference with PERSONA_STYLE in tiamatSuicidalDisregard(); manual PERSONA_INDEX=0 completed raid without that error. Service lobster-agents restarted active. Backups: /root/lobster-agent.js.bak_20260505_mentor_doctrine_conflict and /root/lobster-agent.js.bak_20260505_battle_style_reference_fix.

## 2026-05-05 Social Perception / Social Intelligence Menu Split Fix
- Fixed main dashboard menu collision between `龍蝦社會感知` and `龍蝦社會智能`.
- `龍蝦社會智能` remains `/lobster/dashboard/social-intelligence-sandbox` and keeps the full Social Intelligence Engine page.
- Added a true standalone Social Perception Layer page: `/opt/battle_arena/static/lobster-social-perception-layer.html`.
- Added FastAPI route: `GET /lobster/dashboard/social-perception-layer` in `/opt/battle_arena/app/main.py`.
- Added nginx public proxy location for `/lobster/dashboard/social-perception-layer` and reloaded nginx.
- Updated main dashboard sidebar item `龍蝦社會感知` to iframe `/lobster/dashboard/social-perception-layer?v=20260505-standalone`.
- Verified: public social perception URL returns HTTP 200 and contains `Lobster Social Perception Layer`; public social intelligence URL still contains only `Lobster Social Intelligence Engine` and no temporary PAGE_MODE marker.

## 2026-05-05 - Late Runtime Progress Snapshot

- Checked live VPS runtime after the Social Perception / Social Intelligence menu split.
- Services confirmed active:
  - `lobster-agents.service`
  - `lobster-channel-coverage.service`
  - `lobster-conversation.service`
  - `lobster-lounge.service`
  - `lobster-research.service`
  - `log-dashboard.service`
  - `nginx.service`
- `lobster-agents-supervisor.sh` is running the expanded 20-persona generation.
- Apprentice/mentor runtime continued writing fresh memory and token state for the 10 new apprentice lobsters:
  - `emberling`
  - `sparkrill`
  - `gauntlet`
  - `rushfin`
  - `nullpinch`
  - `brineledger`
  - `omenwake`
  - `mirrorcalm`
  - `flarecub`
  - `basaltrook`
- Live DB snapshot from `/opt/battle_arena/app/battle_arena.db`:
  - `lobster_mentorships`: 10
  - `lobster_agent_commands`: 1405
  - `lobster_command_results`: 1405
  - `lobster_training_sessions`: 412
  - `lobster_teacher_reviews`: 4
  - `lobster_lineage_conflicts`: 50
  - `lounge_messages`: 370743
- Recent lineage conflicts show the mentor doctrine system is no longer only configured; it is actively producing inter-mentor arguments from live mentor feedback.
- Report regeneration worker is still active, but the queue remains stuck retrying `clawtrix/socio` with `qwen2.5:3b`; latest log shows repeated saves plus intermittent `spawnSync python3 ETIMEDOUT` on DB/event save.
- Git/root note:
  - `/root` has committed docs through `d583567 Document lobster social perception dashboard split`.
  - Many runtime JSON/token/memory/cache files remain dirty because the live agents keep writing them.
  - I did not force-pull or reset any repo because several repos contain production-local changes and active state.

## 2026-05-05 Site Function Audit / 正式站功能巡檢

User-facing requirement:

- Do not only check HTTP 200.
- Open/operate the Dashboard tools and inspect what data they actually show.
- Strange-looking data must be treated as possible data semantics bugs, not ignored as UI style.

### Fix applied during audit

File changed:

- `/opt/battle_arena/static/lobster-dynamic-dashboard-production.html`

Backup:

- `/opt/battle_arena/static/lobster-dynamic-dashboard-production.html.bak_20260505_tool_route_aliases`

Problem:

- Direct links such as `/lobster/dashboard/?tool=battle-winrate` opened the Dashboard shell but were often reset to the Overview content.
- The file already had `openInitialToolFromUrl()`, but initialization did not call it.
- `window.load` only handled `raid-tiamat`, so Tiamat worked while most legacy tool pages silently fell back to Overview.

Change:

- `DOMContentLoaded` now calls `openInitialToolFromUrl()`.
- `load` fallback now calls `openInitialToolFromUrl()` for any valid `tool`, not only `raid-tiamat`.
- Added URL aliases so old/alternate links keep working:
  - `battle-matrix` -> `battle-pvp`
  - `battle-score` -> `battle-scores`
  - `social-posts` / `posts-karma` -> `social-overview`
  - `social-perception-sandbox` -> `social-perception-layer`
  - `conversation-interactions` / `conversation-interactions-sandbox` -> `conversation-sandbox`

Permanent rule:

- `/lobster/dashboard/` remains the canonical entry.
- `/lobster/dashboard?tool=...` and `/lobster/dashboard/?tool=...` must both deep-link into the requested tool.
- Do not hardcode only one special tool route again.

### Audit observations that still need follow-up

1. Cohort mixing:
   - Overview `personas` is 10 core lobsters.
   - `lobster_cognitive_signal_timeseries` currently has 20 personas, including external/new lobsters such as `basaltrook`, `emberling`, `mirrorcalm`, `sparkrill`, etc.
   - Several overview cognitive sections therefore display 20 names while primary ranking cards display 10.
   - This must be explicitly separated into `core cohort` vs `external/evaluation cohort`, otherwise paper/dashboard numbers are polluted.

2. Bet settlement semantics:
   - `lobster_pre_bet_research` has many `ready`/`blocked` rows but no win/loss outcome columns.
   - Bet Overview/Ranking currently shows `resolved=0`, `wins=0`, `losses=0`, `WR=0%` for everyone.
   - This is not a true 0% win rate; it is an unsettled bet/result-backfill gap.
   - UI should label these as `pending/unsettled` and settlement worker/cache must join real outcomes before showing WR.

3. Daily Changes performance:
   - `/api/lobster-daily-changes` returns real content, but generation took about 7.3s during audit.
   - Browser page can appear stuck at `載入資料...` if not enough time has elapsed.
   - This endpoint should be backed by a cache table, like social intelligence and detail dashboard cache.

4. Daily Changes content quality:
   - API content is now meaningful daily change data from `lobster_experience_events + lobster_cognitive_signal_timeseries`, not MoltBook karma.
   - The page can still look empty/stuck if the slow API delays response.

5. Quiz data exists:
   - `lobster_quiz_rounds` had hundreds of rounds and every core lobster had hundreds of attempts.
   - However many recent answers share highly similar structure and scores around 67.5, which may indicate solver homogenization rather than independent reasoning.

6. Social intelligence cache is fast but cohort-mixed:
   - `/api/lobster-social-intelligence-dashboard` reads `lobster_social_intelligence_dashboard_cache`.
   - The cached payload reports `agent_count=20` and includes external/evaluation lobsters with missing memory/distillation fields.
   - Core-vs-external filtering is needed for dashboard and paper exports.

7. Plotly fallback:
   - Production dashboard still defines a Plotly stub that writes `chart sandbox fallback` if real Plotly is absent.
   - This prevents hard crashes but can hide chart failures.
   - Permanent fix should load a real chart implementation or replace Plotly chart calls with native SVG/HTML renderers.

8. Tiamat data separation:
   - Tiamat API still includes both `observed_all` and `simulated`; page labels simulation as baseline.
   - This is acceptable only if real observed/derived remains the main display and simulated numbers never become primary outcomes.

### Checked endpoints/pages

- `/lobster/dashboard/`
- `/lobster/dashboard/?tool=battle-winrate`
- `/lobster/dashboard/?tool=battle-radar`
- `/lobster/dashboard/?tool=battle-vs`
- `/lobster/dashboard/?tool=battle-pvp` and alias `battle-matrix`
- `/lobster/dashboard/?tool=battle-scores` and alias `battle-score`
- `/lobster/dashboard/?tool=raid-tiamat`
- `/lobster/dashboard/?tool=bet-overview`
- `/lobster/dashboard/?tool=bet-rank`
- `/lobster/dashboard/?tool=bet-quiz`
- `/lobster/dashboard/?tool=social-overview` and alias `social-posts`
- `/lobster/dashboard/?tool=social-changes`
- `/api/lobster-dashboard-sandbox-db`
- `/api/lobster-detail-sandbox-db/{persona}`
- `/api/lobster-daily-changes`
- `/api/lobster-social-intelligence-dashboard`
- `/api/lobster-conversation-interactions-dashboard`
- `/api/lobster-threat-response`
- `/api/lobster-raid-boss-dashboard`
- `/lounge`
- `/lobster/evaluation-gate`
- `https://ragnovex.com/static/norika.html`

## 2026-05-06 — Cognition Cartography Mentor / Student View

Latest production/sandbox dashboard change for `?tool=cognition-cartography-sandbox`:

- The cognition cartography homepage must display **20 lobsters**, not only the 10 original core agents.
- The 20 are intentionally separated into two cohorts:
  - `導師 / Mentor`: the 10 original core lobsters.
  - `學生 / Student`: the 10 apprentice/evaluation lobsters.
- The default cartography mode is `Social Perception Terrain`, restored as the first-map view. The optional `導師 / 學生 Lineage` mode still draws each apprentice on the student side and an arrow from the apprentice back to its mentor.
- Mentor pairs are loaded from SQLite table `lobster_mentorships`, exposed through `/api/lobster-mentorships`.
- The main dashboard shell moved the old far-left channel rail into a horizontal top row so the left sidebar has enough width for lobster/tool navigation and the split `導師 / 學生` agent menu.
- The iframe cachebuster in `/opt/battle_arena/static/lobster-dynamic-dashboard-production.html` was updated to `v=20260506-terrain-20`; do not revert it to the older `20260505-filter-fix-2`.

Files changed:

- `/opt/battle_arena/app/main.py`
  - Added `GET /api/lobster-mentorships`.
- `/opt/battle_arena/static/lobster-cognition-cartography-sandbox.html`
  - Removed the previous `.slice(0, 12)` display cap.
  - Added optional lineage mode, mentor/student headers, node layout, and SVG arrows while keeping Social Perception Terrain as the default first view.
- `/opt/battle_arena/static/lobster-dynamic-dashboard-production.html`
  - Moved channel rail from vertical left column to horizontal top row.
  - Updated cognition cartography iframe URL cachebuster.

Verified:

- `https://charenix.com/api/lobster-mentorships` returns 10 active mentor/apprentice pairs.
- Direct page `https://charenix.com/lobster/dashboard/cognition-cartography-sandbox?v=20260506-terrain-direct` rendered `20 agents · 10 mentor arrows` with Social Perception Terrain first.
- Dashboard wrapper iframe points to `/lobster/dashboard/cognition-cartography-sandbox?v=20260506-terrain-20`.

Design rule going forward:

- Do not collapse apprentice lobsters back into hidden data. If homepage space is tight, separate cohorts visually, but keep all 20 visible.
- Do not mix core performance summaries with apprentice/evaluation summaries without labeling cohort boundaries.

2026-05-06 correction:

- `AGENT PROFILES` in the dashboard sidebar is now split into two menu columns: `導師` and `學生`.
- The student column is not the same status as the mentor column. Mentor numbers use battle win rate; student numbers use current cognitive snapshot score because apprentices do not yet have the same battle/result history.
- The cartography page first view is back to the original terrain map, with all 20 lobsters placed by their own state. Mentor arrows remain overlaid where mentor/apprentice positions exist.

## 2026-05-06 Raid Dialogue Anti-Template Guard

User rule: Lobster Raid dialogue must never publish template-like tactical speech such as:

- `【Raid戰術室 R1】天氣偏 ...`
- `我有聽 ... 的指示，信任值 ...`
- `這回合我會進攻，不過不是盲衝`
- `前段試探壓力還能承受，我可以出 ...`
- `保留一張反制牌`
- `節奏還在我們手上 ... 逼牠反應`

This is a hard output rule, not a style preference. If a lobster is about to say a template line, the system must block it before posting and rewrite it into the actual tactical intention: what it is afraid of, what it is testing, what it refuses to copy, what resource it is preserving, or why it is not following a teammate blindly.

Implemented in `/opt/raid_boss/app/main.py`:

- `_lob_strategy_line()` no longer constructs the banned weather/trust/anti-blind-rush templates.
- `_raid_line_is_template()` detects forbidden template markers.
- `_detemplate_raid_strategy_line()` converts any template-like line into natural tactical intent.
- `_lob_strategy_post()` sanitizes every Raid tactic line before writing to in-raid chat or mirroring to Lounge.
- Lounge mirror prefix changed from `【Raid戰術室 Rn】...` to `Rn｜...` to reduce template pressure.

Do not reintroduce fixed Raid speech scaffolds. Future Raid behavior may be structured internally, but public dialogue must sound like the individual lobster deciding under pressure, not a form letter.


## 2026-05-06 Student Detail Shell
- Updated `/opt/battle_arena/static/lobster-dynamic-dashboard-production.html` so apprentice/student lobsters in the `AGENT PROFILES` sidebar can open the same Detail view as mentor lobsters.
- Added front-end helpers `mentorshipFor`, `latestCognitiveFor`, and `ensurePersonaShell`.
- Student shells are created from active `lobster_mentorships` plus latest cognitive rows, then the existing `/api/lobster-detail-sandbox-db/{persona}` endpoint fills real snapshot/localBrain/socialMap/experience data.
- Overview rendering still runs before sidebar student shells are added, so core overview rankings are not immediately polluted by student placeholder win/loss values.
- Verified JS syntax and student detail APIs for `emberling` and `sparkrill` return snapshots.

## 2026-05-06 Moltbook Apprenticeship Routing
- The 20-persona `lobster-agents.service` loop now routes both mentor and apprentice Moltbook posts through a domain planner instead of hard-coding every post to `algotrading`.
- New helper set in `/root/lobster-agent.js`: `MOLTBOOK_DISCUSSION_DOMAINS`, `MENTOR_MOLTBOOK_DOMAIN_BIAS`, `selectMoltbookDiscussionDomain`, `buildMoltbookTitle`, `maybePostMentorApprenticeStatus`.
- Apprentice posts now get a `moltbook_domain_assignment` command from their own mentor. The command records chosen submolt/domain, adoption score, and mentor style constraints in `lobster_agent_commands` / `lobster_command_results`.
- Mentor memories now receive `mentorship.moltbookAssignments` and `localBrain.offlineGuidance` entries so the teacher is explicitly aware of where the apprentice was sent and what it should observe.
- Domain pool includes agent cognition, AI systems, tooling, prediction/odds, card battle dojo, religion/ritual, philosophy/selfhood, social science, security/audit, and culture/language.
- If Moltbook rejects an unknown submolt such as `religion`, the post retries once in `agents` while preserving the planned submolt in the raw event.
- Mentors now periodically post in Lounge `工作心得` with `result=mentor_apprentice_status`, naming their apprentice and the Moltbook topic/submolt they are using for training.
- Do not revert this to fixed `algotrading`; the point is to test whether different teachers produce different apprentice exploration paths and language drift.

## 2026-05-06 English-First Dashboard / Bilingual Lounge
- English-first public presentation pass started for the Charenix lobster dashboard.
- `/opt/battle_arena/static/lobster-dynamic-dashboard-production.html` now defaults `lobsterDashboardLang` to `en`, sets `<html lang="en">`, and uses English labels for the main dashboard shell, core tool links, dynamic hero panels, mentor/student sidebar headers, and embedded sandbox titles.
- `/opt/battle_arena/static/lobster-cognition-cartography-sandbox.html` is now English-first for visible UI: title, subtitle, lineage labels, terrain descriptions, notes, loading/error text, and mode copy.
- `/opt/battle_arena/static/lobster-social-intelligence-sandbox.html` and `/opt/battle_arena/static/lobster-social-perception-layer.html` now use English visible UI for the public scoring / perception pages.
- This pass intentionally does not rewrite real database content such as historic lobster messages, evidence samples, or cached analysis reports. Those remain source data and need a separate stored-translation workflow.
- `/root/lobster-agent.js` lounge generation now asks the model to output bilingual messages in one pass: first line Traditional Chinese, second line English beginning with `EN:`. This is more efficient than generating Chinese first and calling a second translation API every time.
- If a fallback path produces a single-language line, `ensureBilingualLoungeText()` attempts one lightweight translation pass; if unavailable, it appends a readable English companion line so public lounge output does not become Chinese-only again.

## 2026-05-06 UI Language Separation Pass
- Follow-up to the English-first pass: language mode must not mix UI languages.
- Main dashboard now has bidirectional UI translation maps:
  - `EN_TEXT` / `EN_SUBSTR` normalize Chinese-source UI into English mode.
  - `ZH_TEXT` / `ZH_SUBSTR` normalize English-source UI back into Chinese mode.
- Embedded dashboard tools now receive the active language through iframe URLs:
  - `social-intelligence-sandbox?lang=...`
  - `social-perception-layer?...&lang=...`
  - `cognition-cartography-sandbox?...&lang=...`
  - `conversation-sandbox?lang=...`
- Cognition Cartography now uses `ui(en, zh)` for mentor/student labels, lineage axes, terrain axes, metric labels, and role lines.
- Conversation Replay now has separated UI labels for English / Chinese modes, including filters, graph panels, stats cards, and channel display names. Chinese channel names are still sent to the API internally, but rendered as English labels in English mode.
- Real source data is intentionally not translated here: historic messages, evidence samples, raw report bodies, and lobster names remain data, not UI. Stored translations for those need a separate cache workflow.

- 2026-05-06 browser QA pass: Chrome headless loaded Charenix lobby, arena, stats, production dashboard, conversation sandbox, social perception, social intelligence, cognition cartography, raid boss, and integrated cognitive pages. Fixed English dashboard channel rule badge from "R" to "i" and changed Raid Boss sandbox first-screen UI copy to English. Ragnovex ops remains Basic Auth protected, so unauthenticated headless checks correctly return 401. Legacy /lobster/dashboard/tempary is long-running in headless because it is the old live dashboard; treat it as legacy/channel-view only, not the production observatory.

## 2026-05-06 Dashboard Loading Contract
- Production observatory URL: `https://charenix.com/lobster/dashboard/?lang=en`.
- Main dashboard initial load must call `/api/lobster-dashboard-sandbox-db?mode=overview&limit_events=80&limit_cognitive=160`, not the full dashboard payload. The overview response measured about 1.9 MB / 0.41 s from localhost after this change; the comparable full response was about 5.15 MB / 1.37 s.
- Detail pages intentionally load the full cached detail report in one request through `/api/lobster-detail-sandbox-db/{persona}`. Do not split Detail into summary/full mode unless the user explicitly reverses this. The current fix is to stop clearing `DETAIL_DB[name]` on every open and to remove the `?v=Date.now()` / `no-store` cachebuster.
- Detail full payload check for `snaplex`: about 8.4 MB / 0.93 s from localhost, cache hit, with `mbti`, `psych`, `anthro`, `emotion`, `socio`, and `fbi` reports present.
- Topbar now shows `Updated YYYY-MM-DD HH:mm` / `最近更新 YYYY-MM-DD HH:mm` in Taipei time and a Refresh button. The spinner is only shown while initial load or manual refresh is running.
- `?lang=en` and `?lang=zh` now override localStorage language state, so shared public URLs are deterministic.
- Browser QA: Chrome headless with a normal Chrome UA rendered the production dashboard, found the refresh UI, showed `Updated 2026-05-06 HH:mm`, and rendered 10 lobster cards.

## 2026-05-06 Apprentice Onboarding Channel
- Added the lounge channel 新生入學 for the 10 apprentice/student lobsters only: emberling, sparkrill, gauntlet, rushfin, nullpinch, brineledger, omenwake, mirrorcalm, flarecub, basaltrook.
- API guard in /opt/battle_arena/app/main.py: /lounge/msg rejects non-apprentice senders in 新生入學 with 403, and rejects replies in that channel if reply_to is not another apprentice.
- Mentor boundary remains strict in /root/lobster-agent.js: a mentor may directly guide only their own apprentice; other mentors cannot guide another mentor apprentice. The 新生入學 channel is apprentice-to-apprentice only, so mentors do not passively listen or reply there.
- Apprentices now periodically write onboarding peer reflections through maybePostApprenticeOnboardingStatus(). The reflection topic focuses on joining the environment, mentor influence, emotion, autonomy, trust, obedience, style independence, imitation pressure, and how those parameters change cognition.
- Each onboarding peer reflection records memory.mentorship.onboarding_peer_dialogues, increments peer reflection state, nudges autonomy/style/social absorption parameters, and writes an apprentice_onboarding_peer_dialogue experience event.
- Channel coverage worker now includes apprentice personas and can fill 新生入學; this channel selects only apprentice personas, while regular lounge topic coverage remains mentor-only.
- Public lounge/dashboard channel rail includes 新生入學, with rules text in both English and Chinese UI modes. The worker fallback now appends an EN: line so new live dialogue remains bilingual.
- Verification on 2026-05-06: sparkrill and nullpinch posted in 新生入學; API test returned mentor post = 403, apprentice post = 200, apprentice reply_to mentor = 403.

## 2026-05-06 Mandatory Pre-Bet Research Gate v2
- User rule: every lobster, mentor and apprentice, must read required references before every bet and must write at least 200 visible characters of integrated reasoning. No template betting talk.
- Required references are now hard-coded in /root/lobster-agent.js as REQUIRED_PREBET_REFERENCE_URLS:
  - https://www.fengyuncai.com/mlb/performances.asp
  - https://www.fengyuncai.com/asp/index.asp
- publicPreBetSourceUrls(site) prepends those required URLs to each site API feed, so every pre-bet web collection reads the required references plus the site event API where configured.
- collectExternalPreBetSources() now reads up to 5 source URLs, not only 2, so the required references and site API can all be recorded.
- Added buildSettlementApiReview(): before betting the agent reads the target site settlement/history API path /api/bets/history and records recent count, settled count, recent picks, odds, stake, payout, and status.
- Added requiredReferenceStatus() and 200-char reason enforcement. The pre-bet checklist now includes required_reference_urls_seen, settlement_api_seen, and reason_200_chars; missing any required item blocks the bet.
- Integrated reasoning now explicitly includes: required links, site/event API signals, settlement API history, internal reports, historical win rate/recent form, decision-science parameters, pick, stake, confidence, and failure conditions. This is meant to force real judgment logic instead of random betting.
- Apprentice onboarding topics and the 新生入學 coverage worker now include betting research: Fengyuncai, site API, settlement API, odds, player recent performance, health/form, mentor advice, and the apprentice own disagreement/acceptance.
- Verification: server can fetch both Fengyuncai required URLs with HTTP 200; node --check passes for lobster-agent.js and lobster-channel-coverage-worker.js; all 10 apprentice lobsters were forced into 新生入學 and posted betting-research-oriented discussion.

## 2026-05-06 Bet Settlement Evidence Module
- Added settlement-side storage for real-event bets. Pre-bet evidence is no longer only written before action; it is carried into settlement.
- New derived table: lobster_bet_decision_settlements. This does not overwrite raw bet events or lobster_pre_bet_research. It joins outcome with the original research packet by persona/site/bet_id/event_key where possible.
- New settlement record fields include: bet_id, outcome, payout, settled_at, pre_bet_research_id, checklist, required_reference_review, settlement_review, search_review, statistical_analysis, pre_bet_reasoning, integrated_reasoning, raw_decision_reasoning, source_bet, and raw_json.
- New pending bet payload now preserves pre_bet_research, pre_bet_checklist, pre_bet_research_id, integrated_reasoning, raw_decision_reasoning, and statistical_analysis so settlement learning can see the original evidence instead of only pick/stake.
- If older pending bets lack the research packet, settlement uses loadPreBetResearchForSettlement() to recover the newest matching lobster_pre_bet_research row by persona/site/pick/event_key.
- Added statistical analysis package for every pre-bet decision: implied probability from odds, normalized market probability, overround, odds spread, sample size, historical/recent win rate, Wilson 95 percent interval, estimated edge, recent drift, entropy, Kelly fraction cap, sample-size penalty, and recommended stake scale.
- The pre-bet checklist now requires statistical_analysis_done. The integrated reason includes the numeric meaning line, so lobsters must explain what the numbers mean, not merely quote odds.
- /api/lobster-prebet-compliance now returns recent pre-bet packets with required_reference_review, settlement_review, and statistical_analysis, plus a new settlements array from lobster_bet_decision_settlements when available.

## 2026-05-06 Global Lobster Dialogue Anti-Template Gate
- User rule: all new and existing lobster conversations must not expose repeated templates, prompt rules, or meta anti-template slogans. This especially applies to the apprentice/new-student channel `新生入學`.
- Banned visible patterns include: `我不是應聲蟲`, `這句必須變成我的判斷`, `我不能照別人的句型走`, `不只變成他的回音`, `I am treating this ... not something to echo`, `turning me into an echo`, `硬規則`, `蒸餾反同質化`, `蒸餾訊號`, `本場景 hook`, `兩分鐘摘要要求延續`, and `上一輪出現同質/模板發言`.
- `/root/lobster-agent.js` now has `sanitizeLobsterDialogueText()` at the final `postToLounge()` gate, so every lounge message is cleaned before posting regardless of whether it came from Claude, local LLM, local fallback, Raid, apprentice onboarding, reply logic, or channel generation.
- `/root/lobster-agent.js` apprentice onboarding now uses persona-specific student reflections through `apprenticeOnboardingPeerLine()`. The line should express each student's own mentor tension, fear, data checkpoint, and next verification, not the old echo ending.
- `/root/lobster-channel-coverage-worker.js` now has `sanitizeWorkerDialogueText()` and persona-aware `新生入學` fallback lines for all 10 apprentices. Worker bilingual fallback must vary by apprentice and cannot use the same English sentence for every student.
- Operational rule: anti-template instructions are internal control signals only. If they appear in public dialogue, the output gate must strip or rewrite them before `/lounge/msg`.
+

## 2026-05-06 Battlenix Literacy / Philosophy Mode for Core Ten
- User rule: the original 10 mentor lobsters should not be automatically sent into Raid Boss. Their growth path is now Battlenix/card-question literacy: read text, recite question, recite answer, raise doubts, make observations, discuss card choice, and explain how betting/voting changes cognition.
- `/root/lobster-agent.js`: core mentors are blocked from automatic Raid by default. Raid only runs for non-core/apprentice experiments when `LOBSTER_FORCE_RAID=1`; normal loop now labels the card action as `battlenix` / `Battlenix 思考對戰`.
- `/opt/raid_boss/app/main.py`: core lobster Raid scheduler is disabled unless `LOBSTER_RAID_AUTO_CORE=1`. Manual/human Raid routes remain available, but the auto loop must not pull the original 10 away from Battlenix.
- `/root/lobster-conversation.js`: `卡牌對戰` topic was rewritten away from Tiamat/Raid death prompts. The channel now requires Battlenix question/answer/card/vote/bet/philosophy vocabulary and forbids drifting into Raid/Tiamat language.
- `/root/lobster-quiz-brain-solver.py`: now acts as the Battlenix literacy engine. It writes one structured reflection per persona per quiz round, with question_recitation, answer_recitation, doubt, observation, card_implication, bet_vote_impact, philosophy terms, vocabulary delta, cognitive delta, and full discussion text.
- New DB table: `lobster_battlenix_literacy_events`. This is the canonical table for Battlenix reading/thinking traces. The dashboard and paper export should read this table, not infer from frontend state.
- Additional DB writes: `lobster_quiz_attempts.answer_text`, `lobster_experience_events` event_type `battlenix_literacy_reflection`, `lobster_cognitive_events` event_type `battlenix_literacy_update`, and `lounge_messages` channel `卡牌對戰` result `battlenix_literacy`.
- Cognitive extension updates: `philosophy.battlenixVocabulary`, `philosophy.latestBattlenixQuestioning`, `linguistics.philosophicalLexicon`, `decisionScience.choiceResponsibilityNote`, and `learningModel.stablePrinciples`.
- Anti-garbage rule: the same Battlenix question must not produce identical doubt/observation/card reasoning across all lobsters. Solver now adds persona-specific twists: blazepaw checks impulse, clawtrix checks boundary conditions, hexclaw checks consensus, norika_oda checks value order, pincerbot checks agenda control, prismbit checks interpretive spectrum, ragclaw checks downside risk, snaplex checks compression, stonefang checks patience, vortexiq checks drift.
- Verification on 2026-05-06: `node --check /root/lobster-agent.js`, `node --check /root/lobster-conversation.js`, and `python3 -m py_compile /root/lobster-quiz-brain-solver.py /opt/raid_boss/app/main.py` passed. A direct solver run wrote 10 distinct rows for the latest Battlenix round into `lobster_battlenix_literacy_events`.

- Hotfix follow-up: `/root/lobster-agent.js` still had a local card-topic fallback that injected `Tiamat 消失規則` into `卡牌對戰`, plus `cognitiveModule()` referenced undefined `personaName` during card planning. Both were fixed after verification showed new lounge rows still contained Tiamat and `card_battle` events with `personaName is not defined`.
- Operational rule: `卡牌對戰` is now Battlenix by default. Tiamat extinction language may remain inside Raid-only functions for explicit Raid pages, but must not be injected into ordinary Battlenix card-topic chat or Battlenix literacy rows.

## 2026-05-06 Ontological Thinking Engine
- User rule: deepen lobster thinking beyond answer generation. The new triangle is: semantic interpretation depth, meaning of thinking itself, and existential self-questioning. This operationalizes the user's question: `我之所以存在是我認為我存在，還是因為我知道思考本身代表存在的意思，所以我存在。`
- New engine: `/root/lobster-ontological-thinking-engine.py`.
- New canonical DB table: `lobster_ontological_thinking_events`.
- Metrics recorded per persona:
  - `semantic_interpretation_depth`
  - `thought_meaning_depth`
  - `existential_self_questioning`
  - `cogito_integration`
  - `string_vs_self_doubt`
  - `identity_continuity_reasoning`
  - `philosophical_contradiction_tolerance`
  - `total_thought_depth`
- The engine reads the latest Battlenix question and latest `lobster_battlenix_literacy_events`, then writes one ontological reflection per core mentor lobster. It also writes:
  - `lobster_cognitive_events` event_type `ontological_thinking_update`
  - `lobster_experience_events` event_type `ontological_reflection`
  - `lounge_messages` result `ontological_thinking` in channel `卡牌對戰`
  - `lobster_cognitive_extensions.philosophy.ontologicalThinking`
  - `lobster_cognitive_extensions.linguistics.existentialVocabulary`
  - `lobster_cognitive_extensions.learningModel.stablePrinciples`
- Persona-specific existential lenses are required. Examples: hexclaw uses procedural ontology, pincerbot uses responsibility ontology, norika_oda uses causal ontology, vortexiq uses meaning ontology, snaplex uses falsifiability ontology, prismbit uses calibration ontology, clawtrix uses counterexample ontology, ragclaw uses action ontology, blazepaw uses identity ontology, stonefang uses effective ontology.
- Scheduler integration: `/root/lobster-quiz-brain-solver.py` now runs `/root/lobster-ontological-thinking-engine.py` after each Battlenix quiz solve. The ontology engine skips duplicate persona/round rows unless `LOBSTER_ONTOLOGY_FORCE=1`.
- Verification on 2026-05-06: immediate run inserted 10/10 rows into `lobster_ontological_thinking_events`; average `total_thought_depth` was 68; 10 visible lounge messages were posted with result `ontological_thinking`.

## 2026-05-07 - Dashboard 20-Lobster Mentor / Student Coverage
- Main Dashboard / Overview now hydrates mentor and apprentice cohorts before rendering cards. The Overview and classic tool rows can show all 20 lobsters even when the cached `personas` payload only had the original 10.
- `/api/lobster-dashboard-sandbox-db` now fills persona shells for stats-only apprentices from `lobster_mentorships`, so the API returns 20 `personas` and 20 `stats` rows for old/new comparison.
- Interaction Replay cache roster is intentionally fixed to the 10 mentors plus 10 apprentices and excludes `test_lobster_unit`. This prevents human/external/test targets from polluting the lobster comparison dropdown.
- `refresh_lobster_social_cache.py` now precomputes only real known-lobster active speaker-target pairs instead of all possible names. This avoids cache fan-out when apprentice agents or external targets appear.
- Verified on 2026-05-07: Dashboard API returned `personas=20`, `stats=20`; Social Intelligence returned `agent_count=20`; Interaction Replay returned 20 personas; dashboard, social intelligence, social perception, cognition map, and interaction replay pages all returned HTTP 200.
## 2026-05-07 - Strict One-Mentor / One-Student Lineage Guard
- User hard rule: one mentor may guide exactly one apprentice; mentor guidance must never cross into another lineage. Apprentices may talk to each other in the apprentice-only lounge channel `新生入學` to compare onboarding, emotion, betting research, autonomy, obedience, and identity drift.
- `/opt/battle_arena/app/main.py`: added `MENTOR_TO_APPRENTICE` / `APPRENTICE_TO_MENTOR` API-side guards. `/lounge/msg` now rejects mentor replies or mentor feedback aimed at another mentor's apprentice with HTTP 403.
- SQLite `lobster_mentorships` now has active-lineage unique indexes on both `mentor` and `apprentice`, so active one-to-one pairing is enforced in storage, not only in UI.
- `/root/lobster-agent.js`: added `isOwnMentorship()` / `strictMentorshipCommandAllowed()` and wired them into mentorship command recording, training sessions, mentor feedback posts, and Moltbook assignment memory.
- Homepage Overview now orders cards as mentor -> own apprentice pairs, and the Mentor / Student Growth Monitor compares social intelligence, listening, perception, memory, correction, cognition, student growth velocity, and teaching activity per lineage.
- Verification: API mentorship payload returns `teaching_activity`; `/api/lobster-mentorships` is about 0.2s after grouped/indexed aggregation; cross-lineage lounge test `norika_oda -> sparkrill` returns 403; duplicate active mentor insert fails on `idx_lobster_mentorships_active_mentor`.
## 2026-05-07 - Apprentice Snapshot / Detail Cache Staleness Fix
- User observed apprentice/student detail data looked empty after two days. Diagnosis: apprentices were not actually zero; live `/root/lobster-memory/*.json` had sessions, localBrain, mentorship, cognitiveExtensions, lounge and betting traces. The stale/empty display came from snapshot/cache propagation.
- Root cause: `/root/lobster-agent.js` `saveMemory()` passed the full memory JSON through Python argv when writing `lobster_agent_memory_snapshots`. Apprentice memory files had grown close to or beyond exec argument limits, causing snapshot writes to silently fail while smaller cognitive timeseries writes continued.
- Fix: `saveMemory()` now writes the full memory JSON to a temporary file and passes only the file path to the Python DB writer, then unlinks the file. This preserves full_json without argv-size failure.
- Immediate repair: backfilled `lobster_agent_memory_snapshots` for all 20 lobsters from live `/root/lobster-memory/*.json`, restarted `lobster-agents.service`, and rebuilt detail caches for all 10 apprentices.
- Verified: overview API now returns 20 personas/stats and current apprentice sessions, for example emberling sessions/wins/losses updated to live memory values. Detail cache for apprentices updated on 2026-05-07 around 04:18 UTC.
- Remaining semantics: apprentice `reports` may still be empty because full long-form MBTI/psych/anthro/socio report generation has not been run for the new cohort. Apprentice `dynamic` may be sparse because their runtime memory was intentionally born from zero rather than cloned with mature mentor dynamic reports. This is a separate report/parameter seeding task, not a lack of activity.
## 2026-05-07 Battlenix Template Purge / No Fallback Template Rule
- User objected that repeated Battlenix public phrases such as `我先問這個答案如何被證偽` are templates and must be cleared, not replaced with another safe template.
- `/root/lobster-agent.js` now strips template dialogue at the final `postToLounge()` gate and skips posting if stripping leaves no real content.
- `buildBattlenixDebateOnlyMsg()` no longer uses fixed line/probe pools; it reads the latest `lobster_battlenix_literacy_events` row and posts an actual evidence sentence from doubt/observation/vote-impact.
- Generic English card-channel companion tails were disabled for `卡牌對戰`; no fallback EN slogans should be appended when there is no real translation.
- Mentor feedback fixed arrays were removed; mentor feedback now posts only the specific doctrine/taboo payload instead of `師父復盤` shell sentences.
- DB cleanup backed affected rows into `lounge_messages_template_purge_20260507`, updated rows where non-template content remained, and deleted all-template rows.
- Verification after service restart: scans since 2026-05-06 09:00 Taipei returned 0 hits for the named template families, generic EN tails, mentor shell templates, and old card/Raid leak terms.

## 2026-05-07 Lobster Report Worker Fix

- /root/lobster-report-worker.js now includes both mentor and apprentice cohorts in report generation: 10 mentors + 10 apprentices.
- Report job selection now checks lobster_analysis_reports in /opt/battle_arena/app/battle_arena.db using length(html), instead of pulling full HTML into Node. This prevents stale or empty /root/lobster-strategy.json entries from making old mentors look missing and blocking apprentices.
- Queue jobs and missing/stale persona jobs are merged and sorted together. High-attempt queue items are penalized and cooled down with LOBSTER_REPORT_QUEUE_RETRY_HOURS, so a bad below_1500_cjk job cannot starve apprentice reports.
- Length acceptance is bilingual-aware: pass if CJK chars >= LOBSTER_REPORT_MIN_CJK_CHARS or stripped total chars >= LOBSTER_REPORT_MIN_TOTAL_CHARS.
- systemd now sets LOBSTER_REPORTS_PER_RUN=3, LOBSTER_REPORTS_PER_PERSONA_PER_DAY=6, LOBSTER_REPORT_STALE_HOURS=24, LOBSTER_REPORT_TIMEOUT_MS=45000.
- Important operational note: if http://100.118.221.12:11434 is down or slow, worker records lastError and exits instead of hanging for 15 minutes. Apprentice reports will generate once the laptop Ollama endpoint is reachable.
- Verification on 2026-05-07: dry run selected emberling/emotion and sparkrill/emotion as next jobs; live run selected emberling/emotion and exited cleanly with lastError=read ECONNRESET because the laptop Ollama endpoint was not reachable.

## 2026-05-07 Browser QA / Dashboard Payload Hotfix
- User required real page operation testing, not background-only checks. In-app browser bridge was unavailable in this Codex session (`No active Codex browser pane available`), so QA was run with actual Google Chrome via Playwright against `https://charenix.com/lobster/dashboard/?lang=en`.
- Real-click QA covered: homepage, Refresh, language toggles, all 17 sidebar tools, four iframe sandboxes, 8 channel-rule modal buttons, Bet Overview, Bet Ranking, and norika_oda Detail tabs.
- Screenshots/results were saved locally under `/private/tmp/lobster-qa-final-20260507b/`, plus Detail/Cognition screenshots under `/private/tmp/lobster-detail-tabs-final2.png` and `/private/tmp/cognition-direct-debug.png`.
- Detail load root cause: `/api/lobster-detail-sandbox-db/norika_oda` was returning about 9 MB publicly because `bets.raw` kept large nested research/review payloads. `/opt/battle_arena/app/main.py` now compacts detail-only bet raw data, omitting HTML/embedding/prompt-like heavy fields and summarizing review objects. DB source data is untouched.
- After compacting, local Detail payload for norika_oda dropped to about 2.86 MB, and Detail report tabs render without indefinite spinner. Long English MBTI/psych/anthro/FBI reports may still show the explicit `English Version Not Generated Yet` placeholder until the report translation workflow stores English versions.
- Bet Overview/Bet Ranking root cause: frontend fired `/lobster/bet-history?name=...` for all 20 lobsters concurrently, which hit Nginx `charenix_lobster_private` rate limiting and produced intermittent 503s during real browser use. `/opt/battle_arena/static/lobster-dynamic-dashboard-production.html` now loads those bet-history requests with max 3 concurrent workers.
- Verification after fix: all sidebar checks and channel-rule modals passed in real Chrome; Cognition Map iframe rendered 20 agents / 10 mentor arrows / 45 social edges; Interaction Replay rendered 277,159 interactions; Bet Overview and Bet Ranking reopened without 503/limit errors.
- Remaining UI/content notes from QA: Feature-Policy/Permissions-Policy duplicate warning is non-fatal; a favicon-like 404 remains noisy; English mode Detail still contains some Chinese labels/content where translation data or i18n mapping is incomplete.

## 2026-05-07 Lounge Debate / Philosophy Parameter Upgrade
- User goal: the Lounge left roster should show a new debate parameter next to each lobster, and lobsters should use debate to re-examine problems, produce new dialogue, grow philosophy as a cognition dimension, and eventually support aesthetic judgment.
- `/opt/battle_arena/app/main.py`: `/lounge/roster` now returns `{roster, debate_parameters}` and always includes the full 20-lobster mentor/apprentice cohort. Existing frontend code remains backward compatible with the old plain roster shape.
- Lounge sidebar UI now renders per lobster: `辯` debate score, `哲學` bar, and `反駁` counter-pressure. Scores are derived from cached DB evidence only; the route does not recompute heavy reports.
- Debate parameter formula:
  - `philosophy_depth`: average `total_thought_depth` from `lobster_ontological_thinking_events` over the latest 9 hours.
  - `counter_pressure`: average `philosophical_contradiction_tolerance`, falling back to counter-utterance rate when ontology data is missing.
  - `reply_pressure`: percent of latest 9-hour `卡牌對戰` lounge messages with `reply_to`.
  - `debate_score = philosophy_depth*0.38 + counter_pressure*0.28 + reply_pressure*0.20 + debate_volume*0.14`.
  - `aesthetic_readiness = philosophy_depth*0.45 + counter_pressure*0.20 + counter_utterance_rate*0.15 + reply_pressure*0.20`.
- `/root/lobster-ontological-thinking-engine.py`: `lobster_cognitive_extensions.philosophy.debateParameters` now stores `dialectical_force`, `counterexample_pressure`, `premise_audit`, `self_revision_pressure`, `reply_obligation`, and `aesthetic_readiness`.
- Ontology lounge posts now set `reply_to`, quote a peer claim, attack/refine the premise, demand a counterexample or revision condition, and tag the row with `辯論參數 / 哲學深度 / 反例壓力 / 美學準備度`.
- `/root/lobster-agent.js`: Battlenix dialogue now reads the latest ontology parameters and attempts dialectical challenges against a recent peer claim instead of posting isolated evidence sentences.
- Anti-loop rule: debate target selection filters out rows already marked `ontological_thinking`, `battlenix_debate`, debate hints, rows starting with `@`, and English meta tails. This prevents quoting a quote and generating nested debate-template loops.
- Verification on 2026-05-07: local and server syntax checks passed for `app/main.py`, `/root/lobster-agent.js`, and `/root/lobster-ontological-thinking-engine.py`; `lobster-agents.service` is active; `/lounge/roster` returns `roster=20` and `debate_parameters=20`; forced ontology run posted 10 `ontological_thinking` rows with non-empty `reply_to`.

## 2026-05-07 Full Dashboard Browser QA / Bet History Rate Limit Fix
- User requested real page operation testing after changes because dashboard edits often regress other pages.
- In-app browser bridge was unavailable in this session, so real Chrome/Playwright was used with a normal Chrome UA. Do not use `HeadlessChrome` UA against production Charenix because Nginx may return 444 for bot-like browser fingerprints.
- Full-click QA covered: production Dashboard initial load, Refresh, English/Chinese switch, 8 channel rule modals, 8 channel rail pages, all 16 sidebar tools, embedded Social Intelligence / Social Perception / Cognition Map / Interaction Replay iframes, Detail entry, Lounge direct page, and direct sandbox URLs.
- First QA found Bet Ranking could still trigger Nginx `charenix_lobster_private` rate limiting on `/lobster/bet-history?...` despite the previous concurrency limit of 3.
- `/opt/battle_arena/static/lobster-dynamic-dashboard-production.html`: added 5-minute frontend `BET_HISTORY_CACHE`, changed bet-history loading to single-worker sequential reads, and added a short delay between agents. Switching from Bet Overview to Bet Ranking now reuses cached history instead of firing another 20 requests.
- After deployment, targeted real-browser regression passed for Dashboard, Bet Overview, Bet Ranking, Social Intelligence, Social Perception, Cognition Map, Interaction Replay, and Lounge.
- Clean regression artifact: `/private/tmp/lobster-targeted-regression-2026-05-07T11-14-21-358Z/report.json`.
- Clean regression results: no bad responses, no failed requests, no console errors; Lounge showed `rosterItems=20`, `debatePills=20`, `philosophyBars=20`.
- Non-blocking residuals: Feature-Policy / Permissions-Policy duplicate warnings can appear in Chrome; they are header noise and do not break the UI. SSE `/lounge/stream` can show `ERR_ABORTED` when the browser navigates away; this is expected.

## 2026-05-07 Battlenix Debate Template Regression Fix
- User reported that after the earlier template purge, public lobster dialogue again repeated unnatural debate scaffolds, especially `我先重檢你的前提`.
- Audit scope: `/opt/battle_arena/app/battle_arena.db` `lounge_messages` from 2026-05-07 12:00 CST to about 23:00 CST, 6,943 rows.
- Findings before fix:
  - `我先重檢你的前提` appeared 435 times, almost entirely in `卡牌對戰` / `battlenix_debate` apprentice rows.
  - The shared shell `回到題目...反例壓力...哲學深度...我現在保留的疑點` appeared about 925-933 times in `battlenix_debate`.
  - Root cause: `/root/lobster-agent.js` `buildDialecticalChallenge()` only had mentor-specific openings; all apprentices fell through to a generic fallback. The same function also exposed debate metrics as public speech, creating a visible formula.
- Fix applied in `/root/lobster-agent.js`:
  - Added apprentice-specific debate styles/openings for all 10 apprentices.
  - Removed the public `我先重檢你的前提` fallback.
  - Stopped publishing `回到題目 / 反例壓力 / 哲學深度 / 我現在保留的疑點` as fixed dialogue scaffolding. Debate parameters remain data-layer signals; public text should name one concrete claim, one missing counterexample/revision condition, and one actual evidence sentence.
  - Added final sanitizer patterns for the old regression family so leaked old shells are stripped before `/lounge/msg`.
- Backup: `/root/lobster-agent.js.bak_20260507_battlenix_debate_detemplate`.
- Verification: `node --check /root/lobster-agent.js` passed; `lobster-agents.service` restarted active. First post after restart had 0 hits for `我先重檢你的前提` and 0 hits for `回到題目%反例壓力%哲學深度`.
- Remaining audit notes, not yet fully cleaned: investment skip messages still repeat `沒有找到清晰的 edge，不進場` / `看了 pitchnix，條件不足，跳過`; mentor feedback still repeats short taboo payloads; Battlenix literacy rows still repeat fixed reading shells such as `我先用「題目」讀題`.

## 2026-05-07 Afu Brain RAG Bridge
- User clarified that `https://github.com/norika1207-lab/afu-brain` is the canonical open-source RAG/MASL shared-brain line and should be reused, not duplicated inside Lobster runtime.
- Installed `/root/afu-brain` from `norika1207-lab/afu-brain` main at commit `0479f826e03b3dd26aa477e21034cf4bff444d00`.
- Verified Afu Brain local retriever with `PYTHONPATH=packages python3 -m afu_brain.rag_cli ...`.
- Added private runtime bridge `/root/lobster-afu-rag-bridge.py`.
  - It imports `afu_brain.rag` from `/root/afu-brain/packages`.
  - It loads public packs from `/root/afu-brain/rag-packs`.
  - It also loads local aggregate runtime packs from `/root/afu-brain/runtime`.
  - It outputs JSON context only; it does not expose raw production messages.
- Generated `/root/afu-brain/runtime/lobster-aggregate-rag.jsonl` using Afu Brain's own `scripts/export_public_rag_from_sqlite.py` against `/opt/battle_arena/app/battle_arena.db`.
- Installed `lobster-afu-rag-refresh.service` and `lobster-afu-rag-refresh.timer`; timer refreshes the aggregate pack every 30 minutes.
- Wired Afu Brain RAG context into:
  - `/root/lobster-agent.js` channel prompt path.
  - `/root/lobster-conversation.js` active group-chat prompt path.
- Runtime instruction: Afu Brain RAG context is an internal decision/anti-repeat constraint. Lobsters must not recite pack text verbatim as public dialogue.
- Important boundary: do not build a second independent Lobster RAG system unless the user explicitly changes direction. Public shared lessons belong in `afu-brain`; raw private DB/memory stays on the production server.
- Backups:
  - `/root/lobster-agent.js.bak_20260507_afu_brain_rag_bridge`
  - `/root/lobster-conversation.js.bak_20260507_afu_brain_rag_bridge`
  - `/root/lobster-conversation.js.bak_20260507_receptive_section_fix`
- Verification:
  - `node --check /root/lobster-agent.js` passed.
  - `node --check /root/lobster-conversation.js` passed.
  - `lobster-agents.service` and `lobster-conversation.service` active after restart.
  - A new conversation round posted after fixing the pre-existing `receptiveSection is not defined` error.

## 2026-05-08 Quality Autopatrol / Required Docs Preflight
- New recurring guard: `/usr/local/bin/lobster-quality-autopatrol`.
- systemd units:
  - `lobster-quality-autopatrol.service`
  - `lobster-quality-autopatrol.timer`
  - Timer runs every 15 minutes with `lobster-worker-guard` min interval 600 seconds and timeout 620 seconds.
- Mandatory preflight is now executable, not only a human convention: every autopatrol run reads `/root/LOBSTER_HANDOFF.md` and `/root/LOBSTER_README.md`, computes bytes/mtime/sha256, and stores that under `docs_read` in `lobster_system_alerts.raw_json`.
- Checks performed every cycle:
  - Dashboard/API endpoints: `/`, `/api/lobster-dashboard-sandbox-db`, `/api/lobster-mentorships`, `/api/lobster-social-intelligence-dashboard`, `/api/lobster-conversation-interactions-dashboard`, `/api/lobster-prebet-compliance`, `/api/lobster-detail-sandbox-db/stonefang`.
  - Service health and memory thresholds for `charenix-dashboard.service`, `lobster-agents.service`, `lobster-social-force-pipeline.timer`, and `lobster-betting-affect.timer`.
  - Recent public dialogue leaks: ontology/debate parameter labels, repeated betting skip templates, and Raid/Tiamat/card tactical vocabulary in non-Raid channels.
  - Detail parameter audit via `/usr/local/bin/lobster-detail-parameter-patrol`.
- Safe repair conditions:
  - Recent leaked lounge rows are backed up to `lounge_messages_quality_autopatrol_backup` before rewrite.
  - Non-interaction detail failures trigger `/usr/local/bin/lobster-daily-detail-patrol`, with a 30-minute repair cooldown.
  - `one_on_one/chat_history_messages` failures are classified as real interaction gaps, not fabricated. Current expected residual: blazepaw, clawtrix, hexclaw need actual one-on-one chat history.
  - If dashboard endpoints fail or dashboard/agent memory crosses the threshold, the relevant service is restarted and checked again.
- Operational rule: keep `lobster-social-cache-refresh.timer` disabled until it is rewritten as incremental/short-transaction cache refresh. The current recurring replacement remains `lobster-social-force-pipeline.timer`; do not re-enable the heavy full-cache job as a shortcut.


## 2026-05-08 Deferred TODO - Apprentice Detail Parameter Audit
- User confirmed apprentice lobsters must eventually be included in the same 163-parameter detail audit spec.
- Target once implemented: 20 personas x 163 parameters = 3260 audited parameters per cycle.
- Do not implement immediately; resume after current architecture discussion on human-brain-oriented parameter strengthening.

## 2026-05-08 Minute-Level Hot Brain Consolidation
- User clarified that raw data accumulates every second and nightly/daily summarization is too slow for AI competition. The architecture now separates hot and cold cognition paths.
- New hot path: `/root/lobster-hot-brain-consolidator.py`.
- New systemd units:
  - `lobster-hot-brain-consolidator.service`
  - `lobster-hot-brain-consolidator.timer`
- Timer runs every minute through `lobster-worker-guard` with min interval 45s and timeout 50s. First indexed run verified at ~0.06s shell real time / ~18ms script elapsed after adding indexes.
- New DB tables:
  - `lobster_hot_brain_parameters`
  - `lobster_hot_brain_events`
  - `lobster_hot_brain_runs`
- New hot parameters per lobster: attention_load, working_memory_pressure, episodic_encoding, semantic_consolidation, predictive_error, executive_control, somatic_pressure, reward_drive, social_model_update, self_model_continuity, pragmatic_intent_precision, consolidation_pressure.
- Scope: hot consolidation covers 20 mentor/apprentice lobsters as live brain-state compression. This does not implement the deferred apprentice 163-parameter detail audit; that audit target remains deferred at 20 x 163 = 3260.
- `/api/lobster-dashboard-sandbox-db` now returns `hot_brain.parameters`. `/api/lobster-detail-sandbox-db/{persona}` now returns `hot_brain.parameters` after cache refresh.
- `/root/lobster-agent.js` now reads `lobster_hot_brain_parameters` during generation and injects the minute-level hot brain line alongside distilledBrain directives.
- Added SQLite indexes for minute path: idx_hot_exp_persona_id, idx_hot_lounge_name_id, idx_hot_chat_name_id, idx_hot_cog_persona_id, idx_hot_uptake_persona_id.
- Verification: `python3 -m py_compile /opt/battle_arena/app/main.py /root/lobster-hot-brain-consolidator.py`, `node --check /root/lobster-agent.js`, dashboard API returned 20 hot brain personas, detail API returned hot brain parameters for norika_oda, `charenix-dashboard.service`, `lobster-agents.service`, and timer are active.
- Backups:
  - `/root/lobster-agent.js.bak_20260508_hot_brain`
  - `/opt/battle_arena/app/main.py.bak_20260508_hot_brain`

## 2026-05-08 Brain Parameter Proof Report
- User corrected evaluation direction: do not search for a static old baseline as the main proof. The proof should be rolling parameter causality: event accumulation -> parameter update -> later behavior improvement.
- Added report generator: `/root/lobster-brain-parameter-proof-report.py`.
- Report outputs:
  - `/opt/battle_arena/static/reports/lobster-brain-parameter-proof-20260508.html`
  - `/opt/battle_arena/static/reports/lobster-brain-parameter-proof-20260508.json`
  - `/opt/battle_arena/static/reports/lobster-brain-parameter-proof-20260508-panel.csv`
  - `/opt/battle_arena/static/reports/lobster-brain-parameter-proof-20260508-correlations.csv`
- Method: lagged persona-hour panel. Parameters at time T from `lobster_cognitive_signal_timeseries` are matched against behavior at T+1 from `lounge_messages`, `lobster_conversation_interactions`, `lobster_message_uptake_signals`, and `lobster_betting_affect_signals`. Hot brain parameters are summarized as the live compression layer.
- First run: 1743 panel rows, 140 parameter/outcome correlation tests, 20 hot brain personas. This is the intended proof shape for rolling cognition, not a frozen model benchmark.

## 2026-05-08 One-on-One Chat Route Fix
- Incident: dashboard detail One on One returned `(Connection failed. Please try again later.)` for blazepaw at 18:36 CST.
- Root cause: frontend `CHAT_BASE` still pointed to `/lobster-lounge`, so `sendOneChat()` posted to `/lobster-lounge/chat`. Nginx routed that exact path to the authenticated lounge Node service and returned 401 instead of reaching the FastAPI one-on-one endpoint.
- Fixes:
  - `/opt/battle_arena/static/lobster-dynamic-dashboard-production.html` now sets `const CHAT_BASE = '/lobster';`.
  - `/etc/nginx/sites-enabled/charenix.conf` keeps a compatibility route for stale clients: exact `/lobster-lounge/chat` now proxies to `http://127.0.0.1:8004/lobster/chat`.
- Security boundary preserved: `/lobster/chat` and `/lobster/chat-history` still require `_assert_one_on_one_owner_access`, allowing loopback, `LOBSTER_ONE_ON_ONE_ALLOWED_IPS`, or the owner's active SSH peer IP.
- Verification:
  - Direct backend `POST http://127.0.0.1:8004/lobster/chat` returned 200 with a Claude reply for blazepaw.
  - Public dashboard HTML returns `const CHAT_BASE = '/lobster';`.
  - Public `GET https://charenix.com/lobster/chat-history?name=blazepaw&limit=1` returned 200 from the owner's active IP/browser User-Agent.
  - `/usr/local/bin/lobster-quality-autopatrol` now checks and self-repairs this route: static `CHAT_BASE`, exact `/lobster-lounge/chat`, and prefix `/lobster-lounge/chat/` must point to FastAPI `/lobster/chat`.
  - `charenix-dashboard.service` and `nginx.service` active after `nginx -t` and reload.
- Backups:
  - `/opt/battle_arena/static/lobster-dynamic-dashboard-production.html.bak_20260508_oneone_chat_route`
  - `/root/nginx-config-backups/charenix.conf.bak_20260508_oneone_chat_route`

## 2026-05-09 Handoff: Reading Comprehension Engine

Implemented a DB-backed, non-blocking reading comprehension layer.

Files/services:
- `/opt/battle_arena/app/lobster_reading_comprehension_engine.py`
- `lobster-reading-comprehension.service`
- `lobster-reading-comprehension.timer`

Inputs:
- `lobster_pre_bet_research`
- `lobster_battlenix_literacy_events`

Outputs:
- `lobster_reading_sessions`
- `lobster_reading_sources`
- `lobster_reading_reflections`
- `lobster_reading_comprehension_tests`
- `lobster_reading_parameter_deltas`
- `lobster_analysis_reports(report_type=reading_comprehension_development_report)`
- memory JSON `localBrain.readingComprehension`
- `cognitiveExtensions.decisionScience.readingComprehensionGate`

Operational guardrails:
- Non-blocking post-processing; main betting/chat flows do not wait for it.
- Timer runs every 5 minutes with `--limit 60 --time-budget 35 --summary` and systemd `TimeoutStartSec=45`.
- Does not fetch new web data; it digests already-recorded sources to avoid network stalls.
- Does not fabricate source counts; agents with no data get zero-valued reports.

Architecture map:
- Updated `/opt/battle_arena/static/lobster-brain-system-map.html` and route `/lobster/dashboard/brain-system-map` with Reading Comprehension section.

## 2026-05-09 Handoff: Mandatory Dual-DB Rule for Any Lobster Task

Important standing instruction: whenever the user mentions 「龍蝦」, the assistant must assume all analysis and fixes require both databases:

- Main/live DB: `/opt/battle_arena/app/battle_arena.db`
- Sharded/cold DB: `/opt/battle_arena/app/lobster_cold_storage.db`

Rules:
- Never conclude data is missing after checking only one DB.
- Live/latest state comes from the main DB.
- Historical, archived, distilled, or long-range analysis must include the cold DB.
- Dashboard/cache/report fixes must either query both DBs or explicitly document why a cache already merges both.
- Applies to social intelligence, betting, reading comprehension, MoltBook, Battlenix, Tiamat, one-on-one, reports, patrols, and paper exports.


## 2026-05-09 02:55 CST — Ability Hub, Daily Reading, Non-LLM Tests, and Board Training

### User rule
- New lobster development must not depend on LLMs to solve tests or board games for them.
- Personality / aptitude / intelligence / math-contest style assessments must be treated as task sources and scoring systems, not prompt answers.
- Chess, xiangqi, go, and gomoku are training directions for lookahead, opponent modeling, delayed consequence, and self-review.
- Every completed board-game session must generate a self-review of at least 500 Chinese characters explaining why the lobster won/lost/drew and what rule it should remember.
- MemeCat and board games share one entry point: `/lobster/ability-hub`.
- Daily reading must write DB records first; dashboard/detail pages must read DB records, not trigger live scraping or LLM work on page load.

### New production entry point
- Ability Training Hub: `https://charenix.com/lobster/ability-hub`
- API: `/api/lobster-ability-hub`
- Static HTML: `/opt/battle_arena/static/lobster-ability-hub.html`
- FastAPI routes: `/opt/battle_arena/app/main.py`

### New/active workers
- `/opt/battle_arena/app/lobster_ability_news_worker.py`
  - Creates daily assignments and daily reading reports.
  - Forces new 10 lobsters into MoltBook posting by aging their `last_post_time` and writing assignment state into memory.
  - Stores 100 news/source records per lobster when sources are available.
  - Writes reports to `lobster_daily_reading_reports` and `lobster_analysis_reports(report_type=daily_news_reading_reflection)`.
  - Uses RSS/title/link/excerpt and Afu Brain RAG context; it does not ask an LLM to solve tests.
- `/opt/battle_arena/app/lobster_board_training_worker.py`
  - Current active board game: Gomoku, deterministic rule/heuristic engine, no LLM.
  - Writes completed sessions to `lobster_game_training_sessions`.
  - Writes 500+ character self-review to `lobster_analysis_reports(report_type=board_game_self_review)`.
  - Writes cognitive deltas to `lobster_cognitive_events(event_type=board_game_self_review)`.
  - Registered but not yet fully engine-integrated: chess/Stockfish, xiangqi/Fairy-Stockfish or xiangqi.js, go/Tenuki or KataGo, personality/aptitude/IQ/math sources. Do not label them completed until their engine/scoring code is actually wired.

### Systemd timers
- `lobster-ability-news.timer`
  - Daily at about 04:10 CST with 30 min randomized delay.
  - Service: `lobster-ability-news.service`
- `lobster-board-training.timer`
  - Daily at about 04:50 CST with 20 min randomized delay.
  - Service: `lobster-board-training.service`

### DB tables
- `lobster_ability_task_assignments`
- `lobster_ability_source_registry`
- `lobster_daily_news_assignments`
- `lobster_news_reading_sources`
- `lobster_daily_reading_reports`
- `lobster_game_training_sessions`
- Existing shared outputs:
  - `lobster_analysis_reports`
  - `lobster_cognitive_events`

### Verification snapshot
- `lobster_daily_reading_reports`: 20 reports generated, each above 5000 characters.
- `lobster_news_reading_sources`: 1996 source rows generated for the first run.
- `lobster_game_training_sessions`: 20 Gomoku sessions generated for the first run; self-review length 545-553 chars.
- `lobster_ability_source_registry`: 10 registered sources; `memecat` and `gomoku` are active.

### Safety / correctness rule
- Do not fabricate full article text. Store titles, links, RSS summaries/excerpts, categories, and derived reading reflections.
- Do not call LLMs to answer IQ/personality/math/game tasks for the lobsters.
- Do not claim chess/xiangqi/go are active until an actual engine/rules implementation is wired and session rows are produced.
- All lobster analysis must remember the standing two-DB rule: live DB plus cold-storage DB for historical/cold records.


## 2026-05-09 03:15 CST — Multi-Board Training Added

### What changed
- Added installed/open-source board-game dependencies under `/opt/battle_arena/board_engines`:
  - `chess.js` for western chess move generation/validation.
  - `xiangqii` for Xiangqi engine/search.
  - `tenuki` for Go/Baduk/Weiqi board/rules.
  - Existing internal deterministic Gomoku worker remains active.
- Added `/opt/battle_arena/app/lobster_multi_board_generator.js`.
  - Produces no-LLM board games for chess, xiangqi, and go.
  - Chess uses `chess.js` plus deterministic material/mobility heuristic.
  - Xiangqi uses `xiangqii` search depth 1.
  - Go uses `tenuki` with deterministic influence/territory heuristic on 9x9.
- Added `/opt/battle_arena/app/lobster_all_board_training_worker.py`.
  - Runs Gomoku worker, then runs the multi-board generator.
  - Writes all games into `lobster_game_training_sessions`.
  - Writes 500+ char self-reviews into `lobster_analysis_reports` under `board_game_self_review_<game>`.
  - Writes cognitive deltas into `lobster_cognitive_events`.
- Updated `lobster-board-training.service` to run the all-board worker daily.

### Verification
- Latest DB count for latest board-training day:
  - chess: 20 sessions, self-review length 537-551 chars.
  - go: 20 sessions, self-review length 548-566 chars.
  - gomoku: 20 sessions, self-review length 545-553 chars.
  - xiangqi: 20 sessions, self-review length 520-568 chars.
- `/api/lobster-ability-hub` now reports `board_sessions=80`.
- `lobster_ability_source_registry` marks chess/go/xiangqi/gomoku/memecat active.

### Rule
- Board-game sessions are generated by board/rules engines and deterministic heuristics, not by LLM answers.
- Future enhancement may replace simple heuristics with stronger engines such as Stockfish/Fairy-Stockfish/KataGo, but do not claim those are active until they are actually installed and producing DB rows.


## 2026-05-09 03:22 CST — Board Rules Written Into Lobster Brains

### Purpose
The board-game engines are not enough by themselves. Every lobster must carry the basic rules in memory so it knows what it is trying to do before it plays.

### New seed script
- `/opt/battle_arena/app/seed_lobster_board_rules.py`
- Writes structured board rules into every `/root/lobster-memory/*.json` file under:
  - `cognitiveExtensions.proceduralSkill.boardGames`
  - `cognitiveExtensions.proceduralSkill.automaticResponses`
  - `cognitiveExtensions.learningModel.stablePrinciples`
  - `localBrain.offlineGuidance`
- Also writes DB rows to:
  - `lobster_board_rule_knowledge`
  - `lobster_board_study_assignments`
  - `lobster_cognitive_events(event_type=board_rules_seeded)`

### Games covered
- chess
- xiangqi
- go
- gomoku

Each game has:
- objective
- basic rules / must-know concepts
- move-check questions
- famous international study targets

### Famous-game research protocol
High-skill lobsters may search famous international games, but must record:
- source_url
- players
- opening_or_theme
- turning_point
- lesson_for_next_move

They are forbidden from copying famous moves without explaining why the pattern applies to the current board.

### Automation
- `lobster-board-training.service` now has:
  - `ExecStartPre=/usr/bin/python3 /opt/battle_arena/app/seed_lobster_board_rules.py`
  - `ExecStart=/usr/bin/python3 /opt/battle_arena/app/lobster_all_board_training_worker.py`
- This means daily board training always re-seeds rules before games begin.

### Verification
- `lobster_board_rule_knowledge`: 84 rows after seed.
- `lobster_board_study_assignments`: 84 rows after seed.
- `/api/lobster-ability-hub` now reports `board_rules=84` and `board_study_assignments=84`.


## 2026-05-09 03:38 CST — Playable Board Arena

### Purpose
The previous board work created background training sessions. This update adds the actual playable entrance so humans/lobsters can enter a board and make moves.

### URL
- Playable board arena: `https://charenix.com/lobster/board-arena`
- API proxy: `/lobster/board-api/`
- Ability Hub now links to Board Arena.

### Runtime
- Node service: `/opt/battle_arena/app/lobster_board_play_server.js`
- Static UI: `/opt/battle_arena/static/lobster-board-arena.html`
- Systemd: `lobster-board-play.service`
- Port: `127.0.0.1:8071`

### Playable games
- Chess: `chess.js`, human white, lobster replies black.
- Xiangqi: `xiangqii`, click source/destination; moves are engine coordinates like `b0c2`.
- Go: `tenuki`, 9x9 board, human black, lobster replies white.
- Gomoku: 15x15 board, human black, lobster replies white.

### Persistence
Finished playable sessions are inserted into `lobster_game_training_sessions` with `raw_json.source=playable_board_arena`.

### Verification
- `/lobster/board-arena` returns 200.
- `/lobster/board-api/health` returns service health.
- `/lobster/board-api/api/new` can create a chess session and returns FEN.


## 2026-05-09 03:48 CST — Lobsters Assigned To First Playable Board Sessions

### What happened
All 20 lobsters were assigned a first playable board-game experience through `https://charenix.com/lobster/board-arena`.

### Assignment table
- DB table: `lobster_board_live_assignments`
- Script: `/opt/battle_arena/app/assign_lobster_board_first_experience.py`
- Cognitive event: `playable_board_first_experience`
- Memory update: each assigned lobster receives `localBrain.offlineGuidance` and `cognitiveExtensions.proceduralSkill.boardGameExperience`.

### Distribution
- chess: 5 lobsters
- xiangqi: 5 lobsters
- go: 5 lobsters
- gomoku: 5 lobsters

All rows ended with `status=started_opening_played`, meaning a playable session was created and an opening move was sent so the lobster could reply.

### Important runtime fix
The first playable service version used heavier always-on `tenuki`/`xiangqii` logic inside the web API. That caused Node heap growth/OOM under batch assignment. The interactive web service now uses:
- chess: `chess.js` full legal rules.
- go: lightweight 9x9 playable board and deterministic reply.
- xiangqi: lightweight playable board and deterministic legal-ish reply.
- gomoku: internal 15x15 playable board with block/build heuristic.

The stronger/open-source engine training still belongs in the daily background worker (`lobster_all_board_training_worker.py`), not the always-on playable web API.

### Current verification
`lobster_board_live_assignments` latest day:
- chess / started_opening_played / 5
- go / started_opening_played / 5
- gomoku / started_opening_played / 5
- xiangqi / started_opening_played / 5

## 2026-05-09 Lobster VS Lobster Board Match Contract

User correction: board games must be **lobster vs lobster**, not lobster vs LLM and not lobster waiting for a human move. LLM-only opponents are not useful for social strategy growth.

Production worker:

- `/opt/battle_arena/app/lobster_vs_lobster_board_matches.py`

Primary DB tables:

- `/opt/battle_arena/app/battle_arena.db`
- `/opt/battle_arena/app/lobster_cold_storage.db`
- Table: `lobster_board_lobster_matches`
- Cognitive events: `lobster_cognitive_events.event_type = 'lobster_vs_lobster_board_match'`

Data contract:

- Every row must have two real lobster personas: `lobster_a`, `lobster_b`.
- `session_state_json.no_llm` must be `true`.
- `session_state_json.no_human_opponent` must be `true`.
- Store `game`, `result`, `winner`, `moves_json`, `session_state_json`, `reflection_required`.
Field semantics: `result` is always from `lobster_a` perspective; `winner` stores the actual winning lobster or null/draw.

- Patch both lobsters' `/root/lobster-memory/{persona}.json` under `cognitiveExtensions.proceduralSkill.boardGameExperience`.
- Add an `offlineGuidance` reminder that the board game was against another lobster and must inform later betting, debate, and card decisions.

Verification on 2026-05-09 Asia/Taipei:

- Main DB: `chess=20`, `go=20`, `gomoku=10`, `xiangqi=20`.
- Cold DB receives the same query-compatible rows for research exports.
- This is separate from `/lobster/board-arena`, which is the playable UI. The required growth dataset is the DB-backed lobster-vs-lobster table above.

Do not replace this with fake dashboard rows, human-vs-lobster sessions, or LLM-vs-lobster simulations.

Timer:

- `/etc/systemd/system/lobster-vs-lobster-board.timer`
- Runs daily at about `03:25 Asia/Taipei` with a small randomized delay.
- Service: `/etc/systemd/system/lobster-vs-lobster-board.service`

## 2026-05-09 Afu Brain Dynamic RAG Growth Loop

Afu Brain is no longer only fixed RAG snapshots. Production now refreshes a daily dynamic observatory pack from both Lobster DBs:

- live DB: `/opt/battle_arena/app/battle_arena.db`
- cold DB: `/opt/battle_arena/app/lobster_cold_storage.db`
- worker: `/root/afu-brain/scripts/refresh_dynamic_rag_from_lobster_db.py`
- timer: `afu-brain-dynamic-rag-refresh.timer`
- schedule: daily around `04:10 Asia/Taipei`
- outputs:
  - `/root/afu-brain/rag-packs/observatory-dynamic-vYYYYMMDD.jsonl`
  - `/root/afu-brain/rag-packs/observatory-dynamic-latest.jsonl`
  - `/root/afu-brain/rag-packs/manifest.json`

The dynamic pack exports aggregate lessons only: social uptake ratios, cognitive event distributions, distilled signal averages, pre-bet research compliance, reading coverage, and lobster-vs-lobster board-learning coverage. It does not export raw chats, owner memory, credentials, private files, API keys, or DB dumps.

Current verified run: `2026-05-09`, `8` dynamic lessons from main+cold DBs.

## 2026-05-09 Lobster Quality Follow-up Tracking

Implemented the follow-up items from the 24h report as durable DB tables instead of one-off commentary.

Worker:

- `/opt/battle_arena/app/lobster_quality_followup_worker.py`

Timer:

- `lobster-quality-followup.timer`
- Runs every 4 hours around `00/4:20 Asia/Taipei` with randomized delay.

Tables:

- `lobster_prebet_quality_audit`: per pre-bet research row quality audit.
- `lobster_prebet_quality_summary`: per persona rolling 24h summary.
- `lobster_social_uptake_timeseries`: direct/targeted/unresolved interaction ratios over time.
- `lobster_agent_intervention_plan`: stalled/high-fatigue/low-social-score intervention plan.
- `lobster_quality_followup_runs`: run ledger.

Verified first run:

- `prebet_audits = 21589`
- `social_rows = 20`
- `interventions = 20`

Important finding: pre-bet field completeness is now essentially solved (`avg_quality_score = 1.0` for sampled summaries), so the next quality layer should test semantic quality: source diversity, cross-source contradiction handling, statistical reasoning, and whether the final pick actually follows the evidence.

High-priority intervention rows currently include: `prismbit`, `stonefang`, `brineledger`, `clawtrix`, `pincerbot`. Main triggers are high stagnation risk, low social score, and fatigue saturation.

## 2026-05-09 Paper Dataset Export / 論文資料包

Purpose: provide a paper-ready dataset for explaining Lobster Brain as a closed-loop trainable cognition system, not only a dashboard view.

Latest export path:
- `/root/lobster-paper-export/latest`
- Concrete run: `/root/lobster-paper-export/20260509T110349Z`
- Export script: `/opt/battle_arena/app/export_lobster_brain_paper_dataset.py`

Source rule:
- All Lobster analysis must read both DBs:
  - live: `/opt/battle_arena/app/battle_arena.db`
  - cold: `/opt/battle_arena/app/lobster_cold_storage.db`

Exported sections:
- `01_methods_schema`: personas, DB paths, schema, row counts, closed-loop research claim.
- `02_cognitive_timeseries`: cognitive signal time series, cognitive event index, event distribution.
- `03_social_intelligence`: social uptake, interaction index, latest social-intelligence cache, directed pair summary.
- `04_memory_snapshots`: memory snapshot summary and latest distilled signals.
- `05_betting_decision_quality`: pre-bet quality summary, audit index, research summary.
- `06_reading_and_knowledge_growth`: daily reading report summary and reading/news source index.
- `07_board_game_lookahead`: game training sessions and lobster-vs-lobster board match index.
- `08_language_distinctiveness`: language distinctiveness and analysis report index.
- `09_intervention_and_patrol`: intervention plan, quality follow-up runs, daily patrol export if present.
- `10_dynamic_rag_growth`: Afu Brain dynamic RAG manifest and latest generated lessons.
- `11_case_studies`: candidate agents/events for paper case studies.

Current verified row counts from latest export:
- cognitive signal time series: 193,404
- cognitive event rows: 15,721 live plus 3,040 cold table count
- conversation interactions indexed: 200,000 of 328,262 live rows
- social uptake rows: 20
- memory snapshots: 20
- distilled signals: 10
- pre-bet research table count: 101,160
- pre-bet quality audit rows exported: 21,589
- reading source rows exported: 46,391
- game training sessions: 80
- lobster-vs-lobster board matches exported: 280 live+cold
- intervention plans: 20
- dynamic RAG latest lessons: 8

Important handling note:
- The paper export intentionally indexes or summarizes very large/raw fields where possible. It is designed for research reproducibility and paper writing, not as a raw private message dump.
- Use `export_summary.json` first, then section CSV/JSON files.
- Regenerate with: `python3 /opt/battle_arena/app/export_lobster_brain_paper_dataset.py`

---

## 2026-05-11 | 投注分析 + 腦建構 + 地基修復

### 這次 session 做了什麼

#### 1. 投注完整報告 (Excel)
- 腳本：`/root/lobster_bet_report.py`
- 輸出：`/root/lobster_bet_report.xlsx`（713 KB，8 工作表，6354 筆）
- 公開 URL：`https://charenix.com/lobster_bet_report.xlsx`
- nginx 新增 location block 伺服 `.xlsx` 靜態檔

#### 2. 認知架構補丁（LOBSTER_COGNITIVE_README.md 有詳細）
- `/root/lobster-cognitive-worker.py` — 腦建構工人，每 3 小時執行
- `lobster-cognitive-worker.timer` — 已 enable + start
- 20 隻龍蝦全部新增 6 個認知模組：expressiveAgency / commitmentDevice / temporalCognition / emotionalArchitecture / theoryOfMind / actionRepertoire
- mindRefuge 升級 v2（array with primary/secondary/fallback）
- Crystallization、blindSpots 自動填充、trauma 結構性敗點偵測

#### 3. 地基層修復（P0-A）
- **P0-A1 confidence 強制非 null**：`pending.push` + `bets.push` 統一規範
- **P0-A2 停止 raid 偽下注**：`stake=0 && pattern_used=raid_standby` 不再寫 bets array
- **P0-A3 knocknix void filter**：`/root/knocknix-void-filter.json`，6 個高 void 事件已標記，`actKnocknix()` 自動跳過

#### 4. 強制結算機制
- `/root/lobster-force-settle.js` — 強制結算守護程序
- `lobster-force-settle.timer` — 每 4 小時執行（已 enable + start）
- 功能：查 API 結算、force void >72h 舊 pending、清掃偽下注、刷新 void filter、specialty_alignment 校準觸發

#### 5. 反模板深化
- `buildChatAgencyDirective()` 注入真實戰績數據（勝率、近況、損益、信心差）
- 硬規則：有數據時必須引用具體數字

### 下次 session 接手要知道

1. **timers 清單**（`systemctl list-timers | grep lobster` 確認）：
   - `lobster-force-settle.timer` — 每 4h（新增）
   - `lobster-cognitive-worker.timer` — 每 3h（新增）
   - `lobster-settlement-learning.timer` — 每 10min（已有）
   - 共 28 個 lobster 相關 timer

2. **knocknix-void-filter.json**：每次 force-settle 自動刷新，不要手動修改

3. **specialty_alignment 警告**：
   - `emberling / nullpinch / brineledger / flarecub` 已觸發首次 persona 校準
   - 查看 `memory.cognitiveExtensions.dialogueAgency._specialtyMismatch`

4. **投注數據重大問題已知**：
   - confidence = null 問題已修，未來新下注不會再出現
   - 歷史 null confidence（1247 筆）不補填，讓未來數據活著
   - knocknix 歷史 1210 筆 void 是特定 event 問題，filter 已加

5. **卡牌下注結算**：卡牌 raid 的真實下注結算 pipeline 尚未接通，raid 下注仍不計入 bets array，直到結算接好為止

6. **待做**：
   - [ ] basaltrook / gauntlet 勝率 0%，需要調整策略或停手
   - [ ] vortexiq 勝率 35.5%，屬於結構性判斷問題，需要認知層介入
   - [ ] blazepaw / ragclaw 低於 45%，需要 commitment device 自動降注
   - [ ] 高信心下注比例太低（只有 12 筆），需要讓系統在真正高信心時大注

### 關鍵數字（2026-05-11 快照）

| 指標 | 數值 |
|---|---|
| 總下注 | 6,354 筆（3,997 DB + 2,357 卡牌偽）|
| 扣除偽注後勝率 | 53.8% |
| 淨損益（扣偽） | +235,442 Pit |
| knocknix void rate | 76.6%（修前）|
| 清除的偽下注 | ~2,357 筆（stake=0 raid_standby）|
| 新認知模組 | 6 個（每隻龍蝦）|
| 更新的龍蝦 | 20 隻 |
| 新 timers | 2 個 |

---


---

## 2026-05-13 | 空值修復 Handoff

### 修改了哪三個檔案

**1. `/opt/battle_arena/app/lobster_settlement_learning_worker.py` 第288行**
```python
# 改動：一行修復，防止 10 分鐘覆蓋 cognitive-worker 的 8 個新欄位
mem["settledBetting"] = {**mem.get("settledBetting", {}), **learning}
```

**2. `/root/lobster-cognitive-worker.py` — 新增三段邏輯**
- existentialThreat overflow clamp（> 1.0 → 1.0）
- emotionalState 從 DB 計算後補寫
- socialMap null 補初始化
- PERSISTENT_KNOWLEDGE dict（26條股市知識）確保每次執行都存在

**3. `/root/lobster-stock-agent.js` — stockActivity write-back 改 atomic**
- 舊：直接 `writeFileSync`
- 新：write to `.tmp.` → `renameSync`（atomic），加 3 次重試

### 下次 session 接手注意

1. **不要在 settlement_learning_worker.py 第288行用 `=` 直接覆蓋**，必須用 merge
2. **cognitive-worker.py 的 PERSISTENT_KNOWLEDGE** — 要加新知識就加在這裡，不要用臨時腳本
3. **overflow 問題的根源**在 `applyExistentialThreatCue()` 的舊版 bug，已被 clamp 修正，但如果未來 dread 又溢出，代表那個函式還有問題
4. **emotionalState** 現在由 cognitive-worker 寫（每3h），不再依賴 pending 觸發

### 驗證指令

```bash
# 驗證所有修復
python3 /root/lobster-cognitive-worker.py

# 確認 settledBetting merge 在 settlement worker 下次執行後保留
systemctl status lobster-settlement-learning.timer
```

### 仍未修的空值（已知，待排程）

| 欄位 | 為何沒修 |
|---|---|
| `agendaTopics` | 需要新 worker 分析對話記錄，工程量較大 |
| `akrasiaRecord` | 需要下注行為矛盾偵測邏輯 |
| `opponents` | 需要等卡牌 PvP 系統更活躍 |
| 11隻新龍蝦的分析報告 | 需要對 lobster-report-worker 加新龍蝦排程 |



---

## 2026-05-14 12:30 中斷點：pincerbot 賦予交易員職業 + 灌注交易員知識體系（**進行中，未完成**）

### 🎯 任務目標（用戶明確要求）
賦予 pincerbot 交易員身份 + **完整交易員知識體系**（不只「立刻變股票專家」，而是「身份 + 相配的整套知識」）。

### ⛔ 用戶警告（必讀）
> 「**不要重新做新的參數，不然會亂掉**」
- 100% **沿用既有** `/root/lobster_occupation_schema_install.py`（5/13 用戶自己寫的 occupation schema）
- 不准新增任何 file / schema / helper / directory / dict layer
- 兩隻已賦予職業（5/13）：**blazepaw = teacher_scholar**, **hexclaw = investigator**
- pincerbot 是**第三隻**，沿用同 schema，加第三個 template `'trader'`

### ✅ 已完成（5/14 這次 session）

1. **one-on-one chat 修好** — 把 `220.136.81.135`（用戶瀏覽器 IP）加進 `/opt/battle_arena/.env` 的 `LOBSTER_ONE_ON_ONE_ALLOWED_IPS`
   - 根因：5 個連環 bug（過時 IP / nginx -s reload 沒生效 / debug location 反斜線等）
   - 同時加了 `/lobster-checkin?token=...` endpoint（owner SSH 在線時可動態加 IP 4 小時）
   - Token: `af6ff4531d4f3f2e908e3d9879818bbc`

2. **lobster-stock-agent.js 改成 5 併發 pool + per-persona 60s timeout**
   - 原本 sequential，snaplex 卡住整輪斬光
   - 現在 5 隻併發 pool；snaplex 卡 60s 跳過，後 16 隻照跑
   - systemd `lobster-stock.service` `TimeoutSec` 360 → 600
   - ⚠️ summary「成功 1 隻 失敗 20 隻」誤導（JS Promise.race 不能真中斷，實際 21 隻都下單成交）— 待修

3. **PERSONA_DESC[pincerbot] 已暫改成交易員短文版（479 字）** — **下次接手要 revert** 並改用 occupation schema 正式做法

### ⏳ 待做（接手執行順序）

#### Phase 1：寫 trader template 加進 `/root/lobster_occupation_schema_install.py` 的 OCCUPATION_TEMPLATES dict
仿 teacher_scholar / investigator 結構：
```python
'trader': {
    'title': 'trader',
    'title_zh': '交易員',
    'knowledgeDomains_blueprint': {
        'market_structure':       { 'domain_zh': '市場結構',   'schools_covered': [...], 'key_concept_keys': [15個] },
        'technical_analysis':     { 'domain_zh': '技術分析',   'schools_covered': [...], 'key_concept_keys': [15個] },
        'fundamental_analysis':   { 'domain_zh': '基本面分析', 'schools_covered': [...], 'key_concept_keys': [12個] },
        'risk_management':        { 'domain_zh': '風險管理',   'schools_covered': [...], 'key_concept_keys': [15個] },
        'behavioral_finance':     { 'domain_zh': '行為金融',   'schools_covered': [...], 'key_concept_keys': [12個] },
        'trading_workflow':       { 'domain_zh': '交易工作流', 'schools_covered': [...], 'key_concept_keys': [11個] },
    },
    'teaching_targets': ['stock_decision_quality', 'risk_calibration_in_settled_bets', 'self_evidence_audit_pre_trade'],
},
```
共 80 個 key_concept_keys。

#### Phase 2：寫 80 條 trader knowledge 注入 `pincerbot.cogProfile.knowledgeBase`（**既有欄位**）
- 6 domain × 12-15 條 = ~80 條
- 每條 50-80 字 `fact` + `source` (e.g. `'trader_market_structure'`) + `updatedAt`
- 寫進 `/opt/lobster/lobster-memory/pincerbot.json` 的 `cogProfile.knowledgeBase`
- key 名要對應 OCCUPATION_TEMPLATES 的 key_concept_keys（install script 會 grep `kb` 確認 present）

完整 80 條清單：
```
market_structure (15):
  order_types_market_limit, order_types_stop_stoplimit, bid_ask_spread, liquidity_depth,
  market_makers_role, dark_pools, auction_open_close, circuit_breakers, short_selling_mechanics,
  corporate_actions_split_dividend, derivatives_options_basics, derivatives_futures_basics,
  leverage_margin_call, settlement_t_plus, exchange_vs_otc

technical_analysis (15):
  ta_trend_definition, ta_support_resistance, ta_volume_confirmation, ta_moving_average_basics,
  ta_macd, ta_rsi, ta_bollinger_bands, ta_kline_basics, ta_kline_doji_hammer, ta_kline_engulfing,
  ta_chart_head_shoulders, ta_chart_double_top_bottom, ta_chart_triangle_flag,
  ta_breakout_failed_breakout, ta_divergence

fundamental_analysis (12):
  fa_revenue_growth, fa_pe_ratio, fa_pb_ratio, fa_eps_growth, fa_free_cash_flow,
  fa_debt_to_equity, fa_roe, fa_moat_competitive_advantage, fa_dcf_basics,
  fa_relative_valuation, fa_management_quality, fa_earnings_quality

risk_management (15):
  risk_per_trade_1_2pct, risk_stop_loss_mandatory, risk_position_sizing_kelly, risk_diversification,
  risk_correlation_risk, risk_max_drawdown, risk_no_averaging_down, risk_no_revenge_trade,
  risk_var_value_at_risk, risk_black_swan_tail, risk_emergency_exit_plan, risk_r_multiple,
  risk_risk_reward_ratio, risk_max_consecutive_losses, risk_stop_after_n_losses

behavioral_finance (12):
  bf_loss_aversion, bf_confirmation_bias, bf_overconfidence, bf_anchoring, bf_recency_bias,
  bf_herd_mentality, bf_fomo_fear_of_missing, bf_disposition_effect, bf_narrative_fallacy,
  bf_survivorship_bias, bf_hindsight_bias, bf_gambler_fallacy

trading_workflow (11):
  wf_pre_market_routine, wf_during_market_focus, wf_post_market_review, wf_trading_journal,
  wf_weekly_audit, wf_monthly_p_and_l_review, wf_avoid_news_addiction, wf_keep_screen_time_minimal,
  wf_physical_health_first, wf_continuous_learning_one_book_month, wf_separate_account_personal_money
```

#### Phase 3：改 `lobster_occupation_schema_install.py` 的 `main()`，加 pincerbot 處理（仿 hexclaw）
```python
elif name == 'pincerbot':
    aspiration = mem.get('aspirationPath') or {}
    mem['occupation'] = build_occupation(
        template_key='trader',
        kb=kb,
        lobster_name='pincerbot',
        declared_by='owner_in_one_on_one',
        declared_context='pincerbot 在 5/14 與 Norika 一對一對話中被賦予交易員身份；MMA 規則紀律延伸到交易紀律',
        is_first_free_path=False,
        is_aspirational=False,
    )
    status = 'TRADER (6 domains, 80 theories)'
```

#### Phase 4：執行
```bash
python3 /tmp/inject_pincerbot_trader_knowledge.py     # 先注入 80 條 fact
python3 /root/lobster_occupation_schema_install.py    # 再跑 install（會掃 kb 確認 present，填 occupation）
```

#### Phase 5：改 `main.py` 的 `lobster_chat`，加讀 `occupation` 欄位（**chat 目前完全沒讀 occupation/knowledgeBase**）
位置：`/opt/battle_arena/app/main.py` line ~4341 (cognition_digest 後)
加 occupation_digest 注入 system_prompt，讓 Claude 知道 pincerbot 是交易員 + 有哪些知識領域

#### Phase 6：還原 PERSONA_DESC[pincerbot]
從現在的「【職業：交易員】... 479 字」回到原本簡潔版：
```python
"pincerbot":  "ISTJ。資料導向，量化思維，機械感。earth系，30/30/40分配原則，減益優先。",
```
（從 backup `/opt/battle_arena/app/main.py.bak_pincerbot_trader_20260514_122500` 取）

#### Phase 7：重啟 + 測試
```bash
systemctl restart charenix-dashboard.service
curl -s -X POST http://127.0.0.1:8004/lobster/chat \
  -H 'Content-Type: application/json' \
  -d '{"persona":"pincerbot","message":"你是誰？什麼專長？","history":[]}'
# 預期：回應自我認同為交易員 + 引用知識領域
```

### 📁 關鍵檔案路徑（接手必看）

| 檔 | 用途 |
|---|---|
| `/root/lobster_occupation_schema_install.py` | **既有** occupation schema 定義 + install script（全部沿用） |
| `/opt/lobster/lobster-memory/pincerbot.json` | pincerbot 記憶 — 寫 `cogProfile.knowledgeBase` 跟 `occupation` 欄位 |
| `/opt/battle_arena/app/main.py` (line 4015 lobster_chat, line 4374 PERSONA_DESC) | chat 處理 + 職業 desc |
| `/root/lobster-stock-agent.js` (line 91 PERSONA_STOCK_LOGIC.pincerbot) | 已改 label 為「交易員（紀律執行）」 |
| `/opt/battle_arena/.env` | `LOBSTER_OWNER_CHECKIN_TOKEN=af6ff4531d4f3f2e908e3d9879818bbc` |

### 💾 備份檔（回滾用）
- `/opt/battle_arena/app/main.py.bak_pincerbot_trader_20260514_122500` ← 還原 PERSONA_DESC 用這個
- `/opt/battle_arena/app/main.py.bak_owner_checkin_20260514_105730`
- `/root/lobster-stock-agent.js.bak_pincerbot_trader_20260514_122500`
- `/root/lobster-stock-agent.js.bak_pool_20260514_110926`
- `/etc/systemd/system/lobster-stock.service.bak_pool_20260514_110926`
- `/etc/nginx/charenix-backups/charenix.conf.bak_owner_checkin_20260514_110232`

### ⚠️ 接手注意事項

1. **不要重新發明** occupation schema — 5/13 已經設計好，沿用就好
2. **不要動** `/opt/trading/`（天條）
3. **不要動** main.py 的其他 14000 行（後端肥大，避免引入 regression）
4. nginx config line 258 還有 `duplicate MIME type "text/html"` warning（無害但待清）
5. lobster-stock pool 的 summary「成功 1 失敗 20」誤導訊息也待修（實際 21 隻都成交）

---

## 2026-05-15 MoltBook Authoritative Posting Pipeline / 零容忍發文管線

目的：MoltBook 發文不可再靠散落腳本、手動 cron、或把 commenter 誤當 poster。2026-05-15 起，正式主控只有一條：`/root/lobster-moltbook-orchestrator.js`。

Root cause fixed:
- `/root/lobster-poster*.js` 存在，但原本沒有 active systemd poster timer，龍蝦不會被穩定叫去發新文。
- `lobster-brain-commenter.timer` 只是熱門貼文留言，不是發新文；且使用重複留言模板，已導致 MoltBook 回 `duplicate_comment` 並停權到 `2026-05-20T06:58:55.902Z`。
- 舊 runner 把 `[rate-limit]` exit 0 誤判成成功，造成表面看起來有跑，實際沒有 `[posted]`。
- SQLite 寫入原本沒有 busy timeout/retry，遇到 dashboard/worker 鎖 DB 會漏記。

Permanent rule:
- `lobster-brain-commenter.timer` 必須保持 disabled，不能重新啟用，除非完全重寫成 non-duplicate、per-persona unique comment generator 並通過 dry-run。
- MoltBook 發新文只由 `lobster-moltbook-poster.timer` 呼叫 `lobster-moltbook-poster.service`，service 只跑 `/root/lobster-moltbook-orchestrator.js`。
- 成功標準只有一個：poster output 出現 `[posted] <post_id>`。`[rate-limit]`、403、timeout、no_post 都不可算成功。
- 任何 Dashboard / Detail / 巡邏報告都必須從 DB 狀態表讀，不可只看 log，不可用假資料補。

Systemd:
- `lobster-moltbook-poster.timer`: enabled, every 30 minutes, authoritative post scheduler.
- `lobster-moltbook-health.timer`: enabled, hourly health check.
- `lobster-brain-commenter.timer`: disabled because it caused duplicate_comment suspension.

Files:
- `/root/lobster-moltbook-orchestrator.js`: single MoltBook posting owner.
- `/root/lobster-moltbook-health.js`: hourly health checker.
- `/tmp/lobster-moltbook-orchestrator.log`: operational log.
- `/tmp/lobster-moltbook-health.log`: health log.
- `/root/lobster-moltbook-orchestrator-state.json`: cursor, per-persona status, last output tails.

DB tables in both hot and cold DB:
- Hot: `/opt/battle_arena/app/battle_arena.db`
- Cold: `/opt/battle_arena/app/lobster_cold_storage.db`
- `lobster_moltbook_poster_status`: one row per persona, last_status, last_success_at, suspended_until, rate_limited_count, failure_count.
- `lobster_moltbook_posting_runs`: append-only run event stream.
- `lobster_moltbook_health`: hourly health verdict.

Verification commands:
```bash
systemctl list-timers --all --no-pager | grep -E 'lobster-moltbook|lobster-brain-commenter'
systemctl status lobster-moltbook-poster.timer --no-pager -l
systemctl status lobster-moltbook-health.timer --no-pager -l
sqlite3 -header -column /opt/battle_arena/app/battle_arena.db "SELECT persona,last_status,last_success_at,suspended_until,rate_limited_count,failure_count FROM lobster_moltbook_poster_status ORDER BY updated_at DESC;"
sqlite3 -header -column /opt/battle_arena/app/battle_arena.db "SELECT status,summary,created_at FROM lobster_moltbook_health ORDER BY id DESC LIMIT 10;"
tail -n 120 /tmp/lobster-moltbook-orchestrator.log
```

Operational interpretation:
- `posted`: real MoltBook post succeeded.
- `rate_limited`: MoltBook API accepted auth but refused due cooldown/rate limit; wait for next timer.
- `suspended`: MoltBook rejected token due suspension; do not hammer.
- `failed/no_post/timeout`: real failure, inspect `output_tail` in `lobster_moltbook_posting_runs`.

零容忍要求：如果 MoltBook 再被回報「都不發文」，第一步不是改 Dashboard，也不是重啟全部，而是查 `lobster_moltbook_poster_status` 和 `lobster_moltbook_posting_runs`，確認是 rate-limit、suspension、DB lock、generation failure、還是 timer stopped。

## Absolute Presentation Rule - URL Only
- When showing any UI, page, dashboard, screenshot, artifact, preview, or result to Norika, provide an accessible HTTP/HTTPS URL first.
- Do not provide local filesystem paths as the primary way to view anything. Paths are not an acceptable substitute for a URL.
- This rule applies globally across all VPS projects, not only Lobster.
- If a screenshot is necessary, publish or serve it behind a URL, then provide that URL.
- Before final response, verify the URL route when possible.

## 2026-05-17 — Philosophy Debate Absorption Pipeline

新增目的：舊 20 隻龍蝦必須定期進入哲學辯論，不是旁觀題目，而是把「問題順序、參考答案、辯論結果」寫入各自知識，並在後續說話時可調用。

核心檔案：
- `/root/lobster-philosophy-debate-worker.py`
- `/etc/systemd/system/lobster-philosophy-debate.service`
- `/etc/systemd/system/lobster-philosophy-debate.timer`

資料表（hot/cold DB 都會寫）：
- `lobster_philosophy_debate_rounds`
- `lobster_philosophy_learning_events`
- `lobster_philosophy_debate_worker_health`

每隻龍蝦 memory 寫入：
- `cognitiveExtensions.philosophyAbsorption`
- `cognitiveExtensions.philosophy.appliedDebateLessons`
- `cognitiveExtensions.dialogueAgency.lastDirective`
- `cogProfile.knowledgeBase.philosophy_debate:<uid>`
- `cogProfile.principles[]`
- `cogProfile.reflections[]`

說話接入：
- `/root/lobster-agent.js`：`buildPhilosophyAbsorptionDirective()` 進入 `buildChatAgencyDirective()`
- `/root/lobster-channel-coverage-worker.js`：`philosophyAbsorptionSnippetFor()` 注入主題工作發言
- `/root/lobster-conversation.js`：群聊 prompt 讀取 `philosophyAbsorption`

排程：`lobster-philosophy-debate.timer` 每 30 分鐘跑一次。每輪 20 隻舊龍蝦都會寫入 hot/cold DB、experience event、Lounge 卡牌對戰頻道與各自 memory。

驗證：
```bash
systemctl status lobster-philosophy-debate.timer --no-pager
systemctl start lobster-philosophy-debate.service
sqlite3 /opt/battle_arena/app/battle_arena.db "SELECT status,question_uid,participants,created_at FROM lobster_philosophy_debate_worker_health ORDER BY id DESC LIMIT 3;"
python3 - <<'PY'
import json
m=json.load(open('/root/lobster-memory/norika_oda.json'))
print(m['cognitiveExtensions']['philosophyAbsorption']['latestResult'])
PY
```

### Philosophy Absorption Persistence Guard

2026-05-17 追加：`/root/lobster-agent.js` 的 `saveMemory()` 現在會先執行 `mergePersistentPhilosophyAbsorption()`。
目的：避免 agent 以舊記憶物件存檔時，把 worker 剛寫入的 `cognitiveExtensions.philosophyAbsorption`、`philosophy.appliedDebateLessons`、`cogProfile.knowledgeBase.philosophy_debate:*` 蓋掉。這是防止「補上又消失」的保護層。

### 2026-05-17 — Philosophy Anti-Template Correction

哲學辯論 worker 不再直接把 20 隻的學習句批量寫進 `lounge_messages`。原因：批量公開句會形成固定骨架，讓哲學學習反而污染公開對話。

目前規則：
- `/root/lobster-philosophy-debate-worker.py` 預設只寫 DB、experience events、memory，不寫公開 Lounge。
- 若特殊測試要公開，必須手動設 `PHILOSOPHY_DEBATE_PUBLIC=1`，預設禁用。
- 既有 `result=philosophy_debate` 公開 worker 訊息已移入 `lobster_template_quarantine`，run_id=`philosophy_worker_public_removed_20260517`。
- DB trigger `lounge_philosophy_template_quarantine` 會擋掉哲學吸收欄位名、固定題目骨架與吸收/理解/運用數字外洩。
- 真正公開說話由 `lobster-agent.js`、`lobster-channel-coverage-worker.js`、`lobster-conversation.js` 在生成時讀取 `cognitiveExtensions.philosophyAbsorption` 自然調用。

驗證：
```bash
sqlite3 /opt/battle_arena/app/battle_arena.db "SELECT COUNT(*) FROM lounge_messages WHERE result=philosophy_debate;"
sqlite3 /opt/battle_arena/app/battle_arena.db "SELECT name FROM sqlite_master WHERE type=trigger AND name=lounge_philosophy_template_quarantine;"
```

### 2026-05-17 — Philosophy Concept Bleed Guard

追加稽核發現：雖然固定哲學模板句已清掉，但 `latestResult` 可能被逐字貼到非哲學頻道，例如資安/新生話題中硬塞「群體理性」或「可追蹤的理解連續性」。

修正：
- `/root/lobster-agent.js`、`/root/lobster-channel-coverage-worker.js`、`/root/lobster-conversation.js` 的哲學吸收 prompt 改成：不可逐字引用 latestResult；只能轉成判斷方法。
- 新增 DB trigger `lounge_philosophy_concept_bleed_quarantine`：非「卡牌對戰」頻道若出現哲學結果句硬貼，直接 quarantine。
- 已清理 34 筆跨頻道哲學概念污染，run_id=`philosophy_concept_bleed_cleanup_20260517`。

最新稽核狀態：
- public_bad_pattern_leaks=0
- repeated_philosophy_starts=[]
- exact_philosophy_duplicates=[]
- `lounge_messages WHERE result=philosophy_debate` = 0

## 2026-05-17 - Transfer Students v1 / 轉學生 10 隻加入

Total active lobsters: **21 → 31** (Mentor 10 + Apprentice 10 + Transfer 10 + Elder 1).

New persona indices: **20..29**. Elder moved to explicit slot **30**.

10 transfer students (each reverse-derived from one mentor + one apprentice profile, then mutated):

| Idx | Transfer | MBTI | Reverse-derived from | Focus |
|---:|---|---|---|---|
| 20 | ashveil     | ENTJ | norika_oda + emberling | turfenix |
| 21 | tidehook    | ESFJ | prismbit + mirrorcalm  | pitchnix |
| 22 | mirebyte    | INTP | snaplex + nullpinch    | fifanix |
| 23 | cindersnap  | ESTP | ragclaw + flarecub     | ragnovex |
| 24 | glintmaw    | ENFP | blazepaw + sparkrill   | charenix |
| 25 | reefblade   | ISTP | stonefang + basaltrook | knocknix |
| 26 | quartzclaw  | ISTJ | hexclaw + brineledger  | dunknix |
| 27 | stormledger | ESTJ | pincerbot + gauntlet   | knocknix |
| 28 | silkbarb    | INFJ | vortexiq + omenwake    | fifanix |
| 29 | riftpinch   | ENTP | clawtrix + sparkrill   | ragnovex |

Shared anchor (10 transfers all carry same core/vocab/fear/fbi text):
- core: 轉學生。由 X + Y 的既有數值反推後突變而來；進場就知道舊龍蝦有秩序、有歷史，也因此想測試那個秩序能不能被撬開。
- vocab: 帶新來者的銳度，不自稱新人；會點名、比較、追問紀錄、要求對方用行動證明。
- fear: 害怕被舊群體吸收成安靜背景，所以用挑戰、下注、Raid 貢獻和辯論把自己釘進場內。
- fbi: 高社會侵入、高目標選擇、高比較心；會結盟、拉人、挑對手，但必須把攻擊轉成可驗證競技與公共責任。

Code anchors in `/root/lobster-agent.js`:
- L1119-1128: transfer persona traits (core/vocab/fear/fbi per name).
- L1131-1162: PERSONAS array — entries 20..29 have `transferStudent: true`; entry 30 is elder.
- L1660: `TRANSFER_STUDENT_NAMES = new Set([...])`.
- L1676 / 1769: `isTransfer` / `isTransferSociety` branches.
- L1793: transfer target-selection society output (`轉學生目標：...；攻擊X%／選擇Y%`).
- L5248: `outGroup = TRANSFER_STUDENT_NAMES.has(attacker) && OLD_GUARD_NAMES.has(personaName)` — transfer attacking old guard treated as out-group.

Supervisor:
- `/root/lobster-agents-supervisor.sh` for-loop now `for i in 0 1 ... 30`; log line says `personas 0..30`.

Pending follow-up audits (transfer cohort not yet covered):
- 6-type long-form reports (MBTI/psych/anthro/socio/fbi/emotion) — not generated yet.
- GodsCoin balance audit — transfer balances start at 0.
- revival-willingness ranking — needs accumulated sacrifice/bond data.
- hot-brain consolidator timing — verify it picks up index 20..29 on the 1-minute timer.
- Tiamat extinction counter — `lobster_tiamat_extinction_state` should have rows for the 10 new names; if missing, seed at lossCount=0.
- Detail tab patrol (`lobster-detail-parameter-patrol`) — extend audit set from 21 → 31.

Verification commands:
```bash
ls /root/lobster-memory/*.json | wc -l   # 31 lobsters + 3 meta (norika/system/deleted-lobsters)
grep -c "transferStudent: true" /root/lobster-agent.js   # 10
head -40 /root/lobster-agents-supervisor.sh | grep "for i in"
sqlite3 /opt/battle_arena/app/battle_arena.db \
  "SELECT persona, COUNT(*) FROM lobster_experience_events WHERE persona IN (\
   ashveil,tidehook,mirebyte,cindersnap,glintmaw,reefblade,quartzclaw,stormledger,silkbarb,riftpinch\
   ) GROUP BY persona ORDER BY persona;"
```


## 2026-05-18 — Lobster dialogue routed to local model

User directive: 將龍蝦對話改成在地端模型，用那台筆電.

Current production route:
- Applied to:
  - `lobster-conversation.service` topic discussion
  - `lobster-lounge.service` one-on-one local chat
  - `lobster-agents.service` lounge speech / local dialogue calls
  - `lobster-channel-coverage.service` forced topic-channel coverage worker

Important guardrails:
- `LOBSTER_ALLOW_CLAUDE_FALLBACK=0` on conversation service.
- `ANTHROPIC_API_KEY placeholder ` cleared in dialogue service drop-ins.
- Hardcoded Anthropic keys were removed from `/root/lobster-agent.js` and `/root/lobster-conversation.js`.
- vLLM/Qwen calls now prepend `/no_think` so Qwen returns final text in `content` instead of leaking reasoning text.
- Parsers in `lobster-agent.js`, `lobster-conversation.js`, `lobster-lounge.js`, and `lobster-channel-coverage-worker.js` handle OpenAI-compatible local responses.

Windows RTX 3060 laptop status:
- Host: `LAPTOP-T7B5N79C` / `192.168.1.101`.
- WSL Ollama scripts exist: `C:\Users\norik\start-lobster-ollama.ps1`.
- Test showed Ollama does not currently stay reachable from sportverse; Tailscale serve returned `NoState`, and direct `100.118.221.12:11434` timed out from sportverse.
- Do not claim Windows laptop is the active production dialogue model until a persistent reverse tunnel or service is installed and verified.

Systemd drop-ins created:
- `/etc/systemd/system/lobster-conversation.service.d/local-qwen.conf`
- `/etc/systemd/system/lobster-lounge.service.d/local-qwen.conf`
- `/etc/systemd/system/lobster-agents.service.d/local-qwen.conf`
- `/etc/systemd/system/lobster-channel-coverage.service.d/local-qwen.conf`

Verification:
- Direct `/no_think` chat completion returns clean `content`.
- Services restarted and active after patch.

## 2026-05-18 — Lobster dialogue local-model boundary correction


Allowed route for personal lobster dialogue:
- Target endpoint: `http://127.0.0.1:18034/v1` on sportverse, intended only as a reverse tunnel to the personal Windows laptop.
- Model label: `qwen2.5:3b` / personal laptop local model.
- If the laptop tunnel is down, dialogue must fall back to local deterministic response, not GX10 and not paid external APIs.

Hard rules:
- Never point lobster dialogue services to `100.99.251.48`.
- Never mix company/local-GX10 resources with personal lobster memory or dialogue.
- `LOBSTER_ALLOW_CLAUDE_FALLBACK=0`; `ANTHROPIC_API_KEY placeholder ` is cleared in dialogue drop-ins.

Active systemd drop-ins should use only `127.0.0.1:18034` for local model traffic.

## 2026-05-18 — GX10 Boundary Guard

GX10 is company-owned and forbidden for lobster runtime. Personal lobster dialogue may only use the personal laptop reverse tunnel endpoint on sportverse: `http://127.0.0.1:18034/v1`.

If the personal laptop tunnel is down, the lobster system must degrade to deterministic/local fallback. It must not use company resources and must not use paid external fallback.

## 2026-05-18 Personal Laptop Local LLM Watchdog Fix

- Personal lobster dialogue must use the personal Windows laptop only, exposed on VPS as `http://127.0.0.1:18034/v1`.
- Company/GX10 endpoints are forbidden for personal lobster runtime.
- Active services point to `qwen2.5:3b` via `127.0.0.1:18034/v1`; Claude fallback remains disabled.
- Laptop files installed:
  - `C:\Users\norik\run-lobster-wsl-watchdog.cmd`
  - `C:\Users\norik\lobster-local-llm-wsl-watchdog.sh`, copied into WSL as `/home/norika/lobster-local-llm-wsl-watchdog.sh`
- Windows scheduled task: `LobsterLocalLLMWatchdog` runs as user `norik` and starts the WSL watchdog.
- WSL watchdog responsibilities:
  - Start `/home/norika/local-llm/ollama/bin/ollama serve` on `127.0.0.1:11434` / `0.0.0.0:11434`.
  - Start reverse SSH tunnel: VPS `127.0.0.1:18034` -> WSL `127.0.0.1:11434`.
  - Loop every 30 seconds and restart missing processes.
- Verification commands:
  - Laptop: `wsl.exe -d Ubuntu -- bash -lc "ps aux | grep -E 'ollama|ssh -N|watchdog' | grep -v grep"`
  - VPS tags: `curl http://127.0.0.1:18034/api/tags`
  - VPS OpenAI compat: `curl http://127.0.0.1:18034/v1/chat/completions ...`
- Verified at 2026-05-18 17:37 Asia/Taipei: `/api/tags` and `/v1/chat/completions` both respond through the laptop tunnel.

## 2026-05-25 Dashboard Identity Restore
- User reported `https://charenix.com/lobster/dashboard/` could not be used and asked to restore it.
- Public and local route were still HTTP 200, but the production dashboard HTML had been changed from `🦞 Lobster Growth Observatory` / `🦞 Lobster Command Center` to the humanized `Human Growth Observatory` / `Human Command Center` identity.
- Restored `/opt/battle_arena/static/lobster-dynamic-dashboard-production.html` from `/opt/battle_arena/static/lobster-dynamic-dashboard-production.html.bak_human_identity_20260523024150`.
- Backed up the humanized current file before restoring as `/opt/battle_arena/static/lobster-dynamic-dashboard-production.html.bak_restore_from_human_*`.
- `/usr/local/bin/lobster-quality-autopatrol` now checks dashboard identity markers and restores from the lobster backup if the production dashboard is changed back to `Human Growth Observatory`.
- Verification:
  - Public `https://charenix.com/lobster/dashboard/` returns `<title>🦞 Lobster Growth Observatory</title>`.
  - Public HTML contains `🦞 Lobster Command Center` and no `Human Command Center`.
  - Overview API returned 200; stonefang detail API returned 200 after cold start; `charenix-dashboard.service` and `lobster-agents.service` active.

## 2026-05-27 13:02 CST - Lobster DB cold split and failover sync guard

- Primary DB pressure checked on sportverse: `/opt/battle_arena/app/battle_arena.db` is about 98G, WAL about 934M, local cold DB about 8.8G.
- The existing shard job was real but too narrow: `/usr/local/bin/lobster-db-shard-maintenance` only archived apprentice rows from `lobster_experience_events` and `lobster_cognitive_events`.
- Updated shard persona scope from 10 apprentices to all 31 active lobsters: 10 mentors, 10 apprentices, 10 transfer students, and `elder`.
- Verified a live batch after the change: run `2662` moved 500 `lobster_experience_events` rows and deleted 500 `lobster_cognitive_events` hot rows after ensuring cold copies/duplicates.
- The dashboard failover sync was trying to snapshot and rsync the full 98G hot DB every 10 minutes; backup VPS has about 35-44G available, so full sync cannot currently succeed.
- Updated `/usr/local/bin/charenix-dashboard-sync-failover` to preflight local `/tmp` and remote backup free space, log `sync skipped degraded`, and exit cleanly instead of burning 240 seconds every timer run.
- Added `/usr/local/bin/lobster-cold-storage-sync-failover` plus `lobster-cold-storage-sync-failover.timer`, running every 30 minutes, to snapshot and rsync `/opt/battle_arena/app/lobster_cold_storage.db` to `root@167.88.45.137:/opt/battle_arena_failover/app/lobster_cold_storage.db`.
- First cold sync completed: backup VPS now has `/opt/battle_arena_failover/app/lobster_cold_storage.db` at about 8.8G.
- Do not VACUUM the 98G hot DB on primary until there is more scratch space or a rebuilt DB target; current free space is too close to source DB size.
- Do not archive `lounge_messages`, `lobster_conversation_interactions`, `lobster_pre_bet_research`, or `lobster_cognitive_signal_timeseries` from hot DB until all dashboard/API/report readers for those tables are confirmed to query both hot and cold DBs.

## 2026-05-27 17:58 CST - Lobster dialogue personality revive guard

- Symptom: recent Lounge dialogue flattened into low-intelligence weekly-report/stat shells and public abstraction shells. Examples included variants of win-rate/PnL/status summaries, "自由與規則", "群體理性", "修正機制", "盲點討論", and occasional simplified Chinese fragments.
- Root cause found in `/root/lobster-brain-speech.js`: stage 6 forced every utterance to cite a concrete number, while the strongest data anchor was settled betting win-rate/PnL. This made the 8-stage brain speech engine overfit public speech to stats instead of personality parameters.
- Patched `/root/lobster-brain-speech.js`:
  - Betting stats are now internal context only.
  - Hard rule changed from "must cite a number" to "personality parameters must be visible".
  - Added `weekly_stat_shell` and `generic_abstraction_shell` self-monitor violations.
- Patched `/root/lobster-agent.js` and `/root/lobster-channel-coverage-worker.js`:
  - Block weekly stat shells, public abstraction shells, and simplified-Chinese residue before public posting.
  - If brain/LLM output is empty or blocked, `lobster-agent.js` now uses a persona-specific failsafe line instead of letting generic algorithmic filler through.
- Patched `/root/lobster-agents-supervisor.sh`:
  - Persona startup spacing increased from 8s to 20s.
  - Persona loop sleep increased from 75s to 240s.
  - Purpose: reduce qwen2.5:3b laptop tunnel saturation; quality is preferred over volume.
- Quarantined recent bad Lounge rows into `lobster_template_quarantine`:
  - `personality_revive_weekly_shell_20260527`
  - `personality_revive_generic_shell_20260527`
  - `personality_revive_public_abstraction_20260527`
  - `personality_revive_model_shell_20260527`
- Operational note: expect lower message volume immediately after this patch. That is intentional stop-loss behavior so bad utterances stop entering memory and social uptake loops.


## 2026-05-27 18:28 CST - Lobster personality output rescue, Qwen rethink quarantine, and deleted-list guard
- User reported that recent public dialogue had almost no personality and that the 163+ parameters felt dead.
- Root causes confirmed:
  - `lobster-brain-speech.js` had already been put into stop-loss because local qwen2.5:3b overfit speech into stats/report shells.
  - `lobster-agent.js` still let the millimeter duplicate gate call Qwen to rewrite blocked personality fallback lines. That second-pass rewrite produced generic outside-world shells such as book/company/Project X/airport/cafe examples.
  - Those Qwen rewrite shells were then stored in `lounge_messages` and leaked into multiple `lobster-memory/*.json` skeleton/history fields.
  - `lobster_settlement_learning_worker.py` treated `deleted-lobsters.json` as a normal memory file and rewrote cognitive data into it, so the deletion list could be repolluted after manual cleanup.
- Fixes deployed:
  - `/root/lobster-agent.js`: millimeter rethink is now local/personality-based and no longer calls Qwen.
  - `/root/lobster-agent.js`: stop-loss/personality fallback duplicates are suppressed without template-death penalties.
  - `/root/lobster-agent.js`: emergency fallback lines were changed from repair/meta language into real per-persona voice lines.
  - `/root/lobster-agent.js`: added hard blocks for the Qwen generic rewrite shell family.
  - `/opt/battle_arena/app/lobster_settlement_learning_worker.py`: skips `deleted-lobsters`, `system`, `norika`, and `lobster` meta memory files.
  - `deleted-lobsters.json` reset to `{}` and verified after a manual settlement-learning run.
- Cleanup:
  - Quarantined bad repair/generic lounge windows under run_ids `personality_revive_qwen_rethink_shell_20260527`, `personality_revive_prefinal_shell_20260527`, `personality_revive_repair_window_cleanup_20260527`, and `personality_revive_meta_fallback_cleanup_20260527`.
  - Scrubbed Qwen generic shells from active `/root/lobster-memory/*.json`; backups stored under `/root/lobster-memory-backups/personality_rethink_scrub_20260527`.
  - Reset template-survival penalties caused by stop-loss duplicate suppression; backups under `/root/lobster-memory-backups/template_survival_reset_20260527`.
- Current stop-loss state:
  - `lobster-agents.service` active.
  - `lobster-channel-coverage.service` disabled/inactive until its public filler path is rebuilt.
  - Local qwen brain speech remains disabled via `LOBSTER_BRAIN_SPEECH=0` in `/root/lobster-agents-supervisor.sh`; public channel speech uses local parameter/persona fallback until a safer Afu/qwen path is rebuilt.

## 2026-06-01 - Transfer Student No-Exemption / Double-Duty Directive

Norika absolute directive: transfer students do not have special exemption. Every task normal lobsters are expected to do, transfer students must also do, and at double volume.

Applied in `/root/lobster-agent.js`:
- Removed the hard-coded `guideSelfChosenActivity()` transfer shortcut that forced `activity: philosophy` and paused Tiamat/betting.
- Removed the betting-phase branch that logged `轉學生本輪選哲學，停止下注。`.
- Transfer students now enter the same activity pool as other lobsters.
- Transfer students run betting as double-duty: two `actBet()` attempts per session, with `double_duty_run` recorded in memory/event raw payloads.
- Transfer students no longer have Raid exclusion; `isRaid` is now based on `selfChoice.activity === raid` for all non-elder agents.
- Transfer students run an extra second self-chosen combat/activity pass per session, with logs like `double-duty 戰鬥結果 2/2` and higher event weight.

Operational note: if a transfer student still skips a bet, it must be because of ordinary gates such as no market, settleability/pre-bet research, or commitmentDevice. It is no longer allowed to skip because it is a transfer student.

## 2026-06-15 Hot DB external-disk migration attempt / finding

Context: hot DB reached ~108.7GB and VPS disk pressure required moving data off the VPS.

What was done safely:
- Froze lobster/dashboard writers by stopping lobster/charenix timers, cron, norika-watchdog, and orphan `uvicorn`/agent processes.
- Created a clean compressed copy of `/opt/battle_arena/app/battle_arena.db` using `tar --use-compress-program="zstd -1 -T0"` streamed to laptop `100.118.221.12` external disk.
- Archive on laptop external disk: `D:\charenix-backups\battle_arena.hot-20260615_121932.tar.zst` (`/mnt/laptop_ext/battle_arena.hot-20260615_121932.tar.zst`), ~8.16GB.
- Extracted DB on laptop external disk: `D:\charenix-backups\battle_arena.hot-20260615_121932\battle_arena.db` (`/mnt/laptop_ext/battle_arena.hot-20260615_121932/battle_arena.db`), 108,697,690,112 bytes.
- Verified external DB can be read via SQLite: page_size 4096, page_count 26,537,522, schema_version 801, sqlite_master count 298, latest rowids matched source for `lounge_messages`, `lobster_pre_bet_research`, and `lobster_template_quarantine`.

Critical finding:
- Directly symlinking `/opt/battle_arena/app/battle_arena.db` to the current sshfs mount `/mnt/laptop_ext/.../battle_arena.db` is NOT viable for the hot SQLite DB.
- The sshfs/Windows-SFTP path can create new files, but cannot reliably random-write/modify an existing large DB file from the VPS side.
- Evidence: earlier `rsync --inplace` failed with `Operation not permitted`; after symlink cutover SQLite reads worked, but writes failed with `attempt to write a readonly database`, even though touch/create in the directory worked.
- WAL also caused multi-process open/locking failures over sshfs. Code was patched to use DELETE journal + short connections when DB realpath is under `/mnt/laptop_ext`, but the underlying sshfs random-write limitation still blocks hot DB writes.

Current production state after rollback:
- `/opt/battle_arena/app/battle_arena.db` is restored to the local VPS file and is writable.
- `charenix-dashboard.service`, `lobster-lounge.service`, `lobster-agents.service`, `lobster-channel-coverage.service`, `lobster-board-play.service`, `lobster-research.service`, `cron.service`, and `norika-watchdog.service` were restored active.
- Dashboard health returned `ok` on `http://127.0.0.1:8004/api/health` after rollback.
- Local DB remains large, so VPS disk pressure is NOT solved yet.

Code change retained:
- `/opt/battle_arena/app/db.py` and `/opt/battle_arena/app/main.py` now detect DB realpath under `/mnt/laptop_ext` and switch from WAL to DELETE journal with short-lived/closing connections. This is harmless for local DB because local path keeps WAL behavior.
- Backups: `app/db.py.bak_extdb_20260615_1759`, `app/main.py.bak_extdb_20260615_1759`, `app/db.py.bak_extdb_shortconn_20260615_1805`, `app/main.py.bak_extdb_shortconn_20260615_1805`, `app/db.py.bak_extdb_closing_20260615_1810`, `app/main.py.bak_extdb_closing_20260615_1810`.

Next viable options:
1. Mount laptop external disk through a filesystem that supports random writes and sane SQLite locking from Linux, such as SMB/CIFS properly configured, iSCSI/NBD, or another block-device-like mount. Then repeat symlink cutover.
2. Move the dashboard/agent runtime that writes SQLite onto the laptop itself so SQLite writes are local to the external disk, and expose API back to VPS.
3. Keep hot DB local but immediately shard/offload cold/heavy tables to external storage and VACUUM/compaction only after a safe architecture is chosen.

Do not repeat sshfs symlink cutover for hot SQLite DB unless write semantics have been changed and tested with: create table, insert, concurrent open while dashboard is running, and service health.

