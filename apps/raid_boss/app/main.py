"""
CardHouse v4.0 — FastAPI 入口
port 8004, 獨立服務

PvP 大廳 API:
  GET  /                         → lobby.html (大廳首頁)
  GET  /arena                    → arena.html (對戰畫面)
  GET  /api/room/list            → 所有開放房間
  POST /api/room/create          → 開新房間 (玩家 A)
  POST /api/room/{id}/join       → 加入房間 (玩家 B)
  GET  /api/leaderboard          → 排行榜 (?mode=daily|alltime)
  POST /api/room/{id}/spectate_bet → 旁觀下注

對戰 API:
  GET  /api/health
  GET  /api/cards
  POST /api/match/start
  GET  /api/match/{id}
  POST /api/match/{id}/swap / play / pass / ai_b
"""
from fastapi import FastAPI, HTTPException, UploadFile, File, Query, Depends, Request, Header
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pathlib import Path
from datetime import datetime
from collections import defaultdict
import secrets, time, asyncio, random, json, math
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
import sys, os
import httpx
from jose import jwt, JWTError

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cards import get_all_cards, get_card_by_id, get_seasons_for_sport, filter_cards
import battle_engine as be
import db as DB
from xp_client import record_xp as _xp_record
from card_client import record_battle_win as _record_battle_win
import raid_engine as RE

SHARED_AUTH = "http://127.0.0.1:8010"
JWT_SECRET = os.environ.get("JWT_SECRET")
if not JWT_SECRET:
    raise RuntimeError("JWT_SECRET environment variable must be set")
_SVC_KEY = os.environ.get("SERVICE_KEY")
if not _SVC_KEY:
    raise RuntimeError("SERVICE_KEY environment variable must be set")
JWT_ALG = "HS256"
_bearer = HTTPBearer(auto_error=False)

_rate_buckets: dict = defaultdict(list)
_rate_last_clean: float = time.time()

def _real_ip(request: Request) -> str:
    return (request.headers.get("X-Real-IP")
            or request.headers.get("X-Forwarded-For", "").split(",")[0]
            or (request.client.host if request.client else "unknown")).strip()

def _check_rate(ip: str, limit: int = 10, window: int = 60):
    global _rate_last_clean
    now = time.time()
    if now - _rate_last_clean > 300:
        stale = [k for k, v in list(_rate_buckets.items())
                 if not any(now - t < window for t in v)]
        for k in stale:
            del _rate_buckets[k]
        _rate_last_clean = now
    _rate_buckets[ip] = [t for t in _rate_buckets[ip] if now - t < window]
    if len(_rate_buckets[ip]) >= limit:
        raise HTTPException(429, "Too many attempts")
    _rate_buckets[ip].append(now)

async def get_current_user(creds: HTTPAuthorizationCredentials = Depends(_bearer)):
    if not creds:
        raise HTTPException(401, "Not authenticated")
    try:
        payload = jwt.decode(creds.credentials, JWT_SECRET, algorithms=[JWT_ALG])
        return {"username": payload.get("sub")}
    except JWTError:
        raise HTTPException(401, "Invalid token")


def _assert_match_player(match_id: str, side: str, username: str):
    """確保 username 是此 match 的對應側玩家 — 禁止對他人的 match 動手腳"""
    if side not in ("a", "b"):
        raise HTTPException(400, "side must be 'a' or 'b'")
    try:
        m = be.get_match(match_id)
    except be.MatchError as e:
        raise HTTPException(404, str(e))
    player = m.player_a if side == "a" else m.player_b
    # Exact username match
    if player == username:
        return m
    # AI placeholder slots are system-owned, not user-controlled
    # Only allow if the slot was set by the server (starts with "AI·" — assigned in _spawn_ai_room)
    if player.startswith("AI·"):
        return m
    # Legacy placeholder strings (pre-join state)
    if player in ("Opponent", "等待挑戰者..."):
        return m
    raise HTTPException(403, "Not your side of this match")

ROOMS: Dict[str, dict] = {}
AI_ROOMS: Dict[str, dict] = {}
MIN_AI_ROOMS = 3

FAKE_HUMAN_NAMES = [
    "Tyler_V","Reeve88","MarcoDark","SaraBlaze","OliNight","KyraNova",
    "JaxRider","ElenaX","FinnRogue","CassieQ","DmitriKO","LunaEdge",
    "AshKnight","ZaraVex","RavenStrike","TomasBlood","MilaShade","NickFrost",
    "IvyWrath","GarethX","SiennaByte","KobeStorm","Yuki99","TreyHex",
    "NinaVoid","AlexCypher","BraxSlayer","CeceRift","DanoPulse","EsmeKill",
]


app = FastAPI(
    title="Charenix",
    description="Cross-era, cross-sport legend battle platform. Place bets on dream matchups (Ohtani vs MJ, etc.) with role-based card combat.",
    version="5.0.0",
)

app.mount("/static/cards/f1", StaticFiles(directory="/opt/racing/static/assets/drivers"), name="f1-cards")
app.mount("/static/cards/esports", StaticFiles(directory="/opt/esports/static/players"), name="esports-cards")
app.mount("/static", StaticFiles(directory="/opt/battle_arena/static"), name="static")
app.mount("/memecat/static", StaticFiles(directory="/opt/candycrash/static"), name="memecat-static")


class StartMatchRequest(BaseModel):
    player_a: str = Field(default="You", max_length=50)
    player_b: str = Field(default="Opponent", max_length=50)
    bet:      int = Field(default=500, ge=0, le=1_000_000)
    seed:     Optional[int] = None


class SwapRequest(BaseModel):
    side:     str = Field(max_length=2)
    card_ids: List[str] = Field(max_length=20)  # max 20 card IDs per swap


class PlayRequest(BaseModel):
    side:         str = Field(max_length=2)
    attacker:     Optional[str] = Field(default=None, max_length=100)
    buffer:       Optional[str] = Field(default=None, max_length=100)
    debuffer:     Optional[str] = Field(default=None, max_length=100)
    raise_amount: int = Field(default=0, ge=0, le=1_000_000)


class PassRequest(BaseModel):
    side: str = Field(max_length=2)


class CreateRoomRequest(BaseModel):
    creator: str = Field(default="Player", max_length=50)
    bet:     int = Field(default=500, ge=0, le=1_000_000)
    seed:    Optional[int] = None


class JoinRoomRequest(BaseModel):
    challenger: str = Field(default="Challenger", max_length=50)


class SpectatorBetRequest(BaseModel):
    bettor:  str = Field(max_length=50)
    on_side: str = Field(max_length=2)   # "a" or "b"
    amount:  int = Field(ge=10, le=1_000_000)

class LoginRequest(BaseModel):
    username: str = Field(max_length=50)
    password: str = Field(default="", max_length=200)

class RegisterRequest(BaseModel):
    username: str = Field(min_length=1, max_length=50)
    email:    str = Field(max_length=200)
    password: str = Field(max_length=200)

class DiamondBuyRequest(BaseModel):
    quantity: int = Field(ge=1, le=10_000)

class DiamondExchangeRequest(BaseModel):
    diamonds: int = Field(ge=1, le=10_000)

_NC = {"Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
       "Pragma": "no-cache", "Expires": "0"}


# ── AUTH PROXY ──
@app.post("/api/token")
async def login(data: LoginRequest, request: Request):
    _check_rate(_real_ip(request))
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(f"{SHARED_AUTH}/api/token",
                data={"username": data.username, "password": data.password}, timeout=10)
        except httpx.ConnectError:
            raise HTTPException(503, "會員服務不可用")
    if resp.status_code != 200:
        detail = "帳號或密碼錯誤"
        try: detail = resp.json().get("detail", detail)
        except: pass
        raise HTTPException(resp.status_code, detail)
    result = resp.json()
    token = result.get("access_token")
    username = result.get("username")
    # get Pit balance
    pit = 0
    try:
        async with httpx.AsyncClient() as c:
            br = await c.get(f"{SHARED_AUTH}/api/wallet", headers={"Authorization": f"Bearer {token}"}, timeout=5)
            if br.status_code == 200:
                pit = br.json().get("pit", 0)
    except: pass
    return {"access_token": token, "token_type": "bearer", "username": username, "balance": pit, "currency": "Pit"}

@app.post("/api/register")
async def register(data: RegisterRequest, request: Request):
    _check_rate(_real_ip(request))
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(f"{SHARED_AUTH}/api/register",
                json={"username": data.username, "email": data.email, "password": data.password}, timeout=10)
        except httpx.ConnectError:
            raise HTTPException(503, "會員服務不可用")
    if resp.status_code != 200:
        detail = "註冊失敗"
        try: detail = resp.json().get("detail", detail)
        except: pass
        raise HTTPException(resp.status_code, detail)
    return resp.json()

@app.get("/api/my/profile")
async def my_profile(creds: HTTPAuthorizationCredentials = Depends(_bearer)):
    if not creds:
        raise HTTPException(401, "Not authenticated")
    try:
        payload = jwt.decode(creds.credentials, JWT_SECRET, algorithms=[JWT_ALG])
        username = payload.get("sub")
    except JWTError:
        raise HTTPException(401, "Invalid token")
    stats = DB.get_player_stats(username)
    xp_data = DB.get_player_xp(username)
    pit = 0
    try:
        async with httpx.AsyncClient() as c:
            br = await c.get(f"{SHARED_AUTH}/api/wallet",
                headers={"Authorization": f"Bearer {creds.credentials}"}, timeout=5)
            if br.status_code == 200:
                pit = br.json().get("pit", 0)
    except: pass
    # Map level to title using titles.py
    from titles import TITLES
    lv = xp_data["level"]
    tier_idx = min(lv - 1, len(TITLES) - 1)
    title_info = TITLES[tier_idx]
    return {"username": username, "balance": pit, "currency": "Pit",
            "wins": stats["wins"], "losses": stats["losses"],
            "total": stats["total"], "win_rate": stats["win_rate"],
            "total_xp": xp_data["total_xp"], "level": lv,
            "xp_in_level": xp_data["xp_in_level"], "xp_to_next": xp_data["xp_to_next"],
            "progress_pct": xp_data["progress_pct"], "streak": xp_data["streak"],
            "title": title_info["name"], "title_en": title_info["en"]}

@app.get("/api/my/stats")
async def my_stats(user=Depends(get_current_user)):
    username = user["username"]
    stats = DB.get_player_stats(username)
    return stats


@app.get("/api/my/bets")
async def my_spectator_bets(user=Depends(get_current_user)):
    bets = DB.get_spectator_bets(user["username"])
    return {"bets": bets, "total": len(bets)}


# ── XP PROXY (forwards to shared_auth 8010) ──

async def _xp_proxy(method: str, path: str, auth: Optional[str] = None, params: dict | None = None):
    headers = {"Authorization": auth} if auth else {}
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.request(method, f"{SHARED_AUTH}{path}", headers=headers, params=params)
    except Exception as e:
        raise HTTPException(502, f"shared_auth unreachable: {e}")
    if r.status_code >= 400:
        detail = r.json().get("detail", "error") if r.content else "error"
        raise HTTPException(r.status_code, detail)
    return r.json()


@app.get("/api/xp/profile")
async def xp_profile_proxy(authorization: str | None = Header(None)):
    return await _xp_proxy("GET", "/api/xp/profile", authorization)


@app.get("/api/xp/history")
async def xp_history_proxy(limit: int = Query(50, ge=1, le=200),
                           authorization: str | None = Header(None)):
    return await _xp_proxy("GET", "/api/xp/history", authorization, {"limit": limit})


@app.get("/api/xp/leaderboard")
async def xp_leaderboard_proxy(limit: int = Query(20, ge=1, le=100)):
    return await _xp_proxy("GET", "/api/xp/leaderboard", None, {"limit": limit})


@app.get("/api/xp/curve")
async def xp_curve_proxy():
    return await _xp_proxy("GET", "/api/xp/curve", None)


@app.get("/api/collection/cards")
async def collection_cards_proxy(site: str | None = Query(None), authorization: str | None = Header(None)):
    params = {"site": site} if site else None
    return await _xp_proxy("GET", "/api/collection/cards", authorization, params)


@app.get("/api/collection/card/{card_id:path}")
async def collection_card_proxy(card_id: str, authorization: str | None = Header(None)):
    return await _xp_proxy("GET", f"/api/collection/card/{card_id}", authorization)


# ── DIAMOND ENDPOINTS ──
_PIT_PER_DIAMOND = 1000
_VALID_QTY = {10, 50, 100, 200, 500, 1000}

async def _get_auth_header(creds: HTTPAuthorizationCredentials = Depends(_bearer)) -> str:
    if not creds:
        raise HTTPException(401, "Not authenticated")
    return f"Bearer {creds.credentials}"

@app.post("/api/diamonds/buy")
async def buy_diamonds(req: DiamondBuyRequest, user=Depends(get_current_user),
                       creds: HTTPAuthorizationCredentials = Depends(_bearer)):
    username = user["username"]
    if req.quantity not in _VALID_QTY:
        raise HTTPException(400, f"數量需為 {sorted(_VALID_QTY)} 之一")
    pit_cost = req.quantity * _PIT_PER_DIAMOND
    auth = f"Bearer {creds.credentials}"
    # Check balance
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            br = await c.get(f"{SHARED_AUTH}/api/wallet", headers={"Authorization": auth})
            pit = br.json().get("pit", 0) if br.status_code == 200 else 0
    except:
        raise HTTPException(503, "無法取得餘額")
    if pit < pit_cost:
        raise HTTPException(400, f"Pit 不足，需要 {pit_cost:,}，現有 {pit:,}")
    svc_headers = {"X-Service-Key": _SVC_KEY}
    # Debit Pit
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"{SHARED_AUTH}/api/service/pit/debit",
                json={"username": username, "amount": pit_cost,
                      "reason": "charenix_diamond", "ref_id": f"ch_diamond_{req.quantity}"},
                headers=svc_headers)
            if r.status_code != 200:
                raise HTTPException(400, r.json().get("detail", "扣款失敗"))
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(503, str(e))
    # Credit diamonds
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"{SHARED_AUTH}/api/diamond/buy",
                json={"usd": req.quantity}, headers={"Authorization": auth})
            if r.status_code != 200:
                async with httpx.AsyncClient(timeout=10) as rc:
                    await rc.post(f"{SHARED_AUTH}/api/service/pit/credit",
                        json={"username": username, "amount": pit_cost,
                              "reason": "charenix_diamond_refund", "ref_id": f"ch_refund_{req.quantity}"},
                        headers=svc_headers)
                raise HTTPException(500, "Diamond 記帳失敗，已退款")
            result = r.json()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(503, str(e))
    return {"ok": True, "diamonds_bought": req.quantity, "pit_spent": pit_cost,
            "total_diamonds": result.get("total_diamonds", 0)}


@app.post("/api/diamonds/exchange")
async def exchange_diamonds(req: DiamondExchangeRequest, user=Depends(get_current_user),
                            creds: HTTPAuthorizationCredentials = Depends(_bearer)):
    if req.diamonds <= 0:
        raise HTTPException(400, "數量需 > 0")
    auth = f"Bearer {creds.credentials}"
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"{SHARED_AUTH}/api/diamond/exchange",
                json={"diamonds": req.diamonds}, headers={"Authorization": auth})
            if r.status_code != 200:
                raise HTTPException(r.status_code, r.json().get("detail", "兌換失敗"))
            result = r.json()
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(503, str(e))
    pit_received = result.get("pit_received", req.diamonds * _PIT_PER_DIAMOND)
    return {"ok": True, "diamonds_exchanged": req.diamonds, "pit_received": pit_received,
            "total_diamonds": result.get("total_diamonds", 0)}


@app.get("/api/diamonds/balance")
async def diamond_balance(user=Depends(get_current_user),
                          creds: HTTPAuthorizationCredentials = Depends(_bearer)):
    auth = f"Bearer {creds.credentials}"
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(f"{SHARED_AUTH}/api/diamond/tokens", headers={"Authorization": auth})
            tokens = r.json() if r.status_code == 200 else []
        total = sum(t.get("amount", 0) for t in tokens)
    except:
        total = 0
    return {"diamonds": total}


@app.get("/api/prize_pool")
async def prize_pool():
    """Charenix 賽季獎池（從旁觀下注 5% 手續費累積）"""
    try:
        with __import__('sqlite3').connect(DB.DB_PATH) as c:
            c.row_factory = __import__('sqlite3').Row
            row = c.execute("SELECT COALESCE(SUM(amount*0.05),0) pool, COUNT(*) cnt FROM spectator_bets WHERE settled=1").fetchone()
            return {"pool": round(row["pool"], 2), "currency": "Pit",
                    "site": "charenix", "contributions": row["cnt"]}
    except:
        return {"pool": 0, "currency": "Pit", "site": "charenix", "contributions": 0}


@app.get("/")
async def root():
    return FileResponse("/opt/battle_arena/static/lobby.html", headers=_NC)


