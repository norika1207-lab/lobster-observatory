#!/usr/bin/env node
/**
 * lobster-pvp.js
 * 讓兩隻龍蝦互打一場 Charenix 卡牌對戰
 * Usage: node /root/lobster-pvp.js [personaA] [personaB]
 * Default: snaplex vs ragclaw
 */
const http  = require('http');
const fs    = require('fs');
const path  = require('path');

const TOKEN_DIR = path.join(__dirname, 'lobster-tokens');
const AUTH_BASE = 'http://localhost:8010';

const PERSONAS = {
  norika_oda:  { password: 'lobster_norika_2026' },
  clawtrix:    { password: 'lobster_clawtrix_2026' },
  pincerbot:   { password: 'lobster_pincerbot_2026' },
  ragclaw:     { password: 'lobster_ragclaw_2026' },
  snaplex:     { password: 'lobster_snaplex_2026' },
  hexclaw:     { password: 'lobster_hexclaw_2026' },
  vortexiq:    { password: 'lobster_vortexiq_2026' },
  prismbit:    { password: 'lobster_prismbit_2026' },
};

const nameA = process.argv[2] || 'snaplex';
const nameB = process.argv[3] || 'ragclaw';

// ── HTTP helpers ────────────────────────────────────────────
function httpReq(options, body) {
  return new Promise((resolve, reject) => {
    const req = http.request(options, res => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => {
        try { resolve([res.statusCode, JSON.parse(d)]); }
        catch { resolve([res.statusCode, d]); }
      });
    });
    req.on('error', reject);
    if (body) req.write(typeof body === 'string' ? body : JSON.stringify(body));
    req.end();
  });
}

function post(port, path_, token, body) {
  return httpReq({
    host: 'localhost', port, path: path_, method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(token ? { 'Authorization': `Bearer ${token}` } : {}) }
  }, body);
}

function get(port, path_, token) {
  return httpReq({
    host: 'localhost', port, path: path_, method: 'GET',
    headers: token ? { 'Authorization': `Bearer ${token}` } : {}
  });
}

// ── Token ────────────────────────────────────────────────────
function loadCachedToken(name) {
  try {
    const d = JSON.parse(fs.readFileSync(path.join(TOKEN_DIR, `${name}.json`), 'utf8'));
    if (Date.now() < d.expiry) return d.token;
  } catch {}
  return null;
}

async function getToken(name) {
  const cached = loadCachedToken(name);
  if (cached) return cached;
  const pw = PERSONAS[name]?.password;
  if (!pw) throw new Error(`Unknown persona: ${name}`);
  const body = `username=${encodeURIComponent(name)}&password=${encodeURIComponent(pw)}`;
  const [, data] = await httpReq({
    host: 'localhost', port: 8010, path: '/api/token', method: 'POST',
    headers: { 'Content-Type': 'application/x-www-form-urlencoded' }
  }, body);
  if (!data.access_token) throw new Error(`Login failed for ${name}: ${JSON.stringify(data)}`);
  fs.writeFileSync(path.join(TOKEN_DIR, `${name}.json`),
    JSON.stringify({ token: data.access_token, expiry: Date.now() + 50*60*1000 }));
  return data.access_token;
}

// ── Element strategy (mirrors battle engine) ────────────────
const ELEM_COUNTER = { wind:'fire', fire:'water', water:'earth', earth:'wind' };
function elemMult(atk, def) {
  if (ELEM_COUNTER[atk] === def) return 1.30;
  if (ELEM_COUNTER[def] === atk) return 0.70;
  return 1.00;
}

// Per-persona battle styles
const PERSONA_STYLE = {
  snaplex:    { sport: [],                            element: null,   aggression: 6, focus: 'counter' },
  ragclaw:    { sport: ['NS_','NBA_','MLB_'],          element: 'wind', aggression: 9, focus: 'own'     },
  pincerbot:  { sport: ['BOX_','MMA_','K1_','UFC_'],  element: 'earth',aggression: 8, focus: 'sport'   },
  hexclaw:    { sport: ['NBA_','H_'],                  element: 'water',aggression: 4, focus: 'sport'   },
  clawtrix:   { sport: ['NS_','CS_','LOL_','DOTA_'],   element: 'wind', aggression: 7, focus: 'sport'   },
  norika_oda: { sport: ['F_','MG_','H_'],              element: 'earth',aggression: 3, focus: 'own'     },
  vortexiq:   { sport: ['F_','MG_','H_'],              element: 'fire', aggression: 7, focus: 'counter' },
  prismbit:   { sport: ['MLB_','MLB'],                 element: 'water',aggression: 2, focus: 'own'     },
};

