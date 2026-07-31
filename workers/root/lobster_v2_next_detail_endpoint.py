#!/usr/bin/env python3
"""Inject /api/lobster-v2-next-detail/{persona} + /lobster/dashboard/v2-next/{persona} into main.py"""
import pathlib

p = pathlib.Path('/opt/battle_arena/app/main.py')
src = p.read_text(encoding='utf-8')

if 'lobster-v2-next-detail' in src:
    print('SKIP: already added')
    raise SystemExit(0)

anchor = '@app.get("/lobster/dashboard/v2-next", response_class=HTMLResponse)'
if anchor not in src:
    raise SystemExit('ANCHOR not found')

# Insert NEW endpoint+route BEFORE the v2-next homepage route
inject = r'''@app.get("/api/lobster-v2-next-detail/{persona}")
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


@app.get("/lobster/dashboard/v2-next/{persona}", response_class=HTMLResponse)
async def lobster_dashboard_v2_next_persona(persona: str):
    html_path = _os.path.join(_os.path.dirname(__file__), "../static/lobster-dashboard-v2-next-detail.html")
    try:
        return HTMLResponse(open(html_path).read(), headers=_NC)
    except FileNotFoundError:
        return HTMLResponse("<h1>V2-NEXT detail not found</h1>", status_code=404, headers=_NC)


'''

src2 = src.replace(anchor, inject + anchor, 1)
p.write_text(src2, encoding='utf-8')
print(f'OK: +{len(src2)-len(src)} bytes')
