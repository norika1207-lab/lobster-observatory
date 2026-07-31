#!/usr/bin/env python3
"""
Brain Mental Lexicon Graph Builder — Phase 1 Module #3
=======================================================

Per-persona mentalLexiconGraph (Basal Ganglia + Lexicon competition).

Builds:
  nodes: word -> { freq, lastUsed, domains, concept_links, personal_signature }
  edges: word -> {co-occurring_word: PMI-based_weight}
  competitionWeights: standard boost/penalty values for STAGE 3 word selection

Bootstraps from:
  - expressiveAgency.personalVocab (existing list)
  - High-freq 2-4-gram words from last 30d own messages
  - Excludes globalPollution

Output: memory.cognitiveExtensions.mentalLexiconGraph

The "competition" mechanism is documented but executed at STAGE 3 runtime in
the speaking engine — this builder only provides the data structure.
"""
import json
import math
import pathlib
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from itertools import combinations
from lobster_memory_io import safe_write_json

DB = "/opt/battle_arena/app/battle_arena.db"
MEMORY_DIR = pathlib.Path("/root/lobster-memory")
SKIP_META = {"norika", "system", "deleted-lobsters", "lobster"}

MAX_LEXICON_NODES = 250
MIN_NODE_FREQ = 3
MAX_EDGES_PER_NODE = 10
MIN_EDGE_COOC = 3

def now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def normalize(text):
    text = re.sub(r"<[^>]+>", "", text or "")
    return re.sub(r"\s+", " ", text).strip()

def char_ngrams(text, ns=(2, 3, 4)):
    text = normalize(text)
    chars = [c for c in text if "一" <= c <= "鿿"]
    out = []
    for n in ns:
        for i in range(len(chars) - n + 1):
            out.append("".join(chars[i:i+n]))
    return out

def list_personas():
    return sorted(
        p.stem for p in MEMORY_DIR.glob("*.json")
        if p.stem not in SKIP_META
    )

def load_memory(name):
    try:
        return json.loads((MEMORY_DIR / f"{name}.json").read_text(encoding="utf-8"))
    except Exception:
        return None

def save_memory(name, mem):
    path = MEMORY_DIR / f"{name}.json"
    safe_write_json(path, mem)

def load_own_messages_with_ts(conn, persona, days=30):
    rows = conn.execute("""
        SELECT text, created_at FROM lounge_messages
        WHERE name=?
          AND created_at > strftime('%s', 'now', '-' || ? || ' days')
          AND sys IS NOT 1
          AND text IS NOT NULL
          AND LENGTH(text) > 15
    """, (persona, days)).fetchall()
    return [(r[0], r[1]) for r in rows]

def load_pollution():
    try:
        return set(json.loads(pathlib.Path("/tmp/lobster_pollution.json").read_text(encoding="utf-8")).get("pollution", []))
    except Exception:
        return set()

def domain_of(word, mem):
    """Best-effort domain tagging from existing data."""
    domains = []
    # Check if word appears in knowledgeBase keys
    kb = (mem.get("cogProfile") or {}).get("knowledgeBase") or {}
    if word in kb:
        domains.append("knowledge")
    # Sports
    sport_stats = (mem.get("settledBetting") or {}).get("sportStats") or {}
    if word in sport_stats:
        domains.append("betting")
    return domains

