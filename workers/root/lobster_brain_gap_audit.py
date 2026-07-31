#!/usr/bin/env python3
"""
Brain-Modeled Speech Engine — Phase 0 Audit
============================================

Per-persona gap audit. Read-only. Outputs markdown summary + JSON.

Measures, per lobster:
  A. Semantic memory bootstrap potential
     - knowledgeBase keys
     - own high-freq concept words
     - sportStats domains
     - co-occurrence edges available

  B. Mental lexicon graph bootstrap
     - personalVocab size
     - own message word freq Zipf distribution
     - words shared with others (= pollution candidates)

  C. Mirror inhibition diagnostics (THIS IS THE KEY)
     - Last 30 min: my skeletons vs OTHER lobsters' skeletons
     - Jaccard distance per pair
     - How many of my own words appeared in others' recent messages

  D. Affective salience baseline
     - current emotionalArchitecture vector
     - existentialThreat pressure

  E. RAG corpus size (per-persona index potential)
     - own_lounge_messages count
     - own_reflections count
     - own_knowledgeBase count
     - own_bet_research count
     - own_reading_comprehension count

  F. Current cognitiveExtensions module audit
     - which fields are missing/null/stub

  G. Pollution exposure
     - % of my recent words that are in globalPollution
     - which polluted words I personally over-use

Output:
  - /tmp/lobster_brain_audit.json  (machine-readable)
  - /tmp/lobster_brain_audit.md    (human-readable summary)
"""
import json
import pathlib
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from itertools import combinations

DB = "/opt/battle_arena/app/battle_arena.db"
MEMORY_DIR = pathlib.Path("/root/lobster-memory")
POLLUTION_FILE = pathlib.Path("/tmp/lobster_pollution.json")
SKIP_META = {"norika", "system", "deleted-lobsters", "lobster"}

# Skeletonize same way as living-template-worker
def normalize(text):
    text = re.sub(r"<[^>]+>", "", text or "")
    return re.sub(r"\s+", " ", text).strip()

def skeletonize(text):
    text = normalize(text)
    text = re.sub(r"@[A-Za-z0-9_]+", "{target}", text)
    text = re.sub(r"[A-Za-z_][A-Za-z0-9_]{2,}", "{term}", text)
    text = re.sub(r"\d+(?:\.\d+)?%?", "{num}", text)
    text = re.sub(r"「[^」]{1,60}」", "「{case}」", text)
    return text

def char_ngrams(text, ns=(2, 3, 4)):
    text = normalize(text)
    chars = [c for c in text if "一" <= c <= "鿿"]
    out = []
    for n in ns:
        for i in range(len(chars) - n + 1):
            out.append("".join(chars[i:i+n]))
    return out

def jaccard(a_set, b_set):
    if not a_set and not b_set:
        return 0.0
    return len(a_set & b_set) / max(1, len(a_set | b_set))

def list_personas():
    return sorted(p.stem for p in MEMORY_DIR.glob("*.json") if p.stem not in SKIP_META)

def load_memory(name):
    try:
        return json.loads((MEMORY_DIR / f"{name}.json").read_text(encoding="utf-8"))
    except Exception:
        return None

def load_pollution():
    try:
        return json.loads(POLLUTION_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"pollution": [], "personal_vocab": {}}

# ---------------------------------------------------------------------------
# Per-persona metrics
# ---------------------------------------------------------------------------

