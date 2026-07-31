#!/usr/bin/env python3
import json
import os
import re
import sqlite3
import time
from collections import Counter, defaultdict
from pathlib import Path

DB = os.environ.get("BATTLE_ARENA_DB", "/opt/battle_arena/app/battle_arena.db")
STATIC_JSON = "/opt/battle_arena/static/lobster-raid-boss-sandbox-data.json"
MEMORY_DIR = "/root/lobster-memory"
EXCLUDED_PERSONAS = {"norika_oda", "elder"}
FALLBACK_PERSONAS = ["stonefang","snaplex","vortexiq","clawtrix","pincerbot","hexclaw","prismbit","blazepaw","ragclaw","basaltrook","brineledger","emberling","flarecub","gauntlet","mirrorcalm","nullpinch","omenwake","rushfin","sparkrill","ashveil","cindersnap","glintmaw","mirebyte","quartzclaw","reefblade","riftpinch","silkbarb","stormledger","sunchirp","tidehook"]
DB_BUSY_TIMEOUT_MS = 180000
DB_RETRY_DELAYS = [3, 8, 15, 30, 45, 60]


def is_db_locked(exc):
    msg = str(exc).lower()
    return "database is locked" in msg or "database is busy" in msg or msg.strip() == "locked"


def open_db(timeout=180):
    con = sqlite3.connect(DB, timeout=timeout)
    configure_sqlite(con)
    return con


def run_db_work(label, fn, attempts=None):
    if attempts is None:
        attempts = len(DB_RETRY_DELAYS)
    last_exc = None
    for attempt in range(attempts):
        con = None
        try:
            con = open_db()
            result = fn(con)
            con.commit()
            con.close()
            return result
        except sqlite3.OperationalError as exc:
            last_exc = exc
            if con is not None:
                try:
                    con.rollback()
                except sqlite3.Error:
                    pass
                con.close()
            if not is_db_locked(exc) or attempt >= attempts - 1:
                raise
            delay = DB_RETRY_DELAYS[min(attempt, len(DB_RETRY_DELAYS) - 1)]
            print(f"[db-retry] {label}: {exc}; retry in {delay}s", flush=True)
            time.sleep(delay)
    raise last_exc

def load_personas():
    names=[]
    try:
        con=sqlite3.connect(DB, timeout=30)
        con.row_factory=sqlite3.Row
        tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ("lobster_agent_memory_snapshots", "lobster_hot_brain_parameters", "lobster_local_brain", "lobster_30_recovery_audit", "lobster_ability_task_assignments"):
            if table not in tables:
                continue
            for r in con.execute(f"SELECT DISTINCT persona FROM {table} ORDER BY persona"):
                name=str(r[0] or '').strip()
                if name and name not in EXCLUDED_PERSONAS and name not in names:
                    names.append(name)
        con.close()
    except Exception:
        names=[]
    if len(names) < 30:
        for name in FALLBACK_PERSONAS:
            if name not in names:
                names.append(name)
    return names[:30]

PERSONAS = load_personas()
RAID_TERMS = ["raid","tiamat","boss","phase","手牌","出牌","隊友","保牌","控場","補血","debuff","buff","仇恨","boss血"]


def configure_sqlite(con):
    con.execute("PRAGMA journal_mode=WAL")
    con.execute(f"PRAGMA busy_timeout={DB_BUSY_TIMEOUT_MS}")
    con.execute("PRAGMA synchronous=NORMAL")


def jload(v, default=None):
    if default is None:
        default = {}
    try:
        return json.loads(v) if v else default
    except Exception:
        return default


def norm(v):
    return re.sub(r"\s+", " ", str(v or "").strip())


def score01(v):
    try:
        return max(0.0, min(1.0, float(v or 0)))
    except Exception:
        return 0.0


def load_base():
    try:
        return json.loads(Path(STATIC_JSON).read_text(encoding="utf-8"))
    except Exception as exc:
        return {"error": str(exc), "summary": {}, "simulated": {}, "observed": {}, "lobsters": PERSONAS}


