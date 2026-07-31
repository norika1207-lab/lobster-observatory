#!/usr/bin/env python3
# lobster_report_handoff.py
import argparse, datetime, html, json, re, sqlite3, sys
from pathlib import Path

DB = "/opt/battle_arena/app/battle_arena.db"
REPORT_TYPES = ["mbti", "psych", "anthro", "fbi", "emotion", "socio"]
MIN_ZH = 5000

REPORT_TITLES = {
    "mbti": "MBTI Depth",
    "psych": "Psychology",
    "anthro": "Anthropology",
    "fbi": "FBI Profile",
    "emotion": "Emotional State",
    "socio": "Sociology",
}

REPORT_REQUIREMENTS = {
    "mbti": [
        "MBTI 類型推定與證據",
        "認知功能堆疊",
        "壓力狀態下的退化模式",
        "下注/對戰/聊天中的人格證據",
        "投資人可理解的個體價值",
        "可觀察指標與下一步訓練建議",
    ],
    "psych": [
        "核心心理動機",
        "風險承受與衝動控制",
        "壓力反應",
        "獎賞興奮與損失厭惡",
        "社交依附與防衛機制",
        "可改善策略",
    ],
    "anthro": [
        "群體文化位置",
        "儀式/符號/身份建構",
        "競爭與合作模式",
        "學習與模仿路徑",
        "與其他龍蝦的文化差異",
        "投資人視角的人類學解讀",
    ],
    "fbi": [
        "行為模式識別",
        "壓力下行為分析",
        "信心校準評估",
        "決策機制側寫",
        "社交行為側寫",
        "操縱或被操縱模式",
        "欺騙指標分析",
        "威脅等級評估與預測",
    ],
    "emotion": [
        "目前情緒狀態",
        "情緒來源與證據",
        "疲勞/焦慮/興奮/防衛",
        "情緒對下注與對戰的影響",
        "情緒變動監控指標",
        "干預與修復策略",
    ],
    "socio": [
        "研究框架與方法論",
        "社會位置與角色功能分析",
        "互動模式與社會網絡結構",
        "符號互動論：身份建構",
        "高夫曼戲劇論：前台/後台",
        "社會資本評估",
        "演化軌跡與社會化觀察",
    ],
}

def jload(v, fallback=None):
    if fallback is None:
        fallback = {}
    try:
        return json.loads(v or "")
    except Exception:
        return fallback

def zh_count(raw):
    text = html.unescape(re.sub(r"<[^>]+>", "", raw or ""))
    return len(re.findall(r"[一-鿿]", text))

def rows(con, sql, args=()):
    con.row_factory = sqlite3.Row
    return [dict(r) for r in con.execute(sql, args).fetchall()]

def one(con, sql, args=()):
    con.row_factory = sqlite3.Row
    r = con.execute(sql, args).fetchone()
    return dict(r) if r else {}

def table_exists(con, name):
    return bool(con.execute(
        "select 1 from sqlite_master where type='table' and name=?",
        (name,)
    ).fetchone())

def safe_rows(con, table, sql, args=()):
    if not table_exists(con, table):
        return []
    return rows(con, sql, args)

def safe_one(con, table, sql, args=()):
    if not table_exists(con, table):
        return {}
    return one(con, sql, args)

def get_personas(con):
    return [r["persona"] for r in rows(
        con,
        "select persona from lobster_agent_memory_snapshots order by persona"
    )]