def metric_semantic_bootstrap(mem, own_msgs):
    """A. How many semantic concept nodes can we get?"""
    kb_keys = list((mem.get("cogProfile") or {}).get("knowledgeBase", {}).keys())
    sport_keys = list((mem.get("settledBetting") or {}).get("sportStats", {}).keys())
    agenda_topics = [
        t.get("topic") for t in
        ((mem.get("cognitiveExtensions") or {}).get("expressiveAgency") or {}).get("agendaTopics", [])
        if t.get("topic")
    ]
    # High-freq 4-gram concept candidates from own messages
    freq = Counter()
    for text in own_msgs:
        freq.update(g for g in char_ngrams(text, ns=(3, 4)) if 3 <= len(g) <= 4)
    top_concepts = [w for w, c in freq.most_common(150) if c >= 3]

    # Co-occurrence edge count: pairs that appear together in >=3 messages
    cooc = Counter()
    for text in own_msgs:
        grams = set(g for g in char_ngrams(text, ns=(3, 4)) if 3 <= len(g) <= 4)
        grams = [g for g in grams if g in top_concepts[:60]]
        for a, b in combinations(sorted(grams), 2):
            cooc[(a, b)] += 1
    edges = sum(1 for v in cooc.values() if v >= 3)

    return {
        "knowledgeBase_keys": len(kb_keys),
        "sport_domains": len(sport_keys),
        "agenda_topics": len(agenda_topics),
        "high_freq_concepts": len(top_concepts),
        "cooccurrence_edges_min3": edges,
        "estimated_nodes": len(set(kb_keys + sport_keys + agenda_topics + top_concepts[:80])),
    }

def metric_lexicon(mem, own_msgs, pollution):
    """B. Lexicon graph bootstrap"""
    personal_vocab = ((mem.get("cognitiveExtensions") or {}).get("expressiveAgency") or {}).get("personalVocab", [])
    own_words = Counter()
    for text in own_msgs:
        own_words.update(char_ngrams(text, ns=(2, 3, 4)))
    own_high_freq = [w for w, c in own_words.most_common(200) if c >= 3 and len(w) >= 2]

    pollution_set = set(pollution.get("pollution", []))
    polluted_in_my_high_freq = [w for w in own_high_freq if w in pollution_set]

    return {
        "personalVocab_size": len(personal_vocab),
        "own_high_freq_words": len(own_high_freq),
        "polluted_in_my_freq": len(polluted_in_my_high_freq),
        "pollution_overlap_pct": round(100 * len(polluted_in_my_high_freq) / max(1, len(own_high_freq)), 1),
        "sample_polluted_words": polluted_in_my_high_freq[:10],
    }

def metric_mirror_inhibition(my_msgs_30min, others_msgs_30min):
    """C. Mirror inhibition — THE CORE DIAGNOSTIC"""
    my_skeletons = set()
    my_word_set = set()
    for text in my_msgs_30min:
        my_skeletons.add(skeletonize(text)[:120])
        my_word_set.update(char_ngrams(text, ns=(3, 4)))

    # Skeleton overlap with each other lobster
    skeleton_pairs = []
    word_pairs = []
    for other_name, other_msgs in others_msgs_30min.items():
        their_skeletons = set()
        their_words = set()
        for text in other_msgs:
            their_skeletons.add(skeletonize(text)[:120])
            their_words.update(char_ngrams(text, ns=(3, 4)))
        if their_skeletons:
            sk_jac = jaccard(my_skeletons, their_skeletons)
            wd_jac = jaccard(my_word_set, their_words)
            if sk_jac > 0 or wd_jac > 0:
                skeleton_pairs.append((other_name, round(sk_jac, 3)))
                word_pairs.append((other_name, round(wd_jac, 3)))

    skeleton_pairs.sort(key=lambda x: -x[1])
    word_pairs.sort(key=lambda x: -x[1])

    return {
        "my_skeleton_count_30min": len(my_skeletons),
        "my_word_set_size_30min": len(my_word_set),
        "skeleton_jaccard_top5_peers": skeleton_pairs[:5],
        "word_jaccard_top5_peers": word_pairs[:5],
        "max_skeleton_overlap": skeleton_pairs[0] if skeleton_pairs else None,
        "max_word_overlap": word_pairs[0] if word_pairs else None,
    }

