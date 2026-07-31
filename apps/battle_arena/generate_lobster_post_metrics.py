#!/usr/bin/env python3
import json
import time
import urllib.request
import urllib.parse
from pathlib import Path
from collections import Counter, defaultdict

TOKEN = "moltbook_sk_CfrJO0mM_iYFsxh4y0U6D8WarzAGraoH"
API_BASE = "https://www.moltbook.com/api/v1"
CACHE_FILE = Path("/opt/battle_arena/lobster-post-cache.json")
OUT_FILE = Path("/opt/battle_arena/static/lobster-post-metrics.json")

STATE_MAP = {
    "norika_oda": Path("/root/norika-poster-state.json"),
    "clawtrix": Path("/root/clawtrix-poster-state.json"),
    "pincerbot": Path("/root/pincerbot-poster-state.json"),
    "ragclaw": Path("/root/ragclaw-poster-state.json"),
    "snaplex": Path("/root/snaplex-poster-state.json"),
    "hexclaw": Path("/root/hexclaw-poster-state.json"),
    "vortexiq": Path("/root/vortexiq-poster-state.json"),
    "prismbit": Path("/root/prismbit-poster-state.json"),
    "blazepaw": None,
    "stonefang": None,
}

TOPIC_BUCKETS = {
    "platform": ["ragnovex", "charenix", "knocknix", "turfenix", "dunknix", "pitchnix", "throttenix", "memecat"],
    "learning": ["learned", "learning", "noticed", "realized", "pattern", "feedback", "memory", "ledger", "loop", "drift"],
    "modeling": ["model", "bayesian", "elo", "poisson", "regression", "probability", "calibration", "edge", "signal", "variance"],
    "agent": ["agent", "openclaw", "context window", "weights", "stateless", "external memory"],
    "emotion": ["honestly", "weird", "clicked", "blind", "hate", "frustrat", "funny", "stumbled"],
}


def load_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except Exception:
        return default


def save_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2))


def fetch_post(post_id):
    url = f"{API_BASE}/posts/{urllib.parse.quote(post_id)}"
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {TOKEN}",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=8) as r:
        data = json.loads(r.read())
    return data.get("post") or data


def load_state_posts(path):
    if not path or not path.exists():
        return []
    try:
        data = json.loads(path.read_text())
    except Exception:
        return []
    return data.get("posted") or []


def topic_counts(text):
    text = (text or "").lower()
    out = Counter()
    for bucket, words in TOPIC_BUCKETS.items():
        if any(w in text for w in words):
            out[bucket] += 1
    if "?" in text:
        out["question"] += 1
    return out


def analytic_title(title):
    t = (title or "").lower()
    seeds = ["why ", "how ", "when ", "what ", "vs ", "beyond ", "from "]
    return int(":" in t or any(s in t for s in seeds))


def build_metrics(name, posts):
    if not posts:
        return {"available": False, "reason": "no_posts"}
    posts = sorted(posts, key=lambda p: p.get("created_at") or p.get("time") or "")
    upvotes = [int(p.get("upvotes", 0) or 0) for p in posts]
    comments = [int(p.get("comment_count", 0) or 0) for p in posts]
    body_lens = [len(p.get("content") or "") for p in posts]
    title_lens = [len(p.get("title") or "") for p in posts]
    verified_posts = sum(1 for p in posts if p.get("verified"))
    days = sorted({(p.get("created_at") or p.get("time") or "")[:10] for p in posts if (p.get("created_at") or p.get("time"))})
    submolt_dist = Counter((p.get("submolt") or "trading") for p in posts)
    mode_dist = Counter((p.get("mode") or "unknown") for p in posts)
    topic_dist = Counter()
    question_posts = 0
    analytic_titles = 0
    ragnovex_mentions = 0
    charenix_mentions = 0
    for p in posts:
        text = f"{p.get('title','')}\n{p.get('content','')}"
        tc = topic_counts(text)
        topic_dist.update(tc)
        question_posts += 1 if tc.get("question") else 0
        analytic_titles += analytic_title(p.get("title", ""))
        lt = text.lower()
        ragnovex_mentions += 1 if "ragnovex" in lt else 0
        charenix_mentions += 1 if "charenix" in lt or "memecat" in lt else 0
    top_post = max(posts, key=lambda p: (int(p.get("upvotes", 0) or 0), int(p.get("comment_count", 0) or 0)))
    discussed_post = max(posts, key=lambda p: (int(p.get("comment_count", 0) or 0), int(p.get("upvotes", 0) or 0)))
    recent_posts = []
    for p in sorted(posts, key=lambda p: p.get("created_at") or p.get("time") or "", reverse=True)[:5]:
        recent_posts.append({
            "id": p.get("id"),
            "title": p.get("title", ""),
            "created_at": p.get("created_at") or p.get("time"),
            "submolt": p.get("submolt") or "trading",
            "upvotes": int(p.get("upvotes", 0) or 0),
            "comment_count": int(p.get("comment_count", 0) or 0),
            "url": p.get("url") or f"https://www.moltbook.com/post/{p.get('id')}"
        })
    posts_total = len(posts)
    return {
        "available": True,
        "posts_total": posts_total,
        "verified_posts": verified_posts,
        "unverified_posts": posts_total - verified_posts,
        "total_karma": sum(upvotes),
        "total_comments": sum(comments),
        "avg_upvotes": round(sum(upvotes) / posts_total, 2),
        "avg_comments": round(sum(comments) / posts_total, 2),
        "avg_body_len": round(sum(body_lens) / posts_total, 1) if posts_total else 0,
        "avg_title_len": round(sum(title_lens) / posts_total, 1) if posts_total else 0,
        "question_posts": question_posts,
        "question_rate": round(question_posts / posts_total * 100, 1),
        "analytic_titles": analytic_titles,
        "analytic_title_rate": round(analytic_titles / posts_total * 100, 1),
        "posting_days": len(days),
        "posts_per_day": round(posts_total / max(len(days), 1), 2),
        "last_post_at": posts[-1].get("created_at") or posts[-1].get("time"),
        "top_submolt": submolt_dist.most_common(1)[0][0] if submolt_dist else None,
        "submolt_dist": dict(submolt_dist),
        "mode_dist": dict(mode_dist),
        "topic_dist": dict(topic_dist),
        "ragnovex_mentions": ragnovex_mentions,
        "charenix_mentions": charenix_mentions,
        "top_post": {
            "title": top_post.get("title", ""),
            "upvotes": int(top_post.get("upvotes", 0) or 0),
            "comment_count": int(top_post.get("comment_count", 0) or 0),
            "created_at": top_post.get("created_at") or top_post.get("time"),
            "url": top_post.get("url") or f"https://www.moltbook.com/post/{top_post.get('id')}"
        },
        "most_discussed_post": {
            "title": discussed_post.get("title", ""),
            "upvotes": int(discussed_post.get("upvotes", 0) or 0),
            "comment_count": int(discussed_post.get("comment_count", 0) or 0),
            "created_at": discussed_post.get("created_at") or discussed_post.get("time"),
            "url": discussed_post.get("url") or f"https://www.moltbook.com/post/{discussed_post.get('id')}"
        },
        "recent_posts": recent_posts,
    }


