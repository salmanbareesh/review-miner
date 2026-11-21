"""File-based storage: stage outputs, API cache and LLM cache as JSON under data/<company>/."""
import hashlib
import json
import os
import threading
import time
from pathlib import Path

DATA = Path(os.environ.get("REVIEW_MINER_DATA") or Path(__file__).resolve().parent.parent / "data")
_TRIPPED = set()  # providers that answered 401/403/429 or gave up: skip them for the rest of the run
_lock = threading.Lock()


def exhausted():
    return {"success": False, "_exhausted": True}


def _path(company, *parts):
    p = DATA.joinpath(company, *parts)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _save(p, v):
    tmp = p.with_suffix(f".{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(v, ensure_ascii=False), encoding="utf-8")
    for attempt in range(5):
        try:
            os.replace(tmp, p)
            return
        except PermissionError:  # Windows: target open in another thread
            time.sleep(0.1 * (attempt + 1))
    os.replace(tmp, p)


def _load(p):
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # missing, half-written, or briefly locked by another thread's replace
        return None


def released(fn):
    return fn


def params_hash(params):
    return hashlib.sha256(json.dumps(params, sort_keys=True).encode()).hexdigest()


def cached_api(company, provider, endpoint, params, fetch):
    """Cached good answer, else call fetch() once. fetch() -> (http_status, body_or_None).
    Only successful answers are cached, so a re-run retries failures and never pays twice for a hit.
    Two workers asking for the same key at the same moment both call fetch(); rare at this agent's volume."""
    if provider in _TRIPPED:
        return exhausted()
    p = _path(company, "api_cache", endpoint, params_hash(params) + ".json")
    hit = _load(p)
    if hit is not None:
        return hit
    http, body = fetch()
    if http is None or http in (401, 403, 429):
        if http is not None:
            with _lock:
                _TRIPPED.add(provider)
        return exhausted()
    if body is None:
        body = {"success": False, "_http": http}
    if 200 <= http < 300 and isinstance(body, dict) and body.get("success") is not False:
        _save(p, body)
    return body


def input_hash(system, user):
    return hashlib.sha256(f"{system}\x00{user}".encode("utf-8", "surrogatepass")).hexdigest()


def llm_get(company, cache_key, ihash):
    r = _load(_path(company, "llm_cache", cache_key + ".json"))
    return r["output"] if r and r.get("input_hash") == ihash else None


def llm_put(company, cache_key, ihash, output):
    _save(_path(company, "llm_cache", cache_key + ".json"), {"input_hash": ihash, "output": output})


def write_output(company, agent, name, value, run_id=None):
    """Stage output data/<company>/<name>.json (the latest is current). value None = deleted."""
    p = _path(company, name + ".json")
    if value is None:
        p.unlink(missing_ok=True)
    else:
        _save(p, value)


def read_output(company, agent, name, run_id=None):
    v = _load(_path(company, name + ".json"))
    if v is None:
        raise FileNotFoundError(f"no stage output {name} for {company}: run the earlier stage first")
    return v
