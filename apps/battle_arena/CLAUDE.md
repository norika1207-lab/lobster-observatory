# Charenix — CLAUDE.md

**描述：** 跨運動卡牌對戰  
**Port：** 8004  
**Slug：** `charenix`  
**路徑：** `/opt/battle_arena`

## ⚡ 斷線重連流程

**Claude 斷線或忘記背景時，立刻執行：**
```bash
bash /opt/dev_tracker/resume.sh charenix
```
把輸出全選貼進新視窗第一句話，Claude 立刻接回上下文。


## 共用守則

- **Port 8000** 是股市交易所，絕對不能動
- **共用模組** 修改前必須先複製到 `/opt/sandbox/` 開發測試，確認後才 cp 回 production
- **共用前端** `/opt/ragnorok_ui/index.js` 各站唯一入口
- **自動 commit** 每次存檔會自動 git commit，不用手動
- **shared_auth** `/opt/shared_auth` port 8010，四站共用會員

## 專案特定規則

### 🚨 Lounge 訊息：絕對禁止重複發送

**每條龍蝦訊息只能 POST 一次到 `/lounge/msg`。**

#### 背景
2026-04-30 發現 `raid_boss/app/main.py` 的 `_lob_post` 函數曾有這段邏輯：
```python
# ❌ 錯誤！這會讓每條訊息在 lounge 出現兩次
if channel == "卡牌對戰":
    targets.append("生活閒聊")  # mirror raid chat to default channel
```
Lounge 顯示全頻道，同一則訊息發到兩個頻道 = 畫面重複兩條。
每次龍蝦對話都白白消耗兩倍 API token。

#### 規則
- `_lob_post` 只呼叫一次 `POST /lounge/msg`，channel 由呼叫方決定
- 不能在後端做任何「mirror 到其他頻道」的邏輯
- 前端 `addMsg` 有 `_seenMsgKeys` dedup 保險，但根本解法在後端
- 新增任何 lounge 發送邏輯前，先確認不會重複發同一則內容
