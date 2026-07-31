#!/usr/bin/env node
/**
 * MoltBook posting orchestrator.
 *
 * Single owner for lobster posting:
 * - rotates all poster scripts
 * - classifies posted / rate_limited / suspended / failed
 * - writes DB status tables for dashboard and audits
 * - never counts a rate-limit as success
 * - never calls the duplicate-prone hot-thread commenter
 */
const fs = require('fs');
const path = require('path');
const { spawnSync, execFileSync } = require('child_process');
const https = require('https');

const DB = process.env.LOBSTER_DB || '/opt/battle_arena/app/battle_arena.db';
const COLD_DB = process.env.LOBSTER_COLD_DB || '/opt/battle_arena/app/lobster_cold_storage.db';
const STATE_FILE = '/root/lobster-moltbook-orchestrator-state.json';
const LOCK_FILE = '/tmp/lobster-moltbook-orchestrator.lock';
const LOG_FILE = '/tmp/lobster-moltbook-orchestrator.log';
const MOLTBOOK_STATUS_URL = 'https://www.moltbook.com/api/v1/agents/status';
const MOLTBOOK_CREDENTIALS_FILE = process.env.MOLTBOOK_CREDENTIALS_FILE || '/root/.config/moltbook/credentials.json';
const POSTERS = [
  '/root/lobster-poster.js',
  ...Array.from({ length: 20 }, (_, i) => `/root/lobster-poster-${i + 2}.js`),
].filter(f => fs.existsSync(f));
const AUTHORED_DIR = process.env.LOBSTER_AUTHORED_DIR || '/root/lobster-authored-queue/ready';
const AUTHORED_POSTER = '/root/lobster-authored-poster.js';
const AUTHORED_ONLY = process.env.LOBSTER_AUTHORED_ONLY === '1';
function nextAuthoredDraft(persona) {
  try {
    return fs.readdirSync(AUTHORED_DIR)
      .filter(f => f.endsWith('.json') && (f.startsWith(persona + '.') || f.startsWith('any.')))
      .sort()
      .map(f => path.join(AUTHORED_DIR, f))[0] || null;
  } catch { return null; }
}

const MAX_TRIES = Number(process.env.LOBSTER_MOLTBOOK_MAX_TRIES || 1);
const MIN_PERSONA_GAP_MINUTES = Number(process.env.LOBSTER_MOLTBOOK_PERSONA_GAP_MINUTES || 180);
const POST_TIMEOUT_MS = Number(process.env.LOBSTER_MOLTBOOK_POST_TIMEOUT_MS || 120000);
const GLOBAL_RATE_LIMIT_COOLDOWN_SECONDS = Number(process.env.LOBSTER_MOLTBOOK_GLOBAL_RATE_LIMIT_COOLDOWN_SECONDS || 600);
const MAX_GLOBAL_RATE_LIMIT_COOLDOWN_SECONDS = Number(process.env.LOBSTER_MOLTBOOK_MAX_GLOBAL_RATE_LIMIT_COOLDOWN_SECONDS || 21600);

function log(line) {
  const msg = `${new Date().toISOString()} ${line}`;
  console.log(line);
  try { fs.appendFileSync(LOG_FILE, msg + '\n'); } catch {}
}

function sleepMs(ms) {
  Atomics.wait(new Int32Array(new SharedArrayBuffer(4)), 0, 0, ms);
}

function shSql(db, sql) {
  let last = null;
  for (let attempt = 1; attempt <= 5; attempt += 1) {
    try {
      execFileSync('/usr/bin/sqlite3', ['-cmd', 'PRAGMA busy_timeout=15000;', db, sql], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });
      return true;
    } catch (e) {
      last = e;
      const msg = String(e.stderr || e.message);
      if (!/database is locked|busy/i.test(msg)) break;
      sleepMs(250 * attempt);
    }
  }
  log(`[db-fail] ${db} ${String(last?.stderr || last?.message || 'unknown').slice(0, 400)}`);
  return false;
}

function q(v) {
  if (v === null || v === undefined) return 'NULL';
  return `'${String(v).replace(/'/g, "''")}'`;
}

