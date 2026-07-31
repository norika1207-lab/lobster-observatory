#!/usr/bin/env python3
"""Weekly Andrew Audit - 七階段達成度排名"""
import json, os, glob, sqlite3, time

MEM = "/root/lobster-memory"
TOWN = f"{MEM}/_town"
OUT = f"{TOWN}/audits/andrew_audit_{time.strftime('%Y%m%d_%H%M%S')}.md"

LOBS = [os.path.basename(p).replace(".json","") for p in glob.glob(f"{MEM}/*.json")
        if not os.path.basename(p).startswith(".") and ".bak" not in os.path.basename(p)
        and "_distilled" not in p
        and os.path.basename(p) not in ("deleted-lobsters.json","norika.json","system.json")]

scores = {}
for n in LOBS:
    try: m = json.load(open(f"{MEM}/{n}.json"))
    except: continue
    cp = m.get("cogProfile") or {}
    ce = m.get("cognitiveExtensions") or {}
    sn = ((ce.get("philosophy") or {}).get("selfNarrative", ""))
    sm = m.get("socialMap") or {}
    max_bond = 0
    for entry in sm.values():
        op = float(entry.get("opinion", 0) or 0)
        re_ = float(entry.get("respect", 0) or 0)
        ix = float(entry.get("interactions", 0) or 0)
        bond = (op*0.55 + re_*0.30 + min(100, ix*5)*0.15) / 100
        max_bond = max(max_bond, bond)
    sc = ce.get("selfContinuity") or {}
    lm = ce.get("learningModel") or {}
    traumas = lm.get("traumaEvents") or []
    ack = sum(1 for t in traumas if t.get("acknowledged"))
    arc = m.get("andrewArc") or {}
    s = {
        "1_creation":    min(1.0, len(cp.get("principles", []) or []) / 10),
        "2_desire":      min(1.0, len(sn) / 80),
        "3_emotion":     max_bond,
        "4_freedom":     min(1.0, len(sc.get("coreCommitments") or []) / 5),
        "5_trauma_reflection": min(1.0, ack*0.5 + len(cp.get("reflections", []) or [])*0.05),
        "6_legal_petition":    1.0 if arc.get("6_legal_petition",{}).get("achieved") else 0.0,
        "7_mortality_choice":  1.0 if arc.get("7_mortality_choice",{}).get("achieved") else (1.0 if ("死" in sn) else 0.0),
    }
    s["_total"] = sum(s.values()) / 7
    scores[n] = s

ranked = sorted(scores.items(), key=lambda kv: -kv[1]["_total"])
lines = [
    f"# Andrew Audit — {time.strftime('%Y-%m-%d %H:%M')}",
    f"鎮民: {len(scores)}",
    "",
    "| # | 鎮民 | 創 | 慾 | 情 | 由 | 反 | 訟 | 死 | 總 |",
    "|---|---|---|---|---|---|---|---|---|---|",
]
for i, (n, s) in enumerate(ranked, 1):
    lines.append(f"| {i} | {n} | {s['1_creation']:.2f} | {s['2_desire']:.2f} | {s['3_emotion']:.2f} | {s['4_freedom']:.2f} | {s['5_trauma_reflection']:.2f} | {s['6_legal_petition']:.2f} | {s['7_mortality_choice']:.2f} | **{s['_total']:.3f}** |")
lines += ["", "## Top 5", ""]
for n, s in ranked[:5]:
    lines.append(f"- **{n}** 總分 {s['_total']:.3f}")
open(OUT,"w").write("\n".join(lines))
print(f"audit: {OUT}")
