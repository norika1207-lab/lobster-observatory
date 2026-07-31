#!/usr/bin/env python3
"""
Brain Semantic Memory Builder — Phase 1 Module #2
==================================================

Per-persona semantic concept network (Wernicke + IPL).

For each lobster L:
  - Collect concept nodes from:
      * cogProfile.knowledgeBase keys
      * settledBetting.sportStats domains
      * expressiveAgency.agendaTopics
      * High-freq 3-4-gram concept words from L's last 30d lounge_messages
  - Build edges via Pointwise Mutual Information (PMI) on co-occurrence in same message
  - Weight nodes by L's own usage frequency
  - Output: memory.cognitiveExtensions.semanticMemory

Spreading activation (run-time at STAGE 2 in speaking engine):
  - Input seed concept(s)
  - Spread depth=2 with decay=0.6
  - Output activated cluster

Runs daily.
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

MAX_NODES = 150
MIN_EDGE_COOC = 3
MAX_EDGES_PER_NODE = 12
DECAY_RATE = 0.15

def now_iso():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")

def normalize(text):
    text = re.sub(r"<[^>]+>", "", text or "")
    return re.sub(r"\s+", " ", text).strip()

def char_ngrams_set(text, ns=(3, 4)):
    """Return set of n-grams from text."""
    text = normalize(text)
    chars = [c for c in text if "一" <= c <= "鿿"]
    out = set()
    for n in ns:
        for i in range(len(chars) - n + 1):
            out.add("".join(chars[i:i+n]))
    return out

def list_personas():
    return sorted(
        p.stem for p in MEMORY_DIR.glob("*.json")
        if p.stem not in SKIP_META
    )

def load_memory(name):
    try:
        return json.loads((MEMORY_DIR / f"{name}.json").read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[semantic] load_memory({name}) failed: {e}", file=sys.stderr)
        return None

def save_memory(name, mem):
    path = MEMORY_DIR / f"{name}.json"
    safe_write_json(path, mem)

def load_own_messages(conn, persona, days=30):
    rows = conn.execute("""
        SELECT text FROM lounge_messages
        WHERE name=?
          AND created_at > strftime('%s', 'now', '-' || ? || ' days')
          AND sys IS NOT 1
          AND text IS NOT NULL
          AND LENGTH(text) > 20
    """, (persona, days)).fetchall()
    return [r[0] for r in rows]

def load_pollution():
    try:
        return set(json.loads(pathlib.Path("/tmp/lobster_pollution.json").read_text(encoding="utf-8")).get("pollution", []))
    except Exception:
        return set()

def build_semantic_memory(persona, mem, own_msgs, pollution):
    """Build full semanticMemory state for one persona."""
    # 1. Collect concept candidates
    kb = (mem.get("cogProfile") or {}).get("knowledgeBase") or {}
    if not isinstance(kb, dict):
        kb = {}
    sport_stats = (mem.get("settledBetting") or {}).get("sportStats") or {}
    ea = (mem.get("cognitiveExtensions") or {}).get("expressiveAgency") or {}
    agenda = [t.get("topic") for t in (ea.get("agendaTopics") or []) if t.get("topic")]

    kb_keys = list(kb.keys())
    sport_keys = list(sport_stats.keys())
    # Sport stats may have empty/None values; filter
    sport_keys = [s for s in sport_keys if s and len(s) >= 2]

    # High-freq concept ngrams from own messages
    word_freq = Counter()
    msg_word_sets = []  # for co-occurrence
    for text in own_msgs:
        ws = char_ngrams_set(text, ns=(3, 4))
        msg_word_sets.append(ws)
        for w in ws:
            word_freq[w] += 1

    # Top concepts, but EXCLUDE pollution
    high_freq_concepts = [
        w for w, c in word_freq.most_common(300)
        if c >= 3 and w not in pollution
    ][:80]

    # Merge all node sources, dedupe, prioritize knowledge/agenda/sport, then high_freq
    nodes_set = set()
    ordered_nodes = []
    for source_name, items in [("kb", kb_keys), ("agenda", agenda), ("sport", sport_keys), ("freq", high_freq_concepts)]:
        for item in items:
            if not item or len(item) < 2:
                continue
            if item not in nodes_set:
                nodes_set.add(item)
                ordered_nodes.append((item, source_name))
                if len(ordered_nodes) >= MAX_NODES:
                    break
        if len(ordered_nodes) >= MAX_NODES:
            break

    # 2. Compute node weights (own usage frequency, normalized)
    max_freq = max(word_freq.values()) if word_freq else 1
    nodes = {}
    for concept, source in ordered_nodes:
        freq = word_freq.get(concept, 0)
        weight = round(min(1.0, 0.3 + 0.7 * (freq / max_freq)), 3)
        # KB concepts boost
        if source == "kb":
            weight = min(1.0, weight + 0.2)
        if source == "agenda":
            weight = min(1.0, weight + 0.15)

        domains = []
        if source == "sport":
            domains.append("betting")
        if source == "kb":
            domains.append("knowledge")
        if source == "agenda":
            domains.append("agenda")

        kb_entry = kb.get(concept, {}) if source == "kb" else {}
        if isinstance(kb_entry, dict):
            definition = str(kb_entry.get("fact") or kb_entry.get("definition") or "")[:120]
        else:
            definition = str(kb_entry or "")[:120]

        nodes[concept] = {
            "weight": weight,
            "source": source,
            "freq": freq,
            "domains": domains,
            "definition": definition if source == "kb" else "",
            "activation": 0.0,
            "lastActivatedAt": None,
        }

    # 3. Build edges via co-occurrence + simple PMI
    # Count pairs in messages, restricted to our nodes
    concept_set = set(nodes.keys())
    pair_count = Counter()
    concept_msg_count = Counter()  # how many messages contain each concept
    total_msgs = len(msg_word_sets)
    for ws in msg_word_sets:
        relevant = ws & concept_set
        for c in relevant:
            concept_msg_count[c] += 1
        for a, b in combinations(sorted(relevant), 2):
            pair_count[(a, b)] += 1

    # PMI = log2( P(a,b) / (P(a) * P(b)) )
    # Edge weight scaled to 0..1
    edges_by_node = defaultdict(list)
    for (a, b), cnt in pair_count.items():
        if cnt < MIN_EDGE_COOC:
            continue
        pa = concept_msg_count[a] / total_msgs if total_msgs > 0 else 0
        pb = concept_msg_count[b] / total_msgs if total_msgs > 0 else 0
        pab = cnt / total_msgs if total_msgs > 0 else 0
        if pa <= 0 or pb <= 0 or pab <= 0:
            continue
        pmi = math.log2(pab / (pa * pb))
        # Normalize: PMI typically in [-3, +5] for sparse co-occurrence
        edge_weight = max(0.0, min(1.0, (pmi + 1) / 6))
        if edge_weight > 0.1:
            edges_by_node[a].append((b, edge_weight, cnt))
            edges_by_node[b].append((a, edge_weight, cnt))

    # Trim edges per node + write to node.connections
    for a, edges in edges_by_node.items():
        edges.sort(key=lambda x: -x[1])
        if a in nodes:
            nodes[a]["connections"] = {
                b: round(w, 3)
                for b, w, _cnt in edges[:MAX_EDGES_PER_NODE]
            }

    # Default empty connections for nodes without
    for c in nodes:
        nodes[c].setdefault("connections", {})

    return {
        "version": 1,
        "updatedAt": now_iso(),
        "source": "semantic-memory-builder-v1",
        "nodeCount": len(nodes),
        "edgeCount": sum(len(n["connections"]) for n in nodes.values()) // 2,
        "concepts": nodes,
        "decayRate": DECAY_RATE,
        "spreadingActivation": {
            "default_depth": 2,
            "default_decay_per_hop": 0.6,
            "instructions": "At runtime: seed concept(s), spread to connections with weight*decay, output ranked activated set"
        }
    }

def main():
    started = datetime.now(timezone.utc)
    conn = sqlite3.connect(DB, timeout=30)
    pollution = load_pollution()
    personas = list_personas()
    print(f"Building semanticMemory for {len(personas)} personas (pollution dict={len(pollution)})", file=sys.stderr)

    summary = []
    for persona in personas:
        mem = load_memory(persona)
        if not mem:
            continue
        own_msgs = load_own_messages(conn, persona, days=30)
        sm = build_semantic_memory(persona, mem, own_msgs, pollution)
        mem.setdefault("cognitiveExtensions", {})
        mem["cognitiveExtensions"]["semanticMemory"] = sm
        mem["updatedAt"] = now_iso()
        save_memory(persona, mem)

        if not sm["concepts"]:
            summary.append({
                "persona": persona,
                "nodes": 0,
                "edges": 0,
                "msgs_used": len(own_msgs),
                "top_concept": "",
                "top_concept_weight": 0,
                "top_connections": [],
            })
            continue

        # sample top connections for a key node (for verification)
        sample_node = max(sm["concepts"].items(), key=lambda kv: kv[1]["weight"])
        sample_name, sample_data = sample_node
        top_conn = sorted(sample_data["connections"].items(), key=lambda x: -x[1])[:3]

        summary.append({
            "persona": persona,
            "nodes": sm["nodeCount"],
            "edges": sm["edgeCount"],
            "msgs_used": len(own_msgs),
            "top_concept": sample_name,
            "top_concept_weight": sample_data["weight"],
            "top_connections": top_conn,
        })

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    print(json.dumps({
        "engine": "lobster-brain-semantic-memory-builder",
        "version": "1.0",
        "started_at": started.isoformat().replace("+00:00", "Z"),
        "elapsed_sec": round(elapsed, 2),
        "personas_built": len(summary),
        "summary": summary,
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
