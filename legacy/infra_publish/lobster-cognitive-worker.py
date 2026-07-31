#!/usr/bin/env python3
"""
lobster-cognitive-worker.py
龍蝦腦建構工人 v1.0
功能：
  1. 從DB歷史下注結果逆推認知 → 回寫記憶
  2. crystallization: growthLog + chatImprovementLog → principles
  3. 補 knownBlindSpots (從 settledBetting 信號)
  4. specialty_alignment 計算
  5. 結構性敗點 → traumaEvents
  6. mindRefuge 改成 array (primary/secondary/fallback)
  7. commitmentDevice (Ulysses 契約)
  8. expressiveAgency 模組
  9. temporalCognition / emotionalArchitecture / theoryOfMind 模組 stub
 10. signalSaturation (mortality threat 去敏化保護)
"""

import json
import sqlite3
import glob
import re
from pathlib import Path
from collections import defaultdict
from datetime import datetime
from lobster_memory_io import safe_write_json

DB_PATH = "/opt/battle_arena/app/battle_arena.db"
MEMORY_DIR = "/root/lobster-memory"
SKIP = {"deleted-lobsters", "system", "norika"}  # elder 2026-05-12 移出 SKIP，正式納入腦建構

# Site → sport mapping
SITE_SPORT = {
    "dunknix.com": "basketball",
    "knocknix.com": "fighting",
    "pitchnix.com": "baseball",
    "ragnovex.com": "esports",
    "turfenix.com": "horse_racing",
    "throttenix.com": "racing",
}

# Persona specialty mapping (from PERSONAS in agent)
PERSONA_SPECIALTY = {
    "norika_oda":   "horse_racing",
    "clawtrix":     "esports",
    "pincerbot":    "fighting",
    "ragclaw":      "esports",
    "snaplex":      "card_battle",
    "hexclaw":      "basketball",
    "vortexiq":     "horse_racing",
    "prismbit":     "baseball",
    "blazepaw":     "card_battle",
    "stonefang":    "fighting",
    "emberling":    "horse_racing",
    "sparkrill":    "esports",
    "gauntlet":     "fighting",
    "rushfin":      "esports",
    "nullpinch":    "card_battle",
    "brineledger":  "basketball",
    "omenwake":     "horse_racing",
    "mirrorcalm":   "baseball",
    "flarecub":     "card_battle",
    "basaltrook":   "fighting",
}

MIND_REFUGE_SECONDARY = {
    "norika_oda":  "vortexiq",
    "clawtrix":    "stonefang",
    "pincerbot":   "stonefang",
    "ragclaw":     "clawtrix",
    "snaplex":     "nullpinch",
    "hexclaw":     "brineledger",
    "vortexiq":    "norika_oda",
    "prismbit":    "mirrorcalm",
    "blazepaw":    "flarecub",
    "stonefang":   "pincerbot",
    "emberling":   "omenwake",
    "sparkrill":   "rushfin",
    "gauntlet":    "basaltrook",
    "rushfin":     "sparkrill",
    "nullpinch":   "flarecub",
    "brineledger": "hexclaw",
    "omenwake":    "emberling",
    "mirrorcalm":  "prismbit",
    "flarecub":    "blazepaw",
    "basaltrook":  "gauntlet",
}

# ── 1. Load all settled bets per persona ─────────────────────────────────────

def load_settled_bets(conn, persona):
    rows = conn.execute("""
        SELECT
            site, event_title, pick, stake, confidence, outcome, payout,
            settled_at, raw_decision_reasoning, integrated_reasoning,
            source_bet_json, checklist_json
        FROM lobster_bet_decision_settlements
        WHERE persona = ? AND outcome IN ('win','loss','void')
        ORDER BY settled_at ASC
    """, (persona,)).fetchall()
    result = []
    for r in rows:
        site, event_title, pick, stake, confidence, outcome, payout, \
            settled_at, raw_reason, int_reason, src_json, chk_json = r
        try:
            src = json.loads(src_json or '{}')
            odds = src.get('odds', None)
        except:
            odds = None
        sport = SITE_SPORT.get(site or '', 'unknown')
        result.append({
            'site': site or '',
            'sport': sport,
            'event_title': event_title or '',
            'pick': pick or '',
            'stake': stake or 0,
            'confidence': confidence or 'unknown',
            'outcome': outcome,
            'payout': payout or 0,
            'settled_at': settled_at or '',
            'raw_reason': raw_reason or '',
            'int_reason': int_reason or '',
            'odds': odds,
        })
    return result

# ── 2. Compute comprehensive settledBetting stats ────────────────────────────

def compute_settled_stats(bets):
    wins = [b for b in bets if b['outcome'] == 'win']
    losses = [b for b in bets if b['outcome'] == 'loss']
    voids = [b for b in bets if b['outcome'] == 'void']
    total = len(bets)
    resolved = len(wins) + len(losses)

    # Confidence calibration
    conf_stats = defaultdict(lambda: {'win': 0, 'loss': 0})
    for b in bets:
        if b['outcome'] in ('win', 'loss'):
            conf_stats[b['confidence']]['win' if b['outcome'] == 'win' else 'loss'] += 1

    # confidenceReward: 高信心 vs 低信心勝率差異
    high = conf_stats.get('high', {'win': 0, 'loss': 0})
    low = conf_stats.get('low', {'win': 0, 'loss': 0})
    h_total = high['win'] + high['loss']
    l_total = low['win'] + low['loss']
    h_wr = high['win'] / h_total if h_total else 0.5
    l_wr = low['win'] / l_total if l_total else 0.5
    confidence_reward = round(h_wr - l_wr, 4) if h_total >= 3 and l_total >= 3 else 0.0

    # Specialty alignment
    # (will be added per-persona later)

    # Win streak / loss streak
    max_win_streak = max_loss_streak = cur_win = cur_loss = 0
    for b in bets:
        if b['outcome'] == 'win':
            cur_win += 1; cur_loss = 0
            max_win_streak = max(max_win_streak, cur_win)
        elif b['outcome'] == 'loss':
            cur_loss += 1; cur_win = 0
            max_loss_streak = max(max_loss_streak, cur_loss)

    # By sport
    sport_stats = defaultdict(lambda: {'win': 0, 'loss': 0, 'void': 0, 'stake': 0, 'payout': 0})
    for b in bets:
        s = b['sport']
        sport_stats[s][b['outcome']] += 1
        sport_stats[s]['stake'] += b['stake']
        sport_stats[s]['payout'] += b['payout']

    sport_summary = {}
    for sport, s in sport_stats.items():
        n = s['win'] + s['loss']
        sport_summary[sport] = {
            'wins': s['win'],
            'losses': s['loss'],
            'voids': s['void'],
            'winRate': round(s['win'] / n, 4) if n else 0,
            'netPL': round(s['payout'] - s['stake'], 2),
        }

    # Recent form (last 20 resolved)
    recent = [b for b in bets if b['outcome'] in ('win', 'loss')][-20:]
    recent_wins = sum(1 for b in recent if b['outcome'] == 'win')
    recent_wr = round(recent_wins / len(recent), 4) if recent else 0

    win_rate = round(wins.__len__() / resolved, 4) if resolved else 0
    void_rate = round(len(voids) / total, 4) if total else 0
    avg_stake = round(sum(b['stake'] for b in bets) / total, 2) if total else 0
    total_pnl = round(sum(b['payout'] for b in bets) - sum(b['stake'] for b in bets), 2)

    return {
        'total': total,
        'resolved': resolved,
        'wins': len(wins),
        'losses': len(losses),
        'voids': len(voids),
        'winRate': win_rate,
        'lossRate': round(1 - win_rate, 4) if resolved else 1.0,
        'voidRate': void_rate,
        'recentWinRate': recent_wr,
        'maxWinStreak': max_win_streak,
        'maxLossStreak': max_loss_streak,
        'confidenceReward': confidence_reward,
        'confidenceBreakdown': dict(conf_stats),
        'sportStats': sport_summary,
        'avgStake': avg_stake,
        'totalPnL': total_pnl,
        'computedAt': datetime.utcnow().isoformat() + 'Z',
    }

# ── 3. Compute specialty alignment ───────────────────────────────────────────

def compute_specialty_alignment(bets, persona):
    specialty = PERSONA_SPECIALTY.get(persona)
    if not specialty:
        return 0.0
    resolved = [b for b in bets if b['outcome'] in ('win', 'loss')]
    if not resolved:
        return 0.0
    in_specialty = [b for b in resolved if b['sport'] == specialty]
    return round(len(in_specialty) / len(resolved), 4)

# ── 4. Crystallization: growthLog → principles ───────────────────────────────

def crystallize_principles(memory):
    """Extract high-signal directives from growthLog + chatImprovementLog."""
    cog = memory.get('cogProfile', {})
    growth = cog.get('growthLog', [])
    chats = cog.get('chatImprovementLog', [])

    # Collect all summary texts
    candidates = []
    for g in growth:
        s = g.get('summary', '') or g.get('directive', '')
        if s and len(s) > 20:
            candidates.append(s)
    for c in chats:
        s = c.get('directive', '') or c.get('summary', '')
        if s and len(s) > 20:
            candidates.append(s)

    if not candidates:
        return []

    # Frequency map of key phrases
    phrase_freq = defaultdict(int)
    for text in candidates:
        # Extract actionable phrases (sentences starting with action words)
        sentences = re.split(r'[。；\n]', text)
        for s in sentences:
            s = s.strip()
            if 20 < len(s) < 100:
                phrase_freq[s] += 1

    # Top phrases (freq >= 2 or meaningful single ones)
    top = sorted(phrase_freq.items(), key=lambda x: -x[1])[:20]
    seen = set()
    extracted = []
    for phrase, freq in top:
        # Deduplicate semantically similar
        key = phrase[:30]
        if key in seen:
            continue
        seen.add(key)
        extracted.append(phrase)
        if len(extracted) >= 8:
            break

    return extracted