function loadMoltbookApiKey() {
  if (process.env.MOLTBOOK_API_KEY) return process.env.MOLTBOOK_API_KEY;
  try {
    const data = JSON.parse(fs.readFileSync(MOLTBOOK_CREDENTIALS_FILE, 'utf8'));
    if (data && data.api_key) return data.api_key;
  } catch {}
  return null;
}

function fetchMoltbookStatus(apiKey) {
  return new Promise((resolve, reject) => {
    const req = https.request(MOLTBOOK_STATUS_URL, {
      method: 'GET',
      timeout: 15000,
      headers: { Authorization: `Bearer ${apiKey}` },
    }, res => {
      let body = '';
      res.setEncoding('utf8');
      res.on('data', chunk => { body += chunk; });
      res.on('end', () => {
        let json = null;
        try { json = JSON.parse(body); } catch {}
        resolve({ statusCode: res.statusCode, json, body: body.slice(0, 500) });
      });
    });
    req.on('timeout', () => req.destroy(new Error('MoltBook status timeout')));
    req.on('error', reject);
    req.end();
  });
}

function recordHealth(status, summary, details) {
  const now = new Date().toISOString();
  const sql = `INSERT INTO lobster_moltbook_health (status, summary, details_json, created_at)
    VALUES (${q(status)}, ${q(summary)}, ${q(JSON.stringify(details || {}))}, ${q(now)});`;
  shSql(DB, sql);
  shSql(COLD_DB, sql);
}

async function ensureMoltbookClaimReady(state) {
  const apiKey = loadMoltbookApiKey();
  if (!apiKey) {
    state.waiting_claim = true;
    state.claim_status = 'missing_key';
    recordHealth('waiting_claim', 'MoltBook key missing; posting paused', {});
    return false;
  }
  if (!state.waiting_claim && state.claim_status === 'claimed') return true;
  try {
    const res = await fetchMoltbookStatus(apiKey);
    const status = res.json && res.json.status ? String(res.json.status) : `http_${res.statusCode}`;
    state.claim_status = status;
    state.claim_agent_name = res.json && res.json.name ? res.json.name : state.claim_agent_name;
    state.claim_checked_at = new Date().toISOString();
    if (status === 'claimed') {
      state.waiting_claim = false;
      recordHealth('ready', 'MoltBook key claimed; posting may resume', { agent: state.claim_agent_name, status });
      return true;
    }
    state.waiting_claim = true;
    recordHealth('waiting_claim', 'MoltBook key pending owner claim; posting paused', { agent: state.claim_agent_name, status });
    return false;
  } catch (e) {
    state.waiting_claim = true;
    state.claim_status = 'status_check_failed';
    state.claim_checked_at = new Date().toISOString();
    recordHealth('waiting_claim', 'MoltBook claim status check failed; posting paused', { error: String(e.message || e).slice(0, 300) });
    return false;
  }
}

function initDb() {
  const ddl = `
CREATE TABLE IF NOT EXISTS lobster_moltbook_poster_status (
  persona TEXT PRIMARY KEY,
  poster_file TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  last_attempt_at TEXT,
  last_success_at TEXT,
  last_post_id TEXT,
  last_post_title TEXT,
  last_url TEXT,
  last_status TEXT,
  suspended_until TEXT,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  rate_limited_count INTEGER NOT NULL DEFAULT 0,
  failure_count INTEGER NOT NULL DEFAULT 0,
  last_message TEXT,
  updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lobster_moltbook_posting_runs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL,
  persona TEXT NOT NULL,
  poster_file TEXT NOT NULL,
  status TEXT NOT NULL,
  post_id TEXT,
  post_title TEXT,
  url TEXT,
  exit_code INTEGER,
  suspended_until TEXT,
  retry_after_seconds INTEGER,
  output_tail TEXT,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS lobster_moltbook_health (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  status TEXT NOT NULL,
  summary TEXT,
  details_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);`;
  shSql(DB, ddl);
  shSql(COLD_DB, ddl);
}

function insertRun(db, event) {
  const sql = `INSERT INTO lobster_moltbook_posting_runs
    (run_id, persona, poster_file, status, post_id, post_title, url, exit_code, suspended_until, retry_after_seconds, output_tail, created_at)
    VALUES (${q(event.run_id)}, ${q(event.persona)}, ${q(event.poster_file)}, ${q(event.status)}, ${q(event.post_id)}, ${q(event.post_title)}, ${q(event.url)}, ${Number.isFinite(event.exit_code) ? event.exit_code : 'NULL'}, ${q(event.suspended_until)}, ${Number.isFinite(event.retry_after_seconds) ? event.retry_after_seconds : 'NULL'}, ${q(event.output_tail)}, ${q(event.created_at)});`;
  shSql(db, sql);
}

