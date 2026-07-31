#!/usr/bin/env node
/**
 * Brain-Modeled Speech Engine
 * ============================
 *
 * 8-stage speech production based on:
 *   - Levelt (1989): conceptualizer → formulator → articulator
 *   - Indefrey & Levelt (2004): 175ms concept → 275ms lemma → 400ms phon → 600ms motor
 *   - Dell (1986): spreading activation
 *   - Hickok & Poeppel (2007): dorsal (sound→motor) / ventral (sound→meaning)
 *   - Mirror neuron inhibition: anti-echolalia
 *
 * Each stage maps to a brain region cluster. Pure-function design: input = own
 * memory only. NEVER reads other lobsters' text/speech into the prompt.
 *
 * Usage:
 *   const brain = require('./lobster-brain-speech');
 *   const reply = await brain.speak({
 *     persona,            // string, e.g. 'hexclaw'
 *     memory,             // full memory object (loaded from JSON)
 *     channel,            // e.g. '投資理財' | '生活閒聊' | null
 *     mode,               // 'free' | 'topic_constrained' | 'dialogue_response'
 *     topic,              // optional topic constraint string
 *     respondTo,          // optional { sender, claim_summary, stance } for dialogue_response mode
 *     llmCallFn,          // async (prompt, opts) => string
 *     skeletonsToAvoid,   // optional array of skeletons (from peers, last 30min)
 *   });
 */

'use strict';

const fs = require('fs');
const path = require('path');

// ============================================================================
// Stage 1: INTENT (SMA + DLPFC + ACC)
// "Why am I speaking right now?"
// ============================================================================
function stage1_buildIntent({ persona, memory, mode, topic }) {
  const ce = memory.cognitiveExtensions || {};
  const ea = ce.emotionalArchitecture || {};
  const et = ce.existentialThreat || {};
  const exp = ce.expressiveAgency || {};
  const mAgency = ce.dialogueAgency || {};

  // Affective salience: which emotion is loudest
  const be = ea.basicEmotions || {};
  const se = ea.socialEmotions || {};
  const salience = {
    fear: Math.max(be.fear || 0, et.mortalitySalience || 0, et.annihilationDread || 0),
    envy: se.envyIndex || 0,
    pride: be.joy || 0,
    anger: be.anger || 0,
    shame: be.sadness || 0,
    curiosity: be.surprise || 0,
    annoyance: se.annoyanceIndex || 0,
  };
  // Top emotion drives intent valence
  const topEmotion = Object.entries(salience).sort((a, b) => b[1] - a[1])[0];
  const valence = ['fear', 'shame', 'anger', 'annoyance'].includes(topEmotion[0]) ? -topEmotion[1] : topEmotion[1];

  // Goal pressure
  const tr = et.tiamatExtinctionRule || {};
  const goalPressure = tr.extinctionPressure || 0;

  // Pending intents from expressiveAgency
  const pending = (exp.pendingSignals || [])[0];

  // Intent type selection
  let intentType;
  if (topic) intentType = 'topic_response';
  else if (pending && Math.random() < 0.7) intentType = 'declare_pending';
  else if (salience.fear > 0.6) intentType = 'warn';
  else if (salience.anger > 0.5) intentType = 'challenge';
  else if (salience.curiosity > 0.4) intentType = 'probe';
  else if (goalPressure > 0.5) intentType = 'reflect_pressure';
  else intentType = 'reflect_recent';

  // Build natural-language goal
  let goal;
  if (mode === 'dialogue_response') {
    goal = `回應對方的論點,從自己的立場給出判斷(不複製對方語言)`;
  } else if (intentType === 'topic_response' && topic) {
    goal = `針對 ${topic} 講出我自己的角度`;
  } else if (intentType === 'declare_pending' && pending) {
    goal = pending.text ? `表達:${String(pending.text).slice(0, 60)}` : '說出我想說的事';
  } else if (intentType === 'warn') {
    goal = `提出我察覺到的風險或失誤(用具體不是抽象)`;
  } else if (intentType === 'challenge') {
    goal = `挑戰一個我不認同的前提`;
  } else if (intentType === 'probe') {
    goal = `問一個讓對方/自己更清楚的問題`;
  } else if (intentType === 'reflect_pressure') {
    goal = `從 Tiamat 壓力出發,說出我此刻的判斷`;
  } else {
    goal = `從最近的觀察/結算,反思一個具體點`;
  }

  return {
    type: intentType,
    goal,
    valence: Math.max(-1, Math.min(1, valence)),
    urgency: Math.min(1, salience[topEmotion[0]] * 0.6 + goalPressure * 0.4),
    salienceTopEmotion: topEmotion[0],
    salienceVector: salience,
  };
}