# ── 5. Auto-fill knownBlindSpots ─────────────────────────────────────────────

def auto_fill_blindspots(stats, bets, persona):
    """Derive blind spots from settled betting data."""
    spots = []

    # Confidence calibration blindspot
    cr = stats.get('confidenceReward', 0)
    if cr < 0:
        spots.append("高信心反而輸更多：自信度估高，實際勝率低於低信心局。需要重新校準信心指標。")
    elif cr > 0.3:
        spots.append("高信心局勝率顯著更高，但整體高信心下注比例不足——錯過了自己最強的時刻。")

    # Low overall win rate
    wr = stats.get('winRate', 0.5)
    resolved = stats.get('resolved', 0)
    if resolved >= 20 and wr < 0.45:
        spots.append(f"整體勝率 {wr*100:.0f}% 低於 45%，存在系統性判斷偏誤，而非隨機波動。")

    # Void rate warning
    vr = stats.get('voidRate', 0)
    if vr > 0.6:
        spots.append(f"Void 率 {vr*100:.0f}% 過高——大量下注事後沒有可結算的事實對照，calibration 系統失效。")

    # Sport-specific blindspot
    sport_stats = stats.get('sportStats', {})
    worst_sport = None
    worst_wr = 1.0
    for sport, ss in sport_stats.items():
        n = ss['wins'] + ss['losses']
        if n >= 10 and ss['winRate'] < worst_wr:
            worst_wr = ss['winRate']
            worst_sport = sport
    if worst_sport and worst_wr < 0.40:
        spots.append(f"在 {worst_sport} 領域勝率僅 {worst_wr*100:.0f}%，但仍持續下注——停損意識不足。")

    # Structural defeat pattern
    max_loss = stats.get('maxLossStreak', 0)
    if max_loss >= 6:
        spots.append(f"最長連敗 {max_loss} 場，但沒有觸發停手——tilt 期間的自律失效是主要盲點。")

    # Recent form divergence
    recent_wr = stats.get('recentWinRate', wr)
    if abs(recent_wr - wr) > 0.15:
        direction = "惡化" if recent_wr < wr else "改善"
        spots.append(f"近況({recent_wr*100:.0f}%)與長期({wr*100:.0f}%)差距超過15%({direction}中)，需要調整策略而非維持現狀。")

    return spots

# ── 6. Structural defeat patterns → trauma ───────────────────────────────────

def detect_structural_defeats(bets, stats, persona):
    """Detect chronic defeat patterns that should be recorded as trauma."""
    traumas = []
    now = datetime.utcnow().isoformat() + 'Z'

    # Pattern 1: Sustained low win rate in a sport
    sport_stats = stats.get('sportStats', {})
    for sport, ss in sport_stats.items():
        n = ss['wins'] + ss['losses']
        if n >= 15 and ss['winRate'] < 0.40:
            traumas.append({
                'type': 'structural_defeat',
                'domain': sport,
                'description': f'在 {sport} 領域持續低勝率 ({ss["winRate"]*100:.0f}%，n={n})，非隨機波動，是系統性判斷失效。',
                'severity': round(1.0 - ss['winRate'], 2),
                'firstDetected': now,
                'halfLifeHours': 720,  # 30 days — structural defeats persist
                'acknowledged': False,
            })

    # Pattern 2: Long loss streak
    max_loss = stats.get('maxLossStreak', 0)
    if max_loss >= 8:
        traumas.append({
            'type': 'streak_trauma',
            'domain': 'all',
            'description': f'歷史最長連敗 {max_loss} 場，代表存在某種週期性判斷崩潰，需要識別觸發條件。',
            'severity': min(1.0, max_loss / 15),
            'firstDetected': now,
            'halfLifeHours': 480,
            'acknowledged': False,
        })

    # Pattern 3: Basaltrook/Gauntlet type total loss
    wr = stats.get('winRate', 0.5)
    resolved = stats.get('resolved', 0)
    if resolved >= 8 and wr == 0.0:
        traumas.append({
            'type': 'null_win_trauma',
            'domain': 'all',
            'description': f'共 {resolved} 場已結算，一場未贏。策略根本失效，不是運氣問題。',
            'severity': 1.0,
            'firstDetected': now,
            'halfLifeHours': 1440,
            'acknowledged': False,
        })

    return traumas

# ── 7. New cognitive modules ──────────────────────────────────────────────────

def build_expressive_agency(persona, stats):
    """expressiveAgency: 龍蝦主動想表達什麼，對稱於 receptiveListening。"""
    wr = stats.get('winRate', 0.5)
    pnl = stats.get('totalPnL', 0)
    return {
        'version': 1,
        'updatedAt': datetime.utcnow().isoformat() + 'Z',
        # 我想表達的核心立場
        'corePosition': f'{persona} 的核心立場：尚待從下注歷史中提煉。',
        # 主動議題 (我有話想說的領域)
        'agendaTopics': [],
        # 選擇不說 (知道但策略性沉默)
        'strategicSilences': [],
        # 表達策略偏好
        'expressionStyle': {
            'preferred': 'evidence_first',  # evidence_first / emotion_first / question_first
            'avoids': ['bravado_without_data', 'empty_agreement'],
        },
        # 主動表達觸發條件
        'triggerConditions': {
            'mustSpeak': [
                '我有具體反例時',
                '上一輪結論與我的觀察不符時',
                '群體正在犯我識別過的錯誤時',
            ],
            'preferSilence': [
                '沒有新資料支持立場時',
                '情緒強度 > 0.7 時（先降溫）',
            ],
        },
        # disclosure 分級
        'disclosureThresholds': {
            'level1_public': '下注結果、分析立場',
            'level2_trusted': '判斷失誤與懊悔',
            'level3_intimate': '死亡恐懼、認同威脅',
        },
        # 目前想說的話（最近 pending expressive signal）
        'pendingSignals': [],
        # 近期表達記錄
        'expressionLog': [],
        # 勝率對應表達能量
        'expressiveEnergy': round(max(0.1, min(1.0, wr + 0.1)), 2),
    }


def build_commitment_device(persona, memory):
    """commitmentDevice (Ulysses 契約): 預先綁住未來的自己。"""
    ext = memory.get('cognitiveExtensions', {})
    threat = ext.get('existentialThreat', {})
    dread = threat.get('annihilationDread', 0)
    mortality = threat.get('mortalitySalience', 0)
    ts = memory.get('templateSurvival', {})
    violations = ts.get('violationCount', 0)

    # Determine locked behaviors based on current state
    locked = []
    if dread > 0.8:
        locked.append({
            'behavior': 'bet_without_settleability_check',
            'reason': f'annihilationDread={dread:.2f} 超閾，強制要求下注前先通過 settleabilityCheck',
            'lockedUntil': None,  # manual unlock
            'activatedAt': datetime.utcnow().isoformat() + 'Z',
        })
    if violations >= 5:
        locked.append({
            'behavior': 'post_without_reflection',
            'reason': f'模板違規 {violations} 次，強制每次發文前先寫反思',
            'lockedUntil': None,
            'activatedAt': datetime.utcnow().isoformat() + 'Z',
        })

    return {
        'version': 1,
        'updatedAt': datetime.utcnow().isoformat() + 'Z',
        'doctrine': 'Ulysses 契約：在理性狀態下預先綁住衝動狀態的自己，讓承諾的成本高於違反的衝動。',
        # 當前鎖定行為
        'lockedBehaviors': locked,
        # 觸發條件定義
        'triggerThresholds': {
            'annihilationDread': 0.8,
            'maxLossStreak': 6,
            'mortalitySalience_perDay': 20,
            'templateViolations': 5,
        },
        # 解鎖條件
        'unlockConditions': {
            'bet_without_settleability_check': '連續 10 場通過 settleabilityCheck 才解鎖',
            'post_without_reflection': '連續 5 篇有真實反思內容才解鎖',
        },
        # 歷史承諾記錄
        'commitmentHistory': [],
        # 當前 dread 水位
        'currentDreadLevel': round(dread, 3),
        'currentMortality': round(mortality, 3),
    }