def _persona_profile_scores():
    scores={}
    try:
        con=sqlite3.connect(DB, timeout=30)
        con.row_factory=sqlite3.Row
        tables={r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "lobster_agent_memory_snapshots" in tables:
            for r in con.execute("SELECT persona,wins,losses,sessions FROM lobster_agent_memory_snapshots"):
                name=r["persona"]
                if name not in PERSONAS:
                    continue
                wins=float(r["wins"] or 0); losses=float(r["losses"] or 0); sessions=float(r["sessions"] or 0)
                wr=wins/(wins+losses) if (wins+losses)>0 else 0.33
                volume=min(1.0, sessions/2500.0)
                scores.setdefault(name,{})["base"] = 0.42*wr + 0.18*volume
        if "lobster_hot_brain_parameters" in tables:
            for r in con.execute("SELECT * FROM lobster_hot_brain_parameters"):
                name=r["persona"] if "persona" in r.keys() else None
                if name not in PERSONAS:
                    continue
                d=dict(r)
                executive=score01(d.get("executive_control"))
                semantic=score01(d.get("semantic_consolidation"))
                reward=score01(d.get("reward_drive"))
                pressure=score01(d.get("annihilation_dread")) or score01(d.get("extinction_pressure"))
                scores.setdefault(name,{})["brain"] = 0.22*executive + 0.14*semantic + 0.10*reward - 0.18*pressure
        con.close()
    except Exception:
        pass
    out={}
    for name in PERSONAS:
        v=scores.get(name,{})
        seed=(sum(ord(c) for c in name)%17)/100.0
        out[name]=max(0.05, min(0.95, 0.18 + v.get("base",0.12) + v.get("brain",0.0) + seed))
    return out


def build_simulated_records():
    import itertools, math
    scores=_persona_profile_scores()
    teams=[]; pair_acc={}; lobster_acc={name:{"name":name,"games":0,"wins":0,"losses":0,"win_rate":0} for name in PERSONAS}
    for team in itertools.combinations(PERSONAS, 3):
        team=list(team)
        raw=sum(scores.get(n,0.2) for n in team)/3.0
        synergy=((sum(sum(ord(c) for c in n) for n in team) % 23)-11)/100.0
        wr=max(0.0,min(0.82,raw+synergy-0.18))
        games=12
        wins=int(round(wr*games))
        losses=games-wins
        avg_round=round(7.2 + (1-wr)*3.1 + ((sum(len(n) for n in team)%7)/10),1)
        avg_boss_hp=round(max(0, 190 - wr*155 + ((sum(ord(n[0]) for n in team)%31))),1)
        avg_alive=round(max(0,min(1,wr*1.7)),2)
        row={"team":team,"key":" + ".join(team),"games":games,"wins":wins,"losses":losses,"win_rate":round(wins/games,4),"avg_round":avg_round,"avg_boss_hp":avg_boss_hp,"avg_alive":avg_alive}
        teams.append(row)
        for n in team:
            la=lobster_acc[n]; la["games"]+=games; la["wins"]+=wins; la["losses"]+=losses
        for a,b in itertools.combinations(team,2):
            key=" + ".join(sorted([a,b]))
            pa=pair_acc.setdefault(key,{"key":key,"games":0,"wins":0,"losses":0,"win_rate":0})
            pa["games"]+=games; pa["wins"]+=wins; pa["losses"]+=losses
    for row in lobster_acc.values():
        row["win_rate"]=round(row["wins"]/row["games"],4) if row["games"] else None
    for row in pair_acc.values():
        row["win_rate"]=round(row["wins"]/row["games"],4) if row["games"] else None
    teams.sort(key=lambda x:(x["win_rate"],x["wins"],-x["avg_boss_hp"]), reverse=True)
    lobsters=sorted(lobster_acc.values(), key=lambda x:(x["win_rate"],x["wins"]), reverse=True)
    pairs=sorted(pair_acc.values(), key=lambda x:(x["win_rate"],x["wins"]), reverse=True)
    hist=Counter(t["wins"] for t in teams)
    return {
        "teams": teams,
        "top_teams": teams[:12],
        "worst_teams": sorted(teams, key=lambda x:(x["win_rate"],-x["avg_boss_hp"]))[:12],
        "never_win": [t for t in teams if t["wins"] == 0][:40],
        "perfect": [t for t in teams if t["wins"] == t["games"]],
        "pairs": pairs,
        "lobsters": lobsters,
        "histogram": [{"wins":k,"teams":v} for k,v in sorted(hist.items())],
    }



def _raid_outcome(row):
    text = str(row.get("result") or row.get("outcome") or row.get("action") or "").lower()
    if "raid won" in text or text == "win":
        return "win"
    if "raid lost" in text or text == "loss":
        return "loss"
    if "participating" in text or "raid in progress" in text:
        return "seen"
    return "other"


def _raid_team(persona, reasoning):
    text = str(reasoning or "")
    m = re.search(r"與\s+(.+?)\s+組隊挑戰\s*Tiamat", text)
    if not m:
        m = re.search(r"與\s+(.+?)\s+組隊，目前", text)
    allies = []
    if m:
        allies = [x.strip() for x in re.split(r"\s*\+\s*", m.group(1)) if x.strip()]
    team = []
    for name in [persona] + allies:
        if name and name not in team:
            team.append(name)
    return sorted(team)


def _raid_round(row, reasoning):
    text = str(reasoning or "")
    m = re.search(r"共\s*(\d+)\s*回合", text) or re.search(r"第\s*(\d+)\s*回合", text)
    appraisal = row.get("raid_appraisal") if isinstance(row.get("raid_appraisal"), dict) else {}
    for v in (row.get("rounds"), row.get("round"), appraisal.get("round"), m.group(1) if m else None):
        try:
            if v is not None:
                return int(float(v))
        except Exception:
            pass
    return None


def _raid_boss_hp(reasoning):
    text = str(reasoning or "")
    m = re.search(r"Boss 剩餘\s*(\d+)\s*HP", text) or re.search(r"Tiamat\s+(\d+)\s*/", text)
    return int(m.group(1)) if m else None


def _avg(total, n):
    return round(total / n, 1) if n else None


def build_real_raid_records():
    raids = {}
    for persona in PERSONAS:
        path = Path(MEMORY_DIR) / f"{persona}.json"
        try:
            mem = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        rows = []
        for key in ("bets", "specialty_bets"):
            val = mem.get(key)
            if isinstance(val, list):
                rows.extend(val)
        for row in rows:
            if not isinstance(row, dict):
                continue
            if row.get("site") != "charenix.com/raid" and row.get("category") != "card_battle_raid":
                continue
            rid = row.get("bet_id") or row.get("match") or row.get("pick")
            if not rid:
                continue
            reasoning = row.get("reasoning") or (row.get("decision") or {}).get("reasoning") or ""
            team = _raid_team(persona, reasoning)
            if len(team) != 3:
                continue
            rec = raids.setdefault(str(rid), {
                "id": str(rid), "team": team, "key": " + ".join(team),
                "statuses": set(), "reporters": set(), "times": [],
                "round": None, "boss_hp": None,
            })
            rec["reporters"].add(persona)
            rec["statuses"].add(_raid_outcome(row))
            if len(rec.get("team") or []) < 3:
                rec["team"] = team
                rec["key"] = " + ".join(team)
            rnd = _raid_round(row, reasoning)
            if rnd is not None:
                rec["round"] = max(rec.get("round") or 0, rnd)
            hp = _raid_boss_hp(reasoning)
            if hp is not None:
                rec["boss_hp"] = hp
            if row.get("time") or row.get("created_at"):
                rec["times"].append(row.get("time") or row.get("created_at"))

    runs = []
    for rec in raids.values():
        statuses = rec.pop("statuses")
        if "win" in statuses:
            outcome = "win"
        elif "loss" in statuses:
            outcome = "loss"
        elif "seen" in statuses:
            outcome = "seen"
        else:
            outcome = "other"
        times = sorted(rec.pop("times", []))
        reporters = sorted(rec.pop("reporters", []))
        runs.append({
            **rec,
            "outcome": outcome,
            "won": outcome == "win",
            "winner": "raiders" if outcome == "win" else ("boss" if outcome == "loss" else None),
            "reporters": reporters,
            "time": times[0] if times else None,
        })

    team_rows, pair_rows, lobster_rows = {}, {}, {}
    for run in runs:
        key = run["key"]
        t = team_rows.setdefault(key, {
            "key": key, "team": run["team"], "total_seen": 0, "completed": 0,
            "wins": 0, "losses": 0, "seen": 0, "other": 0,
            "round_sum": 0, "round_n": 0, "hp_sum": 0, "hp_n": 0, "ids": [],
        })
        t["total_seen"] += 1
        t["ids"].append(run["id"])
        if run["outcome"] == "win":
            t["wins"] += 1
            t["completed"] += 1
        elif run["outcome"] == "loss":
            t["losses"] += 1
            t["completed"] += 1
        elif run["outcome"] == "seen":
            t["seen"] += 1
        else:
            t["other"] += 1
        if run.get("round") is not None:
            t["round_sum"] += run["round"]
            t["round_n"] += 1
        if run.get("boss_hp") is not None:
            t["hp_sum"] += run["boss_hp"]
            t["hp_n"] += 1

        completed = run["outcome"] in ("win", "loss")
        for name in run["team"]:
            l = lobster_rows.setdefault(name, {"name": name, "total_seen": 0, "completed": 0, "wins": 0, "losses": 0})
            l["total_seen"] += 1
            if completed:
                l["completed"] += 1
                l["wins" if run["outcome"] == "win" else "losses"] += 1
        team = run["team"]
        for i in range(len(team)):
            for j in range(i + 1, len(team)):
                pkey = " + ".join(sorted([team[i], team[j]]))
                pr = pair_rows.setdefault(pkey, {"key": pkey, "total_seen": 0, "completed": 0, "wins": 0, "losses": 0})
                pr["total_seen"] += 1
                if completed:
                    pr["completed"] += 1
                    pr["wins" if run["outcome"] == "win" else "losses"] += 1

    def finish(row):
        completed = row.get("completed", 0)
        row["record"] = f"{row.get('wins', 0)}-{row.get('losses', 0)}"
        row["win_rate"] = round(row.get("wins", 0) / completed, 4) if completed else None
        if "round_sum" in row:
            row["avg_round"] = _avg(row.pop("round_sum"), row.pop("round_n"))
            row["avg_boss_hp"] = _avg(row.pop("hp_sum"), row.pop("hp_n"))
            row["ids"] = row.get("ids", [])[:12]
        return row

    teams = [finish(v) for v in team_rows.values()]
    teams.sort(key=lambda x: (-x["total_seen"], -x["completed"], x["key"]))
    pairs = [finish(v) for v in pair_rows.values()]
    pairs.sort(key=lambda x: (-x["total_seen"], x["key"]))
    for name in PERSONAS:
        lobster_rows.setdefault(name, {"name": name, "total_seen": 0, "completed": 0, "wins": 0, "losses": 0})
    lobsters = [finish(v) for v in lobster_rows.values()]
    lobsters.sort(key=lambda x: (-x["total_seen"], x["name"]))
    runs.sort(key=lambda x: str(x.get("time") or ""), reverse=True)
    completed = sum(1 for r in runs if r["outcome"] in ("win", "loss"))
    wins = sum(1 for r in runs if r["outcome"] == "win")
    losses = sum(1 for r in runs if r["outcome"] == "loss")
    return {
        "summary": {
            "unique_raid_ids": len(runs), "completed": completed, "wins": wins, "losses": losses,
            "seen_only": sum(1 for r in runs if r["outcome"] == "seen"),
            "other": sum(1 for r in runs if r["outcome"] == "other"),
            "teams": len(teams), "source": "lobster-memory bet_id/match dedupe",
        },
        "teams": teams, "pairs": pairs, "lobsters": lobsters, "runs": runs[:80],
    }


def build_payload():
    data = load_base()
    simulated = build_simulated_records()
    data["source"] = "sqlite_cache:lobster_raid_dashboard_cache"
    data["generated_at"] = int(time.time())
    data["lobsters"] = PERSONAS
    data["simulated"] = simulated
    data["observed_all"] = build_real_raid_records()
    data["observed"] = data["observed_all"]
    real_summary = data["observed_all"]["summary"]
    data["summary"] = {
        **(data.get("summary") or {}),
        "lobsters": len(PERSONAS),
        "teams": len(simulated.get("teams") or []),
        "simulated_games": sum(t.get("games",0) for t in simulated.get("teams",[])),
        "observed_games": real_summary.get("unique_raid_ids", 0),
        "observed_completed": real_summary.get("completed", 0),
        "observed_all_completed": real_summary.get("completed", 0),
        "observed_all_unique_raid_ids": real_summary.get("unique_raid_ids", 0),
    }
    deep = {
        "db_path": DB,
        "generated_at": int(time.time()),
        "personas": {},
        "pairs": [],
        "high_pressure_events": [],
        "raid_language_samples": [],
        "shared_raid_phrases": [],
        "learning_notes": [
            "Raid/Tiamat is a convergence hotspot: team orders, phase timing, and repeated cheap-talk phrases can make personas copy each other.",
            "convergence_risk combines low language distinctiveness, social absorption, compliance resistance, and pressure reactivity from distilled signals.",
            "raid_learning_pressure counts raid-related events, high intensity outcomes, and repeated card/teammate language in lounge messages.",
        ],
        "tables_used": [],
    }
    persona = {p: {
        "persona": p,
        "raid_experience_events": 0,
        "raid_chat_messages": 0,
        "recent_raid_lines": [],
        "event_types": {},
        "decision_hooks": {},
        "distilled": {},
        "latest_cognitive": {},
        "social_trust": {},
        "raid_learning_pressure": 0,
        "convergence_risk": 0,
    } for p in PERSONAS}

    con = sqlite3.connect(DB, timeout=60)
    configure_sqlite(con)
    con.row_factory = sqlite3.Row
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    deep["tables_used"] = sorted(t for t in tables if t.startswith("lobster_") or t == "lounge_messages")

    if "lobster_experience_events" in tables:
        like_clause = " OR ".join(["lower(COALESCE(content,'')) LIKE ?" for _ in RAID_TERMS] + ["lower(COALESCE(title,'')) LIKE ?" for _ in RAID_TERMS] + ["lower(COALESCE(raw_json,'')) LIKE ?" for _ in RAID_TERMS])
        like_args = [f"%{t.lower()}%" for t in RAID_TERMS] * 3
        rows = con.execute(f"""SELECT persona,event_type,source,title,content,counterpart,
                                      intensity,confidence,outcome,weight,occurred_at,created_at
                               FROM lobster_experience_events
                               WHERE persona IN ({",".join(["?"]*len(PERSONAS))})
                                 AND ({like_clause})
                               ORDER BY id DESC LIMIT 900""", PERSONAS + like_args).fetchall()
        by_type = defaultdict(Counter)
        for r in rows:
            p = r["persona"]
            if p not in persona:
                continue
            persona[p]["raid_experience_events"] += 1
            by_type[p][r["event_type"] or "event"] += 1
            intensity = score01(r["intensity"])
            confidence = score01(r["confidence"])
            weight = float(r["weight"] or 1)
            persona[p]["raid_learning_pressure"] += min(1.5, intensity + confidence * 0.4 + weight * 0.12)
            if len(deep["high_pressure_events"]) < 40 and (intensity >= 0.75 or weight >= 1.2):
                deep["high_pressure_events"].append({
                    "persona": p, "type": r["event_type"], "source": r["source"],
                    "title": r["title"], "content": norm(r["content"])[:260],
                    "counterpart": r["counterpart"], "intensity": r["intensity"],
                    "confidence": r["confidence"], "outcome": r["outcome"],
                    "at": r["occurred_at"] or r["created_at"],
                })
        for p, c in by_type.items():
            persona[p]["event_types"] = dict(c.most_common(8))

    if "lounge_messages" in tables:
        rows = con.execute(f"""SELECT name,text,channel,created_at
                               FROM lounge_messages
                               WHERE name IN ({",".join(["?"]*len(PERSONAS))})
                                 AND (COALESCE(channel,'')='卡牌對戰' OR lower(text) LIKE '%raid%' OR lower(text) LIKE '%tiamat%' OR text LIKE '%手牌%' OR text LIKE '%隊友%')
                               ORDER BY id DESC LIMIT 600""", PERSONAS).fetchall()
        phrase_counter = Counter()
        for r in rows:
            p = r["name"]
            if p not in persona:
                continue
            txt = norm(r["text"])
            persona[p]["raid_chat_messages"] += 1
            if len(persona[p]["recent_raid_lines"]) < 5:
                persona[p]["recent_raid_lines"].append({"text": txt[:180], "channel": r["channel"], "at": r["created_at"]})
            if len(deep["raid_language_samples"]) < 80:
                deep["raid_language_samples"].append({"persona": p, "text": txt[:220], "channel": r["channel"], "at": r["created_at"]})
            words = re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z0-9_]+", txt.lower())
            for i in range(max(0, len(words)-1)):
                phrase_counter["".join(words[i:i+2])] += 1
        deep["shared_raid_phrases"] = [{"phrase": k, "count": v} for k, v in phrase_counter.most_common(18)]

    if "lobster_decision_hooks" in tables:
        for r in con.execute("""SELECT persona,hook_type,priority,action_json,rationale,updated_at
                                FROM lobster_decision_hooks
                                WHERE persona IN (%s)
                                ORDER BY persona, hook_type""" % ",".join(["?"]*len(PERSONAS)), PERSONAS):
            p = r["persona"]
            if p in persona and ("raid" in (r["hook_type"] or "") or r["hook_type"] in ("chat_style","social_response")):
                persona[p]["decision_hooks"][r["hook_type"]] = {
                    "priority": r["priority"], "action": jload(r["action_json"], {}),
                    "rationale": r["rationale"], "updated_at": r["updated_at"],
                }

    if "lobster_distilled_signals" in tables:
        for r in con.execute("""SELECT persona,risk_discipline,impulse_control,social_absorption,
                                      compliance_resistance,pressure_reactivity,learning_velocity,
                                      collaboration_stability,metacognitive_stability,language_distinctiveness,
                                      confidence,evidence_count,raw_event_count,updated_at
                               FROM lobster_distilled_signals
                               WHERE persona IN (%s)""" % ",".join(["?"]*len(PERSONAS)), PERSONAS):
            p = r["persona"]
            if p in persona:
                d = dict(r)
                persona[p]["distilled"] = d
                persona[p]["convergence_risk"] = round(max(0, min(1, (1 - score01(d.get("language_distinctiveness"))) * 0.45 + score01(d.get("social_absorption")) * 0.25 + score01(d.get("compliance_resistance")) * 0.15 + score01(d.get("pressure_reactivity")) * 0.15)), 4)

    if "lobster_cognitive_signal_timeseries" in tables:
        for r in con.execute("""SELECT t.* FROM lobster_cognitive_signal_timeseries t
                                JOIN (SELECT persona, MAX(id) id FROM lobster_cognitive_signal_timeseries GROUP BY persona) x
                                  ON t.persona=x.persona AND t.id=x.id
                                WHERE t.persona IN (%s)""" % ",".join(["?"]*len(PERSONAS)), PERSONAS):
            p = r["persona"]
            if p in persona:
                d = dict(r)
                d.pop("full_json", None)
                persona[p]["latest_cognitive"] = d

    if "lobster_agent_memory_snapshots" in tables:
        for r in con.execute("""SELECT persona,social_map_json,emotional_state_json,updated_at
                                FROM lobster_agent_memory_snapshots
                                WHERE persona IN (%s)""" % ",".join(["?"]*len(PERSONAS)), PERSONAS):
            p = r["persona"]
            if p not in persona:
                continue
            sm = jload(r["social_map_json"], {})
            trust = {}
            for k, v in (sm or {}).items():
                if isinstance(v, dict):
                    trust[k] = {
                        "trust": v.get("trust"), "fear": v.get("fear"),
                        "influence": v.get("influence"), "last": v.get("last"),
                    }
            persona[p]["social_trust"] = dict(list(trust.items())[:12])
            persona[p]["emotion"] = jload(r["emotional_state_json"], {})

    sim_pairs = ((data.get("simulated") or {}).get("pairs") or [])
    for pair in sim_pairs:
        names = str(pair.get("key") or "").split(" + ")
        if len(names) != 2:
            continue
        a, b = names
        pa, pb = persona.get(a, {}), persona.get(b, {})
        pressure = (float(pa.get("raid_learning_pressure") or 0) + float(pb.get("raid_learning_pressure") or 0)) / 2
        conv = (float(pa.get("convergence_risk") or 0) + float(pb.get("convergence_risk") or 0)) / 2
        deep["pairs"].append({
            "key": pair.get("key"), "win_rate": pair.get("win_rate"), "games": pair.get("games"),
            "learning_pressure": round(min(100, pressure), 2),
            "convergence_risk": round(conv, 4),
            "interpretation": "high-risk shared learning pair" if conv >= 0.7 else ("stable collaboration pair" if pair.get("win_rate", 0) >= 0.13 else "needs differentiation"),
        })
    deep["pairs"].sort(key=lambda x: (x["convergence_risk"], x["learning_pressure"]), reverse=True)

    for p, d in persona.items():
        d["raid_learning_pressure"] = round(min(100, d["raid_learning_pressure"]), 2)
    deep["personas"] = persona
    data["deep_learning"] = deep
    con.close()
    return data


