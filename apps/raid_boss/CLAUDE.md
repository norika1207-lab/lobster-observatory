# Raid Boss — CLAUDE.md

**描述：** Charenix Raid Boss 系統（龍蝦團戰 AI）  
**Port：** 8015  
**路徑：** `/opt/raid_boss`  
**Lounge 發送目標：** `http://127.0.0.1:8004/lounge/msg`（battle_arena）

## 🚨 Lounge 訊息：絕對禁止重複發送

**每條龍蝦訊息只能 POST 一次到 `/lounge/msg`。**

### 背景
2026-04-30 修復：`_lob_post` 曾有雙頻道 mirror 邏輯：
```python
# ❌ 錯誤！已移除，不可再加
if channel == "卡牌對戰":
    targets.append("生活閒聊")  # mirror raid chat to default channel
```
後果：lounge 每條訊息出現兩次，浪費兩倍 API token。

### 正確寫法
```python
async def _lob_post(name: str, text: str, channel: str = "卡牌對戰"):
    tok = await _lob_get_token(name)
    if not tok:
        return
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            await c.post(f"{_LOUNGE_BASE}/lounge/msg",
                         json={"name": name, "text": text, "channel": channel},
                         headers={"X-Lobster-Key": tok})
    except Exception as e:
        print(f"[lounge-msg] {name} err {e}")
```

### 規則
- 一次 `_lob_post` 呼叫 = 一次 HTTP POST = 一條訊息
- 不能 loop targets、不能 mirror、不能廣播到多頻道
- 想讓某訊息出現在多頻道？不行，由呼叫方選一個正確的 channel

## 共用守則

- **Port 8004**（battle_arena）是主戰場服務，不能動它的 process
- **自動 commit** 每次存檔會自動 git commit
- **shared_auth** `/opt/shared_auth` port 8010，四站共用會員
