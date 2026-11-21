"""Shared plumbing for review-miner. Every paid call goes through treg.to and store.cached_api;
stage outputs and caches are JSON files under data/<company>/ (scripts/store.py)."""
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from http.client import HTTPException
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import store as db

sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")

REPO = Path(__file__).resolve().parent.parent
AGENT = "review-miner"
COMPANY = os.environ.get("COMPANY", "")  # explicit input, no default
if not re.fullmatch(r"[a-z0-9-]+", COMPANY):
    raise SystemExit(f"set COMPANY=<slug> (reads configs/<slug>.json), got {COMPANY!r}")
_CFG = REPO / "configs" / f"{COMPANY}.json"
if not _CFG.exists():
    raise SystemExit(f"{_CFG} not found. Copy config.example.json there and fill in the competitors.")
CONFIG = json.loads(_CFG.read_text(encoding="utf-8"))
RUN_ID = os.environ.get("RUN_ID")

_ENV = (REPO / ".env").read_text(encoding="utf-8") if (REPO / ".env").exists() else ""


def env(k):
    m = re.search("^" + re.escape(k) + "=(.+)$", _ENV, re.M)
    return (m.group(1).strip() if m else "") or os.environ.get(k, "")


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def read(name):
    return db.read_output(COMPANY, AGENT, name, RUN_ID)


def write(name, v):
    db.write_output(COMPANY, AGENT, name, v, RUN_ID)


def treg(endpoint, body, ok=lambda j: True, method="POST"):
    """https://treg.to/call/<endpoint> through the shared API cache (GET sends `body` as the query string).
    A reply that fails `ok` (blocked page, empty scrape) is returned as an error and never cached, so the next run retries it."""
    def fetch():
        tok = env("TREG_TOKEN")
        if not tok:
            raise SystemExit("TREG_TOKEN missing: put it in .env (see .env.example)")
        url, data = f"https://treg.to/call/{endpoint}", json.dumps(body).encode()
        if method == "GET":
            url, data = f"{url}?{urllib.parse.urlencode(body)}", None
        req = urllib.request.Request(url, data=data, method=method,
                                     headers={"X-Treg-Token": tok, "Content-Type": "application/json"})
        for attempt in range(3):
            try:
                with urllib.request.urlopen(req, timeout=200) as r:
                    status, raw = r.status, r.read()
            except urllib.error.HTTPError as e:
                status, raw = e.code, e.read()
            except (urllib.error.URLError, TimeoutError, OSError, HTTPException):
                time.sleep(3 * (attempt + 1))
                continue
            try:
                j = json.loads(raw)
            except ValueError:
                j = None
            if status in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(5 * (attempt + 1))
                continue
            if 200 <= status < 300 and not (isinstance(j, dict) and ok(j)):
                return 502, {"success": False, "_bad_reply": True}
            if status in (403, 429):
                # treg relays upstream blocks and cooldowns with these codes: return an uncached error instead of
                # tripping the provider breaker, so later calls (cached ones included) keep working.
                return 503, {"success": False, "_http": status}
            return status, j
        return None, None
    return db.cached_api(COMPANY, "treg", endpoint, body, fetch)


def guard(stage, rows, min_rows=1):
    """A stage that emits nothing has failed."""
    n = len(rows) if isinstance(rows, list) else 0
    if n < min_rows:
        log(f"\nFAIL {stage}: emitted {n} rows (need >= {min_rows}). Not writing output.")
        sys.exit(1)
    log(f"OK   {stage}: {n} rows")
    return rows


def pool(items, n, fn):
    if not items:
        return []
    with ThreadPoolExecutor(max_workers=max(1, min(n, len(items)))) as ex:
        return list(ex.map(db.released(fn), items))


def tally(rows, key):
    out = {}
    for r in rows:
        out[key(r)] = out.get(key(r), 0) + 1
    return out


def llm(system, user, tag, model=None):
    """OpenRouter JSON answer, cached in llm_outputs by tag + prompt hash. Failures are never cached."""
    model = model or env("LLM_MODEL") or "openai/gpt-oss-120b"
    if not env("OPENROUTER_API_KEY"):
        raise SystemExit("OPENROUTER_API_KEY missing: put it in .env (see .env.example)")
    ck, ih = f"{re.sub(r'[^A-Za-z0-9._-]', '_', model)}__{tag}", db.input_hash(system, user)
    hit = db.llm_get(COMPANY, ck, ih)
    if hit is not None:
        return hit
    body = json.dumps({"model": model, "temperature": 0, "response_format": {"type": "json_object"},
                       "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}).encode()
    for _ in range(3):
        req = urllib.request.Request("https://openrouter.ai/api/v1/chat/completions", data=body, method="POST",
                                     headers={"Authorization": f"Bearer {env('OPENROUTER_API_KEY')}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                txt = json.loads(r.read())["choices"][0]["message"]["content"]
            v = json.loads(txt[txt.find("{"):txt.rfind("}") + 1])
            if isinstance(v, dict):
                db.llm_put(COMPANY, ck, ih, v)
                return v
        except (urllib.error.URLError, TimeoutError, OSError, HTTPException, ValueError, KeyError, IndexError, TypeError):
            time.sleep(2)
    return {"_error": "llm_failed"}
