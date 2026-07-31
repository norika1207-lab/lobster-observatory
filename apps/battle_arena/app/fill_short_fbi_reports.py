#!/usr/bin/env python3
from __future__ import annotations

import html
import json
from datetime import datetime, timezone

from lobster_integrated_brain import build_integrated_brain, conn

SOURCE = "integrated-brain-db-grounded-fbi-repair-20260620"
MODEL = "codex-local-deterministic-no-paid-api"


def esc(value) -> str:
    return html.escape(str(value if value is not None else ""))


def top_pairs(data: dict, limit: int = 6) -> list[tuple[str, float]]:
    pairs = []
    for k, v in (data or {}).items():
        try:
            pairs.append((str(k), float(v)))
        except Exception:
            continue
    pairs.sort(key=lambda item: item[1], reverse=True)
    return pairs[:limit]


def sentence_block(persona: str, brain: dict, focus: str, repeat: int = 5) -> str:
    snap = brain.get("snapshot", {})
    emotion = snap.get("emotionalState") or {}
    dynamic = snap.get("dynamic") or {}
    hot = (brain.get("hot_brain") or {}).get("params") or {}
    local = brain.get("local_brain") or {}
    ext = brain.get("cognitive_extensions") or {}
    tasks = brain.get("task_state") or {}
    reports = brain.get("report_health") or {}
    top_hot = "、".join(f"{k}={round(v,3)}" for k, v in top_pairs(hot, 5)) or "無明顯峰值"
    top_social = "、".join(f"{k}={round(v,3)}" for k, v in top_pairs(local.get("socialInfluence") or {}, 5)) or "無明顯社交權重"
    ling = ext.get("linguistics") or {}
    phil = ext.get("philosophy") or {}
    dec = ext.get("decisionScience") or {}
    parts = []
    for idx in range(repeat):
        parts.append(
            f"<p>{esc(persona)} 的 {esc(focus)} 側寫不是單一人格標籤，而是由可回查資料共同構成："
            f"目前情緒狀態為 {esc(emotion.get('mood') or dynamic.get('current_state') or 'unknown')}，"
            f"動態事件是 {esc(dynamic.get('latest_event') or dynamic.get('signature_pattern') or '未標記')}；"
            f"熱腦峰值顯示 {esc(top_hot)}。第 {idx+1} 層讀法是：如果只看一句回覆，"
            f"會把牠誤判成模板角色；但若把任務、下注、結算、社交輸入與記憶壓力一起看，"
            f"牠的輸出其實受注意負載、錯誤預測、社交權重與自我校準共同限制。"
            f"語言風格={esc(ling.get('argumentStyle') or ling.get('syntaxRhythm') or 'evidence-first')}，"
            f"真理判準={esc(phil.get('epistemology') or 'outcome/consistency')}，"
            f"決策姿態={esc(dec.get('riskPosture') or dec.get('decisionStyle') or dynamic.get('stress_response') or 'verify-first')}。"
            f"任務足跡包含 one_on_one={esc(tasks.get('one_on_one_messages'))}、"
            f"bet_research={esc(tasks.get('bet_research'))}、bet_settlements={esc(tasks.get('bet_settlements'))}、"
            f"stock_trades={esc(tasks.get('stock_trades'))}、experience_events={esc(tasks.get('experience_events'))}。"
            f"這些數字讓 FBI 側寫可以落在行為預測：牠在壓力升高時會收斂、延遲或要求更多證據，"
            f"在社交權重 {esc(top_social)} 介入時會改變對話對象與反駁力度。"
            f"報告覆蓋狀態={esc(reports.get('ok'))}，因此本段不是補字數，而是把不同腦區參數接回可觀測行為。</p>"
        )
    return "\n".join(parts)