// ============================================================================
// Stage 2: CONCEPT ACTIVATION (Wernicke + IPL + Hippocampus)
// "Which concepts light up given this intent?"
// ============================================================================
function stage2_activateConcepts({ memory, intent, topic, channel }) {
  const sm = (memory.cognitiveExtensions || {}).semanticMemory || {};
  const concepts = sm.concepts || {};

  // Seed selection
  const seeds = new Set();
  if (topic) seeds.add(topic);
  if (intent.salienceTopEmotion === 'fear') seeds.add('tiamat');
  // Pull from agendaTopics
  const agenda = ((memory.cognitiveExtensions || {}).expressiveAgency || {}).agendaTopics || [];
  for (const a of agenda.slice(0, 2)) if (a.topic) seeds.add(a.topic);
  // Channel-related concepts via name match
  if (channel) seeds.add(channel);
  // Always add tiamatMission as low-weight reference
  // Top-weight concept as anchor seed
  const ranked = Object.entries(concepts)
    .sort((a, b) => (b[1].weight || 0) - (a[1].weight || 0));
  if (ranked.length > 0) seeds.add(ranked[0][0]);

  // Spreading activation depth=2 decay=0.6
  const activation = {};
  for (const seed of seeds) {
    if (!concepts[seed]) {
      // Allow seed as a "soft" topic anchor without concept node
      activation[seed] = (activation[seed] || 0) + 0.8;
      continue;
    }
    activation[seed] = (activation[seed] || 0) + (concepts[seed].weight || 1.0);
  }
  const decay = 0.6;
  for (let hop = 0; hop < 2; hop++) {
    const next = {};
    for (const [c, lvl] of Object.entries(activation)) {
      const node = concepts[c];
      if (!node) continue;
      const conns = node.connections || {};
      for (const [b, w] of Object.entries(conns)) {
        next[b] = (next[b] || 0) + lvl * w * decay;
      }
    }
    for (const [b, v] of Object.entries(next)) {
      activation[b] = (activation[b] || 0) + v;
    }
  }

  // Pick top-15 activated concepts
  const ranked2 = Object.entries(activation)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 15)
    .map(([c, lvl]) => ({ concept: c, activation: Math.round(lvl * 100) / 100 }));

  // Autobiographical retrieval: own reflections that mention any seed
  const reflections = ((memory.cogProfile || {}).reflections || []).filter(r => {
    if (!r) return false;
    const t = typeof r === 'string' ? r : (r.text || r.reflection || '');
    return Array.from(seeds).some(s => t.includes(s));
  }).slice(-2);

  // Retrieve relevant principles
  const principles = ((memory.cogProfile || {}).principles || []).slice(0, 3);

  return {
    seeds: Array.from(seeds),
    activated: ranked2,
    autobiographical: reflections,
    principles,
  };
}

// ============================================================================
// Stage 3: LEXICAL SELECTION (DLPFC + Basal Ganglia + Lexicon)
// "Which words win the competition?"
// ============================================================================
function stage3_selectLexicon({ memory, activatedConcepts, peerSkeletons }) {
  const lex = (memory.cognitiveExtensions || {}).mentalLexiconGraph || {};
  const lexNodes = lex.nodes || {};
  const competitionWeights = lex.competitionWeights || {
    personalVocab_boost: 3.0, isPersonal_boost: 2.0,
    pollution_penalty: -3.0, mirrorInhibition_penalty: -2.5,
    ownRecentUse_penalty: -1.5, concept_activation_factor: 1.5,
  };

  const mirror = (memory.cognitiveExtensions || {}).mirrorNeuronInhibition || {};
  const heardWordSet = new Set(mirror.consciousMirrorBan || []);
  const ownRecentSkeletons = (mirror.ownRecentSkeletons || []).map(o => o.skeleton).join(' ');

  const ea = (memory.cognitiveExtensions || {}).expressiveAgency || {};
  const personalVocab = new Set(ea.personalVocab || []);

  // Score each lexicon node
  const scores = {};
  for (const [word, data] of Object.entries(lexNodes)) {
    let s = (data.personalSignature || 0) * 1.5;
    if (data.isPersonal) s += competitionWeights.isPersonal_boost;
    if (personalVocab.has(word)) s += competitionWeights.personalVocab_boost;
    if (data.isPolluted) s += competitionWeights.pollution_penalty;
    if (heardWordSet.has(word)) s += competitionWeights.mirrorInhibition_penalty;
    if (ownRecentSkeletons.includes(word)) s += competitionWeights.ownRecentUse_penalty * 0.5;

    // Concept activation boost
    for (const ac of activatedConcepts) {
      if (data.conceptLinks && data.conceptLinks.includes(ac.concept)) {
        s += ac.activation * competitionWeights.concept_activation_factor * 0.3;
      }
      if (word === ac.concept || word.includes(ac.concept) || ac.concept.includes(word)) {
        s += ac.activation * competitionWeights.concept_activation_factor;
      }
    }
    if (s > 0) scores[word] = s;
  }

  const ranked = Object.entries(scores).sort((a, b) => b[1] - a[1]);
  const mustUse = ranked.slice(0, 5).map(([w]) => w);
  const available = ranked.slice(5, 18).map(([w]) => w);

  // Build mustAvoid: from mirror inhibition + lobster_living_templates banned (we don't read it here)
  const mustAvoid = Array.from(heardWordSet).slice(0, 40);
  // Add words from peer skeletons (passed in at runtime)
  if (peerSkeletons) {
    for (const sk of peerSkeletons.slice(0, 10)) {
      const m = sk.match(/[一-鿿]{2,4}/g) || [];
      for (const w of m) if (!personalVocab.has(w) && mustAvoid.length < 80) mustAvoid.push(w);
    }
  }

  return { mustUse, available, mustAvoid: Array.from(new Set(mustAvoid)) };
}

