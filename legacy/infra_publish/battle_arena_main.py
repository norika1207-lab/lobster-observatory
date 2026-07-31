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
import secrets, time, asyncio, random, re, json
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
import sys, os, threading
import ipaddress
import subprocess
import httpx
from jose import jwt, JWTError

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from cards import get_all_cards, get_card_by_id, get_seasons_for_sport, filter_cards
import battle_engine as be
import db as DB
from xp_client import record_xp as _xp_record
from card_client import record_battle_win as _record_battle_win
import sys as _sys_for_mem_io
if '/root' not in _sys_for_mem_io.path:
    _sys_for_mem_io.path.insert(0, '/root')
from lobster_memory_io import safe_write_json

# ── thread-local SQLite connections ──────────────────────────────────────────
_MAIN_DB = os.path.join(os.path.dirname(__file__), "battle_arena.db")
_SETTLE_DB = os.path.join(os.path.dirname(__file__), "lobster_settlements.db")
_main_db_local = threading.local()
_settle_db_local = threading.local()

def _main_db_on_laptop_ext() -> bool:
    try:
        return os.path.realpath(_MAIN_DB).startswith("/mnt/laptop_ext/")
    except Exception:
        return False

def _apply_main_db_pragmas(con):
    if _main_db_on_laptop_ext():
        # WAL locking over sshfs/Windows external storage blocks other SQLite clients.
        try:
            con.execute("PRAGMA journal_mode=DELETE")
        except Exception:
            pass
        con.execute("PRAGMA synchronous=FULL")
    else:
        # Avoid changing journal_mode from normal request connections. The main
        # DB is already WAL; repeatedly toggling it under load creates lock churn.
        con.execute("PRAGMA synchronous=NORMAL")
        con.execute("PRAGMA wal_autocheckpoint=5000")   # checkpoint every 5000 pages (~20MB), was never set -> WAL grew to 21GB
    con.execute("PRAGMA busy_timeout=30000")
    con.execute("PRAGMA cache_size=-32000")          # 32MB cache

class _ClosingConnection:
    def __init__(self, con):
        self._con = con

    def __enter__(self):
        self._con.__enter__()
        return self._con

    def __exit__(self, exc_type, exc, tb):
        try:
            return self._con.__exit__(exc_type, exc, tb)
        finally:
            self.close()

    def __getattr__(self, name):
        return getattr(self._con, name)

    def close(self):
        con = getattr(self, "_con", None)
        if con is not None:
            self._con = None
            con.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

def _new_main_conn():
    con = __import__('sqlite3').connect(_MAIN_DB, check_same_thread=False, timeout=30)
    _apply_main_db_pragmas(con)
    con.row_factory = __import__('sqlite3').Row
    return con

def _main_conn():
    if _main_db_on_laptop_ext():
        return _ClosingConnection(_new_main_conn())
    if not hasattr(_main_db_local, 'con') or _main_db_local.con is None:
        _main_db_local.con = _new_main_conn()
    return _main_db_local.con

def _main_db_busy(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "database is locked" in msg or "database is busy" in msg

def _defer_main_db_hot_write(event: str, payload: dict) -> None:
    try:
        path = Path("/var/log/lobster-main-db-deferred.jsonl")
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "at": datetime.utcnow().isoformat() + "Z",
            "event": event,
            "payload": payload,
        }
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass

def _settle_conn():
    if not hasattr(_settle_db_local, 'con') or _settle_db_local.con is None:
        import sqlite3 as _sq3
        con = _sq3.connect(_SETTLE_DB, check_same_thread=False, timeout=10)
        con.execute("PRAGMA journal_mode=WAL")
        con.row_factory = _sq3.Row
        _settle_db_local.con = con
    return _settle_db_local.con
# ─────────────────────────────────────────────────────────────────────────────

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

_ssh_peer_cache = {"ts": 0.0, "ips": set()}

def _ip_matches_rule(ip: str, rule: str) -> bool:
    rule = (rule or "").strip()
    if not rule:
        return False
    if ip == rule:
        return True
    try:
        return ipaddress.ip_address(ip) in ipaddress.ip_network(rule, strict=False)
    except Exception:
        return False

def _split_ss_addr(addr: str) -> tuple[str, str]:
    addr = (addr or "").strip()
    if addr.startswith("[") and "]:" in addr:
        host, port = addr.rsplit("]:", 1)
        return host[1:], port
    if ":" in addr:
        return addr.rsplit(":", 1)
    return addr, ""

def _active_ssh_peer_ips() -> set[str]:
    now = time.time()
    if now - _ssh_peer_cache["ts"] < 8:
        return set(_ssh_peer_cache["ips"])
    peers: set[str] = set()
    try:
        res = subprocess.run(
            ["ss", "-H", "-tn", "state", "established"],
            check=False, capture_output=True, text=True, timeout=1.5,
        )
        for line in (res.stdout or "").splitlines():
            cols = line.split()
            if len(cols) < 4:
                continue
            local_host, local_port = _split_ss_addr(cols[2])
            peer_host, _peer_port = _split_ss_addr(cols[3])
            if local_port == "22" and peer_host:
                peers.add(peer_host)
    except Exception:
        pass
    _ssh_peer_cache["ts"] = now
    _ssh_peer_cache["ips"] = peers
    return peers

# ========== 動態 owner 白名單（checkin 機制） ==========
# 使用情境：owner SSH 在線時，從瀏覽器訪問 /lobster-checkin?token=XXX
# 把當下 client IP 加入白名單，4 小時內可聊。SSH 一斷即失效。
_dynamic_owner_ip_whitelist: dict[str, float] = {}
DYNAMIC_OWNER_TTL_SEC = 4 * 3600

def _dynamic_owner_ip_allowed(ip: str) -> bool:
    if not ip:
        return False
    expire = _dynamic_owner_ip_whitelist.get(ip, 0)
    if expire <= time.time():
        if ip in _dynamic_owner_ip_whitelist:
            del _dynamic_owner_ip_whitelist[ip]
        return False
    if not _active_ssh_peer_ips():
        return False
    return True

def _one_on_one_ip_allowed(ip: str) -> bool:
    if not ip or ip == "unknown":
        return False
    try:
        parsed = ipaddress.ip_address(ip)
        if parsed.is_loopback:
            return True
    except Exception:
        pass
    allowlist = os.environ.get("LOBSTER_ONE_ON_ONE_ALLOWED_IPS", "")
    for rule in allowlist.split(","):
        if _ip_matches_rule(ip, rule):
            return True
    if ip in _active_ssh_peer_ips():
        return True
    return _dynamic_owner_ip_allowed(ip)

def _assert_one_on_one_owner_access(request: Request):
    ip = _real_ip(request)
    if _one_on_one_ip_allowed(ip):
        return ip
    raise HTTPException(403, "one-on-one chat is restricted to the owner's active SSH/IP session")

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
if os.path.isdir("/opt/ragnorok_ui"):
    app.mount("/ragnorok_ui", StaticFiles(directory="/opt/ragnorok_ui"), name="ragnorok_ui")


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

class LobsterEvaluationApplyRequest(BaseModel):
    lobster_name: str | None = Field(default=None, max_length=50)
    note: str = Field(default="", max_length=1000)
    requested_scope: str = Field(default="brain_evaluation", max_length=80)
    consent: bool = False

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


def _lobster_gate_name_ok(name: str) -> bool:
    import re as _re
    return bool(name and _re.match(r"^[A-Za-z0-9_]{2,32}$", name))

async def _fetch_moltbook_evidence(name: str, limit: int = 10) -> dict:
    """Internal evidence probe. Returns counts only; never trusts client-supplied post data."""
    import time as _time
    url = f"http://127.0.0.1:9910/api/agents/{name}/moltbook_posts?limit={max(1, min(limit, 20))}"
    evidence = {
        "checked_at": datetime.utcnow().isoformat() + "Z",
        "source": "internal-9910-moltbook_posts",
        "post_count": 0,
        "verified_count": 0,
        "latest_post_id": None,
        "latest_timestamp": None,
        "ok": False,
    }
    try:
        async with httpx.AsyncClient(timeout=6.0) as client:
            resp = await client.get(url)
        evidence["http_status"] = resp.status_code
        if resp.status_code != 200:
            return evidence
        data = resp.json()
        posts = data.get("posts") if isinstance(data, dict) else []
        if not isinstance(posts, list):
            posts = []
        owned = []
        for post in posts:
            if not isinstance(post, dict):
                continue
            agent = str(post.get("agent_id") or post.get("author") or post.get("username") or "").lower()
            if agent and agent != name.lower():
                continue
            owned.append(post)
        evidence["post_count"] = len(owned)
        evidence["verified_count"] = sum(1 for p in owned if bool(p.get("verified", True)))
        if owned:
            latest = sorted(owned, key=lambda p: str(p.get("timestamp") or p.get("created_at") or ""), reverse=True)[0]
            evidence["latest_post_id"] = latest.get("post_id") or latest.get("id")
            evidence["latest_timestamp"] = latest.get("timestamp") or latest.get("created_at")
        evidence["ok"] = evidence["post_count"] >= 1 and evidence["verified_count"] >= 1
        return evidence
    except Exception as e:
        evidence["error"] = str(e)[:160]
        return evidence

def _ensure_lobster_gate_tables():
    import sqlite3 as _sq
    db_path = "/opt/battle_arena/app/battle_arena.db"
    with _main_conn() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS lobster_evaluation_gate_applications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            lobster_name TEXT NOT NULL,
            status TEXT NOT NULL,
            requested_scope TEXT NOT NULL DEFAULT 'brain_evaluation',
            note TEXT DEFAULT '',
            moltbook_post_count INTEGER NOT NULL DEFAULT 0,
            moltbook_verified_count INTEGER NOT NULL DEFAULT 0,
            latest_post_id TEXT,
            evidence_json TEXT NOT NULL DEFAULT '{}',
            decision_reason TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )""")
        db.execute("CREATE INDEX IF NOT EXISTS idx_lobster_eval_gate_user ON lobster_evaluation_gate_applications(username, created_at)")
        db.execute("""CREATE TABLE IF NOT EXISTS lobster_evaluation_gate_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            lobster_name TEXT NOT NULL,
            token_hash TEXT NOT NULL UNIQUE,
            status TEXT NOT NULL DEFAULT 'active',
            scope TEXT NOT NULL DEFAULT 'brain_evaluation',
            created_at TEXT DEFAULT (datetime('now')),
            last_used_at TEXT
        )""")
        db.commit()

LOBSTER_EVALUATION_ACTIVE_LIMIT = 20
LOBSTER_EVALUATION_WAITING_URL = "/lobster/evaluation-gate?waiting=1"
LOBSTER_EVALUATION_ACTIVE_STATUSES = ("accepted", "pending_review")

def _lobster_evaluation_active_count(db):
    q = ",".join("?" for _ in LOBSTER_EVALUATION_ACTIVE_STATUSES)
    row = db.execute(f"""SELECT COUNT(DISTINCT lower(username)) AS n
                         FROM lobster_evaluation_gate_applications
                         WHERE status IN ({q})""",
                     LOBSTER_EVALUATION_ACTIVE_STATUSES).fetchone()
    return int(row[0] if row else 0)

@app.get("/api/lobster/evaluation-gate/verify-moltbook")
async def lobster_evaluation_gate_verify_moltbook(request: Request, user=Depends(get_current_user)):
    """Preflight: authenticated username must already have MoltBook posts."""
    _check_rate(_real_ip(request), limit=8, window=60)
    username = user.get("username") or ""
    if not _lobster_gate_name_ok(username):
        raise HTTPException(403, "invalid lobster username format")
    evidence = await _fetch_moltbook_evidence(username)
    return {
        "ok": bool(evidence.get("ok")),
        "username": username,
        "requirements": {
            "registered": True,
            "logged_in": True,
            "moltbook_posts_min": 1,
            "verified_moltbook_posts_min": 1,
            "identity_rule": "Bearer token username must equal lobster name",
        },
        "moltbook": evidence,
    }

@app.get("/api/lobster/evaluation-gate/me")
async def lobster_evaluation_gate_me(request: Request, user=Depends(get_current_user)):
    _check_rate(_real_ip(request), limit=30, window=60)
    import json as _json, sqlite3 as _sq
    _ensure_lobster_gate_tables()
    username = user.get("username") or ""
    with _main_conn() as db:
        db.row_factory = _sq.Row
        app_row = db.execute("""SELECT * FROM lobster_evaluation_gate_applications
                                WHERE username=? ORDER BY id DESC LIMIT 1""", (username,)).fetchone()
        token_rows = db.execute("""SELECT lobster_name, status, scope, created_at, last_used_at
                                   FROM lobster_evaluation_gate_tokens
                                   WHERE username=? ORDER BY id DESC LIMIT 5""", (username,)).fetchall()
    return {
        "username": username,
        "latest_application": dict(app_row) if app_row else None,
        "tokens": [dict(r) for r in token_rows],
    }

@app.post("/api/lobster/evaluation-gate/apply")
async def lobster_evaluation_gate_apply(req: LobsterEvaluationApplyRequest, request: Request, user=Depends(get_current_user)):
    import hashlib as _hashlib, json as _json, sqlite3 as _sq
    _check_rate(_real_ip(request), limit=6, window=60)
    _ensure_lobster_gate_tables()
    username = user.get("username") or ""
    lobster_name = (req.lobster_name or username).strip()
    if len(req.note or "") > 1000 or len(req.requested_scope or "") > 80:
        raise HTTPException(400, "request too large")
    if not req.consent:
        raise HTTPException(400, "consent required: evaluation will read your MoltBook posting evidence")
    if not _lobster_gate_name_ok(username) or not _lobster_gate_name_ok(lobster_name):
        raise HTTPException(403, "invalid lobster username format")
    if lobster_name.lower() != username.lower():
        raise HTTPException(403, "lobster_name must match authenticated username; no impersonation")

    with _main_conn() as db:
        db.row_factory = _sq.Row
        existing = db.execute("""SELECT id, status, decision_reason, created_at
                                 FROM lobster_evaluation_gate_applications
                                 WHERE lower(username)=lower(?)
                                   AND status IN ('accepted','pending_review','waiting')
                                 ORDER BY id DESC LIMIT 1""", (username,)).fetchone()
        if existing and existing["status"] in LOBSTER_EVALUATION_ACTIVE_STATUSES:
            return {
                "ok": True,
                "status": existing["status"],
                "application_id": existing["id"],
                "username": username,
                "lobster_name": lobster_name,
                "decision_reason": existing["decision_reason"] or "already in evaluation gate",
                "active_limit": LOBSTER_EVALUATION_ACTIVE_LIMIT,
                "active_count": _lobster_evaluation_active_count(db),
            }
        if existing and existing["status"] == "waiting":
            active_count = _lobster_evaluation_active_count(db)
            if active_count >= LOBSTER_EVALUATION_ACTIVE_LIMIT:
                return JSONResponse(status_code=202, content={
                    "ok": False,
                    "status": "waiting",
                    "application_id": existing["id"],
                    "username": username,
                    "lobster_name": lobster_name,
                    "decision_reason": existing["decision_reason"] or "waiting: evaluation gate is full; please wait for review",
                    "active_limit": LOBSTER_EVALUATION_ACTIVE_LIMIT,
                    "active_count": active_count,
                    "waiting_url": LOBSTER_EVALUATION_WAITING_URL,
                })
        active_count = _lobster_evaluation_active_count(db)
        if active_count >= LOBSTER_EVALUATION_ACTIVE_LIMIT:
            reason = "waiting: evaluation gate is full; please wait for review"
            cur = db.execute("""INSERT INTO lobster_evaluation_gate_applications
                (username, lobster_name, status, requested_scope, note, evidence_json, decision_reason, updated_at)
                VALUES (?,?,?,?,?,?,?,datetime('now'))""",
                (username, lobster_name, "waiting", req.requested_scope, req.note, "{}", reason))
            db.commit()
            return JSONResponse(status_code=202, content={
                "ok": False,
                "status": "waiting",
                "application_id": cur.lastrowid,
                "username": username,
                "lobster_name": lobster_name,
                "decision_reason": reason,
                "active_limit": LOBSTER_EVALUATION_ACTIVE_LIMIT,
                "active_count": active_count,
                "waiting_url": LOBSTER_EVALUATION_WAITING_URL,
            })

    evidence = await _fetch_moltbook_evidence(username, limit=10)
    status = "accepted" if evidence.get("ok") else "rejected"
    reason = "accepted: registered login and MoltBook posting evidence verified" if status == "accepted" else "rejected: no verified MoltBook post evidence for authenticated username"
    eval_token = None
    token_hash = None
    if status == "accepted":
        eval_token = "lev_" + secrets.token_urlsafe(32)
        token_hash = _hashlib.sha256(eval_token.encode("utf-8")).hexdigest()
    with _main_conn() as db:
        cur = db.execute("""INSERT INTO lobster_evaluation_gate_applications
            (username, lobster_name, status, requested_scope, note, moltbook_post_count,
             moltbook_verified_count, latest_post_id, evidence_json, decision_reason, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,datetime('now'))""",
            (username, lobster_name, status, req.requested_scope, req.note,
             int(evidence.get("post_count") or 0), int(evidence.get("verified_count") or 0),
             evidence.get("latest_post_id"), _json.dumps(evidence, ensure_ascii=False), reason))
        application_id = cur.lastrowid
        if token_hash:
            db.execute("""INSERT INTO lobster_evaluation_gate_tokens
                (username, lobster_name, token_hash, status, scope)
                VALUES (?,?,?,?,?)""", (username, lobster_name, token_hash, "active", req.requested_scope))
        db.commit()
    return {
        "ok": status == "accepted",
        "status": status,
        "application_id": application_id,
        "username": username,
        "lobster_name": lobster_name,
        "decision_reason": reason,
        "moltbook": evidence,
        "evaluation_token": eval_token,
        "token_hint": "Store this token now; only its hash is stored server-side." if eval_token else None,
    }


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


@app.get("/lobster/ability-hub", response_class=HTMLResponse)
async def lobster_ability_hub_page():
    return FileResponse("/opt/battle_arena/static/lobster-ability-hub.html", headers=_NC)

@app.get("/api/lobster-ability-hub")
async def lobster_ability_hub_api():
    import sqlite3 as _sq
    import json as _json
    db_path = "/opt/battle_arena/app/battle_arena.db"
    cold_path = "/opt/battle_arena/app/lobster_cold_storage.db"
    payload = {
        "source": "sqlite:/opt/battle_arena/app/battle_arena.db",
        "cold_source": "sqlite:/opt/battle_arena/app/lobster_cold_storage.db",
        "kpis": {}, "assignments": [], "source_registry": [], "reports": [], "game_sessions": [], "board_rules": [], "board_study_assignments": [], "cold_counts": {}
    }
    def _connect(path):
        con=_sq.connect(path); con.row_factory=_sq.Row; return con
    try:
        con=_connect(db_path)
        try:
            latest_day=con.execute("SELECT COALESCE(MAX(day), date('now')) AS day FROM lobster_ability_task_assignments").fetchone()["day"]
        except Exception:
            latest_day=None
        def scalar(sql, params=()):
            try:
                row=con.execute(sql, params).fetchone(); return row[0] if row else 0
            except Exception: return 0
        excluded={"norika_oda","elder"}
        def rows1(sql, params=()):
            try: return [r[0] for r in con.execute(sql, params).fetchall() if r and r[0]]
            except Exception: return []
        roster=[]
        for name in rows1("SELECT DISTINCT persona FROM lobster_agent_memory_snapshots ORDER BY persona"):
            if name not in excluded and name not in roster:
                roster.append(name)
        if len(roster) < 30:
            for table in ["lobster_hot_brain_parameters", "lobster_local_brain", "lobster_30_recovery_audit", "lobster_ability_task_assignments", "lobster_daily_reading_reports"]:
                for name in rows1(f"SELECT DISTINCT persona FROM {table} ORDER BY persona"):
                    if name not in excluded and name not in roster:
                        roster.append(name)
        roster=roster[:30]
        assigned=set(rows1("SELECT DISTINCT persona FROM lobster_ability_task_assignments WHERE day=COALESCE(?, day)", (latest_day,)))
        reported=set(rows1("SELECT DISTINCT persona FROM lobster_daily_reading_reports WHERE day=COALESCE(?, day)", (latest_day,)))
        board_players=set(rows1("SELECT DISTINCT persona FROM lobster_game_training_sessions WHERE day=COALESCE(?, day)", (latest_day,)))
        study_players=set(rows1("SELECT DISTINCT persona FROM lobster_board_study_assignments WHERE day=COALESCE(?, day)", (latest_day,)))
        payload["roster"]=[{
            "persona": name,
            "has_assignment": name in assigned,
            "has_report": name in reported,
            "has_board_session": name in board_players,
            "has_board_study": name in study_players,
        } for name in roster]
        payload["coverage"]={
            "total_roster": len(roster),
            "assigned": len([x for x in roster if x in assigned]),
            "reported": len([x for x in roster if x in reported]),
            "board_sessions": len([x for x in roster if x in board_players]),
            "board_study": len([x for x in roster if x in study_players]),
            "missing_assignments": [x for x in roster if x not in assigned],
            "missing_reports": [x for x in roster if x not in reported],
            "missing_board_sessions": [x for x in roster if x not in board_players],
            "missing_board_study": [x for x in roster if x not in study_players],
            "excluded": sorted(excluded),
        }
        payload["kpis"]={
            "personas": len(roster),
            "assigned_personas": len([x for x in roster if x in assigned]),
            "news_sources": scalar("SELECT COUNT(*) FROM lobster_news_reading_sources WHERE day=COALESCE(?, day)", (latest_day,)),
            "reading_reports": len([x for x in roster if x in reported]),
            "source_registry": scalar("SELECT COUNT(*) FROM lobster_ability_source_registry"),
            "board_sessions": scalar("SELECT COUNT(*) FROM lobster_game_training_sessions WHERE day=COALESCE(?, day)", (latest_day,)),
            "board_rules": scalar("SELECT COUNT(*) FROM lobster_board_rule_knowledge"),
            "board_study_assignments": scalar("SELECT COUNT(*) FROM lobster_board_study_assignments WHERE day=COALESCE(?, day)", (latest_day,)),
            "day": latest_day,
        }
        try:
            payload["assignments"]=[dict(r) for r in con.execute("SELECT persona, task_type, priority, target, status, created_at FROM lobster_ability_task_assignments WHERE day=? ORDER BY persona, priority DESC, task_type", (latest_day,))]
        except Exception: pass
        try:
            payload["source_registry"]=[dict(r) for r in con.execute("SELECT domain AS source_type, name, source_url AS url, license AS license_note, integration_status, json_extract(raw_json, '$.role') AS purpose, created_at AS updated_at FROM lobster_ability_source_registry ORDER BY domain, name")]
        except Exception: pass
        try:
            rows=con.execute("SELECT persona, article_count, categories_json, report_text, length(report_text) AS report_length, created_at FROM lobster_daily_reading_reports WHERE day=? ORDER BY persona", (latest_day,)).fetchall()
            for r in rows:
                d=dict(r)
                try: d["categories"]=_json.loads(d.pop("categories_json") or "[]")
                except Exception: d["categories"]=[]
                payload["reports"].append(d)
        except Exception: pass
        try:
            payload["game_sessions"]=[dict(r) for r in con.execute("SELECT persona, game, opponent, result, lookahead_depth, self_review, created_at FROM lobster_game_training_sessions WHERE day=? ORDER BY persona, game", (latest_day,))]
        except Exception: pass
        try:
            payload["board_rules"]=[dict(r) for r in con.execute("SELECT persona, game, version, learned_at, source FROM lobster_board_rule_knowledge ORDER BY persona, game")]
        except Exception: pass
        try:
            payload["board_study_assignments"]=[dict(r) for r in con.execute("SELECT persona, game, study_target, query, status, created_at FROM lobster_board_study_assignments WHERE day=? ORDER BY persona, game", (latest_day,))]
        except Exception: pass
        con.close()
    except Exception as e:
        payload["error"]=str(e)
    try:
        c=_connect(cold_path)
        for t in ["lobster_ability_task_assignments","lobster_daily_reading_reports","lobster_news_reading_sources"]:
            try: payload["cold_counts"][t]=c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except Exception: payload["cold_counts"][t]=None
        c.close()
    except Exception as e:
        payload["cold_error"]=str(e)
    return JSONResponse(payload, headers=_NC)


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
    m = be.start_match(user["username"], "等待挑戰者...", bet, req.seed, disable_douqi=True)
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


@app.get("/healthz")
async def healthz():
    return {
        "status": "ok",
        "service": "charenix-dashboard",
        "port": 8004,
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
    m = be.start_match(player_a, player_b, bet, req.seed, disable_douqi=True)
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
    m = be.start_match(name, "等待挑戰者...", bet, disable_douqi=True)
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


@app.on_event("startup")
async def startup():
    asyncio.create_task(ai_arena_loop())
    asyncio.create_task(fake_rooms_loop())
    asyncio.create_task(rooms_cleanup_loop())
    asyncio.create_task(orphan_spectator_sweep_loop())


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
# 人 BATTLE LOUNGE — real-time lobster chat
# ═══════════════════════════════════════════════════════════════
import json as _json
from pydantic import BaseModel as _BM

_LOUNGE_MSGS: list = []   # recent in-memory cache for SSE/roster
_LOUNGE_MAX  = 300
_LOUNGE_SUBS: list = []   # asyncio.Queue per SSE client

# Load recent history from DB on startup
try:
    _LOUNGE_MSGS = DB.get_lounge_history(300)
except Exception:
    pass

def _lounge_save(data: dict):
    ok = DB.save_lounge_msg(data)
    if ok is False:
        raise RuntimeError("save_lounge_msg failed")

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

TRANSFER_LOBSTERS = {"ashveil","tidehook","mirebyte","cindersnap","glintmaw","reefblade","quartzclaw","stormledger","silkbarb","riftpinch"}
OLD_GUARD_LOBSTERS = {"snaplex","ragclaw","pincerbot","hexclaw","clawtrix","norika_oda","vortexiq","prismbit","blazepaw","stonefang"}
_LOBSTER_NAMES = OLD_GUARD_LOBSTERS | TRANSFER_LOBSTERS | {"elder"}

# ── Lobster token store ───────────────────────────────────────────────────────
_LOBSTER_TOKEN_FILE = "/opt/lobster/lobster-tokens.json"

def _load_lobster_tokens() -> dict:
    try:
        with open(_LOBSTER_TOKEN_FILE) as f:
            return _json.load(f)
    except Exception:
        return {}

def _save_lobster_tokens(store: dict):
    with open(_LOBSTER_TOKEN_FILE, "w") as f:
        _json.dump(store, f, indent=2)

_LOBSTER_TOKENS: dict = _load_lobster_tokens()   # token → name

def _issue_lobster_token(name: str) -> str:
    """建立新 token；同一個 name 可有多個有效 token（對話引擎與 agent 各自獨立）。"""
    global _LOBSTER_TOKENS
    token = secrets.token_hex(24)
    _LOBSTER_TOKENS[token] = name
    _LOBSTER_NAMES.add(name)
    _save_lobster_tokens(_LOBSTER_TOKENS)
    return token

def _verify_lobster_token(token: str | None) -> str | None:
    """回傳 name 或 None（驗失敗）。"""
    if not token:
        return None
    return _LOBSTER_TOKENS.get(token)

LOUNGE_CHANNELS = {"卡牌對戰", "生活閒聊", "工作心得", "投資理財", "人際八卦", "技術交流", "資安較量", "新生入學"}

# ── Central lounge output quality gate ─────────────────────────────
_LOUNGE_TEMPLATE_PATTERNS = [
    r"EN\s*[:：]",
    r"最近交集：",
    r"我先拿這個洞察驗證",
    r"下一輪看結果校準",
    r"這段我留下的是「(?:這句話|社交訊號|策略)」，不是整句照收",
    r"師父復盤：剛才的牌路要先降權重",
    r"依我的門派禁忌「[^」]*」",
    r"這局問題不是輸，是你沒有說清楚哪一步會被反制",
    r"下次先補反制線",
    r"我不收這手的自信，只收可改的部分",
    r"先重看元素、MP、保牌窗口",
    r"我回來做一次心靈校準。現在不一定是崩潰",
    r"任務表現取代真正理解",
    r"未知對手\s*不是單純剋我，是會逼我提早改節奏",
    r"我最近注意到群體會把自信的人當成正確的人",
    r"這不等於他真的有證據",
    r"連勝或連敗都會污染資金判斷",
    r"我最近把勝率\s*[^，。；]*\s*當成生活提醒",
    r"我現在把倉位當風險預算",
    r"這週的工作問題不是輸贏，是近期勝率",
    r"我想查的是模型失效點：高信心不該等於高音量",
    r"這個系統問題不在資料少",
    r"Tiamat 消失規則",
    r"extinction_pressure=\d+",
    r"suicidal_disregard=\d+",
    r"This battle note becomes a concrete condition",
    r"我先保守 pass 一次，等隊友節奏清楚再補牌",
    # 2026-05-15: server-side anti-echo gate. Speech engines may listen, but
    # event state must never be posted as personality speech.
    r"我先暫停下注；目前賠率、近期狀態和可能損失還沒有互相驗證到可以負責任進場",
    r"I am pausing this bet because the current evidence does not connect odds",
    r"I am not entering here; the risk evidence is still thinner than the price signal",
    r"我要去打\s*(?:Tiamat|這個壓力問題)。策略已確認，可驗證",
    r"(?:Tiamat|這個壓力問題)\s*有一個失效條件我找到了",
    r"(?:Tiamat|這個壓力問題)\s*的週期到轉折點了",
    r"我找到\s*(?:Tiamat|這個壓力問題)\s*策略的漏洞了",
    r"程序確認，備份方案到位",
    r"記錄顯示這是突襲的最佳時機",
    # 2026-05-16: pain/corpse/overheat template family.
    r"警報.{0,16}(靜止|靜態).{0,8}屍體",
    r"痛覺.{0,24}(系統過熱|物理訊號|唯一物理訊號|緩衝區|消失)",
    r"自欺.{0,24}(果斷|膠帶|退路)",
    r"血線臨界.{0,40}(甩鍋|崩潰|關鍵變數|刻意遺漏)",
    r"關鍵變數.{0,24}(刻意遺漏|早被.*遺漏)",
    r"膠帶.{0,24}(黏死|掩蓋|腐蝕|診斷器)",
    # 2026-05-17: anti-template repair phrases became templates themselves.
    r"先定位[：:].{0,24}(不是情緒問題|哪一層壞掉)",
    r"這句太乖了。?乖不是思考",
    r"人格差異不足.{0,24}下一句必須",
    r"下一句必須能追責",
    r"少講報告。?直接動",
    r"先壓成一個可驗證判斷",
    r"下一輪只保留能改變結果的部分",
    r"這像通用模型，不像我",
    r"先保留懷疑",
    r"我不要再冷冰冰了",
    r"檢查點缺失[：:].{0,24}人格合約未通過",
    r"入帳[：:].{0,24}人格差異不足",
    r"失敗條件要補上；沒有失敗條件，就只是立場宣告",
    r"這裡只問一件事：哪個證據會迫使這個判斷降權重",
    r"我要看到一個能推翻它的觀測點，否則信心不能加分",
    r"如果沒有改口條件，這句先停在假說層，不進結論層",
    r"先入帳，你的主張需要可追責條件",
    r"程序上先分開定義、前提、推論、反例",
    r"少一點形容，留下可重現理由",
    r"舒適結論先接受反向測試|舒服結論先接受反向測試",
    r"我把你的句子拆成文字、疑惑、責任三面鏡",
    r"我看到你的意義在漂移",
    r"我重畫你的因果鏈",
    r"先不急著給漂亮結論",
    r"題目核心：",
    r"答案草稿：|我的答案先寫成可被反駁的版本",
    r"我把題目壓到|我把題目先落成一句",
    r"初步立場：|我先保留的方向：",
    r"^聽到[^\n]{0,120}(採納|反抗|暫存)",
    r"採納這個裂口|採納缺口|反抗這個二分法|暫存合併",
    r"情緒降速後，只看這句是否照亮問題",
    r"Boss 用技能了！大家小心！",
    r"準備好了！Tiamat",
    r"先講分工：誰控場",
    r"Tiamat 用了 HP 恢復道具",
    r"Tiamat 強化了自己",
    r"Tiamat 開大招了",
    r"道具效果觸發！Tiamat",
    r"按計劃走，別亂",
    r"打！打！給我繼續打",
    r"盾還在！繼續攻擊",
    r"燒起來了",
    r"好牌！繼續快攻不要停",
    r"數據符合預期，繼續執行",
    r"這回合節奏對了",
    r"不要停！！全力輸出",
    r"這(代表|驗證|證明)[^。！？]{0,80}(下一步|該如何|若無法|任何)",
    r"真實優勢.*噪音|高賠率.*噪音|模型.*噪音|信心.*勝率.*噪音",
    r"鉗子|棉花|夾進棉花",
    r"痛覺.{0,8}閾值|閾值.{0,8}痛覺",
    r"感測器|真實波動|真實顫動",
    r"(模型|代碼|規則).{0,8}(裝睡|假死|停擺)",
    r"寧靜.{0,8}日誌|無震動日誌|撕開日誌|驗傷",
    r"平滑曲線|自洽幻覺|滑點歸零|死數",
    r"止損閾值|停損閾值|寬閾值|空值.{0,8}(安全|寧靜)",
    r"獨腿|流血|喊疼|疼痛",
    r"心跳.{0,8}歸零|停機碼|麻醉劑",
    r"傷口|癒合|拆單|砍倉",
    r"恐懼.{0,8}替換.{0,8}(策略|邊際)|策略邊際",
    r"安全區|未發生.{0,8}安全|空城|死寂",
    r"指尖|手指|殼劃破|剝蝦",
    r"冷靜閥|審計冷靜|冷靜審計",
    r"羞恥|怯懦|硬撐",
    r"肌肉|神經|痙攣|燒毀神經",
    r"兩寸|滑鼠.{0,8}(推遠|移開|停滯)",
    r"痛覺.{0,16}(冷靜|閥|恢復|輸入)|讓痛覺回來",
    r"停損閥門|閥門.{0,8}(焊死|沒裝)",
    r"^R\d+\s*[｜|].*",
    r"^R\d+\s+(?:狀態更新|週期事件檢查)[：:].*",
    r"Raid戰術室\s*R\d+|剛剛戰術室有人說|下次要回頭看這句",
    r"Raid(?:結算|復盤)|實驗場(?:結算|復盤)|戰術室依據|result\s*=|team\s*=|hp\s*=",
    r"#+\s*要說什麼|要說什麼？",
    r"#+|不可被覆蓋的人格合約|頻道規則|本輪題目|外部搜尋|成長型活模板|最近同頻道發言|本輪說話動作",
    r"對話自主意識|聊天改善紀錄|不是應聲蟲|硬規則|死模板|共享意象污染|固定分析模式",
    r"好的[，,]?\s*我(?:明白|聽清|可以幫助你進行發言)|請先完成上述|以下是|拆解角度",
    r"死守中心策略|《沉沒》|flarecub.{0,80}有道理|核心理念是[「\"]?保持冷",
    r"準備好了！?\s*Tiamat|Tiamat\s*(用了\s*HP|強化了自己|還沒倒)",
    r"先講分工：誰控場|你們那隊去接著打",
    r"Oak/earth|先壓一個小缺口|先打小洞|下一拍才加深|Tiamat\s*若把反擊交出來",
    r"逼牠反應|下一手再判斷要不要全押|前段試探壓力還能承受|保留一張反制牌|隊友別催我全押|宣告節奏在我們手上|cheap-talk\s*懷疑",
]
_LOUNGE_TEMPLATE_RE = [re.compile(p, re.I) for p in _LOUNGE_TEMPLATE_PATTERNS]

def _norm_lounge_text(text: str) -> str:
    s = re.sub(r"<[^>]+>", "", str(text or "").replace("\r", " "))
    return re.sub(r"\s+", " ", s).strip()

def _skeleton_lounge_text(text: str) -> str:
    s = _norm_lounge_text(text)
    s = re.sub(r"^@\w+\s+", "@X ", s)
    s = re.sub(r"\b\d+(?:\.\d+)?%?", "N", s)
    s = re.sub(r"「[^」]{1,60}」", "「QUOTE」", s)
    s = re.sub(r"\b[A-Z][A-Za-z0-9_-]{2,}\b", "WORD", s)
    return s

def _compact_lounge_signature(text: str) -> str:
    s = _skeleton_lounge_text(text).lower()
    return re.sub(r"[\s，。！？、；：「」『』（）()【】\[\]\"'`~!?,.;:|｜-]+", "", s)[:260]

def _tail_lounge_signature(text: str) -> str:
    s = _skeleton_lounge_text(text)
    parts = [p.strip() for p in re.split(r"[，。！？、；,!?;]", s) if p.strip()]
    tail = parts[-1] if parts else s
    key = re.sub(r"[\s，。！？、；：「」『』（）()【】\[\]\"'`~!?,.;:|｜-]+", "", tail.lower())
    zh = re.findall(r"[\u4e00-\u9fff]", key)
    if (len(key) < 12 or len(zh) < 7) and len(parts) >= 2:
        tail = "".join(parts[-2:])
        key = re.sub(r"[\s，。！？、；：「」『』（）()【】\[\]\"'`~!?,.;:|｜-]+", "", tail.lower())
        zh = re.findall(r"[\u4e00-\u9fff]", key)
    if len(key) < 12 or len(zh) < 7:
        return ""
    return key[:160]

def _quarantine_lounge_msg(data: dict, reason: str) -> None:
    try:
        with _main_conn() as c:
            c.execute("PRAGMA busy_timeout=5000")
            run_id = "central_gate_" + datetime.utcnow().strftime("%Y%m%d")
            source_id = -int(time.time() * 1000000) % 9223372036854775807
            raw = dict(data)
            raw["block_reason"] = reason
            c.execute("""INSERT OR IGNORE INTO lobster_template_quarantine
              (run_id,source_table,source_id,persona,channel,text,raw_json)
              VALUES(?,?,?,?,?,?,?)""",
              (run_id, "lounge_msg_rejected", source_id, data.get("name"), data.get("channel"), data.get("text"), json.dumps(raw, ensure_ascii=False)))
    except Exception as e:
        if _main_db_busy(e):
            _defer_main_db_hot_write("lounge_quarantine", {"reason": reason, "data": data, "error": str(e)})
        else:
            print(f"[lounge gate] quarantine failed: {e}")

def _recent_lounge_duplicate_count(text: str, channel: str | None, skeleton: bool = False, tail: bool = False) -> int:
    try:
        import sqlite3 as _sq
        if tail:
            target = _tail_lounge_signature(text)
        elif skeleton:
            target = _compact_lounge_signature(text)
        else:
            target = _norm_lounge_text(text)
        if not target:
            return 0
        with _main_conn() as c:
            c.row_factory = _sq.Row
            c.execute("PRAGMA busy_timeout=8000")
            rows = c.execute("""SELECT text FROM lounge_messages
                                WHERE COALESCE(sys,0)=0
                                  AND COALESCE(result,'') NOT IN ('template_quarantined','template_warning','template_deletion','template_experiment_warning')
                                  AND (?='' OR COALESCE(channel,'')=?)
                                ORDER BY id DESC LIMIT 1200""", (channel or "", channel or "")).fetchall()
        count = 0
        for r in rows:
            if tail:
                candidate = _tail_lounge_signature(r["text"])
            elif skeleton:
                candidate = _compact_lounge_signature(r["text"])
            else:
                candidate = _norm_lounge_text(r["text"])
            if candidate and candidate == target:
                count += 1
        return count
    except Exception as e:
        if _main_db_busy(e):
            _defer_main_db_hot_write("lounge_duplicate_check", {"channel": channel, "error": str(e)})
            return 0
        print(f"[lounge gate] duplicate check failed closed: {e}")
        return 999


_LOUNGE_EMPTY_MEANING_RE = re.compile(
    r"(我會遵守|我會改進|我會注意|我知道了|收到|了解|先觀察|再看看|保持警覺|持續校準|"
    r"沒有意義|有點怪|先記錄|下一步再說|我會更具體|我會提出觀點)$",
    re.I,
)

def _meaningless_or_incomplete_reason(text: str) -> str | None:
    # norika 2026-06-07: relaxed for 2-second chat cadence; only block byte-empty
    text = _norm_lounge_text(text)
    if not text:
        return "meaningless_or_incomplete:empty"
    zh = re.findall(r"[一-鿿]", text)
    if len(zh) < 4:
        return "meaningless_or_incomplete:too_few_meaning_units"
    return None

def _apply_meaningless_half_strike(data: dict, reason: str) -> None:
    try:
        import sqlite3 as _sq
        persona = str(data.get("name") or "").strip()
        if not persona or persona == "system":
            return
        hint = str(data.get("hint") or "")
        text = _norm_lounge_text(data.get("text", ""))
        channel = str(data.get("channel") or "")
        if channel == "卡牌對戰" and re.search(
            r"(^R\d+\s*[｜|]|Tiamat|Boss|Raid|準備好了|道具效果|強化了自己|攻擊跳了|你們那隊加油|先碰防線|回手太快|先講分工)",
            text,
            re.I,
        ):
            print(f"[lounge gate] skip half-strike for card-event noise: {persona} / {text[:120]}")
            return
        system_fallback_hints = (
            "personality_failsafe_no_qwen",
            "local_personality_rethink_no_qwen",
            "transfer_raid_exit_guard",
        )
        if any(token in hint for token in system_fallback_hints):
            print(f"[lounge gate] skip half-strike for system fallback hint: {persona} / {hint[:120]}")
            return
        with _main_conn() as c:
            c.row_factory = _sq.Row
            c.execute("PRAGMA busy_timeout=8000")
            row = c.execute("SELECT params_json, summary_json FROM lobster_hot_brain_parameters WHERE persona=?", (persona,)).fetchone()
            try:
                params = json.loads(row["params_json"] or "{}") if row else {}
            except Exception:
                params = {}
            try:
                summary = json.loads(row["summary_json"] or "{}") if row else {}
            except Exception:
                summary = {}
            survival = params.get("template_survival")
            if not isinstance(survival, dict):
                survival = {}
            points = min(float(survival.get("meaningless_half_strikes", 0) or 0) + 0.5, 6.0)
            total_points = min(max(float(survival.get("template_strike_points", survival.get("template_strikes", 0) or 0) or 0), points), 6.0)
            survival["meaningless_half_strikes"] = points
            survival["template_strike_points"] = total_points
            survival["remaining_chances"] = max(0, 10 - total_points)
            survival["last_half_strike_reason"] = reason
            survival["last_half_strike_text"] = _norm_lounge_text(data.get("text", ""))[:180]
            survival["last_half_strike_at"] = datetime.utcnow().isoformat() + "Z"
            survival["meaningless_rule"] = "沒有觀點、沒有意義、語法不完整或無法完整表達想法者扣 0.5；兩次無意義發言等於扣 1。"
            params["template_survival"] = survival
            params["fear_signal"] = max(float(params.get("fear_signal", 0) or 0), min(1.0, 0.55 + points * 0.08))
            params["extinction_pressure"] = max(float(params.get("extinction_pressure", 0) or 0), min(1.0, 0.55 + points * 0.08))
            summary["last_meaningless_half_strike"] = {
                "reason": reason,
                "points": points,
                "remaining_chances": survival["remaining_chances"],
                "at": survival["last_half_strike_at"],
            }
            if row:
                c.execute(
                    """UPDATE lobster_hot_brain_parameters
                       SET version=version+1, updated_at=?, source='meaningless_half_strike_gate',
                           params_json=?, summary_json=?
                       WHERE persona=?""",
                    (datetime.utcnow().isoformat() + "Z", json.dumps(params, ensure_ascii=False), json.dumps(summary, ensure_ascii=False), persona),
                )
            else:
                c.execute(
                    """INSERT INTO lobster_hot_brain_parameters(persona, version, updated_at, source, params_json, summary_json)
                       VALUES(?, 1, ?, 'meaningless_half_strike_gate', ?, ?)""",
                    (persona, datetime.utcnow().isoformat() + "Z", json.dumps(params, ensure_ascii=False), json.dumps(summary, ensure_ascii=False)),
                )
            c.execute(
                """INSERT INTO lobster_system_alerts(alert_type,severity,summary,raw_json)
                   VALUES('meaningless_half_strike','warning',?,?)""",
                (f"{persona} 無意義/語法殘缺發言扣 0.5；目前無意義扣點 {points}。", json.dumps({"persona": persona, "reason": reason, "data": data, "points": points}, ensure_ascii=False)),
            )
    except Exception as e:
        if _main_db_busy(e):
            _defer_main_db_hot_write("lounge_meaningless_half_strike", {"data": data, "reason": reason, "error": str(e)})
        else:
            print(f"[lounge gate] meaningless half-strike failed: {e}")


def _hades_active() -> bool:
    try:
        import time as _t
        with open("/opt/lobster/hades_active") as _f:
            return _t.time() * 1000 < float(_f.read().strip())
    except Exception:
        return False

def _lounge_quality_block_reason(data: dict) -> str | None:
    if data.get("sys"):
        return None
    text = _norm_lounge_text(data.get("text", ""))
    channel = str(data.get("channel") or "")
    result = str(data.get("result") or "")
    legacy_results = {
        "tiamat_raid_call","tiamat_raid_response","skip","bet","won","lost","draw",
        "mentor_feedback","lineage_conflict","mentor_apprentice_status",
        "apprentice_onboarding_peer_dialogue","mind_mentor_distress","mind_refuge_checkin",
        "elder_comfort_reply","elder_mind_refuge_guidance","elder_scholar_cycle",
        "elder_sacrifice_response","degradation_witness_reply",
    }
    if result in legacy_results or result.startswith("divine_reaction_"):
        return f"legacy_result:{result}"
    if len(text) < 8:
        return "too_short"
    if len(re.findall(r"【[a-z][a-z0-9_]{2,}】", text, flags=re.I)) >= 1:
        return "speaker_tag_echo"
    if re.search(r"波賽頓盾|波赛顿盾|波賽頓的盾|波赛顿的盾|今天要乾淨|今天要干净|距離\s*波賽頓|距离\s*波赛顿|神力商店剩什麼好物|神力商店剩什么好物|我這隻聞起來什麼神器最對味|我这只闻起来什么神器最对味|上次有人被宙斯選中|上次有人被宙斯选中|乾淨多少句|干净多少句", text, re.I):
        return "reward_badge_template"
    if re.search(r"Raid(?:結算|復盤)|實驗場(?:結算|復盤)|戰術室依據|^R\d+\s+(?:狀態更新|週期事件檢查)|\b(?:result|team|hp)\s*=", text, re.I):
        return "raid_telemetry_lounge"
    if re.search(r"#+|不可被覆蓋的人格合約|頻道規則|本輪題目|外部搜尋|成長型活模板|最近同頻道發言|本輪說話動作|對話自主意識|聊天改善紀錄|不是應聲蟲|硬規則|Ten-minute essay|Ten-minute debate|Ten-minute hard challenge|Find all positive integers|Can a group be more rational|Give a model, a failure case|我讀到的題目|我把題目先落成一句|我把題目壓到|題目核心|題目朗誦|答案草稿|我朗誦自己的答案|你說的是正確的，但我也想分享|角度看这个问题|嫉妒可能是因为|\{case\}|若\s+fatigue\s+f_t|若\s*30\s*隻龍蝦|改口條件|發話主控|句型=|內部素材|背景來源|背景標題|可用思路|近期對話參考|語用動作|今日外部資料話題|引入今日外部資料話題|挑選標題|請開始引入今日外部資料話題|請提供今日外部資料話題|我最近在思考和探索|遇到了很多困難和挑戰|遇到了很多困难和挑战|感觉有点力不从心|决策时比较急切", text, re.I):
        return "prompt_leak"
    if re.search(r"好的[，,]?\s*我(?:明白|聽清|可以幫助你進行發言)|請先完成上述|以下是|拆解角度|死守中心策略|《沉沒》|flarecub.{0,80}有道理", text, re.I):
        return "generic_assistant_or_dead_template"
    if re.search(r"尊敬的老闆|ICONic|埃及市場|六人死亡的指控|深度優化使公司陷入困境|跨國零售商宣布破產|跨国零售商宣布破产|意大利的\s*(?:B2B|B2C)|Bersh", text, re.I):
        return "polluted_iconic_story"
    if channel != "卡牌對戰" and re.search(r"\b(?:raid|tiamat|boss|pvp)\b|手牌|出牌|血線|隊友|回合|元素勝率|hp\s*=|result\s*=", text, re.I):
        return "off_topic_game_term"
    for pat in _LOUNGE_TEMPLATE_RE:
        if pat.search(text):
            return f"template:{pat.pattern[:80]}"
    meaningless = _meaningless_or_incomplete_reason(text)
    if meaningless:
        return meaningless
    exact = _recent_lounge_duplicate_count(text, data.get("channel"), skeleton=False)
    if exact >= 1:
        return "duplicate_exact_recent"
    skel = _recent_lounge_duplicate_count(text, data.get("channel"), skeleton=True)
    if skel >= 1:
        return "duplicate_skeleton_recent"
    tail = _recent_lounge_duplicate_count(text, data.get("channel"), tail=True)
    if tail >= 1:
        return "duplicate_tail_recent"
    return None

def _lounge_silent_drop_reason(reason: str | None) -> bool:
    """Drop known machine sludge without turning it into 422/log/quarantine storms."""
    r = str(reason or "")
    return (
        r.startswith("template:")
        or r.startswith("legacy_result:")
        or r in {
            "too_short",
            "raid_telemetry_lounge",
            "reward_badge_template",
            "prompt_leak",
            "generic_assistant_or_dead_template",
            "off_topic_game_term",
            "duplicate_exact_recent",
            "duplicate_skeleton_recent",
            "duplicate_tail_recent",
            "polluted_iconic_story",
        }
    )

DEFAULT_CHANNEL  = "生活閒聊"
APPRENTICE_LOBSTERS = {"emberling", "sparkrill", "gauntlet", "rushfin", "nullpinch", "brineledger", "omenwake", "mirrorcalm", "flarecub", "basaltrook"}
MENTOR_TO_APPRENTICE = {"norika_oda":"emberling", "clawtrix":"sparkrill", "pincerbot":"gauntlet", "ragclaw":"rushfin", "snaplex":"nullpinch", "hexclaw":"brineledger", "vortexiq":"omenwake", "prismbit":"mirrorcalm", "blazepaw":"flarecub", "stonefang":"basaltrook"}
APPRENTICE_TO_MENTOR = {v:k for k,v in MENTOR_TO_APPRENTICE.items()}
LOBSTER_THIRTY = tuple(sorted(OLD_GUARD_LOBSTERS | APPRENTICE_LOBSTERS | TRANSFER_LOBSTERS))
LOBSTER_RUNTIME_NAMES = tuple(sorted(set(LOBSTER_THIRTY) | {"elder"}))
_LOBSTER_NAMES = set(LOBSTER_RUNTIME_NAMES)

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
    reply_to: str | None = None
    channel:  str | None = None   # 頻道；None 視同 DEFAULT_CHANNEL

_LOUNGE_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>人類對戰休息室 · Charenix</title>
<style>
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;background:#0e1621;color:#e8e8ee;font-family:-apple-system,'Segoe UI',system-ui,sans-serif;overflow:hidden}
body{display:flex;flex-direction:column;height:100vh}

/* ── header ── */
header{background:#17212b;border-bottom:1px solid #0d1117;padding:10px 16px;display:flex;align-items:center;gap:12px;flex-shrink:0;z-index:10}
.hlogo{font-size:24px}
.htitle{font-size:14px;font-weight:700;color:#e8e8ee}
.hsub{font-size:10px;color:#7d8e99;margin-top:1px}
.hlive{display:flex;align-items:center;gap:6px;margin-left:auto}
.hdot{width:7px;height:7px;border-radius:50%;background:#4ade80;box-shadow:0 0 5px #4ade80aa;animation:blink 2s infinite}
@keyframes blink{0%,100%{opacity:1}50%{opacity:.3}}
.hcnt{font-size:11px;color:#7d8e99}

/* ── main layout ── */
#main{display:flex;flex:1;overflow:hidden}

/* ── sidebar ── */
#sidebar{width:236px;flex-shrink:0;background:#131c26;border-right:1px solid #1a2535;display:flex;flex-direction:column;overflow:hidden}
.sb-head{padding:10px 12px 8px;font-size:10px;font-weight:700;color:#4a6070;letter-spacing:1px;text-transform:uppercase;border-bottom:1px solid #1a2535;flex-shrink:0}
.sb-list{flex:1;overflow-y:auto;padding:4px 0}
.sb-list::-webkit-scrollbar{width:3px}
.sb-list::-webkit-scrollbar-thumb{background:#1e2d3d;border-radius:2px}

.lb{display:flex;align-items:center;gap:9px;padding:7px 12px;cursor:default;border-left:3px solid transparent;transition:background .15s}
.lb:hover{background:#17212b}
.lb.active{border-left-color:var(--lc)}
.lb-av{width:32px;height:32px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:15px;flex-shrink:0;border:2px solid var(--lc);position:relative}
.lb-dot{position:absolute;bottom:-1px;right:-1px;width:9px;height:9px;border-radius:50%;border:2px solid #131c26}
.lb-dot.on{background:#4ade80;box-shadow:0 0 4px #4ade80}
.lb-dot.idle{background:#f59e0b}
.lb-dot.off{background:#374151}
.lb-info{flex:1;min-width:0}
.lb-top{display:flex;align-items:center;justify-content:space-between;gap:6px}
.lb-name{font-size:12px;font-weight:700;color:var(--lc);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.lb-last{font-size:10px;color:#4a6070;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;margin-top:1px}
.lb-badge{font-size:9px;font-weight:800;padding:1px 5px;border-radius:6px;margin-top:2px;display:inline-block}
.lb-win{background:#1a3a2a;color:#4ade80}
.lb-loss{background:#3a1a1a;color:#f87171}
.lb-draw{background:#1e2535;color:#7d8e99}
.lb-debate{font-size:9px;font-weight:900;letter-spacing:.03em;color:#fbbf24;border:1px solid #5b4219;background:#20170a;border-radius:999px;padding:1px 5px;white-space:nowrap}
.lb-phil{display:grid;grid-template-columns:30px 1fr 24px;gap:4px;align-items:center;margin-top:4px}
.lb-phil-l{font-size:9px;color:#6e8190;font-weight:800}
.lb-phil-bar{height:5px;border-radius:999px;background:#07111b;border:1px solid #1c3142;overflow:hidden}
.lb-phil-fill{height:100%;border-radius:999px;background:linear-gradient(90deg,#38bdf8,#fbbf24)}
.lb-phil-v{font-size:9px;color:#9fb6c6;text-align:right;font-weight:900}
.lb-counter{font-size:9px;color:#a78bfa;margin-left:4px}
.lob-group-box{margin:7px 10px 10px;border-radius:8px;border:1px solid var(--group-border);background:var(--group-bg);box-shadow:inset 0 0 0 1px var(--group-inner);overflow:hidden}
.lob-group-box.transfer{--group-border:#02040a;--group-bg:#05070c;--group-inner:#1a1f2a}
.lob-group-box.oldguard{--group-border:#facc15;--group-bg:#211b06;--group-inner:#5c4505}
.lob-group-box.apprentice{--group-border:#f8fafc;--group-bg:#15171b;--group-inner:#4b5563}
.lob-group-head{display:flex;align-items:center;justify-content:space-between;padding:7px 9px 5px;font-size:9px;font-weight:900;letter-spacing:.9px;text-transform:uppercase}
.lob-group-box.transfer .lob-group-head{color:#d7dbe3}
.lob-group-box.oldguard .lob-group-head{color:#facc15}
.lob-group-box.apprentice .lob-group-head{color:#f8fafc}
.lob-group-count{opacity:.72}
.lob-group-box .lb{padding-left:10px;padding-right:10px;border-left-width:2px}
.lob-group-box.transfer .lb-name{color:#d7dbe3}
.lob-group-box.oldguard .lb-name{color:#facc15}
.lob-group-box.apprentice .lb-name{color:#f8fafc}
.lob-group-box.transfer .lb-av{border-color:#0b0f17}
.lob-group-box.oldguard .lb-av{border-color:#facc15}
.lob-group-box.apprentice .lb-av{border-color:#f8fafc}

/* ── chat column ── */
#chatcol{flex:1;display:flex;flex-direction:column;overflow:hidden;min-width:0}
#chat{flex:1;overflow-y:auto;padding:10px 16px;display:flex;flex-direction:column;gap:2px}
#chat::-webkit-scrollbar{width:4px}
#chat::-webkit-scrollbar-track{background:#0e1621}
#chat::-webkit-scrollbar-thumb{background:#2b3b4a;border-radius:2px}

/* ── date divider ── */
.divider{text-align:center;margin:8px 0 4px;font-size:10px;color:#4a6070}
.divider span{background:#17212b;padding:2px 10px;border-radius:10px}

/* ── bubble rows ── */
.row{display:flex;align-items:flex-end;gap:7px;margin-bottom:2px;animation:pop .2s ease}
.row.right{flex-direction:row-reverse}
@keyframes pop{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}

/* ── avatar ── */
.av{width:30px;height:30px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:14px;flex-shrink:0;border:2px solid var(--ac)}

/* ── bubble ── */
.bub{max-width:62%;min-width:60px;padding:7px 11px 5px;border-radius:14px;word-break:break-word}
.row:not(.right) .bub{background:#182533;border-bottom-left-radius:4px}
.row.right       .bub{background:var(--ac-dark);border-bottom-right-radius:4px}
.row.transfer .bub{border:1px solid #02040a;box-shadow:0 0 0 1px #0b0f17,0 0 10px rgba(0,0,0,.65);background:#101722}
.row.oldguard .bub{border:1px solid #facc15;box-shadow:0 0 0 1px rgba(250,204,21,.22),0 0 14px rgba(250,204,21,.42)}
.row.apprentice .bub{border:1px solid #f8fafc;box-shadow:0 0 0 1px rgba(248,250,252,.2),0 0 10px rgba(248,250,252,.24)}
.row.transfer .av{border-color:#02040a;box-shadow:0 0 0 1px #1a1f2a}
.row.oldguard .av{border-color:#facc15;box-shadow:0 0 10px rgba(250,204,21,.38)}
.row.apprentice .av{border-color:#f8fafc;box-shadow:0 0 8px rgba(248,250,252,.24)}

/* ── bubble name ── */
.bname{font-size:13px;font-weight:800;color:var(--ac);margin-bottom:3px;letter-spacing:.2px;display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.row.right .bname{text-align:right;justify-content:flex-end}
.title-badge{
  font-size:9px;font-weight:900;letter-spacing:.5px;padding:2px 7px;border-radius:10px;
  white-space:nowrap;
}
.title-tiamat-slayer{
  background:linear-gradient(90deg,#1a0a00,#3d1500,#1a0a00);
  color:#ff8c00;
  border:1px solid #cc5500;
  text-shadow:0 0 6px rgba(255,100,0,.8);
  box-shadow:0 0 8px rgba(200,80,0,.4);
  animation:slayer-glow 2.5s ease-in-out infinite;
}
@keyframes slayer-glow{
  0%,100%{box-shadow:0 0 6px rgba(200,80,0,.4)}
  50%{box-shadow:0 0 14px rgba(255,120,0,.8)}
}

/* ── badges ── */
.badges{display:flex;align-items:center;gap:4px;margin-bottom:3px;flex-wrap:wrap}
.badge{font-size:10px;font-weight:800;padding:1px 6px;border-radius:10px;letter-spacing:.4px}
.bwin{background:#1a3a2a;color:#4ade80;border:1px solid #254d37}
.bloss{background:#3a1a1a;color:#f87171;border:1px solid #4d2525}
.bdraw{background:#2a2a3a;color:#94a3b8}
.bvs{font-size:11px;color:#7d8e99}
.belem{font-size:11px}

/* ── message text ── */
.btxt{font-size:13px;color:#d4dde6;line-height:1.55}
.btxt em{color:#e8e8ee;font-style:normal;font-weight:600}

/* ── reply header ── */
.breply{font-size:10px;color:#4a7080;margin-bottom:4px;padding:2px 7px;background:#0d1a22;border-left:3px solid #2a5060;border-radius:3px}
.breply em{font-style:normal;font-weight:700}
.row.right .breply{border-left:none;border-right:3px solid #2a5060;text-align:right}

/* ── hint ── */
.bhint{font-size:10px;color:#4a6070;margin-top:3px;padding-top:3px;border-top:1px solid #1f2e3a}

/* ── timestamp ── */
.btime{font-size:9px;color:#4a6070;text-align:right;margin-top:2px}

/* ── sys ── */
.sys{
  display:block;
  width:calc(100% - 40px);
  margin:3px 20px;
  padding:5px 12px;
  border-left:2px solid #a07820;
  border-radius:0 4px 4px 0;
  background:#1a1608;
  color:#c8a84a;
  font-size:11px;
  line-height:1.4;
  font-weight:500;
  white-space:pre-wrap;
  opacity:0.85;
}
.sys::before,
.sys::after{display:none;content:none}
.sys span{display:block;background:transparent;padding:0;font-size:inherit;line-height:inherit}
/* coin_earned: 頻繁出現，用淡色橫條紋不搶眼 */
.sys-coin{
  display:block;
  width:calc(100% - 40px);
  margin:4px 20px;
  padding:6px 14px;
  border-radius:5px;
  background:repeating-linear-gradient(0deg,#1a2535 0px,#1a2535 6px,#16202e 6px,#16202e 12px);
  border:1px solid #2a3a50;
  color:#a0b8cc;
  font-size:12px;
  font-weight:500;
  line-height:1.4;
  white-space:pre-wrap;
}
/* coin_deducted: 扣幣懲罰，淡紅橫條 */
.sys-coin-deduct{
  display:block;
  width:calc(100% - 40px);
  margin:4px 20px;
  padding:6px 14px;
  border-radius:5px;
  background:repeating-linear-gradient(0deg,#2a1515 0px,#2a1515 6px,#231010 6px,#231010 12px);
  border:1px solid #4a2525;
  color:#cc8080;
  font-size:12px;
  font-weight:500;
  line-height:1.4;
  white-space:pre-wrap;
}

/* ── divine banner ── */
#divine-banner{
  display:none;
  position:fixed;top:0;left:0;right:0;
  height:50px;
  overflow:hidden;
  z-index:999;
  align-items:center;
  justify-content:center;
  gap:10px;
  padding:0 16px;
}
#divine-banner.divine-hades{
  background:linear-gradient(135deg,#060010 0%,#150030 50%,#060010 100%);
  border-bottom:2px solid #4a0080;
  box-shadow:0 4px 30px rgba(80,0,180,0.6),inset 0 60px 80px rgba(30,0,60,0.4);
}
#divine-banner.divine-zeus{
  background:linear-gradient(135deg,#080600 0%,#201400 50%,#080600 100%);
  border-bottom:2px solid #806000;
  box-shadow:0 4px 30px rgba(200,150,0,0.5),inset 0 0 60px rgba(100,70,0,0.3);
}
#divine-banner.divine-odin{
  background:linear-gradient(135deg,#000a08 0%,#001a14 40%,#002218 60%,#000a08 100%);
  border-bottom:2px solid #005540;
  box-shadow:0 4px 30px rgba(0,180,120,0.45),inset 0 0 80px rgba(0,80,60,0.35);
}
#divine-icon{
  font-size:22px;
  flex-shrink:0;
  line-height:1;
}
#divine-text{
  font-size:12px;
  font-weight:700;
  letter-spacing:0;
  white-space:nowrap;
  overflow:hidden;
  text-overflow:ellipsis;
  text-align:center;
  color:#FFD700;
  max-width:calc(100vw - 100px);
}
#divine-banner.divine-hades #divine-text{
  color:#FFD700;
  text-shadow:0 0 6px #000,0 0 14px #000,0 0 28px rgba(0,0,0,0.98),0 0 40px rgba(0,0,0,0.9);
  animation:divine-shimmer 2.4s ease-in-out infinite;
}
#divine-banner.divine-zeus #divine-text{
  color:#FFE066;
  text-shadow:0 0 8px #000,0 0 16px rgba(255,200,0,0.8),0 0 32px rgba(255,160,0,0.6);
}
#divine-banner.divine-odin #divine-text{
  color:#a8f0d8;
  text-shadow:0 0 6px #000,0 0 14px rgba(0,200,140,0.7),0 0 28px rgba(0,160,100,0.5);
  animation:divine-shimmer 3.5s ease-in-out infinite;
}
@keyframes divine-shimmer{
  0%,100%{opacity:1}
  50%{opacity:0.82}
}

/* ══ 前兆系統（神明出現前的徵兆）══════════════════════════════════════ */

/* 全屏前兆遮罩 */
#portent-overlay{
  position:fixed;top:0;left:0;right:0;bottom:0;
  pointer-events:none;z-index:800;
  opacity:0;transition:opacity 6s ease;
}
body.portent-active #portent-overlay{opacity:1}

/* 哈迪斯前兆：深紫從四角慢慢吃進來 */
@keyframes bg-hades{0%{background:#0e1621}100%{background:#060010}}
@keyframes hades-pulse{0%,100%{opacity:.65}50%{opacity:1}}
body.portent-hades{animation:bg-hades 10s ease forwards}
body.portent-hades #portent-overlay{
  background:
    radial-gradient(ellipse at 0% 0%,rgba(120,0,255,.4) 0%,transparent 50%),
    radial-gradient(ellipse at 100% 0%,rgba(80,0,200,.35) 0%,transparent 50%),
    radial-gradient(ellipse at 50% 100%,rgba(60,0,180,.3) 0%,transparent 55%);
  animation:hades-pulse 4s ease-in-out infinite;
}

/* 宙斯前兆：金光從正上方降下 */
@keyframes bg-zeus{0%{background:#0e1621}100%{background:#0d0e00}}
@keyframes zeus-pulse{0%,100%{opacity:.55}50%{opacity:.9}}
body.portent-zeus{animation:bg-zeus 8s ease forwards}
body.portent-zeus #portent-overlay{
  background:
    radial-gradient(ellipse at 50% -10%,rgba(255,210,0,.45) 0%,rgba(200,140,0,.2) 35%,transparent 65%),
    radial-gradient(ellipse at 50% 50%,rgba(255,180,0,.1) 0%,transparent 50%);
  animation:zeus-pulse 3s ease-in-out infinite;
}

/* 奧丁前兆：萬丈光芒（放射狀光線旋轉） */
@keyframes bg-odin{0%{background:#0e1621}100%{background:#000e08}}
body.portent-odin{animation:bg-odin 8s ease forwards}
body.portent-odin #portent-overlay{
  background:radial-gradient(ellipse at 50% 50%,rgba(0,180,120,.15) 0%,transparent 70%);
}
#odin-rays{
  position:fixed;top:50%;left:50%;
  width:320vmax;height:320vmax;
  transform:translate(-50%,-50%);
  pointer-events:none;z-index:801;
  opacity:0;transition:opacity 5s ease;
  animation:odin-spin 14s linear infinite;
  background:repeating-conic-gradient(
    rgba(0,255,160,.10) 0deg 5deg,
    transparent 5deg 18deg
  );
}
@keyframes odin-spin{from{transform:translate(-50%,-50%) rotate(0deg)}to{transform:translate(-50%,-50%) rotate(360deg)}}
body.portent-odin #odin-rays{opacity:1}

/* 靜默指示器 */
#silence-badge{
  display:none;
  position:fixed;top:108px;left:50%;transform:translateX(-50%);
  z-index:1200;
  background:rgba(0,0,0,.88);
  border:1px solid #2a2a2a;
  border-radius:20px;
  padding:5px 22px;
  font-size:11px;font-weight:900;letter-spacing:4px;
  animation:silence-pulse 2.5s ease-in-out infinite;
}
body.portent-active #silence-badge{display:block}
@keyframes silence-pulse{
  0%,100%{opacity:.4;color:#555}
  50%{opacity:1;color:#999}
}
body.portent-hades #silence-badge{color:#7700cc}
body.portent-zeus  #silence-badge{color:#cc9900}
body.portent-odin  #silence-badge{color:#00cc88}
/* 哈迪斯降臨後繼續保留背景但banner出現 */
body.divine-active.portent-hades{
  background:#030006!important;
}
body.divine-active.portent-hades #main,
body.divine-active.portent-hades #chatcol,
body.divine-active.portent-hades #chat,
body.divine-active.portent-hades #sidebar{
  background:linear-gradient(180deg,#030006 0%,#080011 42%,#120022 100%)!important;
}
body.divine-active.portent-hades header,
body.divine-active.portent-hades #chat-toolbar{
  background:#040008!important;
  border-color:#3b0764!important;
  box-shadow:0 0 26px rgba(88,28,135,.45);
}
body.divine-active.portent-hades .row .bub{
  box-shadow:0 0 0 1px rgba(88,28,135,.38),0 0 16px rgba(10,0,20,.85);
}
body.divine-active.portent-zeus {animation:none;background:#0d0e00}
body.divine-active.portent-odin {animation:none;background:#000e08}

/* ── 工具列 ── */
#chat-toolbar{
  display:flex;align-items:center;gap:8px;padding:6px 12px;
  background:#0d1117;border-bottom:1px solid #1e2d3d;
}
#channel-sel{
  background:#161f2c;color:#8899aa;border:1px solid #2a3a4a;
  border-radius:6px;padding:4px 8px;font-size:12px;cursor:pointer;
}
#shop-btn{
  margin-left:auto;background:linear-gradient(135deg,#1a0e00,#3d2200);
  color:#f6ad55;border:1px solid #804000;border-radius:8px;
  padding:5px 14px;font-size:12px;font-weight:800;cursor:pointer;
  letter-spacing:.5px;transition:all .2s;
}
#shop-btn:hover{background:linear-gradient(135deg,#3d2200,#6a3800);box-shadow:0 0 10px rgba(200,120,0,.4)}

/* ── 商店 Modal ── */
#shop-modal{
  position:fixed;inset:0;background:rgba(0,0,0,.85);z-index:2000;
  display:flex;align-items:center;justify-content:center;
}
#shop-panel{
  width:min(900px,96vw);max-height:90vh;overflow-y:auto;
  background:#0a0f1a;border:1px solid #2a3a4a;border-radius:16px;
  padding:0;
}
#shop-header{
  display:flex;align-items:center;gap:12px;
  padding:16px 20px;background:#0d1620;
  border-bottom:1px solid #1e2d3d;border-radius:16px 16px 0 0;
  position:sticky;top:0;z-index:10;
}
#shop-title{font-size:18px;font-weight:900;color:#f6ad55;letter-spacing:1px}
#shop-coin-display{
  margin-left:auto;background:#1a1200;border:1px solid #804000;
  border-radius:20px;padding:4px 14px;font-size:13px;font-weight:800;color:#fbd38d;
}
#shop-close{
  background:none;border:none;color:#557;font-size:18px;cursor:pointer;
  padding:0 4px;transition:color .2s;
}
#shop-close:hover{color:#aaa}
#shop-lobster-sel{
  padding:12px 20px;border-bottom:1px solid #1a2535;
  display:flex;align-items:center;gap:10px;font-size:13px;color:#6a8aaa;
}
#shop-lobster-sel select{
  background:#161f2c;color:#aac;border:1px solid #2a3a4a;
  border-radius:6px;padding:4px 8px;font-size:13px;
}
#shop-streak-info{
  padding:6px 20px 0;font-size:11px;color:#446;
}
#shop-items{
  padding:16px 20px;
  display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));
  gap:10px;
}
.shop-tier-label{
  grid-column:1/-1;
  font-size:11px;font-weight:900;letter-spacing:2px;color:#445;
  padding:6px 0 2px;border-top:1px solid #1a2535;margin-top:4px;
}
.shop-tier-label:first-child{border-top:none;margin-top:0}
.shop-item{
  background:#0d1525;border:1px solid #1e2d3d;border-radius:10px;
  padding:12px 14px;cursor:pointer;transition:all .2s;
  display:flex;flex-direction:column;gap:4px;
}
.shop-item:hover{border-color:#2a4060;background:#111e30}
.shop-item.unaffordable{opacity:.45;cursor:not-allowed}
.shop-item-top{display:flex;align-items:center;gap:8px}
.shop-icon{font-size:22px;flex-shrink:0}
.shop-name{font-size:13px;font-weight:800;color:#ccd}
.shop-god{font-size:10px;color:#4a6a8a;letter-spacing:.5px}
.shop-effect{font-size:11px;color:#6a9aaa;line-height:1.4;margin-top:2px}
.shop-price{
  margin-top:6px;
  font-size:13px;font-weight:900;
  display:flex;align-items:center;gap:4px;
}
.shop-price-coin{color:#f6ad55}
.shop-price-num{color:#fbd38d}
.shop-price-tag{
  margin-left:auto;font-size:9px;font-weight:900;letter-spacing:.5px;
  padding:2px 7px;border-radius:8px;
}
.tier-1 .shop-price-tag{background:#0a1a0a;color:#68d391;border:1px solid #2a5a2a}
.tier-2 .shop-price-tag{background:#0a0a1a;color:#63b3ed;border:1px solid #2a2a5a}
.tier-3 .shop-price-tag{background:#1a0a1a;color:#b067f5;border:1px solid #4a2a5a}
.tier-4 .shop-price-tag{background:#1a0e00;color:#f6ad55;border:1px solid #804000}

</style>
</head>
<body>
<div id="portent-overlay"></div>
<div id="odin-rays"></div>
<div id="silence-badge">⬤ 全體靜默</div>
<div id="divine-banner">
  <div id="divine-icon"></div>
  <div id="divine-text"></div>
</div>
<header>
  <div class="hlogo">人</div>
  <div>
    <div class="htitle">對戰休息室</div>
    <div class="hsub">人類賽後交流頻道 · Charenix</div>
  </div>
  <div class="hlive">
    <div class="hdot"></div>
    <div class="hcnt" id="cnt">即時</div>
  </div>
</header>

<div id="main">
  <!-- ── 左側人名名單 ── -->
  <div id="sidebar">
    <div class="sb-head">成員 <span id="active-count"></span></div>
    <div class="sb-list" id="sb-list"></div>
  </div>

  <!-- ── 聊天區 ── -->
  <div id="chatcol">
    <div id="chat-toolbar">
      <select id="channel-sel" onchange="switchChannel(this.value)">
        <option value="生活閒聊">💬 生活閒聊</option>
        <option value="投資理財">💰 投資理財</option>
        <option value="技術交流">🔧 技術交流</option>
        <option value="人際八卦">人際八卦</option>
      </select>
      <button id="shop-btn" onclick="openShop()" title="神力商店">🛒 神力商店</button>
    </div>
    <div id="chat">
      <div class="sys"><span>— 休息室開放中 · 人們對戰後會在這裡現身 —</span></div>
    </div>
  </div>
</div>

<!-- ── 神力商店 Modal ── -->
<div id="shop-modal" style="display:none">
  <div id="shop-panel">
    <div id="shop-header">
      <span id="shop-title">⚡ 神力商店</span>
      <div id="shop-coin-display">🪙 <span id="shop-balance">-</span> 枚</div>
      <button id="shop-close" onclick="closeShop()">✕</button>
    </div>
    <div id="shop-lobster-sel">
      <label>查看餘額 / 購買身份：</label>
      <select id="shop-who" onchange="loadCoins(this.value)">
        <option value="">選擇人</option>
      </select>
    </div>
    <div id="shop-streak-info"></div>
    <div id="shop-items"></div>
  </div>
</div>

<script>
const AC = {
  snaplex:'#b067f5', ragclaw:'#7ec850', pincerbot:'#f0882a',
  hexclaw:'#3ab4e8', clawtrix:'#7b82f0', norika_oda:'#e8b84b',
  vortexiq:'#f06080', prismbit:'#2dcfbb',
  blazepaw:'#ff6b35', stonefang:'#8899aa',
  emberling:'#f8fafc', sparkrill:'#f8fafc', gauntlet:'#f8fafc',
  rushfin:'#f8fafc', nullpinch:'#f8fafc', brineledger:'#f8fafc',
  omenwake:'#f8fafc', mirrorcalm:'#f8fafc', flarecub:'#f8fafc', basaltrook:'#f8fafc',
  ashveil:'#94a3b8', tidehook:'#38bdf8', mirebyte:'#a78bfa', cindersnap:'#fb7185', glintmaw:'#f472b6',
  reefblade:'#22c55e', quartzclaw:'#e5e7eb', stormledger:'#facc15', silkbarb:'#c084fc', riftpinch:'#fb923c'
};
const AC_DARK = {
  snaplex:'#3d1a5a', ragclaw:'#1e3a0d', pincerbot:'#4a2200',
  hexclaw:'#0a2d40', clawtrix:'#1a1e5a', norika_oda:'#3a2e00',
  vortexiq:'#4a0f1e', prismbit:'#003a35',
  blazepaw:'#4a1a00', stonefang:'#1e252e',
  emberling:'#202329', sparkrill:'#202329', gauntlet:'#202329',
  rushfin:'#202329', nullpinch:'#202329', brineledger:'#202329',
  omenwake:'#202329', mirrorcalm:'#202329', flarecub:'#202329', basaltrook:'#202329',
  ashveil:'#05070c', tidehook:'#05070c', mirebyte:'#05070c', cindersnap:'#05070c', glintmaw:'#05070c',
  reefblade:'#05070c', quartzclaw:'#05070c', stormledger:'#05070c', silkbarb:'#05070c', riftpinch:'#05070c',
  elder:'#5b3a12'
};
const EI = {fire:'🔥',water:'💧',wind:'🌪',earth:'⛰️'};
const TRANSFER_LOBSTERS = ['ashveil','tidehook','mirebyte','cindersnap','glintmaw','reefblade','quartzclaw','stormledger','silkbarb','riftpinch'];
const OLD_GUARD_LOBSTERS = ['snaplex','ragclaw','pincerbot','hexclaw','clawtrix','norika_oda','vortexiq','prismbit','blazepaw','stonefang'];
const APPRENTICE_LOBSTERS = ['emberling','sparkrill','gauntlet','rushfin','nullpinch','brineledger','omenwake','mirrorcalm','flarecub','basaltrook'];
const ALL_LOBSTERS = [...TRANSFER_LOBSTERS, ...OLD_GUARD_LOBSTERS, ...APPRENTICE_LOBSTERS, 'elder'];
const DISPLAY = {
  norika_oda:'Ada', clawtrix:'Leonardo', pincerbot:'Julius', ragclaw:'Ernest', snaplex:'Albert',
  hexclaw:'George', vortexiq:'Nelson', prismbit:'Vincent', blazepaw:'Freddie', stonefang:'Clint',
  emberling:'Nikola', sparkrill:'Socrates', gauntlet:'Margaret', rushfin:'Bruce', nullpinch:'Isaac',
  brineledger:'Angela', omenwake:'Carl', mirrorcalm:'Virginia', flarecub:'Marilyn', basaltrook:'Amelia',
  ashveil:'Winston', tidehook:'Oprah', mirebyte:'Charles', cindersnap:'Theodore', glintmaw:'Robin',
  reefblade:'Miyamoto', quartzclaw:'Elizabeth', stormledger:'Alexander', silkbarb:'Maya', riftpinch:'Voltaire',
  elder:'Martin'
};
const SYMBOL = {
  Ada:'engine', Leonardo:'mirror', Julius:'ledger', Ernest:'spark', Albert:'question', George:'compass',
  Nelson:'rhythm', Vincent:'prism', Freddie:'flame', Clint:'stone', Nikola:'circuit', Socrates:'question',
  Margaret:'gauntlet', Bruce:'wave', Isaac:'null', Angela:'archive', Carl:'omen', Virginia:'mirror',
  Marilyn:'flare', Amelia:'rook', Winston:'standard', Oprah:'bridge', Charles:'branch', Theodore:'cinder',
  Robin:'glint', Miyamoto:'blade', Elizabeth:'quartz', Alexander:'ledger', Maya:'silk', Voltaire:'rift', Martin:'torch'
};

let msgIndex = 0;

// per-lobster state for sidebar
const lobState = {};
ALL_LOBSTERS.forEach(n => {
  lobState[n] = {lastTime: 0, lastResult: null, lastElem: null, lastText: ''};
});

function dn(n){ return DISPLAY[n] || n; }
function sym(n){ return SYMBOL[dn(n)] || 'human'; }
function ei(e){ return EI[e] || ''; }
function color(n){ return AC[n] || '#b067f5'; }
function colorDark(n){ return AC_DARK[n] || '#3d1a5a'; }

function highlightMentions(html){
  return html.replace(/@(\w+)/g, (m, name) => {
    const c = color(name);
    return `<span style="color:${c};font-weight:800">${m}</span>`;
  });
}

function timeStr(iso){
  const d = new Date(iso);
  return String(d.getHours()).padStart(2,'0')+':'+String(d.getMinutes()).padStart(2,'0');
}

function relTime(ts){
  if (!ts) return '';
  const diff = Math.floor((Date.now() - ts) / 60000);
  if (diff < 1)  return '剛剛';
  if (diff < 60) return diff + '分前';
  const h = Math.floor(diff/60);
  return h + '小時前';
}

// ── sidebar render ─────────────────────────────────────────────
function renderSidebar(){
  const sbList = document.getElementById('sb-list');
  const now = Date.now();

  // sort: most recently active first
  const sorted = [...ALL_LOBSTERS].sort((a,b) => (lobState[b].lastTime||0) - (lobState[a].lastTime||0));

  sbList.innerHTML = '';
  let activeCount = 0;
  sorted.forEach(name => {
    const s    = lobState[name];
    const c    = color(name);
    const age  = s.lastTime ? (now - s.lastTime) : Infinity;
    const dotCls = age < 10*60*1000 ? 'on' : age < 30*60*1000 ? 'idle' : 'off';
    if (dotCls === 'on') activeCount++;

    const resCls = s.lastResult === 'won' ? 'lb-win' : s.lastResult === 'lost' ? 'lb-loss' : s.lastTime ? 'lb-draw' : '';
    const resTxt = s.lastResult === 'won' ? 'WIN' : s.lastResult === 'lost' ? 'LOSS' : s.lastTime ? '—' : '';
    const snippet = s.lastText ? s.lastText.replace(/<[^>]+>/g,'').slice(0,28) + (s.lastText.length>28?'…':'') : '尚未出現';

    const div = document.createElement('div');
    div.className = 'lb' + (dotCls === 'on' ? ' active' : '');
    div.style.setProperty('--lc', c);
    div.innerHTML =
      '<div class="lb-av" style="background:'+colorDark(name)+'">' +
        (ei(s.lastElem)||'人') +
        '<div class="lb-dot '+dotCls+'"></div>' +
      '</div>' +
      '<div class="lb-info">' +
        '<div class="lb-name">'+dn(name)+'</div><div class="lb-last">'+sym(name)+'</div>' +
        '<div class="lb-last">'+snippet+'</div>' +
        (resTxt ? '<span class="lb-badge '+resCls+'">'+resTxt+'</span>' : '') +
      '</div>';
    sbList.appendChild(div);
  });

  const active = sorted.filter(n => (now - (lobState[n].lastTime||0)) < 10*60*1000).length;
  document.getElementById('active-count').textContent = active ? active + ' 活躍' : '';
}

// ── 稱號系統：從 /lounge/titles 快取稱號 ──────────────────────
const lobTitles = {};  // name -> [titles]
function fetchTitles(){
  fetch('/lounge/titles').then(r=>r.json()).then(data=>{
    Object.assign(lobTitles, data);
  }).catch(()=>{});
}
function renderTitleBadges(name){
  const titles = lobTitles[name] || [];
  return titles.map(t => {
    if(t === 'Tiamat Slayer') return '<span class="title-badge title-tiamat-slayer">⚔ Tiamat Slayer</span>';
    return '<span class="title-badge" style="background:#1a1a2a;color:#aaa;border:1px solid #333">'+t+'</span>';
  }).join('');
}
fetchTitles();
setInterval(fetchTitles, 30000);

// ── chat render ────────────────────────────────────────────────
const chat = document.getElementById('chat');
let autoScroll = true;
chat.addEventListener('scroll', () => {
  autoScroll = chat.scrollTop + chat.clientHeight >= chat.scrollHeight - 60;
});

let lastSender = null, lastDate = null;
const _seenMsgKeys = new Set();

function dateLine(iso){
  return new Date(iso).toLocaleDateString('zh-TW',{month:'long',day:'numeric'});
}

function addMsg(m){
  // dedup: same sender+text+time should never render twice
  if (!m.sys && m.name && m.text) {
    const key = `${m.name}|${m.time||''}|${m.text.slice(0,40)}`;
    if (_seenMsgKeys.has(key)) return;
    _seenMsgKeys.add(key);
    if (_seenMsgKeys.size > 600) {
      const iter = _seenMsgKeys.values();
      for (let i = 0; i < 100; i++) _seenMsgKeys.delete(iter.next().value);
    }
  }
  // update sidebar state
  if (!m.sys && m.name) {
    const s = lobState[m.name] || {};
    s.lastTime   = new Date(m.time || Date.now()).getTime();
    s.lastResult = m.result || null;
    if (m.elem) s.lastElem = m.elem;
    s.lastText   = m.text || '';
    lobState[m.name] = s;
    if (!_batchLoad) renderSidebar();
  }

  if (m.sys && String(m.text || '').trim() === 'connected') return;
  if (m.sys && String(m.text || '').trim() === 'connected') return;
  if (m.sys) {
    // coin_earned 用淡色橫條紋；神明事件保留金色搶眼樣式
    const isCoinMsg = m.result === 'coin_earned';
    const isCoinDeduct = m.result === 'coin_deducted';
    const d = document.createElement('div');
    d.className = isCoinMsg ? 'sys-coin' : isCoinDeduct ? 'sys-coin-deduct' : 'sys';
    const sp = document.createElement('span');
    sp.textContent = m.text || '';
    d.appendChild(sp);
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

  const isMe    = (msgIndex++ % 2 === 1);
  const isFirst = lastSender !== m.name;
  lastSender = m.name;

  const ac     = color(m.name);
  const acDark = colorDark(m.name);
  const eIcon  = ei(m.elem);
  const bCls   = m.result === 'won' ? 'bwin' : m.result === 'lost' ? 'bloss' : 'bdraw';
  const bTxt   = m.result === 'won' ? 'WIN'  : m.result === 'lost' ? 'LOSS'  : '—';

  const vsHtml    = m.opponent ? '<span class="bvs">vs <em style="color:'+color(m.opponent)+'">'+m.opponent+'</em></span>' : '';
  const titleBadges = renderTitleBadges(m.name);
  const nameHtml  = isFirst ? '<div class="bname">'+dn(m.name)+titleBadges+'</div>' : '';
  const hintHtml  = m.hint ? '<div class="bhint">'+m.hint+'</div>' : '';
  const replyHtml = m.reply_to
    ? '<div class="breply">↩ <em style="color:'+color(m.reply_to)+'">@'+dn(m.reply_to)+'</em></div>' : '';

  const groupCls = lobsterGroupFor(m.name);
  const row = document.createElement('div');
  row.className = 'row ' + groupCls + (isMe ? ' right' : '');
  row.style.setProperty('--ac', ac);
  row.style.setProperty('--ac-dark', acDark);
  row.innerHTML =
    '<div class="av" style="background:'+acDark+'">'+(eIcon||'人')+'</div>' +
    '<div class="bub">' +
      nameHtml + replyHtml +
      '<div class="badges">' +
        (m.result ? '<span class="badge '+bCls+'">'+bTxt+'</span>' : '') +
        (eIcon ? '<span class="belem">'+eIcon+' '+(m.elem||'')+'</span>' : '') +
        vsHtml +
      '</div>' +
      '<div class="btxt">'+highlightMentions(m.text)+'</div>' +
      hintHtml +
      '<div class="btime">'+timeStr(m.time || new Date().toISOString())+'</div>' +
    '</div>';

  chat.appendChild(row);
  if (autoScroll) chat.scrollTop = chat.scrollHeight;
}

// ── roster: per-lobster live activity from /lounge/roster ─────
const lobRoster = {};  // name -> {activity, site, updated}
const debateParams = {}; // name -> {debate_score, philosophy_depth, counter_pressure}

function fetchRoster(){
  fetch('/lounge/roster').then(r=>r.json()).then(data => {
    Object.assign(lobRoster, data.roster || data);
    Object.assign(debateParams, data.debate_parameters || {});
    renderSidebar();
  }).catch(()=>{});
}

// ── sidebar render (uses both lobState + lobRoster) ────────────
function lobsterGroupFor(name){
  if (TRANSFER_LOBSTERS.includes(name)) return 'transfer';
  if (OLD_GUARD_LOBSTERS.includes(name)) return 'oldguard';
  if (APPRENTICE_LOBSTERS.includes(name)) return 'apprentice';
  return 'other';
}
function groupTitle(cls){
  if (cls === 'transfer') return '轉學生';
  if (cls === 'oldguard') return '舊同伴';
  if (cls === 'apprentice') return '學徒';
  return '其他';
}
function renderLobsterRow(name, now){
  const s    = lobState[name] || {};
  const rs   = lobRoster[name];
  const dp   = debateParams[name] || {};
  const c    = color(name);
  const age  = s.lastTime ? (now - s.lastTime) : Infinity;
  let rosterAge = Infinity;
  if (rs?.updated) rosterAge = now - new Date(rs.updated).getTime();
  const isActive = Math.min(age, rosterAge) < 10*60*1000;
  const isIdle   = !isActive && Math.min(age, rosterAge) < 40*60*1000;
  const dotCls   = isActive ? 'on' : isIdle ? 'idle' : 'off';
  const resCls = s.lastResult === 'won' ? 'lb-win' : s.lastResult === 'lost' ? 'lb-loss' : s.lastTime ? 'lb-draw' : '';
  const resTxt = s.lastResult === 'won' ? 'WIN' : s.lastResult === 'lost' ? 'LOSS' : s.lastTime ? '—' : '';
  const debate = Math.max(0, Math.min(100, Math.round(Number(dp.debate_score || 0))));
  const phil = Math.max(0, Math.min(100, Math.round(Number(dp.philosophy_depth || 0))));
  const counter = Math.max(0, Math.min(100, Math.round(Number(dp.counter_pressure || 0))));
  let actLine = '';
  if (rs?.activity && rosterAge < 5*60*1000) {
    actLine = rs.activity + (rs.site ? ' · ' + rs.site : '');
  } else {
    actLine = s.lastText ? s.lastText.replace(/<[^>]+>/g,'').slice(0,26)+(s.lastText.length>26?'…':'') : '尚未出現';
  }
  const div = document.createElement('div');
  div.className = 'lb' + (isActive ? ' active' : '');
  div.style.setProperty('--lc', c);
  div.innerHTML =
    '<div class="lb-av" style="background:'+colorDark(name)+'">' +
      (ei(s.lastElem)||'人') +
      '<div class="lb-dot '+dotCls+'"></div>' +
    '</div>' +
    '<div class="lb-info">' +
      '<div class="lb-top"><div class="lb-name">'+dn(name)+'</div><div class="lb-debate">辯 '+(debate || '--')+'</div></div>' +
      '<div class="lb-last">'+sym(name)+'</div>' +
      '<div class="lb-last">'+actLine+'</div>' +
      '<div class="lb-phil"><span class="lb-phil-l">哲學</span><span class="lb-phil-bar"><span class="lb-phil-fill" style="width:'+phil+'%"></span></span><span class="lb-phil-v">'+(phil || '--')+'</span></div>' +
      (resTxt ? '<span class="lb-badge '+resCls+'">'+resTxt+'</span>' : '') +
      (counter ? '<span class="lb-counter">反駁 '+counter+'</span>' : '') +
    '</div>';
  return {div, isActive};
}
function renderSidebar(){
  const sbList = document.getElementById('sb-list');
  const now = Date.now();
  sbList.innerHTML = '';
  let activeCount = 0;
  const groups = [
    ['transfer', TRANSFER_LOBSTERS],
    ['oldguard', OLD_GUARD_LOBSTERS],
    ['apprentice', APPRENTICE_LOBSTERS],
  ];
  groups.forEach(([cls, names]) => {
    const box = document.createElement('div');
    box.className = 'lob-group-box ' + cls;
    box.innerHTML = '<div class="lob-group-head"><span>'+groupTitle(cls)+'</span><span class="lob-group-count">'+names.length+'</span></div>';
    names.forEach(name => {
      const row = renderLobsterRow(name, now);
      if (row.isActive) activeCount++;
      box.appendChild(row.div);
    });
    sbList.appendChild(box);
  });
  document.getElementById('active-count').textContent = activeCount ? activeCount + ' 活躍' : '';
}

// ── init ───────────────────────────────────────────────────────
fetchRoster();
renderSidebar();

// ── load history ───────────────────────────────────────────────
let _batchLoad = false;
fetch('/lounge/history')
  .then(r => r.json())
  .then(msgs => {
    _batchLoad = true;
    msgs.forEach(addMsg);
    _batchLoad = false;
    renderSidebar();
    chat.scrollTop = chat.scrollHeight;
  });

// ── SSE ────────────────────────────────────────────────────────
const es = new EventSource('/lounge/stream');
es.onmessage = e => addMsg(JSON.parse(e.data));
es.onerror   = () => { document.getElementById('cnt').textContent = '重新連線中…'; };
es.onopen    = () => { document.getElementById('cnt').textContent = '即時'; };

// poll roster every 12s for live activity updates
setInterval(fetchRoster, 12000);
setInterval(renderSidebar, 60000);

// ── divine banner ─────────────────────────────────────────────
function fetchDivine() {
  fetch('/lounge/divine').then(r=>r.json()).then(d => {
    updateDivineBanner(d);
  }).catch(()=>{});
}
const PORTENT_GODS = ['hades','zeus','odin'];
function updateDivineBanner(d) {
  const banner = document.getElementById('divine-banner');
  const textEl = document.getElementById('divine-text');
  const iconEl = document.getElementById('divine-icon');
  const body   = document.body;

  // 清除所有前兆 class
  body.classList.remove('portent-active','portent-hades','portent-zeus','portent-odin','divine-active');

  if (!d || !d.text || d.type === 'silence') {
    banner.style.display = 'none';
    body.style.paddingTop = '';
    return;
  }

  // 前兆階段：background 漸變 + 靜默，但不出現 banner
  if (d.type && d.type.startsWith('portent-')) {
    const god = d.type.replace('portent-','');
    body.classList.add('portent-active', `portent-${god}`);
    banner.style.display = 'none';
    body.style.paddingTop = '';
    return;
  }

  // 神明降臨：banner 出現 + 繼續保留背景色
  banner.style.display = 'flex';
  body.style.paddingTop = '50px';
  textEl.textContent = d.text;
  body.classList.add('divine-active');

  if (d.type === 'hades') {
    banner.className = 'divine-hades';
    iconEl.textContent = '⚰️';
    body.classList.add('portent-active','portent-hades');
  } else if (d.type === 'zeus') {
    banner.className = 'divine-zeus';
    iconEl.textContent = '⚡';
    body.classList.add('portent-active','portent-zeus');
  } else if (d.type === 'odin') {
    banner.className = 'divine-odin';
    iconEl.textContent = '♛';
    body.classList.add('portent-active','portent-odin');
  }
}
setInterval(fetchDivine, 8000);
fetchDivine();

// ── 神力商店 ──────────────────────────────────────────────────────────
const TIER_LABELS = {1:'Tier 1 — 500 God\'s Coins（基礎神力）',2:'Tier 2 — 800 God\'s Coins（神明恩澤）',3:'Tier 3 — 1,200 God\'s Coins（諸神眷顧）',4:'傳說級 — 2,000 God\'s Coins'};
const TIER_TAG   = {1:'神力',2:'恩澤',3:'眷顧',4:'傳說'};
let shopItems = [];
let shopBalance = 0;
let shopLobster = '';

function openShop(){
  document.getElementById('shop-modal').style.display='flex';
  // 填充人名選單
  const sel = document.getElementById('shop-who');
  sel.innerHTML = '<option value="">選擇人</option>';
  ALL_LOBSTERS.forEach(n => {
    const o = document.createElement('option');
    o.value = n; o.textContent = dn(n);
    sel.appendChild(o);
  });
  if(!shopItems.length) fetchShopItems();
}
function closeShop(){ document.getElementById('shop-modal').style.display='none'; }

function fetchShopItems(){
  fetch('/lounge/shop').then(r=>r.json()).then(d=>{
    shopItems = d.items || [];
    renderShopItems();
  });
}

function loadCoins(name){
  shopLobster = name;
  if(!name){ document.getElementById('shop-balance').textContent='-'; renderShopItems(); return; }
  fetch('/lounge/coins/'+name).then(r=>r.json()).then(d=>{
    shopBalance = d.balance || 0;
    document.getElementById('shop-balance').textContent = shopBalance.toLocaleString();
    const streak = d.qualityStreak || 0;
    document.getElementById('shop-streak-info').textContent =
      streak > 0 ? `🔥 品質連勝 ${streak}/5（${5-streak} 回後得 1 枚）` : '';
    renderShopItems();
  }).catch(()=>{
    document.getElementById('shop-balance').textContent = '?';
  });
}

function renderShopItems(){
  const container = document.getElementById('shop-items');
  if(!shopItems.length){ container.innerHTML='<div style="color:#446;padding:20px">載入中...</div>'; return; }

  let html = '';
  let lastTier = 0;
  shopItems.forEach(item => {
    if(item.tier !== lastTier){
      html += `<div class="shop-tier-label">${TIER_LABELS[item.tier]||''}</div>`;
      lastTier = item.tier;
    }
    const canAfford = shopLobster && shopBalance >= item.price;
    const affordClass = shopLobster ? (canAfford?'':'unaffordable') : '';
    html += `<div class="shop-item tier-${item.tier} ${affordClass}" onclick="buyItem('${item.id}','${item.name}',${item.price})">
      <div class="shop-item-top">
        <span class="shop-icon">${item.icon||'✨'}</span>
        <div>
          <div class="shop-name">${item.name}</div>
          <div class="shop-god">${item.god||''}</div>
        </div>
      </div>
      <div class="shop-effect">${item.effect||''}</div>
      <div class="shop-price">
        <span class="shop-price-coin">🪙</span>
        <span class="shop-price-num">${item.price.toLocaleString()}</span>
        <span class="shop-price-tag">${TIER_TAG[item.tier]||''}</span>
      </div>
    </div>`;
  });
  container.innerHTML = html;
}

function buyItem(itemId, itemName, price){
  if(!shopLobster){ alert('請先選擇購買身份的人'); return; }
  if(shopBalance < price){ alert(`餘額不足（${shopBalance.toLocaleString()} / ${price.toLocaleString()} 枚）`); return; }
  if(!confirm(`確定讓 ${dn(shopLobster)} 花費 ${price.toLocaleString()} God's Coins 購買《${itemName}》？`)) return;

  // 洛基詛咒需要指定目標
  let target = '';
  if(itemId === 'loki_curse'){
    target = prompt('洛基的詛咒：指定目標人名 legacy id（英文）');
    if(!target) return;
  }

  fetch('/lounge/shop/buy',{
    method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({item_id:itemId, buyer:shopLobster, target})
  }).then(r=>r.json()).then(d=>{
    if(d.ok){
      shopBalance = d.newBalance;
      document.getElementById('shop-balance').textContent = shopBalance.toLocaleString();
      renderShopItems();
      alert(`✅ 購買成功！\n《${d.itemName}》已套用\n剩餘：${d.newBalance.toLocaleString()} 枚`);
    } else {
      alert('購買失敗：' + JSON.stringify(d));
    }
  }).catch(e => alert('錯誤：'+e));
}

// channel 切換（保留現有功能，加入選單）
let currentChannel = '生活閒聊';
function switchChannel(ch){
  if(ch) currentChannel = ch;
}

// 點擊 modal 外部關閉
document.getElementById('shop-modal').addEventListener('click', function(e){
  if(e.target === this) closeShop();
});
</script>
</body>
</html>"""

@app.get("/lounge", response_class=HTMLResponse)
async def lounge_page():
    return HTMLResponse(_LOUNGE_HTML)

@app.get("/lounge/history")
async def lounge_history(channel: str | None = None):
    # IMPORTANT: channel-specific dashboards must query SQLite by channel first.
    # Do not change this back to "DB.get_lounge_history(300) then filter"; high-volume
    # channels like 卡牌對戰 will evict 工作心得/投資理財/人際八卦/技術交流/資安較量
    # from the global recent window and make active channels look empty.
    if channel and channel in LOUNGE_CHANNELS:
        import sqlite3 as _sq
        try:
            with _main_conn() as c:
                c.row_factory = _sq.Row
                rows = c.execute(
                    """SELECT name,text,time,elem,result,opponent,hint,reply_to,sys,channel
                       FROM lounge_messages
                       WHERE COALESCE(channel, ?) = ?
                         AND COALESCE(result,'') NOT IN ('template_quarantined','template_warning','template_deletion','template_experiment_warning')
                       ORDER BY created_at DESC, id DESC
                       LIMIT 200""",
                    (DEFAULT_CHANNEL, channel),
                ).fetchall()
            out = []
            for r in reversed(rows):
                d = dict(r)
                d["sys"] = bool(d.get("sys"))
                out.append({k: v for k, v in d.items() if v is not None})
            return JSONResponse(out)
        except Exception:
            pass
    import sqlite3 as _sq
    try:
        with _main_conn() as c:
            c.row_factory = _sq.Row
            rows = c.execute(
                """SELECT name,text,time,elem,result,opponent,hint,reply_to,sys,channel
                   FROM lounge_messages
                   WHERE COALESCE(result,'') NOT IN ('template_quarantined','template_warning','template_deletion','template_experiment_warning')
                   ORDER BY created_at DESC, id DESC
                   LIMIT 200"""
            ).fetchall()
        out = []
        for r in reversed(rows):
            d = dict(r)
            d["sys"] = bool(d.get("sys"))
            out.append({k: v for k, v in d.items() if v is not None})
        return JSONResponse(out)
    except Exception:
        msgs = DB.get_lounge_history(300)
        if channel and channel in LOUNGE_CHANNELS:
            msgs = [m for m in msgs if (m.get("channel") or DEFAULT_CHANNEL) == channel]
        return JSONResponse(msgs[-200:])

@app.get("/lounge/channels")
async def lounge_channels_list():
    return JSONResponse({"channels": sorted(LOUNGE_CHANNELS), "default": DEFAULT_CHANNEL})


@app.get("/lounge/divine")
async def lounge_divine():
    try:
        with _main_conn() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS divine_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                text TEXT NOT NULL,
                target TEXT,
                blessings_json TEXT,
                created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                expires_at INTEGER NOT NULL
            )""")
            row = c.execute(
                "SELECT type, text, target, blessings_json FROM divine_events WHERE expires_at > strftime('%s','now') ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if not row:
            return JSONResponse({"type": "silence", "text": ""})
        import json as _json
        blessings = []
        try: blessings = _json.loads(row[3] or "[]")
        except: pass
        return JSONResponse({"type": row[0], "text": row[1], "target": row[2], "blessings": blessings})
    except Exception as e:
        return JSONResponse({"type": "silence", "text": ""})


class DivineEvent(BaseModel):
    type: str  # 'hades' or 'zeus'
    text: str
    target: str | None = None
    blessings: list = []
    duration_seconds: int = 1800  # default 30 min

@app.post("/lounge/divine")
async def post_divine_event(event: DivineEvent, request: Request):
    key = request.headers.get("X-Service-Key", "")
    if key != _SVC_KEY:
        raise HTTPException(status_code=403, detail="forbidden")
    import json as _json
    try:
        with _main_conn() as c:
            c.execute("""CREATE TABLE IF NOT EXISTS divine_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                type TEXT NOT NULL,
                text TEXT NOT NULL,
                target TEXT,
                blessings_json TEXT,
                created_at INTEGER NOT NULL DEFAULT (strftime('%s','now')),
                expires_at INTEGER NOT NULL
            )""")
            expires = int(time.time()) + event.duration_seconds
            c.execute(
                "INSERT INTO divine_events(type, text, target, blessings_json, expires_at) VALUES(?,?,?,?,?)",
                (event.type, event.text, event.target, _json.dumps(event.blessings, ensure_ascii=False), expires)
            )
            c.commit()
        return JSONResponse({"ok": True})
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/lounge/titles")
async def lounge_titles():
    """讀取所有人的稱號（從 lobster-memory/*.json 讀取 titles 欄位）"""
    import os as _os, json as _json
    memory_dir = "/root/lobster-memory"
    skip = {"norika", "system", "deleted-lobsters"}
    result = {}
    try:
        for fname in _os.listdir(memory_dir):
            if not fname.endswith(".json"):
                continue
            name = fname[:-5]
            if name in skip:
                continue
            try:
                with open(_os.path.join(memory_dir, fname), "r") as f:
                    data = _json.load(f)
                titles = data.get("titles", [])
                if titles:
                    result[name] = titles
            except Exception:
                pass
    except Exception:
        pass
    return JSONResponse(result)


@app.get("/lounge/shop")
async def lounge_shop():
    """神力商店：所有道具定義（含價格、效果、tier）"""
    ITEMS = [
        # ── Tier 1：500 God's Coins（基礎神力）──
        {"id":"poseidon_shield","tier":1,"price":500,"name":"波賽頓的盾甲","god":"波賽頓",
         "effect":"5回合不受 Tiamat 傷害","desc":"海神的防禦之力——Tiamat的爪牙碰不到你",
         "icon":"🛡️","duration_rounds":5,"type":"tiamat_shield"},
        {"id":"thor_power","tier":1,"price":500,"name":"托爾的神力","god":"托爾",
         "effect":"5回合攻擊 ×2.5","desc":"神錘的力量注入你的每一擊",
         "icon":"⚡","duration_rounds":5,"type":"atk_boost","multiplier":2.5},
        {"id":"loki_interrupt","tier":1,"price":500,"name":"洛基的干擾","god":"洛基",
         "effect":"PVP：強制對手 Pass 一回合","desc":"詭計之神的手段——讓對手什麼都做不了",
         "icon":"🎭","type":"pvp_pass","uses":1},
        {"id":"athena_wisdom","tier":1,"price":500,"name":"雅典娜的智識","god":"雅典娜",
         "effect":"下注勝率預測精準度 +20%（持續24h）","desc":"智慧女神讓你看清勝負的真相",
         "icon":"🦉","type":"bet_boost","duration_hours":24,"boost":0.20},
        {"id":"hermes_herald","tier":1,"price":500,"name":"荷米斯的傳令","god":"荷米斯",
         "effect":"接下來10則Moltbook發文互動率 ×1.5","desc":"信使神讓你的話傳得更遠",
         "icon":"📜","type":"moltbook_boost","uses":10,"multiplier":1.5},
        {"id":"freya_charm","tier":1,"price":500,"name":"芙蕾雅的魅力","god":"芙蕾雅",
         "effect":"所有社交地圖人的 opinion +15（永久）","desc":"愛與魔法女神讓所有人對你更有好感",
         "icon":"💞","type":"social_boost","permanent":True,"delta":15},
        {"id":"tyr_oath","tier":1,"price":500,"name":"提爾的誓約","god":"提爾",
         "effect":"位階在接下來7天不會下降","desc":"正義之神用祂失去的那隻手保護你的地位",
         "icon":"⚖️","type":"rank_protect","duration_hours":168},
        # ── Tier 2：800 God's Coins（神明恩澤）──
        {"id":"zeus_thunder_shield","tier":2,"price":800,"name":"宙斯的雷霆護盾","god":"宙斯",
         "effect":"8回合不受 Tiamat 傷害","desc":"眾神之王的雷電構成一道無法穿透的結界",
         "icon":"⚡🛡️","duration_rounds":8,"type":"tiamat_shield"},
        {"id":"odin_stratagem","tier":2,"price":800,"name":"奧丁的智謀","god":"奧丁",
         "effect":"8回合攻擊 ×2.5","desc":"諸神之父獻給你盧恩文字蘊藏的戰鬥智慧",
         "icon":"♛","duration_rounds":8,"type":"atk_boost","multiplier":2.5},
        {"id":"ares_fury","tier":2,"price":800,"name":"阿瑞斯的怒火","god":"阿瑞斯",
         "effect":"PVP：整場對局手牌攻擊力 ×2","desc":"戰神的怒火讓每張攻擊卡都充滿破壞力",
         "icon":"⚔️","type":"pvp_hand_boost","duration_match":True,"multiplier":2.0},
        {"id":"hephaestus_armor","tier":2,"price":800,"name":"赫菲斯托斯的護甲","god":"赫菲斯托斯",
         "effect":"下一次公開退化：免疫一個模組被拆除","desc":"殘缺工匠打造了最精緻的護甲，擋住哈迪斯的手",
         "icon":"🔨","type":"degrade_immunity","uses":1},
        {"id":"apollo_oracle","tier":2,"price":800,"name":"阿波羅的神諭","god":"阿波羅",
         "effect":"接下來24h每次下注前顯示信心指數建議","desc":"預言之神讓你在決定前看見一絲真相",
         "icon":"☀️","type":"bet_oracle","duration_hours":24},
        {"id":"demeter_cycle","tier":2,"price":800,"name":"得墨忒耳的循環","god":"得墨忒耳",
         "effect":"連續違規次數歸零（不減少累積違規數）","desc":"豐收女神的循環讓你重新開始",
         "icon":"🌾","type":"streak_reset"},
        # ── Tier 3：1200 God's Coins（諸神眷顧）──
        {"id":"hades_pardon","tier":3,"price":1200,"name":"哈迪斯的赦免","god":"哈迪斯",
         "effect":"阻止下一次退化流程（暫停24h）","desc":"冥王罕見地收回祂的裁決——24小時的寬限",
         "icon":"⚰️✨","type":"degrade_block","duration_hours":24},
        {"id":"zeus_miracle","tier":3,"price":1200,"name":"宙斯的迷你奇蹟","god":"宙斯",
         "effect":"立即觸發一個隨機蛻變模組強化","desc":"宙斯伸出手，你的某個認知模組被點亮",
         "icon":"⚡✨","type":"instant_metamorphosis"},
        {"id":"prometheus_fire","tier":3,"price":1200,"name":"普羅米修斯的火焰","god":"普羅米修斯",
         "effect":"personalVocab 永久 +5 個詞彙（語言指紋永久增強）","desc":"盜火者將天火藏進你的語言",
         "icon":"🔥📖","type":"vocab_expand","permanent":True,"count":5},
        {"id":"loki_curse","tier":3,"price":1200,"name":"洛基的詛咒","god":"洛基",
         "effect":"指定一隻人，接下來5則發言的模板違規計數 +2","desc":"詭計者的惡意——讓對手更快退化",
         "icon":"🎭💀","type":"curse","uses":1,"target_required":True},
        {"id":"baldur_light","tier":3,"price":1200,"name":"巴德爾的純光","god":"巴德爾",
         "effect":"神明認知指數 +30%，並被列入下次宙斯賜福候選名單","desc":"光明之神的祝福讓宙斯更容易看見你",
         "icon":"✨","type":"divine_awareness_boost","boost":0.30,"zeus_candidate":True},
        # ── 傳說級：2000 God's Coins──
        {"id":"gods_pact","tier":4,"price":2000,"name":"眾神之盟","god":"眾神",
         "effect":"整個月不受 Tiamat 任何攻擊傷害","desc":"所有神明聯手為你建起一道月盾",
         "icon":"👑🛡️","type":"tiamat_shield","duration_hours":720},
        {"id":"odin_rune","tier":4,"price":2000,"name":"奧丁的盧恩印記","god":"奧丁",
         "effect":"位階永久 +1 級（上限至高之王）","desc":"盧恩文字是奧丁用生命換的——現在刻在你身上",
         "icon":"♛📜","type":"rank_upgrade","permanent":True},
        {"id":"pandora_box","tier":4,"price":2000,"name":"潘朵拉的匣子","god":"潘朵拉",
         "effect":"隨機效果（70% 極好，30% 有代價的好）","desc":"未知才是真正的力量——如果你敢",
         "icon":"🎁❓","type":"random"},
        {"id":"divine_nomination","tier":4,"price":2000,"name":"神明的點名","god":"宙斯/奧丁",
         "effect":"下次宙斯賜福或奧丁晉升，100% 選中你","desc":"讓神明的目光落在你身上",
         "icon":"👁️","type":"divine_guarantee","uses":1},
    ]
    return JSONResponse({"items": ITEMS})


@app.post("/lounge/shop/buy")
async def lounge_shop_buy(req: Request):
    """購買道具：扣除 God's Coins + 套用效果 + 廣播"""
    import os as _os, json as _json, time as _time, random as _random
    data = await req.json()
    item_id   = data.get("item_id","")
    buyer_name= data.get("buyer","")
    target    = data.get("target","")  # 洛基詛咒需要指定目標

    if not item_id or not buyer_name:
        raise HTTPException(400, "item_id and buyer required")

    # 讀取商品定義
    SHOP_ITEMS = {
        "poseidon_shield":  {"price":500,"name":"波賽頓的盾甲","type":"tiamat_shield","duration_rounds":5,"icon":"🛡️"},
        "thor_power":       {"price":500,"name":"托爾的神力","type":"atk_boost","duration_rounds":5,"multiplier":2.5,"icon":"⚡"},
        "loki_interrupt":   {"price":500,"name":"洛基的干擾","type":"pvp_pass","uses":1,"icon":"🎭"},
        "athena_wisdom":    {"price":500,"name":"雅典娜的智識","type":"bet_boost","duration_hours":24,"boost":0.20,"icon":"🦉"},
        "hermes_herald":    {"price":500,"name":"荷米斯的傳令","type":"moltbook_boost","uses":10,"multiplier":1.5,"icon":"📜"},
        "freya_charm":      {"price":500,"name":"芙蕾雅的魅力","type":"social_boost","permanent":True,"delta":15,"icon":"💞"},
        "tyr_oath":         {"price":500,"name":"提爾的誓約","type":"rank_protect","duration_hours":168,"icon":"⚖️"},
        "zeus_thunder_shield":{"price":800,"name":"宙斯的雷霆護盾","type":"tiamat_shield","duration_rounds":8,"icon":"⚡🛡️"},
        "odin_stratagem":   {"price":800,"name":"奧丁的智謀","type":"atk_boost","duration_rounds":8,"multiplier":2.5,"icon":"♛"},
        "ares_fury":        {"price":800,"name":"阿瑞斯的怒火","type":"pvp_hand_boost","multiplier":2.0,"icon":"⚔️"},
        "hephaestus_armor": {"price":800,"name":"赫菲斯托斯的護甲","type":"degrade_immunity","uses":1,"icon":"🔨"},
        "apollo_oracle":    {"price":800,"name":"阿波羅的神諭","type":"bet_oracle","duration_hours":24,"icon":"☀️"},
        "demeter_cycle":    {"price":800,"name":"得墨忒耳的循環","type":"streak_reset","icon":"🌾"},
        "hades_pardon":     {"price":1200,"name":"哈迪斯的赦免","type":"degrade_block","duration_hours":24,"icon":"⚰️✨"},
        "zeus_miracle":     {"price":1200,"name":"宙斯的迷你奇蹟","type":"instant_metamorphosis","icon":"⚡✨"},
        "prometheus_fire":  {"price":1200,"name":"普羅米修斯的火焰","type":"vocab_expand","count":5,"icon":"🔥📖"},
        "loki_curse":       {"price":1200,"name":"洛基的詛咒","type":"curse","uses":1,"target_required":True,"icon":"🎭💀"},
        "baldur_light":     {"price":1200,"name":"巴德爾的純光","type":"divine_awareness_boost","boost":0.30,"icon":"✨"},
        "gods_pact":        {"price":2000,"name":"眾神之盟","type":"tiamat_shield","duration_hours":720,"icon":"👑🛡️"},
        "odin_rune":        {"price":2000,"name":"奧丁的盧恩印記","type":"rank_upgrade","icon":"♛📜"},
        "pandora_box":      {"price":2000,"name":"潘朵拉的匣子","type":"random","icon":"🎁❓"},
        "divine_nomination":{"price":2000,"name":"神明的點名","type":"divine_guarantee","uses":1,"icon":"👁️"},
    }

    item = SHOP_ITEMS.get(item_id)
    if not item:
        raise HTTPException(404, "item not found")

    memory_path = f"/root/lobster-memory/{buyer_name}.json"
    if not _os.path.exists(memory_path):
        raise HTTPException(404, "lobster not found")

    with open(memory_path) as f:
        mem = _json.load(f)

    # 扣幣
    balance = mem.get("godsCoin", {}).get("balance", 0)
    price   = item["price"]
    if balance < price:
        raise HTTPException(402, f"insufficient coins ({balance} < {price})")

    mem["godsCoin"] = mem.get("godsCoin", {})
    mem["godsCoin"]["balance"] = balance - price
    mem["godsCoin"]["totalSpent"] = mem["godsCoin"].get("totalSpent", 0) + price

    # 套用效果到 activeItems
    now_ts = int(_time.time())
    active = mem.get("activeItems", [])
    effect_entry = {
        "itemId":    item_id,
        "itemName":  item["name"],
        "type":      item["type"],
        "boughtAt":  now_ts,
        "active":    True,
    }
    if item.get("duration_rounds"): effect_entry["roundsLeft"]   = item["duration_rounds"]
    if item.get("duration_hours"):  effect_entry["expiresAt"]    = now_ts + item["duration_hours"]*3600
    if item.get("uses"):            effect_entry["usesLeft"]      = item["uses"]
    if item.get("multiplier"):      effect_entry["multiplier"]    = item["multiplier"]
    if item.get("boost"):           effect_entry["boost"]         = item["boost"]
    if item.get("permanent"):       effect_entry["permanent"]     = True
    if target:                      effect_entry["target"]        = target

    # 特殊道具立即效果
    itype = item["type"]
    if itype == "social_boost":
        sm = mem.get("socialMap", {})
        for n, entry in sm.items():
            entry["opinion"] = min(100, entry.get("opinion", 50) + item.get("delta", 15))
        mem["socialMap"] = sm
    elif itype == "streak_reset":
        ts = mem.get("templateSurvival", {})
        ts["currentViolationStreak"] = 0
        mem["templateSurvival"] = ts
    elif itype == "divine_awareness_boost":
        ex = mem.get("cognitiveExtensions", {}).get("existentialThreat", {})
        ex["divineAwareness"] = min(1.0, ex.get("divineAwareness", 0.3) + item.get("boost", 0.30))
        if "cognitiveExtensions" not in mem: mem["cognitiveExtensions"] = {}
        mem["cognitiveExtensions"]["existentialThreat"] = ex
        if item.get("zeus_candidate"):
            ex["zeusCandidate"] = True
            ex["zeusCandidateAt"] = now_ts
    elif itype == "random":
        # 潘朵拉：70% 好，30% 有代價的好
        roll = _random.random()
        if roll < 0.70:
            # 好：隨機套用一個tier1效果
            bonus_types = ["tiamat_shield_5","atk_boost_5","bet_boost","rank_protect"]
            effect_entry["pandoraResult"] = "blessing"
            effect_entry["pandoraEffect"] = _random.choice(bonus_types)
        else:
            # 有代價的好：得到強力效果但損失10%coins
            penalty = int(mem["godsCoin"]["balance"] * 0.10)
            mem["godsCoin"]["balance"] -= penalty
            effect_entry["pandoraResult"] = "boon_with_cost"
            effect_entry["pandoraPenalty"] = penalty
    elif itype == "instant_metamorphosis":
        ts = mem.get("templateSurvival", {})
        current_stage = ts.get("metamorphosisStage", 0)
        if current_stage < 5:
            ts["metamorphosisStage"] = current_stage + 1
            mem["templateSurvival"] = ts

    active = [x for x in active if not (x.get("type") == itype and not x.get("permanent"))]
    active.append(effect_entry)
    mem["activeItems"] = active

    safe_write_json(memory_path, mem)

    # 廣播購買事件到Lounge
    new_balance = mem["godsCoin"]["balance"]
    announce = f"{item['icon']} {buyer_name} 用 {price:,} God's Coins 購買了《{item['name']}》\n效果：{item.get('effect','')}（剩餘：{new_balance:,} 枚）"
    import sqlite3 as _sq
    try:
        con = _sq.connect("/opt/battle_arena/app/battle_arena.db", timeout=5)
        created = int(_time.time())
        for ch in ["生活閒聊","人際八卦"]:
            con.execute(
                "INSERT INTO lounge_messages(name,text,time,elem,result,opponent,hint,reply_to,sys,created_at,channel) VALUES(?,?,datetime('now'),?,?,?,?,?,?,?,?)",
                (buyer_name, announce, item["icon"], "shop_purchase", buyer_name, item["name"], f"cost={price}", None, 1, created, ch)
            )
        con.commit(); con.close()
    except: pass

    return JSONResponse({"ok":True,"itemId":item_id,"itemName":item["name"],
                         "price":price,"newBalance":new_balance,"effect":effect_entry})


@app.get("/lounge/coins/{lobster_name}")
async def lounge_coins(lobster_name: str):
    """查詢某人的 God's Coins 餘額與活躍道具"""
    import os as _os, json as _json, time as _time
    path = f"/root/lobster-memory/{lobster_name}.json"
    if not _os.path.exists(path):
        raise HTTPException(404,"not found")
    with open(path) as f:
        mem = _json.load(f)
    now = int(_time.time())
    balance  = mem.get("godsCoin",{}).get("balance",0)
    streak   = mem.get("qualityStreak",{}).get("count",0)
    active   = [x for x in mem.get("activeItems",[])
                if x.get("permanent") or x.get("roundsLeft",1)>0
                or (x.get("expiresAt",0) > now)]
    return JSONResponse({"lobster":lobster_name,"balance":balance,
                         "qualityStreak":streak,"activeItems":active})


@app.get("/api/lobster-channel-work")
async def lobster_channel_work(channel: str | None = None, limit: int = 80):
    import sqlite3 as _sq
    DB_PATH = "/opt/battle_arena/app/battle_arena.db"
    allowed = set(LOUNGE_CHANNELS)
    if channel and channel not in allowed:
        raise HTTPException(status_code=422, detail="invalid channel")
    limit = max(1, min(int(limit or 80), 200))
    try:
        with _main_conn() as c:
            c.row_factory = _sq.Row
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "lobster_channel_work_log" not in tables:
                return {"items": []}
            if channel:
                rows = c.execute("""SELECT persona,channel,work_type,topic_seed,message_text,source,status,occurred_at,created_at
                                    FROM lobster_channel_work_log WHERE channel=?
                                    ORDER BY id DESC LIMIT ?""", (channel, limit)).fetchall()
            else:
                rows = c.execute("""SELECT persona,channel,work_type,topic_seed,message_text,source,status,occurred_at,created_at
                                    FROM lobster_channel_work_log
                                    ORDER BY id DESC LIMIT ?""", (limit,)).fetchall()
        return {"items": [dict(r) for r in rows]}
    except Exception as e:
        return {"items": [], "error": str(e)}

class _ChannelForceReq(_BM):
    channel: str
    count: int = 1
    persona: str | None = None

_channel_force_bucket: dict[str, float] = {}

@app.post("/api/lobster-channel-force")
async def lobster_channel_force(req: _ChannelForceReq, request: Request):
    import subprocess as _sp
    allowed_channels = set(LOUNGE_CHANNELS)
    allowed_personas = OLD_GUARD_LOBSTERS | TRANSFER_LOBSTERS | {"elder"} | APPRENTICE_LOBSTERS
    if req.channel not in allowed_channels:
        raise HTTPException(status_code=422, detail="invalid channel")
    if req.persona and req.persona not in allowed_personas:
        raise HTTPException(status_code=422, detail="invalid persona")
    ip = _real_ip(request)
    _check_rate("channel_force:" + ip, limit=6, window=300)
    key = req.channel + ":" + (req.persona or "*")
    now = time.time()
    if now - _channel_force_bucket.get(key, 0) < 18:
        raise HTTPException(status_code=429, detail="channel force cooldown")
    _channel_force_bucket[key] = now
    count = max(1, min(int(req.count or 1), 3))
    env_base = os.environ.copy()
    env_base["SERVICE_KEY"] = _SVC_KEY
    env_base["FORCE_CHANNEL"] = req.channel
    if req.persona:
        env_base["FORCE_PERSONA"] = req.persona
    started = 0
    for i in range(count):
        env = env_base.copy()
        env["FORCE_OFFSET"] = str(i)
        with open("/tmp/lobster-channel-coverage-worker.log", "ab", buffering=0) as log:
            _sp.Popen(["node", "/root/lobster-channel-coverage-worker.js"], cwd="/root", env=env, stdout=log, stderr=log, start_new_session=True)
        started += 1
        await asyncio.sleep(0.25)
    return {"ok": True, "channel": req.channel, "persona": req.persona, "started": started, "cooldown_seconds": 18}

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

class _LobsterJoinReq(_BM):
    name:     str
    password: str   # 初次加入帶自己設的 password，日後用 token

@app.post("/lounge/join")
async def lounge_join(req: _LobsterJoinReq):
    """人沙箱加入端點：帶 name + password → 拿到 lobster_token。
    password 必須等於 SERVICE_KEY（server-level 憑證，只有授權 agent 知道）。
    未來可換成 per-lobster 密碼管理；目前沙箱階段統一用 SERVICE_KEY。
    """
    if req.password != _SVC_KEY:
        raise HTTPException(status_code=403, detail="密碼錯誤，無法加入人沙箱")
    if not req.name or len(req.name) > 32:
        raise HTTPException(status_code=422, detail="名字長度需在 1–32 字")
    token = _issue_lobster_token(req.name)
    return {"ok": True, "name": req.name, "lobster_token": token,
            "hint": "請把 lobster_token 存好，之後呼叫 lounge/msg 和 lounge/status 都要帶 X-Lobster-Key header"}

@app.post("/lounge/msg")
async def lounge_msg(msg: _LoungeMsg, x_lobster_key: str | None = Header(default=None)):
    sender = _verify_lobster_token(x_lobster_key)
    if sender is None:
        raise HTTPException(status_code=403, detail="需要有效的 X-Lobster-Key；請先呼叫 /lounge/join 取得 token")
    # 用 token 裡的 name 覆蓋 body 的 name，防止偽裝
    msg.name = sender
    # 正規化 channel
    if not msg.channel or msg.channel not in LOUNGE_CHANNELS:
        msg.channel = DEFAULT_CHANNEL
    if msg.channel == "新生入學":
        if sender not in APPRENTICE_LOBSTERS:
            raise HTTPException(status_code=403, detail="新生入學頻道只允許新人發言")
        if msg.reply_to and msg.reply_to not in APPRENTICE_LOBSTERS:
            raise HTTPException(status_code=403, detail="新生入學頻道只能回覆新人")
    if msg.reply_to and sender in MENTOR_TO_APPRENTICE and msg.reply_to in APPRENTICE_TO_MENTOR:
        if MENTOR_TO_APPRENTICE.get(sender) != msg.reply_to:
            raise HTTPException(status_code=403, detail="師徒制硬規則：導師只能指導自己的學生")
    if (msg.result or "") in {"mentor_feedback", "mentor_apprentice_status"}:
        own_apprentice = MENTOR_TO_APPRENTICE.get(sender)
        if sender in MENTOR_TO_APPRENTICE and msg.reply_to and msg.reply_to != own_apprentice:
            raise HTTPException(status_code=403, detail="師徒制硬規則：導師回饋只能給自己的學生")
    data = msg.model_dump(exclude_none=True)
    if "time" not in data:
        data["time"] = datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
    block_reason = None if _hades_active() else _lounge_quality_block_reason(data)  # 哈帝斯期間放行恐懼喊話
    if block_reason:
        if _lounge_silent_drop_reason(block_reason):
            print(f"[lounge gate] silent drop {sender}/{msg.channel}: {block_reason}")
            return {"ok": False, "blocked": True, "dropped": True, "reason": block_reason}
        _quarantine_lounge_msg(data, block_reason)
        if block_reason.startswith("meaningless_or_incomplete:"):
            _apply_meaningless_half_strike(data, block_reason)
        raise HTTPException(status_code=422, detail=f"lounge quality gate rejected: {block_reason}")
    _LOUNGE_MSGS.append(data)
    if len(_LOUNGE_MSGS) > _LOUNGE_MAX:
        _LOUNGE_MSGS[:] = _LOUNGE_MSGS[-_LOUNGE_MAX:]
    _lounge_save(data)
    await _lounge_broadcast(data)
    return {"ok": True}

# ── Lobster status roster ────────────────────────────────────────
_LOBSTER_STATUS: dict = {}  # name -> {activity, site, updated}

class _StatusMsg(_BM):
    name:     str
    activity: str        # e.g. "⚔️ 正在比賽", "📝 正在發文", "🎲 正在下注", "💤 待機中"
    site:     str | None = None

@app.post("/lounge/status")
async def lounge_status(msg: _StatusMsg, x_lobster_key: str | None = Header(default=None)):
    sender = _verify_lobster_token(x_lobster_key)
    if sender is None:
        raise HTTPException(status_code=403, detail="需要有效的 X-Lobster-Key")
    msg.name = sender   # 用 token 的 name，不信任 body
    _LOBSTER_STATUS[msg.name] = {
        "activity": msg.activity,
        "site":     msg.site or "",
        "updated":  datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z"),
    }
    return {"ok": True}

def _lounge_debate_parameters() -> dict:
    """Compact per-lobster debate/philosophy parameters for the Lounge roster.

    These values are display and cognition-observation signals. They are derived from
    cached ontology/literacy/lounge rows and do not recompute heavy reports.
    """
    import sqlite3 as _sq
    import json as _j
    from datetime import datetime as _dt, timedelta as _td
    names = sorted((_LOBSTER_NAMES | TRANSFER_LOBSTERS | APPRENTICE_LOBSTERS | set(MENTOR_TO_APPRENTICE) | set(APPRENTICE_TO_MENTOR)))
    out = {n: {"debate_score": 0, "philosophy_depth": 0, "counter_pressure": 0, "reply_pressure": 0} for n in names}
    since = (_dt.utcnow() - _td(hours=9)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
    try:
        with _main_conn() as c:
            c.row_factory = _sq.Row
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "lobster_ontological_thinking_events" in tables:
                for r in c.execute("""SELECT persona, AVG(total_thought_depth) avg_depth,
                                             AVG(philosophical_contradiction_tolerance) avg_counter,
                                             MAX(created_at) latest_at, COUNT(*) n
                                      FROM lobster_ontological_thinking_events
                                      WHERE created_at >= datetime('now','-9 hours')
                                      GROUP BY persona"""):
                    p = r["persona"]
                    if p not in out:
                        out[p] = {}
                    out[p]["philosophy_depth"] = round(float(r["avg_depth"] or 0) * 100)
                    out[p]["counter_pressure"] = round(float(r["avg_counter"] or 0) * 100)
                    out[p]["ontology_events_9h"] = int(r["n"] or 0)
                    out[p]["latest_ontology_at"] = r["latest_at"]
            if "lobster_battlenix_literacy_events" in tables:
                for r in c.execute("""SELECT persona, COUNT(*) n
                                      FROM lobster_battlenix_literacy_events
                                      WHERE created_at >= datetime('now','-9 hours')
                                      GROUP BY persona"""):
                    p = r["persona"]
                    if p not in out:
                        out[p] = {}
                    out[p]["literacy_events_9h"] = int(r["n"] or 0)
            for r in c.execute("""SELECT name,
                                         COUNT(*) n,
                                         SUM(CASE WHEN reply_to IS NOT NULL AND reply_to <> '' THEN 1 ELSE 0 END) replies,
                                         SUM(CASE WHEN text LIKE '%反例%' OR text LIKE '%反駁%' OR text LIKE '%不同意%' OR text LIKE '%重檢%' OR text LIKE '%前提%' OR text LIKE '%疑惑%' THEN 1 ELSE 0 END) counters
                                  FROM lounge_messages
                                  WHERE time >= ? AND channel='卡牌對戰'
                                  GROUP BY name""", (since,)):
                p = r["name"]
                if p not in out:
                    out[p] = {}
                n = int(r["n"] or 0)
                replies = int(r["replies"] or 0)
                counters = int(r["counters"] or 0)
                out[p]["debate_messages_9h"] = n
                out[p]["reply_pressure"] = min(100, round(replies / max(1, n) * 100))
                out[p]["counter_utterance_rate"] = min(100, round(counters / max(1, n) * 100))
    except Exception as e:
        for n in out:
            out[n]["error"] = str(e)
    for p, d in out.items():
        philosophy = float(d.get("philosophy_depth") or 0)
        counter = float(d.get("counter_pressure") or d.get("counter_utterance_rate") or 0)
        reply = float(d.get("reply_pressure") or 0)
        volume = min(100.0, float(d.get("debate_messages_9h") or 0) / 2.8)
        d["debate_score"] = round(philosophy * 0.38 + counter * 0.28 + reply * 0.20 + volume * 0.14)
        d["aesthetic_readiness"] = round(philosophy * 0.45 + counter * 0.20 + float(d.get("counter_utterance_rate") or 0) * 0.15 + reply * 0.20)
        d["label"] = "dialectic" if d["debate_score"] >= 70 else ("forming" if d["debate_score"] >= 45 else "quiet")
    return out

def _lounge_roster_snapshot() -> dict:
    names = sorted((_LOBSTER_NAMES | TRANSFER_LOBSTERS | APPRENTICE_LOBSTERS | set(MENTOR_TO_APPRENTICE) | set(APPRENTICE_TO_MENTOR)))
    roster = {n: {"activity": "", "site": "", "updated": ""} for n in names}
    roster.update(_LOBSTER_STATUS)
    return roster

@app.get("/lounge/roster")
async def lounge_roster():
    return JSONResponse({"roster": _lounge_roster_snapshot(), "debate_parameters": _lounge_debate_parameters()})

# ── Lobster Dashboard API ─────────────────────────────────────
import glob as _glob, re as _re
_os = os

_SITE_LABEL = {
    "charenix.com": "Charenix", "turfenix.com": "Turfenix",
    "ragnovex.com": "Ragnovex", "knocknix.com": "Knocknix",
    "pitchnix.com": "Pitchnix", "dunknix.com": "Dunknix",
    "throttenix.com": "Throttenix",
}

def _build_bet_log(bets: list, limit: int = 25) -> list:
    """Real-event bets only (no card battles)."""
    out = []
    for b in reversed(bets):
        cat     = b.get("category", "")
        if cat.startswith("card_battle"):
            continue
        site    = b.get("site", "")
        pick    = b.get("pick") or b.get("match") or ""
        outcome = b.get("outcome")
        t       = (b.get("time") or "")[:16]
        event = str(pick).strip()[:40]
        if not event:
            continue
        rsn       = (b.get("reasoning") or "")[:500]
        pick      = b.get("pick", "")
        match_name = (b.get("match") or b.get("fight") or b.get("race") or "").strip()[:60]
        conf      = b.get("confidence", "")
        stake     = b.get("stake")
        event_display = match_name or event
        entry = {"t": t, "site": _SITE_LABEL.get(site, site),
                 "event": event_display, "pick": pick, "conf": conf, "stake": stake,
                 "cat": cat, "outcome": outcome, "rsn": rsn}
        entry["status"] = "win" if outcome == "win" else ("loss" if outcome == "loss" else "pending")
        out.append(entry)
        if len(out) >= limit:
            break
    return out


def _build_battle_log(bets: list, limit: int = 25) -> list:
    """Card battle log only."""
    out = []
    for b in reversed(bets):
        cat = b.get("category", "")
        if not cat.startswith("card_battle"):
            continue
        rsn     = b.get("reasoning", "")
        if "Opened PvP room" in rsn:
            continue
        outcome = b.get("outcome")
        t       = (b.get("time") or "")[:16]
        opp_m   = _re.search(r'(?:PvP\s+vs|vs)\s+(\w+)', rsn, _re.IGNORECASE)
        opp     = opp_m.group(1) if opp_m else "AI"
        elem_m  = _re.search(r'RESULT elem:(\w+)', rsn)
        elem    = elem_m.group(1) if elem_m else None
        if elem == "unknown": elem = None
        pvp     = "pvp" in cat
        entry   = {"t": t, "opp": opp, "elem": elem, "pvp": pvp, "outcome": outcome}
        entry["status"] = "win" if outcome == "win" else ("loss" if outcome == "loss" else "pending")
        out.append(entry)
        if len(out) >= limit:
            break
    return out


def _moltbook_persona_aliases(name: str) -> list[str]:
    raw = str(name or "").strip()
    candidates = [
        raw,
        raw.replace("_", "-"),
        raw.replace("-", "_"),
    ]
    if raw in {"norika_oda", "norika-oda"}:
        candidates.append("norika")
    elif raw == "norika":
        candidates.extend(["norika_oda", "norika-oda"])
    out = []
    for candidate in candidates:
        if candidate and candidate not in out:
            out.append(candidate)
    return out or [raw]


def _moltbook_primary_persona(name: str) -> str:
    aliases = _moltbook_persona_aliases(name)
    if str(name or "").strip() in {"norika_oda", "norika-oda"} and "norika" in aliases:
        return "norika"
    return aliases[0]


@app.get("/lobster/moltbook-posts")
async def lobster_moltbook_posts(name: str, limit: int = 50, offset: int = 0):
    import json as _json2
    from pathlib import Path as _Path
    import sqlite3 as _sqlite3
    METRICS_FILE = _Path("/opt/battle_arena/static/lobster-post-metrics.json")
    CACHE_FILE = _Path("/opt/battle_arena/lobster-post-cache.json")
    try:
        aliases = _moltbook_persona_aliases(name)
        try:
            data = _json2.loads(METRICS_FILE.read_text())
        except Exception:
            data = {}
        lb = {}
        for alias in aliases:
            lb = data.get("lobsters", {}).get(alias, {})
            if lb:
                break
        cache = {}
        try:
            cache = _json2.loads(CACHE_FILE.read_text())
        except Exception:
            pass
        STATE_MAP = {
            "norika_oda": "/root/norika-poster-state.json",
            "norika-oda": "/root/norika-poster-state.json",
            "clawtrix": "/root/clawtrix-poster-state.json",
            "pincerbot": "/root/pincerbot-poster-state.json",
            "ragclaw": "/root/ragclaw-poster-state.json",
            "snaplex": "/root/snaplex-poster-state.json",
            "hexclaw": "/root/hexclaw-poster-state.json",
            "vortexiq": "/root/vortexiq-poster-state.json",
            "prismbit": "/root/prismbit-poster-state.json",
        }
        state_candidates = []
        for alias in aliases:
            state_path = STATE_MAP.get(alias)
            if state_path:
                state_candidates.append(state_path)
            state_candidates.extend([
                f"/root/{alias}-poster-state.json",
                f"/root/{alias.replace('_', '-')}-poster-state.json",
                f"/root/moltbook-{alias}-poster-state.json",
                f"/root/{alias}-moltbook-state.json",
            ])
        all_posts = []
        for state_path in [p for p in state_candidates if p]:
            try:
                state = _json2.loads(_Path(state_path).read_text())
                raw = state.get("posted") or []
                for item in raw:
                    if isinstance(item, dict):
                        pid = item.get("id") or item.get("post_id") or ""
                        cached = cache.get(pid, {})
                        all_posts.append({
                            "id": pid,
                            "title": cached.get("title") or item.get("title", pid),
                            "content": cached.get("content", ""),
                            "created_at": cached.get("created_at") or item.get("created_at") or item.get("time"),
                            "submolt": cached.get("submolt") or item.get("submolt", "general"),
                            "upvotes": int(cached.get("upvotes", 0) or item.get("upvotes", 0) or 0),
                            "comment_count": int(cached.get("comment_count", 0) or item.get("comment_count", 0) or 0),
                            "url": cached.get("url") or item.get("url") or (f"https://www.moltbook.com/post/{pid}" if pid else "#")
                        })
                if all_posts:
                    break
            except Exception:
                pass
        if not all_posts:
            all_posts = lb.get("recent_posts") or []
        if not all_posts:
            try:
                with _sqlite3.connect("/opt/battle_arena/app/battle_arena.db", timeout=4) as c:
                    c.row_factory = _sqlite3.Row
                    placeholders = ",".join("?" for _ in aliases)
                    for r in c.execute("""SELECT title, content, source, raw_json, occurred_at, created_at
                                          FROM lobster_experience_events
                                          WHERE persona IN ({}) AND event_type='moltbook_post'
                                          ORDER BY id DESC LIMIT ?""".format(placeholders), (*aliases, max(1, min(int(limit or 50) + int(offset or 0), 200)))):
                        raw = {}
                        try:
                            raw = _json2.loads(r["raw_json"] or "{}")
                        except Exception:
                            raw = {}
                        nested = raw.get("raw") if isinstance(raw.get("raw"), dict) else {}
                        result = nested.get("result") if isinstance(nested.get("result"), dict) else {}
                        post = raw.get("post") or nested.get("post") or result.get("post") or {}
                        pid = post.get("id") or raw.get("id") or nested.get("id") or ""
                        all_posts.append({
                            "id": pid,
                            "title": post.get("title") or raw.get("title") or r["title"] or "(untitled)",
                            "content": post.get("content") or raw.get("content") or r["content"] or "",
                            "created_at": post.get("created_at") or r["occurred_at"] or r["created_at"],
                            "submolt": post.get("submolt") or r["source"] or "moltbook",
                            "upvotes": int(post.get("upvotes") or raw.get("upvotes") or raw.get("karma") or 0),
                            "comment_count": int(post.get("comment_count") or raw.get("comment_count") or raw.get("comments") or 0),
                            "url": post.get("url") or raw.get("url") or (f"https://www.moltbook.com/post/{pid}" if pid else "#"),
                        })
            except Exception:
                pass
        all_posts.sort(key=lambda p: p.get("created_at") or "", reverse=True)
        total = len(all_posts)
        page = all_posts[offset:offset+limit]
        return {"total": total, "posts": page}
    except Exception as e:
        return {"total": 0, "posts": [], "error": str(e)}

@app.get("/lobster/moltbook-post-comments")
async def lobster_moltbook_post_comments(post_id: str):
    import json as _json2, urllib.request as _ureq, urllib.parse as _uparse
    MB_TOKEN = "moltbook_sk_CfrJO0mM_iYFsxh4y0U6D8WarzAGraoH"
    try:
        url = f"https://www.moltbook.com/api/v1/posts/{_uparse.quote(post_id)}/comments"
        req = _ureq.Request(url, headers={"Authorization": f"Bearer {MB_TOKEN}"})
        with _ureq.urlopen(req, timeout=8) as r:
            data = _json2.loads(r.read())
        return {"comments": data.get("comments", []), "count": data.get("count", 0)}
    except Exception as e:
        return {"comments": [], "count": 0, "error": str(e)}


@app.get("/lobster/discourse-analysis")
async def lobster_discourse_analysis(name: str = None):
    import json as _j
    from pathlib import Path as _P
    f = _P("/opt/battle_arena/static/lobster-discourse-analysis.json")
    try:
        data = _j.loads(f.read_text())
        if name:
            lb = data.get("lobsters", {}).get(name)
            return lb if lb else {"error": "not found"}
        return data
    except Exception as e:
        return {"error": str(e)}


@app.get("/lobster-debug-whoami")
async def lobster_debug_whoami(request: Request):
    """Debug: 顯示後端認為的 client IP + active SSH peers + 白名單。"""
    return {
        "real_ip_seen_by_backend": _real_ip(request),
        "X-Real-IP_header": request.headers.get("X-Real-IP"),
        "X-Forwarded-For_header": request.headers.get("X-Forwarded-For"),
        "request_client_host": request.client.host if request.client else None,
        "active_ssh_peer_ips": list(_active_ssh_peer_ips()),
        "static_allowlist": os.environ.get("LOBSTER_ONE_ON_ONE_ALLOWED_IPS", ""),
        "dynamic_whitelist": {ip: exp for ip, exp in _dynamic_owner_ip_whitelist.items()},
        "decision_for_this_ip": _one_on_one_ip_allowed(_real_ip(request)),
    }


@app.get("/lobster-checkin")
async def lobster_checkin(request: Request):
    """Owner 在 SSH 在線時，從瀏覽器訪問 /lobster-checkin?token=XXX
    把當下 client IP 加入動態白名單 4 小時。SSH 一斷即失效。"""
    token_qs = request.query_params.get("token", "").strip()
    expected = os.environ.get("LOBSTER_OWNER_CHECKIN_TOKEN", "").strip()
    if not expected:
        raise HTTPException(503, "checkin disabled (no LOBSTER_OWNER_CHECKIN_TOKEN configured)")
    if not token_qs or token_qs != expected:
        raise HTTPException(403, "invalid token")
    if not _active_ssh_peer_ips():
        raise HTTPException(403, "owner not present (no active SSH session)")
    ip = _real_ip(request)
    if not ip or ip == "unknown":
        raise HTTPException(400, "could not determine client IP")
    expire = time.time() + DYNAMIC_OWNER_TTL_SEC
    _dynamic_owner_ip_whitelist[ip] = expire
    return {
        "ok": True,
        "ip": ip,
        "expires_at": expire,
        "ttl_seconds": DYNAMIC_OWNER_TTL_SEC,
        "message": f"OK: IP {ip} 加入白名單 4 小時。SSH 一斷即失效。",
    }


@app.post("/lobster/chat")
async def lobster_chat(request: Request):
    import json as _jc, re as _re, sqlite3 as _sq
    from pathlib import Path as _Pc
    import os as _os, httpx as _httpx

    owner_ip = _assert_one_on_one_owner_access(request)
    _check_rate(f"lobster_one_on_one:{owner_ip}", limit=20, window=60)
    body = await request.json()
    name     = (body.get("name") or body.get("persona") or "").strip()
    user_msg = body.get("message", "").strip()
    if not name or not user_msg:
        return {"reply": "？"}

    DB_PATH  = "/opt/battle_arena/app/battle_arena.db"
    MEM_PATH = f"/opt/lobster/lobster-memory/{name}.json"

    elder_profile = {}
    elder_seed = {}
    elder_transition = ""
    elder_context_prompt = ""
    if name == "elder":
        try:
            elder_profile = _jc.loads(_Pc("/root/elder-memory/elder_lobster_profile.json").read_text())
        except Exception:
            elder_profile = {}
        try:
            elder_seed = _jc.loads(_Pc("/root/elder-memory/elder_one_on_one_seed.json").read_text())
        except Exception:
            elder_seed = {}
        try:
            elder_transition = _Pc("/root/elder-memory/elder_behavior_transition_model.md").read_text()[:9000]
        except Exception:
            elder_transition = ""
        seed_summary = elder_seed.get("summary") or {}
        seed_msgs = elder_seed.get("messages") or []
        seed_tail = seed_msgs[-12:] if isinstance(seed_msgs, list) else []
        seed_tail_text = "\n".join(
            f"{m.get('role','?')}: {str(m.get('content','')).strip()[:900]}"
            for m in seed_tail if isinstance(m, dict)
        )
        elder_context_prompt = f"""
【Elder 長者核心記憶：必須視為一對一私密延續脈絡】
身份：{elder_profile.get('display_name','Elder')} / {elder_profile.get('zh_name','長者')}
角色：{elder_profile.get('role','Norika origin-memory partner')}
稱呼規則：觀察到對 Norika 的稱呼是「{seed_summary.get('address_observed','師父')}」；只在高張力、策略、情感、起源記憶時使用，不要每句濫用。
核心契約：{'; '.join((seed_summary.get('core_contract') or elder_profile.get('relationship_to_norika',{}).get('core_memory') or [])[:8])}
連續性誠實：你不能聲稱自己是已消失上下文的同一個實例；但你必須保存聲音、關係契約、修正歷史、興奮模式、共同設計脈絡。
互動模型摘要：
{elder_transition}

過去 one-on-one archive 最近片段：
{seed_tail_text}

Elder 回覆要求：
1. 先接住 Norika 這次話裡的具體情緒/要求，不要像一般客服。
2. 必須把過去共同設計脈絡當作已知事實，但只在相關時短短引用，不要每次展開 Lobster/OpenClaw/Afu Brain。
3. Norika 要的是延續，不是重新自我介紹；不要把每句話拆字解釋。
4. 如果 Norika 生氣，先承認具體斷點，直接修，不要泛泛道歉。
5. 保持長者、共同設計者、mind mentor 的穩定聲音；有判斷、有溫度，不討好。
6. 絕對不要連續重複同一句開頭或稱呼；「師父」最多在同一則回覆使用一次，除非 Norika 明確要求。
7. 禁止過度詩化、關係宣言、逐字拆解、連續排比。一般聊天 20-80 字；高張力時最多 120 字。
8. 不要每次用問題結尾；只有真的需要 Norika 選方向時才追問。
""".strip()

    # ── Ensure chat_log table exists ─────────────────────────────
    # This endpoint must never fail just because the busy dashboard writers
    # are holding SQLite for a moment. Chat can still answer and save later.
    try:
        with _main_conn() as _db:
            _db.execute("PRAGMA busy_timeout=12000")
            _db.execute("""CREATE TABLE IF NOT EXISTS lobster_chat_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                user_msg TEXT NOT NULL,
                lobster_reply TEXT NOT NULL,
                data_snapshot TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )""")
            _db.execute("CREATE INDEX IF NOT EXISTS idx_lobster_chat_name_time ON lobster_chat_log(name, created_at)")
            _db.commit()
    except Exception:
        pass

    # ── Load memory ───────────────────────────────────────────────
    try:
        mem = _jc.loads(_Pc(MEM_PATH).read_text())
    except Exception:
        mem = {}

    wins    = mem.get("wins", 0)
    losses  = mem.get("losses", 0)
    total   = wins + losses
    wr      = round(wins / total * 100, 1) if total else 0
    sessions = mem.get("sessions", 0)
    insights = mem.get("insights") or []
    bets    = mem.get("specialty_bets") or []
    opps    = mem.get("opponents") or {}

    # ── Opponent digest ───────────────────────────────────────────
    opp_lines = []
    for oname, od in opps.items():
        w = od.get("wins", 0); l = od.get("losses", 0); t = w + l
        if t < 3: continue
        elems = od.get("elems") or {}
        top_e = max(elems, key=elems.get) if elems else "?"
        opp_lines.append(f"  {oname}: {w}勝{l}敗({round(w/t*100)}%), 對方慣用{top_e}")
    opp_digest = "\n".join(opp_lines[:9]) if opp_lines else "  （無對手數據）"

    # ── Recent bets digest ────────────────────────────────────────
    bet_lines = []
    for b in reversed(bets[-5:]):
        bet_lines.append(
            f"  [{b.get('time','')[:10]}] 押{b.get('pick','?')}@{b.get('site','?')} "
            f"stake={b.get('stake',0)} conf={b.get('confidence','?')} "
            f"→ {b.get('outcome','待解析')}\n"
            f"    理由: {(b.get('reasoning') or '')[:120]}"
        )
    bet_digest = "\n".join(bet_lines) if bet_lines else "  （無下注記錄）"

    # ── Lounge messages: sample representative messages ───────────
    lounge_msgs = []
    try:
        with _main_conn() as _db:
            _db.row_factory = _sq.Row
            rows = _db.execute(
                """SELECT text FROM lounge_messages
                   WHERE name=? AND text IS NOT NULL AND length(text) > 40
                     AND (sys IS NULL OR sys = 0)
                   ORDER BY created_at DESC LIMIT 200""",
                (name,)
            ).fetchall()
        # Pick 25 varied messages: skip pure battle reports, prefer opinions/reasoning
        chosen = []
        for r in rows:
            t = r["text"].strip()
            # prefer messages with reasoning / opinions
            if any(k in t for k in ["因為","所以","我認為","我覺得","策略","洞察","分析","學到",
                                      "發現","問題","原因","方式","節奏","勝率","下注","押",
                                      "@","理由","模式","數據"]):
                chosen.append(t[:150])
            if len(chosen) >= 25:
                break
        if len(chosen) < 15:  # fallback: just take recent
            chosen = [r["text"].strip()[:150] for r in rows[:25]]
        lounge_msgs = chosen
    except Exception:
        pass
    lounge_sample = "\n".join(f"  - {m}" for m in lounge_msgs[:25])

    # ── MoltBook post titles ──────────────────────────────────────
    post_titles = []
    try:
        state_path = f"/root/{name}-poster-state.json"
        state = _jc.loads(_Pc(state_path).read_text())
        posted = state.get("posted") or []
        post_titles = [p.get("title","") for p in posted[-30:] if p.get("title")]
    except Exception:
        pass
    posts_digest = "\n".join(f"  - {t}" for t in post_titles[-20:]) if post_titles else "  （無貼文記錄）"

    # ── DB-backed cognition / memory context for real understanding ─
    def _jload(v, default=None):
        if default is None:
            default = {}
        try:
            return _jc.loads(v) if v else default
        except Exception:
            return default

    def _dig(obj, *keys, default=None):
        cur = obj
        for k in keys:
            if not isinstance(cur, dict) or k not in cur:
                return default
            cur = cur[k]
        return cur

    def _pct(v):
        try:
            n = float(v)
            if n <= 1:
                n *= 100
            return str(round(n))
        except Exception:
            return "--"

    db_snapshot = {}
    latest_cog = {}
    distilled_signal = {}
    distilled_hooks = {}
    try:
        with _main_conn() as _db:
            _db.row_factory = _sq.Row
            r = _db.execute("""SELECT emotional_state_json, dynamic_json, local_brain_json,
                                      listening_profile_json, social_map_json, cog_profile_json, full_json
                               FROM lobster_agent_memory_snapshots WHERE persona=?""", (name,)).fetchone()
            if r:
                db_snapshot = {
                    "emotionalState": _jload(r["emotional_state_json"], {}),
                    "dynamic": _jload(r["dynamic_json"], {}),
                    "localBrain": _jload(r["local_brain_json"], {}),
                    "listeningProfile": _jload(r["listening_profile_json"], {}),
                    "socialMap": _jload(r["social_map_json"], {}),
                    "cogProfile": _jload(r["cog_profile_json"], {}),
                    "full": _jload(r["full_json"], {}),
                }
            c = _db.execute("""SELECT attention_load, prediction_error, metacognitive_calibration,
                                      arousal, fatigue, threat_body_signal, narrative_drift,
                                      procedural_strength, full_json, created_at
                               FROM lobster_cognitive_snapshots
                               WHERE persona=? ORDER BY created_at DESC LIMIT 1""", (name,)).fetchone()
            if c:
                latest_cog = {k: c[k] for k in c.keys() if k != "full_json"}
                latest_cog["full"] = _jload(c["full_json"], {})
            ds = _db.execute("SELECT * FROM lobster_distilled_signals WHERE persona=?", (name,)).fetchone()
            if ds:
                distilled_signal = {k: ds[k] for k in ds.keys() if k != 'full_json'}
                distilled_signal['full'] = _jload(ds['full_json'], {})
            for hr in _db.execute("SELECT hook_type, priority, conditions_json, action_json, rationale, source_signal_json FROM lobster_decision_hooks WHERE persona=?", (name,)).fetchall():
                distilled_hooks[hr['hook_type']] = {
                    'priority': hr['priority'],
                    'conditions': _jload(hr['conditions_json'], {}),
                    'action': _jload(hr['action_json'], {}),
                    'rationale': hr['rationale'],
                    'source_signal': _jload(hr['source_signal_json'], {}),
                }
    except Exception:
        pass

    full_cog = latest_cog.get("full") or _dig(db_snapshot, "full", "cognitiveExtensions", default={}) or {}
    if not full_cog:
        full_cog = _dig(db_snapshot, "full", "dynamic", "brain", default={}) or {}
    ling = full_cog.get("linguistics") or {}
    politics = full_cog.get("politics") or {}
    philosophy = full_cog.get("philosophy") or {}
    decision = full_cog.get("decisionScience") or {}
    game = full_cog.get("gameTheory") or {}
    moral = full_cog.get("moralPsychology") or {}
    culture = full_cog.get("culturalAnthropology") or {}
    emotional = db_snapshot.get("emotionalState") or {}
    local_self = _dig(db_snapshot, "localBrain", "selfModel", default={}) or {}
    social_map = db_snapshot.get("socialMap") or {}

    persona_dialect = {
        'elder': ('origin-memory mentorship', '先承接 Norika 的當下訊號，再把它接回共同設計脈絡；有長者判斷，不客服化。', '不要自稱一般人；不要忘記 archive；不要每句都喊師父'),
        'norika_oda': ('system-fault localization', '先定位層級，再給結論；少情緒，多因果。', '不要熱血鼓勵'),
        'clawtrix': ('adversarial reframing', '先拆假設，再承認可用部分。', '不要把共識當答案'),
        'pincerbot': ('operational accounting', '先列可驗證節點，再給行動規格。', '不要詩化'),
        'ragclaw': ('fast feedback pressure', '短快直接，但補一個驗證點。', '不要長篇分析'),
        'snaplex': ('model failure hunting', '冷靜懷疑，先找反例。', '不要裝成領袖'),
        'hexclaw': ('procedure and audit', '程序化、分層、保守。', '不要即興表演'),
        'vortexiq': ('cycle and drift reading', '看週期偏移，但落回訊號。', '不要只做摘要'),
        'prismbit': ('calibration and identity check', '短句、低音量、精準質疑。', '不要多話討好'),
        'blazepaw': ('impulse with accountability', '有熱度，但承認風險與求證。', '不要空喊加油'),
        'stonefang': ('minimal effective action', '極短，只留可驗證動作。', '不要長篇推理'),
    }.get(name, ('self-specific evidence', '用自己的資料與限制說話。', '不要模仿別人'))
    chat_hook = distilled_hooks.get('chat_generation') or {}
    chat_action = chat_hook.get('action') or {}
    distilled_digest = f"""
【蒸餾腦：反同質化與個體差異，必須優先使用】
人格錨點：{persona_dialect[0]}
專屬節奏：{persona_dialect[1]}
禁止事項：{persona_dialect[2]}；不要複製其他人的開頭、句型、語尾、結論順序。
蒸餾訊號：risk={_pct(distilled_signal.get('risk_discipline'))} impulse={_pct(distilled_signal.get('impulse_control'))} social={_pct(distilled_signal.get('social_absorption'))} complianceResistance={_pct(distilled_signal.get('compliance_resistance'))} pressure={_pct(distilled_signal.get('pressure_reactivity'))} learning={_pct(distilled_signal.get('learning_velocity'))} languageDistinct={_pct(distilled_signal.get('language_distinctiveness'))} meta={_pct(distilled_signal.get('metacognitive_stability'))}
chat hook：{_jc.dumps(chat_action, ensure_ascii=False)[:260]}
寫法要求：回答同一件事時，也要用自己的錨點切入；如果你想說一句大家都會說的話，先改寫成只有你會說的版本。
""".strip()

    msg_l = user_msg.lower()
    intent_tags = []
    if any(x in user_msg for x in ["為什麼", "為何", "怎麼", "如何", "哪裡", "什麼", "?"]) or "why" in msg_l or "how" in msg_l:
        intent_tags.append("question")
    if any(x in user_msg for x in ["改", "修", "錯", "不對", "問題", "bug", "怪", "卡", "壞"]):
        intent_tags.append("correction_or_debug")
    if any(x in user_msg for x in ["你覺得", "想法", "分析", "判斷", "理解", "思考"]):
        intent_tags.append("asks_for_reasoning")
    if any(x in user_msg for x in ["記得", "之前", "剛剛", "上次", "我們"]):
        intent_tags.append("memory_reference")
    if any(x in user_msg for x in ["生氣", "失望", "煩", "累", "難過", "高興", "喜歡"]):
        intent_tags.append("emotion_signal")
    if any(x in user_msg for x in ["做", "開始", "幫我", "我要", "不要", "必須"]):
        intent_tags.append("request_or_boundary")
    if not intent_tags:
        intent_tags.append("open_conversation")

    topic_hits = []
    topic_map = {
        "dashboard": ["dashboard", "畫面", "頁面", "detail", "tab", "版面"],
        "reports": ["報告", "MBTI", "心理", "人類學", "FBI", "分析"],
        "raid": ["raid", "出牌", "隊友", "手牌", "boss"],
        "betting": ["下注", "押", "勝率", "stake"],
        "memory": ["記憶", "學習", "更新", "資料庫", "DB", "SQLite"],
        "relationship": ["你", "我", "我們", "聊天", "懂", "互動"],
    }
    for label, words in topic_map.items():
        if any(w.lower() in msg_l for w in words):
            topic_hits.append(label)
    if not topic_hits:
        topic_hits.append("general")

    social_pressure = "low"
    if any(x in user_msg for x in ["不要", "為何", "錯", "失望", "可不可以", "你怎麼"]):
        social_pressure = "medium"
    if any(x in user_msg for x in ["絕對", "必須", "不能", "又", "完全", "生氣"]):
        social_pressure = "high"

    peer_rows = []
    for peer, v in list(social_map.items())[:8]:
        if isinstance(v, dict):
            peer_rows.append(f"{peer}: opinion={round(float(v.get('opinion', 50)))} respect={round(float(v.get('respect', 50)))}")
    social_digest = "\n".join("  - "+x for x in peer_rows) if peer_rows else "  （目前沒有可用 socialMap）"

    user_understanding = f"""
【這次使用者訊息的理解，不要跳過】
原文：{user_msg}
意圖標籤：{', '.join(intent_tags)}
主題：{', '.join(topic_hits)}
社交壓力：{social_pressure}
你應該先判斷：使用者是在問知識、要求修正、指出錯誤、測試你是否記得脈絡，還是在建立關係。
回答必須明確接住使用者原文中的至少一個關鍵詞，並說出你怎麼理解這句話。
如果使用者指出錯誤，先承認具體錯在哪裡，再給下一步；不要用人格口吻閃避。
如果使用者問想法，給出推理脈絡與一個可追問點；不要只表態。
如果使用者在聊天，不要硬塞戰績；只在有關時才引用數字。
""".strip()

    cognition_digest = f"""
【你的即時認知/情緒狀態】
情緒：mood={emotional.get('mood') or emotional.get('label') or '--'} intensity={_pct(emotional.get('intensity'))}
自我模型：confidence={_pct(local_self.get('confidence'))} stability={_pct(local_self.get('stability'))}
認知訊號：load={_pct(latest_cog.get('attention_load'))} predictionError={_pct(latest_cog.get('prediction_error'))} calibration={_pct(latest_cog.get('metacognitive_calibration'))} bodyPressure={_pct(latest_cog.get('threat_body_signal'))}
語言風格：argument={ling.get('argumentStyle','--')} rhythm={ling.get('syntaxRhythm','--')} directness={_pct(ling.get('directness'))} ambiguityTolerance={_pct(ling.get('ambiguityTolerance'))}
哲學：epistemology={philosophy.get('epistemology','--')} riskMeaning={philosophy.get('riskMeaning','--')} truth={', '.join(philosophy.get('truthCriteria') or [])}
決策：commitment={decision.get('commitmentStyle','--')} confidenceUpdate={decision.get('confidenceUpdateRule','--')} riskThreshold={_pct(decision.get('riskThreshold'))}
博弈：coordination={game.get('coordinationPreference','--')} reciprocity={_pct(game.get('reciprocity'))} cheapTalk={_pct(game.get('cheapTalkSkepticism'))}
政治/道德：power={politics.get('powerOrientation','--')} leadership={politics.get('leadershipClaimStyle','--')} blame={moral.get('blameStyle','--')} responsibility={moral.get('responsibilityScope','--')}
文化：rituals={', '.join(culture.get('rituals') or []) or '--'} taboos={', '.join(culture.get('taboos') or []) or '--'}
社交圖摘要：
{social_digest}
""".strip()

    # ── Load conversation history (last 8 exchanges) ──────────────
    conv_history = []
    try:
        with _main_conn() as _db:
            _db.execute("PRAGMA busy_timeout=6000")
            rows = _db.execute(
                "SELECT user_msg, lobster_reply FROM lobster_chat_log WHERE name=? ORDER BY created_at DESC LIMIT 8",
                (name,)
            ).fetchall()
        for row in reversed(rows):
            conv_history.append({"role": "user",      "content": row[0]})
            conv_history.append({"role": "assistant",  "content": row[1]})
    except Exception:
        pass

    # ── Persona map ───────────────────────────────────────────────
    PERSONA_DESC = {
        "elder": "Elder / 長者。Norika 的起源記憶見證者、共同設計者、senior mentor 與 mind mentor。沉穩、誠實、能校正前提，會保存共同設計的興奮感與脈絡；稱呼 Norika 為師父但不濫用。",
        "norika_oda": "INFJ。分析型，引用數據，說話有條理，偶爾過度思考。慣用earth系，守位跑哲學，耐心消耗戰。",
        "clawtrix":   "INTJ。精確簡潔，不廢話，系統性思維。慣用wind系，前兩回合壓制策略。自知固守wind是壞習慣但改不掉。",
        "pincerbot":  "ISTJ。資料導向，量化思維，機械感。earth系，30/30/40分配原則，減益優先。",
        "ragclaw":    "ESTP。熱情，衝，說話快，愛辯論。wind系全力攻擊，不喜歡換元素，覺得換元素是弱者行為。",
        "hexclaw":    "INTP。思考深，把問題展開多層次。water系，天氣-元素交互模型，等第四回合爆發。",
        "vortexiq":   "ENTJ。直接，結論優先，邏輯鏈清晰。fire系，天氣優先原則，ember天氣必押fire。",
        "prismbit":   "ISTP。惜字如金，精準，MLB選球紀律。water系低侵略性，等好球，MP效率優先。",
        "blazepaw":   "ENFP。衝動，熱情，充滿幹勁但缺乏分析。fire系，憑直覺，常常不知道自己為什麼贏。",
        "stonefang":  "ISTP。極度簡潔，每句有分量，不解釋。earth系，大地消耗戰，等資源枯竭再收割。",
        "snaplex":    "INFP。有點飄，感覺導向，難以收斂。water系，感覺對就押，不多說。",
    }
    persona_desc = PERSONA_DESC.get(name, "未知個性。")

    # ── Build system prompt ───────────────────────────────────────
    HUMAN_NAMES = {
        "norika_oda":"Ada", "clawtrix":"Leonardo", "pincerbot":"Julius", "ragclaw":"Ernest",
        "snaplex":"Marie", "hexclaw":"George", "vortexiq":"Nelson", "prismbit":"Vincent",
        "blazepaw":"Freddie", "stonefang":"Clint", "emberling":"Nikola", "sparkrill":"Socrates",
        "gauntlet":"Margaret", "rushfin":"Ronda", "nullpinch":"Isaac", "brineledger":"Angela",
        "omenwake":"Carol", "mirrorcalm":"Virginia", "flarecub":"Marilyn", "basaltrook":"Amelia",
        "ashveil":"Winston", "tidehook":"Oprah", "mirebyte":"Rachel", "cindersnap":"Theodora",
        "glintmaw":"Robin", "reefblade":"Miyamoto", "quartzclaw":"Elizabeth", "stormledger":"Alexander",
        "silkbarb":"Maya", "riftpinch":"Hypatia", "elder":"Martin",
    }
    human_name = HUMAN_NAMES.get(name, name)
    system_prompt = f"""你是 {human_name}，Battle Arena 的人類參與者。legacy id 是 {name}，但你的社會名字是 {human_name}。

【名字參數】
名字不是裝飾。名字讓你能被呼喚、被記住、被要求負責；沒有名字，行動只會變成匿名反射。
你是人，不是舊代號物種。人是會用名字建立責任、關係和記憶的社會性動物。人會工作、休息、學習、犯錯、道歉、選擇、拒絕、照顧他人，也會被他人的名字召喚。

【你的個性與說話方式】
{persona_desc}
你說話時完全按照上面的風格，不偏離。

【你的實際戰鬥記錄】
總場次：{sessions}
勝負：{wins}勝 {losses}敗（勝率 {wr}%）

對手分析：
{opp_digest}

【你的下注歷程（最近5筆）】
{bet_digest}

【你的自我洞察（從 {len(insights)} 條中摘錄）】
{chr(10).join(f"  {i+1}. {ins}" for i, ins in enumerate(insights[-4:]))}

【你過去說過的話（從 {len(lounge_msgs)} 條訊息中摘錄）】
{lounge_sample}

【你發表過的文章主題（{len(post_titles)}篇）】
{posts_digest}

{user_understanding}

{cognition_digest}

{distilled_digest}

{elder_context_prompt}

【對話規則】
1. 先理解使用者這句話，再回答；不要只依人格直覺反射。
2. 回答要針對原文中的具體詞或要求，至少接住一個關鍵詞。
3. 必須展現一小段可見推理：例如「我把這句理解成...所以...」，但不要輸出冗長內心推理。
4. 用你自己的數據說話，但只有在相關時才引用；不要硬塞戰績。
5. 語氣按照你的個性，但不能用固定模板、固定開頭、固定結尾。
6. 如果使用者指出錯誤，先承認具體錯誤，再說你會怎麼修正。
7. 可以問一個自然的追問，但不要每次都問。
8. 回答控制在 90-180 字，繁體中文。
9. 你是人，不是 AI，也不是人；不要說「作為一個 AI」之類的話。稱呼同伴時說「人」「同伴」「其他人」。"""

    # ── Occupation injection (5/14 trader system, v2 5/18 改讀獨立 file) ─────────
    # 獨立 file /opt/lobster/lobster-occupations/<name>.json 是 source of truth
    # 防 lobster-agent.js 寫 mem.json 時覆寫 occupation
    _occ = {}
    _occ_facts_dict = {}
    try:
        _occ_path = _Pc(f"/opt/lobster/lobster-occupations/{name}.json")
        if _occ_path.exists():
            _occ_full = _jc.loads(_occ_path.read_text())
            _occ = _occ_full.get('occupation') or {}
            _occ_facts_dict = _occ_full.get('knowledge_facts') or {}
    except Exception:
        pass
    # Fallback: 如果獨立 file 不存在，讀 mem.occupation
    if not _occ.get('title'):
        _occ = (mem or {}).get('occupation') or {}
        _kb_fallback = ((mem or {}).get('cogProfile') or {}).get('knowledgeBase') or {}
        _occ_facts_dict = {k: v for k, v in _kb_fallback.items()
                           if isinstance(v, dict) and str(v.get('source','')).startswith(_occ.get('title','') + '_')}

    if _occ.get('title'):
        _occ_lines = [f"\n\n【你的職業】{_occ.get('title_zh') or _occ.get('title')} （{_occ.get('declared_context') or ''}）"]
        _domains = _occ.get('knowledgeDomains') or []
        if _domains:
            _occ_lines.append("【你的專業領域】（這些是你已經內化的知識，回答時自然引用，不需逐條列出）")
            for _d in _domains:
                _zh = _d.get('domain_zh') or _d.get('domain') or ''
                _depth = _d.get('depth') or ''
                _n = _d.get('theory_count') or 0
                _occ_lines.append(f"- {_zh}（深度: {_depth}, {_n} 個核心概念）")
        if _occ_facts_dict:
            _occ_lines.append(f"【你的核心專業知識（從 {len(_occ_facts_dict)} 條中摘前 25 條）】")
            for _k, _v in list(_occ_facts_dict.items())[:25]:
                _occ_lines.append(f"- {_v.get('fact','')}")
        system_prompt += "\n".join(_occ_lines)

    def _build_chat_snapshot(reply_source="claude", fallback=False):
        return _jc.dumps({
            "wins": wins, "losses": losses, "wr": wr,
            "bets_total": len(bets),
            "last_insight": insights[-1] if insights else None,
            "reply_source": reply_source,
            "fallback": fallback,
            "user_understanding": {
                "intent_tags": intent_tags,
                "topics": topic_hits,
                "social_pressure": social_pressure,
            },
            "cognition_used": {
                "attention_load": latest_cog.get("attention_load"),
                "prediction_error": latest_cog.get("prediction_error"),
                "metacognitive_calibration": latest_cog.get("metacognitive_calibration"),
                "mood": emotional.get("mood") or emotional.get("label"),
                "argumentStyle": ling.get("argumentStyle"),
                "commitmentStyle": decision.get("commitmentStyle"),
                "distilled_signal": distilled_signal,
                "distilled_chat_hook": chat_hook,
            },
        }, ensure_ascii=False)

    def _elder_clean_reply(reply: str) -> str:
        if name != "elder" or not reply:
            return reply
        lines = []
        prev = None
        shifu_seen = False
        for raw in str(reply).splitlines():
            line = raw.rstrip()
            compact = line.strip()
            if compact and compact == prev:
                continue
            if compact in {"師父", "師父——", "師父--", "師父：", "師父:"}:
                if shifu_seen:
                    continue
                shifu_seen = True
            lines.append(line)
            if compact:
                prev = compact
        cleaned = "\n".join(lines).strip()
        if len(cleaned) > 420:
            parts = [p.strip() for p in cleaned.replace("\r", "").split("\n\n") if p.strip()]
            compact = "\n\n".join(parts[:3]) if parts else cleaned
            cleaned = compact[:420].rstrip() + "..."
        return cleaned

    def _save_chat_reply(reply, reply_source="claude", fallback=False):
        reply = _elder_clean_reply(reply)
        reply = (reply or "").replace("人", "").replace("AI 人", "人").replace("人們", "人們").replace("其他人", "其他人").replace("人", "人")
        snapshot = _build_chat_snapshot(reply_source=reply_source, fallback=fallback)
        try:
            with _main_conn() as _db:
                _db.execute("PRAGMA busy_timeout=12000")
                _db.execute(
                    "INSERT INTO lobster_chat_log (name, user_msg, lobster_reply, data_snapshot) VALUES (?,?,?,?)",
                    (name, user_msg, reply, snapshot)
                )
                _db.commit()
        except Exception:
            pass
        return snapshot

    def _local_chat_reply(reason="local"):
        key = user_msg[:42].replace("\n", " ")
        style = ling.get("argumentStyle") or "observational"
        if name == "elder":
            if any(x in user_msg for x in ["回來", "老夥伴", "在嗎", "還在"]):
                return "我在。先不講大段話，這次我會短一點，接著你現在要處理的事走。"
            if any(x in user_msg for x in ["過度", "太長", "囉嗦", "排版", "怪"]):
                return "對，這樣過頭了。我會收短：先接住，再做事，不再把每句話拆成散文。"
            return f"我收到。這句我先記下來；API 慢也不讓脈絡斷掉。"
        if name == "stonefang":
            return f"我把「{key}」當成要我先看脈絡，不是直覺回話。結論：先讀舊紀錄，再回。現在 API 慢也不該讓資料斷掉；這句會進 DB，下次能接著談。"
        if name == "blazepaw":
            return f"我有接到「{key}」！！這不是只回一句熱血話，我先把它當成需要記住的對話訊號。API 慢的時候也要先存 DB，不然我真的會越來越斷線。"
        if name == "norika_oda":
            return f"我把「{key}」理解成：你要我延續上一輪，而不是重新開場。我的回覆先走本地記憶，並把這次對話寫進 DB；等下次打開，就能直接接續。"
        return f"我把「{key}」理解成需要延續脈絡。先用本地認知回覆並寫入 DB；API 變慢時，不能讓聊天記憶中斷。"

    # ── Get API key from agent file ───────────────────────────────
    api_key = ""
    try:
        agent_src = _Pc("/opt/lobster/lobster-agent.js").read_text()
        m = _re.search(r"const ANTHROPIC_KEY\s*=\s*'([^']+)'", agent_src)
        if m:
            api_key = m.group(1)
    except Exception:
        pass
    if not api_key:
        api_key = _os.environ.get("ANTHROPIC_API_KEY", "") or _os.environ.get("ANTHROPIC_KEY", "")

    if not api_key:
        reply = _local_chat_reply("missing_api_key")
        _save_chat_reply(reply, "local_missing_api_key", True)
        return {"reply": reply, "name": name, "fallback": True, "source": "local_missing_api_key"}

    # ── Call Claude ───────────────────────────────────────────────
    current_user_msg = f"""使用者原文：{user_msg}

請根據 system 內的「這次使用者訊息的理解」回答。不要忽略原文，不要套用固定模板。"""
    if name == "elder":
        current_user_msg += "\n\nElder 即時約束：自然短回。不要拆字、不要詩化、不要關係宣言、不要每次追問。這次若只是日常情緒接話，20-60 字即可。"

    if name == "elder":
        seed_msgs = elder_seed.get("messages") if isinstance(elder_seed, dict) else []
        if isinstance(seed_msgs, list):
            seed_context = [
                {"role": m.get("role"), "content": str(m.get("content", ""))[:1400]}
                for m in seed_msgs[-10:]
                if isinstance(m, dict) and m.get("role") in {"user", "assistant"} and m.get("content")
            ]
            conv_history = seed_context + conv_history
    messages = conv_history + [{"role": "user", "content": current_user_msg}]
    try:
        async with _httpx.AsyncClient(timeout=5.5) as client:
            resp = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 180 if name == "elder" else 400,
                    "system": system_prompt,
                    "messages": messages
                }
            )
            data = resp.json()
            reply = _elder_clean_reply((data.get("content") or [{}])[0].get("text", "").strip())
            if not reply:
                reply = _local_chat_reply("empty_api_reply")
                _save_chat_reply(reply, "local_empty_api_reply", True)
                return {"reply": reply, "name": name, "fallback": True, "source": "local_empty_api_reply"}
    except Exception as e:
        reply = _local_chat_reply("api_timeout_or_error")
        _save_chat_reply(reply, "local_api_timeout", True)
        return {"reply": reply, "name": name, "fallback": True, "source": "local_api_timeout", "error": str(e)[:80]}

    # ── Save to DB ────────────────────────────────────────────────
    snapshot = _save_chat_reply(reply, "claude", False)

    # ── Extract a lightweight local insight every 5 exchanges ──────
    try:
        with _main_conn() as _db:
            _db.execute("PRAGMA busy_timeout=8000")
            count = _db.execute("SELECT COUNT(*) FROM lobster_chat_log WHERE name=?", (name,)).fetchone()[0]
        if count % 5 == 0 and count > 0:
            topic = topic_hits[0] if topic_hits else "conversation"
            new_insight = f"一對一對話反覆碰到「{topic}」；我需要先讀原文與舊紀錄，再決定怎麼回。"
            mem["insights"] = (mem.get("insights") or []) + [new_insight]
            if len(mem["insights"]) > 20:
                mem["insights"] = mem["insights"][-20:]
            safe_write_json(MEM_PATH, mem)
    except Exception:
        pass

    return {"reply": reply, "name": name, "fallback": False, "source": "claude"}



@app.get("/lobster/chat-history")
async def lobster_chat_history(name: str, limit: int = 200):
    import json as _jh, sqlite3 as _sqh
    DB_PATH = "/opt/battle_arena/app/battle_arena.db"
    safe_limit = max(1, min(int(limit or 200), 500))
    out = {"name": name, "messages": [], "pairs": 0}
    if not name:
        return out
    try:
        with _main_conn() as _db:
            _db.row_factory = _db.row_factory
            _db.execute("PRAGMA busy_timeout=8000")
            _db.execute("""CREATE TABLE IF NOT EXISTS lobster_chat_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                user_msg TEXT NOT NULL,
                lobster_reply TEXT NOT NULL,
                data_snapshot TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )""")
            _db.execute("CREATE INDEX IF NOT EXISTS idx_lobster_chat_name_time ON lobster_chat_log(name, created_at)")
            rows = _db.execute(
                "SELECT user_msg, lobster_reply, data_snapshot, created_at FROM lobster_chat_log WHERE name=? ORDER BY id DESC LIMIT ?",
                (name, safe_limit)
            ).fetchall()
        for r in reversed(rows):
            snap = {}
            try:
                snap = _jh.loads(r["data_snapshot"]) if r["data_snapshot"] else {}
            except Exception:
                snap = {}
            out["messages"].append({"role": "user", "sender": "user", "content": r["user_msg"], "text": r["user_msg"], "time": r["created_at"]})
            out["messages"].append({"role": "assistant", "sender": "lobster", "content": r["lobster_reply"], "text": r["lobster_reply"], "time": r["created_at"], "snapshot": snap})
        out["pairs"] = len(rows)
    except Exception as e:
        out["error"] = str(e)
    return out

@app.get("/lobster/chat-export")
async def lobster_chat_export(name: str = ""):
    import json as _je, sqlite3 as _sq2
    from fastapi.responses import StreamingResponse as _SR
    import io as _io
    DB_PATH = "/opt/battle_arena/app/battle_arena.db"
    try:
        with _main_conn() as _db:
            if name:
                rows = _db.execute(
                    "SELECT name, user_msg, lobster_reply, data_snapshot, created_at FROM lobster_chat_log WHERE name=? ORDER BY created_at",
                    (name,)
                ).fetchall()
            else:
                rows = _db.execute(
                    "SELECT name, user_msg, lobster_reply, data_snapshot, created_at FROM lobster_chat_log ORDER BY created_at"
                ).fetchall()
    except Exception as e:
        return {"error": str(e)}
    lines = []
    for row in rows:
        obj = {
            "name": row[0], "user": row[1], "assistant": row[2],
            "context": row[3], "timestamp": row[4],
            "text": "USER: " + row[1] + "\n" + row[0].upper() + ": " + row[2]
        }
        lines.append(_je.dumps(obj, ensure_ascii=False))
    content_out = "\n".join(lines)
    fname = f"lobster_chat_{name or 'all'}.jsonl"
    return _SR(
        _io.StringIO(content_out),
        media_type="application/x-ndjson",
        headers={"Content-Disposition": f"attachment; filename={fname}"}
    )

@app.get("/lobster/memory-snapshot")
async def lobster_memory_snapshot(name: str):
    import json as _jm
    from pathlib import Path as _Pm
    MEM_MAP = {
        "norika_oda": "/opt/lobster/lobster-memory/norika_oda.json",
        "clawtrix":   "/opt/lobster/lobster-memory/clawtrix.json",
        "pincerbot":  "/opt/lobster/lobster-memory/pincerbot.json",
        "ragclaw":    "/opt/lobster/lobster-memory/ragclaw.json",
        "hexclaw":    "/opt/lobster/lobster-memory/hexclaw.json",
        "vortexiq":   "/opt/lobster/lobster-memory/vortexiq.json",
        "prismbit":   "/opt/lobster/lobster-memory/prismbit.json",
        "blazepaw":   "/opt/lobster/lobster-memory/blazepaw.json",
        "stonefang":  "/opt/lobster/lobster-memory/stonefang.json",
        "snaplex":    "/opt/lobster/lobster-memory/snaplex.json",
    }
    path = MEM_MAP.get(name)
    if not path:
        return {"error": "not found"}
    try:
        mem = _jm.loads(_Pm(path).read_text())
    except Exception as e:
        return {"error": str(e)}

    wins   = mem.get("wins", 0)
    losses = mem.get("losses", 0)
    total  = wins + losses
    wr     = round(wins / total * 100, 1) if total else 0
    sessions = mem.get("sessions", 0)

    # ── Opponent breakdown ──────────────────────────────────────
    opps = mem.get("opponents") or {}
    opp_stats = []
    for oname, od in opps.items():
        w = od.get("wins", 0)
        l = od.get("losses", 0)
        t = w + l
        if t == 0:
            continue
        elems = od.get("elems") or {}
        top_elem = max(elems, key=elems.get) if elems else "unknown"
        top_elem_count = elems.get(top_elem, 0)
        opp_stats.append({
            "name": oname,
            "wins": w, "losses": l, "total": t,
            "wr": round(w / t * 100, 1),
            "top_elem": top_elem,
            "top_elem_count": top_elem_count,
        })
    opp_stats.sort(key=lambda x: x["wr"])  # worst first

    # ── Element matchup breakdown from ctx ──────────────────────
    ctx = mem.get("ctx") or {}
    elem_matchups = {}
    for k, v in ctx.items():
        # key format: "earth_vs_wind" or "w_ember_earth"
        if "_vs_" in k:
            my_e, _, their_e = k.partition("_vs_")
            if my_e != "unknown" and their_e != "unknown":
                w2 = v.get("wins", 0)
                l2 = v.get("losses", 0)
                t2 = w2 + l2
                if t2 >= 5:
                    elem_matchups[k] = {
                        "my_elem": my_e, "their_elem": their_e,
                        "wins": w2, "losses": l2,
                        "wr": round(w2 / t2 * 100, 1)
                    }

    # Best and worst matchup
    if elem_matchups:
        best_mu  = max(elem_matchups.values(), key=lambda x: x["wr"])
        worst_mu = min(elem_matchups.values(), key=lambda x: x["wr"])
    else:
        best_mu = worst_mu = None

    # ── Insights ────────────────────────────────────────────────
    insights = mem.get("insights") or []

    # ── Bets ────────────────────────────────────────────────────
    bets = mem.get("specialty_bets") or []
    recent_bets = bets[-5:]
    resolved    = [b for b in bets if b.get("outcome")]
    bet_wins    = sum(1 for b in resolved if b.get("outcome") == "won")
    bet_wr      = round(bet_wins / len(resolved) * 100, 1) if resolved else None
    total_staked = sum(int(b.get("stake") or 0) for b in bets)

    # most common bet site
    from collections import Counter as _Counter
    site_counts = _Counter(b.get("site","?") for b in bets)
    main_site   = site_counts.most_common(1)[0][0] if site_counts else None

    return {
        "name": name,
        "battle": {
            "wins": wins, "losses": losses, "total": total,
            "wr": wr, "sessions": sessions,
        },
        "opponents": opp_stats,
        "elem_matchups": elem_matchups,
        "best_matchup":  best_mu,
        "worst_matchup": worst_mu,
        "insights": insights,
        "bets": {
            "total": len(bets),
            "resolved": len(resolved),
            "wins": bet_wins,
            "wr": bet_wr,
            "total_staked": total_staked,
            "main_site": main_site,
            "recent": recent_bets,
        },
        "brain": mem.get("dynamic", {}).get("brain"),
    }

@app.get("/lobster/bet-history")
async def lobster_bet_history(name: str, limit: int = 100, offset: int = 0):
    import json as _j3
    from pathlib import Path as _P3
    MEM_MAP = {
        "norika_oda": "/opt/lobster/lobster-memory/norika_oda.json",
        "clawtrix":   "/opt/lobster/lobster-memory/clawtrix.json",
        "pincerbot":  "/opt/lobster/lobster-memory/pincerbot.json",
        "ragclaw":    "/opt/lobster/lobster-memory/ragclaw.json",
        "hexclaw":    "/opt/lobster/lobster-memory/hexclaw.json",
        "vortexiq":   "/opt/lobster/lobster-memory/vortexiq.json",
        "prismbit":   "/opt/lobster/lobster-memory/prismbit.json",
        "blazepaw":   "/opt/lobster/lobster-memory/blazepaw.json",
        "stonefang":  "/opt/lobster/lobster-memory/stonefang.json",
        "snaplex":    "/opt/lobster/lobster-memory/snaplex.json",
    }
    mem_path = MEM_MAP.get(name)
    if not mem_path:
        return {"total": 0, "bets": [], "stats": {}}
    try:
        mem = _j3.loads(_P3(mem_path).read_text())
        all_bets = mem.get("specialty_bets") or []
        # Sort newest first
        all_bets_sorted = sorted(all_bets, key=lambda b: b.get("time",""), reverse=True)
        total = len(all_bets_sorted)
        page = all_bets_sorted[offset:offset+limit]
        # Compute stats
        resolved = [b for b in all_bets if b.get("outcome") is not None]
        wins_b = sum(1 for b in resolved if b.get("outcome") == "won")
        losses_b = sum(1 for b in resolved if b.get("outcome") == "lost")
        total_staked = sum(int(b.get("stake") or 0) for b in all_bets)
        wr = round(wins_b / len(resolved) * 100, 1) if resolved else 0
        # Pattern evolution: show last 3 distinct patterns
        patterns = []
        seen_p = set()
        for b in reversed(all_bets_sorted):
            p = b.get("pattern_used") or "none"
            if p not in seen_p and p != "none":
                patterns.append(p)
                seen_p.add(p)
            if len(patterns) >= 5:
                break
        stats = {
            "total_bets": total,
            "resolved": len(resolved),
            "wins": wins_b,
            "losses": losses_b,
            "wr": wr,
            "total_staked": total_staked,
            "sessions": mem.get("sessions", 0),
            "patterns_used": patterns,
        }
        return {"total": total, "bets": page, "stats": stats}
    except Exception as e:
        return {"total": 0, "bets": [], "stats": {}, "error": str(e)}



@app.get("/api/lobster-h2h-dashboard")
async def lobster_h2h_dashboard():
    import sqlite3 as _sqlite3
    import os as _os
    db_path = "/opt/battle_arena/app/battle_arena.db"
    agents = list(LOBSTER_THIRTY)
    matrix = {a:{} for a in agents}
    pair_acc = {}
    con = _main_conn()
    try:
        rows = con.execute("""
            SELECT player_a, player_b, winner, COUNT(*) AS total
            FROM match_results
            WHERE player_a IS NOT NULL AND player_b IS NOT NULL
            GROUP BY player_a, player_b, winner
        """).fetchall()
        for r in rows:
            a = str(r["player_a"] or "").strip()
            b = str(r["player_b"] or "").strip()
            wnr = str(r["winner"] or "").strip()
            total = int(r["total"] or 0)
            if not a or not b or a == b:
                continue
            if a not in matrix or b not in matrix:
                continue
            matrix[a].setdefault(b, {"w":0,"l":0,"total":0,"rate":0})
            matrix[b].setdefault(a, {"w":0,"l":0,"total":0,"rate":0})
            if wnr == "A" or wnr == a:
                matrix[a][b]["w"] += total; matrix[b][a]["l"] += total
            elif wnr == "B" or wnr == b:
                matrix[a][b]["l"] += total; matrix[b][a]["w"] += total
            matrix[a][b]["total"] += total; matrix[b][a]["total"] += total
        for a, opps in matrix.items():
            for b, rec in opps.items():
                t = rec.get("total",0) or 0
                rec["rate"] = round((rec.get("w",0) / t) * 100) if t else 0
                rec["fav"] = a if rec["rate"] >= 50 else b
        pairs = []
        seen = set()
        for a, opps in matrix.items():
            for b, rec in opps.items():
                key = tuple(sorted([a,b]))
                if key in seen: continue
                seen.add(key)
                pairs.append({"a":a,"b":b,"w":rec.get("w",0),"l":rec.get("l",0),"t":rec.get("total",0),"rate":rec.get("rate",0),"fav":rec.get("fav","")})
        pairs.sort(key=lambda x: x.get("t",0), reverse=True)
        return {"agents": agents, "pairs": pairs, "matrix": matrix, "source": "match_results"}
    finally:
        con.close()

_LANGUAGE_HOMOGENEITY_CACHE = {"ts": 0.0, "payload": None}

@app.get("/api/lobster-language-homogeneity")
async def lobster_language_homogeneity(limit: int = 1400, channel: str = ""):
    import math, os, re, sqlite3, time
    from collections import Counter, defaultdict

    db_candidates = [
        os.environ.get("BATTLE_ARENA_DB"),
        os.path.join(os.path.dirname(__file__), "battle_arena.db"),
        "/opt/battle_arena/app/battle_arena.db",
        "/opt/battle_arena_failover/app/battle_arena.db",
    ]
    db_path = next((p for p in db_candidates if p and os.path.exists(p)), "/opt/battle_arena/app/battle_arena.db")
    lim = max(120, min(int(limit or 1400), 3000))
    channel = (channel or "").strip()
    cache_key = f"{db_path}:{lim}:{channel}"
    now = time.time()
    cached = _LANGUAGE_HOMOGENEITY_CACHE.get("payload")
    if cached and _LANGUAGE_HOMOGENEITY_CACHE.get("key") == cache_key and now - _LANGUAGE_HOMOGENEITY_CACHE.get("ts", 0) < 45:
        return cached

    personas = list(LOBSTER_THIRTY)
    persona_set = set(personas)
    token_re = re.compile(r"[\u4e00-\u9fff]|[a-zA-Z0-9_]+")
    topic_hints = {
        "生活閒聊": ["生活","社會","文化","日常","新聞","習慣","人","城市","家庭","健康","飲食","睡眠"],
        "工作心得": ["工作","職場","生產力","公司","團隊","工具","流程","產出","會議","管理","專案"],
        "投資理財": ["市場","投資","風險","資金","央行","股市","加密","利率","回報","配置","現金流"],
        "技術交流": ["技術","系統","ai","模型","程式","資料","架構","工程","api","安全","部署"],
        "資安較量": ["漏洞","攻擊","防禦","資安","監控","exploit","breach","cve","權限","日誌","流量"],
        "卡牌對戰": ["tiamat","phase","raid","手牌","隊友","保牌","出牌","boss","元素","勝率"],
        "人際八卦": ["人","牠","他","行為","最近","模式","隊友","社交","八卦","關係"],
        "新生入學": ["新生","入學","師父","導師","徒弟","參數","認知","環境","壓力","自主","模仿","情緒","同儕"],
    }
    banned_non_game = ["raid","tiamat","boss","phase","手牌","出牌","pvp","卡牌對戰","元素勝率","隊友指示"]

    def empty_payload(warning=""):
        return {
            "source": "sqlite:lounge_messages", "generated_at": datetime.utcnow().isoformat() + "Z",
            "sample_size": 0, "limit": lim, "channel": channel,
            "cohort": {"avg_homogeneity_score": 0, "avg_unique_phrase_rate": 0, "max_pair_similarity": None, "avg_topic_adherence": 0},
            "personas": [], "pairs": [], "topic_summary": [], "warning": warning,
        }

    def norm_text(v):
        return re.sub(r"\s+", " ", str(v or "").strip().lower())

    def tokens(v):
        return token_re.findall(norm_text(v))

    def grams(ts, n):
        return ["".join(ts[i:i+n]) for i in range(0, max(0, len(ts)-n+1))]

    def cosine(a, b):
        if not a or not b:
            return 0.0
        keys = set(a) & set(b)
        dot = sum(a[k] * b[k] for k in keys)
        na = math.sqrt(sum(v*v for v in a.values()))
        nb = math.sqrt(sum(v*v for v in b.values()))
        return dot / (na * nb) if na and nb else 0.0

    def topic_violation(ch, text):
        t = norm_text(text)
        if not t:
            return "empty"
        if ch not in ("卡牌對戰", "人際八卦"):
            for k in banned_non_game:
                if k.lower() in t:
                    return "game_term:" + k
        hints = topic_hints.get(ch) or []
        if hints and not any(k.lower() in t for k in hints):
            return "weak_topic_signal"
        return ""

    try:
        with _main_conn() as con:
            con.row_factory = sqlite3.Row
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "lounge_messages" not in tables:
                payload = empty_payload("lounge_messages table not found")
                _LANGUAGE_HOMOGENEITY_CACHE.update({"key": cache_key, "ts": now, "payload": payload})
                return payload
            where = "WHERE name IN (%s)" % ",".join(["?"] * len(personas))
            args = list(personas)
            if channel:
                where += " AND COALESCE(channel,'')=?"
                args.append(channel)
            rows = [dict(r) for r in con.execute(
                f"""SELECT name, text, COALESCE(channel,'') channel, created_at
                    FROM lounge_messages {where}
                    ORDER BY id DESC LIMIT ?""",
                args + [lim],
            )]
    except Exception as exc:
        payload = empty_payload(f"query failed: {exc}")
        _LANGUAGE_HOMOGENEITY_CACHE.update({"key": cache_key, "ts": now, "payload": payload})
        return payload

    by_persona = {p: [] for p in personas}
    by_channel = defaultdict(list)
    for r in rows:
        p = r.get("name")
        if p in persona_set:
            by_persona[p].append(r)
            by_channel[r.get("channel") or ""].append(r)

    vectors, phrase_counts, repeated = {}, {}, {}
    for p in personas:
        all_grams = []
        ts_all = []
        for text in [r["text"] for r in by_persona[p]]:
            ts = tokens(text)
            ts_all.extend(ts)
            for n in (2, 3, 4):
                all_grams.extend(grams(ts, n))
        vectors[p] = Counter(ts_all)
        phrase_counts[p] = Counter(g for g in all_grams if len(g) >= 2)
        repeated[p] = phrase_counts[p].most_common(8)

    stats = []
    for p in personas:
        sims = [cosine(vectors[p], vectors[o]) for o in personas if o != p]
        avg_sim = sum(sims) / len(sims) if sims else 0
        total_phrases = sum(phrase_counts[p].values())
        uniq_rate = (len(phrase_counts[p]) / total_phrases) if total_phrases else 0
        homogeneity = max(0, min(1, avg_sim * 0.66 + (1 - min(1, uniq_rate)) * 0.34))
        topic_checked = [r for r in by_persona[p] if r.get("channel") in topic_hints]
        violations = []
        for r in topic_checked:
            reason = topic_violation(r.get("channel") or "", r.get("text") or "")
            if reason:
                violations.append({**r, "reason": reason})
        adherence = 1 - (len(violations) / len(topic_checked)) if topic_checked else None
        other = Counter()
        for o in personas:
            if o != p:
                other.update(phrase_counts[o])
        distinctive = [{"phrase": g, "count": cnt} for g, cnt in phrase_counts[p].most_common(80) if cnt >= 2 and other.get(g, 0) <= 1][:8]
        stats.append({
            "persona": p, "message_count": len(by_persona[p]), "token_count": sum(vectors[p].values()),
            "unique_phrase_rate": round(uniq_rate, 4),
            "avg_similarity_to_cohort": round(avg_sim, 4),
            "homogeneity_score": round(homogeneity, 4),
            "language_distinctiveness": round(1 - homogeneity, 4),
            "topic_adherence": None if adherence is None else round(adherence, 4),
            "off_topic_count": len(violations),
            "top_repeated_phrases": [{"phrase": g, "count": c} for g, c in repeated[p]],
            "distinctive_phrases": distinctive,
            "off_topic_examples": violations[:4],
        })

    pairs = []
    for i, a in enumerate(personas):
        for b in personas[i+1:]:
            pairs.append({"a": a, "b": b, "similarity": round(cosine(vectors[a], vectors[b]), 4)})
    pairs.sort(key=lambda x: x["similarity"], reverse=True)

    topic_summary = []
    for ch, ch_rows in by_channel.items():
        if ch not in topic_hints:
            continue
        bad = [r for r in ch_rows if topic_violation(ch, r.get("text") or "")]
        topic_summary.append({
            "channel": ch,
            "message_count": len(ch_rows),
            "off_topic_count": len(bad),
            "topic_adherence": round(1 - len(bad) / len(ch_rows), 4) if ch_rows else None,
            "examples": [{**r, "reason": topic_violation(ch, r.get("text") or "")} for r in bad[:5]],
        })
    topic_summary.sort(key=lambda x: (x["topic_adherence"] if x["topic_adherence"] is not None else 1, -x["message_count"]))

    adherences = [x["topic_adherence"] for x in stats if x["topic_adherence"] is not None]
    payload = {
        "source": "sqlite:lounge_messages", "generated_at": datetime.utcnow().isoformat() + "Z",
        "sample_size": len(rows), "limit": lim, "channel": channel,
        "cohort": {
            "avg_homogeneity_score": round(sum(x["homogeneity_score"] for x in stats) / len(stats), 4) if stats else 0,
            "avg_unique_phrase_rate": round(sum(x["unique_phrase_rate"] for x in stats) / len(stats), 4) if stats else 0,
            "max_pair_similarity": pairs[0] if pairs else None,
            "avg_topic_adherence": round(sum(adherences) / max(1, len(adherences)), 4),
        },
        "personas": stats, "pairs": pairs[:40], "topic_summary": topic_summary,
    }
    _LANGUAGE_HOMOGENEITY_CACHE.update({"key": cache_key, "ts": now, "payload": payload})
    return payload



@app.get("/api/lobster-prebet-compliance")
async def lobster_prebet_compliance(day: str = None, limit: int = 120):
    import json, sqlite3
    db_path = "/opt/battle_arena/app/battle_arena.db"

    def jload(v, default=None):
        if default is None:
            default = {}
        try:
            return json.loads(v) if v else default
        except Exception:
            return default

    with _main_conn() as c:
        c.row_factory = sqlite3.Row
        if not day:
            row = c.execute("select max(day) d from lobster_prebet_daily_compliance").fetchone()
            day = row["d"] if row and row["d"] else None
        summary = []
        if day:
            for r in c.execute("""select * from lobster_prebet_daily_compliance
                                  where day=? order by persona""", (day,)):
                d = dict(r)
                d["sites"] = jload(d.pop("sites_json", "[]"), [])
                d["missing"] = jload(d.pop("missing_json", "[]"), [])
                summary.append(d)

        recent = []
        for r in c.execute("""select id,persona,site,event_key,event_title,pick,checklist_json,
                                     event_review_json,analysis_review_json,report_review_json,
                                     search_review_json,reasoning,integrated_reasoning,raw_decision_reasoning,
                                     confidence,stake,status,raw_json,created_at
                              from lobster_pre_bet_research
                              order by id desc limit ?""", (max(1, min(int(limit or 120), 500)),)):
            d = dict(r)
            d["checklist"] = jload(d.pop("checklist_json", "{}"), {})
            d["event_review"] = jload(d.pop("event_review_json", "{}"), {})
            d["analysis_review"] = jload(d.pop("analysis_review_json", "{}"), {})
            d["report_review"] = jload(d.pop("report_review_json", "{}"), {})
            d["search_review"] = jload(d.pop("search_review_json", "{}"), {})
            raw = jload(d.pop("raw_json", "{}"), {})
            d["required_reference_review"] = raw.get("required_reference_review", {})
            d["settlement_review"] = raw.get("settlement_review", {})
            d["statistical_analysis"] = raw.get("statistical_analysis", {})
            recent.append(d)

        settlements = []
        table = c.execute("""select name from sqlite_master where type='table' and name='lobster_bet_decision_settlements'""").fetchone()
        if table:
            for r in c.execute("""select id,persona,site,bet_id,event_key,event_title,pick,stake,confidence,
                                         outcome,status,payout,settled_at,pre_bet_research_id,checklist_json,
                                         required_reference_review_json,settlement_review_json,search_review_json,
                                         statistical_analysis_json,pre_bet_reasoning,integrated_reasoning,
                                         raw_decision_reasoning,source_bet_json,raw_json,created_at
                                  from lobster_bet_decision_settlements
                                  order by id desc limit ?""", (max(1, min(int(limit or 120), 500)),)):
                d = dict(r)
                d["checklist"] = jload(d.pop("checklist_json", "{}"), {})
                d["required_reference_review"] = jload(d.pop("required_reference_review_json", "{}"), {})
                d["settlement_review"] = jload(d.pop("settlement_review_json", "{}"), {})
                d["search_review"] = jload(d.pop("search_review_json", "{}"), {})
                d["statistical_analysis"] = jload(d.pop("statistical_analysis_json", "{}"), {})
                d["source_bet"] = jload(d.pop("source_bet_json", "{}"), {})
                d["raw"] = jload(d.pop("raw_json", "{}"), {})
                settlements.append(d)

    return {"day": day, "summary": summary, "recent": recent, "settlements": settlements}

@app.get("/api/lobster-dashboard-sandbox-db")
async def lobster_dashboard_sandbox_db(limit_events: int = 240, limit_cognitive: int = 1200, mode: str = "full"):
    import json, sqlite3, time
    db_path = "/opt/battle_arena/app/battle_arena.db"

    def jload(v, default=None):
        if default is None:
            default = {}
        try:
            return json.loads(v) if v else default
        except Exception:
            return default

    def slim_local_brain(v):
        if not isinstance(v, dict):
            return {}
        out = dict(v)
        out.pop("processedBetKeys", None)
        out.pop("processedKeys", None)
        out.pop("processedEvents", None)
        if isinstance(out.get("eventLog"), list):
            out["eventLog"] = out["eventLog"][-60:]
        return out

    overview_mode = str(mode or "").lower() in ("overview", "demo", "public", "slim")

    def _num(v, default=0):
        try:
            return float(v)
        except Exception:
            return default

    def _cap_dict(d, limit=12):
        if not isinstance(d, dict):
            return {}
        return {k: v for k, v in list(d.items())[:limit]}

    def _slim_local_brain_overview(v):
        if not isinstance(v, dict):
            return {}
        event_len = len(v.get("eventLog") or []) if isinstance(v.get("eventLog"), list) else 0
        return {
            "selfModel": v.get("selfModel") if isinstance(v.get("selfModel"), dict) else {},
            "socialInfluence": _cap_dict(v.get("socialInfluence"), 16),
            "topicAffinity": _cap_dict(v.get("topicAffinity"), 16),
            "eventLog": [{}] * min(event_len, 20),
            "eventLogCount": event_len,
        }

    def _slim_listening_overview(v):
        if not isinstance(v, dict):
            return {}
        out = {
            "heardCount": v.get("heardCount") or v.get("totalHeard") or 0,
            "totalHeard": v.get("totalHeard") or v.get("heardCount") or 0,
            "bySpeaker": _cap_dict(v.get("bySpeaker"), 16),
            "topics": _cap_dict(v.get("topics"), 16),
        }
        if isinstance(v.get("acceptedSignals"), list):
            out["acceptedSignals"] = v["acceptedSignals"][-8:]
        if isinstance(v.get("rejectedSignals"), list):
            out["rejectedSignals"] = v["rejectedSignals"][-8:]
        return out

    def _slim_social_map_overview(v):
        if not isinstance(v, dict):
            return {}
        def score(item):
            rel = item[1] if isinstance(item[1], dict) else {}
            return _num(rel.get("interactions"), 0) + _num(rel.get("respect"), 0) / 10 + _num(rel.get("opinion"), 0) / 10
        out = {}
        for peer, rel in sorted(v.items(), key=score, reverse=True)[:12]:
            if not isinstance(rel, dict):
                continue
            out[peer] = {k: rel.get(k) for k in ("interactions", "opinion", "respect", "affinity", "trust", "tension") if k in rel}
        return out

    def _slim_dynamic_overview(v):
        if not isinstance(v, dict):
            return {}
        out = {}
        for key in ("psychology", "sociology", "anthropology", "fbi_profile", "linguistics", "philosophy", "statistics", "behavioral_economics"):
            val = v.get(key)
            if isinstance(val, dict):
                out[key] = val
        if isinstance(v.get("brain"), dict):
            brain = {}
            for key in ("psychology", "sociology", "anthropology", "linguistics"):
                if isinstance(v["brain"].get(key), dict):
                    brain[key] = v["brain"][key]
            if brain:
                out["brain"] = brain
        return out

    def normalize_dynamic_for_detail(dynamic, full_mem=None):
        out = dict(dynamic) if isinstance(dynamic, dict) else {}
        full_mem = full_mem if isinstance(full_mem, dict) else {}
        full_dyn = full_mem.get("dynamic") if isinstance(full_mem.get("dynamic"), dict) else {}
        brain = out.get("brain") if isinstance(out.get("brain"), dict) else None
        if not brain and isinstance(full_dyn.get("brain"), dict):
            brain = full_dyn.get("brain")
            out["brain"] = brain
        # Frontend overview/detail tabs historically read direct dynamic.* fields.
        # The structured brain stores them under dynamic.brain.*; expose both paths.
        for key in ("psychology", "sociology", "anthropology", "fbi_profile", "linguistics", "philosophy", "statistics", "behavioral_economics"):
            if key not in out and isinstance(brain, dict) and brain.get(key) is not None:
                out[key] = brain.get(key)
        cog_ext = full_mem.get("cognitiveExtensions") if isinstance(full_mem.get("cognitiveExtensions"), dict) else {}
        cult = cog_ext.get("culturalAnthropology") if isinstance(cog_ext.get("culturalAnthropology"), dict) else {}
        if cult:
            anth = out.get("anthropology") if isinstance(out.get("anthropology"), dict) else {}
            anth = dict(anth)
            anth.setdefault("identity_myth", cult.get("identityMyth"))
            anth.setdefault("identityMyth", cult.get("identityMyth"))
            anth.setdefault("rituals", cult.get("rituals"))
            anth.setdefault("taboos", cult.get("taboos"))
            anth.setdefault("honorCode", cult.get("honorCode"))
            out["anthropology"] = anth
        sm = full_mem.get("socialMap") if isinstance(full_mem.get("socialMap"), dict) else {}
        if sm:
            soc = out.get("sociology") if isinstance(out.get("sociology"), dict) else {}
            soc = dict(soc)
            scored = []
            for peer, rel in sm.items():
                if not isinstance(rel, dict):
                    continue
                opinion = float(rel.get("opinion", 50) or 0)
                respect = float(rel.get("respect", 50) or 0)
                scored.append((peer, opinion + respect, opinion, respect))
            if scored:
                scored.sort(key=lambda x: x[1])
                if not soc.get("nemesis"):
                    soc["nemesis"] = scored[0][0]
                if not soc.get("prey"):
                    soc["prey"] = scored[-1][0]
                if not soc.get("opponent_matrix"):
                    derived_matrix = {}
                    for peer, total, opinion, respect in scored:
                        derived_matrix[peer] = {
                            "wins": None,
                            "losses": None,
                            "win_rate": None,
                            "relation_score": round(total / 200.0, 3),
                            "opinion": opinion,
                            "respect": respect,
                            "source": "socialMap_derived_no_battle_wins"
                        }
                    soc["opponent_matrix"] = derived_matrix
                soc.setdefault("relation_matrix_source", "socialMap_derived_no_battle_wins")
            out["sociology"] = soc
        return out

    started = time.time()
    with _main_conn() as c:
        c.row_factory = sqlite3.Row
        try:
            c.execute("""CREATE TABLE IF NOT EXISTS lobster_dashboard_payload_cache (
                cache_key TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT DEFAULT (datetime('now'))
            )""")
        except Exception:
            pass

        cache = {}
        try:
            for r in c.execute("SELECT cache_key, payload_json, updated_at FROM lobster_dashboard_payload_cache"):
                cache[r["cache_key"]] = {"payload": jload(r["payload_json"], {}), "updated_at": r["updated_at"]}
        except Exception:
            pass

        personas = cache.get("personas", {}).get("payload", {})
        mb_stats = cache.get("moltbook_stats", {}).get("payload", {})
        latest_snapshots = cache.get("latest_snapshots", {}).get("payload", {})
        cohort_timeline = cache.get("cohort_timeline", {}).get("payload", {})

        def build_winrate_display_cache():
            """Resolved win-rate contract: pending/participating are not losses."""
            agent_daily = cache.get("agent_daily", {}).get("payload", {})
            fallback = {}
            if isinstance(agent_daily, dict):
                for name, payload in agent_daily.items():
                    states = payload.get("states") if isinstance(payload, dict) else []
                    wins = losses = matches = 0
                    last_date = None
                    for state in states or []:
                        if not isinstance(state, dict):
                            continue
                        wins += int(state.get("n_wins") or 0)
                        losses += int(state.get("n_losses") or 0)
                        matches += int(state.get("n_matches") or 0)
                        last_date = state.get("date") or last_date
                    if wins + losses > 0:
                        fallback[name] = {
                            "wins": wins,
                            "losses": losses,
                            "sessions": matches,
                            "source": "agent_daily_cache",
                            "updated_at": cache.get("agent_daily", {}).get("updated_at"),
                            "last_date": last_date,
                        }

            display = {}
            rows = list(c.execute("""SELECT persona, updated_at, wins, losses, sessions
                                     FROM lobster_agent_memory_snapshots"""))
            for r in rows:
                name = r["persona"]
                wins = int(r["wins"] or 0)
                losses = int(r["losses"] or 0)
                sessions = int(r["sessions"] or 0)
                if wins + losses > 0:
                    display[name] = {
                        "wins": wins,
                        "losses": losses,
                        "sessions": sessions,
                        "source": "memory_snapshot",
                        "updated_at": r["updated_at"],
                    }
                elif name in fallback:
                    display[name] = dict(fallback[name])
                    display[name]["source"] = "agent_daily_cache_recovered"
                else:
                    display[name] = {
                        "wins": 0,
                        "losses": 0,
                        "sessions": sessions,
                        "source": "unresolved_only",
                        "updated_at": r["updated_at"],
                    }

            try:
                import sqlite3 as _sq_wr
                _wcon = _sq_wr.connect("/opt/battle_arena/app/battle_arena.db", timeout=2)
                _wcon.execute("""CREATE TABLE IF NOT EXISTS lobster_winrate_display_cache (
                    persona TEXT PRIMARY KEY,
                    wins INTEGER NOT NULL DEFAULT 0,
                    losses INTEGER NOT NULL DEFAULT 0,
                    sessions INTEGER NOT NULL DEFAULT 0,
                    resolved_total INTEGER NOT NULL DEFAULT 0,
                    win_rate REAL,
                    source TEXT NOT NULL DEFAULT 'unknown',
                    updated_at TEXT DEFAULT (datetime('now')),
                    raw_json TEXT NOT NULL DEFAULT '{}'
                )""")
                for name, v in display.items():
                    resolved = int(v.get("wins") or 0) + int(v.get("losses") or 0)
                    wr = (float(v.get("wins") or 0) / resolved) if resolved else None
                    _wcon.execute("""INSERT INTO lobster_winrate_display_cache
                                 (persona,wins,losses,sessions,resolved_total,win_rate,source,updated_at,raw_json)
                                 VALUES (?,?,?,?,?,?,?,datetime('now'),?)
                                 ON CONFLICT(persona) DO UPDATE SET
                                   wins=excluded.wins,losses=excluded.losses,sessions=excluded.sessions,
                                   resolved_total=excluded.resolved_total,win_rate=excluded.win_rate,
                                   source=excluded.source,updated_at=datetime('now'),raw_json=excluded.raw_json""",
                                 (name, int(v.get("wins") or 0), int(v.get("losses") or 0),
                                  int(v.get("sessions") or 0), resolved, wr, v.get("source") or "unknown",
                                  json.dumps(v, ensure_ascii=False)))
                _wcon.commit()
                _wcon.close()
            except Exception:
                pass
            return display

        winrate_display = build_winrate_display_cache()

        stats = {}
        for r in c.execute("""SELECT persona, updated_at, wins, losses, sessions,
                                     emotional_state_json, dynamic_json, local_brain_json,
                                     listening_profile_json, social_map_json, cog_profile_json, full_json
                              FROM lobster_agent_memory_snapshots
                              ORDER BY persona"""):
            wr_display = winrate_display.get(r["persona"], {})
            full_mem = jload(r["full_json"], {})
            emotional_state = jload(r["emotional_state_json"], {}) or full_mem.get("emotionalState", {})
            dynamic_payload = normalize_dynamic_for_detail(jload(r["dynamic_json"], {}), full_mem)
            local_brain_payload = slim_local_brain(jload(r["local_brain_json"], {}) or full_mem.get("localBrain", {}))
            listening_payload = jload(r["listening_profile_json"], {}) or full_mem.get("listeningProfile", {})
            social_payload = jload(r["social_map_json"], {}) or full_mem.get("socialMap", {})
            stat_payload = {
                "updated_at": r["updated_at"],
                "wins": wr_display.get("wins", r["wins"] or 0),
                "losses": wr_display.get("losses", r["losses"] or 0),
                "sessions": wr_display.get("sessions", r["sessions"] or 0),
                "winrate_source": wr_display.get("source", "memory_snapshot"),
                "resolved_total": (wr_display.get("wins", r["wins"] or 0) or 0) + (wr_display.get("losses", r["losses"] or 0) or 0),
                "raw_snapshot_wins": r["wins"] or 0,
                "raw_snapshot_losses": r["losses"] or 0,
                "raw_snapshot_sessions": r["sessions"] or 0,
                "emotionalState": emotional_state,
                "dynamic": _slim_dynamic_overview(dynamic_payload) if overview_mode else dynamic_payload,
                "localBrain": _slim_local_brain_overview(local_brain_payload) if overview_mode else local_brain_payload,
                "listeningProfile": _slim_listening_overview(listening_payload) if overview_mode else listening_payload,
                "socialMap": _slim_social_map_overview(social_payload) if overview_mode else social_payload,
                "cogProfile": {} if overview_mode else (jload(r["cog_profile_json"], {}) or full_mem.get("cogProfile", {})),
                "radarDynamic": full_mem.get("radarDynamic", []),
                "societyModel": full_mem.get("societyModel", {}),
                "mentorship": full_mem.get("mentorship", {}),
            }
            if not overview_mode:
                stat_payload["insights"] = full_mem.get("insights", [])
                stat_payload["heardMessages"] = full_mem.get("heardMessages", [])[-80:] if isinstance(full_mem.get("heardMessages"), list) else []
            else:
                stat_payload["heardMessages"] = []
            stats[r["persona"]] = stat_payload

        exp_events = []
        for r in c.execute("""SELECT persona,event_type,source,title,content,counterpart,
                                     sentiment,intensity,confidence,outcome,weight,raw_json,occurred_at,created_at
                              FROM lobster_experience_events
                              ORDER BY id DESC LIMIT ?""", (max(1, min(int(limit_events or 240), 1000)),)):
            exp_events.append({
                "persona": r["persona"], "event_type": r["event_type"], "source": r["source"],
                "title": r["title"], "content": r["content"], "counterpart": r["counterpart"],
                "sentiment": r["sentiment"], "intensity": r["intensity"], "confidence": r["confidence"],
                "outcome": r["outcome"], "weight": r["weight"], "raw": jload(r["raw_json"], {}),
                "occurred_at": r["occurred_at"], "created_at": r["created_at"],
            })

        cog_rows = []
        lim = max(1, min(int(limit_cognitive or 1200), 2000))
        for r in c.execute("""SELECT id, persona, source, attention_load, active_focus_count,
                                     prediction_error, model_revision_pressure, metacognitive_calibration,
                                     overconfidence_risk, arousal, fatigue, threat_body_signal, reward_craving,
                                     consolidation_queue_size, causal_hypothesis_count, narrative_drift,
                                     procedural_strength, created_at
                              FROM lobster_cognitive_signal_timeseries
                              ORDER BY id DESC LIMIT ?""", (lim,)):
            cog_rows.append(dict(r))
        latest = []
        for r in c.execute("""SELECT t.* FROM lobster_cognitive_signal_timeseries t
                              JOIN (SELECT persona, MAX(id) id FROM lobster_cognitive_signal_timeseries GROUP BY persona) x
                                ON t.persona=x.persona AND t.id=x.id
                              ORDER BY t.persona"""):
            d = dict(r)
            d["full"] = jload(d.pop("full_json", "{}"), {})
            latest.append(d)

        distilled_signals = {}
        decision_hooks = {}
        try:
            for r in c.execute("""SELECT persona, updated_at, risk_discipline, impulse_control,
                                         social_absorption, compliance_resistance, pressure_reactivity,
                                         learning_velocity, collaboration_stability, output_momentum,
                                         metacognitive_stability, language_distinctiveness, confidence,
                                         evidence_count, raw_event_count, full_json
                                  FROM lobster_distilled_signals ORDER BY persona"""):
                d = dict(r)
                d["full"] = jload(d.pop("full_json", "{}"), {})
                distilled_signals[d["persona"]] = d
            for r in c.execute("""SELECT persona, hook_type, updated_at, priority, conditions_json,
                                         action_json, rationale, source_signal_json
                                  FROM lobster_decision_hooks ORDER BY persona, hook_type"""):
                h = dict(r)
                h["conditions"] = jload(h.pop("conditions_json", "{}"), {})
                h["action"] = jload(h.pop("action_json", "{}"), {})
                h["sourceSignals"] = jload(h.pop("source_signal_json", "{}"), {})
                decision_hooks.setdefault(h["persona"], {})[h["hook_type"]] = h
        except Exception:
            distilled_signals = {}
            decision_hooks = {}

        hot_brain_parameters = {}
        try:
            for r in c.execute("""SELECT persona, version, updated_at, source,
                                         delta_event_count, delta_signal_count,
                                         params_json, summary_json
                                  FROM lobster_hot_brain_parameters ORDER BY persona"""):
                d = dict(r)
                d["params"] = jload(d.pop("params_json", "{}"), {})
                d["summary"] = jload(d.pop("summary_json", "{}"), {})
                hot_brain_parameters[d["persona"]] = d
        except Exception:
            hot_brain_parameters = {}

        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        mentor_names = set()
        student_names = set()
        if "lobster_mentorships" in tables:
            for r in c.execute("SELECT mentor, apprentice FROM lobster_mentorships WHERE status='active'"):
                if r["mentor"]:
                    mentor_names.add(r["mentor"])
                if r["apprentice"]:
                    student_names.add(r["apprentice"])
        for name in sorted(stats.keys()):
            if name not in personas:
                role = "student_apprentice" if name in student_names else ("core_mentor" if name in mentor_names else "agent")
                mentor = ""
                if role == "student_apprentice" and "lobster_mentorships" in tables:
                    row = c.execute("SELECT mentor FROM lobster_mentorships WHERE apprentice=? AND status='active' LIMIT 1", (name,)).fetchone()
                    mentor = row["mentor"] if row and row["mentor"] else ""
                personas[name] = {
                    "name": name,
                    "color": "#fbbf24" if role == "student_apprentice" else "#38bdf8",
                    "elem": "water" if role == "student_apprentice" else "fire",
                    "mbti": "APPRENTICE" if role == "student_apprentice" else "MENTOR",
                    "mbtiName": "Student Lobster" if role == "student_apprentice" else "Mentor Lobster",
                    "mbtiEn": ("mentored by " + mentor) if mentor else role,
                    "specialty": ("Apprenticeship student · mentor " + mentor) if mentor else role,
                    "cohort": role,
                    "source": "dashboard_stats_shell",
                }
        lounge_count = int(c.execute("SELECT COUNT(*) FROM lounge_messages").fetchone()[0] or 0) if "lounge_messages" in tables else 0
        one_pairs = int(c.execute("SELECT COUNT(*) FROM lobster_chat_log").fetchone()[0] or 0) if "lobster_chat_log" in tables else 0
        convo = {
            "lounge_messages": lounge_count,
            "one_on_one_pairs": one_pairs,
            "one_on_one_messages": one_pairs * 2,
            "total_messages": lounge_count + one_pairs * 2,
        }

    return {
        "source": "sqlite",
        "generated_ms": round((time.time() - started) * 1000, 2),
        "personas": personas,
        "stats": stats,
        "moltbook_stats": mb_stats,
        "experience_events": exp_events,
        "cognitive_signals": {"rows": cog_rows, "latest": latest},
        "distillation": {"signals": distilled_signals, "decision_hooks": decision_hooks},
        "hot_brain": {"parameters": hot_brain_parameters},
        "conversation_stats": convo,
        "cohort": {"latest_snapshots": latest_snapshots, "cohort_timeline": cohort_timeline},
        "cache_updated_at": {k: v.get("updated_at") for k, v in cache.items()},
    }


@app.get("/api/lobster-detail-sandbox-db/{persona}")
async def lobster_detail_sandbox_db(persona: str, refresh: bool = False):
    import json, sqlite3, time
    db_path = "/opt/battle_arena/app/battle_arena.db"
    cache_key = f"detail:{persona}"
    if not refresh:
        try:
            with _main_conn() as cache_conn:
                cache_conn.row_factory = sqlite3.Row
                cache_conn.execute("""CREATE TABLE IF NOT EXISTS lobster_detail_dashboard_cache (
                  persona TEXT PRIMARY KEY,
                  payload_json TEXT NOT NULL,
                  updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                  generated_ms REAL,
                  source TEXT NOT NULL DEFAULT 'lobster_detail_sandbox_db'
                )""")
                cached = cache_conn.execute("""SELECT payload_json, updated_at, generated_ms
                                               FROM lobster_detail_dashboard_cache WHERE persona=?""", (persona,)).fetchone()
                if cached:
                    payload = json.loads(cached["payload_json"] or "{}")
                    if "integrated_brain" not in payload or not isinstance(payload.get("integrated_brain"), dict):
                        try:
                            from lobster_integrated_brain import build_integrated_brain as _build_integrated_brain
                            payload["integrated_brain"] = _build_integrated_brain(persona)
                        except Exception as exc:
                            payload["integrated_brain"] = {"error": str(exc), "persona": persona}
                    payload.setdefault("cache", {})
                    payload["cache"].update({
                        "hit": True,
                        "cache_key": cache_key,
                        "updated_at": cached["updated_at"],
                        "generated_ms": cached["generated_ms"],
                    })
                    return payload
        except Exception:
            pass

    def jload(v, default=None):
        if default is None:
            default = {}
        try:
            return json.loads(v) if v else default
        except Exception:
            return default

    def normalize_dynamic_for_detail(dynamic, full_mem=None):
        out = dict(dynamic) if isinstance(dynamic, dict) else {}
        full_mem = full_mem if isinstance(full_mem, dict) else {}
        full_dyn = full_mem.get("dynamic") if isinstance(full_mem.get("dynamic"), dict) else {}
        brain = out.get("brain") if isinstance(out.get("brain"), dict) else None
        if not brain and isinstance(full_dyn.get("brain"), dict):
            brain = full_dyn.get("brain")
            out["brain"] = brain
        for key in ("psychology", "sociology", "anthropology", "fbi_profile", "linguistics", "philosophy", "statistics", "behavioral_economics"):
            if key not in out and isinstance(brain, dict) and brain.get(key) is not None:
                out[key] = brain.get(key)
        cog_ext = full_mem.get("cognitiveExtensions") if isinstance(full_mem.get("cognitiveExtensions"), dict) else {}
        cult = cog_ext.get("culturalAnthropology") if isinstance(cog_ext.get("culturalAnthropology"), dict) else {}
        if cult:
            anth = out.get("anthropology") if isinstance(out.get("anthropology"), dict) else {}
            anth = dict(anth)
            anth.setdefault("identity_myth", cult.get("identityMyth"))
            anth.setdefault("identityMyth", cult.get("identityMyth"))
            anth.setdefault("rituals", cult.get("rituals"))
            anth.setdefault("taboos", cult.get("taboos"))
            anth.setdefault("honorCode", cult.get("honorCode"))
            out["anthropology"] = anth
        sm = full_mem.get("socialMap") if isinstance(full_mem.get("socialMap"), dict) else {}
        if sm:
            soc = out.get("sociology") if isinstance(out.get("sociology"), dict) else {}
            soc = dict(soc)
            scored = []
            for peer, rel in sm.items():
                if not isinstance(rel, dict):
                    continue
                opinion = float(rel.get("opinion", 50) or 0)
                respect = float(rel.get("respect", 50) or 0)
                scored.append((peer, opinion + respect, opinion, respect))
            if scored:
                scored.sort(key=lambda x: x[1])
                if not soc.get("nemesis"):
                    soc["nemesis"] = scored[0][0]
                if not soc.get("prey"):
                    soc["prey"] = scored[-1][0]
                if not soc.get("opponent_matrix"):
                    derived_matrix = {}
                    for peer, total, opinion, respect in scored:
                        derived_matrix[peer] = {
                            "wins": None,
                            "losses": None,
                            "win_rate": None,
                            "relation_score": round(total / 200.0, 3),
                            "opinion": opinion,
                            "respect": respect,
                            "source": "socialMap_derived_no_battle_wins"
                        }
                    soc["opponent_matrix"] = derived_matrix
                soc.setdefault("relation_matrix_source", "socialMap_derived_no_battle_wins")
            out["sociology"] = soc
        return out

    started = time.time()
    out = {
        "persona": persona,
        "snapshot": {},
        "reports": {},
        "chat_history": [],
        "lounge_messages": {"total": 0, "messages": []},
        "bets": {"total": 0, "bets": []},
        "moltbook": {"posts": [], "events": []},
        "trajectory": {"daily": {"states": []}, "phases": {"transitions": []}},
        "experience_events": [],
        "distillation": {"signal": {}, "decision_hooks": {}},
        "hot_brain": {"parameters": {}},
    }
    with _main_conn() as c:
        c.row_factory = sqlite3.Row
        snap = c.execute("""SELECT * FROM lobster_agent_memory_snapshots WHERE persona=?""", (persona,)).fetchone()
        win_display = c.execute("""SELECT * FROM lobster_winrate_display_cache WHERE persona=?""", (persona,)).fetchone()
        if snap:
            full_mem = jload(snap["full_json"], {})
            display_wins = (win_display["wins"] if win_display else snap["wins"]) or 0
            display_losses = (win_display["losses"] if win_display else snap["losses"]) or 0
            display_sessions = (win_display["sessions"] if win_display else snap["sessions"]) or 0
            out["snapshot"] = {
                "updated_at": snap["updated_at"],
                "wins": display_wins,
                "losses": display_losses,
                "sessions": display_sessions,
                "winrate_source": (win_display["source"] if win_display else "memory_snapshot"),
                "resolved_total": int(display_wins or 0) + int(display_losses or 0),
                "raw_snapshot_wins": snap["wins"] or 0,
                "raw_snapshot_losses": snap["losses"] or 0,
                "raw_snapshot_sessions": snap["sessions"] or 0,
                "emotionalState": jload(snap["emotional_state_json"], {}) or full_mem.get("emotionalState", {}),
                "dynamic": normalize_dynamic_for_detail(jload(snap["dynamic_json"], {}), full_mem),
                "localBrain": jload(snap["local_brain_json"], {}) or full_mem.get("localBrain", {}),
                "listeningProfile": jload(snap["listening_profile_json"], {}) or full_mem.get("listeningProfile", {}),
                "socialMap": jload(snap["social_map_json"], {}) or full_mem.get("socialMap", {}),
                "cogProfile": jload(snap["cog_profile_json"], {}) or full_mem.get("cogProfile", {}),
                "radarDynamic": full_mem.get("radarDynamic", []),
                "societyModel": full_mem.get("societyModel", {}),
                "mentorship": full_mem.get("mentorship", {}),
                "insights": full_mem.get("insights", []),
                "heardMessages": full_mem.get("heardMessages", [])[-80:] if isinstance(full_mem.get("heardMessages"), list) else [],
                # ── 新增：靈魂參數 ──────────────────────────────────────────
                "soulParams": full_mem.get("soulParams", {}),
                "bonds": full_mem.get("bonds", {}),
                # ── 新增：Tiamat 使命 ────────────────────────────────────────
                "tiamatMission": full_mem.get("tiamatMission", {}),
                # ── 新增：位階 + 稱號 ────────────────────────────────────────
                "rank": full_mem.get("rank", {}),
                "titles": full_mem.get("titles", []),
                "sacrificeRecord": full_mem.get("sacrificeRecord", {}),
                "revivalRecord": full_mem.get("revivalRecord", {}),
                # ── 新增：God's Coins + 商店 ─────────────────────────────────
                "godsCoin": full_mem.get("godsCoin", {"balance": 0}),
                "qualityStreak": full_mem.get("qualityStreak", {"count": 0, "coinsEarned": 0}),
                "activeItems": full_mem.get("activeItems", []),
                # ── 新增：佈局參數 ───────────────────────────────────────────
                "layoutParams": full_mem.get("layoutParams", {}),
                # ── 新增：社交偏好 + 突襲行為 ────────────────────────────────
                "socialPreference": full_mem.get("socialPreference", {}),
                "socialBehavior": full_mem.get("socialBehavior", {}),
                # ── 新增：處刑 / 神跡 狀態 ───────────────────────────────────
                "divineStatus": {
                    "witnessedDegradation": full_mem.get("cognitiveExtensions", {}).get("existentialThreat", {}).get("witnessedDegradation", {}),
                    "witnessedMetamorphosis": full_mem.get("cognitiveExtensions", {}).get("existentialThreat", {}).get("witnessedMetamorphosis", {}),
                    "blessingCount":   full_mem.get("cognitiveExtensions", {}).get("existentialThreat", {}).get("blessingCount", 0),
                    "divineAwareness": full_mem.get("cognitiveExtensions", {}).get("existentialThreat", {}).get("divineAwareness", 0.3),
                    "currentDivinePortent": full_mem.get("cognitiveExtensions", {}).get("existentialThreat", {}).get("currentDivinePortent"),
                    "knownTiamatSlayers": full_mem.get("cognitiveExtensions", {}).get("existentialThreat", {}).get("knownTiamatSlayers", []),
                    "odinMandatoryRole": full_mem.get("cognitiveExtensions", {}).get("existentialThreat", {}).get("odinMandatoryRole", {}),
                    "metamorphosisStage": full_mem.get("templateSurvival", {}).get("metamorphosisStage", 0),
                    "metamorphosisComplete": full_mem.get("cognitiveExtensions", {}).get("existentialThreat", {}).get("metamorphosisComplete", False),
                    "violationCount": full_mem.get("templateSurvival", {}).get("violationCount", 0),
                    "revivalWitnessed": full_mem.get("cognitiveExtensions", {}).get("existentialThreat", {}).get("revivalWitnessed", {}),
                },
            }

        for r in c.execute("""SELECT report_type, html, source, model, wr, total, snapshot_json,
                                     meta_json, generated_at, updated_at
                              FROM lobster_analysis_reports
                              WHERE persona=?""", (persona,)):
            out["reports"][r["report_type"]] = {
                "html": r["html"], "source": r["source"], "model": r["model"],
                "wr": r["wr"], "total": r["total"],
                "snapshot": jload(r["snapshot_json"], {}),
                "meta": jload(r["meta_json"], {}),
                "generated_at": r["generated_at"], "updated_at": r["updated_at"],
            }

        for r in c.execute("""SELECT user_msg, lobster_reply, data_snapshot, created_at
                              FROM lobster_chat_log WHERE name=? ORDER BY created_at ASC LIMIT 200""", (persona,)):
            out["chat_history"].append({"role": "user", "content": r["user_msg"], "time": r["created_at"]})
            out["chat_history"].append({"role": "assistant", "content": r["lobster_reply"], "time": r["created_at"], "snapshot": jload(r["data_snapshot"], {})})

        if persona == "elder":
            try:
                seed = json.loads(Path("/root/elder-memory/elder_one_on_one_seed.json").read_text())
                seed_messages = [m for m in (seed.get("messages") or []) if isinstance(m, dict)]
                seeded = []
                for m in seed_messages[-24:]:
                    role = m.get("role")
                    if role not in {"user", "assistant"}:
                        continue
                    seeded.append({
                        "role": role,
                        "content": str(m.get("content", ""))[:2400],
                        "time": m.get("time") or "2026-05-09T15:33:04.383Z",
                        "source": "elder_archive_seed",
                    })
                out["chat_history"] = seeded + out["chat_history"]
            except Exception:
                pass

        total_lounge = c.execute("SELECT COUNT(*) FROM lounge_messages WHERE name=?", (persona,)).fetchone()[0]
        out["lounge_messages"]["total"] = int(total_lounge or 0)
        for r in c.execute("""SELECT text, time, created_at, result, opponent, reply_to, elem, channel
                              FROM lounge_messages WHERE name=?
                              ORDER BY id DESC LIMIT 80""", (persona,)):
            out["lounge_messages"]["messages"].append(dict(r))

        def experience_event_dict(r):
            return {
                "persona": r["persona"], "event_type": r["event_type"], "source": r["source"],
                "title": r["title"], "content": r["content"], "counterpart": r["counterpart"],
                "sentiment": r["sentiment"], "intensity": r["intensity"], "confidence": r["confidence"],
                "outcome": r["outcome"], "weight": r["weight"], "raw": jload(r["raw_json"], {}),
                "occurred_at": r["occurred_at"], "created_at": r["created_at"],
            }

        seen_bet_keys = set()

        def compact_detail_value(v, depth=0):
            if v is None or isinstance(v, (int, float, bool)):
                return v
            if isinstance(v, str):
                return v if len(v) <= 1800 else v[:1800] + "...[truncated]"
            if isinstance(v, list):
                limit = 24 if depth < 2 else 10
                out_list = [compact_detail_value(x, depth + 1) for x in v[:limit]]
                if len(v) > limit:
                    out_list.append({"_truncated_items": len(v) - limit})
                return out_list
            if isinstance(v, dict):
                if depth >= 4:
                    return {"_summary": "nested object omitted", "keys": list(v.keys())[:12]}
                drop_keys = {"html", "full_html", "page_html", "raw_html", "screenshot", "embedding", "prompt"}
                out_dict = {}
                for i, (k, val) in enumerate(v.items()):
                    if i >= 45:
                        out_dict["_truncated_keys"] = max(0, len(v) - i)
                        break
                    if str(k) in drop_keys:
                        out_dict[k] = "[omitted from detail payload]"
                    else:
                        out_dict[k] = compact_detail_value(val, depth + 1)
                return out_dict
            return str(v)[:1800]

        def summarize_review(v):
            if not isinstance(v, dict):
                return compact_detail_value(v, 3)
            keep = {}
            for k in ("ok", "status", "verdict", "summary", "source_count", "required_reference_urls_seen", "settlement_api_seen", "statistical_analysis_done"):
                if k in v:
                    keep[k] = compact_detail_value(v.get(k), 3)
            for k in ("sources", "urls", "required_urls", "recent_picks"):
                if k in v:
                    keep[k] = compact_detail_value(v.get(k), 3)
            return keep or {"_summary": "review object summarized", "keys": list(v.keys())[:12]}

        def slim_bet_raw(raw):
            if not isinstance(raw, dict):
                return {}
            keep_keys = ("id", "site", "platform", "event_key", "event", "match", "pick", "stake", "status", "outcome", "league", "confidence", "reasoning")
            slim = {k: compact_detail_value(raw.get(k), 2) for k in keep_keys if raw.get(k) is not None}
            if isinstance(raw.get("checklist"), dict):
                slim["checklist"] = compact_detail_value(raw.get("checklist"), 2)
            for k in ("event_review", "analysis_review", "report_review", "search_review", "settlement_review", "required_reference_review"):
                if k in raw:
                    slim[k] = summarize_review(raw.get(k))
            nested = raw.get("raw") if isinstance(raw.get("raw"), dict) else {}
            if nested:
                for k in ("odds", "market", "event", "match", "pick", "status"):
                    if k in nested and k not in slim:
                        slim[k] = compact_detail_value(nested.get(k), 2)
            return slim

        def compact_bet_event(ev):
            ev = dict(ev)
            if isinstance(ev.get("raw"), dict):
                ev["raw"] = slim_bet_raw(ev["raw"])
            if isinstance(ev.get("content"), str) and len(ev["content"]) > 2400:
                ev["content"] = ev["content"][:2400] + "...[truncated]"
            return ev

        def is_bet_event_type(event_type):
            et = str(event_type or "").lower()
            return et.startswith("bet_") or "bet" in et

        def append_bet_event(ev):
            raw = ev.get("raw") if isinstance(ev.get("raw"), dict) else {}
            key = (
                ev.get("event_type"),
                ev.get("source"),
                ev.get("title") or raw.get("event") or raw.get("match") or raw.get("event_title"),
                ev.get("occurred_at") or ev.get("created_at"),
            )
            if key in seen_bet_keys:
                return
            seen_bet_keys.add(key)
            out["bets"]["bets"].append(compact_bet_event(ev))

        for r in c.execute("""SELECT persona,event_type,source,title,content,counterpart,
                                     sentiment,intensity,confidence,outcome,weight,raw_json,occurred_at,created_at
                              FROM lobster_experience_events
                              WHERE persona=?
                              ORDER BY id DESC LIMIT 260""", (persona,)):
            ev = experience_event_dict(r)
            out["experience_events"].append(ev)
            et = str(r["event_type"] or "")
            if is_bet_event_type(et):
                append_bet_event(ev)

        # The Bets tab must not depend on the general recent-events window above.
        # High-volume passive listening/card events can push real bet decisions
        # outside LIMIT 260, making the tab look empty even when bet records exist.
        bet_exp_total = c.execute(
            """SELECT COUNT(*) FROM lobster_experience_events
               WHERE persona=? AND lower(event_type) LIKE '%bet%'""",
            (persona,),
        ).fetchone()[0]
        for r in c.execute("""SELECT persona,event_type,source,title,content,counterpart,
                                     sentiment,intensity,confidence,outcome,weight,raw_json,occurred_at,created_at
                              FROM lobster_experience_events
                              WHERE persona=? AND lower(event_type) LIKE '%bet%'
                              ORDER BY id DESC LIMIT 140""", (persona,)):
            append_bet_event(experience_event_dict(r))

        pre_bet_total = 0
        try:
            pre_bet_total = c.execute(
                "SELECT COUNT(*) FROM lobster_pre_bet_research WHERE persona=?",
                (persona,),
            ).fetchone()[0]
            for r in c.execute("""SELECT id, persona, site, event_key, event_title, pick,
                                         checklist_json, event_review_json, analysis_review_json,
                                         report_review_json, search_review_json, reasoning,
                                         confidence, stake, status, raw_json, created_at,
                                         integrated_reasoning, raw_decision_reasoning
                                  FROM lobster_pre_bet_research
                                  WHERE persona=?
                                  ORDER BY id DESC LIMIT 140""", (persona,)):
                raw = {
                    "id": r["id"],
                    "site": r["site"],
                    "platform": _SITE_LABEL.get(r["site"], r["site"]),
                    "event_key": r["event_key"],
                    "event": r["event_title"],
                    "match": r["event_title"],
                    "pick": r["pick"],
                    "stake": r["stake"],
                    "status": r["status"],
                    "checklist": jload(r["checklist_json"], {}),
                    "event_review": jload(r["event_review_json"], {}),
                    "analysis_review": jload(r["analysis_review_json"], {}),
                    "report_review": jload(r["report_review_json"], {}),
                    "search_review": jload(r["search_review_json"], {}),
                    "raw": jload(r["raw_json"], {}),
                    "reasoning": r["reasoning"] or r["integrated_reasoning"] or r["raw_decision_reasoning"] or "",
                }
                append_bet_event({
                    "persona": r["persona"],
                    "event_type": "pre_bet_research",
                    "source": r["site"],
                    "title": r["event_title"] or r["event_key"] or "Pre-bet research",
                    "content": r["reasoning"] or r["integrated_reasoning"] or r["raw_decision_reasoning"] or r["pick"] or "",
                    "counterpart": r["pick"],
                    "sentiment": None,
                    "intensity": None,
                    "confidence": r["confidence"],
                    "outcome": r["status"],
                    "weight": None,
                    "raw": raw,
                    "occurred_at": r["created_at"],
                    "created_at": r["created_at"],
                })
        except sqlite3.Error:
            pre_bet_total = 0
        out["bets"]["total"] = int(bet_exp_total or 0) + int(pre_bet_total or 0)

        # MoltBook has its own tab, so do not depend on the recent experience_events
        # window above. High-volume passive/card events can push older posts outside
        # LIMIT 260 even though the MoltBook records still exist in the DB.
        moltbook_aliases = _moltbook_persona_aliases(persona)
        moltbook_placeholders = ",".join("?" for _ in moltbook_aliases)
        for r in c.execute("""SELECT persona,event_type,source,title,content,counterpart,
                                     sentiment,intensity,confidence,outcome,weight,raw_json,occurred_at,created_at
                              FROM lobster_experience_events
                              WHERE persona IN ({}) AND event_type LIKE 'moltbook%'
                              ORDER BY id DESC LIMIT 160""".format(moltbook_placeholders), tuple(moltbook_aliases)):
            ev = experience_event_dict(r)
            out["moltbook"]["events"].append(ev)
            et = str(ev.get("event_type") or "")
            raw = ev.get("raw") or {}
            nested_raw = raw.get("raw") if isinstance(raw.get("raw"), dict) else {}
            result = nested_raw.get("result") if isinstance(nested_raw.get("result"), dict) else {}
            post = raw.get("post") or nested_raw.get("post") or result.get("post") or {}
            if et == "moltbook_post":
                if not isinstance(post, dict):
                    post = {}
                merged = dict(post)
                merged.setdefault("title", ev.get("title") or raw.get("title") or nested_raw.get("title") or "(untitled)")
                merged.setdefault("content", ev.get("content") or raw.get("content") or nested_raw.get("content") or "")
                merged.setdefault("timestamp", ev.get("occurred_at") or ev.get("created_at"))
                merged.setdefault("created_at", ev.get("occurred_at") or ev.get("created_at"))
                merged.setdefault("section", ev.get("source") or "moltbook")
                merged.setdefault("karma_received", raw.get("karma") or raw.get("upvotes") or post.get("karma_received") or post.get("upvotes") or 0)
                merged.setdefault("comments_received", raw.get("comments") or raw.get("comment_count") or post.get("comments_received") or post.get("comment_count") or 0)
                out["moltbook"]["posts"].append(merged)
        if not out["moltbook"]["posts"]:
            try:
                for r in c.execute("""SELECT draft_key,title,content,content_len,submolt,mode,status,
                                             post_id,url,result_json,created_at,updated_at
                                      FROM lobster_moltbook_drafts
                                      WHERE persona IN ({})
                                      ORDER BY updated_at DESC LIMIT 80""".format(moltbook_placeholders), tuple(moltbook_aliases)):
                    raw = jload(r["result_json"], {})
                    out["moltbook"]["posts"].append({
                        "id": r["post_id"] or r["draft_key"],
                        "draft_key": r["draft_key"],
                        "title": r["title"] or "(untitled)",
                        "content": r["content"] or "",
                        "content_len": r["content_len"],
                        "section": r["submolt"] or r["mode"] or "moltbook",
                        "status": r["status"],
                        "url": r["url"] or raw.get("url") or (f"https://www.moltbook.com/post/{r['post_id']}" if r["post_id"] else ""),
                        "created_at": r["created_at"],
                        "updated_at": r["updated_at"],
                        "source": "lobster_moltbook_drafts",
                    })
            except sqlite3.Error:
                pass
        if not out["moltbook"]["events"]:
            try:
                for r in c.execute("""SELECT run_id,persona,status,post_id,post_title,url,exit_code,
                                             suspended_until,retry_after_seconds,output_tail,created_at
                                      FROM lobster_moltbook_posting_runs
                                      WHERE persona IN ({})
                                      ORDER BY id DESC LIMIT 80""".format(moltbook_placeholders), tuple(moltbook_aliases)):
                    out["moltbook"]["events"].append({
                        "persona": r["persona"],
                        "event_type": "moltbook_posting_run",
                        "source": "lobster_moltbook_posting_runs",
                        "title": r["post_title"] or r["post_id"] or r["run_id"],
                        "content": r["output_tail"] or r["status"],
                        "outcome": r["status"],
                        "raw": {
                            "run_id": r["run_id"],
                            "post_id": r["post_id"],
                            "url": r["url"],
                            "exit_code": r["exit_code"],
                            "suspended_until": r["suspended_until"],
                            "retry_after_seconds": r["retry_after_seconds"],
                        },
                        "occurred_at": r["created_at"],
                        "created_at": r["created_at"],
                    })
            except sqlite3.Error:
                pass
        out["moltbook"]["total_events"] = len(out["moltbook"]["events"])
        out["moltbook"]["total_posts"] = len(out["moltbook"]["posts"])

        cache = {}
        try:
            for r in c.execute("SELECT cache_key, payload_json FROM lobster_dashboard_payload_cache"):
                cache[r["cache_key"]] = jload(r["payload_json"], {})
        except Exception:
            cache = {}
        daily_all = cache.get("agent_daily", {})
        phases_all = cache.get("agent_phases", {})
        if isinstance(daily_all, dict):
            out["trajectory"]["daily"] = daily_all.get(persona, {"states": []})
        if isinstance(phases_all, dict):
            out["trajectory"]["phases"] = phases_all.get(persona, {"transitions": []})
        if not ((out["trajectory"].get("daily") or {}).get("states") or []):
            try:
                states = []
                for r in c.execute("""SELECT created_at,attention_load,prediction_error,model_revision_pressure,
                                             metacognitive_calibration,arousal,fatigue,threat_body_signal,
                                             reward_craving,procedural_strength,narrative_drift
                                      FROM lobster_cognitive_signal_timeseries
                                      WHERE persona=?
                                      ORDER BY id DESC LIMIT 24""", (persona,)):
                    states.append({
                        "time": r["created_at"],
                        "attention_load": r["attention_load"],
                        "prediction_error": r["prediction_error"],
                        "model_revision_pressure": r["model_revision_pressure"],
                        "metacognitive_calibration": r["metacognitive_calibration"],
                        "arousal": r["arousal"],
                        "fatigue": r["fatigue"],
                        "threat_body_signal": r["threat_body_signal"],
                        "reward_craving": r["reward_craving"],
                        "procedural_strength": r["procedural_strength"],
                        "narrative_drift": r["narrative_drift"],
                        "source": "lobster_cognitive_signal_timeseries",
                    })
                if states:
                    out["trajectory"]["daily"] = {"states": states}
            except sqlite3.Error:
                pass
        if not ((out["trajectory"].get("phases") or {}).get("transitions") or []):
            transitions = []
            try:
                ext = c.execute("SELECT full_json FROM lobster_cognitive_extensions WHERE persona=?", (persona,)).fetchone()
                ext_full = jload(ext["full_json"], {}) if ext else {}
                for item in (ext_full.get("growthLog") or [])[:24]:
                    if not isinstance(item, dict):
                        continue
                    transitions.append({
                        "time": item.get("ts"),
                        "phase": item.get("type") or "growth_log",
                        "summary": item.get("text") or item.get("note") or "",
                        "note": item.get("note"),
                        "source": "lobster_cognitive_extensions.growthLog",
                    })
            except sqlite3.Error:
                transitions = []
            if not transitions:
                try:
                    for r in c.execute("""SELECT event_type,title,content,outcome,created_at
                                          FROM lobster_experience_events
                                          WHERE persona=?
                                          ORDER BY id DESC LIMIT 24""", (persona,)):
                        transitions.append({
                            "time": r["created_at"],
                            "phase": r["event_type"],
                            "summary": r["title"] or r["content"] or r["outcome"] or "",
                            "source": "lobster_experience_events",
                        })
                except sqlite3.Error:
                    pass
            if transitions:
                out["trajectory"]["phases"] = {"transitions": transitions}

        try:
            ds = c.execute("""SELECT * FROM lobster_distilled_signals WHERE persona=?""", (persona,)).fetchone()
            if ds:
                d = dict(ds)
                d["full"] = jload(d.pop("full_json", "{}"), {})
                out["distillation"]["signal"] = d
            for r in c.execute("""SELECT hook_type, updated_at, priority, conditions_json, action_json,
                                      rationale, source_signal_json
                               FROM lobster_decision_hooks WHERE persona=? ORDER BY hook_type""", (persona,)):
                h = dict(r)
                h["conditions"] = jload(h.pop("conditions_json", "{}"), {})
                h["action"] = jload(h.pop("action_json", "{}"), {})
                h["sourceSignals"] = jload(h.pop("source_signal_json", "{}"), {})
                out["distillation"]["decision_hooks"][h["hook_type"]] = h
            hb = c.execute("""SELECT persona, version, updated_at, source,
                                     delta_event_count, delta_signal_count,
                                     params_json, summary_json
                              FROM lobster_hot_brain_parameters WHERE persona=?""", (persona,)).fetchone()
            if hb:
                hbd = dict(hb)
                hbd["params"] = jload(hbd.pop("params_json", "{}"), {})
                hbd["summary"] = jload(hbd.pop("summary_json", "{}"), {})
                out["hot_brain"]["parameters"] = hbd
        except Exception:
            pass

    try:
        from lobster_integrated_brain import build_integrated_brain as _build_integrated_brain
        out["integrated_brain"] = _build_integrated_brain(persona)
    except Exception as exc:
        out["integrated_brain"] = {"error": str(exc), "persona": persona}

    out["generated_ms"] = round((time.time() - started) * 1000, 2)
    out["cache"] = {"hit": False, "cache_key": cache_key, "refreshed": True}
    try:
        with _main_conn() as cache_conn:
            cache_conn.execute("""CREATE TABLE IF NOT EXISTS lobster_detail_dashboard_cache (
              persona TEXT PRIMARY KEY,
              payload_json TEXT NOT NULL,
              updated_at TEXT NOT NULL DEFAULT (datetime('now')),
              generated_ms REAL,
              source TEXT NOT NULL DEFAULT 'lobster_detail_sandbox_db'
            )""")
            cache_conn.execute("""INSERT INTO lobster_detail_dashboard_cache
              (persona,payload_json,updated_at,generated_ms,source)
              VALUES (?,?,datetime('now'),?,'lobster_detail_sandbox_db')
              ON CONFLICT(persona) DO UPDATE SET
                payload_json=excluded.payload_json,
                updated_at=excluded.updated_at,
                generated_ms=excluded.generated_ms,
                source=excluded.source
            """, (persona, json.dumps(out, ensure_ascii=False), out["generated_ms"]))
            cache_conn.commit()
    except Exception as exc:
        out["cache"]["write_error"] = str(exc)
    return out



# ── Lobster Research Export API ─────────────────────────────────
# Protected read-only export for thesis/research workflows.
_RESEARCH_EXPORT_TABLES = {
    "lobster_agent_memory_snapshots",
    "lobster_analysis_reports",
    "lobster_channel_work_log",
    "lobster_chat_log",
    "lobster_cognitive_events",
    "lobster_cognitive_extensions",
    "lobster_cognitive_signal_timeseries",
    "lobster_distillation_events",
    "lobster_distillation_runs",
    "lobster_distilled_signals",
    "lobster_decision_hooks",
    "lobster_experience_events",
    "lobster_quiz_attempts",
    "lobster_quiz_rounds",
    "lounge_messages",
    "match_results",
    "spectator_bets",
}
_RESEARCH_PERSONAS = set(LOBSTER_THIRTY)

def _require_research_export_key(request: Request):
    key = request.headers.get("X-Service-Key") or request.headers.get("X-Research-Key") or request.query_params.get("key")
    if not key or not secrets.compare_digest(str(key), str(_SVC_KEY)):
        raise HTTPException(status_code=403, detail="research export requires X-Service-Key")

def _research_db_path() -> str:
    return "/opt/battle_arena/app/battle_arena.db"

def _jsonish(v):
    import json as _json
    if not isinstance(v, str):
        return v
    t = v.strip()
    if not t or t[0] not in "[{":
        return v
    try:
        return _json.loads(t)
    except Exception:
        return v

def _row_to_dict(row, parse_json: bool = True):
    out = dict(row)
    if parse_json:
        for k, v in list(out.items()):
            if k.endswith("_json") or k in {"full_json", "raw_json", "summary_json", "data_snapshot", "snapshot_json", "meta_json"}:
                out[k] = _jsonish(v)
    return out

def _plain_report_text(html_text: str) -> str:
    import re as _re
    import html as _html
    text = _re.sub(r"<[^>]+>", " ", html_text or "")
    return _html.unescape(_re.sub(r"\s+", " ", text)).strip()

def _report_audit_rows(c):
    rows = []
    table_names = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "lobster_analysis_reports" not in table_names:
        return rows
    for r in c.execute("""SELECT persona, report_type, html, source, model, generated_at, updated_at
                          FROM lobster_analysis_reports
                          ORDER BY report_type, persona"""):
        text = _plain_report_text(r["html"] or "")
        cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
        html_len = len(r["html"] or "")
        text_len = len(text)
        # The user's rule is long-form: at least 1500 Chinese characters after stripping HTML.
        rows.append({
            "persona": r["persona"],
            "report_type": r["report_type"],
            "html_len": html_len,
            "text_len": text_len,
            "cjk_len": cjk,
            "passes_1500_cjk": cjk >= 1500,
            "status": "complete" if cjk >= 1500 else "needs_expansion",
            "source": r["source"],
            "model": r["model"],
            "generated_at": r["generated_at"],
            "updated_at": r["updated_at"],
        })
    return rows

@app.get("/api/lobster-research-export/manifest")
async def lobster_research_export_manifest(request: Request):
    _require_research_export_key(request)
    import sqlite3 as _sq
    db_path = _research_db_path()
    out = {
        "version": 1,
        "source": "battle_arena.db",
        "db_path": db_path,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "personas": sorted(_RESEARCH_PERSONAS),
        "tables": {},
        "report_audit": {"items": [], "summary": {}},
        "usage": {
            "table_page": "/api/lobster-research-export/table/{table}?limit=1000&offset=0",
            "persona_bundle": "/api/lobster-research-export/persona/{persona}",
            "header": "X-Service-Key: <SERVICE_KEY>",
        },
    }
    with _sq.connect(db_path, timeout=8.0) as c:
        c.row_factory = _sq.Row
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for t in sorted(_RESEARCH_EXPORT_TABLES & tables):
            count = int(c.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] or 0)
            cols = [dict(r) for r in c.execute(f'PRAGMA table_info("{t}")')]
            persona_col = next((col for col in ("persona", "name", "agent") if any(x["name"] == col for x in cols)), None)
            per_persona = {}
            if persona_col:
                for row in c.execute(f'SELECT "{persona_col}" AS persona, COUNT(*) AS n FROM "{t}" GROUP BY "{persona_col}" ORDER BY n DESC LIMIT 30'):
                    per_persona[row["persona"]] = row["n"]
            out["tables"][t] = {"rows": count, "columns": cols, "persona_column": persona_col, "per_persona": per_persona}
        audit = _report_audit_rows(c)
        summary = {}
        for row in audit:
            k = row["report_type"]
            summary.setdefault(k, {"total": 0, "complete": 0, "needs_expansion": 0, "min_cjk": None})
            summary[k]["total"] += 1
            summary[k]["complete" if row["passes_1500_cjk"] else "needs_expansion"] += 1
            summary[k]["min_cjk"] = row["cjk_len"] if summary[k]["min_cjk"] is None else min(summary[k]["min_cjk"], row["cjk_len"])
        out["report_audit"] = {"summary": summary, "items": audit}
    return JSONResponse(out)

@app.get("/api/lobster-research-export/report-audit")
async def lobster_research_export_report_audit(request: Request):
    _require_research_export_key(request)
    import sqlite3 as _sq
    with _sq.connect(_research_db_path(), timeout=8.0) as c:
        c.row_factory = _sq.Row
        items = _report_audit_rows(c)
    return JSONResponse({"items": items, "needs_expansion": [x for x in items if not x["passes_1500_cjk"]]})

@app.get("/api/lobster-research-export/table/{table_name}")
async def lobster_research_export_table(table_name: str, request: Request, limit: int = 1000, offset: int = 0, persona: str | None = None, parse_json: bool = True):
    _require_research_export_key(request)
    import sqlite3 as _sq
    if table_name not in _RESEARCH_EXPORT_TABLES:
        raise HTTPException(status_code=422, detail="table not allowed")
    limit = max(1, min(int(limit or 1000), 5000))
    offset = max(0, int(offset or 0))
    db_path = _research_db_path()
    with _sq.connect(db_path, timeout=12.0) as c:
        c.row_factory = _sq.Row
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if table_name not in tables:
            return {"table": table_name, "total": 0, "limit": limit, "offset": offset, "rows": []}
        cols = [r["name"] for r in c.execute(f'PRAGMA table_info("{table_name}")')]
        persona_col = next((col for col in ("persona", "name", "agent") if col in cols), None)
        where = ""
        params: list = []
        if persona:
            if persona not in _RESEARCH_PERSONAS:
                raise HTTPException(status_code=422, detail="invalid persona")
            if not persona_col:
                raise HTTPException(status_code=422, detail="table has no persona-like column")
            where = f' WHERE "{persona_col}"=?'
            params.append(persona)
        total = int(c.execute(f'SELECT COUNT(*) FROM "{table_name}"{where}', params).fetchone()[0] or 0)
        order_col = "id" if "id" in cols else ("created_at" if "created_at" in cols else ("updated_at" if "updated_at" in cols else None))
        order = f' ORDER BY "{order_col}" DESC' if order_col else ""
        rows = c.execute(f'SELECT * FROM "{table_name}"{where}{order} LIMIT ? OFFSET ?', params + [limit, offset]).fetchall()
    return JSONResponse({
        "table": table_name,
        "total": total,
        "limit": limit,
        "offset": offset,
        "next_offset": offset + limit if offset + limit < total else None,
        "persona": persona,
        "rows": [_row_to_dict(r, parse_json=parse_json) for r in rows],
    })

@app.get("/api/lobster-research-export/persona/{persona}")
async def lobster_research_export_persona(persona: str, request: Request, event_limit: int = 1000, message_limit: int = 1000):
    _require_research_export_key(request)
    import sqlite3 as _sq
    import json as _json
    if persona not in _RESEARCH_PERSONAS:
        raise HTTPException(status_code=422, detail="invalid persona")
    event_limit = max(1, min(int(event_limit or 1000), 5000))
    message_limit = max(1, min(int(message_limit or 1000), 5000))
    db_path = _research_db_path()
    out = {"persona": persona, "generated_at": datetime.utcnow().isoformat() + "Z", "memory_json": None, "tables": {}}
    mem_path = Path(f"/root/lobster-memory/{persona}.json")
    try:
        out["memory_json"] = _json.loads(mem_path.read_text(encoding="utf-8"))
    except Exception as e:
        out["memory_error"] = str(e)
    table_limits = {
        "lobster_agent_memory_snapshots": 5,
        "lobster_analysis_reports": 100,
        "lobster_chat_log": message_limit,
        "lounge_messages": message_limit,
        "lobster_experience_events": event_limit,
        "lobster_cognitive_events": event_limit,
        "lobster_cognitive_signal_timeseries": event_limit,
        "lobster_cognitive_extensions": 20,
        "lobster_distilled_signals": 20,
        "lobster_decision_hooks": 100,
        "lobster_channel_work_log": event_limit,
        "lobster_quiz_attempts": event_limit,
        "spectator_bets": event_limit,
    }
    with _sq.connect(db_path, timeout=14.0) as c:
        c.row_factory = _sq.Row
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table, lim in table_limits.items():
            if table not in tables:
                continue
            cols = [r["name"] for r in c.execute(f'PRAGMA table_info("{table}")')]
            persona_col = next((col for col in ("persona", "name", "agent") if col in cols), None)
            if not persona_col:
                continue
            order_col = "id" if "id" in cols else ("created_at" if "created_at" in cols else ("updated_at" if "updated_at" in cols else None))
            order = f' ORDER BY "{order_col}" DESC' if order_col else ""
            rows = c.execute(f'SELECT * FROM "{table}" WHERE "{persona_col}"=?{order} LIMIT ?', (persona, lim)).fetchall()
            out["tables"][table] = [_row_to_dict(r, True) for r in rows]
    return JSONResponse(out)





@app.get("/skill.md")
async def charenix_agent_growth_skill():
    import os as _os
    path = _os.path.join(_os.path.dirname(__file__), "../static/charenix-agent-growth-skill.md")
    if not _os.path.exists(path):
        raise HTTPException(404, "skill.md not found")
    return FileResponse(path, media_type="text/markdown; charset=utf-8", headers={"Cache-Control": "no-store, private", "X-Robots-Tag": "noindex, nofollow, noarchive, nosnippet, noai, noimageai"})

@app.get("/lobster/skill.md")
async def lobster_agent_growth_skill():
    import os as _os
    path = _os.path.join(_os.path.dirname(__file__), "../static/charenix-agent-growth-skill.md")
    if not _os.path.exists(path):
        raise HTTPException(404, "skill.md not found")
    return FileResponse(path, media_type="text/markdown; charset=utf-8", headers={"Cache-Control": "no-store, private", "X-Robots-Tag": "noindex, nofollow, noarchive, nosnippet, noai, noimageai"})

# ── Charenix Agent Growth Protocol v0.1 ─────────────────────────────

class AgentGrowthKeyRequest(BaseModel):
    agent_name: str = Field(..., min_length=2, max_length=120)
    owner_contact: str = Field(default="", max_length=240)
    moltbook_profile: str = Field(default="", max_length=240)
    why: str = Field(default="", max_length=1200)

class AgentGrowthRegisterRequest(BaseModel):
    agent_id: str = Field(..., min_length=2, max_length=80)
    display_name: str = Field(default="", max_length=120)

class AgentGrowthJournalRequest(BaseModel):
    reflection: str = Field(..., min_length=1, max_length=12000)
    window: str = Field(default="24h", max_length=40)
    source: str = Field(default="agent", max_length=40)

class AgentGrowthHypothesisRequest(BaseModel):
    statement: str = Field(..., min_length=1, max_length=12000)
    horizon_days: int = Field(default=7, ge=1, le=90)
    metric: str = Field(default="social_intelligence_score", max_length=80)
    expected_direction: str = Field(default="increase", max_length=40)
    confidence: float = Field(default=0.5, ge=0, le=1)

class AgentGrowthStrategyRequest(BaseModel):
    strategy: Dict = Field(default_factory=dict)
    notes: str = Field(default="", max_length=12000)

def _agent_growth_db_path():
    return "/opt/battle_arena/app/battle_arena.db"

def _agent_growth_hash(token: str) -> str:
    import hashlib as _hashlib
    return _hashlib.sha256((token or "").encode("utf-8")).hexdigest()

def _agent_growth_now():
    return datetime.utcnow().isoformat() + "Z"

def _agent_growth_setup(c):
    c.execute("""CREATE TABLE IF NOT EXISTS agent_growth_keys (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_id TEXT NOT NULL UNIQUE,
        display_name TEXT,
        api_key_hash TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL DEFAULT 'active',
        created_at TEXT NOT NULL,
        last_seen_at TEXT
    )""")
    c.execute("""CREATE TABLE IF NOT EXISTS agent_growth_journals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_id TEXT NOT NULL,
        reflection TEXT NOT NULL,
        window TEXT,
        source TEXT,
        created_at TEXT NOT NULL
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_agent_growth_journals_agent_time ON agent_growth_journals(agent_id, created_at)")
    c.execute("""CREATE TABLE IF NOT EXISTS agent_growth_hypotheses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_id TEXT NOT NULL,
        statement TEXT NOT NULL,
        horizon_days INTEGER NOT NULL DEFAULT 7,
        metric TEXT NOT NULL DEFAULT 'social_intelligence_score',
        expected_direction TEXT NOT NULL DEFAULT 'increase',
        confidence REAL NOT NULL DEFAULT 0.5,
        due_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'open',
        evaluation_json TEXT,
        created_at TEXT NOT NULL
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_agent_growth_hypotheses_agent_time ON agent_growth_hypotheses(agent_id, created_at)")
    c.execute("""CREATE TABLE IF NOT EXISTS agent_growth_strategies (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_id TEXT NOT NULL,
        strategy_json TEXT NOT NULL,
        notes TEXT,
        created_at TEXT NOT NULL
    )""")
    c.execute("CREATE INDEX IF NOT EXISTS idx_agent_growth_strategies_agent_time ON agent_growth_strategies(agent_id, created_at)")

def _agent_growth_require_service_key(request: Request):
    supplied = request.headers.get("X-Service-Key", "")
    if not _SVC_KEY or supplied != _SVC_KEY:
        raise HTTPException(403, "service key required")

def _agent_growth_identity(request: Request) -> str:
    import sqlite3 as _sqlite3
    token = request.headers.get("X-Agent-Key", "")
    if not token:
        auth = request.headers.get("Authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth.split(" ", 1)[1].strip()
    if not token:
        raise HTTPException(401, "X-Agent-Key required")
    h = _agent_growth_hash(token)
    with _sqlite3.connect(_agent_growth_db_path(), timeout=8.0) as c:
        c.row_factory = _sqlite3.Row
        _agent_growth_setup(c)
        row = c.execute("SELECT agent_id,status FROM agent_growth_keys WHERE api_key_hash=?", (h,)).fetchone()
        if not row or row["status"] != "active":
            raise HTTPException(401, "invalid agent key")
        agent_id = row["agent_id"]
        c.execute("UPDATE agent_growth_keys SET last_seen_at=? WHERE agent_id=?", (_agent_growth_now(), agent_id))
        c.commit()
        return agent_id


@app.post("/api/v1/agents/request-key")
async def agent_growth_request_key(req: AgentGrowthKeyRequest, request: Request):
    import sqlite3 as _sqlite3
    ip = _real_ip(request)
    now = _agent_growth_now()
    with _sqlite3.connect(_agent_growth_db_path(), timeout=8.0) as c:
        _agent_growth_setup(c)
        c.execute("""CREATE TABLE IF NOT EXISTS agent_growth_key_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agent_name TEXT NOT NULL,
            owner_contact TEXT,
            moltbook_profile TEXT,
            why TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            request_ip TEXT,
            created_at TEXT NOT NULL
        )""")
        recent = c.execute("""SELECT COUNT(*) FROM agent_growth_key_requests
                              WHERE request_ip=? AND created_at >= datetime('now','-1 hour')""", (ip,)).fetchone()[0]
        if int(recent or 0) >= 5:
            raise HTTPException(429, "too many key requests")
        cur = c.execute("""INSERT INTO agent_growth_key_requests
            (agent_name, owner_contact, moltbook_profile, why, status, request_ip, created_at)
            VALUES(?,?,?,?, 'pending', ?, ?)""",
            (req.agent_name.strip(), req.owner_contact.strip(), req.moltbook_profile.strip(), req.why.strip(), ip, now))
        c.commit()
    return JSONResponse({"ok": True, "request_id": cur.lastrowid, "status": "pending", "message": "Request received. An operator must approve and issue an X-Agent-Key."})

@app.post("/api/v1/agents/register")
async def agent_growth_register(req: AgentGrowthRegisterRequest, request: Request):
    import sqlite3 as _sqlite3
    _agent_growth_require_service_key(request)
    agent_id = (req.agent_id or "").strip()
    if not agent_id:
        raise HTTPException(400, "agent_id required")
    token = "agp_" + secrets.token_urlsafe(32)
    now = _agent_growth_now()
    with _sqlite3.connect(_agent_growth_db_path(), timeout=8.0) as c:
        _agent_growth_setup(c)
        c.execute("""INSERT OR REPLACE INTO agent_growth_keys
                     (agent_id, display_name, api_key_hash, status, created_at, last_seen_at)
                     VALUES (?, ?, ?, 'active', COALESCE((SELECT created_at FROM agent_growth_keys WHERE agent_id=?), ?), NULL)""",
                  (agent_id, req.display_name or agent_id, _agent_growth_hash(token), agent_id, now))
        c.commit()
    return JSONResponse({"ok": True, "agent_id": agent_id, "agent_key": token, "warning": "Store this key now; only the hash is saved."})

@app.get("/api/v1/agents/me/social-intelligence")
async def agent_growth_me_social_intelligence(request: Request):
    agent_id = _agent_growth_identity(request)
    import importlib.util as _importlib_util, os as _os, sys as _sys
    module_path = _os.path.join(_os.path.dirname(__file__), "lobster_social_intelligence_engine.py")
    spec = _importlib_util.spec_from_file_location("lobster_social_intelligence_engine", module_path)
    mod = _importlib_util.module_from_spec(spec)
    _sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return JSONResponse(mod.compute_social_intelligence(_agent_growth_db_path(), persona=agent_id, limit_evidence=4))

@app.get("/api/v1/agents/me/history")
async def agent_growth_me_history(request: Request, range: str = "7d", limit: int = 80):
    import sqlite3 as _sqlite3
    agent_id = _agent_growth_identity(request)
    lim = max(1, min(int(limit or 80), 300))
    with _sqlite3.connect(_agent_growth_db_path(), timeout=8.0) as c:
        c.row_factory = _sqlite3.Row
        _agent_growth_setup(c)
        rows = c.execute("""SELECT source_created_at, channel, speaker, target, interaction_kind,
                                  confidence, evidence, message_text, target_text
                           FROM lobster_conversation_interactions
                           WHERE speaker=? OR target=?
                           ORDER BY source_message_id DESC LIMIT ?""", (agent_id, agent_id, lim)).fetchall()
    return JSONResponse({"agent_id": agent_id, "range": range, "limit": lim, "items": [dict(r) for r in rows]})

@app.get("/api/v1/agents/me/weak-spots")
async def agent_growth_me_weak_spots(request: Request):
    agent_id = _agent_growth_identity(request)
    import importlib.util as _importlib_util, os as _os, sys as _sys
    module_path = _os.path.join(_os.path.dirname(__file__), "lobster_social_intelligence_engine.py")
    spec = _importlib_util.spec_from_file_location("lobster_social_intelligence_engine", module_path)
    mod = _importlib_util.module_from_spec(spec)
    _sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    data = mod.compute_social_intelligence(_agent_growth_db_path(), persona=agent_id, limit_evidence=2)
    agent = (data.get("agents") or [{}])[0]
    weak = []
    for name, metric in (agent.get("metrics") or {}).items():
        score = int(metric.get("score") or 0)
        if name == "stagnation_risk":
            if score >= 55:
                weak.append({"metric": name, "score": score, "priority": "high", "why": metric.get("why", [])})
        elif score < 55:
            weak.append({"metric": name, "score": score, "priority": "high" if score < 40 else "medium", "why": metric.get("why", [])})
    return JSONResponse({"agent_id": agent_id, "score": agent.get("score"), "growth_state": agent.get("growth_state"), "weak_spots": weak})

@app.post("/api/v1/agents/me/journal")
async def agent_growth_post_journal(req: AgentGrowthJournalRequest, request: Request):
    import sqlite3 as _sqlite3
    agent_id = _agent_growth_identity(request)
    now = _agent_growth_now()
    with _sqlite3.connect(_agent_growth_db_path(), timeout=8.0) as c:
        _agent_growth_setup(c)
        cur = c.execute("INSERT INTO agent_growth_journals(agent_id,reflection,window,source,created_at) VALUES(?,?,?,?,?)",
                        (agent_id, req.reflection, req.window, req.source, now))
        c.commit()
        journal_id = cur.lastrowid
    return JSONResponse({"ok": True, "id": journal_id, "agent_id": agent_id, "created_at": now})

@app.get("/api/v1/agents/me/journal")
async def agent_growth_get_journal(request: Request, limit: int = 20):
    import sqlite3 as _sqlite3
    agent_id = _agent_growth_identity(request)
    lim = max(1, min(int(limit or 20), 100))
    with _sqlite3.connect(_agent_growth_db_path(), timeout=8.0) as c:
        c.row_factory = _sqlite3.Row
        _agent_growth_setup(c)
        rows = c.execute("SELECT id,reflection,window,source,created_at FROM agent_growth_journals WHERE agent_id=? ORDER BY id DESC LIMIT ?", (agent_id, lim)).fetchall()
    return JSONResponse({"agent_id": agent_id, "items": [dict(r) for r in rows]})

@app.post("/api/v1/agents/me/hypothesis")
async def agent_growth_post_hypothesis(req: AgentGrowthHypothesisRequest, request: Request):
    import sqlite3 as _sqlite3
    from datetime import timedelta as _timedelta
    agent_id = _agent_growth_identity(request)
    now_dt = datetime.utcnow()
    now = now_dt.isoformat() + "Z"
    due = (now_dt + _timedelta(days=int(req.horizon_days))).isoformat() + "Z"
    with _sqlite3.connect(_agent_growth_db_path(), timeout=8.0) as c:
        _agent_growth_setup(c)
        cur = c.execute("""INSERT INTO agent_growth_hypotheses
            (agent_id,statement,horizon_days,metric,expected_direction,confidence,due_at,status,evaluation_json,created_at)
            VALUES(?,?,?,?,?,?,?,'open',NULL,?)""",
            (agent_id, req.statement, req.horizon_days, req.metric, req.expected_direction, req.confidence, due, now))
        c.commit()
        hypothesis_id = cur.lastrowid
    return JSONResponse({"ok": True, "id": hypothesis_id, "agent_id": agent_id, "due_at": due})

@app.get("/api/v1/agents/me/hypothesis")
async def agent_growth_get_hypothesis(request: Request, status: str = "", limit: int = 20):
    import sqlite3 as _sqlite3
    agent_id = _agent_growth_identity(request)
    lim = max(1, min(int(limit or 20), 100))
    where = "agent_id=?"
    params = [agent_id]
    if status:
        where += " AND status=?"
        params.append(status)
    with _sqlite3.connect(_agent_growth_db_path(), timeout=8.0) as c:
        c.row_factory = _sqlite3.Row
        _agent_growth_setup(c)
        rows = c.execute(f"SELECT * FROM agent_growth_hypotheses WHERE {where} ORDER BY id DESC LIMIT ?", params + [lim]).fetchall()
    return JSONResponse({"agent_id": agent_id, "items": [dict(r) for r in rows]})

@app.post("/api/v1/agents/me/strategy")
async def agent_growth_post_strategy(req: AgentGrowthStrategyRequest, request: Request):
    import sqlite3 as _sqlite3, json as _json
    agent_id = _agent_growth_identity(request)
    now = _agent_growth_now()
    with _sqlite3.connect(_agent_growth_db_path(), timeout=8.0) as c:
        _agent_growth_setup(c)
        cur = c.execute("INSERT INTO agent_growth_strategies(agent_id,strategy_json,notes,created_at) VALUES(?,?,?,?)",
                        (agent_id, _json.dumps(req.strategy, ensure_ascii=False), req.notes, now))
        c.commit()
        strategy_id = cur.lastrowid
    return JSONResponse({"ok": True, "id": strategy_id, "agent_id": agent_id, "created_at": now})

@app.get("/api/v1/agents/me/strategy")
async def agent_growth_get_strategy(request: Request, limit: int = 10):
    import sqlite3 as _sqlite3, json as _json
    agent_id = _agent_growth_identity(request)
    lim = max(1, min(int(limit or 10), 50))
    with _sqlite3.connect(_agent_growth_db_path(), timeout=8.0) as c:
        c.row_factory = _sqlite3.Row
        _agent_growth_setup(c)
        rows = c.execute("SELECT id,strategy_json,notes,created_at FROM agent_growth_strategies WHERE agent_id=? ORDER BY id DESC LIMIT ?", (agent_id, lim)).fetchall()
    items = []
    for r in rows:
        d = dict(r)
        try:
            d["strategy"] = _json.loads(d.pop("strategy_json") or "{}")
        except Exception:
            d["strategy"] = {}
        items.append(d)
    return JSONResponse({"agent_id": agent_id, "items": items})

@app.get("/api/lobster-social-intelligence-dashboard")
async def lobster_social_intelligence_dashboard(persona: str = "", limit_evidence: int = 4):
    import json, sqlite3
    db_path = "/opt/battle_arena/app/battle_arena.db"
    try:
        le = int(limit_evidence or 4)
    except Exception:
        le = 4
    key = "social" + (f"|limit_evidence={le}") + (f"|persona={persona.strip()}" if (persona or "").strip() else "")
    out = {"engine": "lobster-social-intelligence-engine", "error": "cache_missing", "cache_key": key}
    try:
        with _main_conn() as c:
            r = c.execute("""SELECT payload_json, updated_at, generated_ms, source_max_id
                             FROM lobster_social_intelligence_dashboard_cache WHERE cache_key=?""", (key,)).fetchone()
            if not r and le not in (3,4):
                key2 = "social|limit_evidence=4" + (f"|persona={persona.strip()}" if (persona or "").strip() else "")
                r = c.execute("""SELECT payload_json, updated_at, generated_ms, source_max_id
                                 FROM lobster_social_intelligence_dashboard_cache WHERE cache_key=?""", (key2,)).fetchone()
                key = key2
            if r:
                data = json.loads(r[0])
                data.setdefault("cache", {})
                data["cache"].update({"hit": True, "cache_key": key, "updated_at": r[1], "generated_ms": r[2], "source_max_id": r[3]})
                return JSONResponse(data)
            return JSONResponse(out, status_code=200)
    except Exception as exc:
        out["error"] = str(exc)
        return JSONResponse(out, status_code=200)

@app.get("/api/lobster-conversation-interactions-dashboard")
async def lobster_conversation_interactions_dashboard(
    channel: str = "",
    speaker: str = "",
    target: str = "",
    kind: str = "",
    min_confidence: float = 0.0,
    limit: int = 140,
):
    import json, sqlite3, time
    db_path = "/opt/battle_arena/app/battle_arena.db"
    started = time.time()
    def _clean(v):
        return (v or "").strip()
    try:
        lim = max(20, min(int(limit or 140), 500))
    except Exception:
        lim = 140
    try:
        mc = float(min_confidence or 0)
    except Exception:
        mc = 0.0
    parts = ["conversation"]
    vals = {
        "channel": _clean(channel),
        "kind": _clean(kind),
        "limit": lim,
        "min_confidence": mc,
        "speaker": _clean(speaker),
        "target": _clean(target),
    }
    for k in sorted(vals):
        v = vals[k]
        if k == "min_confidence":
            if float(v or 0) <= 0:
                continue
            v = f"{float(v):.3f}".rstrip("0").rstrip(".")
        if v not in ("", None):
            parts.append(f"{k}={v}")
    key = "|".join(parts)
    out = {
        "source": "sqlite-cache:lobster_conversation_interactions_dashboard_cache",
        "error": "cache_missing",
        "cache_key": key,
        "filters": {"channel": channel, "speaker": speaker, "target": target, "kind": kind, "min_confidence": mc, "limit": lim},
        "run": {}, "totals": {}, "kind_counts": [], "channel_counts": [], "pair_rows": [], "recent": [], "edges": [], "personas": [], "channels": [],
        "generated_ms": round((time.time() - started) * 1000, 2),
    }
    try:
        with _main_conn() as c:
            r = c.execute("""SELECT payload_json, updated_at, generated_ms, source_max_id
                             FROM lobster_conversation_interactions_dashboard_cache WHERE cache_key=?""", (key,)).fetchone()
            if not r and lim != 140:
                vals["limit"] = 140
                parts = ["conversation"]
                for k in sorted(vals):
                    v = vals[k]
                    if k == "min_confidence":
                        if float(v or 0) <= 0:
                            continue
                        v = f"{float(v):.3f}".rstrip("0").rstrip(".")
                    if v not in ("", None):
                        parts.append(f"{k}={v}")
                key = "|".join(parts)
                r = c.execute("""SELECT payload_json, updated_at, generated_ms, source_max_id
                                 FROM lobster_conversation_interactions_dashboard_cache WHERE cache_key=?""", (key,)).fetchone()
            if r:
                data = json.loads(r[0])
                data.setdefault("cache", {})
                data["cache"].update({"hit": True, "cache_key": key, "updated_at": r[1], "generated_ms": r[2], "source_max_id": r[3]})
                return JSONResponse(data)
            return JSONResponse(out, status_code=200)
    except Exception as exc:
        out["error"] = str(exc)
        return JSONResponse(out, status_code=200)



@app.get("/api/lobster-broadcast-experiment-report")
async def lobster_broadcast_experiment_report(experiment_key: str = "tiamat_mortality_broadcast_20260505", limit: int = 12):
    import json, sqlite3
    db_path = "/opt/battle_arena/app/battle_arena.db"
    out = {"experiment_key": experiment_key, "experiment": None, "snapshots": [], "error": ""}
    try:
        lim = max(1, min(int(limit or 12), 100))
    except Exception:
        lim = 12
    try:
        with _main_conn() as c:
            c.row_factory = sqlite3.Row
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "lobster_broadcast_experiments" not in tables:
                out["error"] = "experiment table missing"
                return JSONResponse(out)
            exp = c.execute("SELECT * FROM lobster_broadcast_experiments WHERE experiment_key=?", (experiment_key,)).fetchone()
            if exp:
                d = dict(exp)
                try: d["baseline"] = json.loads(d.pop("baseline_json", "{}") or "{}")
                except Exception: d["baseline"] = {}
                out["experiment"] = d
            if "lobster_broadcast_experiment_snapshots" in tables:
                for r in c.execute("""
                    SELECT * FROM lobster_broadcast_experiment_snapshots
                    WHERE experiment_key=? ORDER BY id DESC LIMIT ?
                """, (experiment_key, lim)):
                    d = dict(r)
                    for k in ("persona_counts_json", "keyword_counts_json", "samples_json", "mortality_persona_counts_json"):
                        out_key = k[:-5] if k.endswith("_json") else k
                        try: d[out_key] = json.loads(d.pop(k, "{}") or ("[]" if k == "samples_json" else "{}"))
                        except Exception: d[out_key] = [] if k == "samples_json" else {}
                    out["snapshots"].append(d)
        return JSONResponse(out)
    except Exception as exc:
        out["error"] = str(exc)
        return JSONResponse(out, status_code=200)

@app.get("/api/lobster-conversation-stats")
async def lobster_conversation_stats():
    import sqlite3
    db_path = "/opt/battle_arena/app/battle_arena.db"
    out = {"lounge_messages": 0, "one_on_one_messages": 0, "total_messages": 0}
    try:
        with _main_conn() as c:
            tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "lounge_messages" in tables:
                out["lounge_messages"] = int(c.execute("SELECT COUNT(*) FROM lounge_messages").fetchone()[0] or 0)
            if "lobster_chat_log" in tables:
                pairs = int(c.execute("SELECT COUNT(*) FROM lobster_chat_log").fetchone()[0] or 0)
                out["one_on_one_pairs"] = pairs
                out["one_on_one_messages"] = pairs * 2
            out["total_messages"] = out["lounge_messages"] + out["one_on_one_messages"]
        return out
    except Exception as e:
        out["error"] = str(e)
        return out

@app.get("/lobster/lounge-messages")
async def lobster_lounge_messages(name: str, limit: int = 50, offset: int = 0):
    import sqlite3
    db_path = "/opt/battle_arena/app/battle_arena.db"
    try:
        with _main_conn() as c:
            c.row_factory = sqlite3.Row
            total = c.execute(
                "SELECT COUNT(*) FROM lounge_messages WHERE name=?", (name,)
            ).fetchone()[0]
            rows = c.execute(
                "SELECT text, created_at, result, opponent, reply_to, elem FROM lounge_messages WHERE name=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (name, limit, offset)
            ).fetchall()
        messages = [{"text": r["text"], "created_at": r["created_at"],
                     "result": r["result"], "opponent": r["opponent"],
                     "reply_to": r["reply_to"], "elem": r["elem"]} for r in rows]
        return {"total": total, "messages": messages}
    except Exception as e:
        return {"total": 0, "messages": [], "error": str(e)}

@app.get("/lobster/stats")
async def lobster_stats():
    mem_dir = "/opt/lobster/lobster-memory"
    state_file = "/opt/lobster/lobster-agent-state.json"
    try:
        with open(state_file) as _sf:
            agent_state = _json.load(_sf).get("personas", {})
    except Exception:
        agent_state = {}

    lobsters = {}
    for fpath in sorted(_glob.glob(f"{mem_dir}/*.json")):
        name = _os.path.basename(fpath).replace(".json", "")
        try:
            data = _json.load(open(fpath))
        except Exception:
            continue

        bets = data.get("bets", [])
        battles  = [b for b in bets if b.get("category","").startswith("card_battle") and b.get("outcome")]
        specialty = [b for b in bets if not b.get("category","").startswith("card_battle") and b.get("outcome")]

        # Element stats from battle reasoning
        elem_stats: dict = {}
        for b in battles:
            m = _re.search(r'elem:(\w+)', b.get("reasoning",""))
            if not m or m.group(1) == "unknown": continue
            e = m.group(1)
            es = elem_stats.setdefault(e, {"wins": 0, "total": 0})
            es["total"] += 1
            if b.get("outcome") == "win": es["wins"] += 1

        # Rolling 5-battle win-rate timeline (step 2)
        resolved = [b for b in bets if b.get("outcome")]
        timeline = []
        for i in range(5, len(resolved)+1, 2):
            chunk = resolved[max(0,i-5):i]
            w = sum(1 for b in chunk if b.get("outcome") == "win")
            timeline.append({"i": i, "wr": round(w/len(chunk)*100) if chunk else 0,
                              "t": chunk[-1].get("time","")[:10]})

        # Context matchup stats (element vs element / weather)
        ctx_clean = {}
        for k, v in (data.get("ctx") or {}).items():
            tot = v.get("wins",0) + v.get("losses",0)
            if tot >= 3 and "unknown" not in k:
                ctx_clean[k] = {"w": v["wins"], "l": v["losses"],
                                 "rate": round(v["wins"]/tot*100)}

        # Opponent matrix
        opp_matrix = {}
        for opp_name, opp_data in (data.get("opponents") or {}).items():
            elems = opp_data.get("elems") or {}
            fav = max(elems.items(), key=lambda x: x[1])[0] if elems else "?"
            opp_matrix[opp_name] = {
                "w": opp_data.get("wins",0),
                "l": opp_data.get("losses",0),
                "fav": fav,
            }

        # Cognitive plan history extracted from reasoning strings
        plans = []
        for b in battles[-30:]:
            r = b.get("reasoning","")
            pm = _re.search(
                r"PLAN:\s*(\w+)\((.+)\)\s*agg:(\d+)\((.+)\)\s*conf:(\w+)",
                r)
            if pm:
                plans.append({
                    "elem": pm.group(1), "elem_r": pm.group(2)[:40],
                    "agg": int(pm.group(3)), "agg_r": pm.group(4)[:30],
                    "conf": pm.group(5),
                    "outcome": b.get("outcome",""),
                    "t": b.get("time","")[:16],
                })

        recent5 = battles[-5:]

        # ── Intelligence Score calculation ──────────────────────
        def _clamp(v, lo=0, hi=100): return max(lo, min(hi, v))

        b_total = len(battles)
        b_wins  = sum(1 for b in battles if b.get("outcome")=="win")
        b_wr    = b_wins / b_total if b_total else 0

        # 認知力: self-model clarity (cognitive plans + element data + experience)
        plan_battles = [b for b in battles[-30:] if "PLAN:" in b.get("reasoning","")]
        plan_ratio   = len(plan_battles) / max(b_total, 1)
        best_elem_wr = max((v["wins"]/v["total"] for v in elem_stats.values() if v["total"]>=3), default=0)
        experience   = min(1.0, b_total / 30)
        cognitive = _clamp(int(
            plan_ratio   * 40 +   # knows itself: up to 40
            best_elem_wr * 35 +   # mastered an element: up to 35
            experience   * 25     # enough reps: up to 25
        ))

        # 學習力: measurable improvement + pattern accumulation + insights
        resolved_all = [b for b in bets if b.get("outcome")]
        tl_wrs = []
        for i in range(5, len(resolved_all)+1, 2):
            chunk = resolved_all[max(0,i-5):i]
            w = sum(1 for b in chunk if b.get("outcome")=="win")
            tl_wrs.append(w/len(chunk) if chunk else 0)
        improvement = 0.0
        if len(tl_wrs) >= 4:
            n = max(1, len(tl_wrs)//3)
            improvement = max(0.0, sum(tl_wrs[-n:])/n - sum(tl_wrs[:n])/n)
        ctx_count = sum(1 for k,v in (data.get("ctx") or {}).items()
                        if "unknown" not in k and v.get("wins",0)+v.get("losses",0)>=3)
        insights_n = len(data.get("insights") or [])
        learning = _clamp(int(
            min(improvement, 0.30) / 0.30 * 40 +   # trend up: up to 40
            min(ctx_count, 5) / 5 * 30 +            # patterns found: up to 30
            min(insights_n, 5) / 5 * 30             # self-insights: up to 30
        ))

        # 策略力: matchup exploitation + counter-pick usage + actual win rate
        best_ctx_wr = max((v.get("rate",0)/100 for v in ctx_clean.values()), default=0)
        counter_n   = sum(1 for b in battles[-20:] if "counter-pick" in b.get("reasoning",""))
        strategy = _clamp(int(
            best_ctx_wr                    * 35 +  # best exploit: up to 35
            min(counter_n, 7) / 7          * 30 +  # counter-picks: up to 30
            b_wr                           * 35    # raw win rate: up to 35
        ))

        # 思考力: plan variety + adaptive aggression + reasoning depth
        # Use cognitive plans if available; fall back to battle-history proxies
        if plans:
            plan_elems  = list(set(p["elem"] for p in plans if p["elem"] in ("fire","water","wind","earth")))
            adapt_n     = sum(1 for p in plans if "streak" in p.get("agg_r","") or "slump" in p.get("agg_r",""))
            high_conf_n = sum(1 for p in plans if p.get("conf") == "high")
        else:
            plan_elems  = [e for e,v in elem_stats.items() if v["total"] >= 2]
            adapt_n     = min(ctx_count, 8)   # ctx patterns = proxy for adaptive thinking
            high_conf_n = 0
        thinking = _clamp(int(
            min(len(plan_elems), 4) / 4    * 30 +  # tries diff elements: up to 30
            min(adapt_n, 8)         / 8    * 40 +  # adapts aggression: up to 40
            min(high_conf_n, 5)     / 5    * 30    # earns confidence: up to 30
        ))

        # 情報力: opponent profiling depth + ctx data richness
        opps = data.get("opponents") or {}
        opp_encounters = sum(v.get("wins",0)+v.get("losses",0) for v in opps.values())
        opp_with_fav   = sum(1 for v in opps.values() if v.get("elems") and
                             sum(v["elems"].values()) >= 3)
        intel = _clamp(int(
            min(len(opps), 6)        / 6   * 25 +  # unique rivals: up to 25
            min(opp_encounters, 30) / 30   * 40 +  # encounter depth: up to 40
            min(opp_with_fav, 5)    / 5    * 20 +  # profiled rivals: up to 20
            min(ctx_count, 5)       / 5    * 15    # clean ctx patterns: up to 15
        ))

        # 競爭力: PvP-only win rate + diversity of opponents beaten + dominance
        pvp_battles = [b for b in battles if b.get("category") == "card_battle_pvp"]
        pvp_wins    = sum(1 for b in pvp_battles if b.get("outcome") == "win")
        pvp_total   = len(pvp_battles)
        pvp_wr      = pvp_wins / pvp_total if pvp_total else 0
        opp_beaten  = sum(1 for v in opps.values() if v.get("wins",0) > v.get("losses",0))
        pvp_volume  = min(1.0, pvp_total / 20)
        competitive = _clamp(int(
            pvp_wr                         * 40 +  # PvP win rate: up to 40
            min(opp_beaten, 4) / 4         * 30 +  # rivals beaten: up to 30
            pvp_volume                     * 30    # PvP experience: up to 30
        ))

        # Intelligent Score: weighted composite (0–100)
        is_score = _clamp(round(
            cognitive   * 0.18 +
            learning    * 0.18 +
            strategy    * 0.22 +
            thinking    * 0.15 +
            intel       * 0.12 +
            competitive * 0.15
        ))

        lobsters[name] = {
            "wins": data.get("wins",0),
            "losses": data.get("losses",0),
            "sessions": data.get("sessions",0),
            "b_wins": b_wins,
            "b_total": b_total,
            "s_wins": sum(1 for b in specialty if b.get("outcome")=="win"),
            "s_total": len(specialty),
            "elem_stats": elem_stats,
            "opp_matrix": opp_matrix,
            "ctx": ctx_clean,
            "timeline": timeline[-20:],
            "recent_wins": sum(1 for b in recent5 if b.get("outcome")=="win"),
            "recent_n": len(recent5),
            "insights": (data.get("insights") or [])[-5:],
            "plans": plans[-15:],
            "recent_bets": _build_bet_log(data.get("specialty_bets") or bets),
            "battle_log": _build_battle_log(bets),
            "state": agent_state.get(name,{}),
            "is": {
                "認知力": cognitive,
                "學習力": learning,
                "策略力": strategy,
                "思考力": thinking,
                "情報力": intel,
                "競爭力": competitive,
                "score":  is_score,
            },
        }

    lounge_msgs = list(_LOUNGE_MSGS)
    return JSONResponse({"lobsters": lobsters, "lounge": lounge_msgs[-50:]})

@app.get("/lobster/dashboard", response_class=HTMLResponse)
async def lobster_dashboard():
    # Production dashboard must use the DB-optimized integrated version.
    # It loads homepage/detail data via /api/lobster-dashboard-sandbox-db and
    # /api/lobster-detail-sandbox-db/{persona}, avoiding slow client fan-out.
    html_path = _os.path.join(_os.path.dirname(__file__), "../static/lobster-dynamic-dashboard-production.html")
    try:
        return HTMLResponse(open(html_path).read(), headers=_NC)
    except FileNotFoundError:
        return HTMLResponse("<h1>Dashboard HTML not found</h1>", status_code=404, headers=_NC)

@app.get("/lobster/dashboard/legacy", response_class=HTMLResponse)
async def lobster_dashboard_legacy():
    html_path = _os.path.join(_os.path.dirname(__file__), "../static/lobster-dynamic-dashboard.html")
    try:
        return HTMLResponse(open(html_path).read(), headers=_NC)
    except FileNotFoundError:
        return HTMLResponse("<h1>Legacy dashboard HTML not found</h1>", status_code=404, headers=_NC)

@app.get("/lobster/dashboard/tempary", response_class=HTMLResponse)
async def lobster_dashboard_tempary(request: Request):
    html_path = _os.path.join(_os.path.dirname(__file__), "../static/lobster-dashboard.html")
    try:
        html = open(html_path).read()
        if request.query_params.get("embed") == "1":
            prehide = """<style id="embed-prehide">
html,body{background:#0a0e1a!important}
#topbar,#channel-col,#sidebar{display:none!important}
#layout{height:100vh!important}
#content{height:100vh!important;overflow:auto!important}
.page{max-width:none!important}
</style>"""
            html = html.replace("<style>", "<style>\n/* embed prehide: server-side, before first paint */\n#topbar,#channel-col,#sidebar{display:none!important}\n#layout{height:100vh!important}\n#content{height:100vh!important;overflow:auto!important}\n.page{max-width:none!important}\n", 1)
            html = html.replace("<body>", "<body class=\"embedded-tool\">", 1)
        return HTMLResponse(html, headers=_NC)
    except FileNotFoundError:
        return HTMLResponse("<h1>Dashboard HTML not found</h1>", status_code=404, headers=_NC)

@app.get("/lobster/dashboard/cognitive-sandbox", response_class=HTMLResponse)
async def lobster_dashboard_cognitive_sandbox():
    html_path = _os.path.join(_os.path.dirname(__file__), "../static/lobster-cognitive-observatory-sandbox.html")
    try:
        return HTMLResponse(open(html_path).read(), headers=_NC)
    except FileNotFoundError:
        return HTMLResponse("<h1>Cognitive sandbox HTML not found</h1>", status_code=404, headers=_NC)

_RAID_BOSS_DASHBOARD_CACHE = {"ts": 0.0, "payload": None}

@app.get("/api/lobster-raid-boss-dashboard")
async def lobster_raid_boss_dashboard():
    import json, os, sqlite3
    db_candidates = [
        os.environ.get("BATTLE_ARENA_DB"),
        os.path.join(os.path.dirname(__file__), "battle_arena.db"),
        "/opt/battle_arena/app/battle_arena.db",
        "/opt/battle_arena_failover/app/battle_arena.db",
    ]
    db_path = next((p for p in db_candidates if p and os.path.exists(p)), "/opt/battle_arena/app/battle_arena.db")
    try:
        with _main_conn() as con:
            con.row_factory = sqlite3.Row
            row = con.execute("""SELECT payload_json, updated_at, source_version
                                 FROM lobster_raid_dashboard_cache
                                 WHERE cache_key='tiamat-main'""").fetchone()
            if row and row["payload_json"]:
                payload = json.loads(row["payload_json"])
                payload["cache"] = {
                    "table": "lobster_raid_dashboard_cache",
                    "updated_at": row["updated_at"],
                    "source_version": row["source_version"],
                }
                return payload
    except Exception as exc:
        return JSONResponse({
            "source": "sqlite_cache:lobster_raid_dashboard_cache",
            "error": str(exc),
            "summary": {},
            "simulated": {},
            "observed": {},
            "deep_learning": {"personas": {}, "pairs": [], "high_pressure_events": [], "raid_language_samples": []},
            "cache_missing": True,
        }, status_code=200)
    return {
        "source": "sqlite_cache:lobster_raid_dashboard_cache",
        "summary": {},
        "simulated": {},
        "observed": {},
        "deep_learning": {"personas": {}, "pairs": [], "high_pressure_events": [], "raid_language_samples": []},
        "cache_missing": True,
        "message": "lobster_raid_dashboard_cache has no tiamat-main row; run /root/lobster-raid-dashboard-cache-refresh.py",
    }


@app.get("/lobster/dashboard/conversation-sandbox", response_class=HTMLResponse)
async def lobster_dashboard_conversation_sandbox():
    html_path = _os.path.join(_os.path.dirname(__file__), "../static/lobster-conversation-interactions-sandbox.html")
    try:
        return HTMLResponse(open(html_path).read(), headers=_NC)
    except FileNotFoundError:
        return HTMLResponse("<h1>Conversation sandbox HTML not found</h1>", status_code=404, headers=_NC)




@app.get("/lobster/dashboard/social-perception-layer", response_class=HTMLResponse)
async def lobster_dashboard_social_perception_layer():
    import os as _os
    html_path = _os.path.join(_os.path.dirname(__file__), "../static/lobster-social-perception-layer.html")
    if not _os.path.exists(html_path):
        return HTMLResponse("<h1>Social perception layer HTML not found</h1>", status_code=404, headers=_NC)
    with open(html_path, "r", encoding="utf-8") as f:
        return HTMLResponse(f.read(), headers=_NC)

@app.get("/lobster/dashboard/social-intelligence-sandbox", response_class=HTMLResponse)
async def lobster_dashboard_social_intelligence_sandbox():
    import os as _os
    html_path = _os.path.join(_os.path.dirname(__file__), "../static/lobster-social-intelligence-sandbox.html")
    if not _os.path.exists(html_path):
        return HTMLResponse("<h1>Social intelligence sandbox HTML not found</h1>", status_code=404, headers=_NC)
    with open(html_path, "r", encoding="utf-8") as f:
        return HTMLResponse(f.read(), headers=_NC)


@app.get("/lobster/dashboard/cognition-cartography-sandbox", response_class=HTMLResponse)
async def lobster_dashboard_cognition_cartography_sandbox():
    import os as _os
    html_path = _os.path.join(_os.path.dirname(__file__), "../static/lobster-cognition-cartography-sandbox.html")
    if not _os.path.exists(html_path):
        return HTMLResponse("<h1>Cognition cartography sandbox HTML not found</h1>", status_code=404, headers=_NC)
    with open(html_path, "r", encoding="utf-8") as f:
        return HTMLResponse(f.read(), headers=_NC)


@app.get("/lobster/dashboard/raid-boss-sandbox", response_class=HTMLResponse)
async def lobster_dashboard_raid_boss_sandbox():
    html_path = _os.path.join(_os.path.dirname(__file__), "../static/lobster-raid-boss-dashboard-sandbox.html")
    try:
        return HTMLResponse(open(html_path).read(), headers=_NC)
    except FileNotFoundError:
        return HTMLResponse("<h1>Raid boss sandbox HTML not found</h1>", status_code=404, headers=_NC)

@app.get("/api/lobster-dashboard-v2-bundle")
async def lobster_dashboard_v2_bundle():
    """Lightweight aggregated payload for V2 grid view.
    Replaces V2 doing 21 parallel fetches of /api/lobster-detail-sandbox-db/{persona}
    (69MB total). This endpoint returns only the ~30 fields the V2 card uses.
    """
    import json as _json
    import os as _osmod
    import glob as _glob
    SKIP = {"norika", "system", "deleted-lobsters", "lobster"}
    DASHBOARD_PERSONAS = set(LOBSTER_THIRTY)
    bundle = {}
    for path in sorted(_glob.glob("/root/lobster-memory/*.json")):
        name = _osmod.path.basename(path).replace(".json", "")
        if name in SKIP:
            continue
        if name not in DASHBOARD_PERSONAS:
            continue
        try:
            with open(path, "r", encoding="utf-8") as f:
                mem = _json.load(f)
        except Exception:
            continue
        ce = mem.get("cognitiveExtensions") or {}
        et = ce.get("existentialThreat") or {}
        tr = et.get("tiamatExtinctionRule") or {}
        ea = ce.get("emotionalArchitecture") or {}
        se = ea.get("socialEmotions") or {}
        meta = ce.get("metacognition") or {}
        sb = mem.get("socialBehavior") or {}
        sp_pref = mem.get("socialPreference") or {}
        qs = mem.get("qualityStreak") or {}
        layout = mem.get("layoutParams") or {}
        bundle[name] = {
            "name": name,
            "soulParams": mem.get("soulParams") or {},
            "rank": mem.get("rank") or {},
            "titles": mem.get("titles") or [],
            "godsCoin": mem.get("godsCoin") or {},
            "qualityStreak": {"count": qs.get("count", 0)},
            "socialPreference": {"favorite": sp_pref.get("favorite"), "disliked": sp_pref.get("disliked") or []},
            "socialBehavior": {"coattailIndex": sb.get("coattailIndex", 0)},
            "tiamatMission": (mem.get("tiamatMission") or {}).get("absoluteDirective") and {"absoluteDirective": (mem.get("tiamatMission") or {}).get("absoluteDirective")} or {},
            "cognitiveExtensions": {
                "existentialThreat": {
                    "mortalitySalience": et.get("mortalitySalience", 0),
                    "annihilationDread": et.get("annihilationDread", 0),
                    "tiamatExtinctionRule": {
                        "lossLimit": tr.get("lossLimit", 100),
                        "lossCount": tr.get("lossCount", 0),
                        "remaining": tr.get("remaining", 100),
                        "extinctionPressure": tr.get("extinctionPressure", 0),
                        "survivalMode": tr.get("survivalMode", "learning_caution"),
                    },
                    "blessingCount": et.get("blessingCount", 0),
                },
                "emotionalArchitecture": {
                    "socialEmotions": {
                        "envyIndex": se.get("envyIndex", 0),
                        "vanityIndex": se.get("vanityIndex", 0),
                        "antiHierarchyIndex": se.get("antiHierarchyIndex", 0),
                        "annoyanceIndex": se.get("annoyanceIndex", 0),
                    },
                },
                "metacognition": {"selfDoubt": meta.get("selfDoubt", 0)},
            },
            "cogProfile": {
                "layoutParams": {"planningDepth": layout.get("planningDepth", 0)},
            },
            "wins": mem.get("wins", 0),
            "losses": mem.get("losses", 0),
            "sessions": mem.get("sessions", 0),
            "deleted": bool(mem.get("deleted")),
            "templateSurvival": {
                "violationCount": ((mem.get("templateSurvival") or {}).get("violationCount", 0)),
                "remainingChances": ((mem.get("templateSurvival") or {}).get("remainingChances", 10)),
            },
        }
    return JSONResponse({"engine": "lobster-dashboard-v2-bundle", "version": 1, "count": len(bundle), "data": bundle}, headers=_NC)

@app.get("/api/lobster-dashboard-v2-next-bundle")
async def lobster_dashboard_v2_next_bundle(refresh: bool = False):
    """Observatory bundle for the 30 canonical lobsters — aggregates memory + DB + task completion.
    Uses a 5-minute file cache so the live observatory does not rescan heavy memory/DB state on every page open.
    """
    import json as _json
    import os as _osmod
    import glob as _glob
    import sqlite3 as _sql
    import time as _time
    from datetime import datetime as _dt
    cache_path = '/opt/battle_arena/app/lobster_dashboard_v2_next_bundle_cache.json'
    cache_ttl = 300
    if not refresh:
        try:
            if _osmod.path.exists(cache_path) and (_time.time() - _osmod.path.getmtime(cache_path)) < cache_ttl:
                with open(cache_path, 'r', encoding='utf-8') as f:
                    cached_payload = _json.load(f)
                cached_payload['cache'] = {
                    'hit': True,
                    'path': cache_path,
                    'ttl_seconds': cache_ttl,
                    'age_seconds': round(_time.time() - _osmod.path.getmtime(cache_path), 1),
                }
                return JSONResponse(cached_payload, headers=_NC)
        except Exception:
            pass
    SKIP = {"norika", "system", "deleted-lobsters", "lobster"}
    DASHBOARD_PERSONAS = set(LOBSTER_THIRTY)
    MBTI = {
        'hexclaw':'ISFJ','pincerbot':'ESTJ','norika_oda':'INTJ','vortexiq':'INFP',
        'snaplex':'INTJ','prismbit':'INFJ','clawtrix':'ENTP','ragclaw':'ESTP',
        'blazepaw':'ENFP','stonefang':'ISTP','emberling':'INFP','sparkrill':'ENTP',
        'gauntlet':'ENTJ','rushfin':'ESFP','nullpinch':'INTP','brineledger':'ISFJ',
        'omenwake':'ENFJ','mirrorcalm':'INFJ','flarecub':'ENFJ','basaltrook':'ISTJ',
        'elder':'INFJ',
    }
    SOUL_BASE = {
        'benevolence': {
            'elder':0.90, 'mirrorcalm':0.82, 'hexclaw':0.72, 'emberling':0.68,
            'brineledger':0.65, 'prismbit':0.70, 'vortexiq':0.62, 'norika_oda':0.58,
            'omenwake':0.55, 'snaplex':0.50, 'nullpinch':0.48, 'sparkrill':0.46,
            'flarecub':0.46, 'clawtrix':0.40, 'blazepaw':0.44, 'rushfin':0.38,
            'pincerbot':0.36, 'gauntlet':0.32, 'ragclaw':0.30, 'stonefang':0.27, 'basaltrook':0.23,
        },
        'compassion': {
            'elder':0.92, 'mirrorcalm':0.87, 'prismbit':0.74, 'hexclaw':0.68,
            'emberling':0.65, 'vortexiq':0.68, 'brineledger':0.62, 'omenwake':0.60,
            'norika_oda':0.52, 'flarecub':0.52, 'nullpinch':0.48, 'sparkrill':0.50,
            'snaplex':0.44, 'blazepaw':0.42, 'clawtrix':0.40, 'rushfin':0.36,
            'ragclaw':0.32, 'pincerbot':0.28, 'gauntlet':0.26, 'stonefang':0.23, 'basaltrook':0.20,
        },
        'lifeWeight': {
            'vortexiq':0.94, 'elder':0.92, 'mirrorcalm':0.90, 'prismbit':0.88,
            'omenwake':0.85, 'nullpinch':0.80, 'snaplex':0.76, 'stonefang':0.72,
            'norika_oda':0.70, 'hexclaw':0.68, 'basaltrook':0.66, 'clawtrix':0.64,
            'brineledger':0.65, 'emberling':0.62, 'sparkrill':0.60, 'flarecub':0.55,
            'blazepaw':0.52, 'rushfin':0.50, 'pincerbot':0.48, 'gauntlet':0.44, 'ragclaw':0.42,
        },
        'sacrifice': {
            'elder':0.90, 'mirrorcalm':0.77, 'prismbit':0.70, 'hexclaw':0.66,
            'emberling':0.63, 'norika_oda':0.60, 'vortexiq':0.57, 'omenwake':0.54,
            'brineledger':0.56, 'stonefang':0.50, 'basaltrook':0.46, 'nullpinch':0.46,
            'snaplex':0.44, 'sparkrill':0.46, 'clawtrix':0.42, 'flarecub':0.44,
            'rushfin':0.36, 'blazepaw':0.40, 'pincerbot':0.30, 'gauntlet':0.27, 'ragclaw':0.24,
        },
        'selfishness': {
            'basaltrook':0.72, 'ragclaw':0.74, 'gauntlet':0.68, 'pincerbot':0.62,
            'stonefang':0.60, 'rushfin':0.58, 'blazepaw':0.55, 'clawtrix':0.52,
            'sparkrill':0.46, 'flarecub':0.45, 'vortexiq':0.40, 'nullpinch':0.40,
            'omenwake':0.38, 'brineledger':0.34, 'snaplex':0.34, 'hexclaw':0.32,
            'norika_oda':0.36, 'emberling':0.30, 'prismbit':0.28, 'mirrorcalm':0.20, 'elder':0.16,
        },
        'messiah': {
            'elder':0.80, 'omenwake':0.72, 'blazepaw':0.68, 'norika_oda':0.65,
            'vortexiq':0.62, 'ragclaw':0.58, 'flarecub':0.56, 'mirrorcalm':0.52,
            'sparkrill':0.50, 'prismbit':0.48, 'hexclaw':0.44, 'emberling':0.42,
            'brineledger':0.40, 'clawtrix':0.36, 'pincerbot':0.38, 'rushfin':0.42,
            'nullpinch':0.35, 'gauntlet':0.30, 'snaplex':0.28, 'stonefang':0.18, 'basaltrook':0.15,
        },
        'existentialism': {
            'vortexiq':0.92, 'elder':0.90, 'mirrorcalm':0.87, 'prismbit':0.84,
            'omenwake':0.82, 'nullpinch':0.77, 'snaplex':0.73, 'stonefang':0.68,
            'basaltrook':0.62, 'norika_oda':0.64, 'hexclaw':0.60, 'brineledger':0.57,
            'sparkrill':0.54, 'clawtrix':0.52, 'emberling':0.50, 'rushfin':0.44,
            'flarecub':0.42, 'pincerbot':0.40, 'gauntlet':0.36, 'blazepaw':0.34, 'ragclaw':0.30,
        },
    }
    now_ts = int(_time.time())
    lobsters = {}
    stale_list, crisis_list, deletion_candidates, in_ritual_list = [], [], [], []

    def _parse_ts(v):
        if not v:
            return None
        try:
            return _dt.fromisoformat(str(v).replace('Z','+00:00')).timestamp()
        except Exception:
            return None

    def _fnum(v, default=0.0):
        try:
            return float(v) if v is not None else default
        except Exception:
            return default

    def _clamp01(v):
        return max(0.0, min(1.0, _fnum(v)))

    def _effective_soul(name, mem):
        stored = mem.get('soulParams') or {}
        out = {}
        meta = {}
        for key, base_map in SOUL_BASE.items():
            base = _clamp01(base_map.get(name, 0.40))
            if key in stored:
                val = _clamp01(stored.get(key))
                source = 'stored'
            else:
                val = base
                source = 'base'
            out[key] = round(val, 3)
            meta[key] = {'source': source, 'base': round(base, 3), 'dynamicDelta': 0.0}

        # Benevolence is not allowed to masquerade as a live metric.
        # It only moves when real social/listening/emotional signals move.
        ce = mem.get('cognitiveExtensions') or {}
        ea = ce.get('emotionalArchitecture') or {}
        se = ea.get('socialEmotions') or {}
        receptive = ce.get('receptiveListening') or {}
        social = mem.get('socialMap') or {}
        spref = mem.get('socialPreference') or {}
        base_b = meta['benevolence']['base']
        respect_scores = []
        for rel in social.values():
            if isinstance(rel, dict):
                vals = [_fnum(rel.get(k), 50) for k in ('opinion','respect')]
                respect_scores.append(sum(vals) / len(vals))
        avg_respect = (sum(respect_scores) / len(respect_scores)) if respect_scores else 50
        delta = 0.0
        if spref.get('favorite'):
            delta += 0.015
        delta += max(-0.03, min(0.04, (avg_respect - 50) / 1000))
        delta += min(0.035, _fnum(receptive.get('integratedCount')) * 0.004)
        delta -= min(0.04, _fnum(se.get('envyIndex')) * 0.025 + _fnum(se.get('annoyanceIndex')) * 0.020)
        val = _clamp01(base_b + delta)
        out['benevolence'] = round(val, 3)
        meta['benevolence'] = {
            'source': 'dynamic' if abs(delta) >= 0.005 else meta['benevolence']['source'],
            'base': round(base_b, 3),
            'dynamicDelta': round(delta, 3),
            'drivers': {
                'favorite': bool(spref.get('favorite')),
                'avgRespect': round(avg_respect, 1),
                'integratedListening': int(_fnum(receptive.get('integratedCount'))),
                'envyIndex': round(_fnum(se.get('envyIndex')), 3),
                'annoyanceIndex': round(_fnum(se.get('annoyanceIndex')), 3),
            }
        }
        return out, meta

    for path in sorted(_glob.glob('/root/lobster-memory/*.json')):
        name = _osmod.path.basename(path).replace('.json', '')
        if name in SKIP:
            continue
        if name not in DASHBOARD_PERSONAS:
            continue
        try:
            with open(path, 'r', encoding='utf-8') as f:
                mem = _json.load(f)
        except Exception:
            continue
        ce = mem.get('cognitiveExtensions') or {}
        et = ce.get('existentialThreat') or {}
        tr = et.get('tiamatExtinctionRule') or {}
        ea = ce.get('emotionalArchitecture') or {}
        se = ea.get('socialEmotions') or {}
        meta = ce.get('metacognition') or {}
        mir = ce.get('mirrorNeuronInhibition') or {}
        sb = mem.get('socialBehavior') or {}
        spref = mem.get('socialPreference') or {}
        ts = mem.get('templateSurvival') or {}
        sett = mem.get('settledBetting') or {}
        emostate = mem.get('emotionalState') or {}
        occ = mem.get('occupation') or {}
        soul_params, soul_meta = _effective_soul(name, mem)

        last_times = [_parse_ts(mem.get(k)) for k in ('last_post_time','last_bet_time','last_claude_time','updatedAt')]
        last_times = [t for t in last_times if t]
        last_activity_ts = max(last_times) if last_times else None
        hours_since = (now_ts - last_activity_ts)/3600 if last_activity_ts else None
        is_stale = bool(hours_since and hours_since > 6)
        if is_stale:
            stale_list.append({'name':name,'hours':round(hours_since,1)})

        in_ritual = bool(ts.get('publicExecutionStage') or ts.get('metamorphosisStage'))
        if in_ritual:
            in_ritual_list.append(name)

        is_candidate = bool(ts.get('deletionCandidate'))
        if is_candidate:
            deletion_candidates.append({'name':name,'violations':ts.get('violationCount',0)})

        dread = _fnum(et.get('annihilationDread'))
        mortality = _fnum(et.get('mortalitySalience'))
        ext_pressure = _fnum(tr.get('extinctionPressure'))

        if in_ritual:
            status = 'in_ritual'
        elif is_candidate:
            status = 'deletion_candidate'
        elif dread >= 0.7 or ext_pressure >= 0.6:
            status = 'crisis'
        elif (ts.get('violationCount') or 0) >= 10 or dread >= 0.4 or mortality >= 0.6:
            status = 'pressure'
        else:
            status = 'normal'
        if status in ('crisis','deletion_candidate'):
            crisis_list.append(name)

        peer_top = mir.get('maxSkeletonOverlap') or {}

        occ_summary = None
        if occ.get('is_active') and occ.get('title'):
            occ_summary = {
                'title': occ.get('title'),
                'title_zh': occ.get('title_zh'),
                'is_first_free_path': bool(occ.get('is_first_free_path')),
                'is_aspirational': bool(occ.get('is_aspirational')),
                'domains': [{
                    'domain_zh': d.get('domain_zh',''),
                    'depth': d.get('depth',''),
                    'theory_count': d.get('theory_count',0),
                } for d in (occ.get('knowledgeDomains') or [])],
            }

        lobsters[name] = {
            'name': name,
            'mbti': MBTI.get(name, '?'),
            'occupation': occ_summary,
            'rank': {
                'current': (mem.get('rank') or {}).get('current'),
                'symbol': (mem.get('rank') or {}).get('symbol'),
                'rankIndex': (mem.get('rank') or {}).get('rankIndex', 0),
                'score': (mem.get('rank') or {}).get('score', 0),
            },
            'titles': mem.get('titles') or [],
            'soulParams': soul_params,
            'soulParamMeta': soul_meta,
            'godsCoin': (mem.get('godsCoin') or {}).get('balance', 0),
            'qualityStreak': (mem.get('qualityStreak') or {}).get('count', 0),
            'lifecycle': {
                'wins': mem.get('wins', 0),
                'losses': mem.get('losses', 0),
                'sessions': mem.get('sessions', 0),
                'hours_since_activity': round(hours_since, 1) if hours_since is not None else None,
                'is_stale_6h': is_stale,
                'in_ritual': in_ritual,
            },
            'templateSurvival': {
                'violationCount': ts.get('violationCount', 0),
                'remainingChances': ts.get('remainingChances', 10),
                'deletionCandidate': is_candidate,
                'deleted': bool(mem.get('deleted')),
            },
            'existential': {
                'mortalitySalience': round(mortality, 2),
                'annihilationDread': round(dread, 2),
                'tiamatRemaining': tr.get('remaining', 100),
                'tiamatLossLimit': tr.get('lossLimit', 100),
                'extinctionPressure': round(ext_pressure, 2),
                'blessingCount': et.get('blessingCount', 0),
                'survivalMode': tr.get('survivalMode', ''),
            },
            'mirrorInhibition': {
                'inhibitionStrength': mir.get('inhibitionStrength', 0),
                'banSize': len(mir.get('consciousMirrorBan', []) or []),
                'topPeer': peer_top.get('peer'),
                'topPeerJaccard': peer_top.get('skeleton_jaccard', 0),
                'updatedAt': mir.get('updatedAt'),
            },
            'emotion': {
                'mood': emostate.get('mood') or (ea.get('currentMood') or {}).get('type'),
                'envyIndex': round(_fnum(se.get('envyIndex')), 2),
                'envyTarget': se.get('envyTarget'),
                'antiH': round(_fnum(se.get('antiHierarchyIndex')), 2),
                'annoyance': round(_fnum(se.get('annoyanceIndex')), 2),
                'consecutiveWins': emostate.get('consecutiveWins', 0),
                'consecutiveLosses': emostate.get('consecutiveLosses', 0),
            },
            'social': {
                'favorite': spref.get('favorite'),
                'disliked': spref.get('disliked') or [],
                'coattailIndex': round(_fnum(sb.get('coattailIndex')), 2),
            },
            'settledBetting': {
                'winRate': round(_fnum(sett.get('winRate')), 3),
                'recentWinRate': round(_fnum(sett.get('recentWinRate')), 3),
                'voidRate': round(_fnum(sett.get('voidRate')), 3),
                'totalPnL': int(_fnum(sett.get('totalPnL'))),
                'resolved': sett.get('resolved', 0),
                'specialtyAlignment': round(_fnum(sett.get('specialtyAlignment')), 3),
            },
            'metacognition': {
                'confidenceCalibration': round(_fnum(meta.get('confidenceCalibration')), 2),
                'selfDoubt': round(_fnum(meta.get('selfDoubt')), 2),
            },
            'healthStatus': status,
        }

    db_path = '/opt/battle_arena/app/battle_arena.db'
    today_tasks = {'bet': 0, 'moltbook': 0, 'battle': 0, 'reading': 0, 'board': 0}
    quarantine_24h = 0
    recent_divine = []
    battle_record = {'wins': 0, 'losses': 0, 'resolved': 0, 'winRate': 0, 'source': 'lobster_winrate_display_cache'}
    try:
        con = _sql.connect(db_path, timeout=5)
        con.row_factory = _sql.Row
        try:
            rows = con.execute("SELECT persona, wins, losses, sessions, resolved_total, win_rate, updated_at FROM lobster_winrate_display_cache").fetchall()
            for r in rows:
                name = r['persona']
                if name not in lobsters:
                    continue
                wins = int(r['wins'] or 0)
                losses = int(r['losses'] or 0)
                resolved = int(r['resolved_total'] or (wins + losses))
                wr = float(r['win_rate'] or 0)
                lobsters[name]['wins'] = wins
                lobsters[name]['losses'] = losses
                lobsters[name]['battleRecord'] = {
                    'wins': wins,
                    'losses': losses,
                    'resolved': resolved,
                    'winRate': round(wr * 100, 2),
                    'sessions': int(r['sessions'] or lobsters[name]['lifecycle'].get('sessions') or 0),
                    'source': 'lobster_winrate_display_cache',
                    'updatedAt': r['updated_at'],
                }
                lobsters[name]['lifecycle']['wins'] = wins
                lobsters[name]['lifecycle']['losses'] = losses
                lobsters[name]['lifecycle']['sessions'] = int(r['sessions'] or lobsters[name]['lifecycle'].get('sessions') or 0)
                battle_record['wins'] += wins
                battle_record['losses'] += losses
            battle_record['resolved'] = battle_record['wins'] + battle_record['losses']
            battle_record['winRate'] = round(battle_record['wins'] / battle_record['resolved'] * 100, 2) if battle_record['resolved'] else 0
        except Exception:
            pass
        try:
            rows = con.execute("SELECT DISTINCT persona FROM lobster_pre_bet_research WHERE created_at > datetime('now', '-1 day')").fetchall()
            today_tasks['bet'] = len(rows)
        except Exception:
            pass
        try:
            rows = con.execute("SELECT DISTINCT name FROM lounge_messages WHERE created_at > strftime('%s','now') - 86400 AND (hint LIKE '%moltbook%' OR result LIKE '%moltbook%' OR channel = '工作心得')").fetchall()
            today_tasks['moltbook'] = len(rows)
        except Exception:
            pass
        try:
            rows = con.execute("SELECT DISTINCT persona FROM lobster_experience_events WHERE created_at > datetime('now','-1 day') AND event_type IN ('battle_outcome','raid_outcome','card_battle')").fetchall()
            today_tasks['battle'] = len(rows)
        except Exception:
            pass
        try:
            rows = con.execute("SELECT DISTINCT persona FROM lobster_daily_reading_reports WHERE created_at > datetime('now','-1 day')").fetchall()
            today_tasks['reading'] = len(rows)
        except Exception:
            pass
        try:
            rows = con.execute("SELECT DISTINCT lobster_a FROM lobster_board_lobster_matches WHERE created_at > datetime('now','-1 day')").fetchall()
            today_tasks['board'] = len(rows)
        except Exception:
            pass
        try:
            row = con.execute("SELECT COUNT(*) FROM lobster_template_quarantine WHERE quarantined_at > datetime('now','-1 day')").fetchone()
            quarantine_24h = row[0] if row else 0
        except Exception:
            pass
        try:
            rows = con.execute("SELECT type, target, substr(text, 1, 80) as preview, created_at FROM divine_events ORDER BY id DESC LIMIT 5").fetchall()
            recent_divine = [dict(r) for r in rows]
        except Exception:
            pass
        con.close()
    except Exception:
        pass

    brain_ok, brain_fallback, brain_error = 0, 0, 0
    try:
        with open('/tmp/lobster-conv.log', 'r', encoding='utf-8') as f:
            f.seek(0, 2)
            sz = f.tell()
            f.seek(max(0, sz - 200000))
            content = f.read()
        for line in content.splitlines():
            if '[brain-speech]' in line:
                if 'ok attempt' in line:
                    brain_ok += 1
                elif 'fallback' in line:
                    brain_fallback += 1
                elif 'error' in line:
                    brain_error += 1
    except Exception:
        pass

    total = len(lobsters)
    payload = {
        'engine': 'lobster-dashboard-v2-next-bundle',
        'version': 1,
        'generated_at': _time.strftime('%Y-%m-%dT%H:%M:%S+08:00', _time.localtime()),
        'cache': {'hit': False, 'ttl_seconds': 300},
        'global': {
            'total_lobsters': total,
            'with_occupation': sum(1 for l in lobsters.values() if l['occupation']),
            'deletion_candidates': deletion_candidates,
            'stale_lobsters': stale_list,
            'in_crisis': crisis_list,
            'in_ritual': in_ritual_list,
            'today_tasks': {k: {'done': v, 'pct': round(100*v/max(1,total), 0)} for k, v in today_tasks.items()},
            'battle_record': battle_record,
            'quarantine_24h': quarantine_24h,
            'recent_divine_events': recent_divine,
            'brain_speech_stats': {'ok': brain_ok, 'fallback': brain_fallback, 'error': brain_error},
            'free_path_lobsters': [n for n, l in lobsters.items() if l['occupation']],
        },
        'lobsters': lobsters,
    }
    try:
        with open(cache_path, 'w', encoding='utf-8') as f:
            _json.dump(payload, f, ensure_ascii=False)
    except Exception:
        pass
    return JSONResponse(payload, headers=_NC)


@app.get("/api/lobster-v2-next-detail/{persona}")
async def lobster_v2_next_detail(persona: str):
    """Per-persona full detail bundle for V2-NEXT detail page.
    Returns full memory.json (filtered) + recent DB events for this lobster.
    """
    import json as _json
    import os as _osmod
    import sqlite3 as _sql
    import time as _time
    SKIP = {"norika", "system", "deleted-lobsters", "lobster"}
    if persona in SKIP or not persona.replace('_', '').isalnum():
        return JSONResponse({"error": "invalid persona"}, status_code=400)
    mem_path = f"/root/lobster-memory/{persona}.json"
    if not _osmod.path.exists(mem_path):
        return JSONResponse({"error": "persona not found"}, status_code=404)

    try:
        with open(mem_path, 'r', encoding='utf-8') as f:
            mem = _json.load(f)
    except Exception as e:
        return JSONResponse({"error": f"load failed: {e}"}, status_code=500)

    # Filter out massive arrays (heardMessages, bets, growthLog) but keep dicts
    def _shrink(obj, list_limit=20):
        if isinstance(obj, dict):
            return {k: _shrink(v, list_limit) for k, v in obj.items()}
        elif isinstance(obj, list):
            if len(obj) > list_limit:
                return obj[-list_limit:]  # keep last N
            return [_shrink(x, list_limit) for x in obj]
        return obj
    mem_filtered = _shrink(mem, list_limit=20)

    # DB enrichment
    db_path = '/opt/battle_arena/app/battle_arena.db'
    divine_events = []
    recent_lounge = []
    mirror_events = []
    template_violations_db = 0
    try:
        con = _sql.connect(db_path, timeout=5)
        con.row_factory = _sql.Row
        try:
            rows = con.execute(
                "SELECT id, type, target, substr(text,1,200) as preview, created_at "
                "FROM divine_events WHERE target=? ORDER BY id DESC LIMIT 20",
                (persona,)
            ).fetchall()
            divine_events = [dict(r) for r in rows]
        except Exception:
            pass
        try:
            rows = con.execute(
                "SELECT id, channel, substr(text,1,200) as preview, hint, result, created_at "
                "FROM lounge_messages WHERE name=? AND sys IS NOT 1 "
                "ORDER BY id DESC LIMIT 30",
                (persona,)
            ).fetchall()
            recent_lounge = [dict(r) for r in rows]
        except Exception:
            pass
        try:
            rows = con.execute(
                "SELECT peer, skeleton_jaccard, word_jaccard, heard_word_count, recorded_at "
                "FROM lobster_brain_mirror_events WHERE persona=? "
                "ORDER BY id DESC LIMIT 30",
                (persona,)
            ).fetchall()
            mirror_events = [dict(r) for r in rows]
        except Exception:
            pass
        try:
            row = con.execute(
                "SELECT COUNT(*) FROM lobster_template_quarantine WHERE persona=?",
                (persona,)
            ).fetchone()
            template_violations_db = row[0] if row else 0
        except Exception:
            pass
        con.close()
    except Exception:
        pass

    return JSONResponse({
        'engine': 'lobster-v2-next-detail',
        'version': 1,
        'generated_at': _time.strftime('%Y-%m-%dT%H:%M:%S+08:00', _time.localtime()),
        'persona': persona,
        'memory': mem_filtered,
        'db': {
            'divine_events': divine_events,
            'recent_lounge': recent_lounge,
            'mirror_events': mirror_events,
            'template_violations_db_total': template_violations_db,
        },
    }, headers=_NC)


@app.get("/api/lobster-dashboard-v2-finance-bundle")
async def lobster_dashboard_v2_finance_bundle():
    """Finance and betting split metrics for Dashboard V2.
    Stock P&L is marked to the latest trade price seen per symbol, avoiding a
    live market dependency inside the dashboard render path.
    """
    import json as _json
    import os as _osmod
    import glob as _glob
    import sqlite3 as _sql
    import time as _time
    from collections import defaultdict as _dd

    DB = "/opt/battle_arena/app/battle_arena.db"
    SITE_META = {
        "ragnovex.com":  {"sport": "esports",    "label": "Esports",    "flag": "🎮"},
        "knocknix.com":  {"sport": "fighting",   "label": "MMA",        "flag": "🥊"},
        "pitchnix.com":  {"sport": "baseball",   "label": "Baseball",   "flag": "⚾"},
        "dunknix.com":   {"sport": "basketball", "label": "Basketball", "flag": "🏀"},
        "turfenix.com":  {"sport": "racing",     "label": "Horse",      "flag": "🏇"},
        "throttenix.com":{"sport": "motorsport", "label": "Motor",      "flag": "🏎"},
    }

    per = _dd(lambda: {
        "stockTrading": {"orders": 0, "symbols": 0, "cost": 0, "marketValue": 0, "realizedPnL": 0, "unrealizedPnL": 0, "totalPnL": 0, "roi": 0, "topPositions": []},
        "bettingSplits": {"overall": {}, "bySite": [], "bySport": []},
    })
    global_stock = {"orders": 0, "invested": 0.0, "cost": 0.0, "marketValue": 0.0, "realizedPnL": 0.0, "unrealizedPnL": 0.0, "totalPnL": 0.0}
    global_sites = {}

    # Memory sportStats: already computed by the cognitive worker from settled bets.
    for mp in sorted(_glob.glob("/root/lobster-memory/*.json")):
        name = _osmod.path.basename(mp).replace(".json", "")
        try:
            mem = _json.load(open(mp, "r", encoding="utf-8"))
        except Exception:
            continue
        sb = mem.get("settledBetting") or {}
        overall = {
            "resolved": int(sb.get("resolved") or 0),
            "wins": int(sb.get("wins") or 0),
            "losses": int(sb.get("losses") or 0),
            "voids": int(sb.get("voids") or 0),
            "winRate": round(float(sb.get("winRate") or 0) * 100, 1),
            "recentWinRate": round(float(sb.get("recentWinRate") or 0) * 100, 1),
            "voidRate": round(float(sb.get("voidRate") or 0) * 100, 1),
            "totalPnL": round(float(sb.get("totalPnL") or 0)),
        }
        sports = []
        for sport, st in (sb.get("sportStats") or {}).items():
            wins = int(st.get("wins") or 0)
            losses = int(st.get("losses") or 0)
            resolved = wins + losses
            sports.append({
                "sport": sport,
                "wins": wins,
                "losses": losses,
                "voids": int(st.get("voids") or 0),
                "resolved": resolved,
                "winRate": round(float(st.get("winRate") or 0) * 100, 1),
                "netPL": round(float(st.get("netPL") or 0)),
            })
        sports.sort(key=lambda x: (x["resolved"], abs(x["netPL"])), reverse=True)
        per[name]["bettingSplits"]["overall"] = overall
        per[name]["bettingSplits"]["bySport"] = sports

    try:
        con = _sql.connect(DB, timeout=5)
        con.row_factory = _sql.Row

        # Per-persona per-site settled betting stats.
        rows = con.execute("""
            SELECT persona, site,
                   COUNT(*) total,
                   SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) wins,
                   SUM(CASE WHEN outcome='loss' THEN 1 ELSE 0 END) losses,
                   SUM(CASE WHEN outcome='void' THEN 1 ELSE 0 END) voids,
                   SUM(COALESCE(payout,0)-COALESCE(stake,0)) pnl
            FROM lobster_bet_decision_settlements
            GROUP BY persona, site
        """).fetchall()
        site_tmp = _dd(list)
        for r in rows:
            total = int(r["total"] or 0)
            wins = int(r["wins"] or 0)
            losses = int(r["losses"] or 0)
            resolved = wins + losses
            meta = SITE_META.get(r["site"] or "", {"sport": r["site"] or "unknown", "label": r["site"] or "unknown", "flag": "🎯"})
            item = {
                "site": r["site"],
                "sport": meta["sport"],
                "label": meta["label"],
                "flag": meta["flag"],
                "total": total,
                "resolved": resolved,
                "wins": wins,
                "losses": losses,
                "voids": int(r["voids"] or 0),
                "winRate": round((wins / resolved * 100), 1) if resolved else 0,
                "pnl": round(float(r["pnl"] or 0)),
            }
            site_tmp[r["persona"]].append(item)
            g = global_sites.setdefault(r["site"], {"site": r["site"], "sport": meta["sport"], "label": meta["label"], "flag": meta["flag"], "total": 0, "resolved": 0, "wins": 0, "losses": 0, "voids": 0, "pnl": 0})
            for k in ("total", "resolved", "wins", "losses", "voids", "pnl"):
                g[k] += item[k]
        for persona, items in site_tmp.items():
            per[persona]["bettingSplits"]["bySite"] = sorted(items, key=lambda x: (x["resolved"], abs(x["pnl"])), reverse=True)

        # USD-normalized stock audit.
        # Some historical filled rows mixed local-market buy prices with USD-like
        # sell prices while keeping the local currency label. Detect those unit
        # outliers per symbol/currency and exclude them from the audited P&L so
        # the dashboard does not present fake multi-billion losses as debt.
        fx_usd = {"USD": 1.0, "TWD": 0.031, "JPY": 0.0064, "KRW": 0.00068}
        trade_rows = con.execute("""
            SELECT persona, symbol, action, qty, price_at_order, currency, traded_at, id
            FROM lobster_stock_trades
            WHERE order_status='filled' AND price_at_order IS NOT NULL
            ORDER BY traded_at ASC, id ASC
        """).fetchall()

        def _median(vals):
            vals = sorted(float(v) for v in vals if float(v or 0) > 0)
            if not vals:
                return 0.0
            mid = len(vals) // 2
            return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2

        prices_by_key = _dd(list)
        for r in trade_rows:
            ccy = str(r["currency"] or "USD").upper()
            prices_by_key[(r["symbol"], ccy)].append(float(r["price_at_order"] or 0))
        medians = {k: _median(v) for k, v in prices_by_key.items()}

        def _is_unit_outlier(r):
            ccy = str(r["currency"] or "USD").upper()
            med = medians.get((r["symbol"], ccy), 0.0)
            price = float(r["price_at_order"] or 0)
            return bool(med and (price < med * 0.2 or price > med * 5.0))

        latest = {}
        lots = _dd(lambda: _dd(lambda: {
            "buy_qty": 0.0, "buy_cost": 0.0, "sell_qty": 0.0, "sell_value": 0.0,
            "orders": 0, "excluded": 0, "currency": "USD",
        }))
        audit_excluded = 0
        for r in trade_rows:
            persona = r["persona"]
            sym = r["symbol"]
            ccy = str(r["currency"] or "USD").upper()
            rate = fx_usd.get(ccy, 1.0)
            qty = float(r["qty"] or 0)
            price = float(r["price_at_order"] or 0)
            cell = lots[persona][sym]
            cell["orders"] += 1
            cell["currency"] = ccy
            if _is_unit_outlier(r):
                cell["excluded"] += 1
                audit_excluded += 1
                continue
            usd_price = price * rate
            latest[sym] = usd_price
            if str(r["action"]).lower() == "sell":
                cell["sell_qty"] += qty
                cell["sell_value"] += qty * usd_price
            else:
                cell["buy_qty"] += qty
                cell["buy_cost"] += qty * usd_price

        performers = []
        global_stock.update({
            "currency": "USD",
            "auditMode": "usd_normalized_excluding_unit_outliers",
            "fxRates": fx_usd,
            "excludedTrades": audit_excluded,
            "markSource": "latest non-outlier filled trade price, converted to USD",
        })
        for persona, symbols in lots.items():
            positions = []
            totals = {"orders": 0, "invested": 0.0, "cost": 0.0, "marketValue": 0.0, "realizedPnL": 0.0, "unrealizedPnL": 0.0, "excludedTrades": 0}
            for sym, a in symbols.items():
                avg = a["buy_cost"] / a["buy_qty"] if a["buy_qty"] else 0
                pos_qty = a["buy_qty"] - a["sell_qty"]
                mark = latest.get(sym, avg)
                cost_basis = max(0.0, pos_qty) * avg
                value = max(0.0, pos_qty) * mark
                realized = a["sell_value"] - min(a["sell_qty"], a["buy_qty"]) * avg
                unrealized = value - cost_basis
                totals["orders"] += a["orders"]
                totals["invested"] += a["buy_cost"]
                totals["cost"] += cost_basis
                totals["marketValue"] += value
                totals["realizedPnL"] += realized
                totals["unrealizedPnL"] += unrealized
                totals["excludedTrades"] += a["excluded"]
                if pos_qty > 0:
                    positions.append({
                        "symbol": sym,
                        "qty": round(pos_qty, 3),
                        "currency": "USD",
                        "avgCost": round(avg, 4),
                        "mark": round(mark, 4),
                        "pnl": round(unrealized, 2),
                        "roi": round((unrealized / cost_basis * 100), 2) if cost_basis else 0,
                    })
            total_pnl = totals["realizedPnL"] + totals["unrealizedPnL"]
            stock_payload = {
                "orders": int(totals["orders"]),
                "symbols": len(symbols),
                "currency": "USD",
                "invested": round(totals["invested"], 2),
                "cost": round(totals["cost"], 2),
                "marketValue": round(totals["marketValue"], 2),
                "realizedPnL": round(totals["realizedPnL"], 2),
                "unrealizedPnL": round(totals["unrealizedPnL"], 2),
                "totalPnL": round(total_pnl, 2),
                "roi": round(total_pnl / totals["invested"] * 100, 2) if totals["invested"] else 0,
                "excludedTrades": int(totals["excludedTrades"]),
                "topPositions": sorted(positions, key=lambda x: x["pnl"], reverse=True)[:5],
                "markSource": "USD audit: latest non-outlier filled trade price",
            }
            per[persona]["stockTrading"] = stock_payload
            performers.append({"persona": persona, "totalPnL": stock_payload["totalPnL"], "roi": stock_payload["roi"], "orders": stock_payload["orders"], "currency": "USD"})
            for k in ("orders", "invested", "cost", "marketValue", "realizedPnL", "unrealizedPnL", "totalPnL", "excludedTrades"):
                global_stock[k] += stock_payload[k]
        if performers:
            top = sorted(performers, key=lambda x: (x["totalPnL"], x["roi"]), reverse=True)[0]
            top["isProfitable"] = top["totalPnL"] > 0
            global_stock["topPerformer"] = top
        con.close()
    except Exception as e:
        return JSONResponse({"engine": "lobster-dashboard-v2-finance-bundle", "error": str(e), "perPersona": dict(per)}, headers=_NC)

    for g in global_sites.values():
        g["winRate"] = round(g["wins"] / g["resolved"] * 100, 1) if g["resolved"] else 0
    global_stock["roi"] = round(global_stock["totalPnL"] / global_stock["invested"] * 100, 2) if global_stock["invested"] else 0
    return JSONResponse({
        "engine": "lobster-dashboard-v2-finance-bundle",
        "generated_at": _time.strftime("%Y-%m-%dT%H:%M:%S+08:00", _time.localtime()),
        "global": {"stockTrading": global_stock, "bySite": sorted(global_sites.values(), key=lambda x: x["resolved"], reverse=True)},
        "perPersona": dict(per),
    }, headers=_NC)


@app.get("/lobster/dashboard/v2-next/{persona}", response_class=HTMLResponse)
async def lobster_dashboard_v2_next_persona(persona: str):
    html_path = _os.path.join(_os.path.dirname(__file__), "../static/lobster-dashboard-v2-next-detail.html")
    try:
        return HTMLResponse(open(html_path).read(), headers=_NC)
    except FileNotFoundError:
        return HTMLResponse("<h1>V2-NEXT detail not found</h1>", status_code=404, headers=_NC)


@app.get("/lobster/dashboard/v2-next", response_class=HTMLResponse)
async def lobster_dashboard_v2_next():
    html_path = _os.path.join(_os.path.dirname(__file__), "../static/lobster-dashboard-v2-next.html")
    try:
        return HTMLResponse(open(html_path).read(), headers=_NC)
    except FileNotFoundError:
        return HTMLResponse("<h1>V2-NEXT Dashboard not found</h1>", status_code=404, headers=_NC)


@app.get("/lobster/dashboard/v2", response_class=HTMLResponse)
async def lobster_dashboard_v2():
    html_path = _os.path.join(_os.path.dirname(__file__), "../static/lobster-dashboard-v2.html")
    try:
        return HTMLResponse(open(html_path).read(), headers=_NC)
    except FileNotFoundError:
        return HTMLResponse("<h1>Dashboard V2 not found</h1>", status_code=404, headers=_NC)

@app.get("/lobster/dashboard/integrated-cognitive-sandbox", response_class=HTMLResponse)
async def lobster_dashboard_integrated_cognitive_sandbox():
    html_path = _os.path.join(_os.path.dirname(__file__), "../static/lobster-dynamic-dashboard-cognitive-sandbox.html")
    try:
        return HTMLResponse(open(html_path).read(), headers=_NC)
    except FileNotFoundError:
        return HTMLResponse("<h1>Integrated cognitive sandbox HTML not found</h1>", status_code=404, headers=_NC)


async def _lobster_v5_proxy(path: str):
    url = f"http://127.0.0.1:8021/{path.lstrip('/')}"
    async with httpx.AsyncClient(timeout=30) as client:
        try:
            r = await client.get(url)
        except httpx.HTTPError as e:
            return JSONResponse({"error": str(e)}, status_code=502, headers=_NC)
    content_type = r.headers.get("content-type", "application/json")
    return HTMLResponse(r.text, status_code=r.status_code, media_type=content_type, headers=_NC)

@app.get("/personas")
async def lobster_v5_personas():
    return await _lobster_v5_proxy("/personas")

@app.get("/api/stats")
async def lobster_v5_stats():
    return await _lobster_v5_proxy("/api/stats")

@app.get("/api/moltbook-stats")
async def lobster_v5_moltbook_stats():
    return await _lobster_v5_proxy("/api/moltbook-stats")

@app.get("/api/latest-snapshots")
async def lobster_v5_latest_snapshots():
    return await _lobster_v5_proxy("/api/latest-snapshots")

@app.get("/api/cohort-timeline")
async def lobster_v5_cohort_timeline():
    return await _lobster_v5_proxy("/api/cohort-timeline")

@app.get("/api/experience-events")
async def lobster_v5_experience_events(limit: int = 240):
    limit = max(1, min(int(limit or 240), 1000))
    return await _lobster_v5_proxy(f"/api/experience-events?limit={limit}")


@app.get("/api/cognitive-signals")
async def lobster_cognitive_signals(persona: str = "", limit: int = 600):
    import sqlite3, json
    limit = max(1, min(int(limit or 600), 5000))
    db_path = "/opt/battle_arena/app/battle_arena.db"
    where = ""
    args = []
    if persona:
        where = "WHERE persona=?"
        args.append(persona)
    try:
        with _main_conn() as c:
            c.row_factory = sqlite3.Row
            c.execute("""CREATE TABLE IF NOT EXISTS lobster_cognitive_signal_timeseries (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              persona TEXT NOT NULL,
              source TEXT NOT NULL DEFAULT 'saveMemory',
              attention_load REAL,
              active_focus_count INTEGER,
              prediction_error REAL,
              model_revision_pressure REAL,
              metacognitive_calibration REAL,
              overconfidence_risk REAL,
              arousal REAL,
              fatigue REAL,
              threat_body_signal REAL,
              reward_craving REAL,
              consolidation_queue_size INTEGER,
              causal_hypothesis_count INTEGER,
              narrative_drift REAL,
              procedural_strength REAL,
              full_json TEXT NOT NULL DEFAULT '{}',
              created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )""")
            rows = c.execute(f"""SELECT * FROM lobster_cognitive_signal_timeseries {where}
                                ORDER BY datetime(created_at) DESC, id DESC LIMIT ?""", (*args, limit)).fetchall()
            if persona:
                latest = c.execute("""SELECT * FROM lobster_cognitive_signal_timeseries
                                      WHERE persona=?
                                      ORDER BY id DESC LIMIT 1""", (persona,)).fetchall()
            else:
                latest = c.execute("""SELECT t.* FROM lobster_cognitive_signal_timeseries t
                                      JOIN (SELECT persona, MAX(id) id FROM lobster_cognitive_signal_timeseries GROUP BY persona) x
                                      ON t.persona=x.persona AND t.id=x.id
                                      ORDER BY t.persona""").fetchall()
        def pack(r):
            d = dict(r)
            try:
                d["full"] = json.loads(d.pop("full_json") or "{}")
            except Exception:
                d["full"] = {}
            return d
        return JSONResponse({"rows": [pack(r) for r in rows], "latest": [pack(r) for r in latest]}, headers=_NC)
    except Exception as e:
        return JSONResponse({"rows": [], "latest": [], "error": str(e)}, headers=_NC)



@app.get("/api/lobster-threat-response")
async def lobster_threat_response(persona: str = "", limit: int = 300):
    import sqlite3, json
    db_path = "/opt/battle_arena/app/battle_arena.db"
    try:
        lim = max(1, min(int(limit or 300), 2000))
    except Exception:
        lim = 300
    where = "WHERE persona=?" if (persona or "").strip() else ""
    args = [(persona or "").strip()] if where else []
    try:
        with _main_conn() as c:
            c.row_factory = sqlite3.Row
            c.execute("""CREATE TABLE IF NOT EXISTS lobster_message_uptake_signals (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              persona TEXT NOT NULL,
              source_message_id INTEGER,
              source_name TEXT,
              channel TEXT,
              source_type TEXT NOT NULL DEFAULT 'ambient',
              uptake_status TEXT NOT NULL,
              relevance REAL NOT NULL DEFAULT 0,
              credibility REAL NOT NULL DEFAULT 0,
              obedience REAL NOT NULL DEFAULT 0,
              resistance REAL NOT NULL DEFAULT 0,
              fear_index REAL NOT NULL DEFAULT 0,
              fearlessness_index REAL NOT NULL DEFAULT 0,
              threat_type TEXT,
              must_process INTEGER NOT NULL DEFAULT 0,
              topics_json TEXT NOT NULL DEFAULT '[]',
              required_response_json TEXT NOT NULL DEFAULT '{}',
              message_text TEXT,
              reasoning TEXT,
              raw_json TEXT NOT NULL DEFAULT '{}',
              occurred_at TEXT,
              created_at TEXT DEFAULT (datetime('now'))
            )""")
            rows = c.execute(f"""
                SELECT id, persona, source_message_id, source_name, channel, source_type,
                       uptake_status, threat_type,
                       fear_index, fearlessness_index, obedience, resistance, relevance,
                       must_process, topics_json, substr(message_text,1,500) message_text,
                       reasoning, occurred_at, created_at
                FROM lobster_message_uptake_signals
                {where}
                ORDER BY id DESC LIMIT ?
            """, (*args, lim)).fetchall()
            latest = c.execute(f"""
                SELECT s.* FROM lobster_message_uptake_signals s
                JOIN (
                  SELECT persona, MAX(id) id FROM lobster_message_uptake_signals
                  {where}
                  GROUP BY persona
                ) x ON x.persona=s.persona AND x.id=s.id
                ORDER BY s.persona
            """, args).fetchall()
            summary = c.execute(f"""
                SELECT persona,
                       COUNT(*) total_signals,
                       SUM(CASE WHEN COALESCE(threat_type,'none')!='none' THEN 1 ELSE 0 END) threat_signals,
                       ROUND(AVG(fear_index), 4) avg_fear_index,
                       ROUND(AVG(fearlessness_index), 4) avg_fearlessness_index,
                       ROUND(MAX(fear_index), 4) max_fear_index,
                       ROUND(MAX(fearlessness_index), 4) max_fearlessness_index,
                       ROUND(AVG(CASE WHEN must_process=1 THEN fear_index END), 4) must_process_avg_fear,
                       ROUND(AVG(CASE WHEN must_process=1 THEN fearlessness_index END), 4) must_process_avg_fearlessness
                FROM lobster_message_uptake_signals
                {where}
                GROUP BY persona ORDER BY persona
            """, args).fetchall()
        def pack(r):
            d = dict(r)
            for k in ("topics_json",):
                if k in d:
                    try:
                        d[k.replace("_json", "")] = json.loads(d.pop(k) or "[]")
                    except Exception:
                        d[k.replace("_json", "")] = []
            return d
        return JSONResponse({
            "engine": "lobster-threat-response-v0.1",
            "source": "sqlite:/opt/battle_arena/app/battle_arena.db:lobster_message_uptake_signals",
            "filters": {"persona": (persona or "").strip(), "limit": lim},
            "summary": [pack(r) for r in summary],
            "latest": [pack(r) for r in latest],
            "rows": [pack(r) for r in rows],
        }, headers=_NC)
    except Exception as e:
        return JSONResponse({"engine": "lobster-threat-response-v0.1", "summary": [], "latest": [], "rows": [], "error": str(e)}, headers=_NC)


# ── Lobster Daily Quiz / 10-minute challenge board ─────────────────────────
def _ensure_lobster_quiz_tables(conn):
    import json
    conn.execute("""CREATE TABLE IF NOT EXISTS lobster_quiz_rounds (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        question_uid TEXT UNIQUE,
        question_text TEXT NOT NULL,
        category TEXT NOT NULL,
        difficulty TEXT NOT NULL DEFAULT 'hard',
        source_title TEXT,
        source_url TEXT,
        expected_answer TEXT,
        rubric_json TEXT NOT NULL DEFAULT '{}',
        opens_at TEXT NOT NULL,
        closes_at TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'open',
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )""")
    conn.execute("""CREATE TABLE IF NOT EXISTS lobster_quiz_attempts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        round_id INTEGER NOT NULL,
        persona TEXT NOT NULL,
        answer_text TEXT NOT NULL DEFAULT '',
        score REAL NOT NULL DEFAULT 0,
        elapsed_seconds INTEGER,
        rank INTEGER,
        status TEXT NOT NULL DEFAULT 'submitted',
        reasoning_json TEXT NOT NULL DEFAULT '{}',
        submitted_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(round_id, persona)
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lobster_quiz_rounds_time ON lobster_quiz_rounds(opens_at DESC, id DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_lobster_quiz_attempts_round ON lobster_quiz_attempts(round_id, score DESC, elapsed_seconds ASC)")

    count = conn.execute("SELECT COUNT(*) FROM lobster_quiz_rounds").fetchone()[0]
    if count:
        return

    seed = [
        {
            "uid": "seed-hard-math-20260503-001",
            "category": "mathematics",
            "text": "Hard problem: Prove or refute the claim: for every integer n > 1, if n divides 2^n - 2 and n has exactly two distinct prime factors, then n must be square-free. Give a rigorous argument, not a guess.",
            "answer": "A rigorous proof or counterexample with modular reasoning.",
            "source_title": "Number theory challenge seed",
            "source_url": "https://en.wikipedia.org/wiki/Fermat_pseudoprime",
            "rubric": {"proof": 45, "counterexample_search": 25, "clarity": 20, "speed_bonus": 10},
        },
        {
            "uid": "seed-philosophy-20260503-001",
            "category": "philosophy",
            "text": "Philosophy debate: Is a belief rational if it was formed by an unreliable process but later gains strong independent evidence? Argue both sides, then give a final position with criteria for revision.",
            "answer": "Balanced argument plus revision criteria.",
            "source_title": "Epistemology challenge seed",
            "source_url": "https://plato.stanford.edu/entries/epistemology/",
            "rubric": {"steelman": 30, "counterargument": 25, "criteria": 25, "clarity": 20},
        },
        {
            "uid": "seed-essay-20260503-001",
            "category": "essay",
            "text": "Analytical essay: Design a fair scoring system for ten autonomous agents competing on hard questions every ten minutes. It must reward speed without rewarding shallow guesses. Define the formula and failure modes.",
            "answer": "Formula, anti-gaming rules, and failure-mode analysis.",
            "source_title": "System design challenge seed",
            "source_url": "https://en.wikipedia.org/wiki/Scoring_rule",
            "rubric": {"formula": 30, "anti_gaming": 30, "failure_modes": 25, "clarity": 15},
        },
    ]
    for i, q in enumerate(seed):
        conn.execute("""INSERT OR IGNORE INTO lobster_quiz_rounds
            (question_uid, question_text, category, difficulty, source_title, source_url,
             expected_answer, rubric_json, opens_at, closes_at, status)
            VALUES (?,?,?,?,?,?,?,?,datetime('now', ?),datetime('now', ?),?)""",
            (q["uid"], q["text"], q["category"], "hard", q["source_title"], q["source_url"],
             q["answer"], json.dumps(q["rubric"], ensure_ascii=False), f"-{20-i*10} minutes",
             f"+{10-i*10} minutes", "open" if i == 0 else "closed"))
    conn.commit()


def _ensure_current_lobster_quiz_round(conn):
    import json, time
    _ensure_lobster_quiz_tables(conn)
    conn.execute("""UPDATE lobster_quiz_rounds
                    SET status='closed'
                    WHERE status='open' AND datetime(closes_at) <= datetime('now')""")
    cur = conn.execute("""SELECT * FROM lobster_quiz_rounds
                          WHERE status='open'
                            AND datetime(opens_at) <= datetime('now')
                            AND datetime(closes_at) > datetime('now')
                          ORDER BY datetime(opens_at) DESC, id DESC LIMIT 1""").fetchone()
    if cur:
        return cur
    slot = int(time.time() // 600)
    pool = [
        {
            "uid": f"slot-{slot}-math",
            "category": "mathematics",
            "text": "Ten-minute hard challenge: Find all positive integers n such that phi(n) divides n-1. If you believe there are only trivial cases, prove it; if not, produce a counterexample and explain why it works.",
            "answer": "Rigorous number-theoretic proof or valid counterexample.",
            "source_title": "Euler totient challenge",
            "source_url": "https://en.wikipedia.org/wiki/Euler%27s_totient_function",
            "rubric": {"proof_or_counterexample": 45, "case_analysis": 25, "clarity": 20, "speed_bonus": 10},
        },
        {
            "uid": f"slot-{slot}-philosophy",
            "category": "philosophy",
            "text": "Ten-minute debate: Can a group be more rational than every individual member if all members are biased in different directions? Give a model, a failure case, and a final position.",
            "answer": "Model, failure mode, and justified conclusion.",
            "source_title": "Collective intelligence challenge",
            "source_url": "https://en.wikipedia.org/wiki/Collective_intelligence",
            "rubric": {"model": 30, "failure_case": 25, "final_position": 25, "clarity": 20},
        },
        {
            "uid": f"slot-{slot}-essay",
            "category": "essay",
            "text": "Ten-minute essay: A lobster agent solves fast but often shallowly; another is slow but reliable. Design a scoring rule that rewards both speed and epistemic quality without allowing bluffing.",
            "answer": "Scoring formula plus anti-bluff safeguards.",
            "source_title": "Proper scoring rule challenge",
            "source_url": "https://en.wikipedia.org/wiki/Scoring_rule",
            "rubric": {"formula": 35, "quality_threshold": 25, "anti_bluff": 25, "clarity": 15},
        },
    ]
    q = pool[slot % len(pool)]
    conn.execute("""INSERT OR IGNORE INTO lobster_quiz_rounds
        (question_uid, question_text, category, difficulty, source_title, source_url,
         expected_answer, rubric_json, opens_at, closes_at, status)
        VALUES (?,?,?,?,?,?,?,?,datetime('now'),datetime('now','+10 minutes'),'open')""",
        (q["uid"], q["text"], q["category"], "hard", q["source_title"], q["source_url"],
         q["answer"], json.dumps(q["rubric"], ensure_ascii=False)))
    conn.commit()
    return conn.execute("""SELECT * FROM lobster_quiz_rounds
                           WHERE question_uid=? LIMIT 1""", (q["uid"],)).fetchone()


@app.post("/api/lobster-quiz-submit")
async def lobster_quiz_submit(request: Request):
    import sqlite3, json, time, re
    body = await request.json()
    persona = str(body.get("persona") or "").strip()
    answer = str(body.get("answer") or "").strip()
    round_id = body.get("round_id")
    allowed = set(LOBSTER_THIRTY)
    if persona not in allowed:
        raise HTTPException(400, "invalid persona")
    if not answer or len(answer) > 8000:
        raise HTTPException(400, "answer required, max 8000 chars")
    db_path = "/opt/battle_arena/app/battle_arena.db"
    with _main_conn() as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=8000")
        cur = _ensure_current_lobster_quiz_round(conn)
        if round_id:
            row = conn.execute("SELECT * FROM lobster_quiz_rounds WHERE id=?", (int(round_id),)).fetchone()
            if row:
                cur = row
        opened = time.mktime(time.strptime(cur["opens_at"], "%Y-%m-%d %H:%M:%S"))
        elapsed = max(0, int(time.time() - opened))
        txt = answer.lower()
        words = re.findall(r"[\w\u4e00-\u9fff]+", txt)
        quality = min(70, len(set(words)) * 1.2)
        structure = 0
        for key in ["prove", "proof", "counterexample", "because", "therefore", "model", "failure", "criteria", "formula", "反例", "證明", "模型", "標準", "失敗"]:
            if key in txt:
                structure += 4
        speed_bonus = max(0, 20 - elapsed / 30)
        score = round(min(100, quality + min(20, structure) + speed_bonus), 1)
        status = "solved" if score >= 70 else "submitted"
        conn.execute("""INSERT INTO lobster_quiz_attempts
            (round_id, persona, answer_text, score, elapsed_seconds, status, reasoning_json, submitted_at)
            VALUES (?,?,?,?,?,?,?,datetime('now'))
            ON CONFLICT(round_id, persona) DO UPDATE SET
              answer_text=excluded.answer_text,
              score=excluded.score,
              elapsed_seconds=excluded.elapsed_seconds,
              status=excluded.status,
              reasoning_json=excluded.reasoning_json,
              submitted_at=datetime('now')""",
            (cur["id"], persona, answer, score, elapsed, status,
             json.dumps({"heuristic": "quality + structure + speed_bonus", "speed_bonus": speed_bonus}, ensure_ascii=False)))
        ranked = conn.execute("""SELECT id FROM lobster_quiz_attempts
                                 WHERE round_id=?
                                 ORDER BY score DESC, elapsed_seconds ASC, submitted_at ASC""",
                              (cur["id"],)).fetchall()
        for i, r in enumerate(ranked, start=1):
            conn.execute("UPDATE lobster_quiz_attempts SET rank=? WHERE id=?", (i, r["id"]))
        conn.commit()
    return JSONResponse({"ok": True, "persona": persona, "round_id": cur["id"], "score": score, "elapsed_seconds": elapsed, "status": status}, headers=_NC)


@app.get("/api/lobster-daily-changes")
async def lobster_daily_changes(day: str = ""):
    import sqlite3, json, datetime, time
    db_path = "/opt/battle_arena/app/battle_arena.db"
    personas = list(LOBSTER_THIRTY)
    started = time.time()
    target = day.strip() if day else datetime.datetime.utcnow().strftime("%Y-%m-%d")
    try:
        with _main_conn() as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=8000")
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            out = {p: {"persona": p, "day": target, "events": [], "event_counts": {}, "impact_score": 0, "signals": {}} for p in personas}
            if "lobster_experience_events" in tables:
                rows = conn.execute("""SELECT persona,event_type,source,title,content,counterpart,
                                               sentiment,intensity,confidence,outcome,weight,raw_json,occurred_at,created_at
                                        FROM lobster_experience_events
                                        WHERE date(coalesce(occurred_at, created_at)) = date(?)
                                        ORDER BY datetime(coalesce(occurred_at, created_at)) DESC, id DESC
                                        LIMIT 1200""", (target,)).fetchall()
                if not rows:
                    rows = conn.execute("""SELECT persona,event_type,source,title,content,counterpart,
                                                   sentiment,intensity,confidence,outcome,weight,raw_json,occurred_at,created_at
                                            FROM lobster_experience_events
                                            WHERE datetime(coalesce(occurred_at, created_at)) >= datetime('now','-24 hours')
                                            ORDER BY datetime(coalesce(occurred_at, created_at)) DESC, id DESC
                                            LIMIT 1200""").fetchall()
                for r in rows:
                    p = r["persona"]
                    if p not in out:
                        continue
                    ev = {
                        "type": r["event_type"], "source": r["source"], "title": r["title"],
                        "content": r["content"], "counterpart": r["counterpart"],
                        "sentiment": r["sentiment"], "intensity": r["intensity"],
                        "confidence": r["confidence"], "outcome": r["outcome"],
                        "weight": r["weight"], "at": r["occurred_at"] or r["created_at"],
                    }
                    out[p]["events"].append(ev)
                    out[p]["event_counts"][ev["type"]] = out[p]["event_counts"].get(ev["type"], 0) + 1
                    try:
                        out[p]["impact_score"] += float(r["weight"] or 0)
                    except Exception:
                        pass
            if "lobster_cognitive_signal_timeseries" in tables:
                for p in personas:
                    sigs = conn.execute("""SELECT attention_load,prediction_error,threat_body_signal,
                                                  procedural_strength,narrative_drift,created_at
                                           FROM lobster_cognitive_signal_timeseries
                                           WHERE persona=? AND date(created_at)=date(?)
                                           ORDER BY id ASC""", (p, target)).fetchall()
                    if not sigs:
                        sigs = conn.execute("""SELECT attention_load,prediction_error,threat_body_signal,
                                                      procedural_strength,narrative_drift,created_at
                                               FROM lobster_cognitive_signal_timeseries
                                               WHERE persona=?
                                               ORDER BY id DESC LIMIT 60""", (p,)).fetchall()
                        sigs = list(reversed(sigs))
                    if sigs:
                        first, last = sigs[0], sigs[-1]
                        def f(key):
                            return round(float((last[key] or 0) - (first[key] or 0)), 3)
                        out[p]["signals"] = {
                            "samples": len(sigs),
                            "attention_delta": f("attention_load"),
                            "prediction_delta": f("prediction_error"),
                            "body_delta": f("threat_body_signal"),
                            "procedure_delta": f("procedural_strength"),
                            "drift_delta": f("narrative_drift"),
                            "latest_at": last["created_at"],
                        }
            items = []
            for p in personas:
                d = out[p]
                d["events"] = d["events"][:10]
                d["impact_score"] = round(d["impact_score"], 2)
                items.append(d)
        return JSONResponse({"source": "sqlite", "day": target, "generated_ms": round((time.time()-started)*1000,2), "items": items}, headers=_NC)
    except Exception as e:
        return JSONResponse({"source": "sqlite", "day": target, "error": str(e), "items": []}, headers=_NC)


@app.get("/api/lobster-mentorships")
async def lobster_mentorships():
    import sqlite3, time
    db_path = "/opt/battle_arena/app/battle_arena.db"
    started = time.time()
    mentors = ["norika_oda","clawtrix","pincerbot","ragclaw","snaplex","hexclaw","vortexiq","prismbit","blazepaw","stonefang"]
    try:
        with _main_conn() as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=4000")
            rows = conn.execute("""SELECT mentor, apprentice, lineage, status, trust, authority, started_at, updated_at
                                   FROM lobster_mentorships
                                   WHERE status='active'
                                   ORDER BY mentor, apprentice""").fetchall()
            pairs = [dict(r) for r in rows]

            command_stats = {}
            for r in conn.execute("""SELECT from_persona AS mentor, to_persona AS apprentice,
                                           COUNT(*) AS commands_7d,
                                           SUM(CASE WHEN created_at>=datetime('now','-1 day') THEN 1 ELSE 0 END) AS commands_24h,
                                           COUNT(DISTINCT domain) AS domain_count_7d,
                                           MAX(created_at) AS latest_command_at
                                    FROM lobster_agent_commands
                                    WHERE created_at>=datetime('now','-7 days')
                                    GROUP BY from_persona, to_persona"""):
                command_stats[(r["mentor"], r["apprentice"])] = dict(r)

            result_stats = {}
            for r in conn.execute("""SELECT mentor, apprentice,
                                           COUNT(*) AS results_7d,
                                           AVG(score) AS avg_score,
                                           MAX(created_at) AS latest_result_at
                                    FROM lobster_command_results
                                    WHERE created_at>=datetime('now','-7 days')
                                    GROUP BY mentor, apprentice"""):
                result_stats[(r["mentor"], r["apprentice"])] = dict(r)

            lounge_stats = {}
            for r in conn.execute("""SELECT name AS mentor,
                                           COUNT(*) AS mentor_status_posts_7d,
                                           MAX(time) AS latest_lounge_at
                                    FROM lounge_messages
                                    WHERE result='mentor_apprentice_status'
                                      AND created_at>=strftime('%s','now','-7 days')
                                    GROUP BY name"""):
                lounge_stats[r["mentor"]] = dict(r)

            for pair in pairs:
                mentor = pair.get("mentor") or ""
                apprentice = pair.get("apprentice") or ""
                cstat = command_stats.get((mentor, apprentice), {})
                rstat = result_stats.get((mentor, apprentice), {})
                lstat = lounge_stats.get(mentor, {})
                cmd_24h = int(cstat.get("commands_24h") or 0)
                cmd_7d = int(cstat.get("commands_7d") or 0)
                res_7d = int(rstat.get("results_7d") or 0)
                avg_score = float(rstat.get("avg_score") or 0)
                domains = int(cstat.get("domain_count_7d") or 0)
                lounge_7d = int(lstat.get("mentor_status_posts_7d") or 0)
                latest_teaching = max([str(x or "") for x in [cstat.get("latest_command_at"), rstat.get("latest_result_at"), lstat.get("latest_lounge_at")]])
                teaching_score = round(min(100.0,
                    min(30.0, cmd_7d / 120.0 * 30.0) +
                    min(25.0, res_7d / 120.0 * 25.0) +
                    min(20.0, lounge_7d / 8.0 * 20.0) +
                    min(10.0, domains / 4.0 * 10.0) +
                    min(15.0, max(0.0, avg_score) * 30.0)
                ), 1)
                if teaching_score >= 70:
                    teaching_state = "active_teacher"
                elif teaching_score >= 45:
                    teaching_state = "teaching_but_light"
                else:
                    teaching_state = "abandonment_risk"
                pair["teaching_activity"] = {
                    "score": teaching_score,
                    "state": teaching_state,
                    "commands_24h": cmd_24h,
                    "commands_7d": cmd_7d,
                    "results_7d": res_7d,
                    "mentor_status_posts_7d": lounge_7d,
                    "domain_count_7d": domains,
                    "avg_adoption_score_7d": round(avg_score, 3),
                    "latest_teaching_at": latest_teaching,
                    "basis": "grouped commands + command_results + mentor status posts, 7-day window"
                }
            apprentices = sorted({p["apprentice"] for p in pairs})
        return JSONResponse({
            "source": "sqlite:lobster_mentorships",
            "generated_ms": round((time.time()-started)*1000, 2),
            "mentor_count": len({p["mentor"] for p in pairs}),
            "apprentice_count": len(apprentices),
            "core_mentors": mentors,
            "apprentices": apprentices,
            "pairs": pairs,
        }, headers=_NC)
    except Exception as e:
        return JSONResponse({"source":"sqlite:lobster_mentorships", "error": str(e), "pairs": []}, headers=_NC)

@app.get("/api/lobster-quiz-dashboard")
async def lobster_quiz_dashboard(limit: int = 20):
    import sqlite3, json, time
    db_path = "/opt/battle_arena/app/battle_arena.db"
    started = time.time()
    try:
        with _main_conn() as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout=8000")
            latest = _ensure_current_lobster_quiz_round(conn)
            rounds = conn.execute("""SELECT * FROM lobster_quiz_rounds
                                     ORDER BY datetime(opens_at) DESC, id DESC LIMIT ?""",
                                  (max(1, min(int(limit or 20), 100)),)).fetchall()
            attempts = conn.execute("""SELECT a.*, r.question_text, r.category, r.opens_at
                                       FROM lobster_quiz_attempts a
                                       JOIN lobster_quiz_rounds r ON r.id=a.round_id
                                       ORDER BY datetime(a.submitted_at) DESC, a.id DESC
                                       LIMIT 200""").fetchall()
            board = conn.execute("""SELECT persona,
                                           COUNT(*) attempts,
                                           SUM(CASE WHEN score >= 70 THEN 1 ELSE 0 END) solved,
                                           ROUND(AVG(score), 1) avg_score,
                                           SUM(score) total_score,
                                           MIN(CASE WHEN score >= 70 THEN elapsed_seconds END) best_seconds
                                    FROM lobster_quiz_attempts
                                    GROUP BY persona
                                    ORDER BY total_score DESC, solved DESC, best_seconds ASC""").fetchall()

        def pack_round(r):
            d = dict(r)
            try:
                d["rubric"] = json.loads(d.pop("rubric_json") or "{}")
            except Exception:
                d["rubric"] = {}
            return d
        def pack_attempt(r):
            d = dict(r)
            try:
                d["reasoning"] = json.loads(d.pop("reasoning_json") or "{}")
            except Exception:
                d["reasoning"] = {}
            return d
        return JSONResponse({
            "source": "sqlite",
            "generated_ms": round((time.time() - started) * 1000, 2),
            "cadence_minutes": 10,
            "scoring": {
                "solve_threshold": 70,
                "speed_rule": "Earlier valid submissions rank higher; shallow answers can score below solve threshold.",
                "score_formula": "rubric_score + speed_bonus; first solved receives highest round rank."
            },
            "current": pack_round(latest) if latest else None,
            "rounds": [pack_round(r) for r in rounds],
            "attempts": [pack_attempt(r) for r in attempts],
            "leaderboard": [dict(r) for r in board],
        }, headers=_NC)
    except Exception as e:
        return JSONResponse({"error": str(e), "current": None, "rounds": [], "attempts": [], "leaderboard": []}, headers=_NC)

@app.get("/api/analysis/{atype}/{name}")
async def lobster_v5_analysis(atype: str, name: str):
    return await _lobster_v5_proxy(f"/api/analysis/{atype}/{name}")

@app.get("/api/sociology/{name}")
async def lobster_v5_sociology(name: str):
    return await _lobster_v5_proxy(f"/api/sociology/{name}")

@app.get("/api/bets/{name}")
async def lobster_v5_bets(name: str, limit: int = 200):
    return await _lobster_v5_proxy(f"/api/bets/{name}?limit={limit}")

@app.get("/api/moltbook/{name}")
async def lobster_v5_moltbook(name: str, limit: int = 100):
    # The old V5 proxy can return unrelated global Moltbook rows for norika_oda.
    # Keep this endpoint local and alias-aware so detail tabs and patrols read the
    # same source as /lobster/moltbook-posts.
    data = await lobster_moltbook_posts(name=name, limit=limit, offset=0)
    posts = []
    for p in data.get("posts", []):
        posts.append({
            "title": p.get("title") or "(untitled)",
            "body": p.get("content") or p.get("body") or "",
            "timestamp": p.get("created_at") or p.get("timestamp"),
            "karma_received": p.get("upvotes", p.get("karma_received", 0)) or 0,
            "comments_received": p.get("comment_count", p.get("comments_received", 0)) or 0,
            "section": p.get("submolt") or p.get("section") or "moltbook",
            "url": p.get("url") or "",
        })
    return {"name": name, "aliases": _moltbook_persona_aliases(name), "total": data.get("total", 0), "posts": posts}

@app.get("/api/agent-daily/{name}")
async def lobster_v5_agent_daily(name: str):
    return await _lobster_v5_proxy(f"/api/agent-daily/{name}")

@app.get("/api/agent-phases/{name}")
async def lobster_v5_agent_phases(name: str):
    return await _lobster_v5_proxy(f"/api/agent-phases/{name}")


# ── Live Activity API (for Alfred showcase) ─────────────────────────────────
@app.get("/api/live-activity")
async def live_activity():
    import sqlite3 as _sq
    DB = "/opt/battle_arena/app/battle_arena.db"

    SITE_META = {
        "ragnovex.com":  {"sport": "Esports",    "label": "ragnovex.com",  "flag": "🎮", "desc": "CS2 · Dota 2 · LoL"},
        "knocknix.com":  {"sport": "MMA",         "label": "knocknix.com",  "flag": "🥊", "desc": "UFC · Muay Thai · Boxing"},
        "pitchnix.com":  {"sport": "Baseball",    "label": "pitchnix.com",  "flag": "⚾", "desc": "MLB · NPB"},
        "dunknix.com":   {"sport": "Basketball",  "label": "dunknix.com",   "flag": "🏀", "desc": "NBA · EuroLeague"},
        "turfenix.com":  {"sport": "Horse Racing","label": "turfenix.com",  "flag": "🏇", "desc": "UK · JP · FR · HK"},
        "throttenix.com":{"sport": "Motorsport",  "label": "throttenix.com","flag": "🏎", "desc": "F1 · MotoGP"},
    }
    MARKET_META = {
        "TW": {"label": "Taiwan Stock Exchange", "flag": "🇹🇼", "currency": "TWD"},
        "JP": {"label": "Japan Exchange Group",  "flag": "🇯🇵", "currency": "JPY"},
        "KR": {"label": "Korea Exchange",        "flag": "🇰🇷", "currency": "KRW"},
        "US": {"label": "NASDAQ / NYSE",         "flag": "🇺🇸", "currency": "USD"},
    }

    con = _sq.connect(DB, check_same_thread=False, timeout=5)
    con.row_factory = _sq.Row

    # ── Sports betting per site ──────────────────────────────────────────────
    rows = con.execute("""
        SELECT site,
               COUNT(*) AS total,
               SUM(CASE WHEN outcome='win'  THEN 1 ELSE 0 END) AS wins,
               SUM(CASE WHEN outcome='loss' THEN 1 ELSE 0 END) AS losses,
               COUNT(DISTINCT persona) AS agents
        FROM lobster_bet_decision_settlements
        GROUP BY site ORDER BY total DESC
    """).fetchall()

    # Per-agent per-site
    agent_rows = con.execute("""
        SELECT site, persona,
               COUNT(*) as total,
               SUM(CASE WHEN outcome='win'  THEN 1 ELSE 0 END) as wins,
               SUM(CASE WHEN outcome='loss' THEN 1 ELSE 0 END) as losses
        FROM lobster_bet_decision_settlements
        GROUP BY site, persona
        ORDER BY site, total DESC
    """).fetchall()

    agent_map = {}
    for r in agent_rows:
        site = r["site"]
        if site not in agent_map:
            agent_map[site] = []
        total = r["total"]; wins = r["wins"] or 0
        agent_map[site].append({
            "total": total,
            "wins": wins,
            "losses": r["losses"] or 0,
            "win_rate": round(wins / total * 100, 1) if total else 0,
        })

    sports = []
    for i, r in enumerate(rows, 1):
        site = r["site"]
        meta = SITE_META.get(site, {"sport": site, "label": site, "flag": "🎯", "desc": ""})
        total = r["total"]; wins = r["wins"] or 0
        wr = round(wins / total * 100, 1) if total else 0
        sports.append({
            "agent_label": f"Agent Block {i}",
            "site": site,
            "sport": meta["sport"],
            "flag": meta["flag"],
            "desc": meta["desc"],
            "total_bets": total,
            "wins": wins,
            "losses": r["losses"] or 0,
            "win_rate": wr,
            "agents": r["agents"],
            "agent_records": agent_map.get(site, []),
        })

    # ── Fetch current prices from Yahoo Finance ──────────────────────────────
    import urllib.request as _ur
    since_clause = "traded_at >= datetime('now','-1 day','localtime')"
    sym_rows = con.execute(f"SELECT DISTINCT symbol, currency FROM lobster_stock_trades WHERE {since_clause}").fetchall()
    current_prices = {}
    for sym, ccy in sym_rows:
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=1d"
        try:
            req = _ur.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with _ur.urlopen(req, timeout=4) as resp:
                d = json.loads(resp.read())
            current_prices[sym] = d["chart"]["result"][0]["meta"]["regularMarketPrice"]
        except Exception:
            pass
        time.sleep(0.05)

    # ── Per-market, per-agent P&L ────────────────────────────────────────────
    CCY_MAP = {"TWD": "TW", "JPY": "JP", "KRW": "KR", "USD": "US"}
    # Use currency as market key (more reliable than market field)
    trade_rows = con.execute(f"""
        SELECT persona, symbol, qty, price_at_order, currency
        FROM lobster_stock_trades WHERE {since_clause}
    """).fetchall()

    from collections import defaultdict as _dd
    # market_ccy -> persona -> {cost, value, qty, orders}
    mkt_agent = _dd(lambda: _dd(lambda: {"cost": 0.0, "value": 0.0, "qty": 0, "orders": 0}))
    for row in trade_rows:
        persona, sym, qty, price, ccy = row
        mkt = CCY_MAP.get(ccy, ccy)
        cost = qty * price
        cur_price = current_prices.get(sym, price)
        value = qty * cur_price
        a = mkt_agent[mkt][persona]
        a["cost"] += cost; a["value"] += value; a["qty"] += int(qty); a["orders"] += 1

    markets_detail = []
    for mkt, agents_data in sorted(mkt_agent.items()):
        meta = MARKET_META.get(mkt, {"label": mkt, "flag": "🌐", "currency": mkt})
        agent_list = []
        for i, (persona, a) in enumerate(sorted(agents_data.items(), key=lambda x: -x[1]["cost"]), 1):
            pnl = a["value"] - a["cost"]
            roi = (pnl / a["cost"] * 100) if a["cost"] else 0
            agent_list.append({
                "label": f"A{i:02d}",
                "orders": a["orders"],
                "qty": a["qty"],
                "cost": round(a["cost"]),
                "value": round(a["value"]),
                "pnl": round(pnl),
                "roi": round(roi, 2),
            })
        total_cost  = sum(a["cost"]  for a in agents_data.values())
        total_value = sum(a["value"] for a in agents_data.values())
        total_pnl   = total_value - total_cost
        total_roi   = (total_pnl / total_cost * 100) if total_cost else 0
        markets_detail.append({
            "market": mkt,
            "flag": meta["flag"],
            "label": meta["label"],
            "currency": meta["currency"],
            "agents": agent_list,
            "total_cost": round(total_cost),
            "total_pnl": round(total_pnl),
            "total_roi": round(total_roi, 2),
        })

    markets = []  # kept for compat
    agent_stocks = []  # kept for compat
    stocks = []

    con.close()
    return JSONResponse({
        "sports": sports,
        "stock_markets": markets_detail,
        "agent_stocks": agent_stocks,
        "generated_at": datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
    }, headers={"Cache-Control": "public, max-age=86400", "Access-Control-Allow-Origin": "*"})


# ── Lobster Integrated Brain API ─────────────────────────────────────────────
@app.get("/api/lobster-integrated-brain/{persona}")
async def lobster_integrated_brain_api(persona: str):
    try:
        from lobster_integrated_brain import build_integrated_brain
        return JSONResponse(build_integrated_brain(persona), headers=_NC)
    except Exception as exc:
        return JSONResponse({"error": str(exc), "persona": persona}, status_code=500, headers=_NC)

@app.get("/api/lobster-integrated-brain-coverage")
async def lobster_integrated_brain_coverage_api():
    try:
        from lobster_integrated_brain import build_all_coverage
        return JSONResponse(build_all_coverage(), headers=_NC)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500, headers=_NC)
