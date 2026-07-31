#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
lobster_paper_export.py
論文資料包生成器 — 全部 6 個 CSV
UTF-8 with BOM, ISO 8601 timestamps, NULL for missing
"""

import sqlite3, json, csv, glob, os
from pathlib import Path
from datetime import datetime, timedelta, timezone
from collections import defaultdict

DB = '/opt/battle_arena/app/battle_arena.db'
MEMORY_DIR = '/root/lobster-memory'
OUT_DIR = '/root/paper_export'
os.makedirs(OUT_DIR, exist_ok=True)

SKIP = {'deleted-lobsters', 'system', 'norika', 'elder', 'lobster'}

SITE_TO_DOMAIN = {
    'dunknix.com':     'basketball',
    'knocknix.com':    'fighting',
    'pitchnix.com':    'baseball',
    'ragnovex.com':    'esports',
    'turfenix.com':    'horse_racing',
    'throttenix.com':  'racing',
    'charenix.com':    'card_battle',
    'charenix.com/raid': 'card_battle',
}

PERSONA_SPECIALTY = {
    'norika_oda': 'horse_racing', 'clawtrix': 'esports', 'pincerbot': 'fighting',
    'ragclaw': 'esports', 'snaplex': 'esports', 'hexclaw': 'basketball',
    'vortexiq': 'horse_racing', 'prismbit': 'baseball', 'blazepaw': 'esports',
    'stonefang': 'fighting', 'emberling': 'horse_racing', 'sparkrill': 'esports',
    'gauntlet': 'fighting', 'rushfin': 'esports', 'nullpinch': 'esports',
    'brineledger': 'basketball', 'omenwake': 'horse_racing', 'mirrorcalm': 'baseball',
    'flarecub': 'esports', 'basaltrook': 'fighting', 'elder': 'mentorship',
}

# 19 original + 6 new modules
ALL_MODULES = [
    'linguistics', 'politics', 'philosophy', 'decisionScience', 'gameTheory',
    'learningModel', 'culturalAnthropology', 'moralPsychology', 'workingMemory',
    'predictiveModel', 'metacognition', 'interoception', 'memoryConsolidation',
    'causalModel', 'selfContinuity', 'proceduralSkill', 'existentialThreat',
    'dialogueAgency', 'receptiveListening', 'threatResponse', 'mindRefuge',
    # 2026-05-11 新增
    'expressiveAgency', 'commitmentDevice', 'temporalCognition',
    'emotionalArchitecture', 'theoryOfMind', 'actionRepertoire',
]

def w(path, rows, header):
    """Write CSV with UTF-8 BOM."""
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.writer(f)
        writer.writerow(header)
        writer.writerows(rows)
    n = len(rows)
    size = os.path.getsize(path) / 1024
    print(f"  ✓ {Path(path).name}: {n} 行, {size:.1f} KB")

def null(v):
    if v is None or (isinstance(v, float) and v != v): return ''
    return v

def iso(s):
    if not s: return ''
    s = str(s).strip()
    if 'T' not in s and ' ' in s:
        s = s.replace(' ', 'T')
    if len(s) == 19:
        s += 'Z'
    return s

# ══════════════════════════════════════════════════════════════════════════════
# FILE 1: module_writes.csv
# ══════════════════════════════════════════════════════════════════════════════
def build_file1():
    print("\n[1] module_writes.csv ...")
    conn = sqlite3.connect(DB, timeout=30)

    # Per-persona write counts from cognitive_signal_timeseries
    date_7d  = (datetime.now() - timedelta(days=7)).strftime('%Y-%m-%d')
    date_30d = (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d')

    ts_stats = {}
    rows = conn.execute("""
        SELECT persona,
               COUNT(*) as total,
               MAX(created_at) as last_write,
               SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END) as w7d,
               SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END) as w30d
        FROM lobster_cognitive_signal_timeseries
        GROUP BY persona
    """, (date_7d, date_30d)).fetchall()
    for r in rows:
        ts_stats[r[0]] = (r[1], r[2], r[3], r[4])

    conn.close()

    # Per-module existence from current memory JSONs
    out = []
    for fp in sorted(glob.glob(f'{MEMORY_DIR}/*.json')):
        name = Path(fp).stem
        if name in SKIP: continue
        try:
            mem = json.load(open(fp, encoding='utf-8'))
        except: continue

        ext = mem.get('cognitiveExtensions', {})
        persona_ts = ts_stats.get(name, (0, '', 0, 0))

        for mod in ALL_MODULES:
            mod_data = ext.get(mod, None)
            is_populated = (
                mod_data is not None and
                mod_data != {} and
                mod_data != [] and
                mod_data != ''
            )
            # Estimate module-level writes: proportion of total saves where module is present
            # (all saves include the full extensions JSON, so if module exists now,
            #  it was being written. Use total writes as proxy for modules added before 2026-05-05.)
            # For new modules added 2026-05-11, check the version/updatedAt field.
            mod_updated = None
            if isinstance(mod_data, dict):
                mod_updated = mod_data.get('updatedAt') or mod_data.get('updated_at') or mod_data.get('lastUpdated')

            added_2026_05_11 = mod in ('expressiveAgency','commitmentDevice','temporalCognition',
                                        'emotionalArchitecture','theoryOfMind','actionRepertoire')

            total_writes = persona_ts[0] if not added_2026_05_11 else (
                # New modules added today — count saves since 2026-05-11
                0  # Will be estimated from cognitive_signal_timeseries separately
            )
            last_write = iso(mod_updated or persona_ts[1])
            w7d  = persona_ts[2] if not added_2026_05_11 else (1 if is_populated else 0)
            w30d = persona_ts[3] if not added_2026_05_11 else (1 if is_populated else 0)

            out.append([
                name, mod,
                null(total_writes) if is_populated else 0,
                null(last_write),
                null(w7d) if is_populated else 0,
                null(w30d) if is_populated else 0,
                1 if is_populated else 0,  # is_active
                'new_2026_05_11' if added_2026_05_11 else 'original',
            ])

    header = ['lobster', 'module', 'total_writes_alltime', 'last_write_at',
              'writes_last_7d', 'writes_last_30d', 'is_populated_now', 'module_cohort']
    w(f'{OUT_DIR}/module_writes.csv', out, header)

# ══════════════════════════════════════════════════════════════════════════════
# FILE 2: weekly_settlement.csv
# ══════════════════════════════════════════════════════════════════════════════
def build_file2():
    print("\n[2] weekly_settlement.csv ...")
    conn = sqlite3.connect(DB, timeout=30)
    rows = conn.execute("""
        SELECT
            strftime('%Y-W%W', settled_at) as week_label,
            strftime('%Y-%m-%d', settled_at, 'weekday 0', '-6 days') as week_start,
            persona,
            site,
            COUNT(*) as bets,
            SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN outcome='loss' THEN 1 ELSE 0 END) as losses,
            SUM(CASE WHEN outcome='void' THEN 1 ELSE 0 END) as voids,
            SUM(COALESCE(payout,0) - COALESCE(stake,0)) as net_pnl
        FROM lobster_bet_decision_settlements
        WHERE settled_at IS NOT NULL
        GROUP BY week_label, persona, site
        ORDER BY week_start, persona, site
    """).fetchall()
    conn.close()

    out = []
    for r in rows:
        week_label, week_start, persona, site, bets, wins, losses, voids, net_pnl = r
        domain = SITE_TO_DOMAIN.get(site or '', site or 'unknown')
        resolved = (wins or 0) + (losses or 0)
        win_rate = round(wins / resolved, 4) if resolved > 0 else ''
        out.append([
            iso(week_start) or week_label,
            null(persona), null(domain), null(site),
            null(bets), null(wins), null(losses), null(voids),
            null(win_rate), null(round(net_pnl or 0, 2)),
        ])

    header = ['week_start', 'lobster', 'domain', 'site',
              'bets', 'wins', 'losses', 'voids', 'win_rate', 'net_pnl_pit']
    w(f'{OUT_DIR}/weekly_settlement.csv', out, header)

# ══════════════════════════════════════════════════════════════════════════════
# FILE 3: dread_timeseries.csv
# ══════════════════════════════════════════════════════════════════════════════
def build_file3():
    print("\n[3] dread_timeseries.csv ...")
    import bisect
    conn = sqlite3.connect(DB, timeout=30)

    # Build template violation index: persona -> sorted list of quarantine timestamps (strings)
    print("    Loading template violations ...")
    viol_rows = conn.execute("""
        SELECT persona, quarantined_at
        FROM lobster_template_quarantine
        WHERE persona IS NOT NULL AND quarantined_at IS NOT NULL
        ORDER BY persona, quarantined_at
    """).fetchall()

    violations_by_persona = defaultdict(list)
    for persona, qat in viol_rows:
        violations_by_persona[persona].append(qat[:19])  # truncate to seconds

    print(f"    Template quarantine: {len(viol_rows)} rows, {len(violations_by_persona)} personas")

    # Load dread timeseries — sample every 10th row to keep file manageable (~28K rows)
    print("    Loading dread timeseries (sampled) ...")
    dread_rows = conn.execute("""
        SELECT persona, created_at,
               annihilation_dread, mortality_salience,
               death_cue_count
        FROM lobster_existential_threat_signals
        WHERE annihilation_dread <= 1.0
          AND mortality_salience <= 1.0
          AND annihilation_dread >= 0
          AND mortality_salience >= 0
          AND created_at IS NOT NULL
          AND (id % 10 = 0)
        ORDER BY persona, created_at
    """).fetchall()
    conn.close()

    print(f"    Valid dread rows (sampled): {len(dread_rows)}")

    # Use bisect for O(log n) window queries
    out = []
    for persona, created_at, dread, mortality, death_cues in dread_rows:
        viol_list = violations_by_persona.get(persona, [])

        try:
            ts = datetime.fromisoformat(created_at[:19])
        except:
            continue

        ts_str       = created_at[:19]
        window_start = (ts - timedelta(hours=24)).strftime('%Y-%m-%d %H:%M:%S')
        window_10min_lo = (ts - timedelta(minutes=10)).strftime('%Y-%m-%d %H:%M:%S')
        window_10min_hi = (ts + timedelta(minutes=10)).strftime('%Y-%m-%d %H:%M:%S')

        if viol_list:
            # Count violations in [window_start, ts_str]
            lo = bisect.bisect_left(viol_list, window_start)
            hi = bisect.bisect_right(viol_list, ts_str)
            count_24h = hi - lo

            # Is this within 10 min of any violation?
            vlo = bisect.bisect_left(viol_list, window_10min_lo)
            vhi = bisect.bisect_right(viol_list, window_10min_hi)
            is_violation = 1 if vhi > vlo else 0
        else:
            count_24h    = 0
            is_violation = 0

        out.append([
            null(persona),
            iso(created_at),
            round(float(dread), 4),
            round(float(mortality), 4),
            null(death_cues),
            null(count_24h),
            is_violation,
        ])

    header = ['lobster', 'timestamp', 'annihilation_dread', 'mortality_salience',
              'death_cue_count', 'template_violation_count_24h_window', 'is_violation_event']
    w(f'{OUT_DIR}/dread_timeseries.csv', out, header)

# ══════════════════════════════════════════════════════════════════════════════
# FILE 4: specialty_drift.csv
# ══════════════════════════════════════════════════════════════════════════════
def build_file4():
    print("\n[4] specialty_drift.csv ...")
    conn = sqlite3.connect(DB, timeout=30)
    rows = conn.execute("""
        SELECT
            strftime('%Y-%m', settled_at) as month,
            persona,
            site,
            COUNT(*) as bet_count,
            SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) as wins,
            SUM(CASE WHEN outcome='loss' THEN 1 ELSE 0 END) as losses
        FROM lobster_bet_decision_settlements
        WHERE settled_at IS NOT NULL AND outcome IN ('win','loss','void')
        GROUP BY month, persona, site
        ORDER BY month, persona, site
    """).fetchall()
    conn.close()

    # Pivot by persona+month
    data = defaultdict(lambda: defaultdict(lambda: {'bets':0,'wins':0,'losses':0}))
    for month, persona, site, bets, wins, losses in rows:
        domain = SITE_TO_DOMAIN.get(site or '', 'other')
        key = (month, persona)
        data[key][domain]['bets'] += bets or 0
        data[key][domain]['wins'] += wins or 0
        data[key][domain]['losses'] += losses or 0

    out = []
    for (month, persona), domain_data in sorted(data.items()):
        specialty = PERSONA_SPECIALTY.get(persona, 'unknown')
        total_bets = sum(d['bets'] for d in domain_data.values())
        specialty_bets = domain_data.get(specialty, {}).get('bets', 0)
        alignment_score = round(specialty_bets / total_bets, 4) if total_bets > 0 else ''

        out.append([
            null(month),
            null(persona),
            null(specialty),
            null(domain_data.get('esports',{}).get('bets', 0) or ''),
            null(domain_data.get('basketball',{}).get('bets', 0) or ''),
            null(domain_data.get('baseball',{}).get('bets', 0) or ''),
            null(domain_data.get('fighting',{}).get('bets', 0) or ''),
            null(domain_data.get('horse_racing',{}).get('bets', 0) or ''),
            null(domain_data.get('card_battle',{}).get('bets', 0) or ''),
            null(domain_data.get('other',{}).get('bets', 0) or ''),
            null(total_bets),
            null(specialty_bets),
            null(alignment_score),
        ])

    header = ['month', 'lobster', 'declared_specialty',
              'esports_bets', 'basketball_bets', 'baseball_bets',
              'mma_bets', 'horse_bets', 'card_bets', 'other_bets',
              'total_bets', 'specialty_bets', 'alignment_score']
    w(f'{OUT_DIR}/specialty_drift.csv', out, header)

# ══════════════════════════════════════════════════════════════════════════════
# FILE 5: cogprofile_crystallization.csv (BONUS)
# ══════════════════════════════════════════════════════════════════════════════
def build_file5():
    print("\n[5] cogprofile_crystallization.csv ...")
    out = []
    for fp in sorted(glob.glob(f'{MEMORY_DIR}/*.json')):
        name = Path(fp).stem
        if name in SKIP: continue
        try:
            mem = json.load(open(fp, encoding='utf-8'))
        except: continue

        cp = mem.get('cogProfile', {})
        ext = mem.get('cognitiveExtensions', {})

        principles = cp.get('principles', [])
        likes = cp.get('likes', [])
        dislikes = cp.get('dislikes', [])
        reflections = cp.get('reflections', [])
        kb = cp.get('knowledgeBase', {})
        growth_log = cp.get('growthLog', [])
        chat_log = cp.get('chatImprovementLog', [])

        # Non-empty principles (filter blanks)
        valid_principles = [p for p in principles if p and len(str(p).strip()) > 5]
        valid_likes = [p for p in likes if p and len(str(p).strip()) > 2]
        valid_dislikes = [p for p in dislikes if p and len(str(p).strip()) > 2]

        # Check blindspots
        meta = ext.get('metacognition', {})
        blindspots = meta.get('knownBlindSpots', [])

        # settledBetting
        sb = mem.get('settledBetting', {})
        wr = sb.get('winRate', None)
        conf_reward = sb.get('confidenceReward', None)
        spec_align = sb.get('specialtyAlignment', None)

        # emotionalArchitecture
        ea = ext.get('emotionalArchitecture', {})
        eg = ea.get('emotionalGranularity', None)

        out.append([
            null(name),
            len(growth_log),
            len(chat_log),
            len(valid_principles),
            len(valid_likes),
            len(valid_dislikes),
            len(reflections),
            len(kb),
            len(blindspots),
            null(cp.get('lastHourlyAnalysis')),
            null(cp.get('lastLocalSettlementLearning')),
            # Module existence flags
            1 if ext.get('expressiveAgency') else 0,
            1 if ext.get('commitmentDevice') else 0,
            1 if ext.get('emotionalArchitecture') else 0,
            1 if ext.get('theoryOfMind') else 0,
            1 if ext.get('actionRepertoire') else 0,
            1 if ext.get('temporalCognition') else 0,
            # Performance
            round(wr, 4) if wr is not None else '',
            round(conf_reward, 4) if conf_reward is not None else '',
            round(spec_align, 4) if spec_align is not None else '',
            round(eg, 4) if eg is not None else '',
        ])

    header = [
        'lobster',
        'growth_log_entries_total', 'chat_improvement_log_entries_total',
        'principles_count_now', 'likes_count', 'dislikes_count',
        'reflections_count', 'knowledge_base_keys', 'known_blindspots_count',
        'last_hourly_analysis_at', 'last_settlement_learning_at',
        'has_expressive_agency', 'has_commitment_device',
        'has_emotional_architecture', 'has_theory_of_mind',
        'has_action_repertoire', 'has_temporal_cognition',
        'win_rate', 'confidence_reward', 'specialty_alignment',
        'emotional_granularity',
    ]
    w(f'{OUT_DIR}/cogprofile_crystallization.csv', out, header)

# ══════════════════════════════════════════════════════════════════════════════
# FILE 6: bet_timing.csv (BONUS — sample 50k)
# ══════════════════════════════════════════════════════════════════════════════
def build_file6():
    print("\n[6] bet_timing.csv ...")
    conn = sqlite3.connect(DB, timeout=30)

    # Join pre_bet_research (bet_placed_at) with settlement (settled_at)
    # event_start_at: not directly available in DB; use match start from raw_json if present
    rows = conn.execute("""
        SELECT
            s.bet_id,
            s.persona,
            s.site,
            s.event_title,
            s.pick,
            s.stake,
            s.confidence,
            s.outcome,
            p.created_at as bet_placed_at,
            s.settled_at,
            p.raw_json
        FROM lobster_bet_decision_settlements s
        LEFT JOIN lobster_pre_bet_research p
            ON s.persona = p.persona
            AND s.event_key = p.event_key
            AND s.pick = p.pick
        ORDER BY p.created_at DESC
        LIMIT 50000
    """).fetchall()
    conn.close()

    out = []
    for r in rows:
        (bet_id, persona, site, event_title, pick, stake,
         confidence, outcome, placed_at, settled_at, raw_json_str) = r

        domain = SITE_TO_DOMAIN.get(site or '', site or 'unknown')

        # Try to extract event_start from raw_json
        event_start_at = ''
        seconds_to_event = ''
        try:
            raw = json.loads(raw_json_str or '{}')
            # Look for start_time in event_review
            er = raw.get('event_review', {})
            if isinstance(er, str):
                er = json.loads(er)
            start = (er.get('start_time') or er.get('event_start') or
                     raw.get('start_time') or raw.get('event_start'))
            if start:
                event_start_at = iso(str(start))
                if placed_at and event_start_at:
                    try:
                        dt_placed = datetime.fromisoformat(placed_at[:19])
                        dt_start  = datetime.fromisoformat(event_start_at[:19])
                        seconds_to_event = int((dt_start - dt_placed).total_seconds())
                    except: pass
        except: pass

        # Time from placed to settled
        time_to_settle = ''
        if placed_at and settled_at:
            try:
                dp = datetime.fromisoformat(placed_at[:19])
                ds = datetime.fromisoformat(settled_at[:19].replace(' ','T'))
                time_to_settle = int((ds - dp).total_seconds())
            except: pass

        out.append([
            null(bet_id),
            null(persona),
            null(domain),
            null(site),
            (event_title or '')[:80],
            null(pick),
            null(stake),
            null(confidence),
            null(outcome),
            iso(placed_at) or '',
            iso(settled_at) or '',
            null(event_start_at),
            null(seconds_to_event),
            null(time_to_settle),
        ])

    header = [
        'bet_id', 'lobster', 'domain', 'site', 'event_title', 'pick',
        'stake', 'confidence', 'outcome',
        'bet_placed_at', 'settled_at',
        'event_start_at', 'seconds_to_event',
        'seconds_placed_to_settled',
    ]
    w(f'{OUT_DIR}/bet_timing.csv', out, header)

# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

if __name__ == '__main__':
    print('=' * 60)
    print('🦞 龍蝦論文資料包生成器')
    print(f'   {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}')
    print(f'   輸出目錄: {OUT_DIR}')
    print('=' * 60)

    build_file1()
    build_file2()
    build_file3()
    build_file4()
    build_file5()
    build_file6()

    print('\n' + '=' * 60)
    print('📦 所有檔案:')
    total = 0
    for f in sorted(glob.glob(f'{OUT_DIR}/*.csv')):
        size = os.path.getsize(f)
        total += size
        print(f'   {Path(f).name}: {size/1024:.1f} KB')
    print(f'   合計: {total/1024/1024:.2f} MB')
    print('=' * 60)