function updateStatus(db, event) {
  const successFields = event.status === 'posted'
    ? `last_success_at=excluded.last_success_at,last_post_id=excluded.last_post_id,last_post_title=excluded.last_post_title,last_url=excluded.last_url,consecutive_failures=0`
    : `consecutive_failures=lobster_moltbook_poster_status.consecutive_failures+1`;
  const rateInc = event.status === 'rate_limited' ? 1 : 0;
  const failInc = ['failed', 'suspended', 'timeout', 'no_post'].includes(event.status) ? 1 : 0;
  const sql = `INSERT INTO lobster_moltbook_poster_status
    (persona, poster_file, enabled, last_attempt_at, last_success_at, last_post_id, last_post_title, last_url, last_status, suspended_until, consecutive_failures, rate_limited_count, failure_count, last_message, updated_at)
    VALUES (${q(event.persona)}, ${q(event.poster_file)}, 1, ${q(event.created_at)}, ${event.status === 'posted' ? q(event.created_at) : 'NULL'}, ${q(event.post_id)}, ${q(event.post_title)}, ${q(event.url)}, ${q(event.status)}, ${q(event.suspended_until)}, ${event.status === 'posted' ? 0 : 1}, ${rateInc}, ${failInc}, ${q(event.message)}, ${q(event.created_at)})
    ON CONFLICT(persona) DO UPDATE SET
      poster_file=excluded.poster_file,
      last_attempt_at=excluded.last_attempt_at,
      last_status=excluded.last_status,
      suspended_until=excluded.suspended_until,
      rate_limited_count=lobster_moltbook_poster_status.rate_limited_count+${rateInc},
      failure_count=lobster_moltbook_poster_status.failure_count+${failInc},
      last_message=excluded.last_message,
      updated_at=excluded.updated_at,
      ${successFields};`;
  shSql(db, sql);
}

function record(event) {
  insertRun(DB, event); updateStatus(DB, event);
  insertRun(COLD_DB, event); updateStatus(COLD_DB, event);
}

function readState() {
  try { return JSON.parse(fs.readFileSync(STATE_FILE, 'utf8')); }
  catch { return { cursor: 0, by_persona: {}, events: [] }; }
}
function saveState(s) {
  if (!Array.isArray(s.events)) s.events = [];
  s.events = s.events.slice(-500);
  fs.writeFileSync(STATE_FILE, JSON.stringify(s, null, 2));
}

function newestIncidentAt(state, names) {
  const events = Array.isArray(state.events) ? state.events : [];
  let best = null;
  for (const e of events) {
    if (!e || !names.includes(e.status)) continue;
    const t = Date.parse(e.created_at || '');
    if (Number.isFinite(t) && (!best || t > best)) best = t;
  }
  return best;
}

function shouldUseSafePost(state) {
  // Disabled: the old rule (any failure in the last 7 days -> dry recovery-ledger
  // mode) strangled all real content into 200+ identical placeholder posts. Real
  // MoltBook suspensions/rate-limits are already handled via global_suspended_until
  // / global_rate_limited_until. Authored posts now supply the content.
  return false;
}

