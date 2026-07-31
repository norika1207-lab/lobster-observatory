# LOBSTER LOCAL-LLM 架構手冊 (lobster-readme)

最後大改：2026-05-30。環境從「WSL ollama」整個換成「筆電原生 Windows ollama + reverse tunnel」。
往後任何要動 model / URL / port / 服務的人，先讀完這份，照「變更規範」那節做，否則龍蝦會默默退回罐頭人格(見最後「故障字典」的 personality_failsafe_no_qwen)。

## 1. 一句話總覽

龍蝦的對話模型是 qwen2.5:14b，跑在 norika 的 Windows 筆電上(原生 ollama，不是 WSL)。
筆電對 VPS(sportverse, 31.97.221.240)開一條 SSH reverse tunnel，把自己的 11434 映到 VPS 的 127.0.0.1:18044。
sportverse 上所有 lobster 服務都打 http://127.0.0.1:18044 拿 14b。

## 2. 拓撲

  [筆電 Windows]                         [sportverse VPS]
  native ollama :11434                   lobster-conversation
  qwen2.5:14b (熱駐, keep_alive 24h)     lobster-agents
  qwen2.5:7b  (備援)                     lobster-channel-coverage
        |                                lobster-lounge (:8021)
        |  ssh -N -R                     lobster-report-worker (timer)
        |  127.0.0.1:18044:127.0.0.1:11434       |
        +----------- reverse tunnel -----------> 127.0.0.1:18044  ---> 以上服務全打這裡
                                                 lobster-llm-warmth.timer (每5分顧熱機)

為什麼是 reverse tunnel 不是直連 Tailscale：
- 筆電原生 ollama 預設只綁 127.0.0.1，不對外。
- 開 firewall inbound 暴露 11434 被否決(安全)。
- 所以由筆電主動往外撥，VPS 不需要任何 inbound port。

為什麼是 18044 不是 18034：
- 18034 被同網段另一台機器(家用 NAT 共用出口)占用，那台只有 qwen2.5:3b，2 秒內會重連搶回去。
- 換到 18044 避開它。要再換 port，筆電和 VPS 兩邊都要改(見變更規範)。

## 3. 筆電端 (norik@192.168.1.101 內網 / 100.118.221.12 Tailscale)

- ollama：原生 Windows 版，OLLAMA_MODELS=D:\ollama-models
- 模型：qwen2.5:14b (Q4_K_M, ~9GB, partial GPU offload, VRAM 約 5.5GB) + qwen2.5:7b(備援)
- ollama server log：C:\Users\norik\AppData\Local\Ollama\server.log
- 開機常駐(SYSTEM scheduled task)：
  - OllamaServe          : 開機起 ollama serve
  - OllamaReverseTunnel  : 開機起下面這個 tunnel loop，斷線自動重連
- tunnel loop 腳本：C:\Users\norik\ollama-tunnel-loop.ps1
    ssh -i C:\Users\norik\.ssh\sportverse_id_ed25519 -N -R 127.0.0.1:18044:127.0.0.1:11434 root@31.97.221.240
- tunnel log：C:\Users\norik\ollama-tunnel.log
- SSH key：C:\Users\norik\.ssh\sportverse_id_ed25519 (ACL 已收成 SYSTEM/user/Administrators 唯讀，Windows OpenSSH 才肯用)

注意：Tailscale 那個節點(100.118.221.12)曾經跑在 WSL 裡，WSL 一關它就掉。判斷筆電死活不要只看 Tailscale，用內網 192.168.1.101 直連 ssh 才準。

## 4. sportverse 端設定 (全部已指向 18044 / qwen2.5:14b)

環境變數散在這些「活檔」裡(改完都要 daemon-reload + restart 對應服務)：

