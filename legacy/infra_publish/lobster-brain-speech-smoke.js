#!/usr/bin/env node
/**
 * Smoke test for brain speech engine.
 * Uses stub LLM (returns a canned response) to verify all 8 stages run.
 */
const fs = require('fs');
const brain = require('/root/lobster-brain-speech');

const stubLlm = async (prompt, opts) => {
  // Pretend the LLM returned something — print the prompt so we can inspect it
  process.stderr.write('--- PROMPT (first 1500 chars) ---\n');
  process.stderr.write(prompt.slice(0, 1500) + (prompt.length > 1500 ? '... [+' + (prompt.length - 1500) + ' more]' : ''));
  process.stderr.write('\n--- END PROMPT ---\n');
  // Return a sentence that will likely fail purity (so we see STAGE 7 work)
  return 'NVIDIA 系統的下注紀律在 60% 勝率下仍然會出錯,主因是資金過度集中於電競單一領域,需要驗證跨運動分布。';
};

async function main() {
  const persona = process.argv[2] || 'hexclaw';
  const mode = process.argv[3] || 'free';
  const channel = process.argv[4] || null;

  const mem = JSON.parse(fs.readFileSync(`/root/lobster-memory/${persona}.json`, 'utf8'));

  console.log(`\n=== Smoke test: persona=${persona} mode=${mode} channel=${channel || '(none)'} ===\n`);

  const result = await brain.speak({
    persona, memory: mem, channel, mode,
    llmCallFn: stubLlm,
    maxRegens: 1,
  });

  console.log('\n=== RESULT ===');
  console.log(JSON.stringify(result, null, 2));
}

main().catch(e => { console.error('FATAL:', e); process.exit(1); });
