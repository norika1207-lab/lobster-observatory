#!/usr/bin/env python3
# Lobster local-LLM warmth monitor.
# Confirms the reverse tunnel is up and qwen2.5:14b stays resident on the laptop.
# Re-warms the model before keep_alive lapses so the 14B never drops cold.
# Writes a status JSON the dashboard can read, and a rolling log.
import json, os, sys, datetime, urllib.request

URL        = os.environ.get("LOBSTER_LLM_WARM_URL", "http://127.0.0.1:18044")
MODEL      = os.environ.get("LOBSTER_LLM_WARM_MODEL", "qwen2.5:14b")
KEEP       = os.environ.get("LOBSTER_LLM_KEEP_ALIVE", "24h")
MARGIN_MIN = int(os.environ.get("LOBSTER_LLM_REWARM_MARGIN_MIN", "90"))
NUM_GPU    = int(os.environ.get("LOBSTER_LLM_NUM_GPU", "999"))  # force all layers onto GPU
FULL_VRAM_MIN = int(os.environ.get("LOBSTER_LLM_FULL_VRAM_MIN", "9000000000"))  # below = half on CPU, force reload
STATUS     = os.environ.get("LOBSTER_LLM_STATUS_FILE", "/opt/battle_arena/lobster-llm-status.json")
LOG        = os.environ.get("LOBSTER_LLM_WARM_LOG", "/var/log/lobster-llm-warmth.log")

def log(msg):
    try:
        with open(LOG, "a") as f:
            f.write("[%s] %s\n" % (datetime.datetime.now().isoformat(timespec="seconds"), msg))
    except Exception:
        pass

def write_status(d):
    d["ts"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    tmp = STATUS + ".tmp"
    with open(tmp, "w") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(tmp, STATUS)

def get(path, timeout=10):
    with urllib.request.urlopen(urllib.request.Request(URL + path), timeout=timeout) as r:
        return r.status, r.read()

def post(path, payload, timeout=150):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(URL + path, data=data, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.status, r.read()

def model_state():
    try:
        _, body = get("/api/ps", timeout=10)
        for m in json.loads(body or b"{}").get("models", []):
            if m.get("name") == MODEL:
                return m.get("expires_at"), m.get("size_vram", 0)
    except Exception as e:
        log("PS_ERR %s" % e)
    return None, 0

# 1. tunnel reachable
try:
    code, _ = get("/api/tags", timeout=8)
except Exception as e:
    log("TUNNEL_DOWN err=%s" % e)
    write_status({"ok": False, "stage": "tunnel", "error": str(e), "model": MODEL, "url": URL})
    sys.exit(0)
if code != 200:
    log("TUNNEL_DOWN tags_http=%s" % code)
    write_status({"ok": False, "stage": "tunnel", "tags_http": code, "model": MODEL, "url": URL})
    sys.exit(0)

# 2. residency check
expires, vram = model_state()
need_warm = False
if not expires:
    need_warm, reason = True, "not_loaded"
else:
    try:
        e = datetime.datetime.fromisoformat(expires)
        mins = int((e - datetime.datetime.now(e.tzinfo)).total_seconds() // 60)
    except Exception:
        mins = -1
    if mins < MARGIN_MIN:
        need_warm, reason = True, "expiry_%dmin" % mins
    else:
        reason = "resident_%dmin" % mins
# half-on-CPU guard: resident but not fully in VRAM -> force full-GPU reload
if not need_warm and vram and vram < FULL_VRAM_MIN:
    need_warm, reason = True, "half_on_cpu_%.1fGB" % (vram / 1e9)

# 3. re-warm if needed
warm_http = "skip"
if need_warm:
    try:
        wc, _ = post("/api/generate", {"model": MODEL, "prompt": "warm", "stream": False,
                                       "keep_alive": KEEP, "options": {"num_predict": 1}}, timeout=180)
        warm_http = str(wc)
    except Exception as e:
        warm_http = "err:%s" % e
    log("REWARM reason=%s warm_http=%s" % (reason, warm_http))
    expires, vram = model_state()

ok = warm_http in ("200", "skip")
write_status({"ok": ok, "model": MODEL, "reason": reason, "need_warm": need_warm,
              "warm_http": warm_http, "size_vram": vram, "expires_at": expires, "url": URL})
log("OK reason=%s need_warm=%s warm_http=%s vram=%s" % (reason, need_warm, warm_http, vram))
sys.exit(0)
