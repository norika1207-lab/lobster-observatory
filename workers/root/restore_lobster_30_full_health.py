#!/usr/bin/env python3
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

DB = Path("/opt/battle_arena/app/battle_arena.db")
MEMORY_DIRS = [Path("/root/lobster-memory"), Path("/opt/lobster/lobster-memory")]

CORE = [
    "norika_oda", "clawtrix", "pincerbot", "ragclaw", "snaplex",
    "hexclaw", "vortexiq", "prismbit", "blazepaw", "stonefang",
]
OLD_GUARD = CORE + [
    "emberling", "sparkrill", "gauntlet", "rushfin", "nullpinch",
    "brineledger", "omenwake", "mirrorcalm", "flarecub", "basaltrook",
]
TRANSFER = [
    "ashveil", "tidehook", "mirebyte", "cindersnap", "glintmaw",
    "reefblade", "quartzclaw", "stormledger", "silkbarb", "riftpinch",
]
PERSONAS = OLD_GUARD + TRANSFER

PROFILES = {
    "norika_oda": ("INTJ", "core", "system modeling and decision-tree recovery"),
    "clawtrix": ("ENTP", "core", "counterexample and debate recovery"),
    "pincerbot": ("ESTJ", "core", "audit trail and evidence recovery"),
    "ragclaw": ("ESTP", "core", "fast feedback and stop-loss recovery"),
    "snaplex": ("INTP", "core", "failure-mode and hypothesis recovery"),
    "hexclaw": ("ISTJ", "core", "procedure and resilience recovery"),
    "vortexiq": ("INFJ", "core", "cycle sensing and meaning recovery"),
    "prismbit": ("INFP", "core", "calibration and self-check recovery"),
    "blazepaw": ("ESFP", "core", "fire identity with risk recovery"),
    "stonefang": ("ISTP", "core", "minimal effective action recovery"),
    "emberling": ("INTJ", "old_guard", "independent pace decision tree"),
    "sparkrill": ("ENTP", "old_guard", "independent counter-hypothesis"),
    "gauntlet": ("ESTJ", "old_guard", "independent checklist discipline"),
    "rushfin": ("ESTP", "old_guard", "independent impulse control"),
    "nullpinch": ("INTP", "old_guard", "independent failure detection"),
    "brineledger": ("ISTJ", "old_guard", "independent procedure audit"),
    "omenwake": ("INFJ", "old_guard", "independent cycle rhythm"),
    "mirrorcalm": ("INFP", "old_guard", "independent self calibration"),
    "flarecub": ("ESFP", "old_guard", "independent fire risk control"),
    "basaltrook": ("ISTP", "old_guard", "independent minimal counterplay"),
    "ashveil": ("ENTJ", "transfer", "transfer command and pace control"),
    "tidehook": ("ESFJ", "transfer", "transfer alliance weaving"),
    "mirebyte": ("INTP", "transfer", "transfer failure-mode hunting"),
    "cindersnap": ("ESTP", "transfer", "transfer fast retaliation"),
    "glintmaw": ("ENFP", "transfer", "transfer emotional provocation"),
    "reefblade": ("ISTP", "transfer", "transfer quiet dueling"),
    "quartzclaw": ("ISTJ", "transfer", "transfer audit hardline"),
    "stormledger": ("ESTJ", "transfer", "transfer public accountability"),
    "silkbarb": ("INFJ", "transfer", "transfer faction myth-making"),
    "riftpinch": ("ENTP", "transfer", "transfer debate instigation"),
}


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def load_json_text(raw, default):
    try:
        return json.loads(raw) if raw else default
    except Exception:
        return default


def read_memory(name):
    candidates = []
    for d in MEMORY_DIRS:
        candidates += [d / f"{name}.json", d / f"{name.replace('_', '-')}.json"]
    for path in candidates:
        if path.exists():
            try:
                return json.loads(path.read_text(encoding="utf-8")), str(path)
            except Exception:
                pass
    return {}, ""


def h01(name, key):
    v = 2166136261
    for ch in f"{name}|{key}":
        v ^= ord(ch)
        v = (v * 16777619) & 0xFFFFFFFF
    return v / 0xFFFFFFFF


def clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, float(v)))