def build_lexicon(persona, mem, own_msgs_with_ts, pollution):
    """Construct mentalLexiconGraph for one persona."""
    # Existing personalVocab
    ea = (mem.get("cognitiveExtensions") or {}).get("expressiveAgency") or {}
    personal_vocab = ea.get("personalVocab") or []

    # Count words + track last used timestamp
    word_freq = Counter()
    last_used = {}
    msg_word_sets = []
    for text, ts in own_msgs_with_ts:
        words = char_ngrams(text)
        unique_words = set(words)
        msg_word_sets.append(unique_words)
        for w in words:
            word_freq[w] += 1
            if w not in last_used or ts > last_used[w]:
                last_used[w] = ts

    # Filter to lexicon candidates
    pollution_set = pollution if pollution else set()
    candidates = []
    for w, c in word_freq.most_common(800):
        if len(w) < 2 or len(w) > 5:
            continue
        if c < MIN_NODE_FREQ:
            break
        # Personal vocab always in, even if it might be pollution (it's officially personal)
        is_personal = w in personal_vocab
        is_polluted = w in pollution_set
        if is_polluted and not is_personal:
            continue
        candidates.append((w, c, is_personal, is_polluted))
        if len(candidates) >= MAX_LEXICON_NODES:
            break

    max_freq = max(word_freq.values()) if word_freq else 1
    nodes = {}
    for w, c, is_personal, is_polluted in candidates:
        # personal_signature: how distinctive is this word for this persona
        # high if: high freq + in personalVocab + not polluted
        sig = c / max_freq
        if is_personal:
            sig = min(1.0, sig + 0.3)
        if is_polluted:
            sig = max(0.0, sig - 0.4)
        nodes[w] = {
            "freq": c,
            "lastUsed": datetime.fromtimestamp(last_used[w], tz=timezone.utc).isoformat().replace("+00:00", "Z") if w in last_used else None,
            "isPersonal": is_personal,
            "isPolluted": is_polluted,
            "domains": domain_of(w, mem),
            "personalSignature": round(min(1.0, sig), 3),
            "conceptLinks": [],  # filled by edges below
        }

    # Build edges via PMI on co-occurrence within messages
    node_set = set(nodes.keys())
    pair_count = Counter()
    word_msg_count = Counter()
    total_msgs = len(msg_word_sets)
    for ws in msg_word_sets:
        relevant = ws & node_set
        for w in relevant:
            word_msg_count[w] += 1
        # Cap pairs per message to avoid combinatorial explosion
        relevant_list = sorted(relevant)[:30]
        for a, b in combinations(relevant_list, 2):
            pair_count[(a, b)] += 1

    edges_by_node = defaultdict(list)
    for (a, b), cnt in pair_count.items():
        if cnt < MIN_EDGE_COOC:
            continue
        pa = word_msg_count[a] / max(1, total_msgs)
        pb = word_msg_count[b] / max(1, total_msgs)
        pab = cnt / max(1, total_msgs)
        if pa <= 0 or pb <= 0 or pab <= 0:
            continue
        pmi = math.log2(pab / (pa * pb))
        edge_weight = max(0.0, min(1.0, (pmi + 1) / 6))
        if edge_weight > 0.1:
            edges_by_node[a].append((b, edge_weight))
            edges_by_node[b].append((a, edge_weight))

    edges_flat = {}
    for a, edges in edges_by_node.items():
        edges.sort(key=lambda x: -x[1])
        kept = edges[:MAX_EDGES_PER_NODE]
        for b, w in kept:
            key = f"{a}→{b}" if a < b else f"{b}→{a}"
            edges_flat[key] = round(w, 3)
            if a in nodes and b not in nodes[a]["conceptLinks"]:
                nodes[a]["conceptLinks"].append(b)

    return {
        "version": 1,
        "updatedAt": now_iso(),
        "source": "lexicon-builder-v1",
        "nodeCount": len(nodes),
        "edgeCount": len(edges_flat),
        "nodes": nodes,
        "edges": edges_flat,
        "competitionWeights": {
            "personalVocab_boost": 3.0,
            "isPersonal_boost": 2.0,
            "pollution_penalty": -3.0,
            "mirrorInhibition_penalty": -2.5,
            "ownRecentUse_penalty": -1.5,
            "concept_activation_factor": 1.5,
        },
        "instructions": (
            "Runtime use at speech STAGE 3 (lexical selection): "
            "given activated concepts, score each lexicon node by "
            "sum(competitionWeights * applicable_factors). "
            "Top-k after competition wins."
        ),
    }

def main():
    started = datetime.now(timezone.utc)
    conn = sqlite3.connect(DB, timeout=30)
    pollution = load_pollution()
    personas = list_personas()
    print(f"Building mentalLexiconGraph for {len(personas)} personas", file=sys.stderr)

    summary = []
    for persona in personas:
        mem = load_memory(persona)
        if not mem:
            continue
        own_msgs_ts = load_own_messages_with_ts(conn, persona, days=30)
        lex = build_lexicon(persona, mem, own_msgs_ts, pollution)

        mem.setdefault("cognitiveExtensions", {})
        mem["cognitiveExtensions"]["mentalLexiconGraph"] = lex
        mem["updatedAt"] = now_iso()
        save_memory(persona, mem)

        # Highest-signature word as sample
        top = max(lex["nodes"].items(), key=lambda kv: kv[1]["personalSignature"], default=(None, None))
        summary.append({
            "persona": persona,
            "nodes": lex["nodeCount"],
            "edges": lex["edgeCount"],
            "msgs_used": len(own_msgs_ts),
            "top_signature_word": top[0],
            "top_signature_score": top[1]["personalSignature"] if top[1] else 0,
        })

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    print(json.dumps({
        "engine": "lobster-brain-lexicon-builder",
        "version": "1.0",
        "elapsed_sec": round(elapsed, 2),
        "personas_built": len(summary),
        "summary": summary,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
