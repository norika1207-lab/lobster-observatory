#!/usr/bin/env python3
"""每天跑語言獨立性 audit,更新 SOCIETY_BLACKLIST 候選"""
import sqlite3, re, time, json, os
from collections import Counter

TOWN = "/root/lobster-memory/_town"
OUT = f"{TOWN}/audits/language_{time.strftime('%Y%m%d')}.md"
DB = "/opt/battle_arena/app/battle_arena.db"

con = sqlite3.connect(DB, timeout=30)
con.execute("PRAGMA busy_timeout=30000")
con.row_factory = sqlite3.Row
rows = con.execute("""SELECT name, text FROM lounge_messages
                      WHERE created_at > strftime('%s','now','-1 days')
                      AND sys=0 AND length(text)>10""").fetchall()
by_name = {}
for r in rows: by_name.setdefault(r["name"], []).append(r["text"])

def char_ngrams(text, n=4):
    text = re.sub(r"\s+","", text)
    return set(text[i:i+n] for i in range(len(text)-n+1))

# 跨隻共用 4-gram (>= 80% 龍蝦)
all_grams = {}
for n, texts in by_name.items():
    g = set()
    for t in texts[:200]: g |= char_ngrams(t)
    all_grams[n] = g
gram_holders = Counter()
for g in all_grams.values():
    for x in g: gram_holders[x] += 1
threshold = max(len(all_grams)*0.7, 3)
shared = [(x,c) for x,c in gram_holders.most_common(200) if c >= threshold]

# 自我相似度
sims = {}
for n, texts in by_name.items():
    if len(texts) < 5: continue
    grams_list = [char_ngrams(t) for t in texts[:50]]
    s = []
    for i in range(min(20, len(grams_list))):
        for j in range(i+1, min(20, len(grams_list))):
            a, b = grams_list[i], grams_list[j]
            if not a or not b: continue
            s.append(len(a&b)/len(a|b))
    sims[n] = sum(s)/len(s) if s else 0

lines = [
    f"# Language Audit — {time.strftime('%Y-%m-%d %H:%M')}",
    f"過去 24h 鎮民發言: {len(rows)} 筆 / {len(by_name)} 隻有發言",
    "",
    f"## 跨鎮民共用 4-gram (>= {threshold:.0f} 隻共用,潛在 template 痕跡)",
]
for x, c in shared[:30]: lines.append(f"- `{x}`: {c} 隻在用")

lines += ["", "## 自我相似度 top 10 (高 = 自己重複)"]
for n, v in sorted(sims.items(), key=lambda kv:-kv[1])[:10]:
    lines.append(f"- `{n}`: {v:.3f} ({len(by_name[n])} 句)")

open(OUT,"w").write("\n".join(lines))
print(f"language audit: {OUT}")
con.close()