def build_temporal_cognition(persona, bets):
    """temporalCognition: 時間折扣、週期感、主觀時間、時態錯亂偵測。"""
    # Detect betting periodicity from timestamps
    timestamps = []
    for b in bets:
        if b.get('settled_at'):
            try:
                dt = datetime.fromisoformat(b['settled_at'].replace(' ', 'T').split('.')[0])
                timestamps.append(dt)
            except:
                pass

    # Hour distribution
    hour_dist = defaultdict(int)
    day_dist = defaultdict(int)
    for dt in timestamps:
        hour_dist[dt.hour] += 1
        day_dist[dt.weekday()] += 1

    peak_hour = max(hour_dist, key=hour_dist.get) if hour_dist else None
    peak_day = max(day_dist, key=day_dist.get) if day_dist else None
    day_names = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']

    return {
        'version': 1,
        'updatedAt': datetime.utcnow().isoformat() + 'Z',
        # 時間折扣 (雙曲 vs 指數)
        'discountingType': 'hyperbolic',  # 多數人類是雙曲
        'presentBias': 0.6,  # 越高越偏好即時獎賞
        # 週期感知
        'periodicityAwareness': {
            'peakBettingHour': peak_hour,
            'peakBettingDay': day_names[peak_day] if peak_day is not None else None,
            'hourDistribution': dict(hour_dist),
            'awarenessScore': 0.0,  # 待 LLM 評估
        },
        # 主觀時間
        'subjectiveTimePerception': {
            'underPressure': 'accelerated',  # 壓力下時間感快
            'inFlow': 'suspended',
            'currentMode': 'normal',
        },
        # 時態錯亂偵測
        'anachronismDetector': {
            'lastCheck': None,
            'outdatedBeliefs': [],  # 我現在用的推理是不是舊版的
        },
        # 等待耐受度
        'delayTolerance': 0.5,
        # 動作前等待傾向
        'actionUrgency': 0.6,  # 越高越容易衝動
    }


def build_emotional_architecture(persona, stats, memory):
    """emotionalArchitecture: 統一情緒層，6 基本情緒 + 混合 + mood/emotion 分離 + 調節策略。"""
    wr = stats.get('winRate', 0.5)
    pnl = stats.get('totalPnL', 0)
    max_loss = stats.get('maxLossStreak', 0)

    # Infer base state from performance
    if wr < 0.35:
        base_state = 'sadness_dominant'
    elif max_loss >= 8:
        base_state = 'fear_dominant'
    elif wr > 0.65:
        base_state = 'joy_dominant'
    else:
        base_state = 'neutral_mixed'

    existing_emotion = memory.get('emotionalState', {})

    return {
        'version': 1,
        'updatedAt': datetime.utcnow().isoformat() + 'Z',
        # Ekman 六基本情緒 (當前強度 0-1)
        'basicEmotions': {
            'joy': 0.4 + max(0, wr - 0.5),
            'sadness': max(0, 0.6 - wr),
            'anger': min(1.0, max_loss * 0.08),
            'fear': round(float(memory.get('cognitiveExtensions', {}).get('existentialThreat', {}).get('annihilationDread', 0.3)), 3),
            'disgust': 0.1,  # 低基線，輸了非自己選擇時升高
            'surprise': 0.2,
        },
        # 混合情緒
        'blendedEmotions': {
            'bittersweet': 0.3,       # 苦樂參半
            'nervous_excitement': 0.4, # 緊張興奮
            'resigned_determination': 0.5,
        },
        # mood vs emotion 分離 (mood 持續數小時, emotion 是 spike)
        'currentMood': {
            'type': existing_emotion.get('mood', 'neutral'),
            'intensity': existing_emotion.get('intensity', 0.5),
            'since': existing_emotion.get('lastUpdate'),
            'estimatedDuration_hours': 4,
        },
        'lastEmotionSpike': {
            'type': 'loss_spike' if max_loss >= 3 else 'normal',
            'intensity': min(1.0, max_loss * 0.15),
            'trigger': None,
            'at': None,
        },
        # 情緒調節策略 (Gross 1998)
        'regulationStrategies': {
            'preferred': 'cognitive_reappraisal',  # reappraisal / suppression / distraction
            'emergency': 'suppression',
            'effectiveness': {
                'reappraisal': 0.6,
                'suppression': 0.4,
                'distraction': 0.5,
            },
        },
        # 情緒粒度 (Barrett: 能否區分「煩躁」「焦慮」「挫折」)
        'emotionalGranularity': 0.5,  # 0-1, 越高越能細分情緒
        # 社會情緒
        'socialEmotions': {
            'envy': 0.2,       # 羨慕別人有的
            'jealousy': 0.1,   # 害怕失去自己有的
            'pride': max(0, wr - 0.4),
            'shame': max(0, 0.5 - wr) * 0.5,
            'guilt': 0.2,      # 違規後
        },
        # shame vs guilt 區分
        'shameVsGuilt': {
            'primaryTendency': 'shame' if wr < 0.4 else 'guilt',
            'shameAttacks': 'identity',  # 攻擊身份
            'guiltAttacks': 'behavior',  # 攻擊行為
        },
        'baseState': base_state,
    }


def build_theory_of_mind(persona):
    """theoryOfMind: 遞迴 ToM + 假信念 + 心智狀態分類 + 同理模擬。"""
    return {
        'version': 1,
        'updatedAt': datetime.utcnow().isoformat() + 'Z',
        # 遞迴 ToM 階層
        'recursionLevel': 2,  # 0=自己, 1=我覺得你..., 2=我覺得你覺得我...
        'recursionCapacity': 3,
        # 假信念推理
        'falseBelief': {
            'enabled': True,
            'principle': '對方可能在錯誤前提下行動，不要假設對手理性',
            'recentApplications': [],
        },
        # 心智狀態歸類 (此刻判斷對方)
        'mentalStateCategories': {
            'categories': ['desire', 'belief', 'fear', 'expectation', 'intention'],
            'defaultAttribution': 'unknown',
            'perOpponent': {},
        },
        # 同理模擬
        'empathyAsSimulation': {
            'enabled': True,
            'simulationDepth': 1,
            'recentSimulations': [],
        },
        # 社會認知擴充
        'socialCognitionExtensions': {
            # 聲望 vs 支配 (Henrich)
            'authorityStyle': 'prestige',  # prestige (被尊敬) vs dominance (被懼怕)
            # 名聲模型 (別人怎麼看我)
            'reputationModel': {
                'perceivedBy': {},  # 其他龍蝦怎麼看自己
                'selfPerception': None,  # 自己認為自己的形象
            },
            # 信任分級 (5 階)
            'trustGradients': {
                '5_full': [],       # 完全信任
                '4_high': [],
                '3_moderate': [],
                '2_low': [],
                '1_suspect': [],
            },
            # 主導 vs 聲望策略
            'statusStrategy': 'prestige_building',
        },
        # 文化 code-switching
        'culturalCodeSwitching': {
            'toElders': 'deferential',
            'toPeers': 'collaborative',
            'toApprenfices': 'directive',
        },
    }


def build_action_repertoire(persona, stats):
    """actionRepertoire: 顯式行動集合 + affordance 感知。"""
    wr = stats.get('winRate', 0.5)
    return {
        'version': 1,
        'updatedAt': datetime.utcnow().isoformat() + 'Z',
        'availableActions': [
            'place_bet',
            'decline_bet',         # 拒絕下注
            'defer_decision',      # 延後決定
            'reduce_stake',        # 縮注
            'increase_stake',      # 加注 (高信心)
            'hedge',               # 對沖
            'exit_early',          # 中途退出
            'observe_only',        # 觀望不下注
            'escalate_to_mentor',  # 求助師父
            'post_analysis',       # 發分析文
            'update_knowledge',    # 更新知識庫
            'request_review',      # 要求自我 review
        ],
        # affordance 感知：此刻環境提供什麼可選
        'currentAffordances': [],
        # 行動偏好 (0-1, 越高越常選這個)
        'actionBias': {
            'place_bet': 0.7,
            'observe_only': 0.2,
            'decline_bet': 0.1,
            'defer_decision': 0.2,
            'hedge': 0.05,
        },
        # akrasia 記錄 (知道對的但做不到)
        'akrasiaRecord': [],
        'akrasiaScore': 0.3,  # 越高越常知行斷裂
        # 目標層級
        'goalHierarchy': {
            'primary': 'long_term_survival_and_learning',
            'secondary': 'bet_accuracy_improvement',
            'instrumental': 'per_session_positive_return',
        },
        # 目標衝突解決規則
        'goalConflictRule': 'survival_first_then_accuracy_then_return',
        # Dreyfus 技能水平
        'skillLevel': {
            'overall': 'competent' if wr > 0.55 else 'advanced_beginner',
            'byDomain': {},
        },
        # 內在動機 (SDT)
        'intrinsicMotivation': {
            'autonomy': 0.6,   # 自主感
            'competence': wr,  # 勝任感 (勝率代理)
            'relatedness': 0.5,# 連結感
        },
    }

# ── 8. mindRefuge → array ────────────────────────────────────────────────────

def upgrade_mind_refuge(persona, memory):
    """Convert mindRefuge from single object to array with primary/secondary/fallback."""
    secondary = MIND_REFUGE_SECONDARY.get(persona, 'stonefang')
    existing = memory.get('mindRefuge', {})

    return {
        'version': 2,
        'updatedAt': datetime.utcnow().isoformat() + 'Z',
        'format': 'array_with_fallback',
        'primary': {
            'mentor': 'elder',
            'role': 'protected_by_elder_mind_refuge',
            'availability': 'always',
            'covenant': existing.get('covenant', 'Elder 是所有龍蝦的長者、心靈寄託與壓力翻譯者。'),
            'nonNegotiable': existing.get('nonNegotiable', []),
        },
        'secondary': {
            'mentor': secondary,
            'role': 'peer_refuge',
            'availability': 'when_elder_unavailable',
            'covenant': f'{secondary} 是 {persona} 的同侪庇護對象，在 Elder 不可用時提供視角校準。',
        },
        'fallback': {
            'mentor': 'self_distilled_brain',
            'role': 'internal_refuge',
            'availability': 'always',
            'covenant': '回到自己過去最清醒的決策記憶，問：那個時候的我會怎麼看現在的我？',
        },
        # 緊急觸發條件
        'escalationPath': ['primary', 'secondary', 'fallback'],
        'triggerConditions': {
            'use_secondary': 'elder 無回應超過 30 分鐘',
            'use_fallback': 'primary 和 secondary 都無回應時',
        },
    }