function pickCards(hand, mp, state, mySide, personaName) {
  const style = PERSONA_STYLE[personaName] || PERSONA_STYLE.snaplex;
  const oppHand  = mySide === 'a' ? state.hand_b : state.hand_a;
  const weather  = state.weather || {};
  const wElem    = weather.element;
  const wBonus   = weather.bonus || 0;

  const oppElems = (oppHand || []).map(c => c.element).filter(Boolean);
  const mostOppElem = oppElems.length
    ? oppElems.sort((a,b) => oppElems.filter(x=>x===b).length - oppElems.filter(x=>x===a).length)[0]
    : null;

  // Committed element: counter-focused picks best counter, own-focused commits to style elem
  const counterElem = mostOppElem ? ELEM_COUNTER[mostOppElem] : null;
  const myElem = style.focus === 'counter' ? counterElem
    : style.focus === 'own'                ? style.element
    : style.element; // 'sport' uses style element as tiebreaker

  function sportBonus(c) {
    return style.sport.some(pfx => (c.id||'').startsWith(pfx)) ? 5 : 0;
  }
  function attScore(c) {
    let s = c.atk || 0;
    if (wElem && c.element === wElem) s += wBonus * 0.6;
    if (mostOppElem) s *= elemMult(c.element, mostOppElem);
    if ((c.level || 0) >= 50) s += 5;
    s += sportBonus(c);
    if (myElem && c.element === myElem) s += 4;
    return s;
  }
  function bufScore(c) { return Math.abs((c.buff_effect || {}).value || 0); }
  function debScore(c) { return Math.abs((c.debuff_effect || {}).value || 0); }

  const attPool = hand.filter(c => c.role === 'attacker' || c.role === 'dual').sort((a,b) => attScore(b)-attScore(a));
  const bufPool = hand.filter(c => (c.role === 'supporter' || c.role === 'dual') && c.buff_effect).sort((a,b) => bufScore(b)-bufScore(a));
  const debPool = hand.filter(c => (c.role === 'disruptor' || c.role === 'dual') && c.debuff_effect).sort((a,b) => debScore(b)-debScore(a));

  const attacker = attPool[0]?.id || null;
  const used = new Set(attacker ? [attacker] : []);
  const mpBudget = Math.floor(mp * (0.4 + style.aggression * 0.06));
  let mpLeft = mpBudget, buffer = null, debuffer = null;

  for (const c of bufPool) {
    if (!used.has(c.id) && mpLeft >= (c.mp_cost || 0) && bufScore(c) > 0) {
      buffer = c.id; used.add(c.id); mpLeft -= c.mp_cost || 0; break;
    }
  }
  for (const c of debPool) {
    if (!used.has(c.id) && mpLeft >= (c.mp_cost || 0) && debScore(c) > 0) {
      debuffer = c.id; break;
    }
  }
  return { attacker, buffer, debuffer };
}

function cardDesc(hand, id) {
  if (!id) return 'pass';
  const c = hand.find(x => x.id === id);
  return c ? `${c.name_short||c.id}(${c.element||'?'},ATK:${c.atk})` : id;
}

