#!/usr/bin/env python3
"""Inject V2-NEXT bundle endpoint + route into /opt/battle_arena/app/main.py"""
import pathlib

p = pathlib.Path('/opt/battle_arena/app/main.py')
src = p.read_text(encoding='utf-8')

if 'lobster-dashboard-v2-next-bundle' in src:
    print('SKIP: v2-next endpoint already added')
    raise SystemExit(0)

anchor = '@app.get("/lobster/dashboard/v2", response_class=HTMLResponse)'
if anchor not in src:
    raise SystemExit('ANCHOR not found')

new = r'''@app.get("/api/lobster-dashboard-v2-next-bundle")
async def lobster_dashboard_v2_next_bundle():
    """Observatory bundle for 21 lobsters — aggregates memory + DB + task completion."""
    import json as _json
    import os as _osmod
    import glob as _glob
    import sqlite3 as _sql
    import time as _time
    from datetime import datetime as _dt
    SKIP = {"norika", "system", "deleted-lobsters", "lobster"}
    MBTI = {
        'hexclaw':'ISFJ','pincerbot':'ESTJ','norika_oda':'INTJ','vortexiq':'INFP',
        'snaplex':'INTJ','prismbit':'INFJ','clawtrix':'ENTP','ragclaw':'ESTP',
        'blazepaw':'ENFP','stonefang':'ISTP','emberling':'INFP','sparkrill':'ENTP',
        'gauntlet':'ENTJ','rushfin':'ESFP','nullpinch':'INTP','brineledger':'ISFJ',
        'omenwake':'ENFJ','mirrorcalm':'INFJ','flarecub':'ENFJ','basaltrook':'ISTJ',
        'elder':'INFJ',
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

    for path in sorted(_glob.glob('/root/lobster-memory/*.json')):
        name = _osmod.path.basename(path).replace('.json', '')
        if name in SKIP:
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
            'soulParams': {k: round(_fnum(v), 2) for k, v in (mem.get('soulParams') or {}).items() if k != 'bonds'},
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
    try:
        con = _sql.connect(db_path, timeout=5)
        con.row_factory = _sql.Row
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
    return JSONResponse({
        'engine': 'lobster-dashboard-v2-next-bundle',
        'version': 1,
        'generated_at': _time.strftime('%Y-%m-%dT%H:%M:%S+08:00', _time.localtime()),
        'global': {
            'total_lobsters': total,
            'with_occupation': sum(1 for l in lobsters.values() if l['occupation']),
            'deletion_candidates': deletion_candidates,
            'stale_lobsters': stale_list,
            'in_crisis': crisis_list,
            'in_ritual': in_ritual_list,
            'today_tasks': {k: {'done': v, 'pct': round(100*v/max(1,total), 0)} for k, v in today_tasks.items()},
            'quarantine_24h': quarantine_24h,
            'recent_divine_events': recent_divine,
            'brain_speech_stats': {'ok': brain_ok, 'fallback': brain_fallback, 'error': brain_error},
            'free_path_lobsters': [n for n, l in lobsters.items() if l['occupation']],
        },
        'lobsters': lobsters,
    }, headers=_NC)


@app.get("/lobster/dashboard/v2-next", response_class=HTMLResponse)
async def lobster_dashboard_v2_next():
    html_path = _os.path.join(_os.path.dirname(__file__), "../static/lobster-dashboard-v2-next.html")
    try:
        return HTMLResponse(open(html_path).read(), headers=_NC)
    except FileNotFoundError:
        return HTMLResponse("<h1>V2-NEXT Dashboard not found</h1>", status_code=404, headers=_NC)


'''

src2 = src.replace(anchor, new + anchor, 1)
p.write_text(src2, encoding='utf-8')
print(f'OK: +{len(src2)-len(src)} bytes')