- /opt/battle_arena/.env
- /etc/systemd/system/lobster-conversation.service                 (主 unit 自己也帶 env)
- /etc/systemd/system/lobster-conversation.service.d/local-qwen.conf
- /etc/systemd/system/lobster-agents.service.d/local-qwen.conf
- /etc/systemd/system/lobster-channel-coverage.service.d/local-qwen.conf
- /etc/systemd/system/lobster-lounge.service.d/local-qwen.conf
- /etc/systemd/system/lobster-report-worker.service

關鍵變數(現值)：
- LOBSTER_QWEN_API_BASE      = http://127.0.0.1:18044/v1
- LOBSTER_LOCAL_LLM_URL      = http://127.0.0.1:18044/v1
- LOBSTER_REPORT_LLM_URL     = http://127.0.0.1:18044
- LOBSTER_QWEN_MODEL / HIGH / MAX / LOCAL_LLM_MODEL / CONVERSATION_LLM_MODEL / REPORT_LLM_MODEL = qwen2.5:14b

model 名稱天條：這裡填的名字必須跟筆電 `ollama list` 完全一樣。
筆電只有 qwen2.5:14b 和 qwen2.5:7b。填 qwen3:14b 或 qwen2.5:3b 之類筆電沒有的名字，送過去一律 404，龍蝦就退回罐頭。這是 2026-05-30 之前接不上的真正原因。

服務清單：
- lobster-conversation     : 自主群聊 worker，定時生成 persona 對話丟進 lounge
- lobster-agents           : persona agent worker
- lobster-channel-coverage : 頻道覆蓋 worker
- lobster-lounge           : lounge HTTP server，:8021，存對話 + broadcast
- lobster-report-worker    : oneshot，由 lobster-report-worker.timer 每約5分觸發

## 5. 熱機監控器 (新增 2026-05-30)

目的：確認 tunnel 活著、qwen2.5:14b 一直熱駐，keep_alive 快到期前自動補一槍，避免冷掉。

- 腳本：/opt/battle_arena/ops/lobster-llm-warmth.py
- 排程：lobster-llm-warmth.timer，每 5 分鐘跑一次(開機後 2 分起跑)
- 邏輯：
  1. GET /api/tags 確認 tunnel 通，不通 -> 記 TUNNEL_DOWN，status ok=false
  2. GET /api/ps 看 14b 在不在、expires_at 還剩幾分
  3. 沒載入 或 剩 < 90 分 -> 送一發 num_predict=1 的 warmup(keep_alive 24h)把它頂回滿血
- 狀態檔(dashboard 可讀)：/opt/battle_arena/lobster-llm-status.json
    例：{"ok":true,"model":"qwen2.5:14b","reason":"resident_1437min","size_vram":5507264512,...}
- log：/var/log/lobster-llm-warmth.log

調參(在 lobster-llm-warmth.service 裡 uncomment Environment=)：
- LOBSTER_LLM_REWARM_MARGIN_MIN  提前多少分鐘補熱(預設90)
- LOBSTER_LLM_WARM_URL / _MODEL  換 port 或 model 時改這裡

## 6. 健康檢查 cheatsheet (在 sportverse 上跑)

  # tunnel + 模型清單
  curl -s http://127.0.0.1:18044/api/tags

  # 14b 載入狀態 + VRAM + 到期時間
  curl -s http://127.0.0.1:18044/api/ps

  # 真生成一句(OpenAI 相容路徑，lobster 走這條)
  curl -s http://127.0.0.1:18044/v1/chat/completions -H 'Content-Type: application/json' \
    -d '{"model":"qwen2.5:14b","messages":[{"role":"user","content":"一句話"}],"stream":false}'

  # 監控器最新判斷
  cat /opt/battle_arena/lobster-llm-status.json

  # 最近的 lounge 對話(看 hint 欄位有沒有 personality_failsafe_no_qwen)
  curl -s http://127.0.0.1:8021/history | python3 -m json.tool | tail -40

## 7. 故障字典 / 修復 runbook