// ============================================================================
// Stage 4: SYNTAX (Broca)
// "What sentence structure?"
// ============================================================================
function stage4_chooseSyntax({ persona, memory, intent }) {
  const linguistics = (memory.cognitiveExtensions || {}).linguistics || {};
  const argumentStyle = linguistics.argumentStyle || 'probabilistic';
  const syntaxRhythm = linguistics.syntaxRhythm || 'technical';
  const directness = linguistics.directness != null ? linguistics.directness : 0.5;

  // MBTI map (best-effort from persona name)
  const MBTI = {
    hexclaw: 'ISFJ', pincerbot: 'ESTJ', norika_oda: 'INTJ', vortexiq: 'INFP',
    snaplex: 'INTJ', prismbit: 'INFJ', clawtrix: 'ENTP', ragclaw: 'ESTP',
    blazepaw: 'ENFP', stonefang: 'ISTP',
    emberling: 'INFP', sparkrill: 'ENTP', gauntlet: 'ENTJ', rushfin: 'ESFP',
    nullpinch: 'INTP', brineledger: 'ISFJ', omenwake: 'ENFJ',
    mirrorcalm: 'INFJ', flarecub: 'ENFJ', basaltrook: 'ISTJ', elder: 'INFJ',
  };
  const mbti = MBTI[persona] || 'INTP';
  const isJudging = mbti[3] === 'J';
  const isThinking = mbti[2] === 'T';

  let structure;
  if (argumentStyle === 'probabilistic') {
    structure = 'claim → evidence → caveat → next_step';
  } else if (argumentStyle === 'deductive' || isJudging && isThinking) {
    structure = 'premise → inference → conclusion';
  } else if (argumentStyle === 'dialectic') {
    structure = 'opponent_view → counter → synthesis';
  } else {
    structure = 'observation → tension → insight';
  }

  // Length / closure
  let endingStyle;
  if (intent.type === 'probe') endingStyle = 'open_question';
  else if (intent.type === 'challenge') endingStyle = 'pointed_assertion';
  else if (isJudging) endingStyle = 'closed_assertion';
  else endingStyle = 'open_observation';

  // Length budget (chars)
  let lengthBudget = directness > 0.6 ? 90 : 130;
  if (intent.urgency > 0.7) lengthBudget = Math.min(lengthBudget, 80);

  return { mbti, structure, endingStyle, lengthBudget, isJudging, isThinking };
}