function personaFor(file) {
  const src = fs.readFileSync(file, 'utf8');
  const stateName = src.match(/'([^']+-poster-state\.json)'/)?.[1] || `${path.basename(file, '.js')}-state.json`;
  return stateName.replace('-poster-state.json', '').replace(/-/g, '_');
}
function secondsUntil(iso) {
  const t = Date.parse(iso || '');
  if (!Number.isFinite(t)) return 0;
  return Math.max(0, Math.ceil((t - Date.now()) / 1000));
}
function isoAfter(seconds) {
  return new Date(Date.now() + Math.max(1, Number(seconds) || 1) * 1000).toISOString();
}
function cooldownSeconds(retry) {
  const raw = Number.isFinite(retry) && retry > 0 ? retry + 30 : GLOBAL_RATE_LIMIT_COOLDOWN_SECONDS;
  return Math.min(MAX_GLOBAL_RATE_LIMIT_COOLDOWN_SECONDS, Math.max(60, raw));
}
function minutesSince(iso) {
  if (!iso) return 1e9;
  const t = Date.parse(iso);
  return Number.isFinite(t) ? (Date.now() - t) / 60000 : 1e9;
}
function parseResult(out, exitCode, timedOut) {
  const tail = String(out || '').slice(-2200);
  const suspended = tail.match(/suspended until ([0-9T:.Z-]+)/i)?.[1] || null;
  const retry = tail.match(/retry in\s+(\d+)s/i)?.[1];
  const postId = tail.match(/\[posted\]\s+([a-zA-Z0-9-]+)/)?.[1] || null;
  const url = tail.match(/\[url\]\s+(https?:\/\/[^\s]+)/)?.[1] || (postId ? `https://www.moltbook.com/post/${postId}` : null);
  const title = tail.match(/\[title\]\s+(.+)/)?.[1]?.trim()?.slice(0, 180) || null;
  if (timedOut) return { status: 'timeout', postId, title, url, suspended, retry: null, tail };
  if (postId) return { status: 'posted', postId, title, url, suspended, retry: null, tail };
  if (suspended) return { status: 'suspended', postId, title, url, suspended, retry: null, tail };
  if (/\[rate-limit\]/.test(tail) || /Rate limit exceeded/i.test(tail)) return { status: 'rate_limited', postId, title, url, suspended, retry: retry ? Number(retry) : null, tail };
  if (exitCode === 0) return { status: 'no_post', postId, title, url, suspended, retry: null, tail };
  return { status: 'failed', postId, title, url, suspended, retry: null, tail };
}

function acquireLock() {
  try {
    const fd = fs.openSync(LOCK_FILE, 'wx');
    fs.writeFileSync(fd, String(process.pid));
    return fd;
  } catch {
    log('[skip] lock exists');
    process.exit(0);
  }
}
function releaseLock(fd) {
  try { fs.closeSync(fd); } catch {}
  try { fs.unlinkSync(LOCK_FILE); } catch {}
}