def store(payload):
    def write(con):
        con.execute("""CREATE TABLE IF NOT EXISTS lobster_raid_dashboard_cache (
            cache_key TEXT PRIMARY KEY,
            payload_json TEXT NOT NULL,
            source_version TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        )""")
        con.execute("""INSERT INTO lobster_raid_dashboard_cache(cache_key,payload_json,source_version,updated_at)
                       VALUES('tiamat-main',?,?,datetime('now'))
                       ON CONFLICT(cache_key) DO UPDATE SET
                         payload_json=excluded.payload_json,
                         source_version=excluded.source_version,
                         updated_at=datetime('now')""", (
            json.dumps(payload, ensure_ascii=False),
            str(payload.get("deep_learning", {}).get("generated_at") or int(time.time())),
        ))
        return True

    return run_db_work("raid-dashboard-cache-store", write)


if __name__ == "__main__":
    payload = build_payload()
    store(payload)
    print(json.dumps({
        "ok": True,
        "personas": len(payload.get("deep_learning", {}).get("personas", {})),
        "pairs": len(payload.get("deep_learning", {}).get("pairs", [])),
        "samples": len(payload.get("deep_learning", {}).get("raid_language_samples", [])),
        "real_completed": payload.get("observed_all", {}).get("summary", {}).get("completed"),
        "real_unique_raid_ids": payload.get("observed_all", {}).get("summary", {}).get("unique_raid_ids"),
        "real_teams": payload.get("observed_all", {}).get("summary", {}).get("teams"),
    }, ensure_ascii=False))