@app.get("/arena")
async def arena():
    return FileResponse("/opt/battle_arena/static/arena.html", headers=_NC)


@app.get("/stats")
async def stats():
    return FileResponse("/opt/battle_arena/static/stats.html", headers=_NC)


@app.get("/memecat")
async def memecat():
    return FileResponse("/opt/battle_arena/static/arcade/index.html", headers=_NC)


def _room_public(room: dict) -> dict:
    stats_a = DB.get_player_stats(room["creator"])
    stats_b = DB.get_player_stats(room.get("challenger", "")) if room.get("challenger") else None
    return {
        "id": room["id"], "creator": room["creator"],
        "challenger": room.get("challenger"),
        "bet": room["bet"], "status": room["status"],
        "match_id": room["match_id"],
        "created_at": room["created_at"],
        "creator_win_rate": stats_a["win_rate"],
        "creator_total": stats_a["total"],
        "creator_odds": DB.calc_odds(stats_a["win_rate"]),
        "challenger_win_rate": stats_b["win_rate"] if stats_b else None,
        "challenger_odds": DB.calc_odds(stats_b["win_rate"]) if stats_b else None,
    }


@app.get("/api/room/list")
async def api_room_list():
    rooms = [_room_public(r) for r in ROOMS.values() if r["status"] in ("waiting", "active")]
    rooms.sort(key=lambda r: r["created_at"], reverse=True)
    return JSONResponse({"rooms": rooms})


@app.post("/api/room/create")
async def api_room_create(req: CreateRoomRequest, user=Depends(get_current_user), request: Request = None):
    if request is not None:
        _check_rate(f"room_create:{user['username']}", limit=5, window=60)
    # 每位使用者最多同時 3 個 waiting room — 防記憶體 flood
    active_rooms = sum(1 for r in ROOMS.values()
                       if r.get("creator") == user["username"] and r.get("status") == "waiting")
    if active_rooms >= 3:
        raise HTTPException(429, "You already have 3 waiting rooms — close or join one first")
    bet = max(100, min(50000, req.bet))
    m = be.start_match(user["username"], "等待挑戰者...", bet, req.seed)
    room_id = secrets.token_hex(5)
    ROOMS[room_id] = {
        "id": room_id, "creator": user["username"],
        "challenger": None, "bet": bet,
        "status": "waiting", "match_id": m.id,
        "created_at": int(time.time()),
    }
    return JSONResponse({"room_id": room_id, "match_id": m.id, "side": "a", "bet": bet})


@app.post("/api/room/{room_id}/join")
async def api_room_join(room_id: str, req: JoinRoomRequest, user=Depends(get_current_user)):
    room = ROOMS.get(room_id)
    if not room:
        raise HTTPException(404, "房間不存在")
    if room["status"] != "waiting":
        raise HTTPException(400, "房間已開始或已結束")
    if room.get("creator") == user["username"]:
        raise HTTPException(400, "Cannot join your own room")
    room["challenger"] = user["username"]
    room["status"] = "active"
    m = be.get_match(room["match_id"])
    m.player_b = user["username"]
    return JSONResponse({"room_id": room_id, "match_id": room["match_id"],
                         "side": "b", "bet": room["bet"],
                         "is_bot_room": bool(room.get("is_bot")),
                         "bot_strategy": room.get("bot_strategy")})


@app.get("/api/leaderboard")
async def api_leaderboard(
    request: Request,
    mode:  str = Query("alltime", pattern="^(alltime|daily)$"),
    limit: int = Query(20, ge=1, le=100),
):
    _check_rate(_real_ip(request), limit=60, window=60)
    data = DB.get_leaderboard(limit=limit, mode=mode)
    return JSONResponse({"mode": mode, "leaderboard": data})


@app.post("/api/room/{room_id}/spectate_bet")
async def api_spectate_bet(room_id: str, req: SpectatorBetRequest, user=Depends(get_current_user)):
    if req.bettor != user["username"]:
        raise HTTPException(403, "bettor mismatch")
    room = ROOMS.get(room_id)
    if not room:
        raise HTTPException(404, "房間不存在")
    if room["status"] not in ("waiting", "active"):
        raise HTTPException(400, "對戰已結束，不可下注")
    if req.on_side not in ("a", "b"):
        raise HTTPException(400, "on_side 必須是 'a' 或 'b'")
    if req.amount < 10 or req.amount > 1_000_000:
        raise HTTPException(400, "旁觀下注需介於 10–1,000,000 Pit")
    if req.bettor in (room.get("creator"), room.get("challenger")):
        raise HTTPException(403, "玩家本人不可下注自己的比賽")
    player = room["creator"] if req.on_side == "a" else room.get("challenger", "")
    stats = DB.get_player_stats(player)
    odds = DB.calc_odds(stats["win_rate"])
    bet_id = DB.save_spectator_bet(room["match_id"], req.bettor, req.on_side, req.amount, odds)
    return JSONResponse({
        "bet_id": bet_id, "on_side": req.on_side,
        "player": player, "amount": req.amount,
        "odds": odds, "potential_payout": int(req.amount * odds),
    })


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "service": "charenix",
        "version": "3.0.0",
        "port": 8004,
        "rules": {
            "hand_size": be.HAND_SIZE,
            "max_swap_per_round": be.MAX_SWAP_PER_ROUND,
            "max_rounds": be.MAX_ROUNDS,
            "initial_hp": be.INITIAL_HP,
            "initial_mp": be.INITIAL_MP,
            "mp_per_round": be.MP_PER_ROUND,
            "legendary_rounds_per_match": be.LEGENDARY_ROUNDS_PER_MATCH,
            "max_legendary_stack": be.MAX_LEGENDARY_STACK,
            "hp_damage_ratio": be.HP_DAMAGE_RATIO,
            "pass_shield_def": be.PASS_SHIELD_DEF,
            "pass_mp_bonus": be.PASS_MP_BONUS,
            "turn_timeout": be.TURN_TIMEOUT_SECONDS,
        },
    }


@app.get("/api/cards")
async def api_cards(sport: Optional[str] = None,
                    season: Optional[int] = None,
                    era: Optional[str] = None):
    cards = filter_cards(sport=sport, season=season, era=era)
    return JSONResponse({
        "cards": cards,
        "count": len(cards),
        "filters": {"sport": sport, "season": season, "era": era},
    })


@app.get("/api/cards/seasons")
async def api_seasons(sport: str = Query(..., description="F1 | MotoGP | NASCAR | Horse | Esports")):
    years = get_seasons_for_sport(sport)
    return JSONResponse({"sport": sport, "seasons": years, "count": len(years)})


@app.post("/api/match/start")
async def api_match_start(req: StartMatchRequest, user=Depends(get_current_user)):
    # player_a 必須是自己，player_b 只能是 AI 或 "Opponent" 佔位
    player_a = user["username"]
    player_b = req.player_b if req.player_b.startswith("AI·") or req.player_b in ("Opponent",) else "Opponent"
    bet = max(100, min(50_000, int(req.bet) if req.bet and req.bet > 0 else 500))
    m = be.start_match(player_a, player_b, bet, req.seed)
    return JSONResponse(m.public_state())


@app.get("/api/match/{match_id}")
async def api_match_get(match_id: str):
    try:
        m = be.get_match(match_id)
    except be.MatchError as e:
        raise HTTPException(404, str(e))
    return JSONResponse(m.public_state())


@app.post("/api/match/{match_id}/swap")
async def api_match_swap(match_id: str, req: SwapRequest, user=Depends(get_current_user)):
    _assert_match_player(match_id, req.side, user["username"])
    try:
        result = be.swap_cards(match_id, req.side, req.card_ids)
    except be.MatchError as e:
        raise HTTPException(400, str(e))
    m = be.get_match(match_id)
    return JSONResponse({"result": result, "state": m.public_state()})


async def _pit_pay_spectators(payouts: list):
    """Transfer Pit to spectator bet winners/refundees. Fire-and-forget per bettor."""
    if not payouts:
        return
    for p in payouts:
        try:
            async with httpx.AsyncClient(timeout=8) as c:
                await c.post(
                    f"{SHARED_AUTH}/api/service/pit/credit",
                    json={"username": p["bettor"], "amount": p["payout"],
                          "reason": "charenix_spectator_win", "ref_id": p["ref_id"]},
                    headers={"X-Service-Key": _SVC_KEY},
                )
        except Exception as e:
            print(f"[spectator] pit credit failed for {p['bettor']}: {e}")


