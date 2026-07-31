#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import time
from pathlib import Path


ROOT = Path("/opt/lobster-github-ready")
STAMP = time.strftime("%Y%m%d-%H%M%S")

MAX_SOURCE_BYTES = 10 * 1024 * 1024
MAX_STATIC_BYTES = 10 * 1024 * 1024
MAX_LEGACY_BYTES = 3 * 1024 * 1024

SOURCE_EXT = {
    ".py",
    ".js",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".jsx",
    ".json",
    ".md",
    ".txt",
    ".html",
    ".css",
    ".sh",
    ".toml",
    ".yaml",
    ".yml",
    ".ini",
    ".conf",
    ".service",
    ".timer",
    ".sql",
}
STATIC_EXT = {
    ".html",
    ".js",
    ".mjs",
    ".css",
    ".json",
    ".md",
    ".svg",
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".ico",
    ".txt",
    ".csv",
    ".pdf",
}
SKIP_NAMES = {
    ".git",
    "__pycache__",
    "node_modules",
    "venv",
    ".venv",
    "env",
    ".cache",
    ".pytest_cache",
    "dist",
    "build",
}
SKIP_SUFFIXES = (
    ".pyc",
    ".pyo",
    ".db",
    ".db-wal",
    ".db-shm",
    ".sqlite",
    ".sqlite3",
    ".log",
    ".out",
    ".err",
    ".bak",
    ".old",
    ".orig",
    ".tmp",
    "~",
)
SENSITIVE_RE = re.compile(
    r"(secret|token|password|passwd|credential|apikey|api_key|private[_-]?key|\.env$)",
    re.I,
)
GENERATED_JSON_RE = re.compile(r"(backup|cache|status|snapshot|report_row)", re.I)
BIG_STATIC_DIRS = {
    "avatars",
    "cards",
    "town",
    "qwen3b-atlas",
    "qwen3b-tiles",
    "Frankenstein",
    "namecard-test",
    "audio",
    "videos",
    "uploads",
    "models",
}
ALLOWED_STATIC_DIRS = {
    "lobster",
    "lobster-brains",
    "lobster-brains-v2",
    "lobster-pr-strategies",
    "assets",
    "vendor",
}
ALLOWED_STATIC_FILE_RE = re.compile(
    r"(lobster|raid|tiamat|brain|dashboard|lounge|molt|battle)",
    re.I,
)

MANIFEST: list[tuple[str, str, int, str, str]] = []
EXCLUDED: list[tuple[str, str, str]] = []
ERRORS: list[tuple[str, str]] = []


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def du_bytes(path: Path) -> int | str:
    try:
        if path.is_file():
            return path.stat().st_size
        total = 0
        for child in path.rglob("*"):
            if child.is_file() and not child.is_symlink():
                total += child.stat().st_size
        return total
    except Exception:
        return ""


def note_excluded(src: Path, reason: str) -> None:
    try:
        size = src.stat().st_size if src.is_file() else du_bytes(src)
    except Exception:
        size = ""
    EXCLUDED.append((str(src), str(size), reason))


def safe_file(path: Path, max_bytes: int, allowed_ext: set[str] | None = None) -> tuple[bool, str]:
    name = path.name
    if any(part in SKIP_NAMES for part in path.parts):
        return False, "generated/cache directory"
    if path.is_symlink():
        return False, "symlink skipped"
    if any(name.endswith(suffix) for suffix in SKIP_SUFFIXES):
        return False, "runtime/db/log/backup file"
    if SENSITIVE_RE.search(name):
        return False, "sensitive filename"
    if path.suffix == ".json" and GENERATED_JSON_RE.search(name):
        return False, "generated runtime json"
    if allowed_ext is not None and path.suffix not in allowed_ext:
        return False, "extension not selected for GitHub source pack"
    try:
        size = path.stat().st_size
    except OSError as exc:
        return False, f"stat error: {exc}"
    if size > max_bytes:
        return False, f"larger than {max_bytes} bytes"
    return True, ""


