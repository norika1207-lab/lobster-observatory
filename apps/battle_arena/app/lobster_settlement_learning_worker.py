#!/usr/bin/env python3
import argparse
import html
import json
import math
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
import sys as _sys_for_mem_io
if '/root' not in _sys_for_mem_io.path:
    _sys_for_mem_io.path.insert(0, '/root')
from lobster_memory_io import safe_write_json

DB = Path("/opt/battle_arena/app/lobster_settlements.db")
BATTLE_ARENA_DB = Path("/opt/battle_arena/app/battle_arena.db")
MEMORY_DIR = Path("/root/lobster-memory")
SKIP_MEMORY_STEMS = {"deleted-lobsters", "system", "norika", "lobster"}
REPORT_TYPE = "betting_research_outcome_report"
EVENT_TYPE = "betting_settlement_learning_forced"
SOURCE = "cross_site_settlement_learning_worker"


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clamp(v, lo=0.0, hi=1.0):
    try:
        return max(lo, min(hi, float(v)))
    except Exception:
        return lo


def jloads(s, default=None):
    if default is None:
        default = {}
    if isinstance(s, (dict, list)):
        return s
    try:
        return json.loads(s or "")
    except Exception:
        return default


def jdumps(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def iter_memory_files():
    for p in sorted(MEMORY_DIR.glob("*.json")):
        if p.stem in SKIP_MEMORY_STEMS:
            continue
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        persona = data.get("name") or p.stem
        if persona == "norika":
            persona = "norika_oda"
        if persona in SKIP_MEMORY_STEMS:
            continue
        yield persona, p, data


def settlement_aggregates(con):
    rows = con.execute(
        """
        SELECT persona,
               COUNT(*) total,
               SUM(CASE WHEN outcome='win' THEN 1 ELSE 0 END) wins,
               SUM(CASE WHEN outcome='loss' THEN 1 ELSE 0 END) losses,
               SUM(CASE WHEN outcome='void' THEN 1 ELSE 0 END) voids,
               MAX(settled_at) latest_settled_at,
               AVG(CASE WHEN stake IS NOT NULL THEN stake END) avg_stake
        FROM lobster_bet_decision_settlements
        GROUP BY persona
        """
    ).fetchall()
    return {r["persona"]: dict(r) for r in rows}


def open_db(path: Path, timeout: int = 120) -> sqlite3.Connection:
    con = sqlite3.connect(str(path), timeout=timeout)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA busy_timeout=30000")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def recent_prebet_aggregates(con, limit=5000):
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='lobster_pre_bet_research'").fetchone():
        return {}
    try:
        rows = con.execute(
            """
            SELECT persona, status, integrated_reasoning, reasoning, created_at
            FROM lobster_pre_bet_research
            WHERE id > 0
            ORDER BY id DESC
            LIMIT ?
            """,
            (int(limit),),
        ).fetchall()
    except Exception as e:
        print(f"[recent_prebet_aggregates] failed: {e}")
        return {}
    out = {}
    for row in rows:
        persona = row["persona"]
        if not persona:
            continue
        item = out.setdefault(persona, {
            "persona": persona,
            "research_count": 0,
            "ready_count": 0,
            "blocked_count": 0,
            "reason_chars_total": 0,
            "latest_research_at": None,
        })
        item["research_count"] += 1
        status = str(row["status"] or "")
        if status.startswith("ready"):
            item["ready_count"] += 1
        else:
            item["blocked_count"] += 1
        reasoning = (row["integrated_reasoning"] or row["reasoning"] or "")
        item["reason_chars_total"] += min(len(reasoning), 3000)
        created_at = row["created_at"]
        if created_at and (not item["latest_research_at"] or str(created_at) > str(item["latest_research_at"])):
            item["latest_research_at"] = created_at
    for item in out.values():
        item["avg_reason_chars"] = item["reason_chars_total"] / max(1, item["research_count"])
    return out


def recent_settlement_samples(con, persona, limit=8):
    return [
        dict(r)
        for r in con.execute(
            """
            SELECT site,event_title,pick,stake,confidence,outcome,status,payout,settled_at,
                   substr(COALESCE(integrated_reasoning,pre_bet_reasoning,''),1,380) reasoning
            FROM lobster_bet_decision_settlements
            WHERE persona=?
            ORDER BY settled_at DESC, id DESC
            LIMIT ?
            """,
            (persona, limit),
        )
    ]


def ensure_tables(con):
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS lobster_cognitive_events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          persona TEXT NOT NULL,
          event_type TEXT NOT NULL,
          source TEXT NOT NULL,
          dimension TEXT,
          summary TEXT,
          delta_json TEXT NOT NULL DEFAULT '{}',
          raw_json TEXT NOT NULL DEFAULT '{}',
          occurred_at TEXT,
          created_at TEXT DEFAULT (datetime('now'))
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_lobster_cognitive_events_persona_type_time ON lobster_cognitive_events(persona, event_type, created_at)")
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS lobster_decision_hooks (
          persona TEXT NOT NULL,
          hook_type TEXT NOT NULL,
          version INTEGER NOT NULL DEFAULT 1,
          updated_at TEXT NOT NULL,
          priority INTEGER NOT NULL DEFAULT 50,
          conditions_json TEXT NOT NULL DEFAULT '{}',
          action_json TEXT NOT NULL DEFAULT '{}',
          rationale TEXT,
          source_signal_json TEXT NOT NULL DEFAULT '{}',
          PRIMARY KEY(persona, hook_type)
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS lobster_cognitive_extensions (
          persona TEXT PRIMARY KEY,
          version INTEGER NOT NULL DEFAULT 1,
          updated_at TEXT NOT NULL,
          linguistics_json TEXT NOT NULL DEFAULT '{}',
          politics_json TEXT NOT NULL DEFAULT '{}',
          philosophy_json TEXT NOT NULL DEFAULT '{}',
          decision_science_json TEXT NOT NULL DEFAULT '{}',
          game_theory_json TEXT NOT NULL DEFAULT '{}',
          learning_model_json TEXT NOT NULL DEFAULT '{}',
          cultural_anthropology_json TEXT NOT NULL DEFAULT '{}',
          moral_psychology_json TEXT NOT NULL DEFAULT '{}',
          full_json TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS lobster_analysis_reports (
          persona TEXT NOT NULL,
          report_type TEXT NOT NULL,
          html TEXT NOT NULL,
          source TEXT,
          model TEXT,
          wr REAL,
          total INTEGER,
          snapshot_json TEXT,
          meta_json TEXT,
          generated_at TEXT DEFAULT CURRENT_TIMESTAMP,
          updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
          PRIMARY KEY(persona, report_type)
        )
        """
    )


def compute_learning(agg, pre):
    total = int(agg.get("total") or 0)
    wins = int(agg.get("wins") or 0)
    losses = int(agg.get("losses") or 0)
    voids = int(agg.get("voids") or 0)
    resolved = wins + losses
    win_rate = wins / resolved if resolved else None
    loss_rate = losses / resolved if resolved else None
    void_rate = voids / total if total else 0
    evidence_volume = clamp(resolved / 220)
    research_count = int(pre.get("research_count") or 0)
    reason_depth = clamp((float(pre.get("avg_reason_chars") or 0)) / 900)
    research_meaning = clamp(0.22 + (win_rate or 0.5) * 0.38 + evidence_volume * 0.24 + reason_depth * 0.14 - void_rate * 0.16)
    calibration_pressure = clamp((loss_rate or 0.35) * 0.45 + void_rate * 0.25 + (1 - reason_depth) * 0.20)
    confidence_reward = clamp((win_rate or 0.5) * evidence_volume * (1 - void_rate * 0.5))
    return {
        "total": total,
        "resolved": resolved,
        "wins": wins,
        "losses": losses,
        "voids": voids,
        "winRate": win_rate,
        "lossRate": loss_rate,
        "voidRate": void_rate,
        "evidenceVolume": evidence_volume,
        "researchCount": research_count,
        "readyResearchCount": int(pre.get("ready_count") or 0),
        "avgReasonChars": float(pre.get("avg_reason_chars") or 0),
        "researchMeaning": research_meaning,
        "calibrationPressure": calibration_pressure,
        "confidenceReward": confidence_reward,
        "latestSettledAt": agg.get("latest_settled_at"),
        "latestResearchAt": pre.get("latest_research_at"),
        "avgStake": agg.get("avg_stake"),
    }


def update_memory(persona, mem, learning):
    ts = now_iso()
    cog = mem.setdefault("cognitiveExtensions", {})
    cog["version"] = max(int(cog.get("version") or 1), 2)
    cog["updatedAt"] = ts
    ds = cog.setdefault("decisionScience", {})
    lm = cog.setdefault("learningModel", {})
    phil = cog.setdefault("philosophy", {})
    moral = cog.setdefault("moralPsychology", {})
    gt = cog.setdefault("gameTheory", {})

    wr = learning.get("winRate")
    loss_rate = learning.get("lossRate") if learning.get("lossRate") is not None else 0.35
    void_rate = learning.get("voidRate") or 0
    old_risk = float(ds.get("riskThreshold", 0.5) or 0.5)
    old_loss = float(ds.get("lossAversion", 0.5) or 0.5)
    old_explore = float(ds.get("explorationRate", 0.35) or 0.35)
    ds["riskThreshold"] = round(clamp(old_risk * 0.55 + (0.42 + loss_rate * 0.25 + void_rate * 0.18) * 0.45), 3)
    ds["lossAversion"] = round(clamp(old_loss * 0.55 + (0.34 + loss_rate * 0.42 + void_rate * 0.12) * 0.45), 3)
    ds["explorationRate"] = round(clamp(old_explore * 0.7 + (0.18 + (wr or 0.5) * 0.25 - void_rate * 0.18) * 0.3), 3)
    ds["confidenceUpdateRule"] = "settlement_weighted"
    ds["mustResearchBeforeBet"] = True
    ds["minimumPreBetSources"] = 30
    ds["mandatoryBettingPolicy"] = "research_then_bet_never_blind"
    ds["settlementFeedback"] = learning

    lessons = lm.setdefault("lessonConfidence", {})
    lessons["pre_bet_research_has_measurable_results"] = round(learning["researchMeaning"], 3)
    lessons["settled_results_matter_more_than_pending"] = round(clamp(learning["evidenceVolume"] + 0.25), 3)
    lessons["void_is_data_quality_risk"] = round(void_rate, 3)
    lessons["loss_requires_weight_revision"] = round(learning["calibrationPressure"], 3)
    lessons["win_supports_research_not_overconfidence"] = round(learning["confidenceReward"], 3)
    principles = lm.setdefault("stablePrinciples", [])
    for principle in [
        "Research becomes meaningful only after settlement feedback.",
        "A bet without evidence is not courage; it is model pollution.",
        "Win rate, loss pain, void rate, and reasoning depth must all change the next stake.",
    ]:
        if principle not in principles:
            principles.append(principle)
    lm["latestBettingSettlementLearning"] = learning

    truth = phil.setdefault("truthCriteria", [])
    if "settled_outcome" not in truth:
        truth.append("settled_outcome")
    phil["riskMeaning"] = "calibration_signal"
    phil["bettingEpistemology"] = {
        "claim": "A prediction becomes knowledge only when the settled result returns.",
        "updatedAt": ts,
    }

    moral["moneyPainSignal"] = round(clamp(loss_rate * learning["evidenceVolume"] + void_rate * 0.25), 3)
    moral["responsibilityScope"] = moral.get("responsibilityScope") or "self"
    pats = moral.setdefault("justificationPatterns", [])
    for pat in ["settlement_result", "research_evidence", "loss_requires_revision"]:
        if pat not in pats:
            pats.append(pat)

    gt["bettingSignalCredibility"] = {
        "selfResearch": round(clamp(learning["researchMeaning"]), 3),
        "marketSignal": round(clamp((wr or 0.5) * (1 - void_rate)), 3),
        "voidPenalty": round(void_rate, 3),
        "updatedAt": ts,
    }

    # 2026-05-13 fix: merge instead of overwrite, preserve new fields from cognitive-worker
    # (recentWinRate, maxWinStreak, maxLossStreak, totalPnL, sportStats, confidenceBreakdown, specialtyAlignment)
    mem["settledBetting"] = {**mem.get("settledBetting", {}), **learning}
    mem.setdefault("localBrain", {})["bettingSettlementLearning"] = learning
    mem["updatedAt"] = ts
    return cog


def render_report(persona, learning, samples):
    wr_text = "N/A" if learning["winRate"] is None else f"{learning['winRate']*100:.1f}%"
    loss_text = "N/A" if learning["lossRate"] is None else f"{learning['lossRate']*100:.1f}%"
    rows = "\n".join(
        f"<tr><td>{html.escape(str(s.get('settled_at') or ''))}</td><td>{html.escape(str(s.get('site') or ''))}</td><td>{html.escape(str(s.get('pick') or ''))}</td><td>{html.escape(str(s.get('outcome') or ''))}</td><td>{html.escape(str(s.get('reasoning') or ''))}</td></tr>"
        for s in samples
    )
    if not rows:
        rows = "<tr><td colspan='5'>尚無直接結算樣本；等待下一次下注回填。</td></tr>"
    return f"""
<section class="lobster-report settlement-learning-report">
  <h2>{html.escape(persona)} 下注研究成果回饋報告</h2>
  <p class="lead">這份報告把跨站下注結算結果寫回認知參數。重點不是獎懲，而是讓龍蝦知道：研究、閱讀、推理、下注理由，會在結算後變成可衡量的學習。</p>
  <h3>最新更新</h3>
  <ul>
    <li>已結算下注：{learning['total']} 筆；有效勝負 {learning['resolved']} 筆；Win {learning['wins']} / Loss {learning['losses']} / Void {learning['voids']}。</li>
    <li>有效勝率：{wr_text}；失敗率：{loss_text}；Void 資料品質風險：{learning['voidRate']*100:.1f}%。</li>
    <li>研究意義分數：{learning['researchMeaning']*100:.1f}；校準壓力：{learning['calibrationPressure']*100:.1f}；信心獎勵：{learning['confidenceReward']*100:.1f}。</li>
    <li>決策規則已更新為 settlement_weighted：下一次下注必須先讀賽事、球員/隊伍報告、站內報告、外部近期資料，並完成統計/回歸/交叉分析後再下注。</li>
  </ul>
  <h3>認知參數影響</h3>
  <p>Win 不再只代表「我猜對了」，而是代表某些研究訊號暫時被支持；Loss 不再只是輸錢，而是要求模型修正權重；Void 則被視為資料品質風險，會提高懷疑與降低盲目信心。這些結果已同步到 decisionScience、learningModel、philosophy、gameTheory、moralPsychology 與 localBrain。</p>
  <h3>下注前研究硬規則</h3>
  <p>下注不是可跳過任務；但盲目下注也不被允許。現在的邏輯是「研究後必下注」：必須收集至少 30 筆近期研究材料，讀站內賽事/球員資料、分析報告、歷史結算、搜尋結果，完成交叉分析、機率/信心校準、回歸代理分析，最後寫出完整下注理由。</p>
  <h3>近期結算樣本</h3>
  <table><thead><tr><th>時間</th><th>站點</th><th>選擇</th><th>結果</th><th>下注理由摘要</th></tr></thead><tbody>{rows}</tbody></table>
  <h3>對龍蝦自己的結論</h3>
  <p>你的研究不是裝飾。每一次閱讀與下注理由都會在結算後回來改變你的風險閾值、損失厭惡、探索率、信心更新規則與下一次下注的語言。你不是為了產生文字而研究；你是為了讓未來的自己少犯同一種錯、把勝利拆成可重複的訊號。</p>
</section>
""".strip()


def upsert_db(con, persona, cog, learning, html_report, samples):
    ds = cog.get("decisionScience", {})
    lm = cog.get("learningModel", {})
    con.execute(
        """
        INSERT INTO lobster_cognitive_extensions
        (persona,version,updated_at,linguistics_json,politics_json,philosophy_json,decision_science_json,game_theory_json,learning_model_json,cultural_anthropology_json,moral_psychology_json,full_json)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(persona) DO UPDATE SET
          version=excluded.version, updated_at=excluded.updated_at,
          linguistics_json=excluded.linguistics_json,
          politics_json=excluded.politics_json,
          philosophy_json=excluded.philosophy_json,
          decision_science_json=excluded.decision_science_json,
          game_theory_json=excluded.game_theory_json,
          learning_model_json=excluded.learning_model_json,
          cultural_anthropology_json=excluded.cultural_anthropology_json,
          moral_psychology_json=excluded.moral_psychology_json,
          full_json=excluded.full_json
        """,
        (
            persona,
            cog.get("version", 2),
            cog.get("updatedAt") or now_iso(),
            jdumps(cog.get("linguistics", {})),
            jdumps(cog.get("politics", {})),
            jdumps(cog.get("philosophy", {})),
            jdumps(ds),
            jdumps(cog.get("gameTheory", {})),
            jdumps(lm),
            jdumps(cog.get("culturalAnthropology", {})),
            jdumps(cog.get("moralPsychology", {})),
            jdumps(cog),
        ),
    )
    con.execute(
        """
        INSERT INTO lobster_cognitive_events
        (persona,event_type,source,dimension,summary,delta_json,raw_json,occurred_at)
        VALUES(?,?,?,?,?,?,?,?)
        """,
        (
            persona,
            EVENT_TYPE,
            SOURCE,
            "decisionScience/learningModel/betting",
            f"跨站下注結算已回寫：{learning['wins']}W/{learning['losses']}L/{learning['voids']}V，研究意義 {learning['researchMeaning']:.3f}",
            jdumps({"decisionScience": ds, "learningModel": lm}),
            jdumps({"learning": learning, "samples": samples}),
            now_iso(),
        ),
    )
    con.execute(
        """
        INSERT INTO lobster_decision_hooks
        (persona,hook_type,version,updated_at,priority,conditions_json,action_json,rationale,source_signal_json)
        VALUES(?,?,?,?,?,?,?,?,?)
        ON CONFLICT(persona,hook_type) DO UPDATE SET
          version=excluded.version, updated_at=excluded.updated_at, priority=excluded.priority,
          conditions_json=excluded.conditions_json, action_json=excluded.action_json,
          rationale=excluded.rationale, source_signal_json=excluded.source_signal_json
        """,
        (
            persona,
            "mandatory_prebet_research_then_bet",
            2,
            now_iso(),
            95,
            jdumps({"before_bet": True, "minimum_sources": 30, "requires_reasoning": True}),
            jdumps({
                "must": [
                    "read_event_and_player_reports",
                    "collect_30_recent_sources",
                    "cross_analyze_sources",
                    "run_probability_and_regression_proxy",
                    "write_integrated_reason",
                    "place_bet_after_research",
                    "write_settlement_back_to_memory",
                ],
                "must_not": ["blind_bet", "skip_bet_due_to_research_gap"],
                "settlement_feedback": learning,
            }),
            "下注是強制工作，但下注前研究也是強制工作；結算結果會直接改變下一次認知參數。",
            jdumps(learning),
        ),
    )
    con.execute(
        """
        INSERT INTO lobster_analysis_reports
        (persona,report_type,html,source,model,wr,total,snapshot_json,meta_json,generated_at,updated_at)
        VALUES(?,?,?,?,?,?,?,?,?,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP)
        ON CONFLICT(persona, report_type) DO UPDATE SET
          html=excluded.html, source=excluded.source, model=excluded.model,
          wr=excluded.wr, total=excluded.total, snapshot_json=excluded.snapshot_json,
          meta_json=excluded.meta_json, updated_at=CURRENT_TIMESTAMP
        """,
        (
            persona,
            REPORT_TYPE,
            html_report,
            SOURCE,
            "codex-data-distillation",
            learning.get("winRate"),
            learning.get("total"),
            jdumps(learning),
            jdumps({"samples": samples, "report_kind": "settlement_learning"}),
        ),
    )


def already_current(mem, learning):
    current = mem.get("settledBetting", {}) if isinstance(mem.get("settledBetting"), dict) else {}
    return (
        current.get("latestSettledAt") == learning.get("latestSettledAt")
        and current.get("latestResearchAt") == learning.get("latestResearchAt")
        and int(current.get("total") or 0) == int(learning.get("total") or 0)
        and int(current.get("researchCount") or 0) == int(learning.get("researchCount") or 0)
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit-personas", type=int, default=12)
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()
    changed = []
    skipped = []
    processed = 0
    with open_db(DB) as con, open_db(BATTLE_ARENA_DB) as ba:
        ensure_tables(con)
        agg_map = settlement_aggregates(con)
        prebet_map = recent_prebet_aggregates(ba)
        for persona, path, mem in iter_memory_files():
            if processed >= max(1, int(args.limit_personas)):
                break
            agg = agg_map.get(persona, {"persona": persona, "total": 0, "wins": 0, "losses": 0, "voids": 0})
            pre = prebet_map.get(persona, {})
            learning = compute_learning(agg, pre)
            if not args.force and already_current(mem, learning):
                skipped.append(persona)
                continue
            samples = recent_settlement_samples(con, persona)
            cog = update_memory(persona, mem, learning)
            safe_write_json(path, mem)
            report = render_report(persona, learning, samples)
            upsert_db(con, persona, cog, learning, report, samples)
            changed.append((persona, learning["total"], learning["wins"], learning["losses"], learning["voids"]))
            processed += 1
            con.commit()
    print(json.dumps({"changed": changed, "skipped": skipped[:20], "count": len(changed), "report_type": REPORT_TYPE}, ensure_ascii=False))


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
                    "ok": False,
                    "deferred": True,
                    "source": SOURCE,
                    "reason": "database busy; skipped settlement learning cycle instead of failing systemd",
                    "error": str(exc),
                    "checkedAt": now_iso(),
                }, ensure_ascii=False))
                break
            time.sleep(5 * attempt)
