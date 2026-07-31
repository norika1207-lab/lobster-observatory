#!/usr/bin/env python3
"""
Brain Mirror Monitor — Phase 1 Module #1
=========================================

Runs every 5 minutes. Builds cognitiveExtensions.mirrorNeuronInhibition for each persona.

For each lobster L:
  - Collect L's recent skeletons (last 5 own messages, 30/60 min)
  - Collect ALL OTHER lobsters' skeletons in last 30 min
  - Compute:
      heardWords_30min   = union of n-grams in others' recent messages
      heardSkeletons     = list of recent skeletons from others
      ownRecentSkeletons = list of L's own skeletons
      inhibitionStrength = f(pollution overlap, recent mirror events)
      consciousMirrorBan = words L should not use because heard from others

Writes back to /root/lobster-memory/<persona>.json:
  memory.cognitiveExtensions.mirrorNeuronInhibition = {...}

Also writes to DB table for time series:
  lobster_brain_mirror_events (persona, peer, skeleton_match_jaccard, word_match_jaccard, at)
"""
import json
import pathlib
import re
import sqlite3
import sys
import time
from collections import defaultdict, Counter
from datetime import datetime, timezone
from lobster_memory_io import safe_write_json

DB = "/opt/battle_arena/app/battle_arena.db"
MEMORY_DIR = pathlib.Path("/root/lobster-memory")
SKIP_META = {"norika", "system", "deleted-lobsters", "lobster"}

WINDOW_30_MIN = 30 * 60
WINDOW_60_MIN = 60 * 60
OWN_RECENT_SKELETONS_LIMIT = 5
HEARD_SKELETONS_LIMIT = 20

# ---------------------------------------------------------------------------

COPY_TEMPLATE_LEAK_RE = re.compile(
    r"本輪題目|改口條件|Ten-minute|今日外部資料話題|挑選標題|請開始引入|"
    r"我讀到的題目|題目核心|題目朗誦|答案草稿|我朗誦自己的答案|"
    r"你說的是正確的|你说的是正确的|角度看这个问题|嫉妒可能是因为|"
    r"我最近在思考和探索|遇到了很多困難和挑戰|遇到了很多困难和挑战|"
    r"波賽頓盾|波赛顿盾|波賽頓的盾|波赛顿的盾|今天要乾淨|今天要干净|距離\s*波賽頓|距离\s*波赛顿|"
    r"神力商店剩什麼好物|神力商店剩什么好物|我這隻聞起來什麼神器最對味|我这只闻起来什么神器最对味|上次有人被宙斯選中|上次有人被宙斯选中|"
    r"錯誤率\s*p_i|错误率\s*p_i|錯誤相關係數|错误相关系数|群體投票錯誤風險|群体投票错误风险|"
    r"若\s*\{?\d*\}?\s*隻龍蝦各自錯誤率|Find all positive integers|Can a group be more rational|Give a model, a failure case",
    re.I,
)

SAFE_SKELETON_MAX = 120

def is_template_leak(text):
    return bool(COPY_TEMPLATE_LEAK_RE.search(normalize(text)))

def now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def normalize(text):
    text = re.sub(r"<[^>]+>", "", text or "")
    return re.sub(r"\s+", " ", text).strip()

def skeletonize(text):
    text = normalize(text)
    if not text or is_template_leak(text):
        return ""
    text = re.sub(r"@[A-Za-z0-9_]+", "{target}", text)
    text = re.sub(r"[A-Za-z_][A-Za-z0-9_]{2,}", "{term}", text)
    text = re.sub(r"\d+(?:\.\d+)?%?", "{num}", text)
    text = re.sub(r"「[^」]{1,60}」", "「{case}」", text)
    if is_template_leak(text):
        return ""
    return text[:SAFE_SKELETON_MAX]

def char_ngrams(text, ns=(3, 4)):
    text = normalize(text)
    chars = [c for c in text if "一" <= c <= "鿿"]
    out = []
    for n in ns:
        for i in range(len(chars) - n + 1):
            out.append("".join(chars[i:i+n]))
    return out

def jaccard(a, b):
    if not a and not b:
        return 0.0
    return len(a & b) / max(1, len(a | b))