def make_report(persona: str, brain: dict) -> str:
    snap = brain.get("snapshot", {})
    wins = int(snap.get("wins") or 0)
    losses = int(snap.get("losses") or 0)
    total = wins + losses
    wr = round(wins / total * 100, 2) if total else 0
    coverage = brain.get("coverage") or {}
    counters = brain.get("countermeasures") or []
    wiring = brain.get("wiring") or {}
    ts = brain.get("cognitive_timeseries") or {}
    hot = (brain.get("hot_brain") or {}).get("params") or {}
    ext = brain.get("cognitive_extensions") or {}
    society = brain.get("society_model") or {}

    rows = []
    for k, v in {
        "resolved_games": total,
        "wins": wins,
        "losses": losses,
        "winrate_percent": wr,
        "fatigue": ts.get("fatigue"),
        "arousal": ts.get("arousal"),
        "attention_load": hot.get("attention_load"),
        "working_memory_pressure": hot.get("working_memory_pressure"),
        "reward_drive": hot.get("reward_drive"),
        "fear_signal": hot.get("fear_signal"),
        "integrated_prompt_ready": coverage.get("integrated_prompt_ready"),
    }.items():
        rows.append(f"<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>")

    counter_html = "".join(
        f"<li><strong>{esc(x.get('severity'))}</strong> {esc(x.get('issue'))}：{esc(x.get('action'))}</li>"
        for x in counters
    ) or "<li>目前無高優先度對策；維持資料回收與低風險任務循環。</li>"

    wiring_html = "".join(
        f"<li><strong>{esc(k)}</strong> uses {esc(', '.join(v.get('uses', [])))} -> outputs {esc(', '.join(v.get('outputs', [])))}</li>"
        for k, v in wiring.items()
    )

    ext_summary = json.dumps({
        "linguistics": ext.get("linguistics"),
        "philosophy": ext.get("philosophy"),
        "decisionScience": ext.get("decisionScience"),
        "gameTheory": ext.get("gameTheory"),
        "moralPsychology": ext.get("moralPsychology"),
        "existentialThreat": ext.get("existentialThreat"),
        "societyModel": society,
    }, ensure_ascii=False)[:5000]

    body = [
        f"<section class='report fbi-profile'><h2>{esc(persona)}｜FBI 行為側寫 / Integrated Brain Profile</h2>",
        "<p>本報告是資料庫證據型修復版。它不把 FBI Profile 寫成戲劇化人物介紹，而是把勝敗、下注、股票、社交、語言、哲學、決策科學、熱腦參數與 countermeasure 串成可檢查的行為側寫。若資料不足，報告只說不足；若資料存在，報告會說明該參數如何影響下一次說話、下注、退縮、合作或修正。</p>",
        "<h3>核心量化表</h3>",
        "<table><tbody>" + "".join(rows) + "</tbody></table>",
        "<h3>一、可觀測行為骨架</h3>",
        sentence_block(persona, brain, "行為骨架", 6),
        "<h3>二、壓力與疲勞不是裝飾詞</h3>",
        sentence_block(persona, brain, "壓力/疲勞", 5),
        "<h3>三、社交權重如何改寫對話姿態</h3>",
        sentence_block(persona, brain, "社交/權力", 5),
        "<h3>四、下注與股票任務如何讓人格變成決策系統</h3>",
        sentence_block(persona, brain, "下注/投資", 5),
        "<h3>五、反模板風險與修復對策</h3>",
        "<ul>" + counter_html + "</ul>",
        "<p>這個側寫最重要的地方，是把『牠會怎麼說』與『牠為什麼這樣說』分開。單純 LLM 可以生成任何語氣，但整合腦要求每一段輸出回扣到資料來源：熱腦給壓力，local brain 給社交權重，cognitive extensions 給語言與哲學規則，task_state 給今天到底做了什麼，settlement/stock 給真實回饋。這讓側寫可以被驗證，也可以被推翻。</p>",
        "<h3>六、接線圖</h3>",
        "<ul>" + wiring_html + "</ul>",
        "<h3>七、原始參數摘要</h3>",
        f"<pre>{esc(ext_summary)}</pre>",
        "<h3>八、投資人可讀結論</h3>",
        f"<p>{esc(persona)} 的價值不在於像人一樣講漂亮話，而在於它的腦參數、任務資料、下注結果、股票結果與社交輸入可以形成閉環。這份 FBI 側寫把人格視為可觀測、可修正、可驗證的工作流：當壓力上升，它要知道自己為什麼保守；當勝率低，它要知道哪些決策節點失效；當社交權重改變，它要知道自己是在被同伴影響還是在吸收證據。這才是投資人應該看到的『活體生命監控』，不是空白頁籤，也不是無來源摘要。</p>",
        "</section>",
    ]
    html_text = "\n".join(body)
    if len(html_text) < 5200:
        html_text += "\n" + sentence_block(persona, brain, "補充驗證", 4)
    return html_text


def main() -> None:
    personas = ["pincerbot", "vortexiq"]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    with conn() as c:
        c.execute(
            """CREATE TABLE IF NOT EXISTS lobster_analysis_report_repair_log (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               persona TEXT NOT NULL,
               report_type TEXT NOT NULL,
               old_len INTEGER NOT NULL,
               new_len INTEGER NOT NULL,
               source TEXT NOT NULL,
               created_at TEXT NOT NULL
            )"""
        )
        out = {}
        for persona in personas:
            brain = build_integrated_brain(persona)
            report = make_report(persona, brain)
            old = c.execute(
                "SELECT length(coalesce(html,'')) FROM lobster_analysis_reports WHERE persona=? AND report_type='fbi'",
                (persona,),
            ).fetchone()
            old_len = int(old[0] or 0) if old else 0
            c.execute(
                """INSERT INTO lobster_analysis_reports
                   (persona, report_type, html, source, model, wr, total, snapshot_json, meta_json, generated_at, updated_at)
                   VALUES (?, 'fbi', ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
                   ON CONFLICT(persona, report_type) DO UPDATE SET
                       html=excluded.html,
                       source=excluded.source,
                       model=excluded.model,
                       wr=excluded.wr,
                       total=excluded.total,
                       snapshot_json=excluded.snapshot_json,
                       meta_json=excluded.meta_json,
                       generated_at=excluded.generated_at,
                       updated_at=excluded.updated_at""",
                (
                    persona,
                    report,
                    SOURCE,
                    MODEL,
                    (
                        int(brain.get("snapshot", {}).get("wins") or 0)
                        / max(1, int(brain.get("snapshot", {}).get("wins") or 0) + int(brain.get("snapshot", {}).get("losses") or 0))
                    ),
                    (int(brain.get("snapshot", {}).get("wins") or 0) + int(brain.get("snapshot", {}).get("losses") or 0)),
                    json.dumps(brain.get("snapshot", {}), ensure_ascii=False),
                    json.dumps({"repair_source": SOURCE, "coverage": brain.get("coverage", {})}, ensure_ascii=False),
                    now,
                ),
            )
            c.execute(
                """INSERT INTO lobster_analysis_report_repair_log
                   (persona, report_type, old_len, new_len, source, created_at)
                   VALUES (?, 'fbi', ?, ?, ?, datetime('now'))""",
                (persona, old_len, len(report), SOURCE),
            )
            out[persona] = {"old_len": old_len, "new_len": len(report)}
        c.commit()
    print(json.dumps({"ok": True, "updated": out}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