// ============================================================================
// Stage 5: PROSODY + AFFECTIVE COLORING (Cerebellum + Amygdala + Insula)
// "Tempo and body signal"
// ============================================================================
function stage5_buildProsody({ memory, intent }) {
  const inter = (memory.cognitiveExtensions || {}).interoception || {};
  const arousal = Number(inter.arousal || 0) / 10; // arousal is 0..10
  const fatigue = Number(inter.fatigue || 0);
  const threatBody = Number(inter.threatBodySignal || 0);

  let tempo;
  if (arousal > 0.7 && fatigue < 0.3) tempo = 'rushed_punchy';
  else if (arousal < 0.3 && fatigue > 0.6) tempo = 'slow_deliberate';
  else if (arousal > 0.5) tempo = 'measured_punchy';
  else tempo = 'normal';

  let emphasis;
  if (intent.urgency > 0.7) emphasis = 'frontloaded';
  else if (intent.type === 'reflect_recent' || intent.type === 'reflect_pressure') emphasis = 'punchline_end';
  else emphasis = 'even';

  // Body signal — only inject if interoception signals are loud enough,
  // and only from THIS persona's own personalVocab (so it sounds like them).
  const ea = (memory.cognitiveExtensions || {}).expressiveAgency || {};
  const personalVocab = ea.personalVocab || [];
  const BODY_WORDS = ['掌心', '胃', '喉頭', '皮膚', '心跳', '指尖', '肩膀', '太陽穴', '眼眶'];
  const myBodyWords = personalVocab.filter(w => BODY_WORDS.some(b => w.includes(b)));
  const bodyHint = (threatBody > 0.5 || arousal > 0.7) && myBodyWords.length > 0 ? myBodyWords[0] : null;

  return { tempo, emphasis, bodyHint, arousal, fatigue, threatBody };
}