def metric_affective_salience(mem):
    """D. Affective salience baseline"""
    ea = (mem.get("cognitiveExtensions") or {}).get("emotionalArchitecture") or {}
    se = ea.get("socialEmotions", {})
    be = ea.get("basicEmotions", {})
    et = (mem.get("cognitiveExtensions") or {}).get("existentialThreat") or {}
    return {
        "basicEmotions": {k: round(v, 2) for k, v in be.items() if isinstance(v, (int, float))},
        "socialEmotions_envy": round(se.get("envyIndex") or 0, 2),
        "socialEmotions_vanityDesire": round(se.get("vanityDesire") or 0, 2),
        "socialEmotions_antiHierarchy": round(se.get("antiHierarchyIndex") or 0, 2),
        "socialEmotions_annoyance": round(se.get("annoyanceIndex") or 0, 2),
        "mortalitySalience": round(et.get("mortalitySalience") or 0, 2),
        "tiamatPressure": round(((et.get("tiamatExtinctionRule") or {}).get("extinctionPressure") or 0), 2),
    }

def metric_rag_corpus(conn, persona, mem):
    """E. RAG corpus size potential"""
    own_msgs = conn.execute(
        "SELECT COUNT(*) FROM lounge_messages WHERE name=? AND created_at > strftime('%s','now','-30 days')",
        (persona,)
    ).fetchone()[0]
    own_reflections = len((mem.get("cogProfile") or {}).get("reflections", []))
    own_kb = len((mem.get("cogProfile") or {}).get("knowledgeBase", {}))
    own_growth = len((mem.get("cogProfile") or {}).get("growthLog", []))
    own_chat_log = conn.execute(
        "SELECT COUNT(*) FROM lobster_chat_log WHERE name=?",
        (persona,)
    ).fetchone()[0]
    own_prebet = 0
    try:
        own_prebet = conn.execute(
            "SELECT COUNT(*) FROM lobster_pre_bet_research WHERE persona=?",
            (persona,)
        ).fetchone()[0]
    except Exception:
        pass
    return {
        "own_lounge_messages_30d": own_msgs,
        "own_reflections": own_reflections,
        "own_knowledgeBase_entries": own_kb,
        "own_growthLog": own_growth,
        "own_chat_log": own_chat_log,
        "own_pre_bet_research": own_prebet,
        "total_rag_documents": own_msgs + own_reflections + own_kb + own_growth + own_chat_log + own_prebet,
    }

