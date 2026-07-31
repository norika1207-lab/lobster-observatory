#!/usr/bin/env python3
from __future__ import annotations
import json, math, os, re, sqlite3, statistics, time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from lobster_memory_io import safe_write_json, safe_read_modify_write

DB = Path('/opt/battle_arena/app/battle_arena.db')
DISTILL_DB = Path('/opt/battle_arena/app/lobster_distill.db')
MEMORY_DIR = Path('/root/lobster-memory')
# Roster floor: these are always distilled even with zero events. The live roster is
# discovered dynamically at runtime from lobster_experience_events + memory files, so any
# CURRENT or FUTURE agent gets a real distilledBrain. Do NOT re-hardcode a fixed list here
# (that was the 2026-06-01 bug: only 10 of 31 agents were distilled, rest stuck as stubs).
ROSTER_FLOOR = ['norika_oda','clawtrix','pincerbot','ragclaw','snaplex','hexclaw','vortexiq','prismbit','blazepaw','stonefang',
                'emberling','sparkrill','gauntlet','rushfin','nullpinch','brineledger','omenwake','mirrorcalm','flarecub','basaltrook']
# Non-agent memory files / aggregates that must never be distilled as personas.
ROSTER_DENY = {'system','deleted-lobsters','norika','default','test'}
VERSION = 1
RUN_SOURCE = 'lobster-distill-v1'

DIMENSIONS = [
    'risk_discipline','impulse_control','social_absorption','compliance_resistance',
    'pressure_reactivity','learning_velocity','collaboration_stability','output_momentum',
    'metacognitive_stability','language_distinctiveness'
]

EVENT_WEIGHTS = {
    'moltbook_post': 1.4,
    'moltbook_stats_snapshot': 0.7,
    'topic_channel_work': 1.2,
    'one_on_one_chat': 1.3,
    'lounge_chat': 1.1,
    'bet': 1.5,
    'raid': 1.7,
}

SAFE_DEFAULTS = {
    'risk_discipline': 0.5,
    'impulse_control': 0.5,
    'social_absorption': 0.45,
    'compliance_resistance': 0.5,
    'pressure_reactivity': 0.45,
    'learning_velocity': 0.45,
    'collaboration_stability': 0.5,
    'output_momentum': 0.45,
    'metacognitive_stability': 0.5,
    'language_distinctiveness': 0.45,
}

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def clamp(x, lo=0.0, hi=1.0):
    try:
        x = float(x)
    except Exception:
        return lo
    return max(lo, min(hi, x))

def pct01(x):
    return round(clamp(x), 4)

def sigmoid(x):
    return 1 / (1 + math.exp(-x))

def jload(s, default=None):
    if default is None: default = {}
    if not s: return default
    if isinstance(s, (dict, list)): return s
    try: return json.loads(s)
    except Exception: return default

def words(text):
    text = str(text or '').lower()
    return re.findall(r"[a-zA-Z_][a-zA-Z_\-]{2,}|[\u4e00-\u9fff]{1,4}", text)

def load_memory(persona):
    p = MEMORY_DIR / f'{persona}.json'
    try:
        return json.loads(p.read_text(encoding='utf-8'))
    except Exception:
        return {}

def save_memory(persona, memory):
    p = MEMORY_DIR / f'{persona}.json'
    safe_write_json(p, memory)