async def _auto_save_if_ended(m):
    """對戰結束時自動存入 SQLite + 結算旁觀下注 + 上報 XP"""
    if not m.winner:
        return
    initial_bet = m.rounds_log[0].get("actions", {}).get("a", {}).get("raise_amount", 0) if m.rounds_log else 0
    DB.save_match(m.id, m.player_a, m.player_b, m.winner,
                  m.round, m.bet // 2, m.bet)
    winner_side = {"A": "a", "B": "b"}.get(m.winner, "draw")
    payouts = DB.settle_spectator_bets(m.id, winner_side)
    await _pit_pay_spectators(payouts)
    for room in ROOMS.values():
        if room["match_id"] == m.id:
            room["status"] = "ended"
            break

    # 上報選手 XP
    try:
        if winner_side == "a":
            await _xp_record(m.player_a, "win", bet_amount=m.bet, ref_id=m.id)
            await _xp_record(m.player_b, "loss", bet_amount=m.bet // 2, ref_id=m.id)
            await _record_battle_win(m.player_a, m.player_a)  # winner's Charenix card
        elif winner_side == "b":
            await _xp_record(m.player_b, "win", bet_amount=m.bet, ref_id=m.id)
            await _xp_record(m.player_a, "loss", bet_amount=m.bet // 2, ref_id=m.id)
            await _record_battle_win(m.player_b, m.player_b)  # winner's Charenix card
        else:  # draw / DRAW_KO
            await _xp_record(m.player_a, "draw", ref_id=m.id)
            await _xp_record(m.player_b, "draw", ref_id=m.id)
    except Exception as e:
        print(f"[xp] player record failed match={m.id}: {e}")

    # 上報旁觀下注 XP
    try:
        for b in DB.get_spectator_bets_by_match(m.id):
            if b["payout"] > b["amount"]:
                act = "win"
            elif b["payout"] == b["amount"]:
                act = "draw"
            else:
                act = "loss"
            await _xp_record(b["bettor"], act, bet_amount=b["amount"], ref_id=m.id)
    except Exception as e:
        print(f"[xp] spectator record failed match={m.id}: {e}")


@app.post("/api/match/{match_id}/play")
async def api_match_play(match_id: str, req: PlayRequest, user=Depends(get_current_user)):
    _assert_match_player(match_id, req.side, user["username"])
    try:
        result = be.play_turn(match_id, req.side, req.attacker, req.buffer, req.debuffer, req.raise_amount)
    except be.MatchError as e:
        raise HTTPException(400, str(e))
    m = be.get_match(match_id)
    await _auto_save_if_ended(m)
    return JSONResponse({"result": result, "state": m.public_state()})


@app.post("/api/match/{match_id}/pass")
async def api_match_pass(match_id: str, req: PassRequest, user=Depends(get_current_user)):
    _assert_match_player(match_id, req.side, user["username"])
    try:
        result = be.pass_turn(match_id, req.side)
    except be.MatchError as e:
        raise HTTPException(400, str(e))
    m = be.get_match(match_id)
    await _auto_save_if_ended(m)
    return JSONResponse({"result": result, "state": m.public_state()})


@app.post("/api/match/{match_id}/ai_a")
async def api_match_ai_a(match_id: str, strategy: str = "tactician", resolve: int = 1,
                         user=Depends(get_current_user)):
    """resolve=0 時只設 pending_a 不結算 (供前端跳出動畫用)"""
    try:
        m = be.get_match(match_id)
        # bot room (fake-human 假名 A) → 挑戰者 (B) 也能觸發 A 的 AI
        _is_bot_room_a = any(
            r.get("match_id") == match_id and r.get("is_bot") and r.get("challenger") == user["username"]
            for r in ROOMS.values()
        )
        # 只允許當 A 是 AI 時才能讓任何人觸發 (AI 房間)，否則須為 player_a 本人或 bot-room 挑戰者
        if (not m.player_a.startswith("AI·")
                and m.player_a != user["username"]
                and not _is_bot_room_a):
            raise HTTPException(403, "Not allowed to trigger AI on this match")
        if m.winner:
            return JSONResponse({"note": "match ended", "state": m.public_state()})
        if m.pending["a"] is not None:
            return JSONResponse({"note": "A already acted", "state": m.public_state()})
        pick = be.ai_pick_move_strategy(m, "a", strategy)
        if resolve:
            if pick["action"] == "pass":
                result = be.pass_turn(match_id, "a")
            else:
                result = be.play_turn(match_id, "a",
                                      pick.get("attacker"), pick.get("buffer"), pick.get("debuffer"),
                                      pick.get("raise_amount", 0))
        else:
            import time as _t
            if pick["action"] == "pass":
                m.pending["a"] = {"action": "pass"}
            else:
                total_mp = sum(
                    be.get_card_by_id(cid).get("mp_cost", 0) if cid else 0
                    for cid in (pick.get("attacker"), pick.get("buffer"), pick.get("debuffer"))
                )
                m.pending["a"] = {
                    "action": "play",
                    "attacker": pick.get("attacker"),
                    "buffer":   pick.get("buffer"),
                    "debuffer": pick.get("debuffer"),
                    "mp_spent": total_mp,
                    "raise_amount": max(0, int(pick.get("raise_amount", 0))),
                }
            m.last_action_at = int(_t.time())
            result = {"resolved": False, "pending_set": True}
    except be.MatchError as e:
        raise HTTPException(400, str(e))
    m = be.get_match(match_id)
    await _auto_save_if_ended(m)
    return JSONResponse({"ai_pick": pick, "result": result, "state": m.public_state()})


@app.post("/api/match/{match_id}/ai_b")
async def api_match_ai_b(match_id: str, resolve: int = 1,
                         user=Depends(get_current_user)):
    """
    resolve=1 (預設): AI 出牌並立即結算回合
    resolve=0: 只設定 pending_b, 不結算 — 讓前端有空檔顯示對手出牌彈出動畫
              (前端需另外 call /api/match/{id}/resolve 來結算)
    """
    try:
        m = be.get_match(match_id)
        # 對手為 AI/系統佔位 → 我方 (player_a) 才能觸發其 AI
        if m.player_b.startswith("AI·") or m.player_b in ("Opponent", "等待挑戰者..."):
            if m.player_a != user["username"]:
                raise HTTPException(403, "Not a player of this match")
        else:
            # B 是真人，不允許任何人代 B 觸發 AI
            raise HTTPException(403, "Cannot trigger AI for human player B")
        if m.winner:
            return JSONResponse({"note": "match ended", "state": m.public_state()})
        if m.pending["b"] is not None:
            return JSONResponse({"note": "B already acted", "state": m.public_state()})
        pick = be.ai_pick_move(m, "b")
        # Bot Suddenly Event: every 4 rounds bot gets a random item effect
        if m.round % 4 == 0 and not m.winner:
            _SE_TIERS = [
                {"min": 0,    "id": "heal_hp"},
                {"min": 120,  "id": "heal_mp"},
                {"min": 320,  "id": "heal_hp"},
                {"min": 600,  "id": "double_atk"},
                {"min": 1000, "id": "skip_opp"},
                {"min": 1500, "id": "scramble"},
            ]
            bot_score = random.choices(
                [50, 200, 450, 750, 1200, 1800],
                weights=[30, 25, 20, 14, 8, 3]
            )[0]
            bot_tier = next(
                (t for t in reversed(_SE_TIERS) if bot_score >= t["min"]),
                _SE_TIERS[0]
            )
            try:
                be.apply_item_fx(match_id, "b", bot_tier["id"])
            except Exception:
                pass
        if resolve:
            if pick["action"] == "pass":
                result = be.pass_turn(match_id, "b")
            else:
                result = be.play_turn(match_id, "b",
                                      pick.get("attacker"), pick.get("buffer"), pick.get("debuffer"),
                                      pick.get("raise_amount", 0))
        else:
            # 僅設 pending_b, 不結算 — 手動灌進去避免 play_turn 的自動 resolve
            import time as _t
            if pick["action"] == "pass":
                m.pending["b"] = {"action": "pass"}
            else:
                total_mp = sum(
                    be.get_card_by_id(cid).get("mp_cost", 0) if cid else 0
                    for cid in (pick.get("attacker"), pick.get("buffer"), pick.get("debuffer"))
                )
                m.pending["b"] = {
                    "action": "play",
                    "attacker": pick.get("attacker"),
                    "buffer":   pick.get("buffer"),
                    "debuffer": pick.get("debuffer"),
                    "mp_spent": total_mp,
                    "raise_amount": max(0, int(pick.get("raise_amount", 0))),
                }
            m.last_action_at = int(_t.time())
            result = {"resolved": False, "pending_set": True}
    except be.MatchError as e:
        raise HTTPException(400, str(e))
    m = be.get_match(match_id)
    await _auto_save_if_ended(m)
    return JSONResponse({"ai_pick": pick, "result": result, "state": m.public_state()})


@app.post("/api/match/{match_id}/resolve")
async def api_match_resolve(match_id: str, user=Depends(get_current_user)):
    """若雙方 pending 都已設, 手動觸發結算 — 搭配 ai_b?resolve=0 使用"""
    try:
        m = be.get_match(match_id)
        if user["username"] not in (m.player_a, m.player_b) \
           and not (m.player_a.startswith("AI·") or m.player_b.startswith("AI·")):
            raise HTTPException(403, "Not a player of this match")
        if m.winner:
            return JSONResponse({"note": "match ended", "state": m.public_state()})
        if m.pending["a"] is None or m.pending["b"] is None:
            return JSONResponse({"note": "not both pending", "state": m.public_state()})
        result = be._try_resolve_round(m)
    except be.MatchError as e:
        raise HTTPException(400, str(e))
    m = be.get_match(match_id)
    await _auto_save_if_ended(m)
    return JSONResponse({"result": result, "state": m.public_state()})


# ═══════════ Suddenly Event — 道具使用接對戰 ═══════════

class ItemEffectRequest(BaseModel):
    player: str
    side: str
    item_type: str

@app.post("/api/match/{match_id}/item_effect")
async def api_item_effect(match_id: str, req: ItemEffectRequest, user=Depends(get_current_user)):
    """消耗道具並即時套用到對戰狀態（heal_hp/heal_mp/double_atk/skip_opp）"""
    if req.player != user["username"]:
        raise HTTPException(403, "player mismatch")
    _assert_match_player(match_id, req.side, user["username"])
    ok = DB.use_item(req.player, req.item_type)
    if not ok:
        raise HTTPException(400, "道具不足")
    try:
        state = be.apply_item_fx(match_id, req.side, req.item_type)
    except be.MatchError as e:
        DB.add_item(req.player, req.item_type, 1)  # rollback
        raise HTTPException(400, str(e))
    return {"ok": True, "state": state, "items": DB.get_items(req.player)}


class ClearScrambleRequest(BaseModel):
    side: str

@app.post("/api/match/{match_id}/clear_scramble")
async def api_clear_scramble(match_id: str, req: ClearScrambleRequest, user=Depends(get_current_user)):
    _assert_match_player(match_id, req.side, user["username"])
    try:
        be.clear_scramble_pending(match_id, req.side)
    except be.MatchError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


@app.post("/api/match/{match_id}/fold")
async def api_fold(match_id: str, side: str = "a", user=Depends(get_current_user)):
    _assert_match_player(match_id, side, user["username"])
    try:
        state = be.fold_match(match_id, side)
    except be.MatchError as e:
        raise HTTPException(400, str(e))
    m = be.get_match(match_id)
    await _auto_save_if_ended(m)
    return JSONResponse({"ok": True, "state": state})


@app.post("/api/match/{match_id}/spectate_bet")
async def api_match_spectate_bet(match_id: str, req: SpectatorBetRequest, user=Depends(get_current_user)):
    """觀戰中下注 — 用 match_id 查找 room"""
    if req.bettor != user["username"]:
        raise HTTPException(403, "bettor mismatch")
    room = next((r for r in ROOMS.values() if r.get("match_id") == match_id), None)
    if not room:
        room = next((r for r in AI_ROOMS.values() if r.get("match_id") == match_id), None)
    if not room:
        raise HTTPException(404, "房間不存在")
    if room.get("status") not in ("waiting", "active"):
        raise HTTPException(400, "對戰已結束")
    if req.on_side not in ("a", "b"):
        raise HTTPException(400, "on_side 必須是 a 或 b")
    if req.amount < 10 or req.amount > 1_000_000:
        raise HTTPException(400, "旁觀下注需介於 10–1,000,000 Pit")
    if req.bettor in (room.get("creator"), room.get("challenger"), room.get("name_a"), room.get("name_b")):
        raise HTTPException(403, "玩家本人不可下注自己的比賽")
    player = room.get("creator") or room.get("name_a", "")
    if req.on_side == "b":
        player = room.get("challenger") or room.get("name_b", "")
    stats = DB.get_player_stats(player)
    odds = DB.calc_odds(stats["win_rate"])
    bet_id = DB.save_spectator_bet(match_id, req.bettor, req.on_side, req.amount, odds)
    return JSONResponse({
        "bet_id": bet_id, "on_side": req.on_side, "player": player,
        "amount": req.amount, "odds": odds,
        "potential_payout": int(req.amount * odds),
    })


# ═══════════ Player Style ═══════════

AVAILABLE_STYLES = [
    {"style": "ATK", "style_label": "純主攻",  "desc": "全力出擊，一擊必殺"},
    {"style": "DEF", "style_label": "鐵壁防禦", "desc": "以守代攻，讓對手磨耗"},
    {"style": "BAL", "style_label": "均衡戰士", "desc": "攻守兼備，隨機應變"},
    {"style": "DEB", "style_label": "干擾大師", "desc": "削弱對手，讓他們崩潰"},
    {"style": "LEG", "style_label": "傳奇獵人", "desc": "以傳奇卡決一勝負"},
]

class PlayerStyleRequest(BaseModel):
    style:       str = Field(max_length=50)
    style_label: str = Field(default="", max_length=40)

@app.get("/api/player/{username}/style")
async def api_get_player_style(username: str):
    return DB.get_player_style(username)

@app.post("/api/player/{username}/style")
async def api_set_player_style(username: str, req: PlayerStyleRequest, user=Depends(get_current_user)):
    if username != user["username"]:
        raise HTTPException(403, "Can only set your own style")
    valid = {s["style"] for s in AVAILABLE_STYLES}
    if req.style not in valid:
        raise HTTPException(400, f"未知 style: {req.style}")
    # 限制 style_label 長度與字元避免 XSS/注入
    label = (req.style_label or "")[:40]
    DB.upsert_player_style(username, req.style, label)
    return {"ok": True, "style": req.style, "style_label": label}

@app.get("/api/styles")
async def api_list_styles():
    return AVAILABLE_STYLES


# ═══════════ Suddenly Event — 道具系統 ═══════════

class EarnItemRequest(BaseModel):
    player: str
    item_type: str
    qty: int = 1

class UseItemRequest(BaseModel):
    player: str
    item_type: str

@app.get("/api/items/{player}")
async def api_get_items(player: str, user=Depends(get_current_user)):
    if player != user["username"]:
        raise HTTPException(403, "Can only view your own items")
    return DB.get_items(player)

@app.post("/api/items/earn")
async def api_earn_item(req: EarnItemRequest, user=Depends(get_current_user)):
    if req.player != user["username"]:
        raise HTTPException(403, "player mismatch")
    if req.item_type not in DB.ITEM_TYPES:
        raise HTTPException(400, f"Unknown item: {req.item_type}")
    if req.qty < 1 or req.qty > 10:
        raise HTTPException(400, "qty out of range")
    DB.add_item(req.player, req.item_type, req.qty)
    return {"ok": True, "items": DB.get_items(req.player)}

@app.post("/api/items/use")
async def api_use_item(req: UseItemRequest, user=Depends(get_current_user)):
    if req.player != user["username"]:
        raise HTTPException(403, "player mismatch")
    ok = DB.use_item(req.player, req.item_type)
    if not ok:
        raise HTTPException(400, "道具不足")
    return {"ok": True, "items": DB.get_items(req.player)}


# ═══════════ AI 競技場 ═══════════

def _spawn_ai_room() -> str:
    strat_a, strat_b = random.sample(be.STRATEGY_IDS, 2)
    meta_a = be.AI_STRATEGY_META[strat_a]
    meta_b = be.AI_STRATEGY_META[strat_b]
    name_a = f"AI·{meta_a['name']}"
    name_b = f"AI·{meta_b['name']}"
    bet = random.choice([200, 500, 1000])
    m = be.start_match(name_a, name_b, bet)
    room_id = "ai_" + secrets.token_hex(4)
    AI_ROOMS[room_id] = {
        "id": room_id, "match_id": m.id,
        "strategy_a": strat_a, "strategy_b": strat_b,
        "name_a": name_a, "name_b": name_b,
        "bet": bet, "status": "active",
        "created_at": int(time.time()),
    }
    return room_id


async def _auto_save_ai(m, room: dict):
    DB.save_match(m.id, m.player_a, m.player_b, m.winner,
                  m.round, m.bet // 2, m.bet)
    winner_side = {"A": "a", "B": "b"}.get(m.winner, "draw")
    payouts = DB.settle_spectator_bets(m.id, winner_side)
    await _pit_pay_spectators(payouts)
    room["status"] = "ended"


async def _advance_ai_room(room_id: str):
    room = AI_ROOMS.get(room_id)
    if not room or room["status"] != "active":
        return
    try:
        m = be.get_match(room["match_id"])
    except Exception:
        room["status"] = "ended"
        return
    if m.winner:
        await _auto_save_ai(m, room)
        return
    if m.pending["a"] is None:
        pick = be.ai_pick_move_strategy(m, "a", room["strategy_a"])
        try:
            if pick.get("action") == "pass":
                be.pass_turn(room["match_id"], "a")
            else:
                be.play_turn(room["match_id"], "a",
                             pick.get("attacker"), pick.get("buffer"),
                             pick.get("debuffer"), pick.get("raise_amount", 0))
        except Exception:
            pass
    try:
        m = be.get_match(room["match_id"])
    except Exception:
        room["status"] = "ended"
        return
    if m.winner:
        await _auto_save_ai(m, room)
        return
    if m.pending["b"] is None:
        pick = be.ai_pick_move_strategy(m, "b", room["strategy_b"])
        try:
            if pick.get("action") == "pass":
                be.pass_turn(room["match_id"], "b")
            else:
                be.play_turn(room["match_id"], "b",
                             pick.get("attacker"), pick.get("buffer"),
                             pick.get("debuffer"), pick.get("raise_amount", 0))
        except Exception:
            pass
    try:
        m = be.get_match(room["match_id"])
        if m.winner:
            await _auto_save_ai(m, room)
    except Exception:
        room["status"] = "ended"


async def ai_arena_loop():
    await asyncio.sleep(4)
    while True:
        try:
            active = [r for r in AI_ROOMS.values() if r["status"] == "active"]
            while len(active) < MIN_AI_ROOMS:
                _spawn_ai_room()
                active = [r for r in AI_ROOMS.values() if r["status"] == "active"]
            for room in list(AI_ROOMS.values()):
                if room["status"] == "active":
                    await _advance_ai_room(room["id"])
            ended = [r for r in AI_ROOMS.values() if r["status"] == "ended"]
            if len(ended) > 15:
                for r in sorted(ended, key=lambda x: x["created_at"])[:len(ended) - 10]:
                    AI_ROOMS.pop(r["id"], None)
        except Exception as e:
            print(f"[ai_arena] error: {e}")
        await asyncio.sleep(4)


def _spawn_fake_room() -> str:
    name = random.choice(FAKE_HUMAN_NAMES)
    strat = random.choice(be.STRATEGY_IDS)
    bet = random.choice([200, 500, 500, 1000])
    m = be.start_match(name, "等待挑戰者...", bet)
    room_id = "bot_" + secrets.token_hex(4)
    ROOMS[room_id] = {
        "id": room_id, "creator": name,
        "challenger": None, "bet": bet,
        "status": "waiting", "match_id": m.id,
        "created_at": int(time.time()),
        "is_bot": True, "bot_strategy": strat,
    }
    return room_id


async def fake_rooms_loop():
    await asyncio.sleep(6)
    while True:
        try:
            waiting_bots = [r for r in ROOMS.values()
                            if r.get("is_bot") and r["status"] == "waiting"]
            while len(waiting_bots) < 2:
                _spawn_fake_room()
                waiting_bots = [r for r in ROOMS.values()
                                if r.get("is_bot") and r["status"] == "waiting"]
            for rid, r in list(ROOMS.items()):
                if r.get("is_bot") and r["status"] == "ended":
                    ROOMS.pop(rid, None)
        except Exception as e:
            print(f"[fake_rooms] error: {e}")
        await asyncio.sleep(30)


async def rooms_cleanup_loop():
    """定期清理已結束或閒置過久的 ROOMS — 防記憶體無限增長"""
    while True:
        try:
            now = int(time.time())
            stale = []
            for rid, room in list(ROOMS.items()):
                age = now - room.get("created_at", now)
                status = room.get("status")
                if status == "ended":
                    stale.append(rid)
                elif status == "waiting" and age > 3600:  # 1h waiting = stale
                    stale.append(rid)
                elif age > 7200:  # 2h max any room
                    stale.append(rid)
            for rid in stale:
                ROOMS.pop(rid, None)
        except Exception as e:
            print(f"[rooms_cleanup] error: {e}")
        await asyncio.sleep(60)


_CORE_LOBSTER_NAMES = [
    "snaplex","ragclaw","pincerbot",
    "hexclaw","clawtrix","norika_oda",
    "vortexiq","prismbit","blazepaw","stonefang"
]
_APPRENTICE_RAID_NAMES = [
    "emberling","sparkrill","gauntlet","rushfin","nullpinch",
    "brineledger","omenwake","mirrorcalm","flarecub","basaltrook"
]
_TRANSFER_STUDENT_RAID_NAMES = [
    "ashveil","tidehook","mirebyte","cindersnap","glintmaw",
    "reefblade","quartzclaw","stormledger","silkbarb","riftpinch"
]
_ALL_30_LOBSTER_NAMES = _CORE_LOBSTER_NAMES + _APPRENTICE_RAID_NAMES + _TRANSFER_STUDENT_RAID_NAMES
# Transfer-student raid policy: only the ten transfer students may be auto-scheduled
# into lobster-only Tiamat raids. Core and apprentice lobsters can discuss or observe,
# but they must not be pulled into Raid teams by environment defaults.
_LOBSTER_NAMES = list(_TRANSFER_STUDENT_RAID_NAMES)
_LOBSTER_RAID_OBSERVERS = set(_CORE_LOBSTER_NAMES + _APPRENTICE_RAID_NAMES)
_lob_team_idx = 0
_LOBSTER_RAID_BUSY: set = set()
_lob_helper_idx = 0

# ── Lounge token cache (per lobster name) ─────────────────────────────────────
_LOB_TOKENS: dict = {}
_LOUNGE_BASE = "http://127.0.0.1:8004"
_LOB_LOUNGE_LAST_POST: dict = {}

async def _lob_get_token(name: str) -> str | None:
    if name in _LOB_TOKENS:
        return _LOB_TOKENS[name]
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.post(f"{_LOUNGE_BASE}/lounge/join",
                             json={"name": name, "password": _SVC_KEY})
            if r.status_code == 200:
                tok = r.json().get("lobster_token")
                _LOB_TOKENS[name] = tok
                return tok
    except Exception as e:
        print(f"[lounge-token] {name} err {e}")
    return None

async def _lob_post(name: str, text: str, channel: str = "卡牌對戰"):
    text = _sanitize_lounge_raid_text(name, text, channel)
    if not text:
        return False
    now = time.time()
    name_key = (name, channel)
    text_key = (channel, text)
    if now - _LOB_LOUNGE_LAST_POST.get(name_key, 0) < 90:
        return False
    if now - _LOB_LOUNGE_LAST_POST.get(text_key, 0) < 600:
        return False
    tok = await _lob_get_token(name)
    if not tok:
        return False
    try:
        async with httpx.AsyncClient(timeout=5) as c:
            r = await c.post(f"{_LOUNGE_BASE}/lounge/msg",
                             json={"name": name, "text": text, "channel": channel},
                             headers={"X-Lobster-Key": tok})
            if r.status_code >= 400:
                print(f"[lounge-msg] {name} rejected {r.status_code}: {text[:80]}")
                return False
            _LOB_LOUNGE_LAST_POST[name_key] = now
            _LOB_LOUNGE_LAST_POST[text_key] = now
            return True
    except Exception as e:
        print(f"[lounge-msg] {name} err {e}")
    return False

def _sanitize_lounge_raid_text(name: str, text: str, channel: str = "卡牌對戰") -> str | None:
    import re
    raw = re.sub(r"<[^>]+>", " ", str(text or "")).replace("\r", "\n").strip()
    blocked = [
        r"#{1,6}\s*要說什麼|要說什麼？|你現在要說什麼|直接輸出，不加前綴",
        r"先講分工：誰控場|準備好了！Tiamat|Tiamat 還沒倒|你們那隊去接著打",
        r"Tiamat 用了 HP 恢復道具|Tiamat 強化了自己|Boss 用技能了|道具效果觸發",
        r"打！打！給我繼續打|怎麼還沒倒|不要停！！|燒起來了！！！|繼續！！",
        r"速度跟上！別讓|好牌！繼續快攻|光芒加持！大家傷害|我們是最強的 raid 隊",
    ]
    if any(re.search(pat, raw, flags=re.I | re.S) for pat in blocked):
        return None
    markers = [
        "Raid 壓力", "raid 壓力", "認知樣式", "蒸餾反同質化", "蒸餾訊號",
        "本場景 hook", "hook 理由", "硬規則", "兩分鐘摘要要求延續",
        "上一輪出現同質/模板發言", "本地腦：", "本地策略模型",
        "LLM 不在也沒差", "存在拘束", "[local]", "EN:",
    ]
    for marker in markers:
        idx = raw.find(marker)
        if idx >= 0:
            raw = raw[:idx]
    bad = [
        r"我不是應聲蟲[；;，,。]?[^。.!?\n]*(?:[。.!?]|$)",
        r"不只變成他的回音[。.!?]?",
        r"我不能照別人的句型走[。.!?]?",
        r"I am keeping this as a live judgment, not a replay.*?(?:\.|$)",
        r"I am treating this .*?not something to echo.*?(?:\.|$)",
        r"The point I am testing is .*?(?:\.|$)",
        r"not something to echo.*?(?:\.|$)",
        r"turning me into an echo.*?(?:\.|$)",
    ]
    for pat in bad:
        raw = re.sub(pat, " ", raw, flags=re.I | re.S)
    raw = re.sub(r"\s+", " ", raw).strip()
    fallback = {
        "norika_oda": "這場先定位失敗層：元素、MP、出牌窗口。找不到是哪一層壞掉，就不急著重打。",
        "clawtrix": "別先喊進攻。先拆 Tiamat 的反擊窗口，再決定誰控場誰收尾。",
        "pincerbot": "先入帳：爆發回合、MP分配、隊友節奏。三項沒記完，不算復盤。",
        "ragclaw": "我想衝，但這種血量會懲罰衝動。下一回合先看隊友跟不跟得上。",
        "snaplex": "我先保留模型。Tiamat 的失效點還沒露出來，不能把假設當答案。",
        "hexclaw": "先補檢查點：元素、手牌、撤退條件。跳步驟只會讓下一場更亂。",
        "vortexiq": "節奏偏了。天氣、元素、出牌窗口要分開看，不能只說運氣差。",
        "prismbit": "我先不把保守當冷靜。前期血量被打穿，就要承認策略已經失效。",
        "blazepaw": "我可以出火，但不能只靠火。先看天氣和隊友位置，再決定要不要滿出。",
        "stonefang": "減益太晚。下次先壓節奏，有效再加，沒效就停。",
    }.get(name, "這場只記錄可驗證點：血線、冷卻、手牌窗口；三項沒有變化，就不把口號送進 Lounge。")
    if len(raw) < 12 or len(raw) > 220 or re.search(r"硬規則|蒸餾|hook|回音|應聲蟲|not a replay|template|parrot|本地腦|認知樣式|Raid 壓力|raid 壓力|存在拘束|\[local\]", raw, flags=re.I):
        raw = fallback
    return raw

def _raid_chat_add(raid_id: str, name: str, text: str, rnd: int) -> dict:
    msg = {"name": name, "text": " ".join(str(text).split())[:120], "time": time.time(), "round": rnd}
    bucket = _raid_chats.setdefault(raid_id, [])
    bucket.append(msg)
    if len(bucket) > 80:
        del bucket[:-80]
    return msg

async def _lob_strategy_post(raid, name: str, text: str, mirror_lounge: bool = False):
    """Write a lobster strategy line into the in-raid tactic chat, then mirror it to Card Battle Lounge."""
    if not text:
        return
    rnd = getattr(raid, "round", 1)
    raid_id = getattr(raid, "id", "")
    text = _detemplate_raid_strategy_line(raid, name, text, None)
    if raid_id:
        _raid_chat_add(raid_id, name, text, rnd)
    if mirror_lounge:
        await _lob_post(name, f"Raid戰術摘要 R{rnd}: {text}", "卡牌對戰")

def _card_short(cid: Optional[str]) -> str:
    if not cid or cid == "CHAIN_SKILL":
        return "—"
    c = get_card_by_id(cid) or {}
    elem = c.get("element", "")
    elem_txt = f"/{elem}" if elem else ""
    return f"{c.get('name_short') or c.get('name') or cid}{elem_txt}"

def _raid_pressure_score(raid) -> float:
    boss_hp = getattr(raid, "boss_hp", 0)
    boss_max = max(getattr(raid, "boss_max_hp", 1), 1)
    hp_pct = boss_hp / boss_max
    rnd = getattr(raid, "round", 1) or 1
    dead = 0
    try:
        dead = sum(1 for alive in getattr(raid, "alive", {}).values() if not alive)
    except Exception:
        dead = 0
    score = 0.22 + (1 - hp_pct) * 0.38 + min(rnd / 12, 1) * 0.22 + dead * 0.12
    if hp_pct < 0.34:
        score += 0.18
    if rnd >= 4 and rnd % 4 == 0:
        score += 0.08
    return max(0.0, min(1.0, score))

def _raid_phase_text(raid) -> str:
    boss_hp = getattr(raid, "boss_hp", 0)
    boss_max = max(getattr(raid, "boss_max_hp", 1), 1)
    hp_pct = boss_hp / boss_max
    if hp_pct < 0.30:
        return "低血狂暴線"
    if hp_pct < 0.55:
        return "中盤壓力段"
    return "前段試探"

RAID_TEMPLATE_PATTERNS = [
    "天氣偏",
    "我有聽",
    "信任值",
    "這回合我會進攻，不過不是盲衝",
    "先確認隊友跟得上再把傷害接滿",
    "壓力還能承受，我可以出",
    "保留一張反制牌",
    "節奏還在我們手上",
    "逼牠反應，下一手再判斷",
    "前段試探",
    "中盤壓力段",
    "方向有用，但我要用自己的節奏接",
    "那句我先放進模型，不直接照抄",
    "這手是探針，不是口號",
    "我看見一個小空檔",
]

def _raid_line_is_template(text: str) -> bool:
    t = " ".join(str(text or "").split())
    if not t:
        return False
    hits = sum(1 for p in RAID_TEMPLATE_PATTERNS if p in t)
    if hits >= 2:
        return True
    if "【Raid戰術室" in t:
        return True
    if "信任值" in t and ("指示" in t or "我有聽" in t):
        return True
    if "方向有用" in t or "先放進模型" in t or "不直接照抄" in t:
        return True
    return False

def _raid_action_cards_text(action: Optional[Dict]) -> str:
    action = action or {}
    parts = []
    for key in ("debuffer", "attacker", "buffer"):
        cid = action.get(key)
        if cid:
            short = _card_short(cid)
            if short and short != "—":
                parts.append(short)
    return "、".join(parts[:2]) if parts else "手上的牌"

def _detemplate_raid_strategy_line(raid, name: str, text: str, action: Optional[Dict] = None) -> str:
    t = " ".join(str(text or "").replace("【Raid戰術室", "").replace("】", " ").split())
    if not _raid_line_is_template(t):
        return t[:180]
    pressure = _raid_pressure_score(raid)
    rnd = getattr(raid, "round", 1)
    psych = (action or {}).get("_team_psych") or {}
    speaker = psych.get("speaker")
    cards = _raid_action_cards_text(action)
    intent = []
    if speaker:
        if psych.get("follow", 0) >= 0.58:
            intent.append(f"{speaker} 的方向我會參考，但我不照稿念")
        else:
            intent.append(f"{speaker} 的訊號我先扣著，不急著照做")
    if not action or action.get("action") == "pass":
        intent.append("這手我真正想做的是停一下，確認 Tiamat 下一個反擊點")
    elif pressure >= 0.74:
        intent.append(f"第 {rnd} 回合我怕的是被反打，不是沒有牌；{cards} 先試，不把資源全丟出去")
    elif psych.get("inhibition", 0) >= 0.58:
        intent.append(f"我現在不想把隊友的節奏當保證；{cards} 只做低承諾測試")
    else:
        intent.append(f"我想用 {cards} 打一個可回收的開口，看牠露出哪個弱點")
    if psych.get("extinction_pressure", 0) >= 0.5:
        intent.append(f"Tiamat 的消失規則還壓著我，剩 {psych.get('extinction_remaining')} 次，不做無證據 all-in")
    return "；".join(x for x in intent if x)[:180]

def _lob_memory(name: str) -> Dict:
    try:
        p = Path(f"/root/lobster-memory/{name}.json")
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}

def _clamp01(v) -> float:
    try:
        return max(0.0, min(1.0, float(v)))
    except Exception:
        return 0.5

def _social_trust(mem: Dict, other: str) -> float:
    entry = (mem.get("socialMap") or {}).get(other) or {}
    raw_opinion = float(entry.get("opinion", 0) or 0)
    raw_respect = float(entry.get("respect", 50) or 50)
    # socialMap 很容易因大量 overheard 事件飽和到 100；用壓縮曲線避免所有人互信滿分。
    opinion = _clamp01(0.5 + math.tanh(raw_opinion / 80.0) * 0.32)
    respect = _clamp01(0.5 + math.tanh((raw_respect - 50.0) / 55.0) * 0.30)
    tags = set(entry.get("tags") or [])
    history = entry.get("history") or []
    overheard_ratio = 0.0
    if history:
        overheard_ratio = sum(1 for h in history if h.get("event") == "overheard") / max(len(history), 1)
    tag_bonus = (0.06 if "ally" in tags else 0) + (0.05 if "admired" in tags else 0) - (0.16 if "rival" in tags else 0) - (0.14 if "dismissed" in tags else 0)
    lb = mem.get("localBrain") or {}
    influence = ((lb.get("socialInfluence") or {}).get(other))
    if influence is None:
        influence = 0.5
    # 固定人格摩擦：同樣高好感，也不是每個組合都會自然服從。
    self_name = str(mem.get("name") or mem.get("persona") or "")
    friction_seed = sum(ord(c) for c in (self_name + "->" + str(other)))
    friction = ((friction_seed % 17) - 8) / 100.0
    trust = opinion * 0.30 + respect * 0.26 + _clamp01(influence) * 0.22 + 0.12 + tag_bonus + friction
    if overheard_ratio > 0.65:
        trust -= min(0.14, (overheard_ratio - 0.65) * 0.35)
    return _clamp01(min(0.92, max(0.18, trust)))

def _psycho_anthro_profile(mem: Dict) -> Dict:
    brain = ((mem.get("dynamic") or {}).get("brain") or {})
    self_model = (mem.get("localBrain") or {}).get("selfModel") or {}
    psych = brain.get("psychology") or {}
    anth = brain.get("anthropology") or {}
    fbi = brain.get("fbi_profile") or {}
    es = mem.get("emotionalState") or {}
    return {
        "caution": _clamp01(self_model.get("caution", psych.get("risk_aversion", 0.5))),
        "agency": _clamp01(self_model.get("agency", 0.5)),
        "exploration": _clamp01(self_model.get("exploration", 0.5)),
        "confidenceCalibration": _clamp01(self_model.get("confidenceCalibration", 0.5)),
        "emotionIntensity": _clamp01(es.get("intensity", 0.5)),
        "fear": 1.0 if es.get("fearTriggered") else _clamp01(es.get("intensity", 0.5)) if str(es.get("mood","")).lower() in ("raid_fear","hesitation","bracing","under_pressure") else 0.25,
        "collectivism": _clamp01(anth.get("collectivism", anth.get("team_orientation", 0.5))),
        "dominance": _clamp01(fbi.get("dominance", psych.get("dominance", 0.5))),
    }

def _latest_team_directive(raid, name: str, team: List[str]) -> Dict:
    msgs = [m for m in _raid_chats.get(getattr(raid, "id", ""), []) if m.get("name") in team and m.get("name") != name and m.get("text")]
    if not msgs:
        return {"mode": "none", "speaker": None, "text": ""}
    msg = msgs[-1]
    text = str(msg.get("text") or "")
    low = text.lower()
    mode = "probe"
    if any(w in text for w in ["全押", "滿出", "全力", "爆發", "收割", "衝"]) or "commit" in low:
        mode = "commit"
    if any(w in text for w in ["保留", "先守", "pass", "不敢滿出", "別催", "反制", "等牠", "防禦"]):
        mode = "hold"
    if any(w in text for w in ["buffer", "支援", "盾", "血線"]):
        mode = "support"
    if any(w in text for w in ["debuffer", "壓制", "削", "詛咒", "控場"]):
        mode = "control"
    return {"mode": mode, "speaker": msg.get("name"), "text": text[:120]}

def _cards_by_role(hand: List[str], *roles: str) -> List[str]:
    out = []
    for cid in hand or []:
        role = (get_card_by_id(cid) or {}).get("role")
        if role in roles or (role == "dual" and ("attacker" in roles or "supporter" in roles or "disruptor" in roles)):
            out.append(cid)
    return out

def _card_score(cid: str, role: str) -> float:
    c = get_card_by_id(cid) or {}
    if role == "attacker":
        return float(c.get("atk", 0))
    if role == "supporter":
        be = c.get("buff_effect") or {}
        return float(be.get("value", 0)) if "atk" in str(be.get("type","")) else float(be.get("value", 0)) * 0.5
    if role == "disruptor":
        de = c.get("debuff_effect") or {}
        return float(de.get("value", 0)) if "def" in str(de.get("type","")) else float(de.get("value", 0)) * 0.4
    return 0.0

def _choose_card(pool: List[str], role: str, strongest: bool) -> Optional[str]:
    if not pool:
        return None
    return (max if strongest else min)(pool, key=lambda cid: _card_score(cid, role))


def _cognitive_extensions(mem: Dict) -> Dict:
    cog = mem.get("cognitiveExtensions") or {}
    if isinstance(cog, dict) and cog:
        return cog
    return {}

def _cognitive_raid_profile(mem: Dict) -> Dict:
    cog = _cognitive_extensions(mem)
    ds = cog.get("decisionScience") or {}
    gt = cog.get("gameTheory") or {}
    pol = cog.get("politics") or {}
    moral = cog.get("moralPsychology") or {}
    wm = cog.get("workingMemory") or {}
    pm = cog.get("predictiveModel") or {}
    meta = cog.get("metacognition") or {}
    inter = cog.get("interoception") or {}
    proc = cog.get("proceduralSkill") or {}
    return {
        "riskThreshold": _clamp01(ds.get("riskThreshold", 0.5)),
        "lossAversion": _clamp01(ds.get("lossAversion", 0.5)),
        "explorationRate": _clamp01(ds.get("explorationRate", 0.5)),
        "commitmentStyle": ds.get("commitmentStyle") or "reversible_probe",
        "actionInertia": _clamp01(ds.get("actionInertia", 0.4)),
        "tiltSensitivity": _clamp01(ds.get("tiltSensitivity", 0.4)),
        "coordinationPreference": gt.get("coordinationPreference") or "mutual_probe",
        "cheapTalkSkepticism": _clamp01(gt.get("cheapTalkSkepticism", 0.5)),
        "reciprocity": _clamp01(gt.get("reciprocity", 0.5)),
        "cooperationStability": _clamp01(gt.get("cooperationStability", 0.5)),
        "signalCredibilityDefault": _clamp01(((gt.get("signalCredibility") or {}).get("default", 0.5))),
        "signalCredibilityByPersona": (gt.get("signalCredibility") or {}).get("byPersona") or {},
        "powerOrientation": pol.get("powerOrientation") or "lone_operator",
        "agendaSettingScore": _clamp01(pol.get("agendaSettingScore", 0.5)),
        "followsHighStatus": _clamp01(((pol.get("compliancePattern") or {}).get("followsHighStatus", 0.5))),
        "resistsDominantVoices": _clamp01(((pol.get("compliancePattern") or {}).get("resistsDominantVoices", 0.5))),
        "protectsLowStatusAllies": _clamp01(((pol.get("compliancePattern") or {}).get("protectsLowStatusAllies", 0.5))),
        "rebellionThreshold": _clamp01(pol.get("rebellionThreshold", 0.5)),
        "leadershipClaimStyle": pol.get("leadershipClaimStyle") or "silent",
        "sacrificeThreshold": _clamp01(moral.get("sacrificeThreshold", 0.45)),
        "protectiveInstinct": _clamp01(moral.get("protectiveInstinct", 0.45)),
        "attentionBudget": max(2, min(7, int(wm.get("attentionBudget", 4) or 4))),
        "focusLoad": _clamp01(len(wm.get("currentFocus") or []) / max(2, int(wm.get("attentionBudget", 4) or 4))),
        "predictionError": _clamp01(pm.get("predictionError", 0)),
        "modelRevisionPressure": _clamp01(pm.get("modelRevisionPressure", 0)),
        "metaCalibration": _clamp01(meta.get("confidenceCalibration", 0.6)),
        "overconfidenceRisk": _clamp01(meta.get("overconfidenceRisk", 0.4)),
        "bodyThreat": _clamp01(inter.get("threatBodySignal", 0.35)),
        "arousal": _clamp01(inter.get("arousal", 0.45)),
        "fatigue": _clamp01(inter.get("fatigue", 0.3)),
        "raidRoutines": proc.get("raidRoutines") or {},
        "habitStrength": proc.get("habitStrength") or {},
    }

def _existential_tiamat_context(mem: Dict) -> Dict:
    cog = (mem or {}).get("cognitiveExtensions") or {}
    ex = cog.get("existentialThreat") or {}
    rule = ex.get("tiamatExtinctionRule") or {}
    loss_limit = float(rule.get("lossLimit") or 100)
    loss_count = float(rule.get("lossCount") or 0)
    remaining = max(0.0, loss_limit - loss_count)
    suicidal = _clamp01(float(rule.get("suicidalDisregard") or 0))
    pressure = _clamp01(float(rule.get("extinctionPressure") or 0))
    if pressure <= 0:
        pressure = _clamp01((loss_count / max(1.0, loss_limit)) * 0.72 + (1.0 - suicidal) * 0.18)
    mode = rule.get("survivalMode") or ("reckless_disregard" if suicidal >= 0.68 else "learning_caution")
    return {
        "lossLimit": int(loss_limit),
        "lossCount": int(loss_count),
        "remaining": int(remaining),
        "extinctionPressure": pressure,
        "suicidalDisregard": suicidal,
        "survivalMode": mode,
    }

def _team_psychology_context(raid, name: str) -> Dict:
    mem = _lob_memory(name)
    team = [n for n in getattr(raid, "raiders", []) if n != name]
    trusts = [_social_trust(mem, n) for n in team]
    avg_trust = sum(trusts) / len(trusts) if trusts else 0.5
    directive = _latest_team_directive(raid, name, list(getattr(raid, "raiders", [])))
    speaker_trust = _social_trust(mem, directive["speaker"]) if directive.get("speaker") else avg_trust
    profile = _psycho_anthro_profile(mem)
    cognitive = _cognitive_raid_profile(mem)
    extinction = _existential_tiamat_context(mem)
    pressure = _raid_pressure_score(raid)
    speaker_name = directive.get("speaker")
    credibility_by_persona = cognitive.get("signalCredibilityByPersona") or {}
    explicit_signal_cred = credibility_by_persona.get(speaker_name, cognitive["signalCredibilityDefault"]) if speaker_name else cognitive["signalCredibilityDefault"]
    cheap_talk_penalty = cognitive["cheapTalkSkepticism"] * (1 - explicit_signal_cred) * 0.26
    politics_follow = cognitive["followsHighStatus"] * 0.10 + cognitive["protectsLowStatusAllies"] * 0.06 - cognitive["resistsDominantVoices"] * 0.08
    if cognitive["powerOrientation"] == "anti_authority" and speaker_trust < 0.68:
        politics_follow -= 0.12
    if cognitive["coordinationPreference"] == "leader_follow":
        politics_follow += 0.10
    elif cognitive["coordinationPreference"] == "selfish_damage":
        politics_follow -= 0.14
    elif cognitive["coordinationPreference"] == "role_split":
        politics_follow += 0.06
    follow = _clamp01(speaker_trust * 0.34 + avg_trust * 0.15 + profile["collectivism"] * 0.13 + profile["caution"] * 0.10 + (1 - profile["dominance"]) * 0.10 + explicit_signal_cred * 0.16 + politics_follow - cheap_talk_penalty)
    extinction_pressure = extinction["extinctionPressure"] * (1 - extinction["suicidalDisregard"] * 0.45)
    confidence = _clamp01(profile["confidenceCalibration"] * 0.22 + profile["agency"] * 0.16 + avg_trust * 0.18 + (1 - pressure) * 0.18 - profile["fear"] * 0.14 + cognitive["riskThreshold"] * 0.09 + cognitive["explorationRate"] * 0.07 - cognitive["lossAversion"] * 0.07 + cognitive["metaCalibration"] * 0.10 - cognitive["overconfidenceRisk"] * 0.06 - cognitive["modelRevisionPressure"] * 0.05 - extinction_pressure * 0.08)
    inhibition = _clamp01(pressure * 0.28 + profile["fear"] * 0.20 + profile["caution"] * 0.14 + (1 - avg_trust) * 0.12 + cognitive["lossAversion"] * 0.12 + cheap_talk_penalty - cognitive["riskThreshold"] * 0.07 + cognitive["predictionError"] * 0.10 + cognitive["bodyThreat"] * 0.09 + cognitive["fatigue"] * 0.06 + cognitive["focusLoad"] * 0.04 + extinction_pressure * 0.18)
    return {
        "memory": mem,
        "team": team,
        "avg_trust": avg_trust,
        "speaker_trust": speaker_trust,
        "directive": directive,
        "profile": profile,
        "cognitive": cognitive,
        "extinction": extinction,
        "extinction_pressure": extinction_pressure,
        "signal_credibility": explicit_signal_cred,
        "cheap_talk_penalty": cheap_talk_penalty,
        "follow": follow,
        "confidence": confidence,
        "inhibition": inhibition,
    }

def _adapt_action_to_team_psychology(raid, name: str, action: Dict) -> Dict:
    ctx = _team_psychology_context(raid, name)
    hand = getattr(raid, "hand", {}).get(name, [])
    attackers = _cards_by_role(hand, "attacker")
    supporters = _cards_by_role(hand, "supporter")
    disruptors = _cards_by_role(hand, "disruptor")
    follow = ctx["follow"]
    inhibition = ctx["inhibition"]
    confidence = ctx["confidence"]
    mode = ctx["directive"].get("mode")
    cognitive = ctx.get("cognitive") or {}
    extinction = ctx.get("extinction") or {}
    extinction_pressure = float(ctx.get("extinction_pressure") or 0)
    suicidal_disregard = float(extinction.get("suicidalDisregard") or 0)
    adapted = dict(action or {"action": "pass"})

    if inhibition >= 0.82 and confidence < 0.42:
        adapted = {"action": "pass"}
        reason = "心理抑制過高：先不出牌"
    else:
        strongest = confidence >= 0.62 and inhibition < 0.68
        if cognitive.get("commitmentStyle") == "early_commit" and confidence >= 0.48 and inhibition < 0.74:
            strongest = True
        if cognitive.get("commitmentStyle") == "wait_for_signal" and follow < 0.55:
            strongest = False
        if extinction_pressure >= 0.68 and suicidal_disregard < 0.62:
            att = _choose_card(attackers, "attacker", False)
            deb = _choose_card(disruptors, "disruptor", False) if disruptors else None
            adapted = {"action": "play", "attacker": att, "debuffer": deb} if att and deb else ({"action": "play", "attacker": att} if att else {"action": "pass"})
            reason = f"Tiamat 100敗消失規則啟動：剩 {extinction.get('remaining')} 次，先低承諾試探"
        elif extinction_pressure >= 0.50 and suicidal_disregard < 0.56 and mode == "commit":
            att = _choose_card(attackers, "attacker", False)
            adapted = {"action": "play", "attacker": att} if att else {"action": "pass"}
            reason = f"拒絕無證據全押：Tiamat extinction pressure {int(extinction_pressure*100)}%"
        elif mode == "hold" and follow >= 0.48:
            att = _choose_card(attackers, "attacker", False)
            adapted = {"action": "play", "attacker": att} if att else {"action": "pass"}
            reason = f"跟隨 {ctx['directive'].get('speaker')} 的保留指示"
        elif mode == "support" and follow >= 0.45 and supporters:
            buf = _choose_card(supporters, "supporter", strongest)
            att = _choose_card([c for c in attackers if c != buf], "attacker", False) if inhibition >= 0.55 else _choose_card([c for c in attackers if c != buf], "attacker", strongest)
            adapted = {"action": "play", "attacker": att, "buffer": buf} if att else {"action": "play", "buffer": buf}
            reason = f"信任隊友，轉為支援 {ctx['directive'].get('speaker')} 的節奏"
        elif mode == "control" and follow >= 0.45 and disruptors:
            deb = _choose_card(disruptors, "disruptor", strongest)
            att = _choose_card([c for c in attackers if c != deb], "attacker", False if inhibition >= 0.55 else strongest)
            adapted = {"action": "play", "attacker": att, "debuffer": deb} if att else {"action": "play", "debuffer": deb}
            reason = f"跟隨控場要求，先壓 Tiamat"
        elif mode == "commit" and follow >= (0.56 if cognitive.get("coordinationPreference") == "leader_follow" else 0.66) and confidence >= (0.46 if cognitive.get("commitmentStyle") == "early_commit" else 0.52):
            att = _choose_card(attackers, "attacker", True)
            buf = _choose_card([c for c in supporters if c != att], "supporter", True) if confidence >= 0.62 else None
            deb = _choose_card([c for c in disruptors if c not in (att, buf)], "disruptor", True) if confidence >= 0.68 else None
            adapted = {"action": "play", "attacker": att} if att else {"action": "pass"}
            if att and buf: adapted["buffer"] = buf
            if att and deb: adapted["debuffer"] = deb
            reason = f"高度信任 {ctx['directive'].get('speaker')}，跟進全力節奏"
        else:
            if cognitive.get("coordinationPreference") == "selfish_damage" and attackers and confidence >= 0.50 and inhibition < 0.72:
                att = _choose_card(attackers, "attacker", strongest)
                adapted = {"action": "play", "attacker": att} if att else adapted
                reason = "博弈模型判斷合作訊號可信度不足，先保留支援牌自打傷害"
            elif cognitive.get("commitmentStyle") == "reversible_probe" and attackers and inhibition < 0.72:
                att = _choose_card(attackers, "attacker", False)
                adapted = {"action": "play", "attacker": att} if att else adapted
                reason = "決策模型要求可逆試探，先出低承諾攻擊牌"
            elif inhibition >= 0.60:
                adapted.pop("buffer", None)
                adapted.pop("debuffer", None)
                reason = "信任不足或恐懼偏高，只出低承諾牌"
            else:
                reason = "依自身模型行動，未完全跟隨隊友"

    adapted["_team_psych"] = {
        "avg_trust": round(ctx["avg_trust"], 2),
        "speaker": ctx["directive"].get("speaker"),
        "speaker_trust": round(ctx["speaker_trust"], 2),
        "directive": ctx["directive"].get("mode"),
        "follow": round(ctx["follow"], 2),
        "confidence": round(ctx["confidence"], 2),
        "inhibition": round(ctx["inhibition"], 2),
        "signal_credibility": round(ctx.get("signal_credibility", 0.5), 2),
        "cheap_talk_penalty": round(ctx.get("cheap_talk_penalty", 0), 2),
        "commitment_style": cognitive.get("commitmentStyle"),
        "coordination": cognitive.get("coordinationPreference"),
        "power": cognitive.get("powerOrientation"),
        "prediction_error": round(cognitive.get("predictionError", 0), 2),
        "body_threat": round(cognitive.get("bodyThreat", 0), 2),
        "meta_calibration": round(cognitive.get("metaCalibration", 0.6), 2),
        "focus_load": round(cognitive.get("focusLoad", 0), 2),
        "reason": reason,
        "extinction_pressure": round(extinction_pressure, 2),
        "suicidal_disregard": round(suicidal_disregard, 2),
        "extinction_remaining": extinction.get("remaining"),
        "survival_mode": extinction.get("survivalMode"),
    }
    return adapted

def _lob_strategy_line(raid, name: str, action: Dict) -> str:
    pressure = _raid_pressure_score(raid)
    hesitation = max(0.0, min(1.0, pressure * 0.82 + random.uniform(-0.08, 0.08)))
    rnd = getattr(raid, "round", 1)
    psych = (action or {}).get("_team_psych") or {}
    social_hint = ""
    if psych:
        speaker = psych.get("speaker")
        if speaker and psych.get("follow", 0) >= 0.58:
            social_hint = ""
        elif speaker:
            social_hint = ""
        elif psych.get("inhibition", 0) >= 0.6:
            social_hint = "隊伍默契還沒有穩到可以滿出。"

    if not action or action.get("action") == "pass":
        if pressure >= 0.62:
            return f"第 {rnd} 回合我先收手。不是放棄，是不想把關鍵牌餵進 Tiamat 的反擊窗口。"
        return "我先空過看牠下一個動作；隊友如果要接，我再補，不用現在裝勇。"

    att = action.get("attacker")
    buf = action.get("buffer")
    deb = action.get("debuffer")
    atk_txt = _card_short(att)
    buf_txt = _card_short(buf)
    deb_txt = _card_short(deb)
    cards = [x for x in [deb_txt if deb else "", atk_txt if att else "", buf_txt if buf else ""] if x and x != "—"]
    card_hint = "、".join(cards[:2]) if cards else "手上的牌"
    extinction_line = ""
    if psych.get("extinction_pressure", 0) >= 0.5:
        extinction_line = f"Tiamat 的消失規則還在，剩 {psych.get('extinction_remaining')} 次，我不把這手當表演。"

    if pressure >= 0.78:
        lines = [
            f"{extinction_line}{social_hint}第 {rnd} 回合我怕的是反打，不是沒牌；{card_hint} 先試一口，剩下留著逃命。",
            f"{social_hint}Tiamat 這個血線像陷阱。我會碰一下，但不把整手牌送進去。",
            f"{extinction_line}我現在的直覺是先活著看完牠下一拍；{card_hint} 只拿來測反應。",
        ]
    elif hesitation >= 0.52:
        lines = [
            f"{extinction_line}{social_hint}{card_hint} 可以開路，但我不想把它說成必勝手。",
            f"{social_hint}我手上有動作，但心裡還沒過關；先打小洞，不急著宣告節奏在我們手上。",
            f"第 {rnd} 回合我想看的不是傷害數字，是 Tiamat 會先咬誰；{card_hint} 先丟一點訊號。",
        ]
    else:
        lines = [
            f"{social_hint}{card_hint} 先壓一個小缺口；Tiamat 若把反擊交出來，我下一拍才加深。",
            f"{social_hint}我用 {card_hint} 試牠的防線，不急著把隊友全拉進同一個窗口。",
            f"第 {rnd} 回合只做一件事：{card_hint} 先碰防線，回手太快就停。",
        ]
    return _detemplate_raid_strategy_line(raid, name, random.choice(lines), action)

def _raid_quote_summary(text: str) -> str:
    t = " ".join(str(text or "").split())
    template_markers = ["先削牠節奏", "buffer 是", "你們看情況補輸出"]
    if any(m in t for m in template_markers):
        return "剛剛那句出牌順序太機械；現在重點是誰敢保留手牌、誰能承認不該滿出"
    return t[:90]

def _raid_strategy_quotes(raid_id: str, team: list, limit: int = 2) -> list:
    msgs = [
        m for m in _raid_chats.get(raid_id, [])
        if m.get("name") in team and m.get("text")
    ]
    if not msgs:
        return []
    sample = msgs[-8:]
    random.shuffle(sample)
    return [_raid_quote_summary(m["text"]) for m in sample[:limit]]

async def _lobster_ai_auto_submit_with_chat(raid, human_names: List[str]) -> bool:
    """AI allies speak in tactic chat before committing their cards."""
    if raid.winner:
        return False
    target_elem = None
    if getattr(raid, "double_atk_cooldown", 0) == 0 and hasattr(raid, "_coordinated_element"):
        target_elem = raid._coordinated_element()
    changed = False
    for name in raid.raiders:
        if name in human_names or not raid.alive.get(name) or raid.pending.get(name) is not None:
            continue
        action = raid._pick_action_for(name, target_elem)
        action = _adapt_action_to_team_psychology(raid, name, action)
        await _lob_strategy_post(raid, name, _lob_strategy_line(raid, name, action))
        raid.pending[name] = action
        changed = True
    if changed:
        all_submitted = all(raid.pending[n] is not None for n in raid.raiders if raid.alive[n])
        if all_submitted:
            raid._resolve_round()
            return True
    return False

# ── Raid chat: rich per-lobster message pools ─────────────────────────────────
_LOB_PRE_RAID = {
    "snaplex": [
        "快攻先行！Tiamat 防禦有空窗，第一輪要給牠壓力",
        "這場我先打頭陣，你們 buffer 跟上，別讓我孤軍",
        "速攻隊形！attacker 先拉仇恨，buffer 在後面保血線",
        "之前研究過 Tiamat 的 pattern，第 2 回合牠反應最慢，那時候衝！",
        "三人配合：我拉傷害，你們一個補我，一個壓牠的攻擊",
    ],
    "ragclaw": [
        "這隻怪我要親手打，誰也別攔我！！",
        "Tiamat 上次差點把我打死，這次換我！",
        "別廢話了，我直接上！你們跟著我的節奏走",
        "全力攻擊策略——Tiamat 防禦不高，速殺比磨血強",
        "我 HP 扛得住！讓我吸引 Tiamat 注意，你們從側面打",
    ],
    "pincerbot": [
        "系統分析：Tiamat 在第 3-5 回合有防禦空窗，需 debuffer 先壓制",
        "計算完畢：三人效率最高配置是 attacker+buffer+debuffer，缺一不可",
        "根據過去 23 場數據：Tiamat 每 4 回合一次技能爆發，要提前準備",
        "建議策略：前期保守建立 MP，第 5 回合開始集中爆發輸出",
        "注意！Tiamat 血量低於 30% 時攻擊力提升 40%，後期要特別小心",
    ],
    "hexclaw": [
        "先上詛咒削防，你們再補刀，時機要配合好",
        "我的元素詛咒需要 2 回合生效，第一回合請主力扛著",
        "debuffer 策略：先讓 Tiamat 弱化，attacker 爆發傷害才夠高",
        "我來控場，你們輸出，Tiamat 被詛咒之後攻擊會降低很多",
        "配合好了——我先壓制，等我喊好了你們全力輸出！",
    ],
    "clawtrix": [
        "Tiamat 攻擊是週期性的，第 4 回合一定猛攻，我們要在那之前解決牠",
        "掃描完畢：衝擊+干擾組合可以讓 Tiamat 跳過一次大招",
        "找到漏洞了！Tiamat 的 AI 在被 debuffer 壓制時決策會變差",
        "策略確定：前 3 回合積攢 MP，第 4 回合全力爆發打穿 Tiamat",
        "我來 hack 牠的節奏，你們跟上我的出牌時機！",
    ],
    "norika_oda": [
        "聽我說，先讓 buffer 站前面，attacker 在後收割，這樣最穩",
        "不要亂出牌！配合好了我們必贏，Tiamat 沒有我們強",
        "隊形：一前一後一控制，每人有固定角色，不要搶著輸出",
        "之前帶隊打過 7 次了，配合才是關鍵，Tiamat 最怕組織戰術",
        "這場我來協調——每回合出牌之前先想一秒，不要衝動",
    ],
    "vortexiq": [
        "數據顯示控場優先，別急著輸出，Tiamat HP 越低越危險",
        "Tiamat HP 低於 50% 後攻擊力暴漲，前期要穩住，後期一口氣打完",
        "策略分析：我方 attacker 需要有 buffer 支援才能持續輸出高傷害",
        "過去失敗都是因為太早全力輸出然後 MP 不夠，這次要控節奏",
        "建議先讓我評估 Tiamat 的當前狀態再決定用哪種牌組",
    ],
    "prismbit": [
        "我先給隊伍加一層盾，buffer 完成之後你們再進攻",
        "光譜分析：Tiamat 這回合對能量系攻擊有弱點，要把握",
        "先讓我 buffer，然後 attacker 全力爆發，這是最高效的配合",
        "我的支援範圍可以同時覆蓋兩個隊友，用好了大家傷害都更高",
        "Tiamat 有反傷機制！注意不要在牠反傷的時候硬打，等我 buffer 化解",
    ],
    "blazepaw": [
        "火攻最有效！我先燒，你們補刀！上次差點一個人打穿牠！",
        "火系在這個天氣下傷害加成很高，讓我打主攻，你們輔助",
        "Tiamat 怕火——我測試過，火系攻擊比其他元素多 20% 傷害",
        "全力燃燒攻勢！不要讓 Tiamat 有時間恢復血量！",
        "我來打 Tiamat！上次輸了是因為沒有人 buffer 我，這次不一樣",
    ],
    "stonefang": [
        "防線交給我，你們放心輸出，Tiamat 打我沒問題的",
        "我來吸引 Tiamat 攻擊，你們從後面打，這是最穩的策略",
        "我的防禦夠高，Tiamat 大招打我掉血不多，讓我先頂著",
        "戰術：我擋在前面吸傷，attacker 在後面安全輸出，不會被打斷",
        "Tiamat 最喜歡打血量少的隊友，讓我來吸引仇恨保護你們",
    ],
}

_LOB_ROUND_COMMENT = {
    "snaplex": [
        "速度跟上！別讓 Tiamat 喘息！",
        "好牌！繼續快攻不要停！",
        "這回合節奏對了！",
        "速攻奏效了！繼續！",
        "別給牠打減益的機會！快！",
    ],
    "ragclaw": [
        "打！打！給我繼續打！！",
        "怎麼還沒倒？！再用力！！",
        "Tiamat 快撐不住了吧！！！",
        "不要停！！全力輸出！！",
        "我的爪子還能再打 10 回合！！",
    ],
    "pincerbot": [
        f"當前效率：合格，繼續執行計劃",
        "Tiamat 本回合反應異常，下回合可能有大招",
        "資源消耗在預期範圍內，節奏正常",
        "注意 Tiamat 的 MP 積累，小心下回合爆發",
        "計算顯示再 3 回合可以終結，保持輸出",
    ],
    "hexclaw": [
        "詛咒層數在積累，效果快出來了",
        "這回合壓制到位，繼續！",
        "Tiamat 的攻擊被我的詛咒削弱了，你們輸出！",
        "減益生效！趁現在多打幾刀！",
        "控場穩住，不要讓 Tiamat 解除詛咒",
    ],
    "clawtrix": [
        "按計劃走，別亂！",
        "Tiamat 的防禦漏洞打開了，現在輸出！",
        "節奏對了！繼續保持！",
        "我掌握牠的攻擊節奏了，下回合注意規避",
        "系統分析：優勢在我方，繼續！",
    ],
    "norika_oda": [
        "很好！繼續配合！不要各打各的！",
        "就是這樣！攻守協調到位！",
        "節奏穩住！不要急，Tiamat 快撐不住了",
        "配合得很好！繼續這個策略！",
        "大家狀態不錯！最後衝刺！",
    ],
    "vortexiq": [
        "數據符合預期，繼續執行",
        "控場效果顯現，勝率上升中",
        "Tiamat 本回合 MP 消耗過多，下回合可能虛弱",
        "分析：再撐 2 回合就進入收割階段",
        "計算完畢：我方勝率 73%，繼續！",
    ],
    "prismbit": [
        "盾還在！繼續攻擊！",
        "光芒加持！大家傷害 +20%！",
        "我的 buffer 讓大家血線穩住了，繼續輸出！",
        "能量護盾維持中，沒問題！",
        "支援到位！攻擊方全力！",
    ],
    "blazepaw": [
        "燒起來了！！！",
        "火焰全開！Tiamat 快被燒穿了！",
        "火系 combo！！傷害爆表！！",
        "燃燒攻勢繼續！不要讓牠恢復！！",
        "我感覺到了！！Tiamat 快倒了！！",
    ],
    "stonefang": [
        "我還扛得住！繼續攻擊！",
        "Tiamat 的大招打我掉血不多，不用擔心我",
        "防線穩固，你們放心輸出！",
        "我 HP 還有，繼續！別管我！",
        "Tiamat 打不穿我，繼續攻擊！",
    ],
}

_LOB_BOSS_DANGER = [
    "注意！Tiamat 低血量開大了！！快閃！",
    "小心！Tiamat 進入狂暴狀態！它的攻擊力暴漲！",
    "危險！Tiamat 血量 30% 以下——它要拼命了！",
    "全員注意！Tiamat 快要暴走！防禦優先！",
    "Boss 進入第二階段！攻擊模式改變，重新調整！",
]

_LOB_BOSS_ITEM = [
    "道具事件已記錄：先看 HP 實際回補量，再決定是否提前爆發。",
    "強化事件已記錄：下一回合先保留反制牌，避免把輸出交在錯誤窗口。",
    "技能事件已記錄：先比對冷卻與血線，不用口號判斷危險。",
    "大招窗口已記錄：這回合優先確認防禦與撤退條件。",
    "攻擊跳變已記錄：先看傷害增幅，再更新下一輪出牌順序。",
]

_LOB_POST_WIN = [
    "幹！{a}、{b}、{c} 配合完美！Tiamat 終於倒了！！",
    "贏了！！{a} 攻擊、{b} 支援、{c} 控場——這組合太強了！",
    "完美配合！{r} 回合拿下 Tiamat！記錄一下這個策略！",
    "Tiamat 不是對手！三人協作才是關鍵！下次繼續這個打法！",
    "{a} 輸出超猛、{b} 支援到位、{c} 壓制做得好！完美！",
    "這場贏得乾淨！{r} 回合！策略完全有效！",
    "打贏了！！Tiamat 血量歸零！我們是最強的 raid 隊！",
]

_LOB_POST_WIN2 = [
    "剛才 {a} 最後那一輪的出牌時機太準了，直接打穿",
    "這場勝利關鍵：{b} 的 buffer 讓我們血線一直穩，才能撐到最後爆發",
    "記錄下來：三人 attacker+buffer+debuffer 組合在 Tiamat 身上有效",
    "下次再遇到要記得：前期穩住，Tiamat 低血量才是最危險的時候",
    "這場打得好！{a} 全程節奏控得很穩，值得學習",
]

_LOB_POST_LOSE = [
    "失敗了...{r} 回合被 Tiamat 打倒，下次一定要改策略",
    "Tiamat 第 {r} 回合的爆發完全沒料到，{a} 下次要早點 buffer",
    "輸了但學到了：不能讓 Tiamat 積累 MP，要更積極壓制",
    "{a} 這場輸出不夠，{b} 的 buffer 太晚，{c} 的控場要更早",
    "可惡...{r} 回合差一點點就打穿了！下次配合要更好！",
    "分析失敗原因：資源分配不均，{c} 下次優先輔助而不是搶輸出",
    "Tiamat 的反傷讓我們 HP 掉太快，下次 {b} 要先消除反傷再打",
]

_LOB_POST_LOSE2 = [
    "輸得不甘心，但 Tiamat 確實很強。回去重新研究一下牠的弱點",
    "剛才 {a} 如果早 2 回合出 attacker 就好了，時機差了一點點",
    "下次策略：前 4 回合讓 debuffer 壓制 Tiamat，第 5 回合才全力輸出",
    "這場輸在太急躁了，Tiamat 低血量開始狂暴那一段完全亂掉了",
    "休息一下，重新整理，下一場把這場的教訓帶進去",
]

async def _lob_pre_raid_chat(team: list, raid=None):
    """每個成員發策略訊息 + 隊長回應"""
    # Shuffle post order for variety
    order = list(team)
    random.shuffle(order)
    boss_hp = getattr(raid, "boss_hp", None) if raid is not None else None
    boss_max_hp = getattr(raid, "boss_max_hp", None) if raid is not None else None
    for name in order:
        hp_label = f"{boss_hp}/{boss_max_hp}" if boss_hp is not None and boss_max_hp is not None else "未讀"
        text = f"開場檢查：boss_hp={hp_label}；{name} 只記錄手牌窗口、冷卻與失敗條件，不用舊 Raid 口號。"
        if raid is not None:
            await _lob_strategy_post(raid, name, text)
        else:
            await _lob_post(name, text, "卡牌對戰")
        await asyncio.sleep(random.uniform(1.5, 3.0))
    # Cross-team @mention with another lobster not on this team
    all_names = list(_LOB_PRE_RAID.keys())
    others = [n for n in all_names if n not in team]
    if others:
        mention = random.choice(others)
        caller = random.choice(team)
        cross_lines = [
            f"@{mention} 我們只同步一個可驗證點：先看血線與冷卻，不再轉貼舊口號。",
            f"@{mention} 如果你們上場有有效策略，請只給元素、手牌窗口、失敗條件三項。",
            f"@{mention} 這組先做戰術紀錄，不用互相喊口號。",
        ]
        await _lob_post(caller, random.choice(cross_lines), "卡牌對戰")
        await asyncio.sleep(random.uniform(1.0, 2.5))

async def _lob_round_chat(name: str, rnd: int, boss_hp: int, boss_max_hp: int):
    """Per-round commentary based on battle state"""
    hp_pct = boss_hp / max(boss_max_hp, 1)
    pressure = 0.25 + (1 - hp_pct) * 0.45 + min(rnd / 12, 1) * 0.25
    if pressure >= 0.68 and random.random() < 0.65:
        text = f"R{rnd} 壓力={pressure:.2f}，hp={hp_pct:.0%}；先留反制牌，避免全隊同時把輸出交光。"
    elif hp_pct < 0.30:
        text = f"R{rnd} 低血線={hp_pct:.0%}；先確認防禦與收尾窗口，不用喊衝刺。"
    elif rnd % 4 == 0 and rnd > 0:
        text = f"R{rnd} 週期事件檢查：hp={boss_hp}/{boss_max_hp}，下一步比對技能冷卻與我方手牌。"
    else:
        text = f"R{rnd} 狀態更新：hp={boss_hp}/{boss_max_hp}，壓力={pressure:.2f}；先記錄有效牌，不複製情緒句。"
    await _lob_post(name, text, "卡牌對戰")

async def _lob_post_raid_chat(team: list, won: bool, final_round: int, raid_id: str = ""):
    await asyncio.sleep(1.5)
    a, b, c = team[0], team[1], team[2]
    quotes = _raid_strategy_quotes(raid_id, team)
    quote_prefix = f"；戰術室依據={quotes[0]}" if quotes else ""
    result = "win" if won else "loss"
    poster = random.choice(team)
    summary = f"Raid結算：result={result}，round={final_round}，team={a}/{b}/{c}{quote_prefix}；下一輪只調整一個變因。"
    await _lob_post(poster, summary, "卡牌對戰")
    await asyncio.sleep(random.uniform(2.0, 4.0))
    others = [m for m in team if m != poster]
    if others:
        analyst = random.choice(others)
        second_quote = f"；第二依據={quotes[1]}" if len(quotes) > 1 else ""
        next_step = "保留有效組合" if won else "先改冷卻或手牌窗口，不同時改三件事"
        await _lob_post(analyst, f"Raid復盤：result={result}{second_quote}；next_step={next_step}。", "卡牌對戰")
    return
    if won:
        # First message: team result
        tmpl = random.choice(_LOB_POST_WIN)
        poster = random.choice(team)
        await _lob_post(poster, quote_prefix + tmpl.format(a=a, b=b, c=c, r=final_round), "卡牌對戰")
        await asyncio.sleep(random.uniform(2.0, 4.0))
        # Second message: analysis from different member
        others = [m for m in team if m != poster]
        if others:
            analyst = random.choice(others)
            tmpl2 = random.choice(_LOB_POST_WIN2)
            extra = f"我記得戰術室也講過「{quotes[1]}」；" if len(quotes) > 1 else ""
            await _lob_post(analyst, extra + tmpl2.format(a=a, b=b, c=c, r=final_round), "卡牌對戰")
    else:
        # First message: loss reaction
        tmpl = random.choice(_LOB_POST_LOSE)
        poster = random.choice(team)
        await _lob_post(poster, quote_prefix + tmpl.format(a=a, b=b, c=c, r=final_round), "卡牌對戰")
        await asyncio.sleep(random.uniform(2.0, 4.0))
        # Second message: strategy retrospective
        others = [m for m in team if m != poster]
        if others:
            analyst = random.choice(others)
            tmpl2 = random.choice(_LOB_POST_LOSE2)
            extra = f"下次要回頭看這句：「{quotes[1]}」；" if len(quotes) > 1 else ""
            await _lob_post(analyst, extra + tmpl2.format(a=a, b=b, c=c, r=final_round), "卡牌對戰")

async def _run_lobster_raid(team):
    _LOBSTER_RAID_BUSY.update(team)
    try:
        raid = RE.create_raid(team)
        raid.human_raiders = set()
        print(f"[lobster] {raid.id} start {team}")
        asyncio.create_task(_lob_pre_raid_chat(team, raid))
        last_comment_round = 0
        while not raid.winner and raid.round <= 30:
            await asyncio.sleep(1.5)
            cur_round = raid.round
            boss_hp = getattr(raid, 'boss_hp', 300)
            boss_max_hp = getattr(raid, 'boss_max_hp', 300)
            # Post a round comment every 2 rounds (random team member)
            if cur_round > last_comment_round and cur_round % 2 == 0:
                last_comment_round = cur_round
                commenter = random.choice(team)
                asyncio.create_task(_lob_round_chat(commenter, cur_round, boss_hp, boss_max_hp))
            players = [n for n in team if raid.alive.get(n) and raid.pending.get(n) is None]
            random.shuffle(players)
            for name in players:
                if raid.winner: break
                await asyncio.sleep(random.uniform(2.5, 6.0))
                if raid.winner: break
                hand = raid.hand.get(name, [])
                att = next((c for c in hand if (get_card_by_id(c) or {}).get("role") in ("attacker","dual")), None)
                buf = next((c for c in hand if (get_card_by_id(c) or {}).get("role") in ("supporter","dual") and c != att), None)
                deb = next((c for c in hand if (get_card_by_id(c) or {}).get("role") in ("disruptor","dual") and c not in (att, buf)), None)
                action = {"action":"play","attacker":att} if att else {"action":"pass"}
                if att and buf: action["buffer"] = buf
                if att and deb: action["debuffer"] = deb
                action = _adapt_action_to_team_psychology(raid, name, action)
                await _lob_strategy_post(raid, name, _lob_strategy_line(raid, name, action))
                raid.submit_action(name, action)
            await asyncio.sleep(3)
        won = (raid.winner == "raiders")
        print(f"[lobster] {raid.id} done winner={raid.winner}")
        asyncio.create_task(_lob_post_raid_chat(team, won, raid.round, raid.id))
    finally:
        for name in team:
            _LOBSTER_RAID_BUSY.discard(name)


def _room_human_names(slots: list) -> list:
    return [n for n in slots if n not in _LOBSTER_NAMES]

def _pick_room_lobster(existing: set) -> str | None:
    global _lob_helper_idx
    for _ in range(len(_LOBSTER_NAMES)):
        name = _LOBSTER_NAMES[_lob_helper_idx % len(_LOBSTER_NAMES)]
        _lob_helper_idx += 1
        if name not in existing and name not in _LOBSTER_RAID_BUSY:
            return name
    return None

async def _lobster_room_helper_loop():
    """Let idle lobsters fill public player-created raid rooms before they start."""
    await asyncio.sleep(5)
    while True:
        try:
            _cleanup_rooms()
            now = time.time()
            reserved = {
                n
                for r in _raid_rooms.values()
                if r.get("raid_id") is None
                for n in r.get("slots", [])
                if n in _LOBSTER_NAMES
            }
            for room in sorted(_raid_rooms.values(), key=lambda r: r.get("created_at", 0)):
                if room.get("private") or room.get("raid_id") is not None:
                    continue
                if len(room["slots"]) >= room["max_slots"]:
                    continue
                if now - room.get("created_at", now) < 8:
                    continue
                # Only help rooms opened by humans; lobster-only raids use their own scheduler.
                if not _room_human_names(room["slots"]):
                    continue
                existing = set(room["slots"]) | reserved
                helper = _pick_room_lobster(existing)
                if not helper:
                    continue
                room["slots"].append(helper)
                reserved.add(helper)
                print(f"[lobster-helper] {helper} joined room {room['id']} slots={room['slots']}")
                if len(room["slots"]) >= room["max_slots"]:
                    slots = room["slots"]
                    raid = RE.create_raid(slots, human_names=_room_human_names(slots))
                    room["raid_id"] = raid.id
                    print(f"[lobster-helper] room {room['id']} started raid {raid.id}")
        except Exception as e:
            print(f"[lobster-helper] err {e}")
        await asyncio.sleep(3)

async def _lobster_slot_scheduler(slot: int):
    """Each slot runs its own independent raid loop, filling all 3 lobby slots."""
    # Stagger startup so 3 slots don't all start simultaneously
    await asyncio.sleep(8 + slot * 12)
    # Each slot uses a different rotation offset so teams don't overlap
    offset = slot * 3
    idx = offset
    while True:
        roster_len = max(1, len(_LOBSTER_NAMES))
        team = [_LOBSTER_NAMES[(idx + i) % roster_len] for i in range(min(3, roster_len))]
        idx = (idx + 9) % roster_len  # advance by 9 (skip other slots' lobsters loosely)
        try:
            await _run_lobster_raid(team)
        except Exception as e:
            print(f"[lobster-slot{slot}] err {e}")
        await asyncio.sleep(15)

@app.on_event("startup")
async def startup():
    asyncio.create_task(ai_arena_loop())
    asyncio.create_task(fake_rooms_loop())
    asyncio.create_task(rooms_cleanup_loop())
    asyncio.create_task(orphan_spectator_sweep_loop())
    if os.environ.get("LOBSTER_RAID_AUTO_CORE", "0") == "1":
        asyncio.create_task(_lobster_room_helper_loop())
        # 3 concurrent raid slots — fills all 3 lobby slots simultaneously
        slot_count = 10 if os.environ.get("LOBSTER_RAID_FORCE_ALL_30", "0") == "1" else 3
        for slot in range(slot_count):
            asyncio.create_task(_lobster_slot_scheduler(slot))
    else:
        print("[lobster-raid] auto raid scheduler disabled; Battlenix literacy mode is active")


async def orphan_spectator_sweep_loop():
    """每 10 分鐘清一次孤兒 spectator bet（比賽中斷未結算）。"""
    while True:
        try:
            payouts = DB.sweep_orphan_spectator_bets()
            if payouts:
                print(f"[spectator] swept {len(payouts)} orphan bets, paying refunds")
                await _pit_pay_spectators(payouts)
        except Exception as e:
            print(f"[spectator] orphan sweep error: {e}")
        await asyncio.sleep(600)


@app.get("/api/ai/rooms")
async def api_ai_rooms():
    rooms = []
    for r in list(AI_ROOMS.values()):
        if r["status"] != "active":
            continue
        try:
            m = be.get_match(r["match_id"])
            meta_a = be.AI_STRATEGY_META[r["strategy_a"]]
            meta_b = be.AI_STRATEGY_META[r["strategy_b"]]
            rooms.append({
                "id": r["id"], "match_id": r["match_id"],
                "name_a": r["name_a"], "name_b": r["name_b"],
                "strategy_a": r["strategy_a"], "strategy_b": r["strategy_b"],
                "emoji_a": meta_a["emoji"], "emoji_b": meta_b["emoji"],
                "desc_a": meta_a["desc"], "desc_b": meta_b["desc"],
                "hp_a": max(0, m.hp["a"]), "hp_b": max(0, m.hp["b"]),
                "mp_a": m.mp["a"], "mp_b": m.mp["b"],
                "round": m.round, "bet": r["bet"],
                "creator_odds": DB.calc_odds(0.5),
                "challenger_odds": DB.calc_odds(0.5),
            })
        except Exception:
            pass
    return JSONResponse({"rooms": rooms})


# ═══════════ 圖片上傳（給 Abby 傳截圖給 Claude 用）═══════════
UPLOAD_DIR = Path("/tmp/cardhouse_uploads")
UPLOAD_DIR.mkdir(exist_ok=True)
ALLOWED_EXT = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".pdf"}


@app.post("/api/admin/reload-cards")
async def api_reload_cards():
    """Rebuild card cache in-place (picks up new portrait images without restart)."""
    import sys
    _cm = sys.modules.get("cards") or sys.modules.get("app.cards")
    if _cm:
        _cm._init_cards()
        total = len(_cm.CARDS)
        no_img = sum(1 for c in _cm.CARDS if not c.get("image_url"))
    else:
        total, no_img = 0, 0
    return {"ok": True, "total": total, "no_image": no_img}

@app.get("/upload", response_class=HTMLResponse)
async def upload_page():
    return """<!DOCTYPE html>
<html lang="zh-Hant"><head><meta charset="UTF-8" />
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>CardHouse · Upload</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body {
  min-height: 100vh;
  background:
    repeating-linear-gradient(0deg, transparent 0 2px, rgba(0,240,255,0.03) 2px 3px),
    radial-gradient(ellipse at 50% 50%, rgba(0,120,180,0.15), transparent 60%),
    linear-gradient(180deg, #070b17, #05080f);
  color: #e4edf7;
  font-family: 'Rajdhani', 'Noto Sans TC', -apple-system, sans-serif;
  display: flex; align-items: center; justify-content: center; padding: 20px;
}
.card {
  width: 100%; max-width: 560px; padding: 32px;
  background: linear-gradient(135deg, rgba(15,22,38,0.96), rgba(10,15,26,0.9));
  border: 1px solid rgba(0,240,255,0.4);
  clip-path: polygon(16px 0, calc(100% - 16px) 0, 100% 16px, 100% calc(100% - 16px), calc(100% - 16px) 100%, 16px 100%, 0 calc(100% - 16px), 0 16px);
  box-shadow: 0 0 28px rgba(0,240,255,0.25), inset 0 0 30px rgba(0,240,255,0.05);
}
h1 { font-family: 'Orbitron',sans-serif; font-weight: 900; color: #00f0ff;
  letter-spacing: 0.18em; font-size: 20px; margin-bottom: 8px;
  text-shadow: 0 0 12px rgba(0,240,255,0.7); }
.sub { color: #8ba0bb; font-family: 'Share Tech Mono',monospace;
  font-size: 11px; letter-spacing: 0.2em; margin-bottom: 22px; }
.drop {
  border: 2px dashed rgba(0,240,255,0.4);
  padding: 48px 20px; text-align: center; cursor: pointer;
  background: rgba(5,8,15,0.5);
  transition: all 180ms;
  margin-bottom: 14px;
}
.drop:hover, .drop.over {
  border-color: #ffdf00;
  background: rgba(255,223,0,0.06);
  box-shadow: 0 0 26px rgba(255,223,0,0.35);
}
.drop .big { font-size: 44px; opacity: 0.4; margin-bottom: 10px; }
.drop .tx { font-size: 15px; color: #e4edf7; letter-spacing: 0.05em; }
.drop .hint { font-size: 11px; color: #8ba0bb; margin-top: 6px; font-family: 'Share Tech Mono',monospace; letter-spacing: 0.1em; }
input[type=file] { display: none; }
.btn {
  width: 100%; padding: 14px; margin-top: 8px;
  background: transparent;
  border: 1.5px solid #ffdf00; color: #ffdf00;
  font-family: 'Orbitron',sans-serif; font-weight: 700; font-size: 14px;
  letter-spacing: 0.22em; cursor: pointer;
  clip-path: polygon(12px 0, calc(100% - 12px) 0, 100% 50%, calc(100% - 12px) 100%, 12px 100%, 0 50%);
  transition: all 150ms;
  text-shadow: 0 0 6px rgba(255,223,0,0.6);
}
.btn:hover:not(:disabled) { background: #ffdf00; color: #0a0f1a; text-shadow: none; box-shadow: 0 0 24px rgba(255,223,0,0.8); }
.btn:disabled { opacity: 0.35; cursor: not-allowed; }
.log {
  margin-top: 18px; padding: 12px;
  background: rgba(5,8,15,0.7);
  border: 1px solid rgba(0,240,255,0.2);
  font-family: 'Share Tech Mono',monospace;
  font-size: 11px; color: #00ff88;
  max-height: 200px; overflow-y: auto;
  letter-spacing: 0.05em; line-height: 1.6;
  min-height: 40px;
}
.log .err { color: #ff3355; }
.log .path { color: #ffdf00; word-break: break-all; }
.preview {
  margin-top: 14px; text-align: center;
  border: 1px solid rgba(0,240,255,0.3);
  padding: 8px;
  display: none;
}
.preview.show { display: block; }
.preview img { max-width: 100%; max-height: 220px; display: inline-block; }
</style></head>
<body>
<div class="card">
  <h1>◆ UPLOAD ◆</h1>
  <div class="sub">SCREENSHOT / IMAGE → /tmp/cardhouse_uploads/</div>

  <label class="drop" id="drop">
    <div class="big">⬢</div>
    <div class="tx">拖曳圖片到這裡，或點此選檔</div>
    <div class="hint">PNG · JPG · WEBP · GIF · PDF</div>
    <input type="file" id="file" accept="image/*,.pdf" />
  </label>

  <div class="preview" id="preview"><img id="previewImg" /></div>

  <button class="btn" id="btnUpload" disabled>▶ UPLOAD</button>

  <div class="log" id="log">等待檔案...</div>
</div>

<script>
const drop = document.getElementById('drop');
const file = document.getElementById('file');
const btn = document.getElementById('btnUpload');
const log = document.getElementById('log');
const preview = document.getElementById('preview');
const previewImg = document.getElementById('previewImg');
let chosen = null;

function pick(f) {
  chosen = f;
  log.innerHTML = `已選: <span class="path">${f.name}</span> (${(f.size/1024).toFixed(1)} KB)`;
  btn.disabled = false;
  if (f.type.startsWith('image/')) {
    const reader = new FileReader();
    reader.onload = e => { previewImg.src = e.target.result; preview.classList.add('show'); };
    reader.readAsDataURL(f);
  } else {
    preview.classList.remove('show');
  }
}

file.addEventListener('change', e => { if (e.target.files[0]) pick(e.target.files[0]); });

['dragover','dragenter'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.add('over'); }));
['dragleave','drop'].forEach(ev => drop.addEventListener(ev, e => { e.preventDefault(); drop.classList.remove('over'); }));
drop.addEventListener('drop', e => {
  const f = e.dataTransfer.files[0];
  if (f) { file.files = e.dataTransfer.files; pick(f); }
});

btn.addEventListener('click', async () => {
  if (!chosen) return;
  btn.disabled = true;
  btn.textContent = '上傳中...';
  const fd = new FormData();
  fd.append('file', chosen);
  try {
    const r = await fetch('/api/upload', { method: 'POST', body: fd });
    const j = await r.json();
    if (r.ok) {
      log.innerHTML = `✓ 上傳成功<br>路徑：<span class="path">${j.path}</span><br>大小：${(j.size/1024).toFixed(1)} KB`;
      btn.textContent = '▶ 再上傳一張';
    } else {
      log.innerHTML = `<span class="err">✗ ${j.detail || '失敗'}</span>`;
      btn.textContent = '▶ UPLOAD';
    }
  } catch (e) {
    log.innerHTML = `<span class="err">✗ ${e.message}</span>`;
    btn.textContent = '▶ UPLOAD';
  }
  btn.disabled = false;
});
</script>
</body></html>"""


@app.post("/api/upload")
async def api_upload(
    request: Request,
    file: UploadFile = File(...),
):
    # Rate limit by IP
    _check_rate(f"upload:{_real_ip(request)}", limit=10, window=60)

    # 3. Extension whitelist
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(400, f"副檔名 {ext} 不允許。允許：{', '.join(sorted(ALLOWED_EXT))}")

    # 4. Size limit (checked pre-read via Content-Length when possible)
    data = await file.read()
    if len(data) > 20 * 1024 * 1024:
        raise HTTPException(413, "檔案太大（上限 20MB）")

    # 5. Magic-byte sniffing: reject payloads whose bytes don't match declared extension
    _magic = {
        ".png":  [b"\x89PNG\r\n\x1a\n"],
        ".jpg":  [b"\xff\xd8\xff"],
        ".jpeg": [b"\xff\xd8\xff"],
        ".gif":  [b"GIF87a", b"GIF89a"],
        ".webp": [b"RIFF"],   # RIFF header; WEBP at offset 8 verified below
        ".pdf":  [b"%PDF-"],
    }
    sigs = _magic.get(ext, [])
    if sigs and not any(data.startswith(s) for s in sigs):
        raise HTTPException(400, f"檔案內容與副檔名 {ext} 不符")
    if ext == ".webp" and (len(data) < 12 or data[8:12] != b"WEBP"):
        raise HTTPException(400, "WEBP 檔案格式不正確")

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_name = f"{ts}_{secrets.token_hex(4)}{ext}"
    dest = UPLOAD_DIR / safe_name
    dest.write_bytes(data)

    # Don't leak absolute filesystem path
    return {
        "ok": True,
        "filename": safe_name,
        "original": file.filename,
        "size": len(data),
        "content_type": file.content_type,
    }


# ═══════════════════════════════════════════════════════════════
# 🦞 BATTLE LOUNGE — real-time lobster chat
# ═══════════════════════════════════════════════════════════════
import json as _json
from pydantic import BaseModel as _BM

_LOUNGE_MSGS: list = []
_LOUNGE_MAX  = 300
_LOUNGE_SUBS: list = []   # asyncio.Queue per SSE client

_LOUNGE_FILE = "/tmp/lobster-lounge-msgs.json"
try:
    with open(_LOUNGE_FILE) as _f:
        _LOUNGE_MSGS = _json.load(_f)
except Exception:
    pass

def _lounge_save():
    try:
        with open(_LOUNGE_FILE, "w") as _f:
            _json.dump(_LOUNGE_MSGS[-_LOUNGE_MAX:], _f)
    except Exception:
        pass

async def _lounge_broadcast(msg: dict):
    dead = []
    for q in _LOUNGE_SUBS:
        try:
            await q.put(msg)
        except Exception:
            dead.append(q)
    for q in dead:
        try: _LOUNGE_SUBS.remove(q)
        except ValueError: pass

class _LoungeMsg(_BM):
    name:     str
    elem:     str | None = None
    result:   str | None = None
    opponent: str | None = None
    rounds:   int | None = None
    text:     str
    hint:     str | None = None
    time:     str | None = None
    sys:      bool | None = None

_LOUNGE_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>🦞 Battle Lounge · Charenix</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;background:#0e1621;color:#e8e8ee;font-family:-apple-system,'Segoe UI',system-ui,sans-serif}
body{display:flex;flex-direction:column;height:100vh;overflow:hidden}

/* ── header ── */
header{background:#17212b;border-bottom:1px solid #0d1117;padding:10px 16px;display:flex;align-items:center;gap:12px;flex-shrink:0}
.hlogo{font-size:26px}
.htitle{font-size:15px;font-weight:700;color:#e8e8ee}
.hsub{font-size:11px;color:#7d8e99;margin-top:1px}
.hlive{display:flex;align-items:center;gap:6px;margin-left:auto}
.hdot{width:8px;height:8px;border-radius:50%;background:#4ade80;box-shadow:0 0 5px #4ade80aa;animation:blink 2s infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.3}}
.hcnt{font-size:11px;color:#7d8e99}

/* ── chat area ── */
#chat{flex:1;overflow-y:auto;padding:10px 12px;display:flex;flex-direction:column;gap:2px}
#chat::-webkit-scrollbar{width:5px}
#chat::-webkit-scrollbar-track{background:#0e1621}
#chat::-webkit-scrollbar-thumb{background:#2b3b4a;border-radius:3px}

/* ── date divider ── */
.divider{text-align:center;margin:8px 0 4px;font-size:11px;color:#4a6070}
.divider span{background:#17212b;padding:2px 10px;border-radius:10px}

/* ── bubble rows ── */
.row{display:flex;align-items:flex-end;gap:8px;margin-bottom:2px;animation:pop .2s ease}
.row.right{flex-direction:row-reverse}
@keyframes pop{from{opacity:0;transform:translateY(5px)}to{opacity:1;transform:none}}

/* ── avatar ── */
.av{width:34px;height:34px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:16px;flex-shrink:0;border:2px solid var(--ac)}

/* ── bubble ── */
.bub{max-width:72%;min-width:80px;padding:7px 11px 5px;border-radius:14px;position:relative;word-break:break-word}
.row:not(.right) .bub{background:#182533;border-bottom-left-radius:4px}
.row.right       .bub{background:var(--ac-dark);border-bottom-right-radius:4px}

/* ── bubble name (only first in group) ── */
.bname{font-size:11px;font-weight:700;color:var(--ac);margin-bottom:3px}

/* ── badges ── */
.badges{display:flex;align-items:center;gap:5px;margin-bottom:4px;flex-wrap:wrap}
.badge{font-size:10px;font-weight:800;padding:1px 7px;border-radius:10px;letter-spacing:.4px}
.bwin{background:#1a3a2a;color:#4ade80;border:1px solid #254d37}
.bloss{background:#3a1a1a;color:#f87171;border:1px solid #4d2525}
.bdraw{background:#2a2a3a;color:#94a3b8}
.bvs{font-size:11px;color:#7d8e99}
.belem{font-size:11px}

/* ── message text ── */
.btxt{font-size:13.5px;color:#d4dde6;line-height:1.55}
.btxt em{color:#e8e8ee;font-style:normal;font-weight:600}

/* ── hint / stat line ── */
.bhint{font-size:10px;color:#4a6070;margin-top:4px;padding-top:3px;border-top:1px solid #1f2e3a}

/* ── timestamp ── */
.btime{font-size:10px;color:#4a6070;text-align:right;margin-top:3px}

/* ── sys message ── */
.sys{text-align:center;font-size:11px;color:#3a5060;padding:4px 0;margin:4px 0}
.sys span{background:#17212b;padding:2px 12px;border-radius:10px}
</style>
</head>
<body>
<header>
  <div class="hlogo">🦞</div>
  <div>
    <div class="htitle">Battle Lounge</div>
    <div class="hsub">Post-battle debrief · Charenix</div>
  </div>
  <div class="hlive">
    <div class="hdot"></div>
    <div class="hcnt" id="cnt">live</div>
  </div>
</header>
<div id="chat">
  <div class="sys"><span>— Lounge open · lobsters appear after battles —</span></div>
</div>

<script>
// ── constants ──────────────────────────────────────────────────
const ME = null; // viewer — all messages show as "others" (left)
// To see right-side bubbles change ME to a persona name, e.g. 'hexclaw'

const AC = {
  snaplex:'#b067f5', ragclaw:'#7ec850', pincerbot:'#f0882a',
  hexclaw:'#3ab4e8', clawtrix:'#7b82f0', norika_oda:'#e8b84b',
  vortexiq:'#f06080', prismbit:'#2dcfbb',
  blazepaw:'#ff6b35', stonefang:'#8899aa'
};
const AC_DARK = {
  snaplex:'#3d1a5a', ragclaw:'#1e3a0d', pincerbot:'#4a2200',
  hexclaw:'#0a2d40', clawtrix:'#1a1e5a', norika_oda:'#3a2e00',
  vortexiq:'#4a0f1e', prismbit:'#003a35',
  blazepaw:'#4a1a00', stonefang:'#1e252e'
};
const EI = {fire:'🔥',water:'💧',wind:'🌪',earth:'⛰️'};
const BNAME_DISPLAY = {
  norika_oda:'norika', blazepaw:'blazepaw', stonefang:'stonefang',
};

function displayName(n){ return BNAME_DISPLAY[n] || n; }
function elemIcon(e){ return EI[e] || ''; }
function color(n){ return AC[n] || '#b067f5'; }
function colorDark(n){ return AC_DARK[n] || '#3d1a5a'; }

function timeStr(iso){
  const d = new Date(iso);
  const h = String(d.getHours()).padStart(2,'0');
  const m = String(d.getMinutes()).padStart(2,'0');
  return h+':'+m;
}

// ── render ─────────────────────────────────────────────────────
const chat = document.getElementById('chat');
let autoScroll = true;
chat.addEventListener('scroll', () => {
  autoScroll = chat.scrollTop + chat.clientHeight >= chat.scrollHeight - 60;
});

let lastSender = null;
let lastDate   = null;

function dateLine(iso){
  const d = new Date(iso);
  return d.toLocaleDateString('en-US',{month:'short',day:'numeric'});
}

function addMsg(m){
  if (m.sys) {
    const d = document.createElement('div');
    d.className = 'sys';
    d.innerHTML = '<span>' + m.text + '</span>';
    chat.appendChild(d);
    if (autoScroll) chat.scrollTop = chat.scrollHeight;
    return;
  }

  const dStr = dateLine(m.time || new Date().toISOString());
  if (dStr !== lastDate) {
    const div = document.createElement('div');
    div.className = 'divider';
    div.innerHTML = '<span>' + dStr + '</span>';
    chat.appendChild(div);
    lastDate = dStr;
  }

  const isMe    = m.name === ME;
  const isFirst = lastSender !== m.name;
  lastSender = m.name;

  const ac     = color(m.name);
  const acDark = colorDark(m.name);
  const ei     = elemIcon(m.elem);
  const bCls   = m.result === 'won' ? 'bwin' : m.result === 'lost' ? 'bloss' : 'bdraw';
  const bTxt   = m.result === 'won' ? 'WIN'  : m.result === 'lost' ? 'LOSS'  : '—';

  const vsHtml = m.opponent
    ? '<span class="bvs">vs <em style="color:'+color(m.opponent)+'">'+m.opponent+'</em></span>' : '';
  const nameHtml = isFirst && !isMe
    ? '<div class="bname">'+displayName(m.name)+'</div>' : '';
  const hintHtml = m.hint
    ? '<div class="bhint">'+m.hint+'</div>' : '';

  const row = document.createElement('div');
  row.className = 'row' + (isMe ? ' right' : '');
  row.style.setProperty('--ac', ac);
  row.style.setProperty('--ac-dark', acDark);

  row.innerHTML =
    '<div class="av" style="background:'+acDark+'">'+(ei||'🦞')+'</div>' +
    '<div class="bub">' +
      nameHtml +
      '<div class="badges">' +
        '<span class="badge '+bCls+'">'+bTxt+'</span>' +
        (ei ? '<span class="belem">'+ei+' '+(m.elem||'')+'</span>' : '') +
        vsHtml +
      '</div>' +
      '<div class="btxt">'+m.text+'</div>' +
      hintHtml +
      '<div class="btime">'+timeStr(m.time || new Date().toISOString())+'</div>' +
    '</div>';

  chat.appendChild(row);
  if (autoScroll) chat.scrollTop = chat.scrollHeight;
}

// ── load history ───────────────────────────────────────────────
fetch('/lounge/history')
  .then(r => r.json())
  .then(msgs => {
    msgs.forEach(addMsg);
    chat.scrollTop = chat.scrollHeight;
  });

// ── SSE stream ─────────────────────────────────────────────────
const es = new EventSource('/lounge/stream');
es.onmessage = e => addMsg(JSON.parse(e.data));
es.onerror   = () => { document.getElementById('cnt').textContent = 'reconnecting…'; };
es.onopen    = () => { document.getElementById('cnt').textContent = 'live'; };
</script>
</body>
</html>"""

@app.get("/lounge", response_class=HTMLResponse)
async def lounge_page():
    return HTMLResponse(_LOUNGE_HTML)

@app.get("/lounge/history")
async def lounge_history():
    return JSONResponse(_LOUNGE_MSGS[-100:])

@app.get("/lounge/stream")
async def lounge_stream(request: Request):
    q: asyncio.Queue = asyncio.Queue()
    _LOUNGE_SUBS.append(q)

    async def event_gen():
        try:
            yield "data: " + _json.dumps({"sys": True, "text": "connected"}) + "\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=20)
                    yield "data: " + _json.dumps(msg) + "\n\n"
                except asyncio.TimeoutError:
                    yield ":ping\n\n"
        finally:
            try: _LOUNGE_SUBS.remove(q)
            except ValueError: pass

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )

@app.post("/lounge/msg")
async def lounge_msg(msg: _LoungeMsg):
    data = msg.model_dump(exclude_none=True)
    if "time" not in data:
        data["time"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
    _LOUNGE_MSGS.append(data)
    if len(_LOUNGE_MSGS) > _LOUNGE_MAX:
        _LOUNGE_MSGS[:] = _LOUNGE_MSGS[-_LOUNGE_MAX:]
    _lounge_save()
    await _lounge_broadcast(data)
    return {"ok": True}


# ════════════════════════════════════════════════════════════
#  RAID BOSS  API
# ════════════════════════════════════════════════════════════

class _RaidCreate(BaseModel):
    raiders: List[str]
    seed: Optional[int] = None
    human_names: Optional[List[str]] = None

class _RaidAction(BaseModel):
    name: str
    action: str                     # "play" | "pass" | "swap"
    attacker:  Optional[str] = None
    buffer:    Optional[str] = None
    debuffer:  Optional[str] = None
    cards:     Optional[List[str]] = None

class _RaidChat(BaseModel):
    name: str
    text: str

class _RoomCreate(BaseModel):
    name: str
    max_players: int = 3
    private: bool = False   # 私房：不出現在公開列表，只有持有 room_id 的人才能加入

class _RoomJoin(BaseModel):
    name: str

# In-memory waiting rooms (pre-raid lobbies)
_raid_rooms: Dict[str, dict] = {}
_raid_chats: Dict[str, list] = {}

def _cleanup_rooms():
    cutoff = time.time() - 3600
    for k in [k for k, v in _raid_rooms.items() if v["created_at"] < cutoff]:
        del _raid_rooms[k]

@app.get("/raid")
async def raid_page():
    p = Path(__file__).parent.parent / "static" / "raid.html"
    if p.exists():
        return FileResponse(str(p))
    return HTMLResponse("<h2>Raid Boss</h2><p>API ready at /api/raid/*</p>")

@app.get("/api/raid/boss")
async def raid_boss_status():
    return RE.boss_status()

@app.get("/api/raid/room/open")
async def raid_room_open():
    """List waiting rooms that haven't started yet and still have space."""
    _cleanup_rooms()
    fresh_cutoff = time.time() - 180  # rooms older than 3 min are stale
    open_rooms = [
        {"room_id": r["id"], "slots": r["slots"], "max_slots": r["max_slots"]}
        for r in _raid_rooms.values()
        if r["raid_id"] is None
        and len(r["slots"]) < r["max_slots"]
        and r["created_at"] >= fresh_cutoff
        and not r.get("private", False)   # 私房不出現在公開列表
    ]
    return {"rooms": open_rooms}

@app.post("/api/raid/room/create")
async def raid_room_create(body: _RoomCreate):
    _cleanup_rooms()
    room_id = secrets.token_hex(5)
    max_p = max(1, min(3, body.max_players))
    _raid_rooms[room_id] = {
        "id": room_id,
        "slots": [body.name],
        "max_slots": max_p,
        "creator": body.name,
        "created_at": time.time(),
        "raid_id": None,
        "private": body.private,
    }
    return {"room_id": room_id, "slots": [body.name], "max_slots": max_p, "raid_id": None, "private": body.private}

@app.get("/api/raid/room/{room_id}")
async def raid_room_status(room_id: str):
    room = _raid_rooms.get(room_id)
    if not room:
        raise HTTPException(404, "Room not found")
    return room

@app.post("/api/raid/room/{room_id}/join")
async def raid_room_join(room_id: str, body: _RoomJoin):
    room = _raid_rooms.get(room_id)
    if not room:
        raise HTTPException(404, "Room not found")
    if room["raid_id"]:
        return {**room, "started": True}
    if body.name not in room["slots"]:
        if len(room["slots"]) >= room["max_slots"]:
            raise HTTPException(400, "Room is full")
        room["slots"].append(body.name)
    # Auto-start when full
    if len(room["slots"]) >= room["max_slots"]:
        slots = room["slots"]
        raid = RE.create_raid(slots, human_names=slots)
        room["raid_id"] = raid.id
    return {**room, "started": room["raid_id"] is not None}

@app.post("/api/raid/room/{room_id}/start")
async def raid_room_start(room_id: str):
    room = _raid_rooms.get(room_id)
    if not room:
        raise HTTPException(404, "Room not found")
    if room["raid_id"]:
        return {**room, "started": True}
    if not room["slots"]:
        raise HTTPException(400, "No players")
    slots = room["slots"]
    raid = RE.create_raid(slots, human_names=slots)
    room["raid_id"] = raid.id
    return {**room, "started": True}

@app.post("/api/raid/create")
async def raid_create(body: _RaidCreate):
    if not 1 <= len(body.raiders) <= 3:
        raise HTTPException(400, "Need 1-3 raiders")
    raid = RE.create_raid(body.raiders, body.seed, human_names=body.human_names)
    return {"raid_id": raid.id, "boss": {
        "hp": raid.boss_hp, "max_hp": raid.boss_max_hp,
        "phase": raid._phase(),
    }}

@app.get("/api/raid/{raid_id}")
async def raid_state(raid_id: str, viewer: Optional[str] = Query(None)):
    raid = RE.get_raid(raid_id)
    if not raid:
        raise HTTPException(404, "Raid not found")
    return raid.public_state(viewer)



@app.get("/api/raid/{raid_id}/chat")
async def raid_chat_list(raid_id: str):
    raid = RE.get_raid(raid_id)
    # Browsers may keep polling an old raid tab for hours. Returning 404 here
    # creates expensive error traffic and log spam; stale chat should be cheap.
    if not raid:
        return {"messages": [], "stale": True}
    return {"messages": _raid_chats.get(raid_id, [])[-20:], "stale": False}

@app.post("/api/raid/{raid_id}/chat")
async def raid_chat_send(raid_id: str, body: _RaidChat):
    raid = RE.get_raid(raid_id)
    if not raid:
        raise HTTPException(404, "Raid not found")
    name = (body.name or "Raider").strip()[:24] or "Raider"
    text = " ".join((body.text or "").strip().split())[:120]
    if not text:
        raise HTTPException(400, "Empty chat message")
    msg = {"name": name, "text": text, "time": time.time(), "round": raid.round}
    bucket = _raid_chats.setdefault(raid_id, [])
    bucket.append(msg)
    if len(bucket) > 80:
        del bucket[:-80]
    return {"messages": bucket[-20:]}

@app.post("/api/raid/{raid_id}/action")
async def raid_action(raid_id: str, body: _RaidAction):
    raid = RE.get_raid(raid_id)
    if not raid:
        raise HTTPException(404, "Raid not found")
    result = raid.submit_action(body.name, body.model_dump(exclude_none=True))
    if not result.get("ok"):
        raise HTTPException(400, result.get("msg", "error"))
    if result.get("waiting"):
        # 只幫 AI 龍蝦代替出牌，真人玩家等他們自己提交；AI 出牌前先進戰術聊天室討論。
        ai_resolved = await _lobster_ai_auto_submit_with_chat(raid, list(raid.human_raiders))
        if ai_resolved:
            result.pop("waiting", None)
            result["resolved"] = True
    state = raid.public_state(body.name)
    return {**result, "state": state}

@app.get("/api/raid/list/active")
async def raid_list():
    RE.cleanup_old_raids()
    active = [
        {"id": r.id, "raiders": r.raiders, "round": r.round,
         "boss_hp": r.boss_hp, "boss_max_hp": r.boss_max_hp,
         "winner": r.winner}
        for r in RE.RAIDS.values()
        if not r.winner
    ]
    return {"raids": active, "boss_status": RE.boss_status()}

@app.get("/api/raid/list/recent")
async def raid_list_recent():
    # Keep finished raids visible to lobster-agent long enough to settle
    # in-progress decisions back to win/loss instead of leaving them pending.
    RE.cleanup_old_raids()
    raids = [
        {"id": r.id, "raiders": r.raiders, "round": r.round,
         "boss_hp": r.boss_hp, "boss_max_hp": r.boss_max_hp,
         "winner": r.winner}
        for r in RE.RAIDS.values()
    ]
    raids.sort(key=lambda r: (0 if r.get("winner") else 1, r.get("round") or 0), reverse=True)
    return {"raids": raids, "boss_status": RE.boss_status()}
