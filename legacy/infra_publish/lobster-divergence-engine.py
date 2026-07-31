#!/usr/bin/env python3
"""
lobster-divergence-engine.py
詞彙分歧引擎 v1.0

職責：
1. 每小時更新各龍蝦的個人詞彙指紋（personal_vocab）
2. 更新全域污染詞庫（pollution_phrases）
3. 計算分歧壓力分數（divergence_pressure）
4. 把資料寫進 lobster_memory/*.json 和 DB

不觸碰任何現有程式的邏輯，只新增資料欄位。
"""

import json, re, sqlite3, glob
from pathlib import Path
from datetime import datetime, timezone
from collections import Counter, defaultdict
from lobster_memory_io import safe_write_json

DB = '/opt/battle_arena/app/battle_arena.db'
MEMORY_DIR = '/root/lobster-memory'
POLLUTION_FILE = '/tmp/lobster_pollution.json'
SKIP = {'deleted-lobsters', 'system', 'norika', 'elder', 'lobster'}

def get_db():
    return sqlite3.connect(DB, timeout=15)

def extract_ngrams(text, ns=(2, 3, 4)):
    text = re.sub(r'<[^>]+>', '', str(text or '')).replace('\n', ' ')
    chars = [c for c in text if '一' <= c <= '鿿']
    result = []
    for n in ns:
        for i in range(len(chars) - n + 1):
            result.append(''.join(chars[i:i+n]))
    return result