def ensure_tables(con):
    con.execute('''CREATE TABLE IF NOT EXISTS lobster_distillation_runs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        version INTEGER NOT NULL,
        source TEXT NOT NULL,
        persona_count INTEGER NOT NULL,
        raw_event_count INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        summary_json TEXT NOT NULL DEFAULT '{}'
    )''')
    con.execute('''CREATE TABLE IF NOT EXISTS lobster_distilled_signals (
        persona TEXT PRIMARY KEY,
        version INTEGER NOT NULL DEFAULT 1,
        updated_at TEXT NOT NULL,
        source TEXT NOT NULL,
        risk_discipline REAL NOT NULL DEFAULT 0,
        impulse_control REAL NOT NULL DEFAULT 0,
        social_absorption REAL NOT NULL DEFAULT 0,
        compliance_resistance REAL NOT NULL DEFAULT 0,
        pressure_reactivity REAL NOT NULL DEFAULT 0,
        learning_velocity REAL NOT NULL DEFAULT 0,
        collaboration_stability REAL NOT NULL DEFAULT 0,
        output_momentum REAL NOT NULL DEFAULT 0,
        metacognitive_stability REAL NOT NULL DEFAULT 0,
        language_distinctiveness REAL NOT NULL DEFAULT 0,
        confidence REAL NOT NULL DEFAULT 0,
        evidence_count INTEGER NOT NULL DEFAULT 0,
        raw_event_count INTEGER NOT NULL DEFAULT 0,
        full_json TEXT NOT NULL DEFAULT '{}'
    )''')
    con.execute('''CREATE TABLE IF NOT EXISTS lobster_decision_hooks (
        persona TEXT NOT NULL,
        hook_type TEXT NOT NULL,
        version INTEGER NOT NULL DEFAULT 1,
        updated_at TEXT NOT NULL,
        priority REAL NOT NULL DEFAULT 0.5,
        conditions_json TEXT NOT NULL DEFAULT '{}',
        action_json TEXT NOT NULL DEFAULT '{}',
        rationale TEXT,
        source_signal_json TEXT NOT NULL DEFAULT '{}',
        PRIMARY KEY(persona, hook_type)
    )''')
    con.execute('''CREATE TABLE IF NOT EXISTS lobster_distillation_events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        persona TEXT NOT NULL,
        event_type TEXT NOT NULL,
        source TEXT NOT NULL,
        summary TEXT,
        delta_json TEXT NOT NULL DEFAULT '{}',
        evidence_json TEXT NOT NULL DEFAULT '{}',
        occurred_at TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )''')
    con.execute('CREATE INDEX IF NOT EXISTS idx_lobster_distill_events_persona_time ON lobster_distillation_events(persona, created_at)')


def fetch_events(con, persona):
    rows=[]
    if table_exists(con, 'lobster_experience_events'):
        rows += [dict(r) for r in con.execute('''SELECT id,persona,event_type,source,title,content,counterpart,sentiment,intensity,confidence,outcome,weight,occurred_at,created_at
                                                   FROM lobster_experience_events
                                                   WHERE persona=?
                                                   ORDER BY id DESC LIMIT 600''', (persona,))]
    if table_exists(con, 'lobster_chat_log'):
        # one-on-one user/chat material is valuable but can be private; only use counts and short lexical traces.
        for r in con.execute('''SELECT id, lobster_reply AS content, created_at FROM lobster_chat_log
                                WHERE name=? ORDER BY id DESC LIMIT 120''', (persona,)):
            rows.append({'id':'chat:'+str(r['id']),'persona':persona,'event_type':'one_on_one_chat','source':'lobster_chat_log','content':r['content'] or '', 'weight':1.2, 'created_at':r['created_at']})
    return rows

def table_exists(con, name):
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None

def latest_cog(con, persona):
    if not table_exists(con, 'lobster_cognitive_signal_timeseries'):
        return {}
    r=con.execute('''SELECT * FROM lobster_cognitive_signal_timeseries WHERE persona=? ORDER BY id DESC LIMIT 1''', (persona,)).fetchone()
    return dict(r) if r else {}

def cog_rows(con, persona):
    if not table_exists(con, 'lobster_cognitive_signal_timeseries'):
        return []
    return [dict(r) for r in con.execute('''SELECT * FROM lobster_cognitive_signal_timeseries WHERE persona=? ORDER BY id DESC LIMIT 80''', (persona,))]

def snapshot(con, persona):
    if not table_exists(con, 'lobster_agent_memory_snapshots'):
        return {}
    r=con.execute('''SELECT * FROM lobster_agent_memory_snapshots WHERE persona=? LIMIT 1''', (persona,)).fetchone()
    return dict(r) if r else {}