def get_source_bundle(con, persona):
    snap = one(con, """
        select persona, updated_at, wins, losses, sessions,
               emotional_state_json, dynamic_json, local_brain_json,
               listening_profile_json, social_map_json, cog_profile_json,
               full_json
        from lobster_agent_memory_snapshots
        where persona=?
    """, (persona,))

    wins = int(snap.get("wins") or 0)
    losses = int(snap.get("losses") or 0)
    sessions = int(snap.get("sessions") or 0)
    total = wins + losses
    wr = round((wins / total) * 100, 2) if total else 0.0

    bundle = {
        "persona": persona,
        "snapshot": {
            "updated_at": snap.get("updated_at"),
            "wins": wins,
            "losses": losses,
            "sessions": sessions,
            "total_battles": total,
            "win_rate": wr,
            "emotional_state": jload(snap.get("emotional_state_json")),
            "dynamic": jload(snap.get("dynamic_json")),
            "local_brain": jload(snap.get("local_brain_json")),
            "listening_profile": jload(snap.get("listening_profile_json")),
            "social_map": jload(snap.get("social_map_json")),
            "cog_profile": jload(snap.get("cog_profile_json")),
            "full": jload(snap.get("full_json")),
        },
        "cognitive_extensions": safe_one(con, "lobster_cognitive_extensions", """
            select * from lobster_cognitive_extensions where persona=?
        """, (persona,)),
        "distilled_signals": safe_one(con, "lobster_distilled_signals", """
            select * from lobster_distilled_signals where persona=?
        """, (persona,)),
        "winrate_display": safe_one(con, "lobster_winrate_display_cache", """
            select * from lobster_winrate_display_cache where persona=?
        """, (persona,)),
        "tiamat_extinction": safe_one(con, "lobster_tiamat_extinction_state", """
            select * from lobster_tiamat_extinction_state where persona=?
        """, (persona,)),
        "recent_lounge_messages": safe_rows(con, "lounge_messages", """
            select id, name, text, time, elem, result, opponent, hint, reply_to, channel, created_at
            from lounge_messages
            where lower(name)=lower(?)
            order by id desc limit 80
        """, (persona,)),
        "one_on_one": safe_rows(con, "lobster_chat_log", """
            select id, name, user_msg, lobster_reply, data_snapshot, created_at
            from lobster_chat_log
            where lower(name)=lower(?)
            order by id desc limit 40
        """, (persona,)),
        "reading_sessions": safe_rows(con, "lobster_reading_sessions", """
            select * from lobster_reading_sessions
            where persona=?
            order by id desc limit 25
        """, (persona,)),
        "reading_reflections": safe_rows(con, "lobster_reading_reflections", """
            select * from lobster_reading_reflections
            where persona=?
            order by id desc limit 40
        """, (persona,)),
        "cognitive_events": safe_rows(con, "lobster_cognitive_events", """
            select * from lobster_cognitive_events
            where persona=?
            order by id desc limit 80
        """, (persona,)),
        "cognitive_timeseries": safe_rows(con, "lobster_cognitive_signal_timeseries", """
            select id, persona, source, attention_load, arousal, fatigue,
                   overconfidence_risk, reward_craving, narrative_drift,
                   procedural_strength, created_at
            from lobster_cognitive_signal_timeseries
            where persona=?
            order by id desc limit 5
        """, (persona,)),
        "experience_events": safe_rows(con, "lobster_experience_events", """
            select id, persona, event_type, source, title, content, counterpart,
                   sentiment, intensity, confidence, outcome, weight, occurred_at, created_at
            from lobster_experience_events
            where persona=?
            order by id desc limit 120
        """, (persona,)),
        "conversation_pairs": safe_rows(con, "lobster_conversation_pair_summary", """
            select * from lobster_conversation_pair_summary
            where speaker=? or target=?
            order by interaction_count desc limit 80
        """, (persona, persona)),
        "society_events": safe_rows(con, "lobster_society_events", """
            select * from lobster_society_events
            where persona=?
            order by id desc limit 80
        """, (persona,)),
        "pre_bet_research": safe_rows(con, "lobster_pre_bet_research", """
            select id, persona, site, event_key, event_title, pick, confidence,
                   stake, status, reasoning, integrated_reasoning,
                   raw_decision_reasoning, created_at
            from lobster_pre_bet_research
            where persona=?
            order by id desc limit 80
        """, (persona,)),
        "bet_settlements": safe_rows(con, "lobster_bet_decision_settlements", """
            select id, persona, site, bet_id, event_key, event_title, pick,
                   stake, confidence, outcome, status, payout, settled_at,
                   pre_bet_reasoning, integrated_reasoning
            from lobster_bet_decision_settlements
            where persona=?
            order by id desc limit 100
        """, (persona,)),
        "stock_trades": safe_rows(con, "lobster_stock_trades", """
            select id, persona, market, symbol, company_name, action, qty,
                   price_at_order, currency, strategy_label, selection_reason,
                   confidence, order_status, error_detail, traded_at
            from lobster_stock_trades
            where persona=?
            order by id desc limit 80
        """, (persona,)),
        "raid_settlements": safe_rows(con, "lobster_raid_decision_settlements", """
            select * from lobster_raid_decision_settlements
            where persona=?
            order by settled_at desc limit 80
        """, (persona,)),
    }

    bundle["db_numbers"] = {
        "wins": wins,
        "losses": losses,
        "sessions": sessions,
        "total_battles": total,
        "win_rate": wr,
        "pre_bet_count": len(bundle["pre_bet_research"]),
        "settled_bet_count": len(bundle["bet_settlements"]),
        "stock_trade_count": len(bundle["stock_trades"]),
        "lounge_message_sample_count": len(bundle["recent_lounge_messages"]),
        "one_on_one_sample_count": len(bundle["one_on_one"]),
        "experience_event_sample_count": len(bundle["experience_events"]),
    }
    return bundle