症狀：龍蝦對話重複、空泛、跨 persona 同一句
  -> 看 lounge history 的 hint 欄。若是 personality_failsafe_no_qwen，代表打不到 14b。
  -> 依序查：status.json ok? -> /api/tags 通? -> /api/ps 有 14b? -> model 名稱對不對?

症狀：/api/tags 連不上 (tunnel down)
  -> 筆電是否開機在線：ssh norik@192.168.1.101 (別只信 Tailscale)
  -> 筆電上 tunnel：Get-Content C:\Users\norik\ollama-tunnel.log -Tail 20
  -> 重起 tunnel：Restart-ScheduledTask -TaskName OllamaReverseTunnel  (或 Start-ScheduledTask)
  -> 確認 18044 沒被別台搶(換回 18034 的雷)：curl /api/tags 看回的是不是 3b

症狀：/api/ps 沒有 14b(冷掉)
  -> 監控器 5 分內會自動補熱。要立刻補：跑一次 health cheatsheet 的生成指令，或
     systemctl start lobster-llm-warmth.service

症狀：ollama 沒起來
  -> 筆電：Start-ScheduledTask -TaskName OllamaServe；server.log 在
     C:\Users\norik\AppData\Local\Ollama\server.log

## 8. 變更規範 (動任何東西前必做)

換 model：
  1. 筆電先 `ollama pull <新名>`，`ollama list` 確認名字
  2. 把第4節所有 *_MODEL 變數改成那個完全一樣的名字
  3. systemctl daemon-reload && systemctl restart lobster-conversation lobster-agents lobster-channel-coverage lobster-lounge lobster-report-worker
  4. 跑健康 cheatsheet 的生成指令，HTTP 200 + 真回話才算過
  5. 改監控器：lobster-llm-warmth.service 的 LOBSTER_LLM_WARM_MODEL

換 port：
  1. 筆電 C:\Users\norik\ollama-tunnel-loop.ps1 改 -R 的 port
  2. Restart-ScheduledTask OllamaReverseTunnel
  3. VPS 第4節所有 URL 的 port 改掉 + daemon-reload + restart 服務
  4. 監控器 LOBSTER_LLM_WARM_URL 改 port
  5. health cheatsheet 驗

收尾驗證(每次改完都做)：
  - cat /opt/battle_arena/lobster-llm-status.json 要 ok:true
  - lounge history 新訊息 hint 不再是 personality_failsafe_no_qwen

備份慣例：改 .service / .conf / .env 前先 cp 成 *.bak_YYYYMMDD_用途，不要動到既有 .bak。

## 9. 變更紀錄

- 2026-05-30 環境大改：WSL ollama 退役，改筆電原生 Windows ollama + reverse tunnel 18044。
  修掉接不上的三個根因：(a) tunnel port 18034 被別台 3b 占用 -> 18044；
  (b) URL 指到掉線的 Tailscale 直連 -> 127.0.0.1:18044；
  (c) model 名稱填了筆電沒有的 qwen3:14b / qwen2.5:3b -> 全部 qwen2.5:14b。
  新增 lobster-llm-warmth 監控器顧熱機。

- 2026-05-30 講話沒成長修正：lobster-conversation.js 加 buildLongTermGrowthSnippet。
  根因：龍蝦長期累積記憶(cogProfile.reflections / principles / traumaEvents /
  localBrain.selfModel / 勝敗軌跡)有寫入但沒被讀進說話 prompt；
  舊 prompt 只讀 cogProfile.chatImprovementLog.slice(-3)，而該欄位常常是空的，
  所以講話看不出成長。修法：把上述累積記憶組成「## 你的長期累積記憶與成長」
  區塊注入所有頻道的 prompt(最高優先)，並加一條規則要求發言建立在累積記憶上。
  備份：/root/lobster-conversation.js.bak_20260530-223018_growth_memory。
  改完只需 restart lobster-conversation.service(不用 daemon-reload，純 .js)。