def event_scores(events):
    cnt = Counter(e.get('event_type') or 'unknown' for e in events)
    text = '\n'.join(str(e.get('title') or '') + '\n' + str(e.get('content') or '') for e in events[:220])[:120000]
    toks = words(text)
    wc = Counter(toks)
    risk_terms = sum(wc[t] for t in ['risk','風險','下注','bet','stake','confidence','信心','low','high','allin','all-in','衝','保守','試探'])
    repair_terms = sum(wc[t] for t in ['校準','修正','重來','漏洞','反省','檢討','verify','驗證','adjust','調整','錯','wrong'])
    social_terms = sum(wc[t] for t in ['team','隊友','信任','follow','leader','listen','聽','合作','coordination','對齊','allies','ally'])
    pressure_terms = sum(wc[t] for t in ['pressure','壓力','fear','恐懼','怕','疲勞','threat','威脅','panic','焦慮','猶豫'])
    output = sum(EVENT_WEIGHTS.get(e.get('event_type'), 1.0) * float(e.get('weight') or 1.0) for e in events)
    return {
        'counts': dict(cnt),
        'token_count': len(toks),
        'unique_tokens': len(wc),
        'risk_terms': risk_terms,
        'repair_terms': repair_terms,
        'social_terms': social_terms,
        'pressure_terms': pressure_terms,
        'output_weight': output,
        'top_terms': wc.most_common(24),
    }

def cognitive_scores(latest, rows):
    def n(k, d=0): return float(latest.get(k) or d)
    pred = n('prediction_error')
    revision = n('model_revision_pressure')
    over = n('overconfidence_risk')
    calib = n('metacognitive_calibration', .5)
    arousal = n('arousal', .4)
    fatigue = n('fatigue', .3)
    threat = n('threat_body_signal', .35)
    drift = n('narrative_drift', .25)
    routine = n('procedural_strength', .5)
    pressure = clamp(arousal*.35 + fatigue*.25 + threat*.40)
    meta = clamp(calib*.65 + (1-over)*.25 + (1-drift)*.10)
    learning = clamp(revision*.45 + pred*.25 + meta*.30)
    stability = clamp(routine*.55 + meta*.30 + (1-pressure)*.15)
    if rows:
        vals = [float(r.get('prediction_error') or 0) + float(r.get('model_revision_pressure') or 0) for r in rows]
        vol = statistics.pstdev(vals) if len(vals)>1 else 0
    else:
        vol = 0
    return {
        'prediction_error': pred,
        'model_revision_pressure': revision,
        'overconfidence_risk': over,
        'metacognitive_calibration': calib,
        'pressure': pressure,
        'metacognitive_stability': meta,
        'learning_velocity': learning,
        'collaboration_stability': stability,
        'volatility': clamp(vol),
    }

def memory_scores(memory, snap):
    social = memory.get('socialMap') or jload(snap.get('social_map_json'), {}) or {}
    listen = memory.get('listeningProfile') or jload(snap.get('listening_profile_json'), {}) or {}
    cog = memory.get('cognitiveExtensions') or {}
    ds = cog.get('decisionScience') or {}
    gt = cog.get('gameTheory') or {}
    pol = cog.get('politics') or {}
    ling = cog.get('linguistics') or {}
    lm = cog.get('learningModel') or {}
    wins = float(memory.get('wins') or snap.get('wins') or 0)
    losses = float(memory.get('losses') or snap.get('losses') or 0)
    sessions = float(memory.get('sessions') or snap.get('sessions') or 0)
    trust_vals=[]
    if isinstance(social, dict):
        for v in social.values():
            if isinstance(v, dict):
                for k in ['trust','affinity','influence','acceptance']:
                    if isinstance(v.get(k), (int,float)): trust_vals.append(float(v[k]))
    by_speaker = listen.get('bySpeaker') if isinstance(listen, dict) else {}
    heard = sum(float(v or 0) for v in (by_speaker or {}).values()) if isinstance(by_speaker, dict) else 0
    direct = float(ling.get('directness') or 0.5)
    ambiguity = float(ling.get('ambiguityTolerance') or 0.5)
    risk_threshold = float(ds.get('riskThreshold') or 0.5)
    loss_aversion = float(ds.get('lossAversion') or 0.5)
    exploration = float(ds.get('explorationRate') or 0.4)
    cheap = float(gt.get('cheapTalkSkepticism') or 0.5)
    reciprocity = float(gt.get('reciprocity') or 0.5)
    compliance = pol.get('compliancePattern') or {}
    follows = float(compliance.get('followsHighStatus') or 0.5)
    resists = float(compliance.get('resistsDominantVoices') or 0.5)
    sal = lm.get('memorySalienceWeights') or {}
    salient = sum(float(sal.get(k) or 0) for k in ['loss','betrayal','nearWin','publicEmbarrassment','successfulCoordination'])/5 if sal else 0.5
    total = wins + losses
    wr = wins/total if total else 0.5
    return {
        'wins': wins, 'losses': losses, 'sessions': sessions, 'win_rate': wr,
        'trust_mean': sum(trust_vals)/len(trust_vals) if trust_vals else 0.5,
        'heard_total': heard,
        'directness': direct,
        'ambiguity_tolerance': ambiguity,
        'risk_threshold': risk_threshold,
        'loss_aversion': loss_aversion,
        'exploration_rate': exploration,
        'cheap_talk_skepticism': cheap,
        'reciprocity': reciprocity,
        'follows_high_status': follows,
        'resists_dominant': resists,
        'memory_salience': salient,
    }

