// lobster_structured_analyze.js
// 對單一 pick 跑 structured analysis,回傳 {pass, json, blockReason}
// 由 lobster-stock-agent.js require,共用其 callClaude 函數

'use strict';
const { execFileSync } = require('child_process');
const fs = require('fs');

const KDIR = '/root/lobster-stock-knowledge';
const SKILLS = ['technical','fundamental','macro','risk','discipline']
  .map(k => fs.readFileSync(`${KDIR}/lobster_skill_${k}.md`, 'utf8'))
  .join('\n\n=== SKILL DIVIDER ===\n\n');

function fetchTechnicals(symbol) {
  try {
    const out = execFileSync('python3', [`${KDIR}/lobster_fetch_technicals.py`, symbol],
      { timeout: 30000, stdio: ['ignore','pipe','pipe'] }).toString();
    return JSON.parse(out);
  } catch (e) {
    return { error: `tech_fetch_fail: ${e.message?.slice(0,80)}`, symbol };
  }
}

function fetchMacro() {
  try {
    const out = execFileSync('python3', [`${KDIR}/lobster_fetch_macro.py`],
      { timeout: 30000, stdio: ['ignore','pipe','pipe'] }).toString();
    return JSON.parse(out);
  } catch (e) {
    return { error: `macro_fail: ${e.message?.slice(0,80)}` };
  }
}

// callClaude 是 agent.js 內的全域函數(我們透過 caller 傳進來)
// 此 module 不直接 import https,只負責組 prompt + gate
function buildPrompt(persona, symbol, name, technicals, financials, macro, reportExcerpt, sourceCount) {
  const macroBrief = {
    vix: macro?.vix?.last, ust_10y: macro?.ust_10y?.last, dxy: macro?.dxy?.last,
    wti_oil: macro?.wti_oil?.last, spx_ret_5d: macro?.spx?.ret_5d_pct,
    ndx_ret_5d: macro?.ndx?.ret_5d_pct, regime: macro?.regime,
    sectors_5d: Object.fromEntries(Object.entries(macro?.sector_etfs||{}).map(([k,v]) => [k, v?.ret_5d_pct]))
  };

  const system = `你是 LLM 多智能體 ${persona} 的投資決策模組。你必須嚴格遵守下面五份技能卡,並以 strict JSON 輸出決策。除了 JSON 本身,你不可以輸出任何其他文字 (不准 markdown 標記、不准前言、不准 explanation)。`;

  const user = `## 五份技能卡 (完全遵守)

${SKILLS}

## 本次決策輸入

Symbol: ${symbol} (${name||symbol})

【技術線快照】(由 yfinance + Python 計算)
${JSON.stringify(technicals, null, 2)}

【財務快照】
${JSON.stringify(financials, null, 2)}

【本輪總經快照】
${JSON.stringify(macroBrief, null, 2)}

【外部研究報告摘要】 (sourceCount=${sourceCount}, excerpt 長度=${reportExcerpt?.length||0})
${reportExcerpt || '(沒有外部研究,本次必須 skip)'}

## 輸出要求 (嚴格 JSON,Skill 5 內 schema)

只回 JSON 物件,不要 markdown,不要文字外的內容。schema:

{
  "technical": { "trend": "...", "ma_alignment": "...", "rsi": 0.0, "macd_signal": "...", "bb_position": "...", "volume_signal": "...", "key_support": 0.0, "key_resistance": 0.0, "verdict": "bull|neutral|bear" },
  "fundamental": { "revenue_yoy_pct": 0.0, "gross_margin_pct": 0.0, "operating_margin_trend": "...", "fcf_sign": "...", "valuation_vs_history": "...", "verdict": "bull|neutral|bear" },
  "external": { "headline_news": ["..."], "sector_momentum_1m": "...", "macro_alignment": "tailwind|neutral|headwind" },
  "risks": [ {"type":"...","what":"...","trigger":"...","loss_estimate":"...","mitigation":"..."}, {"type":"...","what":"...","trigger":"...","loss_estimate":"...","mitigation":"..."} ],
  "decision": { "action": "buy|skip", "confidence": 0.0, "thesis": "至少 200 字繁體中文,引用 ≥3 個技術指標數字 + ≥2 個基本面數字 + ≥1 個外部新聞", "stop_loss_price": 0.0, "take_profit_price": 0.0, "position_size_pct": 0.0 }
}

action 必須是 "buy" 或 "skip"。任一條件不滿足就回 "skip" 並在 thesis 內解釋為什麼。`;

  return { system, user };
}

function parseJsonLoose(text) {
  if (!text) return null;
  // 拿出第一個 {...} block
  const m = text.match(/\{[\s\S]*\}/);
  if (!m) return null;
  try { return JSON.parse(m[0]); } catch { return null; }
}

function applyGate(parsed, technicals) {
  if (!parsed) return { pass: false, blockReason: 'no_parse' };
  const tech = parsed.technical || {};
  const fund = parsed.fundamental || {};
  const ext = parsed.external || {};
  const risks = Array.isArray(parsed.risks) ? parsed.risks : [];
  const dec = parsed.decision || {};

  if (dec.action !== 'buy') return { pass: false, blockReason: 'llm_skip' };
  if (!dec.thesis || dec.thesis.length < 200) return { pass: false, blockReason: `thesis_short_${dec.thesis?.length||0}` };
  if (risks.length < 2) return { pass: false, blockReason: `risks_insufficient_${risks.length}` };
  if (tech.rsi === null || tech.rsi === undefined) return { pass: false, blockReason: 'no_rsi_in_decision' };
  if (technicals?.error) return { pass: false, blockReason: `tech_fetch_error_${technicals.error?.slice(0,40)}` };
  if (!Array.isArray(ext.headline_news) || ext.headline_news.length < 1) return { pass: false, blockReason: 'no_news' };
  if (fund.verdict === 'bear') return { pass: false, blockReason: 'fundamental_bear' };
  if (typeof dec.confidence !== 'number' || dec.confidence < 0.55) return { pass: false, blockReason: `low_conf_${dec.confidence}` };
  if (typeof dec.stop_loss_price !== 'number' || dec.stop_loss_price <= 0)
    return { pass: false, blockReason: 'missing_sl' };
  if (typeof dec.take_profit_price !== 'number' || dec.take_profit_price <= 0)
    return { pass: false, blockReason: 'missing_tp' };

  return { pass: true };
}

function formatSelectionReason(json) {
  const t = json.technical||{}, f = json.fundamental||{}, e = json.external||{};
  const risks = json.risks||[]; const d = json.decision||{};
  const r1 = (risks[0]?.what||'').slice(0,40);
  const r2 = (risks[1]?.what||'').slice(0,40);
  const news = (e.headline_news?.[0]||'').slice(0,60);
  const head = `[T:${t.trend||'?'}/MA:${t.ma_alignment||'?'}/RSI:${t.rsi??'?'}/MACD:${t.macd_signal||t.macd_state||'?'}] ` +
               `[F:rev${f.revenue_yoy_pct??'?'}% gm${f.gross_margin_pct??'?'}% FCF:${f.fcf_sign||'?'}] ` +
               `[E:${news}] [risk×${risks.length}:${r1}/${r2}] ` +
               `SL:${d.stop_loss_price} TP:${d.take_profit_price} conf:${d.confidence}\n論點: `;
  const room = Math.max(0, 1180 - head.length);
  return (head + (d.thesis||'').slice(0, room)).slice(0, 1200);
}

module.exports = { fetchTechnicals, fetchMacro, buildPrompt, parseJsonLoose, applyGate, formatSelectionReason };
