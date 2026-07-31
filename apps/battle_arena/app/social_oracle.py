"""Cross-site social result oracle.

Uses public, no-auth sources to get race/match/fight results faster than
official APIs. Designed to be a pre-settlement signal — callers still
require cross-source consensus before paying out.

Sources (all free, no API key):
  - Reddit RSS (per-subreddit /new.rss) — post-event threads hit within minutes
  - Wikipedia recent revisions — updated by fans during/right after events
  - Nitter mirrors (Twitter/X fallback) — mostly dead as of 2026-04 but tried
  - fxtwitter oEmbed (single-tweet resolver if caller has a URL)

Returns structured signals:
  {"source": str, "url": str, "text": str, "ts": iso, "winner_hint": "a"|"b"|""}

Callers build their own _detect_winner() from bet-specific domain knowledge
and feed signals into consensus().
"""
import asyncio
import re
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Iterable, Optional

import httpx

UA = "Mozilla/5.0 (compatible; claw-social-oracle/1.0)"
DEFAULT_TIMEOUT = 8.0


@dataclass
class Signal:
    source: str
    url: str
    text: str
    ts: Optional[datetime]
    winner_hint: str = ""   # 'a' | 'b' | ''
    confidence: float = 0.5

    def as_dict(self):
        return {
            "source": self.source,
            "url": self.url,
            "text": self.text[:500],
            "ts": self.ts.isoformat() if self.ts else None,
            "winner_hint": self.winner_hint,
            "confidence": self.confidence,
        }


# ───────────────────────── HTTP ─────────────────────────

_client: Optional[httpx.AsyncClient] = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=DEFAULT_TIMEOUT,
            headers={"User-Agent": UA},
            follow_redirects=True,
        )
    return _client


# ───────────────────────── Reddit ─────────────────────────

_ATOM_ENTRY_RE = re.compile(r"<entry>(.*?)</entry>", re.DOTALL)
_ATOM_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.DOTALL)
_ATOM_LINK_RE = re.compile(r'<link[^>]*href="([^"]+)"')
_ATOM_UPDATED_RE = re.compile(r"<updated>(.*?)</updated>")


async def reddit_new(subreddit: str, limit: int = 30) -> list[dict]:
    """Return recent posts from r/<subreddit>/new.rss (Atom feed)."""
    c = _get_client()
    url = f"https://www.reddit.com/r/{subreddit}/new.rss?limit={limit}"
    try:
        r = await c.get(url)
        if r.status_code != 200 or not r.text:
            return []
        entries = _ATOM_ENTRY_RE.findall(r.text)[:limit]
        out = []
        for e in entries:
            title_m = _ATOM_TITLE_RE.search(e)
            link_m = _ATOM_LINK_RE.search(e)
            updated_m = _ATOM_UPDATED_RE.search(e)
            if not title_m:
                continue
            title = re.sub(r"<!\[CDATA\[|\]\]>", "", title_m.group(1)).strip()
            ts = None
            if updated_m:
                try:
                    ts = datetime.fromisoformat(updated_m.group(1).replace("Z", "+00:00"))
                except Exception:
                    pass
            out.append({
                "title": title,
                "url": link_m.group(1) if link_m else "",
                "ts": ts,
            })
        return out
    except Exception:
        return []


# ───────────────────────── Wikipedia ─────────────────────────

async def wikipedia_section(page: str, lang: str = "en") -> str:
    """Fetch plain-text summary of a Wikipedia page (first 2000 chars).
    Good for: F1 race pages ('2026 Chinese Grand Prix') which update within minutes.
    """
    c = _get_client()
    url = f"https://{lang}.wikipedia.org/w/api.php"
    params = {
        "action": "query", "prop": "extracts", "explaintext": "1",
        "exlimit": "1", "titles": page, "format": "json", "redirects": "1",
    }
    try:
        r = await c.get(url, params=params)
        if r.status_code != 200:
            return ""
        data = r.json()
        pages = data.get("query", {}).get("pages", {})
        for _, p in pages.items():
            if "extract" in p:
                return p["extract"][:2000]
        return ""
    except Exception:
        return ""


# ───────────────────────── Nitter (fallback) ─────────────────────────

NITTER_HOSTS = [
    "nitter.privacydev.net",
    "nitter.poast.org",
    "nitter.net",
    "nitter.tiekoetter.com",
    "xcancel.com",
]

_NITTER_ITEM_RE = re.compile(r"<item>(.*?)</item>", re.DOTALL)
_NITTER_TITLE_RE = re.compile(r"<title><!\[CDATA\[(.*?)\]\]></title>")
_NITTER_TITLE_PLAIN_RE = re.compile(r"<title>(.*?)</title>")
_NITTER_PUB_RE = re.compile(r"<pubDate>(.*?)</pubDate>")
_NITTER_LINK_RE = re.compile(r"<link>(.*?)</link>")