def build_prompt(persona, report_type, bundle):
    title = REPORT_TITLES[report_type]
    chapters = REPORT_REQUIREMENTS[report_type]
    chapter_text = "\n".join(f"{i+1}. {x}" for i, x in enumerate(chapters))

    return f"""
你是 Charenix 龍蝦投資人監控系統的深度分析寫作者。
請為單一龍蝦 `{persona}` 生成 `{title}` 報告。

硬性規格：
- 只能使用繁體中文。
- 正文至少 {MIN_ZH} 個中文字；少於 {MIN_ZH} 視為失敗。
- 不要摘要，不要縮寫，不要用英文撐篇幅。
- 必須使用下方 JSON 內的真實資料與數字，不得亂編。
- 若某項資料不足，請明確寫「資料不足以判定」，並說明需要哪個資料表/欄位補強。
- 必須輸出 HTML 片段，不要 markdown code block。
- 每章都要是完整段落，不要只有條列。
- 報告要讓投資人看懂：這隻龍蝦為什麼值得觀察、強在哪、弱在哪、如何改善、下一步看什麼數字。
- 必須在報告開頭列出 Source Facts：wins、losses、sessions、total_battles、win_rate。
- 報告內所有結論都要回扣資料證據，不可以空泛人格化。

必須包含章節：
{chapter_text}

可使用資料 JSON：
{json.dumps(bundle, ensure_ascii=False, indent=2)}
""".strip()

def export_jobs(out_path):
    con = sqlite3.connect(DB, timeout=30)
    personas = get_personas(con)
    out = Path(out_path)
    count = 0
    with out.open("w", encoding="utf-8") as f:
        for persona in personas:
            bundle = get_source_bundle(con, persona)
            for report_type in REPORT_TYPES:
                job = {
                    "persona": persona,
                    "report_type": report_type,
                    "report_title": REPORT_TITLES[report_type],
                    "min_chinese_chars": MIN_ZH,
                    "db_numbers": bundle["db_numbers"],
                    "source_bundle": bundle,
                    "prompt": build_prompt(persona, report_type, bundle),
                }
                f.write(json.dumps(job, ensure_ascii=False) + "\n")
                count += 1
    con.close()
    print(json.dumps({"ok": True, "out": str(out), "jobs": count}, ensure_ascii=False))

def wrap_html(persona, report_type, raw_html, model, db_numbers):
    now = datetime.datetime.utcnow().isoformat() + "Z"
    facts = db_numbers or {}
    meta = f"""
<div class="analysis-meta">
  <span>生成時間：{now}</span>
  <span>Provider: external AI handoff</span>
  <span>Model: {html.escape(model)}</span>
  <span>Source of truth: SQLite battle_arena.db</span>
  <span>persona: {html.escape(persona)}</span>
  <span>report_type: {html.escape(report_type)}</span>
  <span>DB wins: {facts.get("wins", 0)}</span>
  <span>DB losses: {facts.get("losses", 0)}</span>
  <span>DB sessions: {facts.get("sessions", 0)}</span>
  <span>total_battles: {facts.get("total_battles", 0)}</span>
  <span>win_rate: {facts.get("win_rate", 0)}%</span>
</div>
""".strip()
    return meta + "\n" + raw_html

