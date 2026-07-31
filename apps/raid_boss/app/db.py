"""CardHouse — SQLite 持久化：對戰結果、玩家排行、旁觀下注"""
import sqlite3, time, os

DB_PATH = os.path.join(os.path.dirname(__file__), "battle_arena.db")


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS match_results (
            id TEXT PRIMARY KEY,
            player_a TEXT NOT NULL,
            player_b TEXT NOT NULL,
            winner TEXT,
            rounds INTEGER DEFAULT 0,
            initial_bet INTEGER DEFAULT 0,
            final_pot INTEGER DEFAULT 0,
            created_at INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS player_styles (
            player TEXT PRIMARY KEY,
            style TEXT NOT NULL DEFAULT 'ATK',
            style_label TEXT NOT NULL DEFAULT '純主攻'
        );
        CREATE TABLE IF NOT EXISTS spectator_bets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT NOT NULL,
            bettor TEXT NOT NULL,
            on_side TEXT NOT NULL,
            amount INTEGER NOT NULL,
            odds REAL NOT NULL,
            settled INTEGER DEFAULT 0,
            payout INTEGER DEFAULT 0,
            created_at INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS player_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player TEXT NOT NULL,
            item_type TEXT NOT NULL,
            quantity INTEGER NOT NULL DEFAULT 1,
            earned_at INTEGER DEFAULT 0
        );
        CREATE UNIQUE INDEX IF NOT EXISTS idx_player_item ON player_items(player, item_type);
        """)


def save_match(match_id: str, player_a: str, player_b: str, winner: str,
               rounds: int, initial_bet: int, final_pot: int):
    try:
        with _conn() as c:
            c.execute(
                "INSERT OR REPLACE INTO match_results VALUES (?,?,?,?,?,?,?,?)",
                (match_id, player_a, player_b, winner, rounds, initial_bet, final_pot, int(time.time()))
            )
    except Exception as e:
        print(f"[db] save_match error: {e}")


def get_player_stats(username: str) -> dict:
    """單一玩家歷史勝率"""
    try:
        with _conn() as c:
            rows = c.execute("""
                SELECT SUM(is_win) w, SUM(is_loss) l, SUM(is_draw) d, COUNT(*) t
                FROM (
                    SELECT CASE WHEN winner='A' THEN 1 ELSE 0 END is_win,
                           CASE WHEN winner='B' THEN 1 ELSE 0 END is_loss,
                           CASE WHEN winner LIKE 'DRAW%' THEN 1 ELSE 0 END is_draw
                    FROM match_results WHERE player_a=? AND player_a NOT IN ('AI','TBD')
                    UNION ALL
                    SELECT CASE WHEN winner='B' THEN 1 ELSE 0 END,
                           CASE WHEN winner='A' THEN 1 ELSE 0 END,
                           CASE WHEN winner LIKE 'DRAW%' THEN 1 ELSE 0 END
                    FROM match_results WHERE player_b=? AND player_b NOT IN ('AI','TBD')
                )
            """, (username, username)).fetchone()
            t = rows["t"] or 0
            w = rows["w"] or 0
            return {"wins": w, "losses": rows["l"] or 0, "draws": rows["d"] or 0,
                    "total": t, "win_rate": round(w / t, 3) if t else 0.5}
    except Exception as e:
        print(f"[db] get_player_stats error: {e}")
        return {"wins": 0, "losses": 0, "draws": 0, "total": 0, "win_rate": 0.5}


def calc_odds(win_rate: float, house_edge: float = 0.03) -> float:
    """勝率 → 賠率（含 3% 平台抽水）"""
    wr = max(0.05, min(0.95, win_rate))
    return round(min(10.0, (1 / wr) * (1 - house_edge)), 2)


def upsert_player_style(player: str, style: str, style_label: str):
    try:
        with _conn() as c:
            c.execute("INSERT OR REPLACE INTO player_styles VALUES (?,?,?)",
                      (player, style, style_label))
    except Exception as e:
        print(f"[db] upsert_player_style error: {e}")


def get_leaderboard(limit: int = 20, mode: str = "alltime") -> list:
    try:
        since_cond = f"AND created_at >= {int(time.time()) - 86400}" if mode == "daily" else ""
        with _conn() as c:
            rows = c.execute(f"""
                SELECT base.player, base.wins, base.losses, base.draws, base.total, base.total_pit,
                       COALESCE(ps.style,'ATK') style,
                       COALESCE(ps.style_label,'純主攻') style_label
                FROM (
                  SELECT player,
                    SUM(is_win) wins, SUM(is_loss) losses, SUM(is_draw) draws,
                    COUNT(*) total, SUM(pit) total_pit
                  FROM (
                    SELECT player_a player,
                        CASE WHEN winner='A' THEN 1 ELSE 0 END is_win,
                        CASE WHEN winner='B' THEN 1 ELSE 0 END is_loss,
                        CASE WHEN winner LIKE 'DRAW%' THEN 1 ELSE 0 END is_draw,
                        CASE WHEN winner='A' THEN final_pot WHEN winner='B' THEN -initial_bet ELSE 0 END pit
                    FROM match_results {since_cond.replace('AND','WHERE',1) if not since_cond.startswith('AND') else 'WHERE 1=1 ' + since_cond}
                    WHERE player_a NOT IN ('AI','TBD') AND player_a NOT LIKE 'AI·%'
                    UNION ALL
                    SELECT player_b,
                        CASE WHEN winner='B' THEN 1 ELSE 0 END,
                        CASE WHEN winner='A' THEN 1 ELSE 0 END,
                        CASE WHEN winner LIKE 'DRAW%' THEN 1 ELSE 0 END,
                        CASE WHEN winner='B' THEN final_pot WHEN winner='A' THEN -initial_bet ELSE 0 END
                    FROM match_results WHERE player_b NOT IN ('AI','TBD') AND player_b NOT LIKE 'AI·%' {since_cond}
                  )
                  WHERE player NOT IN ('AI','TBD') AND player NOT LIKE 'AI·%'
                  GROUP BY player
                ) base
                LEFT JOIN player_styles ps ON base.player = ps.player
                ORDER BY base.wins DESC, base.total DESC LIMIT ?
            """, (limit,)).fetchall()
            out = []
            for i, r in enumerate(rows):
                t = r["total"] or 1
                w = r["wins"] or 0
                wr = round(w / t, 3)
                out.append({
                    "rank": i + 1, "player": r["player"],
                    "wins": w, "losses": r["losses"] or 0,
                    "draws": r["draws"] or 0, "total": t,
                    "win_rate": wr, "total_pit": r["total_pit"] or 0,
                    "odds": calc_odds(wr),
                    "style": r["style"], "style_label": r["style_label"],
                })
            return out
    except Exception as e:
        print(f"[db] get_leaderboard error: {e}")
        return []


def save_spectator_bet(match_id: str, bettor: str, on_side: str,
                       amount: int, odds: float) -> int:
    with _conn() as c:
        cur = c.execute(
            "INSERT INTO spectator_bets(match_id,bettor,on_side,amount,odds,created_at) VALUES(?,?,?,?,?,?)",
            (match_id, bettor, on_side, amount, odds, int(time.time()))
        )
        return cur.lastrowid


def get_spectator_bets_by_match(match_id: str) -> list:
    """取得指定對戰的所有旁觀下注（for XP 上報）"""
    try:
        with _conn() as c:
            rows = c.execute(
                "SELECT bettor, on_side, amount, odds, payout, settled FROM spectator_bets WHERE match_id=?",
                (match_id,)
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        print(f"[db] get_spectator_bets_by_match error: {e}")
        return []


def settle_spectator_bets(match_id: str, winner_side: str) -> list:
    """結算旁觀下注（A贏 → winner_side='a'，平局全退）
    Returns list of {"bettor": str, "payout": int} for payout > 0 (caller must transfer Pit)."""
    payouts = []
    try:
        with _conn() as c:
            bets = c.execute(
                "SELECT * FROM spectator_bets WHERE match_id=? AND settled=0", (match_id,)
            ).fetchall()
            for b in bets:
                if winner_side in ("draw", "draw_ko"):
                    payout = b["amount"]  # 平局退還本金
                elif b["on_side"] == winner_side:
                    payout = int(b["amount"] * b["odds"])
                else:
                    payout = 0
                c.execute("UPDATE spectator_bets SET settled=1,payout=? WHERE id=?",
                          (payout, b["id"]))
                if payout > 0:
                    payouts.append({"bettor": b["bettor"], "payout": payout, "ref_id": str(b["id"])})
    except Exception as e:
        print(f"[db] settle_spectator_bets error: {e}")
    return payouts


ORPHAN_VOID_AFTER_SEC = 3600  # 1 hour: matches never go this long


def sweep_orphan_spectator_bets() -> list:
    """比賽 >1h 前下注但沒 match_result → 視為中斷，退本金作廢。
    Returns list of {"bettor": str, "payout": int} for caller to transfer Pit."""
    import time
    cutoff = int(time.time()) - ORPHAN_VOID_AFTER_SEC
    payouts = []
    try:
        with _conn() as c:
            rows = c.execute(
                """SELECT sb.id, sb.bettor, sb.amount
                     FROM spectator_bets sb
                     LEFT JOIN match_results mr ON mr.id = sb.match_id
                    WHERE sb.settled=0 AND sb.created_at < ? AND mr.id IS NULL""",
                (cutoff,),
            ).fetchall()
            for r in rows:
                c.execute(
                    "UPDATE spectator_bets SET settled=1, payout=? WHERE id=?",
                    (r["amount"], r["id"]),
                )
                payouts.append({"bettor": r["bettor"], "payout": r["amount"], "ref_id": f"orphan_{r['id']}"})
    except Exception as e:
        print(f"[db] sweep_orphan_spectator_bets error: {e}")
    return payouts


# ─── Item persistence ────────────────────────────────────────────────────────

ITEM_TYPES = {"heal_hp", "heal_mp", "double_atk", "skip_opp", "scramble"}

def get_player_style(player: str) -> dict:
    try:
        with _conn() as c:
            row = c.execute(
                "SELECT style, style_label FROM player_styles WHERE player=?", (player,)
            ).fetchone()
            if row:
                return {"style": row["style"], "style_label": row["style_label"]}
            return {"style": "ATK", "style_label": "純主攻"}
    except Exception as e:
        print(f"[db] get_player_style error: {e}")
        return {"style": "ATK", "style_label": "純主攻"}


def get_items(player: str) -> dict:
    try:
        with _conn() as c:
            rows = c.execute(
                "SELECT item_type, quantity FROM player_items WHERE player=? AND quantity>0",
                (player,)
            ).fetchall()
            return {r["item_type"]: r["quantity"] for r in rows}
    except Exception as e:
        print(f"[db] get_items error: {e}")
        return {}

def add_item(player: str, item_type: str, qty: int = 1):
    if item_type not in ITEM_TYPES:
        return
    try:
        with _conn() as c:
            c.execute("""
                INSERT INTO player_items(player, item_type, quantity, earned_at)
                VALUES (?,?,?,?)
                ON CONFLICT(player, item_type)
                DO UPDATE SET quantity=quantity+excluded.quantity,
                              earned_at=excluded.earned_at
            """, (player, item_type, qty, int(time.time())))
    except Exception as e:
        print(f"[db] add_item error: {e}")

def use_item(player: str, item_type: str) -> bool:
    try:
        with _conn() as c:
            # Atomic decrement: only succeeds if quantity > 0, eliminates race condition
            cur = c.execute(
                "UPDATE player_items SET quantity=quantity-1 "
                "WHERE player=? AND item_type=? AND quantity > 0",
                (player, item_type)
            )
            return cur.rowcount > 0
    except Exception as e:
        print(f"[db] use_item error: {e}")
        return False


def get_spectator_bets(username: str) -> list:
    try:
        with _conn() as c:
            rows = c.execute("""
                SELECT sb.*, mr.player_a, mr.player_b, mr.winner
                FROM spectator_bets sb
                LEFT JOIN match_results mr ON sb.match_id = mr.id
                WHERE sb.bettor = ?
                ORDER BY sb.created_at DESC LIMIT 50
            """, (username,)).fetchall()
            return [{
                "id": r["id"], "match_id": r["match_id"],
                "on_side": r["on_side"], "amount": r["amount"],
                "odds": r["odds"], "settled": bool(r["settled"]),
                "payout": r["payout"], "created_at": r["created_at"],
                "player_a": r["player_a"], "player_b": r["player_b"],
                "winner": r["winner"],
            } for r in rows]
    except Exception as e:
        print(f"[db] get_spectator_bets error: {e}")
        return []


XP_LEVEL_THRESH = [0, 100, 250, 500, 900, 1400, 2100, 3000, 4200, 6000, 8500]

def get_player_xp(username: str) -> dict:
    try:
        with _conn() as c:
            rows = c.execute("""
                SELECT winner, initial_bet, player_a, player_b
                FROM match_results
                WHERE (player_a=? OR player_b=?)
                  AND player_a NOT IN ('AI','TBD') AND player_b NOT IN ('AI','TBD')
            """, (username, username)).fetchall()
            xp = 0
            cur_streak = 0
            for r in rows:
                is_a = r["player_a"] == username
                won = (r["winner"] == "A" and is_a) or (r["winner"] == "B" and not is_a)
                draw = r["winner"] and r["winner"].startswith("DRAW")
                bet = r["initial_bet"] or 0
                if won:
                    xp += 50 + min(50, bet // 100)
                    cur_streak = max(0, cur_streak) + 1
                elif draw:
                    xp += 25
                    cur_streak = 0
                else:
                    xp += 10
                    cur_streak = min(0, cur_streak) - 1
            lv = 1
            for i, thresh in enumerate(XP_LEVEL_THRESH):
                if xp >= thresh:
                    lv = i + 1
            lv = min(lv, len(XP_LEVEL_THRESH))
            cur_base = XP_LEVEL_THRESH[lv - 1]
            next_base = XP_LEVEL_THRESH[lv] if lv < len(XP_LEVEL_THRESH) else xp + 1000
            xp_in_lv = xp - cur_base
            xp_to_next = next_base - cur_base
            pct = round(min(100, xp_in_lv / max(1, xp_to_next) * 100), 1)
            return {"total_xp": xp, "level": lv, "xp_in_level": xp_in_lv,
                    "xp_to_next": xp_to_next, "progress_pct": pct, "streak": cur_streak}
    except Exception as e:
        print(f"[db] get_player_xp error: {e}")
        return {"total_xp": 0, "level": 1, "xp_in_level": 0, "xp_to_next": 100,
                "progress_pct": 0.0, "streak": 0}


init_db()