def copy_one(
    src: Path,
    dst: Path,
    reason: str,
    max_bytes: int = MAX_SOURCE_BYTES,
    allowed_ext: set[str] | None = SOURCE_EXT,
) -> None:
    try:
        if not src.exists() or not src.is_file():
            return
        ok, why = safe_file(src, max_bytes, allowed_ext)
        if not ok:
            note_excluded(src, why)
            return
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
        try:
            os.chmod(dst, src.stat().st_mode & 0o777)
        except Exception:
            pass
        MANIFEST.append(
            (
                str(src),
                str(dst.relative_to(ROOT)),
                src.stat().st_size,
                sha256_file(dst),
                reason,
            )
        )
    except Exception as exc:
        ERRORS.append((str(src), str(exc)))


def copy_tree(
    src_root: Path,
    dst_root: Path,
    reason: str,
    max_bytes: int = MAX_SOURCE_BYTES,
    allowed_ext: set[str] | None = SOURCE_EXT,
    extra_skip_dirs: set[str] | None = None,
) -> None:
    if not src_root.exists():
        return
    extra_skip_dirs = extra_skip_dirs or set()
    for path in src_root.rglob("*"):
        if not path.is_file():
            continue
        rel = path.relative_to(src_root)
        if any(part in SKIP_NAMES for part in rel.parts):
            continue
        if any(part in extra_skip_dirs for part in rel.parts):
            note_excluded(path, "large/static runtime asset directory")
            continue
        copy_one(path, dst_root / rel, reason, max_bytes=max_bytes, allowed_ext=allowed_ext)