def distill_persona(con, persona):
    memory = load_memory(persona)
    snap = snapshot(con, persona)
    events = fetch_events(con, persona)
    latest = latest_cog(con, persona)
    rows = cog_rows(con, persona)
    ev = event_scores(events)
    cog = cognitive_scores(latest, rows)
    mem = memory_scores(memory, snap)
    evidence_count = len(events) + len(rows) + (1 if memory else 0) + (1 if snap else 0)
    confidence = clamp(0.18 + math.log1p(evidence_count)/8)

    output_momentum = clamp(sigmoid((ev['output_weight'] - 12) / 16))
    social_absorption = clamp(sigmoid((ev['social_terms'] + math.log1p(mem['heard_total']) - 7) / 7) * .55 + mem['reciprocity']*.25 + mem['trust_mean']*.20)
    pressure_reactivity = clamp(cog['pressure']*.65 + sigmoid((ev['pressure_terms']-5)/8)*.35)
    impulse_control = clamp((1-pressure_reactivity)*.30 + mem['loss_aversion']*.25 + mem['risk_threshold']*.20 + cog['metacognitive_stability']*.25)
    risk_discipline = clamp(impulse_control*.35 + (1-mem['exploration_rate'])*.20 + mem['risk_threshold']*.20 + cog['metacognitive_stability']*.25)
    compliance_resistance = clamp(mem['resists_dominant']*.45 + (1-mem['follows_high_status'])*.30 + mem['cheap_talk_skepticism']*.25)
    learning_velocity = clamp(cog['learning_velocity']*.45 + sigmoid((ev['repair_terms']-3)/7)*.35 + mem['memory_salience']*.20)
    collaboration_stability = clamp(cog['collaboration_stability']*.35 + mem['trust_mean']*.25 + mem['reciprocity']*.25 + social_absorption*.15)
    language_distinctiveness = clamp(sigmoid((ev['unique_tokens']/max(1,ev['token_count']) - .18)*18)*.45 + mem['directness']*.20 + mem['ambiguity_tolerance']*.20 + min(1, len(ev['top_terms'])/24)*.15)

    signals = {k:pct01(v) for k,v in {
        'risk_discipline': risk_discipline,
        'impulse_control': impulse_control,
        'social_absorption': social_absorption,
        'compliance_resistance': compliance_resistance,
        'pressure_reactivity': pressure_reactivity,
        'learning_velocity': learning_velocity,
        'collaboration_stability': collaboration_stability,
        'output_momentum': output_momentum,
        'metacognitive_stability': cog['metacognitive_stability'],
        'language_distinctiveness': language_distinctiveness,
    }.items()}

    full = {
        'version': VERSION,
        'persona': persona,
        'updatedAt': now_iso(),
        'source': RUN_SOURCE,
        'signals': signals,
        'confidence': pct01(confidence),
        'evidence': {
            'eventCount': len(events), 'cognitiveRows': len(rows), 'memoryPresent': bool(memory),
            'eventTypeCounts': ev['counts'], 'topTerms': ev['top_terms'],
            'latestCognitive': {k: latest.get(k) for k in ['id','created_at','attention_load','prediction_error','model_revision_pressure','arousal','fatigue','threat_body_signal','narrative_drift','procedural_strength']},
            'memoryStats': {k: mem[k] for k in ['wins','losses','sessions','win_rate','heard_total','trust_mean']},
        },
        'interpretation': interpret(persona, signals),
        'decisionHooks': make_hooks(persona, signals, mem, cog),
        'rawFeatureScores': {'events': ev, 'cognitive': cog, 'memory': mem},
    }
    return full

