#!/usr/bin/env node
// lobster-authored-poster.js
// 發 Claude(鎮長)在 session 內寫好的稿到 MoltBook(norika-oda 帳號)。
// orchestrator 在該 persona 有現成稿時改 spawn 這支,輸出 marker 對齊 parseResult()。
const fs = require('fs');
const path = require('path');
const https = require('https');

const PERSONA = process.env.LOBSTER_AUTHORED_PERSONA || 'lobster';
const DRAFT_FILE = process.env.LOBSTER_AUTHORED_FILE || '';
const SENT_DIR = process.env.LOBSTER_AUTHORED_SENT_DIR || '/root/lobster-authored-queue/sent';
const CRED = process.env.MOLTBOOK_CREDENTIALS_FILE || '/root/.config/moltbook/credentials.json';

function mbKey() {
  if (process.env.MOLTBOOK_API_KEY) return process.env.MOLTBOOK_API_KEY;
  const d = JSON.parse(fs.readFileSync(CRED, 'utf8'));
  if (d && d.api_key) return d.api_key;
  throw new Error('no moltbook api key');
}

function post(key, body) {
  return new Promise((resolve) => {
    const payload = JSON.stringify(body);
    const req = https.request({
      host: 'www.moltbook.com', path: '/api/v1/posts', method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(payload),
        Authorization: `Bearer ${key}`,
      },
    }, (res) => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => { let j = null; try { j = JSON.parse(d); } catch {} resolve([res.statusCode, j, d]); });
    });
    req.on('error', e => resolve([0, null, String(e.message)]));
    req.setTimeout(60000, () => { req.destroy(); resolve([0, null, 'timeout']); });
    req.write(payload); req.end();
  });
}

(async () => {
  if (!DRAFT_FILE || !fs.existsSync(DRAFT_FILE)) { console.log('[no-draft] nothing to post'); process.exit(0); }
  let draft;
  try { draft = JSON.parse(fs.readFileSync(DRAFT_FILE, 'utf8')); }
  catch (e) { console.error(`[fail] bad draft json: ${e.message}`); process.exit(1); }
  const title = String(draft.title || '').trim();
  const content = String(draft.content || '').trim();
  const submolt = draft.submolt || 'betting-strategies';
  if (!title || !content) { console.error('[fail] draft missing title/content'); process.exit(1); }
  console.log(`[title] ${title}`);
  console.log(`[mode]  authored(${PERSONA})`);
  const key = mbKey();
  const [code, j, raw] = await post(key, { title, content, type: 'text', submolt_name: submolt, submolt });
  if (code === 429) {
    const retry = (j && (j.retry_after_seconds || j.retryAfter)) || 60;
    console.log(`[rate-limit] ${(j && j.message) || 'rate limited'} — retry in ${retry}s`);
    process.exit(0);
  }
  if ((code === 403 || code === 423) && /suspend/i.test(raw)) {
    console.log(`suspended until ${(j && j.suspended_until) || ''}`);
    process.exit(0);
  }
  const ok = j && (j.success || j.post);
  if (!ok) { console.error(`[post-fail] code=${code} ${String(raw).slice(0, 500)}`); process.exit(1); }
  const id = (j.post && j.post.id) || j.id || '';
  console.log(`[posted] ${id}`);
  console.log(`[url]    https://www.moltbook.com/post/${id}`);
  try {
    fs.mkdirSync(SENT_DIR, { recursive: true });
    fs.renameSync(DRAFT_FILE, path.join(SENT_DIR, path.basename(DRAFT_FILE)));
  } catch {}
  process.exit(0);
})();