async def nitter_user(username: str, limit: int = 20) -> list[dict]:
    """Fetch a user's recent tweets via Nitter RSS (mostly dead 2026-04)."""
    c = _get_client()
    for host in NITTER_HOSTS:
        url = f"https://{host}/{username}/rss"
        try:
            r = await c.get(url, timeout=5)
            if r.status_code != 200 or "<rss" not in r.text.lower():
                continue
            items = _NITTER_ITEM_RE.findall(r.text)[:limit]
            out = []
            for it in items:
                tm = _NITTER_TITLE_RE.search(it) or _NITTER_TITLE_PLAIN_RE.search(it)
                pm = _NITTER_PUB_RE.search(it)
                lm = _NITTER_LINK_RE.search(it)
                if not tm:
                    continue
                title = tm.group(1).strip()
                ts = None
                if pm:
                    try:
                        from email.utils import parsedate_to_datetime
                        ts = parsedate_to_datetime(pm.group(1))
                    except Exception:
                        pass
                out.append({"title": title, "url": lm.group(1) if lm else "", "ts": ts})
            return out
        except Exception:
            continue
    return []


# ───────────────────────── Helpers ─────────────────────────

def name_mentioned(name: str, text: str, min_parts: int = 2) -> bool:
    """True if the 'significant' parts of `name` (len>2) appear in `text`."""
    parts = [p for p in name.lower().split() if len(p) > 2]
    if not parts:
        return False
    tl = text.lower()
    hits = sum(1 for p in parts[:min_parts] if p in tl)
    return hits >= min(min_parts, len(parts))


_VICTORY_VERBS = re.compile(
    r"\b(wins?|winning|won|beats?|beat|defeats?|defeated|takes?|taking|claims?|claimed|"
    r"captures?|captured|crowned|triumphs?|triumphed|tops?|topped|leads?|led|"
    r"ko|kos|ko'd|tko|tko'd|submits?|submitted|knocks? out|knocked out|"
    r"sweeps?|swept|finishes?|finished first|victory|champions?)\b",
    re.IGNORECASE,
)


def victory_in(text: str) -> bool:
    """Heuristic: does text describe a victory/result?"""
    return bool(_VICTORY_VERBS.search(text))


def detect_winner(text: str, name_a: str, name_b: str) -> str:
    """Return 'a' or 'b' or ''. Uses word order heuristic:
    whoever appears first AND is next to a victory verb wins.
    Good enough for post-race headlines like 'Verstappen wins Chinese GP over Norris'.
    """
    tl = text.lower()
    a_parts = [p for p in name_a.lower().split() if len(p) > 2]
    b_parts = [p for p in name_b.lower().split() if len(p) > 2]
    if not a_parts or not b_parts:
        return ""
    a_pos = min((tl.find(p) for p in a_parts if p in tl), default=-1)
    b_pos = min((tl.find(p) for p in b_parts if p in tl), default=-1)
    if a_pos < 0 and b_pos < 0:
        return ""
    if a_pos >= 0 and b_pos < 0:
        return "a" if victory_in(text) else ""
    if b_pos >= 0 and a_pos < 0:
        return "b" if victory_in(text) else ""
    # Both mentioned: passive voice check
    pm = re.search(r"\b(?:beaten|defeated|outpointed|dominated|stopped)\b[^.]{0,40}?\bby\b", tl)
    if pm:
        before = tl[:pm.start()]
        after = tl[pm.end():]
        a_before = any(p in before for p in a_parts)
        b_before = any(p in before for p in b_parts)
        a_after = any(p in after for p in a_parts)
        b_after = any(p in after for p in b_parts)
        if a_before and b_after and not b_before:
            return "b"
        if b_before and a_after and not a_before:
            return "a"
    # Active voice default: whoever mentioned first wins
    if victory_in(text):
        return "a" if a_pos < b_pos else "b"
    return ""


# ───────────────────────── Consensus ─────────────────────────

def consensus(signals: Iterable[Signal], min_sources: int = 2, min_agreement: float = 0.67) -> dict:
    """Aggregate winner hints. Returns {"winner": "a"|"b"|None, "confidence": 0..1, "n": int, "sources": [...]}.

    Rules:
      - Must have >= min_sources distinct sources
      - >= min_agreement of them must agree on same winner
      - Otherwise return None
    """
    sigs = [s for s in signals if s.winner_hint in ("a", "b")]
    if len(sigs) < min_sources:
        return {"winner": None, "confidence": 0.0, "n": len(sigs), "sources": [s.source for s in sigs]}
    distinct_sources = {s.source for s in sigs}
    if len(distinct_sources) < min_sources:
        return {"winner": None, "confidence": 0.0, "n": len(sigs), "sources": list(distinct_sources)}
    a_votes = sum(s.confidence for s in sigs if s.winner_hint == "a")
    b_votes = sum(s.confidence for s in sigs if s.winner_hint == "b")
    total = a_votes + b_votes
    if total == 0:
        return {"winner": None, "confidence": 0.0, "n": len(sigs), "sources": list(distinct_sources)}
    a_share = a_votes / total
    if a_share >= min_agreement:
        return {"winner": "a", "confidence": a_share, "n": len(sigs), "sources": list(distinct_sources)}
    if (1 - a_share) >= min_agreement:
        return {"winner": "b", "confidence": 1 - a_share, "n": len(sigs), "sources": list(distinct_sources)}
    return {"winner": None, "confidence": max(a_share, 1 - a_share), "n": len(sigs), "sources": list(distinct_sources)}