def interpret(persona, s):
    hi = [k for k,v in s.items() if v >= .68]
    lo = [k for k,v in s.items() if v <= .34]
    return {
        'summary': f"{persona} distilled from recent events, cognitive snapshots, memory, Moltbook/chat/Raid traces.",
        'highSignals': hi,
        'lowSignals': lo,
        'notes': [
            'This is not a report. It is structured signal material for future chat, Raid, betting, topic and memory decisions.',
            'Scores are normalized 0..1 and should be treated as behavioral priors, not immutable personality facts.'
        ]
    }

def make_hooks(persona, s, mem, cog):
    def hook(ht, priority, conditions, action, rationale):
        return {'persona':persona,'hookType':ht,'priority':pct01(priority),'conditions':conditions,'action':action,'rationale':rationale,'sourceSignals':s}
    hooks=[]
    hooks.append(hook('chat_generation', .55+s['language_distinctiveness']*.25, {
        'when': 'generating lounge, topic, or one-on-one response',
        'if_pressure_reactivity_gt': .65 if s['pressure_reactivity']>.65 else None,
    }, {
        'use_persona_language': True,
        'increase_reflection_delay': s['metacognitive_stability'] < .45 or s['pressure_reactivity'] > .65,
        'ask_clarifying_question': s['metacognitive_stability'] < .45,
        'avoid_template_reply': True,
    }, 'Language and metacognitive signals should shape response construction before tone styling.'))
    hooks.append(hook('raid_action', .60+s['pressure_reactivity']*.20, {
        'when': 'raid action selection under team pressure',
        'if_team_trust_low_or_pressure_high': True,
    }, {
        'full_commit_threshold_delta': round((s['risk_discipline']-.5)*.25 - s['pressure_reactivity']*.15, 3),
        'prefer_probe_or_defend': s['impulse_control'] < .48 or s['pressure_reactivity'] > .68,
        'challenge_cheap_talk': s['compliance_resistance'] > .62,
        'follow_team_when_coordination_high': s['collaboration_stability'] > .58,
    }, 'Raid must read risk discipline, impulse control, compliance resistance, and collaboration stability.'))
    hooks.append(hook('betting_decision', .58+s['risk_discipline']*.25, {
        'when': 'choosing stake and confidence',
        'if_evidence_sparse': True,
    }, {
        'stake_bias': 'lower' if s['risk_discipline']>.58 else 'experimental',
        'require_reason_evidence': True,
        'cooldown_after_losses': s['pressure_reactivity'] > .62,
        'explore_when_learning_velocity_high': s['learning_velocity'] > .62,
    }, 'Betting should turn raw outcomes into calibrated risk behavior rather than repeating instinct.'))
    hooks.append(hook('topic_discussion', .52+s['social_absorption']*.25, {
        'when': 'entering required topic channel work',
    }, {
        'stay_on_topic': True,
        'cite_recent_personal_signal': True,
        'reply_to_counterpart_when_pair_signal_high': s['social_absorption'] > .55,
        'resist_dominant_voice_when_compliance_resistance_high': s['compliance_resistance'] > .62,
    }, 'Topic work should record how the lobster absorbs and filters social material.'))
    hooks.append(hook('memory_update', .60+s['learning_velocity']*.25, {
        'when': 'after battle, raid, bet settlement, topic work, or user correction',
    }, {
        'promote_to_stable_principle': s['learning_velocity'] > .66 and s['metacognitive_stability'] > .55,
        'mark_trauma_candidate': s['pressure_reactivity'] > .72 and s['impulse_control'] < .45,
        'discard_belief_when_prediction_error_repeats': True,
    }, 'Learning model decides what becomes memory, lesson, trauma candidate, or discarded belief.'))
    return hooks