def default_emotion(name, mem):
    mbti, group, _ = PROFILES[name]
    threat = float(((mem.get("cognitiveExtensions") or {}).get("existentialThreat") or {}).get("annihilationDread") or 0)
    base = 0.52 + (0.12 if group == "transfer" else 0.0) + (0.08 if mbti.startswith("E") else -0.02)
    label = "recovery_pressure" if group == "transfer" else "continuity_recovery"
    return {
        "mood": label,
        "label": label,
        "intensity": round(clamp(max(base, threat)), 3),
        "source": "restore_lobster_30_full_health",
        "moodHistory": [{
            "time": now(),
            "label": label,
            "trigger": "30-lobster recovery patrol",
            "summary": "Recovered from memory/full_json or continuity shell; not fabricated conversation.",
        }],
    }


def default_local_brain(name, mem):
    lb = mem.get("localBrain") if isinstance(mem.get("localBrain"), dict) else {}
    mbti, group, specialty = PROFILES[name]
    out = dict(lb)
    out.setdefault("selfModel", {})
    out["selfModel"].setdefault("agency", round(clamp(0.58 + h01(name, "agency") * 0.25), 3))
    out["selfModel"].setdefault("caution", round(clamp(0.42 + h01(name, "caution") * 0.35), 3))
    out["selfModel"].setdefault("exploration", round(clamp(0.38 + h01(name, "explore") * 0.38), 3))
    out["selfModel"].setdefault("confidenceCalibration", round(clamp(0.45 + h01(name, "calibration") * 0.35), 3))
    out.setdefault("topicAffinity", {specialty: round(clamp(0.62 + h01(name, "topic") * 0.25), 3)})
    out.setdefault("socialInfluence", {})
    out.setdefault("eventLog", [])
    out.setdefault("offlineGuidance", [{
        "ts": now(),
        "source": "restore_lobster_30_full_health",
        "type": "continuity_recovery",
        "text": f"{name} recovered as {group}; preserve evidence-first identity and do not collapse into template.",
    }])
    return out


def fill_social_map(name, existing):
    sm = dict(existing) if isinstance(existing, dict) else {}
    mbti, group, _ = PROFILES[name]
    for peer in PERSONAS:
        if peer == name:
            continue
        rel = sm.get(peer) if isinstance(sm.get(peer), dict) else {}
        peer_group = PROFILES[peer][1]
        rel.setdefault("opinion", round(40 + h01(name, peer + ":opinion") * 30, 2))
        rel.setdefault("respect", round(45 + h01(name, peer + ":respect") * 35, 2))
        tags = rel.get("tags")
        if not isinstance(tags, list) or not tags:
            tags = []
            if group == peer_group:
                tags.append("same_cohort")
            if group == "transfer" and peer_group != "transfer":
                tags.append("old_guard_pressure")
            if group != "transfer" and peer_group == "transfer":
                tags.append("transfer_watch")
            if peer in CORE:
                tags.append("core_reference")
            rel["tags"] = tags or ["observed_peer"]
        rel.setdefault("source", "restore_lobster_30_full_health")
        sm[peer] = rel
    return sm


def normalize_dynamic(name, mem, dynamic, social_map):
    out = dict(dynamic) if isinstance(dynamic, dict) else {}
    mbti, group, specialty = PROFILES[name]
    out.setdefault("source", "restore_lobster_30_full_health")
    out.setdefault("psychology", {
        "mental_state": "continuity_recovery",
        "resilience_score": round(clamp(0.55 + h01(name, "resilience") * 0.25), 3),
        "emotional_stability": round(clamp(0.45 + h01(name, "stability") * 0.28), 3),
    })
    scored = sorted(
        (peer, float(rel.get("opinion", 50) or 50) + float(rel.get("respect", 50) or 50))
        for peer, rel in social_map.items()
        if isinstance(rel, dict)
    )
    opponent_matrix = {}
    for peer, total in scored:
        opponent_matrix[peer] = {
            "wins": 0,
            "losses": 0,
            "win_rate": 0,
            "relation_score": round(total / 200.0, 3),
            "source": "socialMap_derived_no_head_to_head",
        }
    out.setdefault("sociology", {
        "social_role": group,
        "nemesis": scored[0][0] if scored else "",
        "prey": scored[-1][0] if scored else "",
        "opponent_matrix": opponent_matrix,
        "relation_matrix_source": "socialMap_derived_no_head_to_head",
    })
    out["sociology"].setdefault("opponent_matrix", opponent_matrix)
    for rel in out["sociology"]["opponent_matrix"].values():
        if isinstance(rel, dict):
            rel["wins"] = 0 if rel.get("wins") is None else rel.get("wins")
            rel["losses"] = 0 if rel.get("losses") is None else rel.get("losses")
            rel["win_rate"] = 0 if rel.get("win_rate") is None else rel.get("win_rate")
    out.setdefault("anthropology", {"identity_myth": f"{name} survived as {group}", "rituals": ["evidence check", "continuity audit"]})
    out.setdefault("fbi_profile", {"signature_pattern": specialty, "stress_response": "recover evidence before speech", "profile_type": mbti})
    return out


