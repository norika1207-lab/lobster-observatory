# 龍蝦記憶 → 蒸餾 → 成長 → 說話 pipeline（給未來每個 AI 看的）

最後更新：2026-06-01（norika + Claude）

這份是 lobster 認知/記憶系統的單一事實來源。動這條鏈上任何檔案前先讀完。
之前出過事：有人把 distill 的 persona 名單寫死成 10 個，結果 31 個 agent 裡 19 個
卡在 stub，永遠不會長出真實人格訊號。這份就是要擋掉這種「進來亂改」。

## 一句話

每個龍蝦的原始經歷（events / cognitive / memory）會被 lobster-distill.py 壓成 10 個
0..1 的行為訊號 + 5 條 decisionHooks + 一個 growth（跟上一輪比的位移），寫進 DB 與
記憶檔，最後 lobster-conversation.js 在生成每句話前讀這些訊號，逼每隻龍蝦講得不一樣。

## 資料流（誰讀誰寫）

```
lobster_experience_events / lobster_cognitive_* / lobster-memory/<persona>.json
        │  (原始經歷、認知快照、記憶)
        ▼
lobster-distill.py   每 30 分鐘由 lobster-distill.timer 跑
        │  算 10 訊號 + confidence + decisionHooks + growth(跟上輪 delta)
        ▼
        ├─► lobster_distill.db   ← 權威儲存（race-free）
        │     lobster_distilled_signals / lobster_decision_hooks / lobster_distillation_runs
        │
        ├─► lobster-memory/_distilled/<persona>.json  ← 給 JS 讀的單寫者 sidecar（race-free）
        │     【只有 lobster-distill.py 會寫這個資料夾，所以不會被洗掉】
        │
        └─► lobster-memory/<persona>.json 的 distilledBrain 欄位  ← 舊的相容快取（會被 JS 洗）
        ▼
lobster-conversation.js  buildDistilledConversationSnippet()   ← loadDistilledBrain() 先讀 sidecar
lobster-agent.js         distilledSignals()/distilledHook()    ← loadDistilledBrain() 先讀 sidecar
        │  讀 signals + topic_discussion hook（sidecar 沒有才回退讀記憶檔的 distilledBrain）
        ▼
   送進 14b (lobster-zh-q2k) 的 system prompt → 反同質化、逼出個體差異
```

## 10 個訊號（全部 0..1）

risk_discipline, impulse_control, social_absorption, compliance_resistance,
pressure_reactivity, learning_velocity, collaboration_stability, output_momentum,
metacognitive_stability, language_distinctiveness

這些是「行為先驗」，不是固定人格事實。每輪重算。

## growth 區塊（2026-06-01 新增，這就是 norika 要的「增長依據」）

每次蒸餾會跟『上一輪同一隻龍蝦的訊號』比，算出：

- deltas：每個訊號的位移
- movers：位移最大的前 5 個（|delta| >= 0.02 才算）
- learningVelocityTrend：rising / falling / steady
- netMovement：所有位移絕對值總和（這一輪整體變動量）

第一次跑某隻龍蝦時 firstRun=true，當基準線。
growth 同時寫進 lobster_distill.db 的 full_json 和記憶檔的 distilledBrain.growth。
這讓「成長」變成可被讀取、可被後續決策參考的依據，而不是一堆 raw log。

## roster 單一事實來源（最重要的一條，別再寫死）

lobster-distill.py 的 discover_personas() 在執行時動態組出名單：