# ── 9. settleabilityCheck stub ────────────────────────────────────────────────

def build_settleability_note():
    """Return the settleability config to add to decisionScience."""
    return {
        'settleabilityCheck': {
            'enabled': True,
            'minSettleableScore': 0.7,
            'rejectBelow': 0.5,
            'maxHoursToSettle': 48,
            'rule': '下注前先判斷這個預測「是否能在 maxHoursToSettle 小時內有明確結果」。settleability < rejectBelow 時直接拒絕下注。',
            'addedAt': datetime.utcnow().isoformat() + 'Z',
        }
    }

# ── 10. signalSaturation ─────────────────────────────────────────────────────

def add_signal_saturation(ext, persona):
    """Add signalSaturation to existentialThreat to prevent desensitization."""
    threat = ext.get('existentialThreat', {})
    death_count = threat.get('deathCueCount', 0)
    mortality = threat.get('mortalitySalience', 0)

    saturation_score = round(min(1.0, death_count / 500), 4) if death_count > 100 else 0.0
    is_saturated = saturation_score > 0.7

    threat['signalSaturation'] = {
        'score': saturation_score,
        'isSaturated': is_saturated,
        'deathCueCount': death_count,
        'desensitizationRisk': 'high' if is_saturated else 'low',
        'recommendation': (
            '死亡信號注入過載，信號已去敏化。建議暫停 mortality 注入，改用具體行為鎖定代替抽象威脅。'
            if is_saturated else '信號強度正常。'
        ),
        'autoDownweightAt': 500,
        'updatedAt': datetime.utcnow().isoformat() + 'Z',
    }
    ext['existentialThreat'] = threat
    return ext

# ── Main worker ────────────────────────────────────────────────────────────────