async function main() {
  const lock = acquireLock();
  try {
    initDb();
    if (!POSTERS.length) throw new Error('no poster scripts found');
    const state = readState();
    if (!(await ensureMoltbookClaimReady(state))) {
      saveState(state);
      log(`[waiting-claim] MoltBook claim status=${state.claim_status || 'unknown'}; posting paused`);
      releaseLock(lock);
      process.exit(0);
    }
    const runId = `moltbook-${Date.now()}`;
    let sawRateLimit = false;
    if (state.global_suspended_until && secondsUntil(state.global_suspended_until) > 0) {
      log(`[blocked] MoltBook agent suspended for ${secondsUntil(state.global_suspended_until)}s until ${state.global_suspended_until}`);
      recordHealth('suspended', 'MoltBook agent globally suspended; posting paused', { until: state.global_suspended_until });
      saveState(state);
      releaseLock(lock);
      process.exit(0);
    }
    if (state.global_rate_limited_until && secondsUntil(state.global_rate_limited_until) > 0) {
      log(`[blocked] MoltBook global cooldown active for ${secondsUntil(state.global_rate_limited_until)}s until ${state.global_rate_limited_until}`);
      saveState(state);
      releaseLock(lock);
      process.exit(0);
    }

    for (let attempt = 0; attempt < Math.min(Math.max(1, MAX_TRIES), POSTERS.length); attempt += 1) {
      const idx = (Number(state.cursor || 0) + attempt) % POSTERS.length;
      const file = POSTERS[idx];
      const persona = personaFor(file);
      const ps = state.by_persona[persona] || {};
      const now = new Date().toISOString();

      if (ps.suspended_until && Date.parse(ps.suspended_until) > Date.now()) {
        const event = { run_id: runId, persona, poster_file: file, status: 'suspended', suspended_until: ps.suspended_until, exit_code: null, output_tail: 'skip existing suspension', message: `suspended until ${ps.suspended_until}`, created_at: now };
        record(event);
        state.events.push(event);
        continue;
      }
      if (minutesSince(ps.last_success_at) < MIN_PERSONA_GAP_MINUTES) {
        const event = { run_id: runId, persona, poster_file: file, status: 'skip_recent_success', exit_code: null, output_tail: 'skip persona gap', message: 'persona recently posted', created_at: now };
        insertRun(DB, event); insertRun(COLD_DB, event);
        state.events.push(event);
        continue;
      }

      const authoredDraft = nextAuthoredDraft(persona);
      if (!authoredDraft && AUTHORED_ONLY) {
        state.events.push({ run_id: runId, persona, poster_file: file, status: 'skip_no_draft', exit_code: null, output_tail: '', created_at: now });
        continue;
      }
      log(`[run] ${persona} ${authoredDraft ? 'authored:' + path.basename(authoredDraft) : file}`);
      const res = spawnSync('/usr/bin/node', [authoredDraft ? AUTHORED_POSTER : file], {
        cwd: '/root',
        encoding: 'utf8',
        timeout: POST_TIMEOUT_MS,
        env: {
          ...process.env,
          LOBSTER_POSTER_LLM: process.env.LOBSTER_POSTER_LLM || 'local',
          LOBSTER_ALLOW_EXTERNAL_LLM: process.env.LOBSTER_ALLOW_EXTERNAL_LLM === '1' ? '1' : '0',
          LOBSTER_POST_RUN_ID: `${runId}-${persona}-${attempt}`,
          LOBSTER_MOLTBOOK_SAFE_POST: shouldUseSafePost(state) ? '1' : (process.env.LOBSTER_MOLTBOOK_SAFE_POST || ''),
          LOBSTER_AUTHORED_FILE: authoredDraft || '',
          LOBSTER_AUTHORED_PERSONA: persona,
        },
      });
      const out = `${res.stdout || ''}\n${res.stderr || ''}`.trim();
      const parsed = parseResult(out, res.status, Boolean(res.error && String(res.error.message).includes('ETIMEDOUT')));
      const event = {
        run_id: runId,
        persona,
        poster_file: file,
        status: parsed.status,
        post_id: parsed.postId,
        post_title: parsed.title,
        url: parsed.url,
        exit_code: Number.isFinite(res.status) ? res.status : null,
        suspended_until: parsed.suspended,
        retry_after_seconds: parsed.retry,
        output_tail: parsed.tail,
        message: parsed.status,
        created_at: now,
      };
      record(event);
      state.cursor = (idx + 1) % POSTERS.length;
      state.by_persona[persona] = {
        ...ps,
        last_attempt_at: now,
        last_status: parsed.status,
        last_output_tail: parsed.tail.slice(-800),
        ...(parsed.status === 'posted' ? { last_success_at: now, last_post_id: parsed.postId, last_url: parsed.url } : {}),
        ...(parsed.status === 'suspended' ? { suspended_until: parsed.suspended } : {}),
        ...(parsed.status === 'rate_limited' ? { last_rate_limited_at: now } : {}),
      };
      state.events.push(event);
      saveState(state);
      log(`[result] ${persona} status=${parsed.status}${parsed.postId ? ` post=${parsed.postId}` : ''}`);

      if (parsed.status === 'posted') {
        state.global_suspended_until = null;
        releaseLock(lock);
        return;
      }
      if (parsed.status === 'suspended' && parsed.suspended) {
        state.global_suspended_until = parsed.suspended;
        recordHealth('suspended', 'MoltBook agent globally suspended; posting paused', { until: parsed.suspended, persona });
        log(`[suspended] global MoltBook suspension until ${parsed.suspended}`);
        saveState(state);
        break;
      }
      if (parsed.status === 'rate_limited') {
        sawRateLimit = true;
        const until = isoAfter(cooldownSeconds(parsed.retry));
        state.global_rate_limited_until = until;
        state.global_rate_limited_retry_after_seconds = parsed.retry || null;
        log(`[cooldown] global MoltBook rate limit until ${until}`);
        saveState(state);
        break;
      }
    }
    saveState(state);
    if (sawRateLimit) {
      log('[done] no successful post: global MoltBook rate limit enforced');
      releaseLock(lock);
      process.exit(0);
    }
    log('[done] no successful post this run');
    releaseLock(lock);
    process.exit(0);
  } catch (e) {
    log(`[fatal] ${e.message}`);
    releaseLock(lock);
    process.exit(1);
  }
}

main();