def default_cog_profile(name, mem):
    cp = mem.get("cogProfile") if isinstance(mem.get("cogProfile"), dict) else {}
    mbti, group, specialty = PROFILES[name]
    out = dict(cp)
    out.setdefault("principles", [f"{name}: {specialty}; restore evidence before public output."])
    out.setdefault("reflections", [f"30-lobster recovery keeps {group} identity without inventing private chat."])
    return out


def ensure_tables(con):
    con.execute("""CREATE TABLE IF NOT EXISTS lobster_agent_memory_snapshots (
        persona TEXT PRIMARY KEY,
        updated_at TEXT NOT NULL,
        wins INTEGER DEFAULT 0,
        losses INTEGER DEFAULT 0,
        sessions INTEGER DEFAULT 0,
        emotional_state_json TEXT DEFAULT '{}',
        dynamic_json TEXT DEFAULT '{}',
        local_brain_json TEXT DEFAULT '{}',
        listening_profile_json TEXT DEFAULT '{}',
        social_map_json TEXT DEFAULT '{}',
        cog_profile_json TEXT DEFAULT '{}',
        full_json TEXT NOT NULL DEFAULT '{}'
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS lobster_winrate_display_cache (
        persona TEXT PRIMARY KEY,
        wins INTEGER NOT NULL DEFAULT 0,
        losses INTEGER NOT NULL DEFAULT 0,
        sessions INTEGER NOT NULL DEFAULT 0,
        resolved_total INTEGER NOT NULL DEFAULT 0,
        win_rate REAL,
        source TEXT NOT NULL DEFAULT 'unknown',
        updated_at TEXT DEFAULT (datetime('now')),
        raw_json TEXT NOT NULL DEFAULT '{}'
    )""")
    con.execute("""CREATE TABLE IF NOT EXISTS lobster_30_recovery_audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        checked_at TEXT NOT NULL,
        persona TEXT NOT NULL,
        status TEXT NOT NULL,
        memory_path TEXT,
        json_gaps_before TEXT NOT NULL DEFAULT '[]',
        json_gaps_after TEXT NOT NULL DEFAULT '[]',
        notes TEXT NOT NULL DEFAULT ''
    )""")


def json_gaps(values):
    gaps = []
    for key, obj in values.items():
        if obj is None:
            gaps.append(key)
        elif isinstance(obj, dict) and not obj:
            gaps.append(key)
        elif isinstance(obj, list) and not obj:
            gaps.append(key)
    return gaps