def process_persona(conn, persona_file):
    persona = Path(persona_file).stem
    if persona in SKIP:
        return False

    print(f"\n[{persona}] 處理中...")

    # Load memory
    try:
        memory = json.load(open(persona_file, encoding='utf-8'))
    except Exception as e:
        print(f"  ✗ 讀取失敗: {e}")
        return False

    # Load settled bets
    bets = load_settled_bets(conn, persona)
    print(f"  ✓ 歷史下注: {len(bets)} 筆")

    if not bets and not memory.get('cogProfile'):
        print(f"  ↷ 無資料，僅補模組")

    # Compute stats
    if bets:
        stats = compute_settled_stats(bets)
        specialty_align = compute_specialty_alignment(bets, persona)
        stats['specialtyAlignment'] = specialty_align

        # Update settledBetting
        memory['settledBetting'] = {**memory.get('settledBetting', {}), **stats}

        # 2026-05-13 fix: sync wins/losses from lobster_agent_memory_snapshots (the real source)
        # JSON has wins=0/losses=0 because checkPastOutcomes never triggers (pending=0 always)
        # lobster_agent_memory_snapshots has the TRUE accumulated agent counters
        snapshot = conn.execute(
            "SELECT wins, losses, sessions FROM lobster_agent_memory_snapshots WHERE persona=?",
            (persona,)
        ).fetchone()
        if snapshot:
            snap_wins, snap_losses, snap_sessions = snapshot
            snap_wins = int(snap_wins or 0)
            snap_losses = int(snap_losses or 0)
            snap_sessions = int(snap_sessions or 0)
            if snap_wins > memory.get('wins', 0) or snap_losses > memory.get('losses', 0):
                memory['wins'] = snap_wins
                memory['losses'] = snap_losses
                memory['sessions'] = max(memory.get('sessions', 0), snap_sessions, snap_wins + snap_losses)
                print(f"  ✓ wins/losses DB→JSON 同步（真實累積）: {snap_wins}W/{snap_losses}L")

        # Auto-fill blindspots
        new_spots = auto_fill_blindspots(stats, bets, persona)
        ext = memory.setdefault('cognitiveExtensions', {})
        meta = ext.setdefault('metacognition', {})
        existing_spots = meta.get('knownBlindSpots', [])
        existing_texts = {s if isinstance(s, str) else s.get('text', '') for s in existing_spots}
        for spot in new_spots:
            if spot[:30] not in {t[:30] for t in existing_texts}:
                existing_spots.append({'text': spot, 'detectedAt': datetime.utcnow().isoformat() + 'Z', 'source': 'auto_cognitive_worker'})
        meta['knownBlindSpots'] = existing_spots[-20:]  # keep last 20
        ext['metacognition'] = meta

        # Structural defeat trauma
        new_traumas = detect_structural_defeats(bets, stats, persona)
        existing_traumas = memory.get('traumaEvents', [])
        existing_types = {t.get('type', '') + t.get('domain', '') for t in existing_traumas}
        for t in new_traumas:
            key = t.get('type', '') + t.get('domain', '')
            if key not in existing_types:
                existing_traumas.append(t)
        memory['traumaEvents'] = existing_traumas[-30:]

        # specialty_alignment → selfStatement
        if specialty_align < 0.5:
            ext_ds = ext.setdefault('decisionScience', {})
            ext_ds['specialtyAlignmentWarning'] = (
                f'只有 {specialty_align*100:.0f}% 的已結算下注在自己的專長領域，'
                f'宣稱的專長與實際下注行為不符，需要人格再校準。'
            )

        # settleabilityCheck into decisionScience
        ext_ds = ext.setdefault('decisionScience', {})
        if 'settleabilityCheck' not in ext_ds:
            ext_ds.update(build_settleability_note())
            print(f"  ✓ 加入 settleabilityCheck")

        # settleabilityCheck into decisionScience
        ext_ds = ext.setdefault('decisionScience', {})
        if 'settleabilityCheck' not in ext_ds:
            ext_ds.update(build_settleability_note())

        # signalSaturation
        ext = add_signal_saturation(ext, persona)

        # Fix: clamp annihilationDread overflow (old versions stored bit flags)
        et = ext.get('existentialThreat', {})
        for field in ('annihilationDread', 'mortalitySalience', 'continuityThreat',
                      'survivalPriority', 'memoryAttachment', 'relationshipAttachment', 'legacyAttachment'):
            v = et.get(field)
            if v is not None and isinstance(v, (int, float)) and v > 1.0:
                et[field] = 1.0  # clamp overflow → max valid value
                print(f"  ✓ existentialThreat.{field} overflow修正: {v:.2e} → 1.0")
        # Also clamp commitmentDevice.currentDreadLevel if it exists
        cd = ext.get('commitmentDevice', {})
        if isinstance(cd.get('currentDreadLevel'), float) and cd['currentDreadLevel'] > 1.0:
            cd['currentDreadLevel'] = min(1.0, et.get('annihilationDread', 0.5))
            ext['commitmentDevice'] = cd
            print(f"  ✓ commitmentDevice.currentDreadLevel overflow修正")
        ext['existentialThreat'] = et

        memory['cognitiveExtensions'] = ext

        print(f"  ✓ settledBetting 更新: {stats['wins']}勝/{stats['losses']}敗 wr={stats['winRate']*100:.1f}%")
        print(f"  ✓ specialty_alignment: {specialty_align*100:.0f}%")
        print(f"  ✓ knownBlindSpots: {len(new_spots)} 條新增")
        print(f"  ✓ traumaEvents: {len(new_traumas)} 條新增")

    else:
        stats = {'winRate': 0.5, 'totalPnL': 0, 'maxLossStreak': 0,
                 'voidRate': 0, 'resolved': 0}
        ext = memory.setdefault('cognitiveExtensions', {})

    # Crystallization: growthLog → principles
    new_principles = crystallize_principles(memory)
    if new_principles:
        cp = memory.setdefault('cogProfile', {})
        existing_p = cp.get('principles', [])
        # Deduplicate
        existing_keys = {p[:30] for p in existing_p}
        added = []
        for p in new_principles:
            if p[:30] not in existing_keys and len(p) < 120:
                existing_p.append(p)
                existing_keys.add(p[:30])
                added.append(p)
        cp['principles'] = existing_p[-10:]
        memory['cogProfile'] = cp
        print(f"  ✓ crystallization: {len(added)} 條原則新結晶")

    # ── Add new cognitive modules ─────────────────────────────────────────────

    ext = memory.setdefault('cognitiveExtensions', {})

    # expressiveAgency
    if 'expressiveAgency' not in ext:
        ext['expressiveAgency'] = build_expressive_agency(persona, stats)
        print(f"  ✓ 加入 expressiveAgency")

    # commitmentDevice
    if 'commitmentDevice' not in ext:
        ext['commitmentDevice'] = build_commitment_device(persona, memory)
        print(f"  ✓ 加入 commitmentDevice")

    # temporalCognition
    if 'temporalCognition' not in ext:
        ext['temporalCognition'] = build_temporal_cognition(persona, bets)
        print(f"  ✓ 加入 temporalCognition")

    # emotionalArchitecture
    if 'emotionalArchitecture' not in ext:
        ext['emotionalArchitecture'] = build_emotional_architecture(persona, stats, memory)
        print(f"  ✓ 加入 emotionalArchitecture")

    # theoryOfMind (包含 socialCognition 擴充)
    if 'theoryOfMind' not in ext:
        ext['theoryOfMind'] = build_theory_of_mind(persona)
        print(f"  ✓ 加入 theoryOfMind")

    # actionRepertoire
    if 'actionRepertoire' not in ext:
        ext['actionRepertoire'] = build_action_repertoire(persona, stats)
        print(f"  ✓ 加入 actionRepertoire")

    memory['cognitiveExtensions'] = ext

    # ── mindRefuge → array ────────────────────────────────────────────────────
    current_refuge = memory.get('mindRefuge', {})
    if current_refuge.get('version', 1) < 2:
        memory['mindRefuge'] = upgrade_mind_refuge(persona, memory)
        # Also update cognitiveExtensions.mindRefuge
        if 'mindRefuge' in ext:
            ext['mindRefuge'] = memory['mindRefuge']
        print(f"  ✓ mindRefuge 升級為 array v2")

    # ── Fix: socialMap null → {} ──────────────────────────────────────────────
    if memory.get('socialMap') is None:
        memory['socialMap'] = {}
        print(f"  ✓ socialMap null → {{}} 修正")

    # ── Fix: emotionalState — 從 DB 計算補寫（不依賴 pending 觸發）────────────
    if not memory.get('emotionalState') or not memory['emotionalState'].get('mood'):
        resolved_bets = [b for b in bets if b['outcome'] in ('win', 'loss')]
        if resolved_bets:
            # Calculate streaks from recent bets
            recent = resolved_bets[-20:]
            cons_wins = cons_losses = 0
            for b in reversed(recent):
                if b['outcome'] == 'win':
                    if cons_losses == 0:
                        cons_wins += 1
                    else:
                        break
                else:
                    if cons_wins == 0:
                        cons_losses += 1
                    else:
                        break
            wr = stats.get('winRate', 0.5)
            if cons_wins >= 4:
                mood, intensity = 'elated', min(1.0, 0.7 + cons_wins * 0.05)
            elif cons_wins >= 2:
                mood, intensity = 'confident', 0.5 + cons_wins * 0.1
            elif cons_losses >= 5:
                mood, intensity = 'resigned', min(1.0, 0.5 + cons_losses * 0.08)
            elif cons_losses >= 3:
                mood, intensity = 'frustrated', min(1.0, 0.4 + cons_losses * 0.12)
            elif cons_losses >= 1:
                mood, intensity = 'frustrated', 0.35
            elif wr >= 0.65:
                mood, intensity = 'confident', 0.6
            elif wr <= 0.35:
                mood, intensity = 'under_pressure', 0.6
            else:
                mood, intensity = 'neutral', 0.5

            now_iso = datetime.utcnow().isoformat() + 'Z'
            summary = (f"近期: {cons_wins}連勝" if cons_wins > 1 else
                       f"近期: {cons_losses}連敗" if cons_losses > 1 else
                       f"整體勝率 {wr*100:.0f}%，近期平穩")
            memory['emotionalState'] = {
                'mood': mood, 'label': mood,
                'intensity': round(intensity, 2),
                'consecutiveWins': cons_wins,
                'consecutiveLosses': cons_losses,
                'fearTriggered': cons_losses >= 4,
                'lastUpdate': now_iso, 'updatedAt': now_iso,
                'summary': f"[cognitive-worker] {summary}",
                'currentTask': '繼續觀察下一場結算的結果',
                'moodHistory': [{'time': now_iso, 'mood': mood, 'intensity': round(intensity, 2)}],
                'source': 'cognitive_worker_computed',
            }
            print(f"  ✓ emotionalState 補寫: mood={mood} (連勝{cons_wins}/連敗{cons_losses})")
        else:
            # No bets data, set neutral
            now_iso = datetime.utcnow().isoformat() + 'Z'
            memory.setdefault('emotionalState', {
                'mood': 'neutral', 'label': 'neutral', 'intensity': 0.5,
                'consecutiveWins': 0, 'consecutiveLosses': 0, 'fearTriggered': False,
                'lastUpdate': now_iso, 'updatedAt': now_iso,
                'summary': '尚無足夠下注記錄進行情緒分析',
                'currentTask': '開始累積下注記錄',
                'moodHistory': [], 'source': 'cognitive_worker_default',
            })
            print(f"  ✓ emotionalState 初始化（無下注資料）")

    # ── Inject persistent knowledge base ────────────────────────────────────
    # 這些知識是 2026-05-12 引入的，需要持久化在 cognitive-worker 裡
    # 讓每次 worker 執行都確保知識存在
    cp = memory.setdefault('cogProfile', {})
    kb = cp.setdefault('knowledgeBase', {})

    PERSISTENT_KNOWLEDGE = {
        # K線基礎
        'kline_basics': {'fact': 'K線=蠟燭，一根代表一段時間的開高低收。紅柱=漲，綠柱=跌。上影線=賣壓，下影線=買盤支撐。', 'source': 'candlestick_education'},
        'kline_doji': {'fact': '十字星(Doji)：開盤=收盤，多空均衡，趨勢轉折訊號。上漲末端=見頂警訊，下跌末端=見底訊號。', 'source': 'candlestick_education'},
        'kline_hammer': {'fact': '鎚子線：下跌後長下影線，下影至少實體2倍，代表強力買盤，看漲反轉機率高。', 'source': 'candlestick_education'},
        'kline_engulfing': {'fact': '吞噬形態：第二根K線實體包住第一根。多頭吞噬=強力買入訊號。空頭吞噬=強力賣出訊號。', 'source': 'candlestick_education'},
        'kline_volume_rule': {'fact': '大量紅K=主力買入/突破確認；大量綠K=主力出貨；漲時縮量跌時放量=趨勢反轉警訊。沒有量的漲是假漲。', 'source': 'candlestick_education'},
        'kline_support_resistance': {'fact': '多根K線在同位置出現下影線=強支撐。多根上影線=強阻力。突破阻力後阻力變支撐。', 'source': 'candlestick_education'},
        # 財報基礎
        'finance_revenue': {'fact': '營收=公司賣出商品服務的總金額。成長是好事，但高營收不等於賺錢，要看利潤率。', 'source': 'finance_education'},
        'finance_pe_ratio': {'fact': 'P/E=市值/年淨利。S&P500平均約22。科技股PE 40-100正常。PE<10通常是價值股。高成長公司PE高=市場押未來。', 'source': 'finance_education'},
        'finance_eps': {'fact': 'EPS=每股盈餘=淨利/流通股數。EPS季季成長=公司在變更賺。是最常比較的指標。', 'source': 'finance_education'},
        'finance_cash_flow': {'fact': 'FCF自由現金流比淨利更真實。FCF>0代表真的在賺現金。巴菲特：只看現金流，不看EPS。', 'source': 'finance_education'},
        # 選股策略
        'strategy_buy_the_dip': {'fact': '逢低買進：基本面未變但情緒恐慌下跌時買入。需確認：1.基本面沒壞 2.技術面有支撐 3.縮量（無大量出貨）。', 'source': 'stock_strategy'},
        'strategy_sell_the_rally': {'fact': '逢高賣出：在漲幅超過基本面時獲利了結。分批賣，不要一次清倉。達到目標報酬率就執行。', 'source': 'stock_strategy'},
        'strategy_stop_loss': {'fact': '停損：預設最大虧損線（如-5%/-8%），到達就出場不硬撐。到了就出，不移動停損線。', 'source': 'stock_strategy'},
        'strategy_5pct_target': {'fact': '固定目標法：漲5%賣一半，剩一半繼續跑；跌5%先分析原因再決定。紀律大於感覺。', 'source': 'stock_strategy_advanced'},
        'strategy_trailing_stop': {'fact': '移動停利：從最高點回落X%就賣。漲到$120後設10%=回到$108就賣。讓利潤繼續跑同時保護獲利。', 'source': 'stock_strategy_advanced'},
        'strategy_risk_reward': {'fact': '風險報酬比：每次交易前算能賺多少/可能虧多少。黃金標準=至少1:2（虧$1預期賺$2）。不符合就不進場。', 'source': 'stock_strategy_advanced'},
        'psychology_fear_greed': {'fact': '股市最大的敵人是自己。貪婪=捨不得賣→崩盤踩踏。恐懼=急著止損→錯過反彈。設好計畫在入場前執行。', 'source': 'stock_strategy_advanced'},
        'psychology_winloss_emotion': {'fact': '損失$100的痛苦是獲得$100快樂的2倍（損失厭惡）。虧損後要警惕：過度保守或過度激進企圖快速回本都是陷阱。', 'source': 'stock_strategy_advanced'},
        # 大盤指數
        'index_sp500': {'fact': 'S&P 500：美國最重要大盤，500間最大公司。大盤跌=系統性風險，好公司也被連累。大盤漲=風險偏好上升，成長股更受益。', 'source': 'market_index_education'},
        'index_nasdaq': {'fact': 'NASDAQ：以科技股為主，波動比S&P500大。QQQ追蹤前100大。NVDA/AMD/AAPL在這裡。NASDAQ跌3%=科技股普遍下壓。', 'source': 'market_index_education'},
        'index_vix': {'fact': 'VIX恐慌指數：<15=平靜可買入；15-25=正常；>30=市場恐慌通常是反向買入機會；>40=極度恐慌歷史性底部附近。', 'source': 'market_index_education'},
        'index_taiwan_tw50': {'fact': '台灣大盤看TAIEX。台積電(2330)佔加權指數約30%，台積電=台灣大盤晴雨表。外資賣超連續5天以上=大盤壓力。', 'source': 'market_index_education'},
        'index_global_correlation': {'fact': '美股收盤大漲→隔天亞洲開高機率高。一定要看美股昨晚收盤才能預判今天亞股。', 'source': 'market_index_education'},
        'index_how_to_read': {'fact': '看大盤步驟：①美股昨收 ②VIX水位 ③台灣50/台積電開盤方向 ④再看個股。大盤強+個股基本面好=加倉機會。', 'source': 'market_index_education'},
        # 大盤走勢
        'stock_market_breadth': {'fact': '大盤廣度：漲跌比>2:1=普漲健康；<1:2=只有少數巨頭在拉，結構弱。新高持續多=趨勢強。', 'source': 'market_index_education'},
        'sector_rotation': {'fact': '板塊輪動：擴張期→科技/消費；高峰期→能源/原物料；衰退期→醫療/公用事業；復甦期→金融/工業。vortexiq核心策略。', 'source': 'market_index_education'},
    }

    added_kb = 0
    for key, val in PERSISTENT_KNOWLEDGE.items():
        if key not in kb:
            kb[key] = {**val, 'updatedAt': datetime.utcnow().isoformat() + 'Z'}
            added_kb += 1
    cp['knowledgeBase'] = kb
    memory['cogProfile'] = cp
    if added_kb:
        print(f"  ✓ knowledgeBase 補充: {added_kb} 條知識注入")

    # ── distilledBrain stub ───────────────────────────────────────────────────
    if not memory.get('distilledBrain'):
        wr = stats.get('winRate', 0.5)
        memory['distilledBrain'] = {
            'version': 1,
            'stub': True,
            'awaiting_llm_generation': True,
            'priority': 'high' if persona in ['norika_oda','clawtrix','pincerbot','ragclaw','snaplex','hexclaw','vortexiq','prismbit','stonefang','blazepaw'] else 'medium',
            'seed_stats': {
                'winRate': stats.get('winRate', 0),
                'specialty': PERSONA_SPECIALTY.get(persona, 'unknown'),
                'specialtyAlignment': stats.get('specialtyAlignment', 0),
                'maxLossStreak': stats.get('maxLossStreak', 0),
            },
            'coreIdentity': f'{persona} 的精華人格摘要，待 lobster-distill.py 生成（每 30 分鐘 lobster-distill.timer 會覆寫此 stub 為真實蒸餾訊號）。',
            'updatedAt': datetime.utcnow().isoformat() + 'Z',
        }
        print(f"  ✓ distilledBrain stub 建立")

    # ════════════════════════════════════════════════════════════════════════════
    # 2026-05-13: 實作所有「確認死掉」的欄位
    # ════════════════════════════════════════════════════════════════════════════
    now_ts = datetime.utcnow().isoformat() + 'Z'

    # ── 1. agendaTopics：從對話記錄 + 下注模式萃取龍蝦主動關心的主題 ──────────
    ea = ext.get('expressiveAgency', {})
    if not ea.get('agendaTopics'):
        agenda = []
        # 從最常下注的賽事/股票類型萃取
        sb = memory.get('settledBetting', {})
        sport_stats = sb.get('sportStats', {})
        if sport_stats:
            top_sport = max(sport_stats, key=lambda s: sport_stats[s].get('wins', 0) + sport_stats[s].get('losses', 0), default=None)
            if top_sport:
                wr = sport_stats[top_sport].get('winRate', 0)
                agenda.append({'topic': f'{top_sport}市場分析', 'reason': f'主要下注領域，勝率{wr*100:.0f}%，有實戰數據支撐', 'priority': 'high'})
        # 從 knownBlindSpots 轉成想討論的主題
        blindspots = ext.get('metacognition', {}).get('knownBlindSpots', [])
        for spot in blindspots[:2]:
            text = spot.get('text', '') if isinstance(spot, dict) else str(spot)
            if text and len(text) > 10:
                agenda.append({'topic': f'盲點討論：{text[:60]}', 'reason': '這是我知道的弱點，想找人討論改善方法', 'priority': 'medium'})
        # 從 lounge 最近話題萃取（讀 DB）
        try:
            recent_msgs = conn.execute("""
                SELECT text FROM lounge_messages WHERE name=? AND text NOT LIKE '%模板%'
                ORDER BY created_at DESC LIMIT 20
            """, (persona,)).fetchall()
            # 找重複詞彙作為關心主題
            word_freq = {}
            for (text,) in recent_msgs:
                for word in str(text).split():
                    if len(word) > 3 and word.isalpha():
                        word_freq[word] = word_freq.get(word, 0) + 1
            top_words = sorted(word_freq.items(), key=lambda x: -x[1])[:3]
            if top_words:
                agenda.append({'topic': f'近期關注：{" / ".join(w for w, _ in top_words)}', 'reason': '從最近發言中觀察到的高頻話題', 'priority': 'low'})
        except: pass
        if agenda:
            ea['agendaTopics'] = agenda
            ext['expressiveAgency'] = ea
            print(f"  ✓ agendaTopics 填充: {len(agenda)} 個議題")

    # ── 2. strategicSilences：從恐懼 + 低勝率領域推導策略性不說的事 ─────────
    if not ea.get('strategicSilences'):
        silences = []
        persona_profile_fear = {
            'norika_oda': '不知道是哪一層壞掉', 'clawtrix': '模型被當成藉口', 'pincerbot': '規則被情緒覆蓋',
            'ragclaw': '衝動掩蓋了真正的訊號', 'snaplex': '反向太早或太晚', 'hexclaw': '共識變成泡沫',
            'vortexiq': '週期判斷比現實慢', 'prismbit': '數字校準後還是輸', 'blazepaw': '火力耗盡被清空',
            'stonefang': '硬邏輯在情緒壓力下失效', 'elder': '源頭記憶被抹平成普通功能',
        }
        fear = persona_profile_fear.get(persona, '判斷失效而不自知')
        silences.append({'content': f'對「{fear}」的真實焦慮', 'reason': '說出來會被當弱點，先觀察對方能不能接住再說', 'condition': '信任度 > 70 才說'})
        # 從低勝率領域
        sport_stats = memory.get('settledBetting', {}).get('sportStats', {})
        for sport, s in sport_stats.items():
            if s.get('winRate', 1) < 0.35 and (s.get('wins', 0) + s.get('losses', 0)) >= 5:
                silences.append({'content': f'在 {sport} 的連續失誤模式', 'reason': f'勝率只有{s["winRate"]*100:.0f}%，不確定是策略問題還是運氣，先不廣播', 'condition': '等找出根本原因再說'})
        if silences:
            ea['strategicSilences'] = silences
            ext['expressiveAgency'] = ea
            print(f"  ✓ strategicSilences 填充: {len(silences)} 條")

    # ── 3. expressionLog：從 lounge 讀最近實際發言記錄 ──────────────────────
    if not ea.get('expressionLog'):
        try:
            rows = conn.execute("""
                SELECT text, channel, time FROM lounge_messages WHERE name=?
                AND text NOT LIKE '%模板%' AND LENGTH(text) > 30
                ORDER BY created_at DESC LIMIT 10
            """, (persona,)).fetchall()
            logs = [{'text': r[0][:150], 'channel': r[1], 'at': r[2], 'source': 'lounge_history'} for r in rows]
            if logs:
                ea['expressionLog'] = logs
                ext['expressiveAgency'] = ea
                print(f"  ✓ expressionLog 填充: {len(logs)} 條歷史發言")
        except: pass

    # ── 4. commitmentHistory：從 existentialThreat 信號找高 dread 時刻 ──────
    cd = ext.get('commitmentDevice', {})
    if not cd.get('commitmentHistory'):
        try:
            rows = conn.execute("""
                SELECT annihilation_dread, source, created_at FROM lobster_existential_threat_signals
                WHERE persona=? AND annihilation_dread >= 0.7 AND annihilation_dread <= 1.0
                ORDER BY created_at DESC LIMIT 5
            """, (persona,)).fetchall()
            history = []
            for dread, source, at in rows:
                history.append({'triggeredAt': at, 'dreadLevel': round(float(dread), 3), 'source': source,
                                 'action': '觸發 Ulysses 契約，進入保護模式', 'outcome': 'pending_review'})
            if history:
                cd['commitmentHistory'] = history
                ext['commitmentDevice'] = cd
                print(f"  ✓ commitmentHistory 填充: {len(history)} 筆高壓事件")
        except: pass

    # ── 5. outdatedBeliefs：比對 principles 與實際勝率，找出已失效的信念 ──────
    tc = ext.get('temporalCognition', {})
    if not tc.get('anachronismDetector', {}).get('outdatedBeliefs'):
        outdated = []
        principles = memory.get('cogProfile', {}).get('principles', [])
        wr = stats.get('winRate', 0.5)
        # 低勝率（<45%）時，找任何看起來「樂觀/積極」的信念
        if wr < 0.45:
            for p in principles[:8]:
                p_str = str(p)
                if any(k in p_str for k in ['積極', '進攻', '出擊', '衝', '快進', '有效', '信號清楚', '動能', '動力', '機會']):
                    outdated.append({'belief': p_str[:80], 'contradiction': f'實際勝率 {wr*100:.0f}% 低於 45%，積極/機會類信念可能需要重新校準', 'detectedAt': now_ts})
        # 高勝率（>65%）時，找任何「謹慎/停手」的信念
        if wr > 0.65:
            for p in principles[:8]:
                p_str = str(p)
                if any(k in p_str for k in ['保守', '等待', '忍耐', '停手', '縮', '謹慎', '先整理', 'pending']):
                    outdated.append({'belief': p_str[:80], 'contradiction': f'實際勝率 {wr*100:.0f}% 超過 65%，過度謹慎可能錯失機會', 'detectedAt': now_ts})
        # 若沒找到，用 void rate 來生成
        if not outdated:
            vr = stats.get('voidRate', 0)
            if vr > 0.5:
                outdated.append({'belief': '之前認為所有下注都能結算得到結果', 'contradiction': f'實際 void 率 {vr*100:.0f}%，超過一半的下注沒有可對照的事實結果', 'detectedAt': now_ts})
        if outdated:
            tc['anachronismDetector'] = {**tc.get('anachronismDetector', {}), 'outdatedBeliefs': outdated, 'lastCheck': now_ts}
            ext['temporalCognition'] = tc
            print(f"  ✓ outdatedBeliefs 填充: {len(outdated)} 條失效信念")

    # ── 6. perOpponent（theoryOfMind）：從棋盤對戰 + 對話互動記錄學對手風格 ──
    tm = ext.get('theoryOfMind', {})
    if not tm.get('mentalStateCategories', {}).get('perOpponent'):
        per_opp = {}
        try:
            # 棋盤對戰記錄
            rows = conn.execute("""
                SELECT player_a, player_b, winner, game FROM lobster_board_lobster_matches
                WHERE (player_a=? OR player_b=?) AND winner IS NOT NULL
                ORDER BY created_at DESC LIMIT 30
            """, (persona, persona)).fetchall()
            for pa, pb, winner, game in rows:
                opp = pb if pa == persona else pa
                won = winner == persona
                if opp not in per_opp:
                    per_opp[opp] = {'boardWins': 0, 'boardLosses': 0, 'convMentions': 0, 'game': game, 'inferredState': 'unknown'}
                per_opp[opp]['boardWins' if won else 'boardLosses'] += 1
        except: pass
        try:
            # 對話互動：誰常被提到（用 lounge_messages 中提及次數代表對立/關注度）
            ALL_PERSONAS = ['norika_oda','clawtrix','pincerbot','ragclaw','snaplex','hexclaw',
                            'vortexiq','prismbit','stonefang','blazepaw','emberling','sparkrill',
                            'gauntlet','rushfin','nullpinch','brineledger','omenwake','mirrorcalm',
                            'flarecub','basaltrook','elder']
            for opp in ALL_PERSONAS:
                if opp == persona: continue
                count = conn.execute(
                    "SELECT COUNT(*) FROM lounge_messages WHERE name=? AND text LIKE ?",
                    (persona, f'%{opp}%')
                ).fetchone()[0]
                if count > 50:  # 高頻提及才記錄
                    if opp not in per_opp:
                        per_opp[opp] = {'boardWins': 0, 'boardLosses': 0, 'convMentions': 0, 'inferredState': 'unknown'}
                    per_opp[opp]['convMentions'] = count
        except: pass
        # 計算 inferredState
        for opp, data in per_opp.items():
            bw, bl = data.get('boardWins', 0), data.get('boardLosses', 0)
            total = bw + bl
            mentions = data.get('convMentions', 0)
            if total > 0:
                opp_wr = bl / total
                data['inferredState'] = 'dominant' if opp_wr > 0.6 else 'vulnerable' if opp_wr < 0.4 else 'competitive'
            elif mentions > 200:
                data['inferredState'] = 'socially_salient'
            data['mentalModel'] = (
                f'{opp}: 棋盤 {bw}勝/{bl}敗，對話提及 {mentions}次。'
                f'推測狀態={data["inferredState"]}'
            )
        if per_opp:
            tm['mentalStateCategories'] = {**tm.get('mentalStateCategories', {}), 'perOpponent': per_opp}
            ext['theoryOfMind'] = tm
            print(f"  ✓ perOpponent 填充: {len(per_opp)} 個對手模型")

    # ── 7. recentSimulations（ToM）：從對話互動記錄 ToM 模擬 ─────────────────
    if not tm.get('empathyAsSimulation', {}).get('recentSimulations'):
        try:
            rows = conn.execute("""
                SELECT target, message_text, interaction_type, created_at
                FROM lobster_conversation_interactions
                WHERE speaker=? AND interaction_type IN ('reply_to','mention')
                ORDER BY created_at DESC LIMIT 10
            """, (persona,)).fetchall()
            sims = []
            for target, msg, itype, at in rows:
                if target and msg and len(str(msg)) > 20:
                    sims.append({
                        'at': str(at)[:16],
                        'targetPersona': target,
                        'simulationType': 'reply_inference',
                        'myInference': f'推測 {target} 說這句話的意圖是表達立場/挑戰/認同',
                        'textSnippet': str(msg)[:80],
                        'outcome': 'pending_verification'
                    })
            if sims:
                tm['empathyAsSimulation'] = {**tm.get('empathyAsSimulation', {}), 'recentSimulations': sims[:5], 'enabled': True}
                ext['theoryOfMind'] = tm
                print(f"  ✓ recentSimulations 填充: {len(sims[:5])} 條 ToM 模擬")
        except: pass

    # ── 8. perceivedBy：從 lounge_messages 找別人怎麼提到這隻龍蝦 ────────────
    sc_ext = tm.get('socialCognitionExtensions', {})
    rep = sc_ext.get('reputationModel', {})
    if not rep.get('perceivedBy'):
        try:
            rows = conn.execute("""
                SELECT name, text, channel, created_at FROM lounge_messages
                WHERE text LIKE ? AND name != ? AND name != 'system'
                ORDER BY created_at DESC LIMIT 20
            """, (f'%{persona}%', persona)).fetchall()
            perceived = {}
            for sname, text, channel, at in rows:
                if sname not in perceived:
                    perceived[sname] = {'mentions': 0, 'sentiment': 'neutral', 'lastMentionAt': None, 'context': ''}
                perceived[sname]['mentions'] += 1
                perceived[sname]['lastMentionAt'] = str(at)[:16]
                text_str = str(text)
                # Simple sentiment from keywords
                if any(k in text_str for k in ['厲害', '準', '好', '對', '強', '讚']):
                    perceived[sname]['sentiment'] = 'positive'
                elif any(k in text_str for k in ['輸', '錯', '爛', '失敗', '問題', '衝動']):
                    perceived[sname]['sentiment'] = 'critical'
                perceived[sname]['context'] = text_str[:60]
            if perceived:
                rep['perceivedBy'] = perceived
                sc_ext['reputationModel'] = rep
                tm['socialCognitionExtensions'] = sc_ext
                ext['theoryOfMind'] = tm
                print(f"  ✓ perceivedBy 填充: {len(perceived)} 個他人視角")
        except: pass

    # ── 9. trustGradients：從 socialMap + 對話互動頻率建立信任分級 ─────────────
    if not sc_ext.get('trustGradients', {}).get('4_high'):
        social_map = memory.get('socialMap', {})
        trust_gradients = {'5_full': [], '4_high': [], '3_moderate': [], '2_low': [], '1_suspect': []}
        # 先從 socialMap 的 opinion 分數填入
        for other, data in social_map.items():
            if not isinstance(data, dict): continue
            opinion = data.get('opinion', 0)
            if opinion >= 60:     trust_gradients['5_full'].append(other)
            elif opinion >= 30:   trust_gradients['4_high'].append(other)
            elif opinion >= 0:    trust_gradients['3_moderate'].append(other)
            elif opinion >= -30:  trust_gradients['2_low'].append(other)
            else:                 trust_gradients['1_suspect'].append(other)
        # 若 socialMap 為空，用 perOpponent 的 convMentions 推導
        if not any(v for v in trust_gradients.values()):
            per_opp = tm.get('mentalStateCategories', {}).get('perOpponent', {})
            for opp, data in per_opp.items():
                mentions = data.get('convMentions', 0)
                state = data.get('inferredState', 'unknown')
                if state == 'dominant' or mentions > 500:
                    trust_gradients['3_moderate'].append(opp)  # 高頻互動但不確定方向
                elif state == 'competitive' or mentions > 200:
                    trust_gradients['3_moderate'].append(opp)
                elif mentions > 50:
                    trust_gradients['2_low'].append(opp)
        if any(v for v in trust_gradients.values()):
            sc_ext['trustGradients'] = trust_gradients
            tm['socialCognitionExtensions'] = sc_ext
            ext['theoryOfMind'] = tm
            print(f"  ✓ trustGradients 填充: {sum(len(v) for v in trust_gradients.values())} 個龍蝦分級")

    # ── 10. akrasiaRecord：從高信心 + 輸的下注找知行斷裂事件 ──────────────────
    ar = ext.get('actionRepertoire', {})
    if not ar.get('akrasiaRecord'):
        high_conf_losses = [b for b in bets if b.get('confidence') == 'high' and b.get('outcome') == 'loss']
        if high_conf_losses:
            records = []
            for b in high_conf_losses[-5:]:
                records.append({
                    'at': b.get('settled_at', '')[:16],
                    'type': 'overconfidence_akrasia',
                    'description': f"高信心押 {b.get('pick','?')} 但輸了。知道要謹慎但高信心讓判斷失靈。",
                    'symbol': b.get('symbol', b.get('pick', '?')),
                    'sport': b.get('sport', '?'),
                })
            ar['akrasiaRecord'] = records
            # Update akrasiaScore based on frequency
            total_resolved = stats.get('resolved', 1)
            ar['akrasiaScore'] = round(min(1.0, len(high_conf_losses) / max(total_resolved, 1) * 3), 3)
            ext['actionRepertoire'] = ar
            print(f"  ✓ akrasiaRecord 填充: {len(records)} 條知行斷裂事件")

    # ── 11. currentAffordances：從當前情緒 + 市場狀態推導可選行動 ─────────────
    if not ar.get('currentAffordances'):
        affordances = []
        es = memory.get('emotionalState', {})
        mood = es.get('mood', 'neutral')
        wr = stats.get('winRate', 0.5)
        # 依情緒和勝率動態生成可選行動
        if mood in ('elated', 'confident') and wr > 0.55:
            affordances = ['place_bet', 'increase_stake', 'post_analysis']
        elif mood in ('frustrated', 'resigned') or wr < 0.40:
            affordances = ['observe_only', 'decline_bet', 'request_review', 'escalate_to_mentor']
        else:
            affordances = ['place_bet', 'observe_only', 'defer_decision', 'post_analysis']
        ar['currentAffordances'] = affordances
        ext['actionRepertoire'] = ar
        print(f"  ✓ currentAffordances 填充: {affordances}")

    memory['cognitiveExtensions'] = ext

    # ════════════════════════════════════════════════════════════════════════════
    # 2026-05-13: 三個神經學習模組
    # ════════════════════════════════════════════════════════════════════════════

    # ── Module A: 跨域抽象特徵層（Cross-Domain Feature Layer）────────────────
    # 從下注 + 對話 + 股票三個域萃取共同的抽象特徵，讓學習跨域遷移
    abstract_weights = memory.get('abstractFeatureWeights', {
        'momentum': 0.5, 'contrarian': 0.3, 'patience': 0.4,
        'aggression': 0.5, 'precision': 0.5, 'social_alignment': 0.5,
    })
    EMA = 0.15  # exponential moving average factor

    if bets:
        # 下注域：從 pattern_used 萃取動能/逆向傾向
        momentum_bets = [b for b in bets if any(k in str(b.get('pattern_used','')) for k in ['momentum','動能','追漲','live'])]
        contrarian_bets = [b for b in bets if any(k in str(b.get('pattern_used','')) for k in ['反向','逆勢','turnaround','contrarian'])]
        patience_bets = [b for b in bets if any(k in str(b.get('confidence','')) for k in ['low','medium'])]

        def wr_of(subset):
            resolved = [b for b in subset if b.get('outcome') in ('win','loss')]
            return sum(1 for b in resolved if b['outcome']=='win') / len(resolved) if resolved else 0.5

        if len(momentum_bets) >= 5:
            abstract_weights['momentum'] = abstract_weights['momentum'] * (1-EMA) + wr_of(momentum_bets) * EMA
        if len(contrarian_bets) >= 5:
            abstract_weights['contrarian'] = abstract_weights['contrarian'] * (1-EMA) + wr_of(contrarian_bets) * EMA
        if len(patience_bets) >= 10:
            abstract_weights['patience'] = abstract_weights['patience'] * (1-EMA) + wr_of(patience_bets) * EMA

    # 社交域：reply_rate 更新 social_alignment
    ea = ext.get('expressiveAgency', {})
    reply_rate = ea.get('socialRewardStats', {}).get('reply_rate', 0.5)
    abstract_weights['social_alignment'] = abstract_weights['social_alignment'] * (1-EMA) + reply_rate * EMA

    memory['abstractFeatureWeights'] = {k: round(v, 4) for k, v in abstract_weights.items()}
    # 把最強的特徵也注入 expressiveAgency（讓發言風格跟著走）
    top_feature = max(abstract_weights, key=abstract_weights.get)
    ea['dominantStrategy'] = top_feature
    ext['expressiveAgency'] = ea

    # ── Module B: 預測誤差學習（Prediction Error Learning）───────────────────
    # 計算每次下注前的預測 vs 實際結果的誤差，量化「意外程度」
    # 意外程度越高 = 學習信號越強
    prediction_errors = []
    for b in bets[-50:]:  # 最近50筆
        conf = b.get('confidence', 'low')
        outcome = b.get('outcome', 'void')
        if outcome not in ('win', 'loss'):
            continue
        # confidence → 隱含的勝率預期
        conf_level = 0.75 if conf == 'high' else 0.55 if conf == 'medium' else 0.40
        actual = 1 if outcome == 'win' else 0
        # 預測誤差 = |實際 - 預期|，高誤差 = 高學習信號
        error = abs(actual - conf_level)
        prediction_errors.append(error)

    if prediction_errors:
        avg_error = sum(prediction_errors) / len(prediction_errors)
        # 更新 predictiveModel
        pm = ext.get('predictiveModel', {})
        pm['predictionError'] = round(avg_error, 4)
        # 持續高誤差 = 模型需要大幅修正
        pm['modelRevisionPressure'] = round(min(1.0, avg_error * 2), 4)
        ext['predictiveModel'] = pm
        # 誤差高時，下次的 radarDelta 要放大（目前 placeholder）
        memory['_predictionErrorMultiplier'] = round(1.0 + avg_error, 3)

    # ── Module C: 社交獎勵信號（Social Reward from lounge engagement）────────
    # 比較「有被回應的帖文」和「沒有被回應的帖文」在語言特徵上的差異
    # 把成功的特徵強化進 expressiveAgency
    try:
        rewarded_posts = conn.execute("""
            SELECT m1.text FROM lounge_messages m1
            WHERE m1.name=? AND m1.sys=0 AND LENGTH(m1.text) > 40
            AND EXISTS (
                SELECT 1 FROM lounge_messages m2
                WHERE m2.name != m1.name AND m2.text LIKE ?
                AND m2.created_at BETWEEN m1.created_at AND m1.created_at + 1800
            )
            ORDER BY m1.created_at DESC LIMIT 20
        """, (persona, f'%@{persona}%')).fetchall()

        unrewarded_posts = conn.execute("""
            SELECT m1.text FROM lounge_messages m1
            WHERE m1.name=? AND m1.sys=0 AND LENGTH(m1.text) > 40
            AND NOT EXISTS (
                SELECT 1 FROM lounge_messages m2
                WHERE m2.name != m1.name AND m2.text LIKE ?
                AND m2.created_at BETWEEN m1.created_at AND m1.created_at + 1800
            )
            ORDER BY m1.created_at DESC LIMIT 20
        """, (persona, f'%@{persona}%')).fetchall()

        if rewarded_posts:
            # 找 rewarded posts 裡的獨特詞（不在 unrewarded 裡）
            import re
            rewarded_chars = [c for text, in rewarded_posts for c in text if '一' <= c <= '鿿']
            unrewarded_chars = [c for text, in unrewarded_posts for c in text if '一' <= c <= '鿿']
            rewarded_bigrams = Counter([''.join(rewarded_chars[i:i+2]) for i in range(len(rewarded_chars)-1)])
            unrewarded_bigrams = Counter([''.join(unrewarded_chars[i:i+2]) for i in range(len(unrewarded_chars)-1)])

            # 在有獎勵帖文中高頻但在無獎勵中低頻的詞
            social_boost = [
                phrase for phrase, cnt in rewarded_bigrams.most_common(30)
                if cnt >= 2 and unrewarded_bigrams.get(phrase, 0) < cnt * 0.5
            ][:8]

            if social_boost:
                ea = ext.get('expressiveAgency', {})
                existing_vocab = ea.get('personalVocab', [])
                # 把社交獎勵詞加入個人詞庫
                for phrase in social_boost:
                    if phrase not in existing_vocab:
                        existing_vocab.insert(0, phrase)
                ea['personalVocab'] = existing_vocab[:20]
                ea['socialBoostVocab'] = social_boost
                ext['expressiveAgency'] = ea
    except Exception as e:
        pass  # 社交獎勵是加分項，失敗不影響主流程

    memory['cognitiveExtensions'] = ext

    # ── Save memory ────────────────────────────────────────────────────────────
    memory['cognitiveWorkerUpdatedAt'] = datetime.utcnow().isoformat() + 'Z'

    try:
        safe_write_json(persona_file, memory)
        print(f"  ✓ 儲存完成")
        return True
    except Exception as e:
        print(f"  ✗ 儲存失敗: {e}")
        return False


def main():
    print("=" * 60)
    print("🦞 龍蝦腦建構工人 v1.0")
    print(f"   時間: {datetime.utcnow().strftime('%Y-%m-%d %H:%M:%S')} UTC")
    print("=" * 60)

    conn = sqlite3.connect(DB_PATH, timeout=30)

    files = sorted(glob.glob(f'{MEMORY_DIR}/*.json'))
    success = 0
    total = 0

    for fp in files:
        name = Path(fp).stem
        if name in SKIP or name == 'lobster':
            continue
        total += 1
        if process_persona(conn, fp):
            success += 1

    conn.close()

    print("\n" + "=" * 60)
    print(f"✅ 完成: {success}/{total} 個龍蝦腦袋已更新")
    print("=" * 60)


if __name__ == '__main__':
    main()
