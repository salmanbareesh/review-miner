"""Stage 1: negative competitor reviews -> stage output `reviews` (+ `scrape_report`).

G2 (anyapi.web.scrape): the reviews_and_filters frame, filtered to 1-2 stars, newest first; every page is read ("most_recent" is not strictly date-ordered) and filtered by `since` here.
Capterra (crawl4ai): no working star/sort URL params, so every page is read and filtered here.
TrustRadius (crawl4ai): newest first, paged until older than `since`.
Trustpilot (page HTML, ?stars=1&stars=2, newest first): paged until older than `since`; carries the reviewer photo.

ONLY_SITES=trustpilot,... re-reads only those sites and keeps every other site's rows from the current `reviews`.
"""
import math
import os
import urllib.parse

import parse
from lib import CONFIG, guard, log, pool, read, tally, treg, write

SINCE, MAX_STARS = CONFIG["since"], CONFIG["max_stars"]
MAX_PAGES = 80


def html_of(j):
    return ((j.get("output") or {}).get("data") or {}).get("html") or j.get("html") or ""


def page(endpoint, url):
    """Page HTML from `endpoint`, else from the other scraper (crawl4ai has per-site cooldowns). None if both fail."""
    for e in (endpoint, *({"anyapi.web.scrape", "crawl4ai.web.scrape"} - {endpoint})):
        body = {"url": url} if e == "anyapi.web.scrape" else {"url": url, "format": "html"}
        # a real review page is never tiny; a blocked/empty reply must not be cached as "no reviews"
        j = treg(e, body, ok=lambda j: len(html_of(j)) > 5000)
        if j.get("success") is not False:
            return html_of(j)
    return None


def g2(c):
    q = urllib.parse.urlencode([("filters[nps_score][]", s) for s in range(1, MAX_STARS + 1)] + [("order", "most_recent")])
    rows = []
    for n in range(1, MAX_PAGES + 1):
        h = page("anyapi.web.scrape", f"https://www.g2.com/products/{c['g2']}/reviews_and_filters?{q}&page={n}")
        if h is None:
            raise RuntimeError(f"page {n} failed")
        got = parse.g2(h, c["name"])
        rows += got
        if not got:  # read every page: "most_recent" is not strictly date-ordered (measured 2026-10-25)
            return rows
    return rows


def capterra(c):
    base = f"https://www.capterra.com/p/{c['capterra']}/reviews/"
    first = page("crawl4ai.web.scrape", base)
    if first is None:
        raise RuntimeError("page 1 failed")
    total = parse.capterra_total(first)  # None when every review fits on one page (no "Showing x-y of n")
    if not total and not parse.capterra(first, c["name"]):
        raise RuntimeError("page 1 has neither a review count nor reviews")
    pages = [first] + pool(list(range(2, min(math.ceil((total or 0) / 25), MAX_PAGES) + 1)), 3,
                           lambda n: page("crawl4ai.web.scrape", f"{base}?page={n}"))
    if any(p is None for p in pages):
        raise RuntimeError(f"{sum(p is None for p in pages)} of {len(pages)} pages failed")
    return [r for p in pages for r in parse.capterra(p, c["name"])]


def trustradius(c):
    rows = []
    for n in range(1, MAX_PAGES + 1):
        h = page("crawl4ai.web.scrape", f"https://www.trustradius.com/products/{c['trustradius']}/reviews?page={n}")
        if h is None:
            raise RuntimeError(f"page {n} failed")
        got = parse.trustradius(h, c["name"])
        rows += got
        if not got or max(r["date"] for r in got) < SINCE:
            return rows
    return rows


def trustpilot(c):
    stars = "&".join(f"stars={s}" for s in range(1, MAX_STARS + 1))
    rows = []
    for n in range(1, MAX_PAGES + 1):
        h = page("crawl4ai.web.scrape", f"https://www.trustpilot.com/review/{c['trustpilot']}?{stars}&page={n}")
        if h is None:
            raise RuntimeError(f"page {n} failed")
        got = parse.trustpilot(h, c["name"])
        new = {r["review_id"] for r in got} - {r["review_id"] for r in rows}
        rows += got
        if not new or min(r["date"] for r in got) < SINCE:  # past the last page Trustpilot repeats a page
            return rows
    return rows


SITES = {"g2": g2, "capterra": capterra, "trustradius": trustradius, "trustpilot": trustpilot}


def run(job):
    c, site = job
    try:
        got = SITES[site](c)
        keep = [r for r in got if r["stars"] is not None and r["stars"] <= MAX_STARS and r["date"] >= SINCE]
        log(f"  {c['name']:20} {site:12} read {len(got):4}  kept {len(keep)}")
        return {"competitor": c["name"], "site": site, "read": len(got), "kept": keep, "error": None}
    except Exception as e:  # one dead source must not hide the others; it is reported, not swallowed
        log(f"  {c['name']:20} {site:12} FAILED: {e}")
        return {"competitor": c["name"], "site": site, "read": 0, "kept": [], "error": str(e)}


only = [s for s in os.environ.get("ONLY_SITES", "").split(",") if s]
if any(s not in SITES for s in only):
    raise SystemExit(f"ONLY_SITES must be from {list(SITES)}")
jobs = [(c, s) for c in CONFIG["competitors"] for s in SITES if c.get(s) and (not only or s in only)]
res = pool(jobs, 4, run)
seen, reviews = set(), []
if only:  # keep the other sites exactly as the last complete run left them
    for x in read("reviews"):
        if x["platform"] not in only:
            seen.add(x["review_id"])
            reviews.append(x)
for r in res:
    for x in r["kept"]:
        if x["review_id"] not in seen:
            seen.add(x["review_id"])
            reviews.append(x)
report = [{k: r[k] for k in ("competitor", "site", "read", "error")} | {"kept": len(r["kept"])} for r in res]
failed = [r for r in report if r["error"]]
log(f"\n{len(reviews)} reviews <= {MAX_STARS} stars since {SINCE}; named {sum(not r['anonymous'] for r in reviews)}, "
    f"anonymous {sum(r['anonymous'] for r in reviews)}")
log("by site:", tally(reviews, lambda r: r["platform"]))
log("by competitor:", tally(reviews, lambda r: r["competitor"]))
write("scrape_report", report)
if failed:
    log(f"{len(failed)} sources FAILED:", [(f["competitor"], f["site"], f["error"]) for f in failed])
    if os.environ.get("ALLOW_FAILED") != "1":
        # a partial run must not replace a complete `reviews`; re-run (finished pages are cached) or set ALLOW_FAILED=1
        raise SystemExit("FAIL 1_scrape: sources failed, `reviews` not written")
write("reviews", guard("1_scrape", reviews))