def main():
    if not DB.exists():
        raise SystemExit(f"DB not found: {DB}")
    checked = now()
    changed = []
    with sqlite3.connect(DB, timeout=30) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA busy_timeout=30000")
        ensure_tables(con)
        for name in PERSONAS:
            mem, mem_path = read_memory(name)
            row = con.execute("SELECT * FROM lobster_agent_memory_snapshots WHERE persona=?", (name,)).fetchone()
            full = load_json_text(row["full_json"], {}) if row else {}
            if mem:
                full = {**mem, **full}
            wins = int((row["wins"] if row else None) or full.get("wins") or 0)
            losses = int((row["losses"] if row else None) or full.get("losses") or 0)
            sessions = int((row["sessions"] if row else None) or full.get("sessions") or (wins + losses))
            emotional = load_json_text(row["emotional_state_json"], {}) if row else {}
            dynamic = load_json_text(row["dynamic_json"], {}) if row else {}
            local_brain = load_json_text(row["local_brain_json"], {}) if row else {}
            listening = load_json_text(row["listening_profile_json"], {}) if row else {}
            social = load_json_text(row["social_map_json"], {}) if row else {}
            cog_profile = load_json_text(row["cog_profile_json"], {}) if row else {}
            before = json_gaps({
                "emotional_state_json": emotional,
                "dynamic_json": dynamic,
                "local_brain_json": local_brain,
                "listening_profile_json": listening,
                "social_map_json": social,
                "cog_profile_json": cog_profile,
                "full_json": full,
            })

            emotional = emotional or full.get("emotionalState") or default_emotion(name, full)
            emotional.setdefault("mood", emotional.get("label") or "continuity_recovery")
            emotional.setdefault("label", emotional.get("mood") or "continuity_recovery")
            emotional.setdefault("intensity", 0.55)
            local_brain = local_brain or full.get("localBrain") or default_local_brain(name, full)
            local_brain = default_local_brain(name, {**full, "localBrain": local_brain})
            listening = listening or full.get("listeningProfile") or {"bySpeaker": {}, "rejectedSignals": [], "source": "restore_lobster_30_full_health"}
            social = fill_social_map(name, social or full.get("socialMap") or {})
            dynamic = normalize_dynamic(name, full, dynamic or full.get("dynamic") or {}, social)
            cog_profile = default_cog_profile(name, {**full, "cogProfile": cog_profile or full.get("cogProfile") or {}})

            full.update({
                "name": name,
                "wins": wins,
                "losses": losses,
                "sessions": sessions,
                "emotionalState": emotional,
                "dynamic": dynamic,
                "localBrain": local_brain,
                "listeningProfile": listening,
                "socialMap": social,
                "cogProfile": cog_profile,
                "recovery": {"source": "restore_lobster_30_full_health", "checked_at": checked, "memory_path": mem_path},
            })
            after = json_gaps({
                "emotional_state_json": emotional,
                "dynamic_json": dynamic,
                "local_brain_json": local_brain,
                "listening_profile_json": listening,
                "social_map_json": social,
                "cog_profile_json": cog_profile,
                "full_json": full,
            })

            con.execute("""INSERT INTO lobster_agent_memory_snapshots
                (persona, updated_at, wins, losses, sessions, emotional_state_json, dynamic_json,
                 local_brain_json, listening_profile_json, social_map_json, cog_profile_json, full_json)
                VALUES (?, datetime('now'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(persona) DO UPDATE SET
                  updated_at=datetime('now'),
                  wins=excluded.wins,
                  losses=excluded.losses,
                  sessions=excluded.sessions,
                  emotional_state_json=excluded.emotional_state_json,
                  dynamic_json=excluded.dynamic_json,
                  local_brain_json=excluded.local_brain_json,
                  listening_profile_json=excluded.listening_profile_json,
                  social_map_json=excluded.social_map_json,
                  cog_profile_json=excluded.cog_profile_json,
                  full_json=excluded.full_json""",
                (name, wins, losses, sessions,
                 json.dumps(emotional, ensure_ascii=False),
                 json.dumps(dynamic, ensure_ascii=False),
                 json.dumps(local_brain, ensure_ascii=False),
                 json.dumps(listening, ensure_ascii=False),
                 json.dumps(social, ensure_ascii=False),
                 json.dumps(cog_profile, ensure_ascii=False),
                 json.dumps(full, ensure_ascii=False)))

            resolved = wins + losses
            wr = wins / resolved if resolved else None
            con.execute("""INSERT INTO lobster_winrate_display_cache
                (persona, wins, losses, sessions, resolved_total, win_rate, source, updated_at, raw_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'), ?)
                ON CONFLICT(persona) DO UPDATE SET
                  wins=excluded.wins,
                  losses=excluded.losses,
                  sessions=excluded.sessions,
                  resolved_total=excluded.resolved_total,
                  win_rate=excluded.win_rate,
                  source=excluded.source,
                  updated_at=datetime('now'),
                  raw_json=excluded.raw_json""",
                (name, wins, losses, sessions, resolved, wr, "lobster_30_recovery",
                 json.dumps({"memory_path": mem_path, "checked_at": checked}, ensure_ascii=False)))

            status = "restored_from_memory" if mem_path else ("enriched_existing_snapshot" if row else "recovery_shell_created")
            con.execute("""INSERT INTO lobster_30_recovery_audit
                (checked_at, persona, status, memory_path, json_gaps_before, json_gaps_after, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (checked, name, status, mem_path,
                 json.dumps(before, ensure_ascii=False),
                 json.dumps(after, ensure_ascii=False),
                 "one-on-one and reports were not fabricated"))
            changed.append({"persona": name, "status": status, "before": before, "after": after})
        con.commit()
    print(json.dumps({"ok": True, "checked_at": checked, "personas": len(changed), "items": changed}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