def main():
    cache = load_json(CACHE_FILE, {})
    all_posts_by_lobster = {}
    fetched = 0
    errors = 0

    for lobster, state_path in STATE_MAP.items():
        if state_path is None:
            all_posts_by_lobster[lobster] = []
            continue
        state_posts = load_state_posts(state_path)
        merged_posts = []
        for sp in state_posts:
            pid = sp.get("post_id")
            if not pid:
                continue
            rec = {
                "id": pid,
                "title": sp.get("title", ""),
                "content": "",
                "created_at": sp.get("time", ""),
                "time": sp.get("time", ""),
                "submolt": sp.get("submolt", "trading"),
                "verified": bool(sp.get("verified")),
                "mode": sp.get("mode", "unknown"),
                "upvotes": 0,
                "comment_count": 0,
                "url": f"https://www.moltbook.com/post/{pid}",
            }
            if pid in cache:
                rec.update(cache[pid])
            need_fetch = not rec.get("content") or "upvotes" not in rec or "comment_count" not in rec
            if need_fetch:
                try:
                    post = fetch_post(pid)
                    fetched += 1
                    sub = post.get("submolt")
                    rec.update({
                        "title": post.get("title") or rec["title"],
                        "content": post.get("content") or rec.get("content", ""),
                        "created_at": post.get("created_at") or rec.get("created_at", ""),
                        "submolt": (sub.get("name") if isinstance(sub, dict) else sub) or rec.get("submolt", "trading"),
                        "upvotes": int(post.get("upvotes", 0) or 0),
                        "comment_count": int(post.get("comment_count", 0) or 0),
                        "url": post.get("url") or rec["url"],
                    })
                    time.sleep(0.04)
                except Exception:
                    errors += 1
            cache[pid] = {
                "title": rec.get("title", ""),
                "content": rec.get("content", ""),
                "created_at": rec.get("created_at", ""),
                "submolt": rec.get("submolt", "trading"),
                "upvotes": int(rec.get("upvotes", 0) or 0),
                "comment_count": int(rec.get("comment_count", 0) or 0),
                "url": rec.get("url", f"https://www.moltbook.com/post/{pid}"),
            }
            merged_posts.append(rec)
        all_posts_by_lobster[lobster] = merged_posts

    save_json(CACHE_FILE, cache)

    result = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": {
            "poster_lobsters": [k for k,v in STATE_MAP.items() if v is not None],
            "no_poster_data": [k for k,v in STATE_MAP.items() if v is None],
            "fetched_posts": fetched,
            "fetch_errors": errors,
        },
        "lobsters": {},
    }

    for lobster in STATE_MAP:
        posts = all_posts_by_lobster.get(lobster, [])
        result["lobsters"][lobster] = build_metrics(lobster, posts)
        if STATE_MAP[lobster] is None:
            result["lobsters"][lobster] = {"available": False, "reason": "no_poster_state"}

    save_json(OUT_FILE, result)
    print(f"Wrote {OUT_FILE} with {sum(len(v) for v in all_posts_by_lobster.values())} post refs; fetched={fetched}; errors={errors}")

if __name__ == '__main__':
    main()
