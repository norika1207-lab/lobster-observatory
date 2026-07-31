# Lobster System GitHub Source Pack

This folder is the GitHub-ready source pack for the Charenix/Lobster system. It was generated on 2026-07-31 15:08:52 CST from the live VPS, without moving or modifying the running services.

## What This Contains

- `apps/battle_arena/`: main Charenix lobster dashboard/API source and selected browser-facing static pages.
- `apps/raid_boss/`: Tiamat/Raid Boss source.
- `workers/root/`: root-level lobster workers, schedulers, and operational scripts that were historically placed under `/root`.
- `ops/bin/` and `ops/sbin/`: operational helper scripts used by services and timers.
- `ops/systemd/`: current lobster/charenix/raid systemd units and timers.
- `legacy/`: older source/docs bundles kept for traceability, not as the preferred runtime path.
- `data-manifests/`: copied files, excluded runtime data, and large/sensitive files that should not go to GitHub.

## What Is Intentionally Not Included

The live database, raw memory JSON, WAL files, logs, generated caches, tokens, and large static/runtime assets are not copied into Git. They remain on the VPS or external backup and are listed in `data-manifests/runtime-data-manifest.tsv` and `data-manifests/excluded-files.tsv`.

## Push Policy

Before pushing to GitHub:

1. Run a secret scan.
2. Confirm `git status --short` contains only source, docs, manifests, and safe configs.
3. Do not add any `.db`, `.env`, token, password, raw memory dump, or huge asset folder.

## Current Source Of Truth

The live runtime still uses `/opt/battle_arena`, `/opt/raid_boss`, `/root/lobster-*`, `/usr/local/bin/lobster-*`, `/usr/local/sbin/lobster-*`, and `/etc/systemd/system/lobster*.service|timer`. This folder is a clean consolidation target for GitHub, not yet a replacement runtime directory.
