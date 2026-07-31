# Repository Organization

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
