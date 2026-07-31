# Lobster / Charenix Anti-Crawl Defense

Updated: 2026-05-04

## Active files

- `/etc/nginx/conf.d/01-charenix-lobster-anti-crawl.conf`
  - Defines full-site scanner UA/path detection.
  - Defines Lobster private path detection.
  - Defines sensitive Lobster conversation endpoints.
  - Defines same-site referer gate for conversation data.
  - Defines rate-limit keys/zones.

- `/etc/nginx/snippets/charenix-lobster-anti-crawl.conf`
  - Enforces global scanner block.
  - Enforces Lobster crawler block.
  - Enforces direct-access block for sensitive conversation endpoints.
  - Adds noindex/noarchive/noai headers.

- `/etc/nginx/sites-enabled/charenix.conf`
  - Includes the anti-crawl snippet in the `charenix.com` server block.
  - Serves `/robots.txt` as `Disallow: /`.
  - Adds a dedicated `/api/lobster-conversation*` location with no-store and stricter limits.

- `/etc/nginx/snippets/anti-bot-block.conf`
  - `/.well-known/ai.txt` now denies crawling/scraping/training instead of inviting agents.

- `/etc/nginx/snippets/charenix-security-headers.conf`
  - Adds `X-Robots-Tag: noindex, nofollow, noarchive, nosnippet, noai, noimageai`.

## Expected behavior

- Normal browser access to Lobster Dashboard: allowed.
- `/robots.txt`: disallows the entire site.
- Direct fetch of `/api/lobster-conversation*` without a Charenix referer: `403`.
- Same-site fetch of `/api/lobster-conversation*` from Charenix pages: allowed.
- Scanner/script UAs such as curl, wget, sqlmap, python-requests: dropped with Nginx `444`.
- Search/AI crawler UAs against `/lobster`, `/lobster-lounge`, or `/api/lobster*`: dropped with Nginx `444`.

## Verification commands

Use a normal browser UA:

```bash
curl -A "Mozilla/5.0 CharenixOwnerCheck" -I https://charenix.com/lobster/dashboard/conversation-sandbox
curl -A "Mozilla/5.0 CharenixOwnerCheck" https://charenix.com/robots.txt
curl -A "Mozilla/5.0 CharenixOwnerCheck" -w "%{http_code}
" "https://charenix.com/api/lobster-conversation-interactions-dashboard?limit=1"
curl -A "Mozilla/5.0 CharenixOwnerCheck" -e "https://charenix.com/lobster/dashboard/conversation-sandbox" -w "%{http_code}
" "https://charenix.com/api/lobster-conversation-interactions-dashboard?limit=1"
curl -A "Googlebot/2.1" -w "%{http_code}
" https://charenix.com/lobster/dashboard/
```

Last known verification:

- Dashboard normal UA: `200`
- robots.txt normal UA: `200`, `Disallow: /`
- Direct conversation API without referer: `403`
- Same-site conversation API with referer: `200`
- Googlebot against Lobster Dashboard: dropped / empty reply (`444` behavior)
- curl UA against site root: dropped / empty reply (`444` behavior)

## Screen recording / browser capture policy

Added on 2026-05-04:

- `Permissions-Policy: display-capture=(), camera=(), microphone=()`
- Legacy `Feature-Policy: display-capture 'none'; camera 'none'; microphone 'none'`

This blocks browser-level Screen Capture API calls such as `navigator.mediaDevices.getDisplayMedia()` on supported browsers, so in-page capture/record/share-screen controls cannot obtain permission from the page. It does not give a website power to disable native OS screen recording or external camera capture.