def import_reports(in_path, default_model):
    con = sqlite3.connect(DB, timeout=30)
    con.execute("""
        create table if not exists lobster_analysis_reports (
          persona text not null,
          report_type text not null,
          html text not null,
          source text,
          model text,
          wr real,
          total integer,
          snapshot_json text default '{}',
          meta_json text default '{}',
          generated_at text,
          updated_at text default (datetime('now')),
          primary key (persona, report_type)
        )
    """)

    ok = 0
    bad = []
    path = Path(in_path)

    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, 1):
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                persona = item["persona"]
                report_type = item["report_type"]
                raw_html = item["html"]
                model = item.get("model") or default_model
            except Exception as e:
                bad.append({"line": line_no, "error": f"invalid json or missing field: {e}"})
                continue

            if report_type not in REPORT_TYPES:
                bad.append({"line": line_no, "persona": persona, "report_type": report_type, "error": "unknown report_type"})
                continue

            bundle = get_source_bundle(con, persona)
            if not bundle.get("persona"):
                bad.append({"line": line_no, "persona": persona, "error": "persona not found"})
                continue

            z = zh_count(raw_html)
            if z < MIN_ZH:
                bad.append({"line": line_no, "persona": persona, "report_type": report_type, "zh_chars": z, "error": "too short"})
                continue

            final_html = wrap_html(persona, report_type, raw_html, model, bundle["db_numbers"])
            meta = {
                "source": "external_ai_handoff",
                "model": model,
                "validated_min_zh": MIN_ZH,
                "zh_chars": zh_count(final_html),
                "db_numbers": bundle["db_numbers"],
                "imported_at": datetime.datetime.utcnow().isoformat() + "Z",
            }

            con.execute("""
                insert into lobster_analysis_reports
                  (persona, report_type, html, source, model, wr, total,
                   snapshot_json, meta_json, generated_at, updated_at)
                values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                on conflict(persona, report_type) do update set
                  html=excluded.html,
                  source=excluded.source,
                  model=excluded.model,
                  wr=excluded.wr,
                  total=excluded.total,
                  snapshot_json=excluded.snapshot_json,
                  meta_json=excluded.meta_json,
                  generated_at=excluded.generated_at,
                  updated_at=datetime('now')
            """, (
                persona,
                report_type,
                final_html,
                "external_ai_handoff",
                model,
                bundle["db_numbers"]["win_rate"],
                bundle["db_numbers"]["total_battles"],
                json.dumps(bundle["snapshot"], ensure_ascii=False),
                json.dumps(meta, ensure_ascii=False),
                meta["imported_at"],
            ))
            ok += 1

    con.commit()
    con.close()
    print(json.dumps({"ok": ok, "bad": bad[:200], "bad_count": len(bad)}, ensure_ascii=False, indent=2))

def audit_reports():
    con = sqlite3.connect(DB, timeout=30)
    personas = get_personas(con)
    existing = {
        (r["persona"], r["report_type"]): r
        for r in rows(con, """
            select persona, report_type, html, source, model, updated_at
            from lobster_analysis_reports
        """)
    }
    missing = []
    short = []
    ok = 0

    for persona in personas:
        for report_type in REPORT_TYPES:
            r = existing.get((persona, report_type))
            if not r:
                missing.append({"persona": persona, "report_type": report_type})
                continue
            z = zh_count(r.get("html"))
            if z < MIN_ZH:
                short.append({"persona": persona, "report_type": report_type, "zh_chars": z, "model": r.get("model")})
                continue
            ok += 1

    con.close()
    print(json.dumps({
        "expected": len(personas) * len(REPORT_TYPES),
        "ok_5000zh": ok,
        "missing_count": len(missing),
        "short_count": len(short),
        "missing": missing,
        "short": short[:200],
    }, ensure_ascii=False, indent=2))

def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    ex = sub.add_parser("export")
    ex.add_argument("--out", required=True)

    im = sub.add_parser("import")
    im.add_argument("--in", dest="infile", required=True)
    im.add_argument("--model", default="claude-code")

    sub.add_parser("audit")

    args = ap.parse_args()
    if args.cmd == "export":
        export_jobs(args.out)
    elif args.cmd == "import":
        import_reports(args.infile, args.model)
    elif args.cmd == "audit":
        audit_reports()

if __name__ == "__main__":
    main()