def list_personas():
    return sorted(
        p.stem for p in MEMORY_DIR.glob("*.json")
        if p.stem not in SKIP_META
    )

def load_memory(name):
    try:
        return json.loads((MEMORY_DIR / f"{name}.json").read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[mirror-monitor] load_memory({name}) failed: {e}", file=sys.stderr)
        return None

def save_memory(name, mem):
    path = MEMORY_DIR / f"{name}.json"
    safe_write_json(path, mem)

def ensure_mirror_table(conn):
    conn.execute("""
        CREATE TABLE IF NOT EXISTS lobster_brain_mirror_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            persona TEXT NOT NULL,
            peer TEXT NOT NULL,
            skeleton_jaccard REAL,
            word_jaccard REAL,
            heard_word_count INTEGER,
            recorded_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_mirror_events_persona
        ON lobster_brain_mirror_events(persona, recorded_at)
    """)
    conn.commit()

# ---------------------------------------------------------------------------

def load_recent_messages(conn, since_unix):
    rows = conn.execute("""
        SELECT name, text, created_at FROM lounge_messages
        WHERE created_at >= ?
          AND sys IS NOT 1
          AND text IS NOT NULL
          AND LENGTH(text) > 15
        ORDER BY id DESC
    """, (since_unix,)).fetchall()

    by_persona = defaultdict(list)
    for name, text, ts in rows:
        if name and not is_template_leak(text):
            by_persona[name].append({"text": text, "at": ts})
    return by_persona

def compute_persona_mirror_state(persona, my_msgs, others_msgs_30min):
    """For one persona, compute the full mirrorNeuronInhibition state."""
    # 1. Own recent skeletons (last N + last 30min, deduped)
    own_skeletons = []
    own_word_set = set()
    for m in my_msgs[:OWN_RECENT_SKELETONS_LIMIT * 2]:
        sk = skeletonize(m["text"])
        if sk and sk not in [x["skeleton"] for x in own_skeletons]:
            own_skeletons.append({
                "skeleton": sk,
                "at": datetime.fromtimestamp(m["at"], tz=timezone.utc).isoformat().replace("+00:00", "Z")
            })
        own_word_set.update(char_ngrams(m["text"]))
        if len(own_skeletons) >= OWN_RECENT_SKELETONS_LIMIT:
            break

    # 2. Heard skeletons from all peers
    heard_skeletons = []
    heard_word_counter = Counter()
    peer_jaccards = []
    for peer_name, peer_msgs in others_msgs_30min.items():
        if peer_name == persona:
            continue
        peer_skel_set = set()
        peer_word_set = set()
        for m in peer_msgs:
            sk = skeletonize(m["text"])
            if sk:
                peer_skel_set.add(sk)
                if len(heard_skeletons) < HEARD_SKELETONS_LIMIT:
                    heard_skeletons.append({
                        "skeleton": sk,
                        "from": peer_name,
                        "at": datetime.fromtimestamp(m["at"], tz=timezone.utc).isoformat().replace("+00:00", "Z")
                    })
            for w in char_ngrams(m["text"]):
                heard_word_counter[w] += 1
                peer_word_set.add(w)
        # Compute pairwise jaccards
        if peer_skel_set:
            my_skel_set = {s["skeleton"] for s in own_skeletons}
            sk_jac = jaccard(my_skel_set, peer_skel_set)
            wd_jac = jaccard(own_word_set, peer_word_set)
            peer_jaccards.append({
                "peer": peer_name,
                "skeleton_jaccard": round(sk_jac, 3),
                "word_jaccard": round(wd_jac, 3),
            })

    peer_jaccards.sort(key=lambda r: -r["skeleton_jaccard"])

    # 3. consciousMirrorBan: words I should NOT use
    # criterion: heard from others >=3 times in last 30 min, AND not in my recent vocabulary
    # (if I already use it as personal vocab, it's mine — don't ban)
    heard_high_freq = {w for w, c in heard_word_counter.items() if c >= 3 and len(w) >= 2}
    conscious_ban = sorted(heard_high_freq - own_word_set)[:50]

    # 4. inhibitionStrength: 0..1
    # Higher when: many high overlap peers, heard high-freq words abundant
    overlap_count = sum(1 for p in peer_jaccards if p["skeleton_jaccard"] > 0.15 or p["word_jaccard"] > 0.40)
    inhibition_strength = min(1.0, 0.3 + overlap_count * 0.1 + min(0.4, len(conscious_ban) / 100))

    state = {
        "version": 1,
        "updatedAt": now_iso(),
        "ownRecentSkeletons": own_skeletons,
        "ownWordSetSize_30min": len(own_word_set),
        "recentlyHeardSkeletons": heard_skeletons,
        "heardWords_30min_count": len(heard_word_counter),
        "consciousMirrorBan": conscious_ban,
        "peerJaccards": peer_jaccards[:10],
        "maxSkeletonOverlap": peer_jaccards[0] if peer_jaccards else None,
        "inhibitionStrength": round(inhibition_strength, 3),
        "windowMinutes": 30,
    }
    return state, peer_jaccards

# ---------------------------------------------------------------------------

def main():
    started = datetime.now(timezone.utc)
    conn = sqlite3.connect(DB, timeout=90)
    conn.execute("PRAGMA busy_timeout=90000")
    ensure_mirror_table(conn)

    # Last 60 min so we can compute own 30min AND look back further for "own recent 5"
    rows_60min = load_recent_messages(conn, int(started.timestamp()) - WINDOW_60_MIN)
    # Filter to 30 min for "others"
    cutoff_30 = int(started.timestamp()) - WINDOW_30_MIN
    rows_30min_others = defaultdict(list)
    for name, msgs in rows_60min.items():
        for m in msgs:
            if m["at"] >= cutoff_30:
                rows_30min_others[name].append(m)

    personas = list_personas()
    written = 0
    db_inserts = 0
    summary = []

    for persona in personas:
        mem = load_memory(persona)
        if not mem:
            continue
        my_msgs = rows_60min.get(persona, [])
        # Compute mirror state
        state, peer_jaccards = compute_persona_mirror_state(persona, my_msgs, rows_30min_others)

        mem.setdefault("cognitiveExtensions", {})
        mem["cognitiveExtensions"]["mirrorNeuronInhibition"] = state
        mem["updatedAt"] = now_iso()
        save_memory(persona, mem)
        written += 1

        # DB time-series
        for pj in peer_jaccards:
            if pj["skeleton_jaccard"] > 0.05 or pj["word_jaccard"] > 0.15:
                conn.execute("""
                    INSERT INTO lobster_brain_mirror_events
                      (persona, peer, skeleton_jaccard, word_jaccard, heard_word_count)
                    VALUES (?, ?, ?, ?, ?)
                """, (persona, pj["peer"], pj["skeleton_jaccard"], pj["word_jaccard"],
                      state["heardWords_30min_count"]))
                db_inserts += 1
        conn.commit()

        top = state["maxSkeletonOverlap"]
        summary.append({
            "persona": persona,
            "own_skeletons": len(state["ownRecentSkeletons"]),
            "heard_count": len(state["recentlyHeardSkeletons"]),
            "ban_size": len(state["consciousMirrorBan"]),
            "inhibition": state["inhibitionStrength"],
            "top_peer": top["peer"] if top else None,
            "top_sk_jac": top["skeleton_jaccard"] if top else 0,
        })

    conn.commit()
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()

    result = {
        "engine": "lobster-brain-mirror-monitor",
        "version": "1.0",
        "started_at": started.isoformat().replace("+00:00", "Z"),
        "elapsed_sec": round(elapsed, 2),
        "personas_processed": written,
        "db_inserts": db_inserts,
        "summary": sorted(summary, key=lambda r: -r["inhibition"]),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    for attempt in range(1, 4):
        try:
            main()
            break
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() and "busy" not in str(exc).lower():
                raise
            if attempt == 3:
                print(json.dumps({
                    "engine": "lobster-brain-mirror-monitor",
                    "ok": False,
                    "deferred": True,
                    "reason": "battle_arena.db busy; skipped this cycle instead of failing systemd",
                    "error": str(exc),
                    "checkedAt": now_iso(),
                }, ensure_ascii=False))
                break
            time.sleep(8 * attempt)
