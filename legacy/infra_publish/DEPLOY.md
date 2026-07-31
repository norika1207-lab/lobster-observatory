# DEPLOY：從這個 repo 重建龍蝦 server

這個 repo 是 sportverse 上龍蝦系統的乾淨備份（沒有任何祕密、沒有 dotfiles）。
照這份就能把整台重建回去。真正的祕密值只活在 server 的 `/etc` 跟環境裡，這裡只放
佔位符。

## repo 結構與檔案落點

| repo 路徑 | 部署到 | 說明 |
|---|---|---|
| `lobster-*.js` / `lobster-*.py` / `lobster_*.py` | `/root/` | 對話引擎、distill、所有 worker |
| `LOBSTER_*.md` | `/root/` | 各子系統 README |
| `systemd/lobster-*.service` / `*.timer` | `/etc/systemd/system/` | 96 個服務與排程 |
| `systemd/lobster-llm-warmth.service.d/zh-q2k.conf` | `/etc/systemd/system/lobster-llm-warmth.service.d/` | warmth VRAM 門檻 drop-in |
| `ops/lobster-llm-warmth.py` | `/opt/battle_arena/ops/` | 模型保溫腳本 |

## 部署步驟

1. 放程式碼

```bash
cp lobster-*.js lobster-*.py lobster_*.py LOBSTER_*.md /root/
mkdir -p /opt/battle_arena/ops
cp ops/lobster-llm-warmth.py /opt/battle_arena/ops/
```

2. 放 systemd unit（含 drop-in 目錄）

```bash
cp systemd/lobster-*.service systemd/lobster-*.timer /etc/systemd/system/
mkdir -p /etc/systemd/system/lobster-llm-warmth.service.d
cp systemd/lobster-llm-warmth.service.d/zh-q2k.conf /etc/systemd/system/lobster-llm-warmth.service.d/
```

3. 回填被 redact 的祕密（見下節）。

4. reload + 啟用

```bash
systemctl daemon-reload
systemctl enable --now lobster-conversation.service lobster-agents.service
systemctl enable --now lobster-distill.timer lobster-llm-warmth.timer
```

## 必須回填的 redacted 值

部署前一定要把這些佔位符換回真值，否則對話引擎連不上模型。

| 檔案 | 變數 | repo 裡的佔位符 | 真值來源 |
|---|---|---|---|
| `systemd/lobster-conversation.service` | `DASHSCOPE_API_KEY` | `__REDACTED__SET_VIA_DEPLOY__` | norika 的 DashScope/Qwen 雲端 key |
| `systemd/lobster-conversation.service` | `QWEN_API_KEY` | `__REDACTED__SET_VIA_DEPLOY__` | 同上 |

`lobster-stock.service` / `lobster-force-settle.service` 的 `SERVICE_KEY=` 本來就是空值，
不用填。

回填方式（不要把真值 commit 回這個 repo）：

```bash
sed -i 's#__REDACTED__SET_VIA_DEPLOY__#<真值>#' /etc/systemd/system/lobster-conversation.service
systemctl daemon-reload && systemctl restart lobster-conversation.service
```

## 模型與傳輸前置（重建新機才需要）

- 對話引擎走本地 14b：`lobster-zh-q2k:latest`（Qwen2.5-14B 動手術 + 繁中 QLoRA + Q2_K，5.57GB）。
- 透過反向 SSH tunnel 連到筆電的 ollama（`sportverse:18044` → laptop `11434`）。
- warmth 用 `LOBSTER_LLM_WARM_MODEL=lobster-zh-q2k:latest` 每 5 分鐘把模型重新釘住 +24h。
- drop-in `zh-q2k.conf` 把 `LOBSTER_LLM_FULL_VRAM_MIN` 降到 5e9，避免 5.57GB 模型被誤判成
  half-on-cpu 一直重載。

模型本身（GGUF 權重）不在這個 repo，太大。要嘛從筆電的 ollama 既有副本，要嘛重跑
Frankenstein 手術流程產生。

## 記憶與 distill pipeline

完整說明看 `LOBSTER_MEMORY_PIPELINE_README.md`。一句話：每 30 分鐘 `lobster-distill.timer`
把 31 隻龍蝦的經歷壓成行為訊號 + decisionHooks + growth，寫進 `lobster_distill.db` 跟
race-free 的 `/root/lobster-memory/_distilled/<persona>.json` sidecar，對話引擎讀 sidecar
逼出個體差異。DB 跟 sidecar 是權威，記憶檔主欄位只是相容回退。

## 不在這個 repo 的東西（重建時要另外處理）

- `/root/lobster-memory/`（每隻龍蝦的記憶 JSON）跟所有 `*.db`（battle_arena.db、
  lobster_distill.db 等）是資料，不是程式碼，要另外備份還原。
- GGUF 模型權重（見上節）。
- `.ssh` 私鑰、`KEYS.md`、各種 token：刻意不在這裡，照 server 原本的安全管道補。
- deploy key：這個 repo 用 `/root/.ssh/lobster_infra_deploy` 推。換機要重生 key 並在
  GitHub repo 的 Deploy keys 重新加（勾 Allow write access）。

## 維護規則

- redacted 欄位永遠不要把真值 commit 回來。改 unit 先確認沒帶祕密再 push。
- 推這個 repo 一律走 deploy key，不要用 origin 混到別的 repo。
- 程式碼有改先在 server 上 `node --check` / `python3 -m py_compile` 過再 commit。
