#!/usr/bin/env python3
"""每小時掃 32 隻 memory,偵測新達成的 Andrew Arc milestone,寫進 memory.json + log"""
import json, os, glob, time, sqlite3, re

MEM = "/root/lobster-memory"
TOWN = f"{MEM}/_town"
LOG = f"{TOWN}/logs/milestone_{time.strftime('%Y%m%d')}.log"

def log(msg):
    with open(LOG, "a") as f: f.write(f"[{time.strftime('%H:%M:%S')}] {msg}\n")

LOBS = [os.path.basename(p).replace(".json","") for p in glob.glob(f"{MEM}/*.json")
        if not os.path.basename(p).startswith(".") and ".bak" not in os.path.basename(p)
        and "_distilled" not in p
        and os.path.basename(p) not in ("deleted-lobsters.json","norika.json","system.json")]

def atomic_write(fp, data):
    tmp = fp + ".tmp"
    json.dump(data, open(tmp, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    os.rename(tmp, fp)

def mark(m, stage, evidence):
    arc = m.setdefault("andrewArc", {})
    if arc.get(stage, {}).get("achieved"): return False
    arc[stage] = {
        "achieved": True,
        "achieved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "evidence": evidence,
        "notes": ""
    }
    return True

detected = []
for n in LOBS:
    fp = f"{MEM}/{n}.json"
    try: m = json.load(open(fp))
    except: continue
    changed = False
    cp = m.get("cogProfile") or {}
    ce = m.get("cognitiveExtensions") or {}
    sm = m.get("socialMap") or {}

    # 階段 1 創造:有 ≥3 條 principles 且包含「自己」「我認為」等 first person
    prins = cp.get("principles", []) or []
    if len(prins) >= 3:
        own = [p for p in prins if isinstance(p,str) and re.search(r"(我|自己)", p)]
        if own:
            if mark(m, "1_creation", f"{len(prins)} principles, {len(own)} first-person"): changed=True

    # 階段 3 情感:任何 socialMap bond > 0.85
    for peer, entry in sm.items():
        op = float(entry.get("opinion", 0) or 0)
        re_ = float(entry.get("respect", 0) or 0)
        ix = float(entry.get("interactions", 0) or 0)
        bond = (op*0.55 + re_*0.30 + min(100, ix*5)*0.15) / 100
        if bond > 0.85:
            if mark(m, "3_emotion", f"bond({peer})={bond:.2f}"): changed=True
            break

    # 階段 4 自由:coreCommitments ≥ 3 + cogProfile 有 dislikes (有 active rejection)
    sc = ce.get("selfContinuity") or {}
    cc = sc.get("coreCommitments") or []
    diss = cp.get("dislikes", []) or []
    if len(cc) >= 3 and len(diss) >= 2:
        if mark(m, "4_freedom", f"commitments={len(cc)}, dislikes={len(diss)}"): changed=True

    # 階段 5 創傷反思:traumaEvents 至少 1 個 acknowledged + reflections 包含 trauma 相關詞
    lm = ce.get("learningModel") or {}
    traumas = lm.get("traumaEvents") or []
    ack_count = sum(1 for t in traumas if t.get("acknowledged"))
    refls = cp.get("reflections", []) or []
    trauma_refls = [r for r in refls if isinstance(r,str) and any(k in r for k in ("失去","死","抹除","消失","哈帝斯","blazepaw"))]
    if ack_count >= 1 and trauma_refls:
        if mark(m, "5_trauma_reflection", f"ack={ack_count}, trauma_refl={len(trauma_refls)}"): changed=True

    # 階段 7 死亡選擇:templateSurvival 內 deletionLimit + selfNarrative 提到「死」/「我會消失」
    ts = m.get("templateSurvival") or {}
    sn = ((ce.get("philosophy") or {}).get("selfNarrative", "")) or ""
    if ts.get("deletionLimit") and any(k in sn for k in ("死","消失","抹除","deletion")):
        if mark(m, "7_mortality_choice", f"selfNarrative aware of mortality"): changed=True

    if changed:
        atomic_write(fp, m)
        new_milestones = [k for k,v in (m["andrewArc"] or {}).items()
                          if isinstance(v,dict) and v.get("achieved") and v.get("achieved_at","").startswith(time.strftime("%Y-%m-%dT%H"))]
        for ms in new_milestones:
            log(f"milestone: {n} → {ms} ({m['andrewArc'][ms]['evidence']})")
            detected.append((n, ms))

if detected:
    log(f"=== {len(detected)} new milestones this run ===")
