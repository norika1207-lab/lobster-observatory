#!/usr/bin/env python3
"""真實股價結算引擎 (norika 2026-06-07)
目的: 把龍蝦 19475 筆 filled 股票交易,用 yfinance 真實歷史收盤價重新結算,
找出 25 天 (5/11~today) 真正會研究股市、勝率高的龍蝦。

關鍵修正: 原 price_at_order 是假價 (2330.TW 記 57 而非 2365),
改用每筆 traded_at 當天的真實收盤價當買入/賣出價。

跨幣別 (USD/JPY/TWD/KRW) 用「每筆報酬率%」統一度量,equal-weight,
這樣比的是選股能力,不被幣別金額大小汙染。

輸出:
- 寫回新表 lobster_stock_scoreboard
- stdout 印真實勝率排行
"""
import sqlite3, json, sys, datetime as dt, time
from collections import defaultdict, deque
import yfinance as yf

DB = '/opt/battle_arena/app/battle_arena.db'
con = sqlite3.connect(DB, timeout=120)
con.row_factory = sqlite3.Row
con.execute("PRAGMA journal_mode=WAL")
con.execute("PRAGMA busy_timeout=120000")
con.execute("PRAGMA synchronous=NORMAL")

rows = con.execute("""
    SELECT persona, symbol, action, qty, currency, traded_at, confidence
    FROM lobster_stock_trades
    WHERE order_status='filled'
    ORDER BY traded_at ASC
""").fetchall()
print(f"filled trades: {len(rows)}", flush=True)

syms = sorted({r['symbol'] for r in rows})
print(f"unique symbols: {len(syms)}", flush=True)

# ── 抓歷史日K ──────────────────────────────────────────────
start = '2026-05-08'
end = (dt.date.today() + dt.timedelta(days=1)).isoformat()
print(f"downloading {start} ~ {end} ...", flush=True)
data = yf.download(syms, start=start, end=end, progress=False,
                   group_by='ticker', auto_adjust=True, threads=True)

price = {}   # symbol -> {date_iso: close}
for s in syms:
    try:
        df = data[s] if len(syms) > 1 else data
        ser = df['Close'].dropna()
        price[s] = {d.date().isoformat(): float(c) for d, c in ser.items()}
    except Exception as e:
        price[s] = {}
miss = [s for s in syms if not price[s]]
print(f"symbols with price: {len(syms)-len(miss)}/{len(syms)}; missing={miss[:20]}", flush=True)


def close_on_or_before(sym, datestr):
    days = sorted(price.get(sym, {}).keys())
    pick = None
    for d in days:
        if d <= datestr:
            pick = d
        else:
            break
    return price[sym].get(pick) if pick else None


def latest_close(sym):
    days = sorted(price.get(sym, {}).keys())
    return price[sym].get(days[-1]) if days else None


# ── FIFO 結算 (每 persona+symbol 一條 buy 佇列) ──────────────
books = defaultdict(deque)        # (persona,symbol) -> deque[(qty, buy_price, conf)]
closed = defaultdict(list)        # persona -> [report_return_pct, ...]
closed_conf = defaultdict(list)   # persona -> [(ret, conf)]
skipped = 0

for r in rows:
    persona, sym, action, qty = r['persona'], r['symbol'], r['action'], float(r['qty'] or 0)
    day = (r['traded_at'] or '')[:10]
    px = close_on_or_before(sym, day)
    if px is None or qty <= 0:
        skipped += 1
        continue
    key = (persona, sym)
    if action == 'buy':
        books[key].append([qty, px, r['confidence'] or 'na'])
    elif action == 'sell':
        remain = qty
        while remain > 1e-9 and books[key]:
            lot = books[key][0]
            take = min(remain, lot[0])
            ret = (px - lot[1]) / lot[1] * 100.0
            closed[persona].append(ret)
            closed_conf[persona].append((ret, lot[2]))
            lot[0] -= take
            remain -= take
            if lot[0] <= 1e-9:
                books[key].popleft()

# ── 未平倉 mark-to-market ──────────────────────────────────
open_ret = defaultdict(list)
for (persona, sym), lots in books.items():
    cur = latest_close(sym)
    if cur is None:
        continue
    for qty, bp, conf in lots:
        if qty > 1e-9:
            open_ret[persona].append((cur - bp) / bp * 100.0)