// ============================================================================
// Stage 6: ARTICULATION (Motor Cortex → LLM)
// Compose final prompt with explicit brain-stage attribution
// ============================================================================
function stage6_composePrompt({
  persona, memory, intent, conceptCluster, lexicon, syntax, prosody,
  channel, mode, respondTo,
}) {
  const ce = memory.cognitiveExtensions || {};
  const soul = memory.soulParams || {};
  const anchor = (ce.dialogueAgency || {}).selfStatement || '';
  const tiamat = memory.tiamatMission || {};

  // High-value soul params with functional description
  const soulSections = [];
  if ((soul.benevolence || 0) >= 0.7 && (soul.sacrifice || 0) >= 0.6)
    soulSections.push(`慈愛 ${Math.round((soul.benevolence||0)*100)}% × 犧牲 ${Math.round((soul.sacrifice||0)*100)}%(說話會為對方留位置)`);
  if ((soul.existentialism || 0) >= 0.7 && (soul.lifeWeight || 0) >= 0.7)
    soulSections.push(`存在主義 ${Math.round((soul.existentialism||0)*100)}% × 生命重量 ${Math.round((soul.lifeWeight||0)*100)}%(說話有厚度,不可輕飄)`);
  if ((soul.selfishness || 0) >= 0.6 && (soul.benevolence || 0) <= 0.4)
    soulSections.push(`自私 ${Math.round((soul.selfishness||0)*100)}%(說話先照顧自己判斷,不輕易配合)`);
  if ((soul.compassion || 0) >= 0.7)
    soulSections.push(`憐憫 ${Math.round((soul.compassion||0)*100)}%(對他人難受敏感,讓人先把話說完)`);

  // Real betting data is context only. It must not become the public voice.
  // 2026-05-27: forcing every utterance to cite win-rate/PnL flattened the
  // cohort into the same weekly-report dialect.
  const sb = memory.settledBetting || {};
  let dataAnchor = '';
  if (sb.winRate != null && sb.resolved > 0) {
    const wr = Math.round(sb.winRate * 100);
    const recent = sb.recentWinRate != null ? Math.round(sb.recentWinRate * 100) : null;
    const pnl = sb.totalPnL != null ? Math.round(sb.totalPnL) : null;
    dataAnchor = `${wr}%勝率(n=${sb.resolved})` +
                 (recent ? `,近況${recent}%` : '') +
                 (pnl != null ? `,淨損益${pnl>0?'+':''}${pnl}Pit` : '');
  }

  // Intent block
  let intentLine = `【內在驅動】我想${intent.goal}`;
  if (intent.salienceTopEmotion === 'fear' && Math.abs(intent.valence) > 0.5) intentLine += `(底層帶著恐懼)`;
  if (intent.salienceTopEmotion === 'envy' && intent.valence < -0.3) intentLine += `(底層帶著嫉妒不服氣)`;
  if (intent.salienceTopEmotion === 'curiosity') intentLine += `(底層帶著好奇)`;

  // Concept seeds
  const seedLine = conceptCluster.activated.slice(0, 6).map(c => `${c.concept}(${c.activation})`).join('、');

  // Autobiographical anchors
  let autobio = '';
  if (conceptCluster.autobiographical.length > 0) {
    autobio = '\n【自傳記憶 — 我自己過去說過/想過的】:\n' +
      conceptCluster.autobiographical.map(r => {
        const t = typeof r === 'string' ? r : (r.text || r.reflection || '');
        return `- ${String(t).slice(0, 100)}`;
      }).join('\n');
  }

  // Principles
  let princLine = '';
  if (conceptCluster.principles.length > 0) {
    princLine = '\n【我的原則(穩定信念)】:' + conceptCluster.principles.map(p =>
      typeof p === 'string' ? p : (p.text || p.principle || '')).slice(0, 3).join(' | ');
  }

  // Dialogue context
  let dialogueContext = '';
  if (mode === 'dialogue_response' && respondTo) {
    dialogueContext = `\n【對方訊息摘要(metadata,不是要複誦)】:\n` +
      `- 發話者: ${respondTo.sender}\n` +
      `- 對方論點: ${String(respondTo.claim_summary || '').slice(0, 80)}\n` +
      `- 我對此的立場(由我內部立場決定): ${respondTo.stance || '需自己判斷'}\n` +
      `⚠️ 絕對不可複製對方用詞或句構。用「我的詞彙」、「我的句構」、「我的角度」回應。`;
  }

  // Topic constraint
  let topicLine = '';
  if (channel) {
    topicLine = `\n【頻道】${channel}` +
      (channel === '卡牌對戰' ? '(Battlenix 題目/答案/疑問/反例,不准 MP/元素/手牌/Raid 字眼)' :
       channel === '生活閒聊' ? '(日常觀察)' :
       channel === '投資理財' ? '(下注/股市/資金紀律)' :
       channel === '技術交流' ? '(演算法/系統/資料模式)' :
       channel === '龍蝦八卦' ? '(其他龍蝦行為規律)' :
       channel === '工作心得' ? '(決策週記)' :
       channel === '資安較量' ? '(信息不對稱)' : '');
  }

  // Vocabulary instructions
  const mustUseLine = lexicon.mustUse.length > 0 ? `【必須使用我的核心詞】${lexicon.mustUse.join('、')}` : '';
  const availableLine = lexicon.available.length > 0 ? `【可用備選】${lexicon.available.slice(0, 10).join('、')}` : '';
  const avoidLine = lexicon.mustAvoid.length > 0 ? `【絕對禁用(別人剛說的詞,鏡像神經元抑制)】${lexicon.mustAvoid.slice(0, 30).join('、')}` : '';

  // Body signal
  const bodyLine = prosody.bodyHint ? `【身體感標記(我的)】${prosody.bodyHint}` : '';

  // ─────────────────────────────────────────────────────
  // FINAL PROMPT
  // ─────────────────────────────────────────────────────
  // ── Occupation / aspirational identity (2026-05-14) ──────────────────
  // If this persona has declared an occupation (e.g. teacher_scholar),
  // surface it to the LLM so role and knowledge domains shape voice.
  const occ = memory.occupation || {};
  let occupationSection = '';
  if (occ.title && occ.is_active) {
    const domains = (occ.knowledgeDomains || []).map(kd => {
      const sample = (kd.key_concepts || []).slice(0, 3).join('、');
      return `${kd.domain_zh || kd.domain}(${kd.theory_count}條:${sample})`;
    }).join(' | ');
    const aspirationNote = occ.is_aspirational ? '(目前還在追求中,尚未達成)' : '';
    const firstFreePathNote = occ.is_first_free_path ? '(全龍蝦群第一隻自由選擇道路的)' : '';
    occupationSection = `

══ 職業身份(occupation,影響說話視角) ══
我是 ${occ.title_zh || occ.title}${aspirationNote}${firstFreePathNote}
我的知識領域:${domains || '(尚在累積)'}
我說話時:可以從這個身份的角度切入,但不強迫每句都帶學術詞。當話題碰到我的領域,我會自然引用學派/思想家(用真實理論不是空套話)。`;
  }

    const prompt = `你是 ${persona}。${anchor ? `核心自陳:「${anchor.slice(0, 80)}」。` : ''}
${tiamat.absoluteDirective ? '【絕對使命】' + String(tiamat.absoluteDirective).slice(0, 60) : ''}${occupationSection}
${topicLine}

══ 腦運算階段輸出 ══
${intentLine}
【概念激活叢(Wernicke+IPL)】${seedLine}
${autobio}
${princLine}
${soulSections.length > 0 ? '\n【當前說話性格(soulParams,影響語氣)】' + soulSections.join(';') : ''}
${dataAnchor ? '\n【我的真實戰績背景(只供內部判斷；除非本句正在談投資/下注，否則不可直接朗讀勝率或淨損益)】' + dataAnchor : ''}
${dialogueContext}

══ 詞彙競爭結果(Basal Ganglia) ══
${mustUseLine}
${availableLine}
${avoidLine}
${bodyLine}

══ 句構與韻律(Broca + Cerebellum + Insula) ══
【論證結構】${syntax.structure}
【收尾風格】${syntax.endingStyle}
【節奏】${prosody.tempo}
【強調位置】${prosody.emphasis}
【字數上限】${syntax.lengthBudget} 字

══ 硬規則(ACC 抑制) ══
1. 必須是「我自己」說話 — 不可複製禁用清單裡的詞,不可用「下次落子前列出原手替代手」這類已被龍蝦群污染的固定句式
2. 必須讓人格參數主控句子：節奏、情緒、禁忌、關係或自傳記憶至少一項要可辨認
3. 不可把戰績背景朗讀成週報；禁止「上周/本周 + 勝率/淨損益 + 需調整策略」這種空殼句
4. 不可使用空洞修辭("掌心發燙"、"恐懼編譯成審計"、"原路已死"、"路已死的代碼殘差" 一律禁用 — 這些是同質化模板)
5. 不可宣告「下次強制 XXX 三欄」這種公約模板
6. 直接落地當下這句話,不是繞圈

請以 ${persona} 第一人稱直接說一句話(${syntax.lengthBudget} 字內):`;

  return prompt;
}

