# LOBSTER MANDATORY SESSION BOOTSTRAP / 龍蝦強制開場機制

This file is a hard operating rule for every Codex / Claude / AI session touching 龍蝦.

## Absolute Trigger
If the user message contains any of these terms:

- 龍蝦
- lobster
- Lobster
- Charenix lobster
- MoltBook lobster
- Tiamat
- Battlenix
- lobster dashboard
- lobster detail

then before making conclusions, file edits, remote changes, DB writes, dashboard patches, or systemd changes, the agent MUST run:

```bash
/usr/local/bin/lobster-context-guard
```

## What the guard enforces
The guard reads and prints the required markdown context from `/root`:

- `/root/LOBSTER_MANDATORY_SESSION_BOOTSTRAP.md`
- `/root/LOBSTER_README.md`
- `/root/LOBSTER_HANDOFF.md`
- `/root/LOBSTER_MASTER_README.md`
- `/root/LOBSTER_BRAIN_README.md`
- `/root/LOBSTER_COGNITIVE_README.md`
- `/root/lobster-README.md`
- any other `/root/*LOBSTER*.md` or `/root/*lobster*.md`

It also lists hot/cold DB locations and service status commands.

## Non-negotiable rules
- Do not patch first and read docs later.
- Do not create new scripts until existing pipeline ownership is identified.
- Do not use Dashboard symptoms as source of truth; check DB + service + markdown first.
- Any 龍蝦 analysis must include both DBs:
  - hot DB: `/opt/battle_arena/app/battle_arena.db`
  - cold DB: `/opt/battle_arena/app/lobster_cold_storage.db`
- Any new scheduler must have DB status table, health table, README/HANDOFF entry, and systemd verification.
- MoltBook permanent owner is `/root/lobster-moltbook-orchestrator.js`; `lobster-brain-commenter.timer` must stay disabled unless rebuilt and dry-run verified.

## Required first response behavior
When a user asks about 龍蝦, the agent should say it is loading the mandatory lobster context, then run the guard. This is required even in a new session.

## Absolute Presentation Rule - URL Only
- When showing any UI, page, dashboard, screenshot, artifact, preview, or result to Norika, provide an accessible HTTP/HTTPS URL first.
- Do not provide local filesystem paths as the primary way to view anything. Paths are not an acceptable substitute for a URL.
- This rule applies globally across all VPS projects, not only Lobster.
- If a screenshot is necessary, publish or serve it behind a URL, then provide that URL.
- Before final response, verify the URL route when possible.