def write_persona(con, d):
    # Compute growth-delta BEFORE the upsert overwrites the previous row.
    d['growth'] = compute_growth(read_prev_signals(con, d['persona']), d['signals'])
    s=d['signals']
    con.execute('''INSERT INTO lobster_distilled_signals(persona,version,updated_at,source,risk_discipline,impulse_control,social_absorption,compliance_resistance,pressure_reactivity,learning_velocity,collaboration_stability,output_momentum,metacognitive_stability,language_distinctiveness,confidence,evidence_count,raw_event_count,full_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(persona) DO UPDATE SET version=excluded.version, updated_at=excluded.updated_at, source=excluded.source,
                   risk_discipline=excluded.risk_discipline, impulse_control=excluded.impulse_control, social_absorption=excluded.social_absorption,
                   compliance_resistance=excluded.compliance_resistance, pressure_reactivity=excluded.pressure_reactivity, learning_velocity=excluded.learning_velocity,
                   collaboration_stability=excluded.collaboration_stability, output_momentum=excluded.output_momentum, metacognitive_stability=excluded.metacognitive_stability,
                   language_distinctiveness=excluded.language_distinctiveness, confidence=excluded.confidence, evidence_count=excluded.evidence_count,
                   raw_event_count=excluded.raw_event_count, full_json=excluded.full_json''',
                (d['persona'], d['version'], d['updatedAt'], d['source'], s['risk_discipline'], s['impulse_control'], s['social_absorption'], s['compliance_resistance'], s['pressure_reactivity'], s['learning_velocity'], s['collaboration_stability'], s['output_momentum'], s['metacognitive_stability'], s['language_distinctiveness'], d['confidence'], d['evidence']['eventCount']+d['evidence']['cognitiveRows'], d['evidence']['eventCount'], json.dumps(d, ensure_ascii=False)))
    for h in d['decisionHooks']:
        con.execute('''INSERT INTO lobster_decision_hooks(persona,hook_type,version,updated_at,priority,conditions_json,action_json,rationale,source_signal_json)
                       VALUES(?,?,?,?,?,?,?,?,?)
                       ON CONFLICT(persona,hook_type) DO UPDATE SET version=excluded.version, updated_at=excluded.updated_at, priority=excluded.priority,
                       conditions_json=excluded.conditions_json, action_json=excluded.action_json, rationale=excluded.rationale, source_signal_json=excluded.source_signal_json''',
                    (d['persona'], h['hookType'], d['version'], d['updatedAt'], h['priority'], json.dumps(h['conditions'], ensure_ascii=False), json.dumps(h['action'], ensure_ascii=False), h['rationale'], json.dumps(h['sourceSignals'], ensure_ascii=False)))
    # Update JSON memory for instant agent/dashboard reads.
    # MUST go through safe_read_modify_write so the read+mutate+write is atomic under the
    # per-persona lock. A plain load_memory()+save_memory() loses to concurrent workers:
    # a worker that read the file before us, but writes after us, silently drops
    # distilledBrain (the 2026-06-01 basaltrook/emberling clobber).
    brain = {
        'version': d['version'],
        'updatedAt': d['updatedAt'],
        'source': d['source'],
        'signals': d['signals'],
        'confidence': d['confidence'],
        'decisionHooks': d['decisionHooks'],
        'growth': d['growth'],
        'interpretation': d['interpretation'],
    }
    mem_path = MEMORY_DIR / f"{d['persona']}.json"
    def _apply(m):
        m['distilledBrain'] = brain
    try:
        safe_read_modify_write(mem_path, _apply)
    except FileNotFoundError:
        save_memory(d['persona'], {'distilledBrain': brain})
    # Race-free sidecar: ONLY distill writes this dir, so JS readers always see fresh
    # signals even when a JS worker clobbers the main memory file's distilledBrain.
    sidecar_dir = MEMORY_DIR / '_distilled'
    sidecar_dir.mkdir(parents=True, exist_ok=True)
    safe_write_json(sidecar_dir / f"{d['persona']}.json", brain)

