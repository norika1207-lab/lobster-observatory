#!/usr/bin/env python3
import json
import math
import time
import urllib.request
from pathlib import Path

OUT = Path("/opt/battle_arena/static/lobster-dashboard-v2-vitals.json")
API = "http://127.0.0.1:8004/api/lobster-dashboard-v2-next-bundle"
FIN = "http://127.0.0.1:8004/api/lobster-dashboard-v2-finance-bundle"
MAX_SAMPLES = 24


def clamp(v, lo=0.0, hi=1.0):
    try:
        return max(lo, min(hi, float(v)))
    except Exception:
        return lo


def load_url(url):
    with urllib.request.urlopen(url, timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def score(row, finance):
    stock = (finance or {}).get("stockTrading") or {}
    bet = ((finance or {}).get("bettingSplits") or {}).get("overall") or row.get("settledBetting") or {}
    ex = row.get("existential") or {}
    wr = float(bet.get("winRate") or 0)
    roi = float(stock.get("roi") or 0)
    pressure = float(ex.get("extinctionPressure") or 0)
    sessions = min(1, float((row.get("lifecycle") or {}).get("sessions") or 0) / 200)
    return max(0, min(100, round(35 + (wr - 50) * .6 + roi * .15 - pressure * 25 + sessions * 15 + 15)))


def main():
    bundle = load_url(API)
    finance = load_url(FIN)
    old = {"samples": []}
    if OUT.exists():
        try:
            old = json.loads(OUT.read_text(encoding="utf-8"))
        except Exception:
            pass
    ts = time.strftime("%Y-%m-%dT%H:%M:%S+08:00", time.localtime())
    sample = {"ts": ts, "lobsters": {}}
    per_fin = finance.get("perPersona") or {}
    for name, row in (bundle.get("lobsters") or {}).items():
        fin = per_fin.get(name) or {}
        stock = fin.get("stockTrading") or {}
        bet = (fin.get("bettingSplits") or {}).get("overall") or row.get("settledBetting") or {}
        ex = row.get("existential") or {}
        emo = row.get("emotion") or {}
        sample["lobsters"][name] = {
            "score": score(row, fin),
            "stress": round(max(clamp(ex.get("extinctionPressure")), clamp(ex.get("annihilationDread"))), 3),
            "betWR": round(float(bet.get("winRate") or 0), 2),
            "stockPnL": round(float(stock.get("totalPnL") or 0), 2),
            "stockROI": round(float(stock.get("roi") or 0), 2),
            "output": int((row.get("lifecycle") or {}).get("sessions") or 0),
            "envy": round(float(emo.get("envyIndex") or 0), 3),
            "health": row.get("healthStatus") or "normal",
        }
    samples = (old.get("samples") or [])[-(MAX_SAMPLES - 1):] + [sample]
    OUT.write_text(json.dumps({"engine": "lobster-v2-vital-snapshot", "intervalSec": 300, "samples": samples}, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "samples": len(samples), "ts": ts}, ensure_ascii=False))


if __name__ == "__main__":
    main()