def metric_module_audit(mem):
    """F. Current 27-module field-level audit"""
    ce = mem.get("cognitiveExtensions") or {}
    expected = [
        "linguistics", "politics", "philosophy", "decisionScience", "gameTheory",
        "learningModel", "culturalAnthropology", "moralPsychology", "workingMemory",
        "predictiveModel", "metacognition", "interoception", "memoryConsolidation",
        "causalModel", "selfContinuity", "proceduralSkill", "existentialThreat",
        "dialogueAgency", "receptiveListening", "threatResponse", "mindRefuge",
        "expressiveAgency", "commitmentDevice", "temporalCognition",
        "emotionalArchitecture", "theoryOfMind", "actionRepertoire",
    ]
    new_required = [
        "semanticMemory", "mentalLexiconGraph", "narrativeIntent",
        "mirrorNeuronInhibition", "inhibitionGating", "affectiveSalience",
        "prosody", "workingMemoryBuffer",
    ]
    present = [m for m in expected if isinstance(ce.get(m), dict) and ce.get(m)]
    missing = [m for m in expected if m not in present]
    new_present = [m for m in new_required if isinstance(ce.get(m), dict) and ce.get(m)]
    return {
        "existing_modules_present": len(present),
        "existing_modules_missing": missing,
        "new_modules_needed": [m for m in new_required if m not in new_present],
        "new_modules_already_present": new_present,
    }

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    if not POLLUTION_FILE.exists():
        print("WARN: /tmp/lobster_pollution.json missing")
    pollution = load_pollution()
    personas = list_personas()
    print(f"Auditing {len(personas)} personas...", file=sys.stderr)

    conn = sqlite3.connect(DB, timeout=30)

    # Pre-load 30-min messages for all personas
    rows_30min = conn.execute("""
        SELECT name, text FROM lounge_messages
        WHERE created_at > strftime('%s','now','-30 minutes')
          AND sys IS NOT 1
          AND text IS NOT NULL
        ORDER BY id DESC
    """).fetchall()
    msgs_by_persona_30min = defaultdict(list)
    for name, text in rows_30min:
        if name and text:
            msgs_by_persona_30min[name].append(text)

    # Pre-load 7-day messages per persona for semantic/lexicon bootstrap
    rows_7d = conn.execute("""
        SELECT name, text FROM lounge_messages
        WHERE created_at > strftime('%s','now','-7 days')
          AND sys IS NOT 1
          AND text IS NOT NULL
          AND LENGTH(text) > 20
    """).fetchall()
    msgs_by_persona_7d = defaultdict(list)
    for name, text in rows_7d:
        if name and text:
            msgs_by_persona_7d[name].append(text)

    results = {}
    for persona in personas:
        mem = load_memory(persona)
        if not mem:
            continue
        own_msgs_7d = msgs_by_persona_7d.get(persona, [])
        own_msgs_30min = msgs_by_persona_30min.get(persona, [])
        others_msgs_30min = {n: msgs_by_persona_30min[n] for n in msgs_by_persona_30min if n != persona and n in personas}

        results[persona] = {
            "A_semantic_bootstrap": metric_semantic_bootstrap(mem, own_msgs_7d),
            "B_lexicon": metric_lexicon(mem, own_msgs_7d, pollution),
            "C_mirror_inhibition_30min": metric_mirror_inhibition(own_msgs_30min, others_msgs_30min),
            "D_affective_salience": metric_affective_salience(mem),
            "E_rag_corpus": metric_rag_corpus(conn, persona, mem),
            "F_module_audit": metric_module_audit(mem),
        }

    # Global summary
    global_summary = {
        "pollution_dict_size": len(pollution.get("pollution", [])),
        "personas_audited": len(results),
        "avg_pollution_overlap_pct": round(
            sum(r["B_lexicon"]["pollution_overlap_pct"] for r in results.values()) / max(1, len(results)), 1
        ),
        "max_mirror_skeleton_pair": None,
        "worst_polluted_persona": None,
        "lobsters_needing_all_new_modules": [
            p for p, r in results.items()
            if len(r["F_module_audit"]["new_modules_needed"]) == 8
        ],
    }
    # find max mirror pair
    max_pair = (0, None, None)
    for p, r in results.items():
        top = r["C_mirror_inhibition_30min"].get("max_skeleton_overlap")
        if top and top[1] > max_pair[0]:
            max_pair = (top[1], p, top[0])
    if max_pair[1]:
        global_summary["max_mirror_skeleton_pair"] = {
            "between": [max_pair[1], max_pair[2]], "jaccard": max_pair[0]
        }
    # worst polluted
    worst = max(results.items(), key=lambda kv: kv[1]["B_lexicon"]["pollution_overlap_pct"], default=(None, None))
    if worst[0]:
        global_summary["worst_polluted_persona"] = {
            "name": worst[0], "pollution_overlap_pct": worst[1]["B_lexicon"]["pollution_overlap_pct"]
        }

    output = {
        "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "global_summary": global_summary,
        "per_persona": results,
    }

    json_path = pathlib.Path("/tmp/lobster_brain_audit.json")
    json_path.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")

    # Markdown summary
    md = []
    md.append("# Lobster Brain Gap Audit\n")
    md.append(f"Generated: {output['generated_at']}\n")
    md.append("\n## Global Summary\n")
    md.append(f"- Personas audited: **{global_summary['personas_audited']}**")
    md.append(f"- Pollution dict size: {global_summary['pollution_dict_size']}")
    md.append(f"- Avg pollution overlap %: **{global_summary['avg_pollution_overlap_pct']}%**")
    if global_summary["max_mirror_skeleton_pair"]:
        mp = global_summary["max_mirror_skeleton_pair"]
        md.append(f"- 🔴 Max mirror skeleton pair: **{mp['between'][0]} ↔ {mp['between'][1]}** (jaccard {mp['jaccard']})")
    if global_summary["worst_polluted_persona"]:
        wp = global_summary["worst_polluted_persona"]
        md.append(f"- Worst polluted persona: **{wp['name']}** ({wp['pollution_overlap_pct']}% of high-freq vocab is polluted)")
    md.append(f"- Lobsters needing all 8 new modules: {len(global_summary['lobsters_needing_all_new_modules'])}/{global_summary['personas_audited']}")

    md.append("\n## Per-Persona Snapshot\n")
    md.append("| Persona | KB keys | SemNodes | Lex size | Pollution% | Mirror peer | Sk-jac | RAG docs | New mods needed |")
    md.append("|---|---:|---:|---:|---:|---|---:|---:|---:|")
    for p in sorted(results.keys()):
        r = results[p]
        sem = r["A_semantic_bootstrap"]
        lex = r["B_lexicon"]
        mir = r["C_mirror_inhibition_30min"]
        rag = r["E_rag_corpus"]
        new_mods = len(r["F_module_audit"]["new_modules_needed"])
        peer = mir["max_skeleton_overlap"]
        peer_str = f"{peer[0]}({peer[1]})" if peer else "—"
        md.append(
            f"| {p} | {sem['knowledgeBase_keys']} | {sem['estimated_nodes']} | "
            f"{lex['personalVocab_size']}+{lex['own_high_freq_words']} | "
            f"{lex['pollution_overlap_pct']}% | {peer_str} | "
            f"{mir['skeleton_jaccard_top5_peers'][0][1] if mir['skeleton_jaccard_top5_peers'] else '—'} | "
            f"{rag['total_rag_documents']} | {new_mods}/8 |"
        )

    md.append("\n## Top 5 Mirror Pairs (cross-lobster skeleton overlap last 30min)\n")
    all_pairs = []
    for p, r in results.items():
        for peer, jac in r["C_mirror_inhibition_30min"]["skeleton_jaccard_top5_peers"][:3]:
            if jac > 0.05:
                all_pairs.append((p, peer, jac))
    all_pairs.sort(key=lambda x: -x[2])
    seen = set()
    md.append("| A | B | Jaccard |")
    md.append("|---|---|---:|")
    for a, b, jac in all_pairs:
        key = tuple(sorted([a, b]))
        if key in seen:
            continue
        seen.add(key)
        md.append(f"| {a} | {b} | {jac} |")
        if len(seen) >= 10:
            break

    md.append("\n## Top 5 Polluted Personas (high-freq vocab pollution overlap)\n")
    sorted_pol = sorted(results.items(), key=lambda kv: -kv[1]["B_lexicon"]["pollution_overlap_pct"])[:5]
    md.append("| Persona | Pollution % | Sample polluted words I use |")
    md.append("|---|---:|---|")
    for p, r in sorted_pol:
        sample = "、".join(r["B_lexicon"]["sample_polluted_words"][:5])
        md.append(f"| {p} | {r['B_lexicon']['pollution_overlap_pct']}% | {sample} |")

    md.append("\n## RAG Corpus Distribution\n")
    md.append("| Persona | Lounge 30d | Reflections | KB | GrowthLog | ChatLog | PreBet | TOTAL |")
    md.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for p in sorted(results.keys()):
        rag = results[p]["E_rag_corpus"]
        md.append(
            f"| {p} | {rag['own_lounge_messages_30d']} | {rag['own_reflections']} | "
            f"{rag['own_knowledgeBase_entries']} | {rag['own_growthLog']} | "
            f"{rag['own_chat_log']} | {rag['own_pre_bet_research']} | "
            f"**{rag['total_rag_documents']}** |"
        )

    md_text = "\n".join(md) + "\n"
    pathlib.Path("/tmp/lobster_brain_audit.md").write_text(md_text, encoding="utf-8")

    print(md_text)
    print(f"\nJSON: /tmp/lobster_brain_audit.json", file=sys.stderr)
    print(f"MD:   /tmp/lobster_brain_audit.md", file=sys.stderr)


if __name__ == "__main__":
    main()