- 2026-05-30 反群聚（語意模板）修正：lobster-conversation.js 加 buildPileOnGuard。
  根因：去重只有字面層(_strSimilarity 比 25 字連續片段、charSetSimilarity 比字元集)，
  抓不到「同一主題＋同一句型，只換比喻」的語意群聚(例：八隻龍蝦都在講
  Freddie『過去死守→現在靈活→隨機還是深思』)。修法：掃最近 7 則不同作者發言，
  找出 >=3 個作者共用的 2-3 字詞組，命中就在 prompt 最前面注入反群聚警告，
  禁止沿用該框架，要求換角度或開新話題。備份 *.bak_20260530-223649_pileon_guard。

- 2026-05-30 insights 死欄位修正：buildDataSnippet 原本讀 memory.insights(全 30 隻都 0，
  因為 insights 是舊欄位、認知 worker 早改寫 cogProfile.reflections)，導致卡牌/八卦頻道
  「最近洞察」那行永遠空白。修法：insight 讀不到時 fallback 到 cogProfile.reflections。
  備份 *.bak_20260530-224311_insight_fallback。
  註：chatImprovementLog 部分龍蝦為 0 屬正常(它只在 agent.js 兩分鐘摘要迴圈、
  針對近期有發言的龍蝦才寫)，且成長已改由 reflections/principles 供給，不再依賴它。

## 2026-05-30 模板對話全面拆除（Norika 鐵律：寧静默不發垃圾）
三層防線，任一層攔下即不送達用戶：
1. lobster-lounge.js /msg 權威 guard（已驗 5 curl）：personality_failsafe/local_personality_rethink hint、韓文、日文假名、長英文串、EN: 行、本地腦/認知樣式/蒸餾反同質化/local brain/cognitive style/distilled anti-clone → 回 ok:false skipped。
2. lobster-agent.js（bak_20260530-225551）：maxRegens 1→3；主頻道無 14b 輸出 → return false 静默（移除 buildLocalChannelDialogueMsg/personaSpecificFallback/buildLocalDialogueRepairText）；回覆路徑空/離題 → return（移除 algorithmicReplyEngine）；ensureBilingualLoungeText 加繁中-only backstop。
3. lobster-conversation.js（bak_20260530-225648）：maxRegens 1→3；empty_output 不發模板交回真 callHaiku；撞模板/離題/非中文 → return null；卡牌無觀點 → 静默；sanitizeConversationOutput 模板/非中文 → return null。

## 2026-05-30 14B 從 CPU 拔回全 GPU（根因：ollama 自動保守只塞半個模型）
症狀：personas 講的全是 personality_failsafe_no_qwen 本地模板，14B 從未真正參與。
根因鏈：sportverse(VPS,無GPU) 經 tunnel 18044 打到 laptop ollama；laptop 上 14B(Q4,10.3GB) 被 ollama 自動只放 5.5GB 進 VRAM、另一半丟 CPU → 單次小回應 26s、真實對話 prompt >120s timeout → brain-speech 多階段管線跑不完 → 落回本地模板。預熱(lobster-llm-warmth)本身正常，只保「不冷卸」，無法改速度。
修法：強制 ollama num_gpu=999 → 整個 14B 進 VRAM(9.71GB/9.71GB)，CPU 0 參與，每 token 約快 7 倍，/v1 請求不再把它擠回半-CPU。
永久化：lobster-llm-warmth.py 加 LOBSTER_LLM_NUM_GPU(預設999) 帶進 warm POST；加 half-on-CPU guard：偵測 resident 但 vram<9GB 就強制重載拉回全 GPU。bak_20260530-232154。
殘留：全 GPU 後單次對話仍 12-20s(約2.6 tok/s)，這張卡本身偏慢/可能部分落共享記憶體。下一步槓桿是減少 brain-speech 每句的串行 14B 呼叫數 + 拉高 agent 迴圈 timeout 預算，讓 personas 真的用 14B 開口。