# ───────────────────────── High-level scan ─────────────────────────

async def scan_subreddits(subreddits: list[str], name_a: str, name_b: str,
                          since: Optional[datetime] = None) -> list[Signal]:
    """Scan multiple subreddits for posts mentioning both names with a victory verb."""
    all_sigs: list[Signal] = []

    async def _one(sub: str):
        posts = await reddit_new(sub, limit=30)
        for p in posts:
            if since and p.get("ts") and p["ts"] < since:
                continue
            text = p["title"]
            if not victory_in(text):
                continue
            if not (name_mentioned(name_a, text) or name_mentioned(name_b, text)):
                continue
            hint = detect_winner(text, name_a, name_b)
            all_sigs.append(Signal(
                source=f"reddit:r/{sub}",
                url=p.get("url", ""),
                text=text,
                ts=p.get("ts"),
                winner_hint=hint,
                confidence=0.6 if hint else 0.3,
            ))

    await asyncio.gather(*[_one(s) for s in subreddits], return_exceptions=True)
    return all_sigs


async def scan_twitter_accounts(accounts: list[str], name_a: str, name_b: str,
                                since: Optional[datetime] = None) -> list[Signal]:
    """Nitter-based scan of given journalist/official accounts. Often fails."""
    all_sigs: list[Signal] = []

    async def _one(acc: str):
        tweets = await nitter_user(acc, limit=20)
        for t in tweets:
            if since and t.get("ts") and t["ts"] < since:
                continue
            text = t["title"]
            if not victory_in(text):
                continue
            if not (name_mentioned(name_a, text) and name_mentioned(name_b, text)):
                continue
            hint = detect_winner(text, name_a, name_b)
            all_sigs.append(Signal(
                source=f"x:@{acc}",
                url=t.get("url", ""),
                text=text,
                ts=t.get("ts"),
                winner_hint=hint,
                confidence=0.75 if hint else 0.4,   # journalists trusted higher than reddit
            ))

    await asyncio.gather(*[_one(a) for a in accounts], return_exceptions=True)
    return all_sigs


async def scan_wikipedia(page: str, name_a: str, name_b: str) -> Optional[Signal]:
    """Read the Wikipedia page extract; winner often in first 500 chars."""
    text = await wikipedia_section(page)
    if not text:
        return None
    if not (name_mentioned(name_a, text) and name_mentioned(name_b, text)):
        return None
    if not victory_in(text):
        return None
    hint = detect_winner(text[:1000], name_a, name_b)
    return Signal(
        source=f"wikipedia:{page}",
        url=f"https://en.wikipedia.org/wiki/{page.replace(' ', '_')}",
        text=text[:500],
        ts=datetime.now(timezone.utc),
        winner_hint=hint,
        confidence=0.8 if hint else 0.3,   # high trust; fan-edited but quickly corrected
    )


async def resolve(*,
                  name_a: str,
                  name_b: str,
                  subreddits: list[str] = None,
                  twitter_accounts: list[str] = None,
                  wikipedia_page: str = None,
                  since: Optional[datetime] = None,
                  min_sources: int = 2,
                  min_agreement: float = 0.67) -> dict:
    """One-shot cross-reference. Returns consensus dict with 'signals' list.
    Caller decides whether to settle based on winner + confidence + n.
    """
    tasks = []
    if subreddits:
        tasks.append(scan_subreddits(subreddits, name_a, name_b, since))
    if twitter_accounts:
        tasks.append(scan_twitter_accounts(twitter_accounts, name_a, name_b, since))
    if wikipedia_page:
        async def _wiki():
            s = await scan_wikipedia(wikipedia_page, name_a, name_b)
            return [s] if s else []
        tasks.append(_wiki())

    if not tasks:
        return {"winner": None, "confidence": 0.0, "signals": []}

    results = await asyncio.gather(*tasks, return_exceptions=True)
    signals: list[Signal] = []
    for r in results:
        if isinstance(r, list):
            signals.extend([s for s in r if s])

    c = consensus(signals, min_sources=min_sources, min_agreement=min_agreement)
    c["signals"] = [s.as_dict() for s in signals]
    return c


async def close():
    global _client
    if _client and not _client.is_closed:
        await _client.aclose()
    _client = None
