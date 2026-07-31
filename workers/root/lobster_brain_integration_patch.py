#!/usr/bin/env python3
"""
Brain Integration Patch — surgical edits to lobster-agent.js

Changes:
1. Disable livingTemplate injection at lines 13182 and 13229
2. Kill mentor feedback JSON pipeline (early return)
3. Add brain speech engine require at top
4. Disable mentor-apprentice command application
"""
import pathlib
import shutil
import sys
from datetime import datetime, timezone

AGENT = pathlib.Path("/root/lobster-agent.js")
BACKUP = AGENT.with_suffix(f".js.bak_{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_brain_integration")

def patch():
    shutil.copy2(AGENT, BACKUP)
    src = AGENT.read_text(encoding="utf-8")
    orig_len = len(src)

    patches = []

    # ─────────────────────────────────────────────────────
    # PATCH 1: Disable livingTemplate injection
    # ─────────────────────────────────────────────────────
    old1a = "  const livingTemplateReport = livingTemplateSnippetFor(personaName, channel);"
    new1a = "  // 2026-05-13: livingTemplate injection disabled (was main template pollution source). Brain speech engine replaces this.\n  const livingTemplateReport = '';"
    if old1a not in src:
        patches.append("FAIL: livingTemplate injection line not found")
    else:
        src = src.replace(old1a, new1a, 1)
        patches.append("OK: disabled livingTemplate injection (line 13182)")

    # The line `${livingTemplateReport || '（暫無可用活模板）'}` — replace the whole template line
    old1b = "${livingTemplateReport || '（暫無可用活模板）'}"
    new1b = "（活模板系統 2026-05-13 已停用 — 由腦運算引擎接管）"
    if old1b in src:
        src = src.replace(old1b, new1b, 1)
        patches.append("OK: disabled livingTemplate prompt section (line 13229)")
    else:
        patches.append("WARN: livingTemplate prompt section not found")

    # ─────────────────────────────────────────────────────
    # PATCH 2: Kill mentor feedback JSON pipeline
    # Find the function that contains "mentor feedback template" recordTemplateViolation
    # Strategy: locate the call and inject early return at top of the function
    # ─────────────────────────────────────────────────────
    # Find the function definition before this line
    # The function definition ends at line 1717-ish. Look for `async function ... feedback` or similar
    anchor2 = "  recordTemplateViolation(feedback.cmd.mentor, rewritten || feedback.feedback, 'mentor feedback template');"
    if anchor2 in src:
        # Find the function name by searching backward
        idx = src.find(anchor2)
        # Find preceding function decl
        prefix = src[:idx]
        # Find last "async function" or "function" before this index
        last_async = prefix.rfind("async function ")
        last_sync = prefix.rfind("\nfunction ")
        fn_start = max(last_async, last_sync)
        if fn_start > 0:
            # Find the opening `{` after fn declaration
            brace = src.find("{", fn_start)
            if brace > 0 and brace < idx:
                # Insert early-return after the opening brace
                injection = "\n  // 2026-05-13: mentor-apprentice system disabled per user (learned 1 week, enough). Early return.\n  if (process.env.LOBSTER_MENTORSHIP_ENABLED !== '1') return;\n"
                src = src[:brace+1] + injection + src[brace+1:]
                patches.append(f"OK: disabled mentor feedback pipeline at fn around offset {fn_start}")
            else:
                patches.append("WARN: could not find brace for mentor feedback fn")
        else:
            patches.append("WARN: could not find mentor feedback function declaration")
    else:
        patches.append("FAIL: mentor feedback violation anchor not found")

    # ─────────────────────────────────────────────────────
    # PATCH 3: Add brain speech engine require near top
    # Add right after the first `const fs = require(...)`-style line
    # ─────────────────────────────────────────────────────
    require_marker = "const fs = require('fs');"
    if require_marker in src and "lobster-brain-speech" not in src:
        injection3 = ("const fs = require('fs');\n"
                      "// 2026-05-13: Brain speech engine (8-stage neural model). Replaces livingTemplate.\n"
                      "let _brainSpeech = null;\n"
                      "try { _brainSpeech = require('/root/lobster-brain-speech'); } catch (e) { console.error('[brain-speech] load failed:', e.message); }\n"
                      "const BRAIN_SPEECH_ENABLED = process.env.LOBSTER_BRAIN_SPEECH !== '0';")
        src = src.replace(require_marker, injection3, 1)
        patches.append("OK: added brain speech engine require + flag")
    elif "lobster-brain-speech" in src:
        patches.append("SKIP: brain speech already required")
    else:
        patches.append("WARN: const fs require not found at top")

    # ─────────────────────────────────────────────────────
    # PATCH 4: Disable mentor command application + strict mentorship guard
    # Hook into applyMentorCommand or similar to short-circuit
    # ─────────────────────────────────────────────────────
    # Look for the function that mutates memory.mentorship.mentor
    anchor4 = "memory.mentorship.mentor = cmd.mentor;"
    if anchor4 in src:
        idx = src.find(anchor4)
        prefix = src[:idx]
        last_fn = max(prefix.rfind("async function "), prefix.rfind("\nfunction "))
        if last_fn > 0:
            brace = src.find("{", last_fn)
            if brace > 0 and brace < idx:
                injection4 = "\n  // 2026-05-13: mentor-apprentice command application disabled per user.\n  if (process.env.LOBSTER_MENTORSHIP_ENABLED !== '1') return;\n"
                src = src[:brace+1] + injection4 + src[brace+1:]
                patches.append(f"OK: disabled mentor command application at offset {last_fn}")

    # ─────────────────────────────────────────────────────
    # Write back
    # ─────────────────────────────────────────────────────
    AGENT.write_text(src, encoding="utf-8")
    new_len = len(src)
    patches.append(f"SUMMARY: orig_bytes={orig_len} new_bytes={new_len} diff={new_len-orig_len:+d}")
    patches.append(f"BACKUP: {BACKUP}")

    print("\n".join(patches))

if __name__ == "__main__":
    patch()