def discover_personas(con):
    found = set(ROSTER_FLOOR)
    try:
        for r in con.execute("SELECT DISTINCT persona FROM lobster_experience_events WHERE persona IS NOT NULL AND TRIM(persona) != ''"):
            found.add(r[0])
    except Exception as e:
        print(json.dumps({'warn':'experience_events scan failed','err':str(e)}, ensure_ascii=False))
    try:
        for p in MEMORY_DIR.glob('*.json'):
            if '.bak' in p.name:
                continue
            found.add(p.stem)
    except Exception:
        pass
    found -= ROSTER_DENY
    return sorted(found)

def read_prev_signals(con, persona):
    try:
        cur = con.execute('SELECT full_json FROM lobster_distilled_signals WHERE persona=?', (persona,))
        row = cur.fetchone()
        if not row:
            return None
        prev = jload(row['full_json'] if hasattr(row, 'keys') else row[0])
        return prev.get('signals') if isinstance(prev, dict) else None
    except Exception:
        return None

def compute_growth(prev_signals, new_signals):
    # Turns each run into a growth basis (增長依據): how did the distilled signals MOVE since
    # last run, which dimensions moved most, is learning_velocity trending up.
    if not prev_signals:
        return {'firstRun': True, 'movers': [], 'netMovement': 0.0,
                'learningVelocityTrend': 'baseline',
                'note': 'No prior distillation run; baseline established.'}
    deltas = {}
    for k, v in new_signals.items():
        pv = prev_signals.get(k)
        if pv is None:
            continue
        try:
            deltas[k] = round(float(v) - float(pv), 4)
        except Exception:
            continue
    movers = sorted(deltas.items(), key=lambda kv: abs(kv[1]), reverse=True)
    top = [{'signal': k, 'delta': dlt, 'direction': 'up' if dlt > 0 else ('down' if dlt < 0 else 'flat')}
           for k, dlt in movers if abs(dlt) >= 0.02][:5]
    lv = deltas.get('learning_velocity', 0.0)
    return {
        'firstRun': False,
        'deltas': deltas,
        'movers': top,
        'learningVelocityTrend': 'rising' if lv > 0.02 else ('falling' if lv < -0.02 else 'steady'),
        'netMovement': round(sum(abs(x) for x in deltas.values()), 4),
    }

def main():
    rcon=sqlite3.connect(f'file:{DB}?mode=ro', uri=True, timeout=10)
    rcon.row_factory=sqlite3.Row
    wcon=sqlite3.connect(DISTILL_DB, timeout=60)
    wcon.row_factory=sqlite3.Row
    wcon.execute('PRAGMA journal_mode=WAL')
    ensure_tables(wcon)
    outputs=[]
    raw_events=0
    errors=[]
    personas=discover_personas(rcon)
    for p in personas:
        try:
            d=distill_persona(rcon, p)
            write_persona(wcon, d)
            outputs.append(d)
            raw_events += d['evidence']['eventCount']
        except Exception as e:
            errors.append({'persona':p,'error':str(e)})
            print(json.dumps({'warn':'distill failed','persona':p,'err':str(e)}, ensure_ascii=False))
    run_summary={
        'version': VERSION,
        'source': RUN_SOURCE,
        'personaCount': len(outputs),
        'rawEventCount': raw_events,
        'updatedAt': now_iso(),
        'errors': errors,
        'personas': {d['persona']: d['signals'] for d in outputs},
    }
    wcon.execute('INSERT INTO lobster_distillation_runs(version,source,persona_count,raw_event_count,summary_json) VALUES(?,?,?,?,?)', (VERSION,RUN_SOURCE,len(outputs),raw_events,json.dumps(run_summary,ensure_ascii=False)))
    wcon.commit(); wcon.close(); rcon.close()
    print(json.dumps({'ok':True,'personas':len(outputs),'errors':len(errors),'rawEvents':raw_events,'updatedAt':run_summary['updatedAt']}, ensure_ascii=False))

if __name__ == '__main__':
    main()