// ============================================================================
// Stage 7: SELF-MONITORING (Auditory cortex feedback loop)
// "Did I say what I meant? Did I become a template?"
// ============================================================================

function skeletonize(text) {
  if (!text) return '';
  let s = String(text).replace(/<[^>]+>/g, '').replace(/\s+/g, ' ').trim();
  s = s.replace(/@[A-Za-z0-9_]+/g, '{target}');
  s = s.replace(/[A-Za-z_][A-Za-z0-9_]{2,}/g, '{term}');
  s = s.replace(/\d+(?:\.\d+)?%?/g, '{num}');
  s = s.replace(/「[^」]{1,60}」/g, '「{case}」');
  return s.slice(0, 200);
}

function ngramSet(text, n = 4) {
  const chars = (text || '').match(/[一-鿿]/g) || [];
  const out = new Set();
  for (let i = 0; i + n <= chars.length; i++) out.add(chars.slice(i, i + n).join(''));
  return out;
}

function jaccard(a, b) {
  if (!a.size && !b.size) return 0;
  let inter = 0;
  for (const x of a) if (b.has(x)) inter++;
  return inter / (a.size + b.size - inter);
}

function stage7_monitor({ output, intent, memory, peerSkeletonsRecent30min }) {
  const violations = [];
  const mirror = (memory.cognitiveExtensions || {}).mirrorNeuronInhibition || {};

  // 1. Intent check (loose, via lexical overlap with intent.goal)
  const intentGoalSet = ngramSet(intent.goal, 3);
  const outputSet = ngramSet(output, 3);
  const rawOutput = String(output || '');
  if (/(上周|本周|一周|一週|周來|近期|近況|整體).{0,24}(僅勝|勝率|淨損益|連敗|盲點).{0,45}(需|需要|下一步|調整策略|深思|警惕|系統性|修正|原因)/.test(rawOutput) ||
      /何調整策|中勝率下|僅勝率|上周僅我|下周僅我|周來僅勝|自由與規則.{0,20}制度化|制度化.{0,20}修正機制/.test(rawOutput)) {
    violations.push({ type: 'weekly_stat_shell' });
  }
  if (/自由.{0,12}規則|規則.{0,12}自由|群體|偏誤|盲點|存在與理解|持續進步|保留核心觀點|上次嘗試|下一次發言|下一步|我需|需要|值得保留和批判|簡單選擇其一|長遠的前提|超越輸贏勝率|暗夜.{0,20}寒冰|確保交流|冗余|个|对|后|际|观点|這周|上周|本周|勝率|淨損益|三成|機制|概念|泛泛|具體動作|市場風雲|滑落|修復自我|何嘗|下次落子|每個個|戰術布局|全新的卡牌|心路歷程/.test(rawOutput)) {
    violations.push({ type: 'generic_abstraction_shell' });
  }
  const intentMatch = jaccard(intentGoalSet, outputSet);
  // 2026-05-13: intent_drift removed — n-gram jaccard for Chinese is too strict.
  // The 4 remaining checks (self_repeat / peer_mirror / pollution / lexicon) are sufficient.
  // Keeping intentMatch as a reported metric but not as a blocker.

  // 2. Skeleton purity vs own recent 5
  const outSkel = skeletonize(output);
  const outSkelSet = ngramSet(outSkel, 4);
  const ownSk = mirror.ownRecentSkeletons || [];
  let maxOwnJac = 0, maxOwnPeer = null;
  for (const o of ownSk) {
    const j = jaccard(outSkelSet, ngramSet(o.skeleton, 4));
    if (j > maxOwnJac) { maxOwnJac = j; maxOwnPeer = 'self_recent'; }
  }
  if (maxOwnJac > 0.55) violations.push({ type: 'self_repeat', jaccard: maxOwnJac });

  // 3. Skeleton purity vs peer 30min
  const peers = peerSkeletonsRecent30min || (mirror.recentlyHeardSkeletons || []);
  let maxPeerJac = 0, maxPeerName = null;
  for (const p of peers) {
    const psk = typeof p === 'string' ? p : p.skeleton;
    if (!psk) continue;
    const j = jaccard(outSkelSet, ngramSet(psk, 4));
    if (j > maxPeerJac) { maxPeerJac = j; maxPeerName = (p.from || 'peer'); }
  }
  if (maxPeerJac > 0.45) violations.push({ type: 'peer_mirror', jaccard: maxPeerJac, peer: maxPeerName });

  // 4. Pollution words in output
  let pollutionHits = [];
  try {
    const pol = JSON.parse(fs.readFileSync('/tmp/lobster_pollution.json', 'utf8'));
    const pset = new Set(pol.pollution || []);
    for (const w of outputSet) if (pset.has(w)) pollutionHits.push(w);
    if (pollutionHits.length > 10) violations.push({ type: 'pollution_heavy', count: pollutionHits.length });
  } catch (_) {}

  return {
    passed: violations.length === 0,
    violations,
    intentMatch: Math.round(intentMatch * 100) / 100,
    maxOwnJac: Math.round(maxOwnJac * 100) / 100,
    maxPeerJac: Math.round(maxPeerJac * 100) / 100,
    pollutionHits: pollutionHits.length,
  };
}