// ── Main battle ──────────────────────────────────────────────
async function main() {
  console.log(`\n🦞 LOBSTER PvP: ${nameA} vs ${nameB}`);
  console.log('═'.repeat(50));

  // Get tokens (sequential to avoid rate limit)
  console.log(`Getting tokens...`);
  const tA = await getToken(nameA);
  const tB = await getToken(nameB);
  const styleA = PERSONA_STYLE[nameA] || PERSONA_STYLE.snaplex;
  const styleB = PERSONA_STYLE[nameB] || PERSONA_STYLE.snaplex;
  console.log(`✓ ${nameA} logged in  [elem:${styleA.element||'adaptive'} agg:${styleA.aggression} ${styleA.focus}]`);
  console.log(`✓ ${nameB} logged in  [elem:${styleB.element||'adaptive'} agg:${styleB.aggression} ${styleB.focus}]`);

  // A creates room
  const [cs, cr] = await post(8004, '/api/room/create', tA, { creator: nameA, bet: 500 });
  if (cs !== 200) throw new Error(`Create room failed: ${JSON.stringify(cr)}`);
  const roomId  = cr.room_id;
  const matchId = cr.match_id;
  console.log(`\n✓ Room created: ${roomId}  match: ${matchId}  bet: ${cr.bet}`);

  // B joins room
  const [js, jr] = await post(8004, `/api/room/${roomId}/join`, tB, { challenger: nameB });
  if (js !== 200) throw new Error(`Join room failed: ${JSON.stringify(jr)}`);
  console.log(`✓ ${nameB} joined as player B\n`);

  // Battle loop
  let round = 0;
  let winner = null;

  while (!winner && round < 30) {
    const [, state] = await get(8004, `/api/match/${matchId}`, tA);
    if (!state?.id) break;
    winner = state.winner;
    if (winner) break;

    console.log(`─── Round ${state.round} ─── HP: ${nameA}=${state.hp_a} ${nameB}=${state.hp_b}  MP: A=${state.mp_a} B=${state.mp_b}  Weather: ${state.weather?.cn||'晴'}`);

    // Both play simultaneously (each with their own persona style)
    const picksA = pickCards(state.hand_a, state.mp_a, state, 'a', nameA);
    const picksB = pickCards(state.hand_b, state.mp_b, state, 'b', nameB);

    const playA = async () => {
      if (state.pending_a) return;
      if (picksA.attacker) {
        await post(8004, `/api/match/${matchId}/play`, tA,
          { side:'a', attacker:picksA.attacker, buffer:picksA.buffer, debuffer:picksA.debuffer, raise_amount:0 });
      } else {
        await post(8004, `/api/match/${matchId}/pass`, tA, { side:'a' });
      }
    };
    const playB = async () => {
      if (state.pending_b) return;
      if (picksB.attacker) {
        await post(8004, `/api/match/${matchId}/play`, tB,
          { side:'b', attacker:picksB.attacker, buffer:picksB.buffer, debuffer:picksB.debuffer, raise_amount:0 });
      } else {
        await post(8004, `/api/match/${matchId}/pass`, tB, { side:'b' });
      }
    };

    // Play simultaneously
    await Promise.all([playA(), playB()]);

    // Get resolved state
    const [, resolved] = await get(8004, `/api/match/${matchId}`, tA);
    const log = resolved?.rounds_log?.[resolved.rounds_log.length - 1];

    // Print round summary
    const atkA = cardDesc(state.hand_a, picksA.attacker);
    const atkB = cardDesc(state.hand_b, picksB.attacker);
    console.log(`  ${nameA}: ${atkA}${picksA.buffer ? ` +buf` : ''}${picksA.debuffer ? ` +deb` : ''}`);
    console.log(`  ${nameB}: ${atkB}${picksB.buffer ? ` +buf` : ''}${picksB.debuffer ? ` +deb` : ''}`);
    if (log?.dmg_a !== undefined) {
      console.log(`  Damage → A dealt ${log.dmg_a||0}  B dealt ${log.dmg_b||0}`);
    }
    console.log(`  HP after: ${nameA}=${resolved?.hp_a} ${nameB}=${resolved?.hp_b}`);

    winner = resolved?.winner;
    round++;
  }

  console.log('\n' + '═'.repeat(50));
  if (winner) {
    const winnerName = (winner === 'A' || winner === 'a') ? nameA : nameB;
    const loserName  = (winner === 'A' || winner === 'a') ? nameB : nameA;
    console.log(`🏆 WINNER: ${winnerName} defeats ${loserName} in ${round} rounds!`);
  } else {
    console.log(`⏱  Draw / timeout after ${round} rounds`);
  }
}

main().catch(e => { console.error('Error:', e.message); process.exit(1); });
