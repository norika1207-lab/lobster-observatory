#!/usr/bin/env node
/**
 * Rotates all lobster MoltBook poster scripts so every lobster gets posting chances.
 * This wrapper does not generate fake records; it only runs the existing poster scripts
 * and records operational state/errors for scheduling and dashboard audits.
 */
const fs = require('fs');
const path = require('path');
const { spawnSync } = require('child_process');

const STATE_FILE = '/root/lobster-moltbook-poster-runner-state.json';
const POSTERS = [
  '/root/lobster-poster.js',
  ...Array.from({ length: 20 }, (_, i) => `/root/lobster-poster-${i + 2}.js`),
].filter(f => fs.existsSync(f));
const MAX_TRIES = Number(process.env.LOBSTER_POSTER_MAX_TRIES || 5);
const MIN_GAP_MINUTES = Number(process.env.LOBSTER_POSTER_MIN_GAP_MINUTES || 20);

function readState() {
  try { return JSON.parse(fs.readFileSync(STATE_FILE, 'utf8')); }
  catch { return { cursor: 0, last_run: null, by_persona: {}, events: [] }; }
}
function saveState(s) {
  if (!Array.isArray(s.events)) s.events = [];
  s.events = s.events.slice(-300);
  fs.writeFileSync(STATE_FILE, JSON.stringify(s, null, 2));
}
function personaFor(file) {
  try {
    const src = fs.readFileSync(file, 'utf8');
    const stateName = src.match(/'([^']+-poster-state\.json)'/)?.[1] || `${path.basename(file, '.js')}-state.json`;
    return stateName.replace('-poster-state.json', '').replace(/-/g, '_');
  } catch { return path.basename(file, '.js'); }
}
function minutesSince(iso) {
  if (!iso) return 1e9;
  const t = Date.parse(iso);
  if (!Number.isFinite(t)) return 1e9;
  return (Date.now() - t) / 60000;
}
function suspendedUntil(message) {
  const m = String(message || '').match(/suspended until ([0-9T:.Z-]+)/i);
  return m ? m[1] : null;
}

function main() {
  const state = readState();
  if (!POSTERS.length) throw new Error('no poster scripts found');
  const now = new Date().toISOString();
  state.last_run = now;

  for (let attempt = 0; attempt < Math.min(MAX_TRIES, POSTERS.length); attempt += 1) {
    const idx = (Number(state.cursor || 0) + attempt) % POSTERS.length;
    const file = POSTERS[idx];
    const persona = personaFor(file);
    const ps = state.by_persona[persona] || {};
    if (ps.suspended_until && Date.parse(ps.suspended_until) > Date.now()) {
      state.events.push({ time: now, persona, file, action: 'skip_suspended', suspended_until: ps.suspended_until });
      continue;
    }
    if (minutesSince(ps.last_success_at) < MIN_GAP_MINUTES) {
      state.events.push({ time: now, persona, file, action: 'skip_recent_success' });
      continue;
    }

    console.log(`[run] ${persona} ${file}`);
    const res = spawnSync('/usr/bin/node', [file], {
      cwd: '/root',
      encoding: 'utf8',
      timeout: 120000,
      env: { ...process.env, LOBSTER_POSTER_LLM: process.env.LOBSTER_POSTER_LLM || 'local' },
    });
    const out = `${res.stdout || ''}\n${res.stderr || ''}`.trim();
    const event = { time: now, persona, file, exit_code: res.status, signal: res.signal || null, output_tail: out.slice(-1600) };

    if (res.status === 0) {
      state.cursor = (idx + 1) % POSTERS.length;
      const posted = /\[posted\]\s+/.test(out);
      const rateLimited = /\[rate-limit\]/.test(out);
      state.by_persona[persona] = {
        ...ps,
        last_exit_code: 0,
        last_output_tail: out.slice(-600),
        ...(posted ? { last_success_at: now } : {}),
        ...(rateLimited ? { last_rate_limited_at: now } : {}),
      };
      event.action = posted ? 'posted' : rateLimited ? 'rate_limited' : 'success_no_post';
      state.events.push(event);
      saveState(state);
      console.log(out);
      console.log(`[done] ${persona} action=${event.action}`);
      return;
    }

    const until = suspendedUntil(out);
    state.by_persona[persona] = { ...ps, last_failure_at: now, last_exit_code: res.status, last_output_tail: out.slice(-600), ...(until ? { suspended_until: until } : {}) };
    event.action = until ? 'failed_suspended' : 'failed';
    if (until) event.suspended_until = until;
    state.events.push(event);
    state.cursor = (idx + 1) % POSTERS.length;
    saveState(state);
    console.log(out);
    console.log(`[try-next] ${persona} failed exit=${res.status}`);
  }
  saveState(state);
  console.log('[done] no poster succeeded this run');
  process.exit(1);
}

try { main(); }
catch (e) { console.error('[fatal]', e.message); process.exit(1); }