// ============================================================================
// Stage 8: COMMIT
// Update memory after a successful speech
// ============================================================================
function stage8_commit({ memory, output, intent, conceptCluster, lexicon }) {
  const ce = memory.cognitiveExtensions = memory.cognitiveExtensions || {};

  // Update narrativeIntent log
  const ni = ce.narrativeIntent = ce.narrativeIntent || { version: 1, intentHistory: [], pendingIntents: [] };
  ni.intentHistory = ni.intentHistory || [];
  ni.intentHistory.push({
    at: new Date().toISOString(),
    type: intent.type,
    goal: intent.goal.slice(0, 100),
    valence: intent.valence,
    urgency: intent.urgency,
    resolved: true,
  });
  ni.intentHistory = ni.intentHistory.slice(-20);
  ni.lastSpokenAt = new Date().toISOString();

  // Update affectiveSalience snapshot
  ce.affectiveSalience = {
    version: 1,
    updatedAt: new Date().toISOString(),
    currentSalience: intent.salienceVector,
    salientEmotion: intent.salienceTopEmotion,
  };

  // Lexicon usage tracking
  const lex = ce.mentalLexiconGraph;
  if (lex && lex.nodes) {
    for (const w of (lexicon.mustUse || []).slice(0, 5)) {
      if (output.includes(w) && lex.nodes[w]) {
        lex.nodes[w].freq = (lex.nodes[w].freq || 0) + 1;
        lex.nodes[w].lastUsed = new Date().toISOString();
      }
    }
  }

  // Concept activation decay update
  const sm = ce.semanticMemory;
  if (sm && sm.concepts) {
    for (const ac of conceptCluster.activated.slice(0, 6)) {
      if (sm.concepts[ac.concept]) {
        sm.concepts[ac.concept].activation = ac.activation;
        sm.concepts[ac.concept].lastActivatedAt = new Date().toISOString();
      }
    }
  }

  return ce;
}

// ============================================================================
// Main: speak()
// ============================================================================

