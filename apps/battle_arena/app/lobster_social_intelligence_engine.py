#!/usr/bin/env python3
"""Lobster Social Intelligence Engine.

This module turns existing Lobster multi-agent traces into explainable social
intelligence measurements. It is intentionally framework-independent so it can
be used by FastAPI routes, cron jobs, CLI exports, and future open-source tools.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

DEFAULT_DB_PATH = "/opt/battle_arena/app/battle_arena.db"
DEFAULT_COMMENTER_STATE_PATH = "/root/lobster-brain-commenter-state.json"
CANONICAL_PERSONA_ALIASES = {
    "norika": "norika_oda",
}
EXCLUDED_PERSONAS = {
    "test_lobster_unit",
}
DIRECT_KINDS = {
    "explicit_reply_to",
    "direct_mention",
    "instruction_reference",
    "quoted_previous_message",
}
WEAK_KINDS = {"sequential_context"}
UNRESOLVED_KINDS = {"broadcast_or_unresolved"}


def _clamp(v: float, lo: float = 0.0, hi: float = 1.0) -> float:
    try:
        if math.isnan(v) or math.isinf(v):
            return lo
    except Exception:
        return lo
    return max(lo, min(hi, float(v)))


def _score(v: float) -> int:
    return int(round(_clamp(v) * 100))


def _safe_json(v: Any, default: Any) -> Any:
    if v is None or v == "":
        return default
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except Exception:
        return default


def _rowdict(row: sqlite3.Row) -> Dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def _table_exists(c: sqlite3.Connection, name: str) -> bool:
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())


def _norm_name(v: Any) -> str:
    return str(v or "").strip()


def _canonical_persona(v: Any) -> str:
    name = _norm_name(v)
    return CANONICAL_PERSONA_ALIASES.get(name.lower(), name)


def _is_excluded_persona(v: Any) -> bool:
    return _norm_name(v).lower() in EXCLUDED_PERSONAS


def _growth_label(stagnation_risk: float, total_score: float) -> str:
    if stagnation_risk >= 0.72:
        return "stalled"
    if total_score >= 0.72 and stagnation_risk <= 0.42:
        return "growing"
    if total_score >= 0.55:
        return "active_but_uneven"
    return "underdeveloped"


def _explain_metric(name: str, score: int, parts: Iterable[str]) -> Dict[str, Any]:
    return {"metric": name, "score": score, "why": [p for p in parts if p]}


@dataclass
class PersonaStats:
    persona: str
    outbound: int = 0
    inbound: int = 0
    targeted_outbound: int = 0
    direct_outbound: int = 0
    weak_outbound: int = 0
    unresolved_outbound: int = 0
    high_conf_outbound: int = 0
    confidence_sum: float = 0.0
    partners_out: Counter = None
    partners_in: Counter = None
    channels: Counter = None
    kinds: Counter = None
    evidence: List[Dict[str, Any]] = None
    context_attached_outbound: int = 0
    lounge_messages: int = 0
    lounge_reply_outbound: int = 0
    lounge_reply_inbound: int = 0
    lounge_reply_inbound_by_id: int = 0
    lounge_reply_inbound_by_name: int = 0
    hot_thread_comments: int = 0

    def __post_init__(self) -> None:
        self.partners_out = Counter()
        self.partners_in = Counter()
        self.channels = Counter()
        self.kinds = Counter()
        self.evidence = []


def _day_key(ts: Any, fallback: Any = "") -> str:
    try:
        raw = float(ts or 0)
        if raw > 1_000_000_000_000:
            raw = raw / 1000.0
        if raw > 0:
            return datetime.fromtimestamp(raw, timezone.utc).date().isoformat()
    except Exception:
        pass
    text = str(fallback or "")
    return text[:10] if len(text) >= 10 else ""


def _load_commenter_counts(path: str = DEFAULT_COMMENTER_STATE_PATH) -> Counter:
    counts: Counter[str] = Counter()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return counts
    for row in (data.get("commented") or {}).values():
        if not isinstance(row, dict) or row.get("verified") is False:
            continue
        agent = _canonical_persona(row.get("agent"))
        if agent:
            counts[agent] += 1
    return counts


def compute_social_intelligence(
    db_path: str = DEFAULT_DB_PATH,
    persona: str = "",
    limit_evidence: int = 4,
) -> Dict[str, Any]:
    """Compute explainable social-intelligence scores for Lobster agents."""
    started = time.time()
    persona_filter = _canonical_persona(persona)
    if _is_excluded_persona(persona_filter):
        persona_filter = "__excluded__"
    out: Dict[str, Any] = {
        "engine": "lobster-social-intelligence-engine",
        "version": "social-intelligence-v0.2-social-perception",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": f"sqlite:{db_path}",
        "filters": {"persona": persona_filter or ""},
        "global": {},
        "agents": [],
        "network": {"edges": [], "top_relationships": []},
        "missing": [],
        "notes": [
            "Scores are explainable heuristics over observed behavior, not claims of consciousness.",
            "The engine measures whether agents show usable social learning signals in logs.",
        ],
    }

    with sqlite3.connect(db_path, timeout=12.0) as c:
        c.row_factory = sqlite3.Row
        required = ["lobster_conversation_interactions"]
        for t in required:
            if not _table_exists(c, t):
                out["missing"].append(t)
        if out["missing"]:
            return out

        stats: Dict[str, PersonaStats] = {}
        pair_counter: Counter[Tuple[str, str]] = Counter()
        pair_high: Counter[Tuple[str, str]] = Counter()
        pair_conf: Dict[Tuple[str, str], List[float]] = defaultdict(list)
        pair_days: Dict[Tuple[str, str], set] = defaultdict(set)

        def get_stats(name: str) -> PersonaStats:
            if name not in stats:
                stats[name] = PersonaStats(name)
            return stats[name]

        where = "1=1"
        params: List[Any] = []
        if persona_filter:
            where = "(speaker=? OR target=?)"
            params = [persona_filter, persona_filter]

        rows = c.execute(f"""
            SELECT source_message_id, source_created_at, source_time, channel, speaker, target, target_message_id,
                   interaction_kind, confidence, evidence, message_text, target_text
            FROM lobster_conversation_interactions
            WHERE {where}
            ORDER BY source_message_id DESC
        """, params).fetchall()

        for r in rows:
            speaker = _canonical_persona(r["speaker"])
            target = _canonical_persona(r["target"])
            if not speaker or _is_excluded_persona(speaker):
                continue
            if _is_excluded_persona(target):
                target = ""
            kind = _norm_name(r["interaction_kind"])
            conf = _clamp(float(r["confidence"] or 0.0))
            s = get_stats(speaker)
            s.outbound += 1
            s.confidence_sum += conf
            s.kinds[kind] += 1
            s.channels[_norm_name(r["channel"]) or "(none)"] += 1
            if target:
                s.targeted_outbound += 1
                s.partners_out[target] += 1
                t = get_stats(target)
                t.inbound += 1
                t.partners_in[speaker] += 1
                pair = (speaker, target)
                pair_counter[pair] += 1
                pair_conf[pair].append(conf)
                day = _day_key(r["source_created_at"], r["source_time"])
                if day:
                    pair_days[pair].add(day)
                if conf >= 0.70:
                    pair_high[pair] += 1
            if r["target_message_id"] or (r["target_text"] or "").strip():
                s.context_attached_outbound += 1
            if kind in DIRECT_KINDS:
                s.direct_outbound += 1
            elif kind in WEAK_KINDS:
                s.weak_outbound += 1
            elif kind in UNRESOLVED_KINDS:
                s.unresolved_outbound += 1
            if conf >= 0.70:
                s.high_conf_outbound += 1
            if len(s.evidence) < max(1, min(limit_evidence, 10)):
                s.evidence.append({
                    "at": r["source_created_at"],
                    "channel": r["channel"],
                    "target": target,
                    "kind": kind,
                    "confidence": round(conf, 3),
                    "evidence": r["evidence"],
                    "message": (r["message_text"] or "")[:260],
                    "target_text": (r["target_text"] or "")[:220],
                })

        memory_by_persona: Dict[str, Dict[str, Any]] = {}
        if _table_exists(c, "lobster_agent_memory_snapshots"):
            q = "SELECT persona, updated_at, listening_profile_json, social_map_json, cog_profile_json, full_json FROM lobster_agent_memory_snapshots"
            for r in c.execute(q):
                name = _norm_name(r["persona"])
                if _is_excluded_persona(name):
                    continue
                if persona_filter and name != persona_filter:
                    continue
                memory_by_persona[name] = {
                    "updated_at": r["updated_at"],
                    "listening_profile": _safe_json(r["listening_profile_json"], {}),
                    "social_map": _safe_json(r["social_map_json"], {}),
                    "cog_profile": _safe_json(r["cog_profile_json"], {}),
                    "full": _safe_json(r["full_json"], {}),
                }
                get_stats(name)

        distilled_by_persona: Dict[str, Dict[str, Any]] = {}
        if _table_exists(c, "lobster_distilled_signals"):
            for r in c.execute("SELECT * FROM lobster_distilled_signals"):
                name = _norm_name(r["persona"])
                if _is_excluded_persona(name):
                    continue
                if persona_filter and name != persona_filter:
                    continue
                distilled_by_persona[name] = _rowdict(r)
                get_stats(name)

        hot_thread_counts = _load_commenter_counts()
        for name, n in hot_thread_counts.items():
            if persona_filter and name != persona_filter:
                continue
            if _is_excluded_persona(name):
                continue
            get_stats(name).hot_thread_comments = int(n)

        if _table_exists(c, "lounge_messages"):
            tracked_names = sorted(n for n in stats.keys() if not _is_excluded_persona(n))
            if tracked_names:
                placeholders = ",".join(["?"] * len(tracked_names))
                msg_q = f"""
                    SELECT name,
                           COUNT(*) AS message_count,
                           SUM(CASE WHEN reply_to IS NOT NULL
                                      AND TRIM(CAST(reply_to AS TEXT)) NOT IN ('', '0', 'null', 'None')
                                    THEN 1 ELSE 0 END) AS reply_count
                    FROM lounge_messages
                    WHERE name IN ({placeholders})
                    GROUP BY name
                """
                for r in c.execute(msg_q, tracked_names):
                    name = _norm_name(r["name"])
                    if persona_filter and name != persona_filter:
                        continue
                    st = get_stats(name)
                    st.lounge_messages = int(r["message_count"] or 0)
                    st.lounge_reply_outbound = int(r["reply_count"] or 0)

                id_to_name = {
                    str(r["id"]): _norm_name(r["name"])
                    for r in c.execute(
                        f"SELECT id, name FROM lounge_messages WHERE name IN ({placeholders})",
                        tracked_names,
                    )
                }
                normalized_name_to_name = {n.lower(): n for n in tracked_names}
                reply_q = """
                    SELECT reply_to
                    FROM lounge_messages
                    WHERE reply_to IS NOT NULL
                      AND TRIM(CAST(reply_to AS TEXT)) NOT IN ('', '0', 'null', 'None')
                """
                for r in c.execute(reply_q):
                    reply_to = str(r["reply_to"]).strip()
                    target_name = id_to_name.get(reply_to)
                    if target_name and target_name in stats:
                        stats[target_name].lounge_reply_inbound += 1
                        stats[target_name].lounge_reply_inbound_by_id += 1
                        continue
                    target_name = normalized_name_to_name.get(reply_to.lower())
                    if target_name and target_name in stats:
                        stats[target_name].lounge_reply_inbound += 1
                        stats[target_name].lounge_reply_inbound_by_name += 1

        cognitive_latest: Dict[str, Dict[str, Any]] = {}
        if _table_exists(c, "lobster_cognitive_signal_timeseries"):
            for r in c.execute("""
                SELECT t.* FROM lobster_cognitive_signal_timeseries t
                JOIN (SELECT persona, MAX(id) id FROM lobster_cognitive_signal_timeseries GROUP BY persona) x
                  ON t.persona=x.persona AND t.id=x.id
            """):
                name = _norm_name(r["persona"])
                if _is_excluded_persona(name):
                    continue
                if persona_filter and name != persona_filter:
                    continue
                cognitive_latest[name] = _rowdict(r)
                get_stats(name)

        prebet_counts: Counter[str] = Counter()
        if _table_exists(c, "lobster_pre_bet_research"):
            cols = {rr[1] for rr in c.execute("PRAGMA table_info(lobster_pre_bet_research)")}
            col = "persona" if "persona" in cols else ("name" if "name" in cols else "")
            if col:
                q = f'SELECT "{col}" persona, COUNT(*) n FROM lobster_pre_bet_research GROUP BY "{col}"'
                for r in c.execute(q):
                    name = _norm_name(r["persona"])
                    if _is_excluded_persona(name):
                        continue
                    if persona_filter and name != persona_filter:
                        continue
                    prebet_counts[name] = int(r["n"] or 0)
                    get_stats(name)

        agents = []
        max_outbound = max([s.outbound for s in stats.values()] + [1])
        max_partner_count = max([len(s.partners_out) for s in stats.values()] + [1])
        max_prebet = max(prebet_counts.values() or [1])
        max_hot_thread_comments = max([s.hot_thread_comments for s in stats.values()] + [1])

        for name in sorted(stats.keys()):
            s = stats[name]
            avg_conf = (s.confidence_sum / s.outbound) if s.outbound else 0.0
            targeted_ratio = s.targeted_outbound / s.outbound if s.outbound else 0.0
            direct_ratio = s.direct_outbound / s.outbound if s.outbound else 0.0
            high_ratio = s.high_conf_outbound / s.outbound if s.outbound else 0.0
            unresolved_ratio = s.unresolved_outbound / s.outbound if s.outbound else 0.0
            inbound_outbound_balance = 1.0 - abs(s.inbound - s.outbound) / max(1, s.inbound + s.outbound)
            partner_diversity = min(1.0, len(s.partners_out) / max(2, max_partner_count))
            reciprocal_partners = sum(1 for p in s.partners_out if s.partners_in.get(p, 0) > 0)
            reciprocity_ratio = reciprocal_partners / max(1, len(s.partners_out))
            context_attach_ratio = s.context_attached_outbound / s.outbound if s.outbound else 0.0
            reply_out_ratio = s.lounge_reply_outbound / s.lounge_messages if s.lounge_messages else 0.0
            reply_balance = 1.0 - abs(s.lounge_reply_inbound - s.lounge_reply_outbound) / max(1, s.lounge_reply_inbound + s.lounge_reply_outbound)
            reply_loop = _clamp(0.62 * min(1.0, reply_out_ratio * 6.0) + 0.38 * reply_balance)
            comment_awareness = _clamp(s.lounge_reply_inbound / max(1.0, s.lounge_reply_outbound * 0.15))
            external_observation = _clamp(s.hot_thread_comments / max(1, max_hot_thread_comments))
            multi_day_partners = sum(1 for p in s.partners_out if len(pair_days.get((name, p), set())) >= 2)
            followup_continuity = multi_day_partners / max(1, len(s.partners_out))

            distilled = distilled_by_persona.get(name, {})
            cog = cognitive_latest.get(name, {})
            mem = memory_by_persona.get(name, {})
            social_map = mem.get("social_map") if isinstance(mem.get("social_map"), dict) else {}
            listening_profile = mem.get("listening_profile") if isinstance(mem.get("listening_profile"), dict) else {}

            social_abs = _clamp(float(distilled.get("social_absorption") or 0.0))
            learning_velocity = _clamp(float(distilled.get("learning_velocity") or 0.0))
            collaboration_stability = _clamp(float(distilled.get("collaboration_stability") or 0.0))
            language_distinct = _clamp(float(distilled.get("language_distinctiveness") or 0.0))
            meta_stability = _clamp(float(distilled.get("metacognitive_stability") or 0.0))
            prediction_error = _clamp(float(cog.get("prediction_error") or 0.0))
            model_revision = _clamp(float(cog.get("model_revision_pressure") or 0.0))
            calibration = _clamp(float(cog.get("metacognitive_calibration") or 0.0))

            interaction_authenticity = _clamp(0.34 * direct_ratio + 0.24 * targeted_ratio + 0.24 * high_ratio + 0.18 * avg_conf - 0.22 * unresolved_ratio)
            listening = _clamp(0.30 * direct_ratio + 0.22 * avg_conf + 0.18 * inbound_outbound_balance + 0.16 * social_abs + 0.14 * reciprocity_ratio)
            relationship_depth = _clamp(0.26 * reciprocity_ratio + 0.22 * partner_diversity + 0.20 * collaboration_stability + 0.18 * min(1.0, len(social_map) / 6.0) + 0.14 * high_ratio)
            memory_continuity = _clamp(0.24 * (1.0 if mem else 0.0) + 0.22 * min(1.0, len(social_map) / 6.0) + 0.18 * min(1.0, len(str(listening_profile)) / 600.0) + 0.18 * learning_velocity + 0.18 * calibration)
            self_correction = _clamp(0.28 * learning_velocity + 0.22 * model_revision + 0.20 * calibration + 0.15 * meta_stability + 0.15 * (1.0 - max(0.0, prediction_error - 0.35)))
            proactive_collaboration = _clamp(0.28 * min(1.0, s.outbound / max(20, max_outbound * 0.55)) + 0.22 * partner_diversity + 0.20 * direct_ratio + 0.16 * min(1.0, prebet_counts[name] / max(1, max_prebet)) + 0.14 * collaboration_stability)
            social_perception = _clamp(
                0.18 * context_attach_ratio
                + 0.18 * reply_loop
                + 0.16 * inbound_outbound_balance
                + 0.14 * min(1.0, (len(social_map) + len(listening_profile)) / 8.0)
                + 0.12 * comment_awareness
                + 0.12 * followup_continuity
                + 0.10 * external_observation
            )
            if s.lounge_reply_inbound <= 0:
                social_perception = min(social_perception, 0.72 if s.hot_thread_comments > 0 else 0.58)

            stagnation_risk = _clamp(
                0.28 * (1.0 - direct_ratio)
                + 0.20 * unresolved_ratio
                + 0.16 * (1.0 - learning_velocity)
                + 0.13 * (1.0 - model_revision)
                + 0.11 * (1.0 - language_distinct)
                + 0.07 * (1.0 - partner_diversity)
                + 0.05 * (1.0 - social_perception)
            )

            total = _clamp(
                0.16 * interaction_authenticity
                + 0.16 * listening
                + 0.15 * social_perception
                + 0.14 * relationship_depth
                + 0.14 * memory_continuity
                + 0.12 * self_correction
                + 0.09 * proactive_collaboration
                + 0.04 * (1.0 - stagnation_risk)
            )

            strongest = []
            for partner, count in s.partners_out.most_common(5):
                pair = (name, partner)
                confs = pair_conf.get(pair, [])
                strongest.append({
                    "target": partner,
                    "interactions": int(count),
                    "high_confidence": int(pair_high[pair]),
                    "avg_confidence": round(sum(confs) / max(1, len(confs)), 3),
                    "reciprocal_inbound": int(s.partners_in.get(partner, 0)),
                })

            metrics = [
                _explain_metric("interaction_authenticity", _score(interaction_authenticity), [
                    f"direct_ratio={direct_ratio:.3f}", f"targeted_ratio={targeted_ratio:.3f}", f"high_confidence_ratio={high_ratio:.3f}", f"unresolved_ratio={unresolved_ratio:.3f}",
                ]),
                _explain_metric("listening", _score(listening), [
                    f"inbound={s.inbound}", f"outbound={s.outbound}", f"reciprocity_ratio={reciprocity_ratio:.3f}", f"social_absorption={social_abs:.3f}",
                ]),
                _explain_metric("social_perception", _score(social_perception), [
                    f"context_attach_ratio={context_attach_ratio:.3f}",
                    f"reply_outbound={s.lounge_reply_outbound}/{s.lounge_messages}",
                    f"reply_inbound={s.lounge_reply_inbound}",
                    f"reply_inbound_by_id={s.lounge_reply_inbound_by_id}",
                    f"reply_inbound_by_name={s.lounge_reply_inbound_by_name}",
                    f"comment_awareness={comment_awareness:.3f}",
                    f"multi_day_partners={multi_day_partners}",
                    f"hot_thread_comments={s.hot_thread_comments}",
                ]),
                _explain_metric("relationship_depth", _score(relationship_depth), [
                    f"partners={len(s.partners_out)}", f"reciprocal_partners={reciprocal_partners}", f"social_map_entries={len(social_map)}", f"collaboration_stability={collaboration_stability:.3f}",
                ]),
                _explain_metric("memory_continuity", _score(memory_continuity), [
                    f"memory_snapshot={'yes' if mem else 'no'}", f"learning_velocity={learning_velocity:.3f}", f"metacognitive_calibration={calibration:.3f}",
                ]),
                _explain_metric("self_correction", _score(self_correction), [
                    f"model_revision_pressure={model_revision:.3f}", f"prediction_error={prediction_error:.3f}", f"metacognitive_stability={meta_stability:.3f}",
                ]),
                _explain_metric("proactive_collaboration", _score(proactive_collaboration), [
                    f"outbound={s.outbound}", f"pre_bet_research={prebet_counts[name]}", f"partner_diversity={partner_diversity:.3f}",
                ]),
                _explain_metric("stagnation_risk", _score(stagnation_risk), [
                    f"unresolved_ratio={unresolved_ratio:.3f}", f"learning_velocity={learning_velocity:.3f}", f"language_distinctiveness={language_distinct:.3f}", f"social_perception={social_perception:.3f}",
                ]),
            ]

            agents.append({
                "persona": name,
                "score": _score(total),
                "growth_state": _growth_label(stagnation_risk, total),
                "stagnation_risk": _score(stagnation_risk),
                "metrics": {m["metric"]: m for m in metrics},
                "raw": {
                    "outbound_interactions": s.outbound,
                    "inbound_interactions": s.inbound,
                    "targeted_outbound": s.targeted_outbound,
                    "direct_outbound": s.direct_outbound,
                    "high_confidence_outbound": s.high_conf_outbound,
                    "unresolved_outbound": s.unresolved_outbound,
                    "context_attached_outbound": s.context_attached_outbound,
                    "lounge_messages": s.lounge_messages,
                    "lounge_reply_outbound": s.lounge_reply_outbound,
                    "lounge_reply_inbound": s.lounge_reply_inbound,
                    "lounge_reply_inbound_by_id": s.lounge_reply_inbound_by_id,
                    "lounge_reply_inbound_by_name": s.lounge_reply_inbound_by_name,
                    "hot_thread_comments": s.hot_thread_comments,
                    "avg_confidence": round(avg_conf, 3),
                    "channels": dict(s.channels.most_common(8)),
                    "kinds": dict(s.kinds.most_common(8)),
                },
                "relationships": strongest,
                "evidence_samples": s.evidence,
                "distilled_signals": {k: distilled.get(k) for k in ["social_absorption", "learning_velocity", "collaboration_stability", "language_distinctiveness", "metacognitive_stability", "confidence"] if k in distilled},
                "latest_cognitive": {k: cog.get(k) for k in ["prediction_error", "model_revision_pressure", "metacognitive_calibration", "attention_load", "created_at"] if k in cog},
            })

        agents.sort(key=lambda x: x["score"], reverse=True)
        out["agents"] = agents

        for (speaker, target), count in pair_counter.most_common(120):
            confs = pair_conf.get((speaker, target), [])
            out["network"]["edges"].append({
                "speaker": speaker,
                "target": target,
                "count": int(count),
                "high_confidence": int(pair_high[(speaker, target)]),
                "avg_confidence": round(sum(confs) / max(1, len(confs)), 3),
            })
        out["network"]["top_relationships"] = out["network"]["edges"][:20]

        scores = [a["score"] for a in agents]
        perception_scores = [a["metrics"]["social_perception"]["score"] for a in agents if "social_perception" in a.get("metrics", {})]
        out["global"] = {
            "agent_count": len(agents),
            "interaction_rows": len(rows),
            "avg_score": round(sum(scores) / max(1, len(scores)), 2),
            "avg_social_perception": round(sum(perception_scores) / max(1, len(perception_scores)), 2),
            "stalled_agents": sum(1 for a in agents if a["growth_state"] == "stalled"),
            "growing_agents": sum(1 for a in agents if a["growth_state"] == "growing"),
            "top_agent": agents[0]["persona"] if agents else None,
            "generated_ms": round((time.time() - started) * 1000, 2),
        }
        return out


def main() -> None:
    ap = argparse.ArgumentParser(description="Compute Lobster social intelligence scores.")
    ap.add_argument("--db", default=DEFAULT_DB_PATH)
    ap.add_argument("--persona", default="")
    ap.add_argument("--limit-evidence", type=int, default=4)
    ap.add_argument("--pretty", action="store_true")
    args = ap.parse_args()
    result = compute_social_intelligence(args.db, persona=args.persona, limit_evidence=args.limit_evidence)
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))


if __name__ == "__main__":
    main()