# ── 彙整每隻龍蝦 ───────────────────────────────────────────
personas = sorted(set(list(closed.keys()) + list(open_ret.keys())))
board = []
for p in personas:
    cl = closed.get(p, [])
    op = open_ret.get(p, [])
    n_closed = len(cl)
    wins = sum(1 for x in cl if x > 0)
    win_rate = (wins / n_closed * 100.0) if n_closed else 0.0
    avg_closed = (sum(cl) / n_closed) if n_closed else 0.0
    n_open = len(op)
    avg_open = (sum(op) / n_open) if n_open else 0.0
    allr = cl + op
    avg_all = (sum(allr) / len(allr)) if allr else 0.0
    # high-confidence 子集勝率 (敢重壓又對 = 真會)
    hc = [ret for ret, conf in closed_conf.get(p, []) if str(conf).lower() == 'high']
    hc_win = (sum(1 for x in hc if x > 0) / len(hc) * 100.0) if hc else 0.0
    board.append({
        'persona': p, 'closed': n_closed, 'wins': wins,
        'win_rate': round(win_rate, 1), 'avg_closed_ret': round(avg_closed, 2),
        'open': n_open, 'avg_open_ret': round(avg_open, 2),
        'avg_all_ret': round(avg_all, 2),
        'hc_closed': len(hc), 'hc_win_rate': round(hc_win, 1),
    })

# ── 寫回 scoreboard 表 ─────────────────────────────────────
def write_scoreboard_with_retry(board):
    last = None
    for attempt in range(1, 7):
        try:
            con.execute("BEGIN IMMEDIATE")
            con.execute("""
            CREATE TABLE IF NOT EXISTS lobster_stock_scoreboard (
              persona TEXT PRIMARY KEY,
              closed_trades INTEGER, wins INTEGER, win_rate REAL,
              avg_closed_ret REAL, open_positions INTEGER, avg_open_ret REAL,
              avg_all_ret REAL, hc_closed INTEGER, hc_win_rate REAL,
              computed_at TEXT DEFAULT (datetime('now'))
            )""")
            con.execute("DELETE FROM lobster_stock_scoreboard")
            for b in board:
                con.execute("""INSERT INTO lobster_stock_scoreboard
                   (persona,closed_trades,wins,win_rate,avg_closed_ret,open_positions,avg_open_ret,avg_all_ret,hc_closed,hc_win_rate)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                   (b['persona'], b['closed'], b['wins'], b['win_rate'], b['avg_closed_ret'],
                    b['open'], b['avg_open_ret'], b['avg_all_ret'], b['hc_closed'], b['hc_win_rate']))
            con.commit()
            return
        except sqlite3.OperationalError as e:
            last = e
            try:
                con.rollback()
            except Exception:
                pass
            if "locked" not in str(e).lower() and "busy" not in str(e).lower():
                raise
            wait = min(20, 2 * attempt)
            print(f"[stock-settle] DB busy on scoreboard write attempt={attempt}; retry in {wait}s", file=sys.stderr, flush=True)
            time.sleep(wait)
    raise last

write_scoreboard_with_retry(board)

print(f"\nskipped (no price/qty): {skipped}")
print(f"\n{'='*92}")
print("真實勝率排行 (依已平倉勝率,需 closed>=10 才算有效樣本)")
print(f"{'龍蝦':14s} {'平倉':>5s} {'勝率%':>7s} {'平均報酬%':>9s} {'未平倉':>5s} {'未實現%':>8s} {'總報酬%':>8s} {'高信心勝率%':>10s}")
print('-'*92)
ranked = sorted(board, key=lambda b: (b['win_rate'] if b['closed'] >= 10 else -1, b['avg_all_ret']), reverse=True)
for b in ranked:
    flag = '' if b['closed'] >= 10 else '  (樣本不足)'
    print(f"{b['persona']:14s} {b['closed']:>5d} {b['win_rate']:>7.1f} {b['avg_closed_ret']:>9.2f} "
          f"{b['open']:>5d} {b['avg_open_ret']:>8.2f} {b['avg_all_ret']:>8.2f} {b['hc_win_rate']:>10.1f}{flag}")
con.close()
print(f"\n寫入 lobster_stock_scoreboard 完成,{len(board)} 隻龍蝦")