1. ROSTER_FLOOR：20 隻一定要蒸餾的（mentor 10 + apprentice 10），就算 0 events 也跑
2. lobster_experience_events 裡所有 DISTINCT persona（真正在動的 agent，含未來新增的）
3. lobster-memory/*.json 的檔名（去掉 .bak）
4. 減掉 ROSTER_DENY = {system, deleted-lobsters, norika, default, test}（非 agent 的檔）

結果：現在 31 隻全蒸餾，未來任何新 agent 只要開始產 events 或有記憶檔，
下一輪自動納入。【不要再把名單改回寫死的固定陣列】，那是 2026-06-01 的 bug。

## 並發與鎖的真相（degrade 模式，要懂）

lobster_memory_io.py 提供 fcntl 檔案鎖：

- safe_write_json：只在『寫』時上鎖
- safe_read_modify_write：『讀+改+寫』整段上鎖（要做 read-modify-write 一律用這個）

distill.py 已改用 safe_read_modify_write 寫 distilledBrain（2026-06-01 修），
所以 Python worker 之間不會互相蓋掉。

但有個跨語言缺口要知道：fcntl 鎖只在 Python 之間有效。JS worker
（lobster-channel-coverage-worker.js、lobster-conversation.js 等）用 fs.writeFile，
不認這個鎖。所以 JS worker 偶爾會在 distill 寫完後幾秒內，拿它 distill 前讀到的舊副本
覆寫記憶檔，把 distilledBrain 洗掉（basaltrook 就中過）。

已經根治（2026-06-01）：用單寫者 sidecar `lobster-memory/_distilled/<persona>.json`。
只有 lobster-distill.py 會寫這個資料夾，沒有任何別的 worker 碰它，所以不會被洗。
兩個說話消費端（conversation.js、lobster-agent.js）都用 loadDistilledBrain() 先讀
sidecar，sidecar 沒有才回退讀記憶檔的 distilledBrain。記憶檔主欄位被 JS 洗掉已經
不再影響說話品質了。

為什麼這樣就夠（不用去改 30+ 個 JS writer）：
1. lobster_distill.db 才是權威，DB 裡 31 隻永遠正確完整，沒有 race
2. sidecar 是單寫者，永遠跟 DB 一致，說話端讀它
3. lobster-distill.timer 每 30 分鐘重跑，sidecar 跟著更新
4. 記憶檔主欄位的 distilledBrain 變成純相容回退，被洗也沒差
5. 沒去碰 30+ 個正在跑的 JS service，零風險

驗證過（2026-06-01）：把記憶檔 distilledBrain 設成空 {} 模擬被洗，loadDistilledBrain
仍從 sidecar 拿到 basaltrook 的 risk=0.71 + growth；sidecar 不存在時正確回退記憶檔。

## 手動跑 / 驗證

```bash
# 手動跑一次（會寫 DB + 記憶檔）
cd /root && python3 lobster-distill.py
# 預期輸出：{"ok": true, "personas": 31, "errors": 0, ...}

# 看 DB 權威資料（不受 race 影響）
sqlite3 /opt/battle_arena/app/lobster_distill.db \
  "SELECT persona, round(learning_velocity,3), updated_at FROM lobster_distilled_signals ORDER BY persona"

# 看某隻的 growth
python3 -c "import json;d=json.load(open('/root/lobster-memory/clawtrix.json'));print(json.dumps(d['distilledBrain']['growth'],ensure_ascii=False,indent=1))"

# 看 timer
systemctl list-timers lobster-distill.timer
```

## DO-NOT（給未來 AI 的紅線）

1. 不要把 distill.py 的 roster 改回寫死的固定名單。用 discover_personas()。
2. 不要把 distilledBrain 的記憶檔當權威。權威是 lobster_distill.db。
3. 不要在 distill.py 用 load_memory()+save_memory() 做 read-modify-write，
   一律用 safe_read_modify_write（否則 race 洗檔）。
4. 不要為了「補滿」就在記憶裡塞 LLM 生成的自然語言心得而沒有人工複審。
   norika 規則：寧静默也不要 AI 套話 / 模板 / 機器感文字。
5. 不要刪 lobster-cognitive-worker.py 的 distilledBrain stub（它是 distill 還沒跑到時的
   保底，有 `if not memory.get('distilledBrain')` 守著，不會蓋掉真資料）。
6. 改任何一個檔先 .bak 備份（這條鏈上所有改動都有 .bak_<時間>_<標籤>）。
7. `lobster-memory/_distilled/` 是 lobster-distill.py 的單寫者 sidecar。
   【不要讓任何其他 worker 寫這個資料夾】，它 race-free 的前提就是只有一個寫者。
   說話端要拿蒸餾訊號一律走 loadDistilledBrain()，不要直接讀 memory.distilledBrain。

## 2026-06-01 這次改了什麼（含 backup 標籤）

- lobster-distill.py
  - .bak_*_growth_roster：動態 roster + growth-delta + main() 每隻 try/except
  - .bak_*_atomic_mem：記憶檔改用 safe_read_modify_write（修 Python 端 race）
  - .bak_*_sidecar：write_persona 多寫一份單寫者 sidecar `_distilled/<persona>.json`
- lobster-cognitive-worker.py
  - .bak_*_stubmsg：stub 文案從「待 lobster-report-worker 生成」改成正確的
    「待 lobster-distill.py 生成」
- lobster-conversation.js
  - .bak_*_sidecar：loadMemory 蓋上 __persona；新增 loadDistilledBrain()；
    buildDistilledConversationSnippet 改讀 sidecar
- lobster-agent.js
  - .bak_*_sidecar：loadMemory 蓋上 __persona；新增 loadDistilledBrain()；
    distilledSignals/distilledHook 改讀 sidecar
- 結果：31 隻全蒸餾、0 error、每隻有 growth；DB + sidecar 31 隻永遠完整且 race-free；
  說話端讀 sidecar，記憶檔主欄位被 JS 洗掉不再影響品質。