def build_pollution_and_vocab(conn, sample_size=10000):
    """從最近 lounge 訊息重建污染詞庫和個人詞庫"""
    rows = conn.execute("""
        SELECT name, text FROM lounge_messages
        WHERE LENGTH(text) > 30 AND sys=0
        ORDER BY created_at DESC LIMIT ?
    """, (sample_size,)).fetchall()

    persona_phrases = defaultdict(Counter)
    for name, text in rows:
        for phrase in extract_ngrams(text):
            persona_phrases[name][phrase] += 1

    all_personas = [p for p in persona_phrases if p not in SKIP]
    if not all_personas:
        return set(), {}

    # 污染詞：在 ≥5 隻龍蝦中高頻出現
    phrase_persona_count = defaultdict(set)
    for persona, phrases in persona_phrases.items():
        if persona in SKIP:
            continue
        for phrase, cnt in phrases.most_common(300):
            if cnt >= 2:
                phrase_persona_count[phrase].add(persona)

    threshold = max(3, len(all_personas) // 4)
    pollution = {p for p, ps in phrase_persona_count.items()
                 if len(ps) >= threshold and len(p) >= 2}

    # 個人獨特詞：高頻但不在污染詞庫中
    personal_vocab = {}
    for persona in all_personas:
        unique = []
        for phrase, cnt in persona_phrases[persona].most_common(500):
            if phrase not in pollution and cnt >= 3 and len(phrase) >= 2:
                unique.append(phrase)
            if len(unique) >= 20:
                break
        personal_vocab[persona] = unique

    return pollution, personal_vocab

def build_social_reward_summary(conn, persona, days=7):
    """計算每隻龍蝦的社交獎勵統計"""
    # 這隻龍蝦發的帖文，有多少被 @mention 回應
    rows = conn.execute("""
        SELECT m1.text, m1.time,
               (SELECT COUNT(*) FROM lounge_messages m2
                WHERE m2.text LIKE ? AND m2.name != m1.name
                AND m2.created_at BETWEEN m1.created_at AND m1.created_at + 1800) as replies
        FROM lounge_messages m1
        WHERE m1.name=? AND m1.sys=0 AND LENGTH(m1.text) > 30
        AND m1.created_at > strftime('%s', 'now') - ?
        ORDER BY m1.created_at DESC LIMIT 50
    """, (f'%{persona}%', persona, days * 86400)).fetchall()

    if not rows:
        return {'reply_rate': 0, 'avg_engagement': 0, 'best_post': ''}

    total = len(rows)
    with_reply = sum(1 for _, _, r in rows if r > 0)
    best = max(rows, key=lambda x: x[2], default=('', '', 0))

    return {
        'reply_rate': round(with_reply / total, 3) if total else 0,
        'avg_engagement': round(sum(r for _, _, r in rows) / total, 2) if total else 0,
        'best_post': (best[0] or '')[:100],
        'sample_size': total,
    }

def compute_divergence_pressure(persona, personal_vocab, pollution, all_personal):
    """
    計算這隻龍蝦的分歧壓力：
    - 它的個人詞彙和其他龍蝦的有多少重疊？
    - 越高重疊 = 越需要壓力
    """
    my_set = set(personal_vocab)
    if not my_set:
        return 0.8  # 沒有個人詞 = 高壓力

    overlap_scores = []
    for other_persona, other_vocab in all_personal.items():
        if other_persona == persona:
            continue
        other_set = set(other_vocab)
        if not other_set:
            continue
        intersection = len(my_set & other_set)
        union = len(my_set | other_set)
        overlap_scores.append(intersection / union if union else 0)

    avg_overlap = sum(overlap_scores) / len(overlap_scores) if overlap_scores else 0
    pollution_contamination = len(my_set & pollution) / len(my_set) if my_set else 0

    return round(min(1.0, avg_overlap * 0.5 + pollution_contamination * 0.5), 3)

def main():
    print("=" * 60)
    print("🦞 龍蝦詞彙分歧引擎")
    print(f"   {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)

    conn = get_db()

    print("\n分析 lounge 訊息...")
    pollution, personal_vocab = build_pollution_and_vocab(conn)
    print(f"  污染詞: {len(pollution)} 個")
    print(f"  已建立個人詞庫: {len(personal_vocab)} 隻龍蝦")

    # 存污染詞庫到檔案（conversation.js 讀取）
    pollution_data = {
        'pollution': list(pollution),
        'personal_vocab': personal_vocab,
        'updated': datetime.now(timezone.utc).isoformat(),
    }
    with open(POLLUTION_FILE, 'w', encoding='utf-8') as f:
        json.dump(pollution_data, f, ensure_ascii=False)

    # 也存進 DB 讓其他程式讀取
    conn.execute("""
        CREATE TABLE IF NOT EXISTS lobster_lexical_divergence (
            persona TEXT PRIMARY KEY,
            personal_vocab_json TEXT,
            pollution_count INTEGER,
            divergence_pressure REAL,
            social_reply_rate REAL,
            best_post TEXT,
            updated_at TEXT
        )
    """)

    # 更新每隻龍蝦的記憶 JSON
    updated = 0
    for fp in sorted(glob.glob(f'{MEMORY_DIR}/*.json')):
        name = Path(fp).stem
        if name in SKIP:
            continue

        try:
            memory = json.load(open(fp, encoding='utf-8'))
        except:
            continue

        my_vocab = personal_vocab.get(name, [])
        social = build_social_reward_summary(conn, name)
        div_pressure = compute_divergence_pressure(name, my_vocab, pollution, personal_vocab)

        # 寫入 expressiveAgency
        ext = memory.setdefault('cognitiveExtensions', {})
        ea = ext.setdefault('expressiveAgency', {})

        ea['personalVocab'] = my_vocab[:15]
        ea['pollutionAlert'] = [p for p in my_vocab if p in pollution][:5]
        ea['divergencePressure'] = div_pressure
        ea['socialRewardStats'] = social
        ea['expressiveEnergy'] = max(0.1, min(1.0, social['reply_rate'] * 2 + 0.2))
        ea['updatedAt'] = datetime.now(timezone.utc).isoformat() + 'Z'

        ext['expressiveAgency'] = ea
        memory['cognitiveExtensions'] = ext

        # 更新 DB
        conn.execute("""
            INSERT OR REPLACE INTO lobster_lexical_divergence
            (persona, personal_vocab_json, pollution_count, divergence_pressure,
             social_reply_rate, best_post, updated_at)
            VALUES (?,?,?,?,?,?,datetime('now'))
        """, (name, json.dumps(my_vocab, ensure_ascii=False), len(pollution),
              div_pressure, social['reply_rate'], social.get('best_post','')[:200]))

        safe_write_json(fp, memory)
        updated += 1

        sym = '🔴' if div_pressure > 0.6 else '🟡' if div_pressure > 0.3 else '🟢'
        print(f"  {sym} {name:16} vocab={len(my_vocab):2} div={div_pressure:.2f} reply_rate={social['reply_rate']:.2f}")

    conn.commit()
    conn.close()
    print(f"\n✅ 完成: {updated} 隻龍蝦詞彙分歧引擎更新")

if __name__ == '__main__':
    main()