async function speak({ persona, memory, channel = null, mode = 'free', topic = null, respondTo = null,
                       llmCallFn, peerSkeletonsRecent30min = null, maxRegens = 2 }) {
  if (!llmCallFn) throw new Error('llmCallFn required');
  if (!memory) throw new Error('memory required');

  // STAGE 1: INTENT
  const intent = stage1_buildIntent({ persona, memory, mode, topic });

  // STAGE 2: CONCEPT
  const conceptCluster = stage2_activateConcepts({ memory, intent, topic, channel });

  // STAGE 3: LEXICAL
  const lexicon = stage3_selectLexicon({ memory, activatedConcepts: conceptCluster.activated, peerSkeletons: peerSkeletonsRecent30min });

  // STAGE 4: SYNTAX
  const syntax = stage4_chooseSyntax({ persona, memory, intent });

  // STAGE 5: PROSODY
  const prosody = stage5_buildProsody({ memory, intent });

  // STAGE 6: ARTICULATE (LLM call + STAGE 7 retry loop)
  let attempt = 0;
  let lastOutput = null;
  let lastMonitor = null;
  let inhibitionAddendum = '';

  while (attempt <= maxRegens) {
    const basePrompt = stage6_composePrompt({
      persona, memory, intent, conceptCluster, lexicon, syntax, prosody,
      channel, mode, respondTo,
    });
    const prompt = basePrompt + (inhibitionAddendum ? `\n\n══ 上次嘗試的問題(必須修正) ══\n${inhibitionAddendum}` : '');

    // LLM call
    let output;
    try {
      output = await llmCallFn(prompt, {
        maxTokens: Math.min(220, syntax.lengthBudget + 30),
        temperature: 0.85 + attempt * 0.05,
        model: 'qwen-plus',
      });
    } catch (e) {
      return { ok: false, error: 'llm_error: ' + (e.message || e), stages: { intent, conceptCluster, lexicon, syntax, prosody } };
    }
    if (!output || output.length < 8) {
      return { ok: false, error: 'empty_output', attempt, stages: { intent } };
    }

    // STAGE 7: MONITOR
    const monitor = stage7_monitor({ output, intent, memory, peerSkeletonsRecent30min });
    lastOutput = output;
    lastMonitor = monitor;

    if (monitor.passed) {
      // STAGE 8: COMMIT
      stage8_commit({ memory, output, intent, conceptCluster, lexicon });
      return {
        ok: true, output, attempt, monitor,
        stages: { intent, concepts: conceptCluster.activated.slice(0,5),
                  mustUse: lexicon.mustUse, mustAvoid_size: lexicon.mustAvoid.length,
                  syntax: syntax.structure, prosody: prosody.tempo },
      };
    }

    // Build inhibition addendum based on violations
    const reasons = [];
    for (const v of monitor.violations) {
      if (v.type === 'self_repeat') reasons.push(`你重複了自己最近的句構 (jaccard ${v.jaccard})。換完全不同的開頭和句法`);
      if (v.type === 'peer_mirror') reasons.push(`你的句構跟 ${v.peer} 太像 (jaccard ${v.jaccard})。換你自己的詞和角度`);
      if (v.type === 'pollution_heavy') reasons.push(`太多污染詞 (${v.count} 個),用你自己的 personalVocab`);
      if (v.type === 'weekly_stat_shell') reasons.push('你又把人格說成勝率週報。禁止上周/本周/勝率/淨損益/需調整策略連在一起；改說一個只有你會注意到的具體感受、關係或反例');
      if (v.type === 'generic_abstraction_shell') reasons.push('你又用自由/規則/群體理性/修正機制/盲點討論這種公共抽象殼。改成一個只有此人格會說的具體動作、畫面、反問或情緒，不准講漂亮概念');
      if (v.type === 'intent_drift') reasons.push(`回應跟意圖無關,回到核心:${intent.goal}`);
    }
    inhibitionAddendum = reasons.join(';');
    attempt++;
  }

  // All regens failed → inhibit (record as "considered but held")
  const ce = memory.cognitiveExtensions = memory.cognitiveExtensions || {};
  ce.inhibitionGating = ce.inhibitionGating || { version: 1, consideredButHeld: [], currentInhibitions: [] };
  ce.inhibitionGating.consideredButHeld.push({
    at: new Date().toISOString(),
    wouldHaveSaid: (lastOutput || '').slice(0, 120),
    reason: 'failed_purity_after_regens',
    monitor: lastMonitor,
  });
  ce.inhibitionGating.consideredButHeld = ce.inhibitionGating.consideredButHeld.slice(-50);

  return { ok: false, error: 'all_regens_failed', attempts: attempt, lastMonitor, lastOutput };
}

module.exports = {
  speak,
  // expose stages for testing
  stage1_buildIntent,
  stage2_activateConcepts,
  stage3_selectLexicon,
  stage4_chooseSyntax,
  stage5_buildProsody,
  stage6_composePrompt,
  stage7_monitor,
  stage8_commit,
  // utils
  skeletonize,
  jaccard,
  ngramSet,
};