def write_tsv(path: Path, header: tuple[str, ...], rows: list[tuple]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w") as f:
        f.write("\t".join(header) + "\n")
        for row in rows:
            f.write("\t".join(map(str, row)) + "\n")


def add_runtime(entries: list[tuple[str, str, str]], path: str, reason: str) -> None:
    p = Path(path)
    if p.exists():
        entries.append((str(p), str(du_bytes(p)), reason))


def sanitize_public_text(root: Path) -> None:
    replacements = {
        "ANTHROPIC_API_" + "KEY=": "ANTHROPIC_API_KEY placeholder ",
        "OPENAI_API_" + "KEY=": "OPENAI_API_KEY placeholder ",
        "ZENODO_" + "TOKEN=": "ZENODO_TOKEN placeholder ",
    }
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in {".md", ".txt", ".service", ".timer", ".sh", ".py", ".js"}:
            continue
        try:
            text = path.read_text(errors="ignore")
        except Exception:
            continue
        new_text = text
        for old, new in replacements.items():
            new_text = new_text.replace(old, new)
        if new_text != text:
            path.write_text(new_text)


def main() -> None:
    if ROOT.exists() and any(ROOT.iterdir()):
        backup = ROOT.with_name(ROOT.name + ".prev-" + STAMP)
        ROOT.rename(backup)
        print(f"existing staging moved to {backup}")
    ROOT.mkdir(parents=True, exist_ok=True)

    copy_tree(
        Path("/opt/battle_arena"),
        ROOT / "apps" / "battle_arena",
        "main charenix lobster app source",
        max_bytes=MAX_SOURCE_BYTES,
        allowed_ext=SOURCE_EXT,
        extra_skip_dirs={"venv", "__pycache__", "static"},
    )

    static = Path("/opt/battle_arena/static")
    if static.exists():
        for path in static.rglob("*"):
            if not path.is_file():
                continue
            rel = path.relative_to(static)
            if len(rel.parts) == 1 and not ALLOWED_STATIC_FILE_RE.search(rel.name):
                note_excluded(path, "non-lobster static root file excluded")
                continue
            if len(rel.parts) > 1 and rel.parts[0] not in ALLOWED_STATIC_DIRS:
                note_excluded(path, "non-lobster static directory excluded")
                continue
            if any(part in BIG_STATIC_DIRS or part in SKIP_NAMES for part in rel.parts):
                note_excluded(path, "large static/runtime asset excluded from GitHub pack")
                continue
            copy_one(
                path,
                ROOT / "apps" / "battle_arena" / "static" / rel,
                "selected dashboard/static source",
                max_bytes=MAX_STATIC_BYTES,
                allowed_ext=STATIC_EXT,
            )

    copy_tree(
        Path("/opt/raid_boss"),
        ROOT / "apps" / "raid_boss",
        "raid boss / tiamat source",
        max_bytes=MAX_SOURCE_BYTES,
        allowed_ext=SOURCE_EXT,
        extra_skip_dirs={"venv", "__pycache__"},
    )

    root = Path("/root")
    root_patterns = [
        "lobster*",
        "LOBSTER*",
        "run_lobsters.sh",
        "restore_lobster*.py",
        "patch_lobster*.py",
        "update_social_intelligence_cache.py",
        "sports-endpoint-watchdog.sh",
    ]
    for pattern in root_patterns:
        for src in root.glob(pattern):
            if src.is_file():
                copy_one(
                    src,
                    ROOT / "workers" / "root" / src.name,
                    "root-level lobster worker/doc",
                    max_bytes=MAX_SOURCE_BYTES,
                    allowed_ext=SOURCE_EXT,
                )
            elif src.is_dir() and src.name in {"lobster-mayor", "lobster-observatory"}:
                copy_tree(
                    src,
                    ROOT / "workers" / "root" / src.name,
                    "root-level lobster worker directory",
                    max_bytes=MAX_LEGACY_BYTES,
                    allowed_ext=SOURCE_EXT,
                )

    for base, dest in [
        (Path("/usr/local/bin"), ROOT / "ops" / "bin"),
        (Path("/usr/local/sbin"), ROOT / "ops" / "sbin"),
    ]:
        for pattern in ["lobster*", "charenix-dashboard*", "sports-*", "settlement*"]:
            for src in base.glob(pattern):
                if src.is_file():
                    copy_one(
                        src,
                        dest / src.name,
                        f"operational script from {base}",
                        max_bytes=MAX_SOURCE_BYTES,
                        allowed_ext=None,
                    )

    systemd = Path("/etc/systemd/system")
    for pattern in [
        "lobster*.service",
        "lobster*.timer",
        "charenix-dashboard*.service",
        "charenix-dashboard*.timer",
        "raid-boss.service",
        "sports-endpoint-watchdog.service",
        "sports-endpoint-watchdog.timer",
    ]:
        for src in systemd.glob(pattern):
            if src.is_file():
                copy_one(
                    src,
                    ROOT / "ops" / "systemd" / src.name,
                    "current systemd unit/timer",
                    max_bytes=2 * 1024 * 1024,
                    allowed_ext={".service", ".timer"},
                )

    copy_tree(
        Path("/root/_infra_publish"),
        ROOT / "legacy" / "infra_publish",
        "prior infra publish bundle (source/docs only)",
        max_bytes=MAX_LEGACY_BYTES,
        allowed_ext=SOURCE_EXT,
    )

    runtime_entries: list[tuple[str, str, str]] = []
    runtime_reason = "runtime data or large asset, excluded from GitHub; keep on VPS/external backup"
    for path in [
        "/opt/battle_arena/app/battle_arena.db",
        "/opt/battle_arena/app/battle_arena.db-wal",
        "/opt/battle_arena/app/battle_arena.db-shm",
        "/opt/battle_arena/app/lobster_cold_storage.db",
        "/opt/battle_arena/app/lobster_settlements.db",
        "/opt/battle_arena/app/lobster_worker_runs.db",
        "/opt/battle_arena/app/lobster_distill.db",
        "/root/lobster-memory",
        "/opt/lobster_ops/lobster_file_archive.db",
        "/opt/lobster",
        "/opt/battle_arena/static/avatars",
        "/opt/battle_arena/static/cards",
        "/opt/battle_arena/static/town",
        "/opt/battle_arena/static/qwen3b-atlas",
        "/opt/battle_arena/static/qwen3b-tiles",
        "/opt/raid_boss/app/raid_boss.db",
    ]:
        add_runtime(runtime_entries, path, runtime_reason)

    service_rows: list[str] = []
    try:
        out = subprocess.check_output(
            [
                "bash",
                "-lc",
                "systemctl list-unit-files --type=service --type=timer --no-pager | grep -Ei 'lobster|charenix-dashboard|raid-boss|sports-endpoint' || true",
            ],
            text=True,
        )
        service_rows = out.splitlines()
    except Exception as exc:
        ERRORS.append(("systemctl manifest", str(exc)))

    write_tsv(
        ROOT / "data-manifests" / "source-copy-manifest.tsv",
        ("source", "destination", "size_bytes", "sha256", "reason"),
        sorted(MANIFEST),
    )
    write_tsv(
        ROOT / "data-manifests" / "excluded-files.tsv",
        ("path", "size_bytes", "reason"),
        sorted(EXCLUDED),
    )
    write_tsv(
        ROOT / "data-manifests" / "runtime-data-manifest.tsv",
        ("path", "size_bytes", "reason"),
        runtime_entries,
    )

    systemd_manifest = ROOT / "data-manifests" / "systemd-units.txt"
    systemd_manifest.parent.mkdir(parents=True, exist_ok=True)
    systemd_manifest.write_text("\n".join(service_rows) + "\n")

    if ERRORS:
        write_tsv(ROOT / "data-manifests" / "copy-errors.tsv", ("path", "error"), ERRORS)

    sanitize_public_text(ROOT)

    (ROOT / ".gitignore").write_text(
        """# Runtime databases and write-ahead logs
*.db
*.db-*
*.sqlite
*.sqlite3
*.wal
*.shm

# Logs / generated files
*.log
*.out
*.err
*.tmp
*.bak*
*.old
*.orig
*~
__pycache__/
*.pyc
.pytest_cache/
.cache/

# Dependencies and virtualenvs
node_modules/
venv/
.venv/

# Secrets
.env
*.key
*secret*
*token*
*password*
*credential*

# Large runtime/public assets tracked by manifest only
runtime-data/
data/
large-assets/
private/
apps/battle_arena/static/avatars/
apps/battle_arena/static/cards/
apps/battle_arena/static/town/
apps/battle_arena/static/qwen3b-*/
apps/battle_arena/static/Frankenstein/
"""
    )

    (ROOT / "README.md").write_text(
        f"""# Lobster System GitHub Source Pack

This folder is the GitHub-ready source pack for the Charenix/Lobster system. It was generated on {time.strftime('%Y-%m-%d %H:%M:%S %Z')} from the live VPS, without moving or modifying the running services.

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
"""
    )

    (ROOT / "docs").mkdir(parents=True, exist_ok=True)
    (ROOT / "docs" / "REPO_ORGANIZATION.md").write_text(
        """# Repository Organization

Goal: stop scattering lobster code across the VPS and prepare a sane GitHub repository.

Rules:

- New source code belongs under this repository structure.
- Runtime data belongs in databases or external storage, not random JSON/log files across `/root` or `/opt`.
- Generated audits and debug reports must stay out of public release zips unless explicitly intended as public research data.
- Heavy databases and memory files are referenced by manifest only.
- Systemd files live under `ops/systemd/` so deployment can be reviewed in one place.

Recommended future migration:

1. Review this source pack.
2. Create a private GitHub repo first.
3. Move active scripts from `/root` into `workers/` one by one.
4. Update systemd units only after testing.
5. Keep DB/log/archive paths outside the repo.
"""
    )

    for path in ROOT.rglob("*"):
        if path.is_file() and (path.suffix == ".sh" or path.parent.name in {"bin", "sbin"}):
            try:
                path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            except Exception:
                pass

    print(
        json.dumps(
            {
                "root": str(ROOT),
                "copied_files": len(MANIFEST),
                "excluded_entries": len(EXCLUDED),
                "runtime_manifest_entries": len(runtime_entries),
                "errors": len(ERRORS),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
