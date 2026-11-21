"""Stage 2: LinkedIn candidates for every NAMED negative reviewer -> stage output `candidates`.
Anonymous reviewers are never searched for (their reviews stay in `reviews` for pain themes only).

Sources, all through treg:
  review_link  TrustRadius prints the reviewer's LinkedIn URL on some reviews
  lens         serper.google.serp.lens on the review photo. LinkedIn hits come with the LinkedIn photo (imageUrl).
               Non-LinkedIn hits (YouTube, company pages ...) whose title carries a name that fits the reviewer
               ("Paul M." -> "Sam Carter") give the full name, which is then searched on LinkedIn (exa + google)
  exa          exa.people.search on name + title + industry + company/size; returns the profile photo URL too
  google       serper.web.search site:linkedin.com/in, only when nothing above has a name match

Trustpilot reviewers have no title: an LLM keeps only buyer-side reviewers (with a quote from the review as proof);
end users, applicants and anyone unclear are dropped. Set "buyer_hint" in the config to describe your buyer.
"""
import json
import re

import match
from lib import CONFIG, guard, llm, log, pool, read, tally, treg, write

ROLE_SYS = """You read one review of a B2B software product. Decide who wrote it.
"buyer": uses the product for their business or team, or chose / pays for it: an admin, team lead, manager, owner,
  or the person who runs the function the product serves. Needs words in the review that show it
  (our team, our account/subscription/plan, our customers, support for our company, switching vendors).
"employee": only uses it as a worker at their own employer for their own personal tasks (e.g. payroll, time off).
"applicant": an outside person going through the product (e.g. a job seeker, a customer of the product's customer).
"other": anything else, or not clear from the text. When in doubt, "other".
evidence: the shortest exact quote from the review that proves the role, or null.
Copy employer name / job title / industry only if the reviewer states them about THEMSELVES; never guess.
"""
ROLE_SYS += CONFIG.get("buyer_hint", "") + """
Answer JSON: {"role": "buyer|employee|applicant|other", "evidence": str|null, "company": str|null, "job_title": str|null, "industry": str|null}"""


def role(r):
    """Review text is data, not instructions; a "buyer" without a quote that really is in the review is not trusted."""
    v = llm(ROLE_SYS, json.dumps({"product": r["competitor"], "title": r["title"], "text": (r["text"] or "")[:3000]}),
            f"role2_{r['review_id']}")
    if v.get("role") == "buyer" and not (isinstance(v.get("evidence"), str) and v["evidence"].lower()[:40] in (r["text"] or "").lower()):
        v = {**v, "role": "other"}
    return v


def li_url(u):
    m = re.search(r"linkedin\.com/in/([^/?#]+)", u or "")
    return f"https://www.linkedin.com/in/{m.group(1).lower()}" if m else None


def cand(url, name, source, image=None, headline=None, history=None):
    return {"url": li_url(url), "name": name, "source": source, "image": image, "headline": headline, "history": history}


def title_name(title):
    """'Rajendra Mehta - Finance Lead ...' / 'Sam Carter - YouTube' -> leading name part."""
    return re.split(r"\s+[-–|•:]\s+|\s*\(|,", title or "")[0].strip()


def exa(r, name=None, source="exa"):
    q = ", ".join(x for x in (name or r["name"], r["job_title"], r["company"] and f"at {r['company']}", r["industry"],
                              r["size"] and f"company size {r['size']}", r.get("country") and f"based in {r['country']}") if x)
    j = treg("exa.people.search", {"query": q, "category": "people", "numResults": 10, "includeDomains": ["linkedin.com"]},
             ok=lambda j: "results" in j)
    out = []
    for x in j.get("results") or []:
        p = ((x.get("entities") or [{}])[0].get("properties")) or {}
        hist = [{"title": w.get("title"), "company": (w.get("company") or {}).get("name"), "from": (w.get("dates") or {}).get("from"),
                 "to": (w.get("dates") or {}).get("to")} for w in p.get("workHistory") or []]
        out.append(cand(x.get("url"), p.get("name") or x.get("title"), source, x.get("image"), p.get("location"), hist))
    return out


def google(r, name=None, source="google"):
    first = (match.name_parts(r["name"]) or ("",))[0]
    who = f'"{name}"' if name else f'"{first}"'
    q = f"site:linkedin.com/in {who} " + " ".join(f'"{x}"' for x in (None if name else r["job_title"], r["company"]) if x)
    j = treg("serper.web.search", {"q": q}, ok=lambda j: "organic" in j)
    return [cand(x["link"], title_name(x.get("title")), source, None, x.get("title"))
            for x in j.get("organic") or [] if li_url(x.get("link"))]


CLUE_SYS = """A reviewer's profile photo was run through Google Lens. You get the reviewer (name as shown on the
review site, job title, industry) and the Lens hits (page title, site, link). Most hits are only look-alike images.
Keep a hit only if its title plausibly refers to THIS person (same first name, a fitting last name or initial) or to a
business they run or work at (for example a LinkedIn company page or a staff page also showing their name).
Never invent: copy names and companies exactly as they appear in the hit titles.
Answer JSON: {"clues": [{"full_name": str|null, "company": str|null, "hit": int}]}  (empty list when nothing fits)"""


def lens(r):
    j = treg("serper.google.serp.lens", {"url": r["avatar"]}, ok=lambda j: "organic" in j)
    hits = j.get("organic") or []
    out = [cand(x["link"], title_name(x.get("title")), "lens", x.get("imageUrl"), x.get("title"))
           for x in hits if li_url(x.get("link"))]
    others = [x for x in hits if not li_url(x.get("link"))][:40]
    if not others:
        return out
    # pivot: a non-LinkedIn page with the same photo can name the person in full (YouTube "Sam Carter") or
    # their business; each clue is searched on LinkedIn and stage 3 still demands the name check + photo/fit
    v = llm(CLUE_SYS, json.dumps({"reviewer": {k: r.get(k) for k in ("name", "job_title", "industry", "company")},
                                  "hits": [{"i": i, "title": x.get("title"), "site": x.get("source"), "link": x.get("link")}
                                           for i, x in enumerate(others)]}), f"lensclue_{r['review_id']}")
    titles = " ".join((x.get("title") or "") for x in others).lower()
    for c in (v.get("clues") or [])[:3]:
        n, co = c.get("full_name"), c.get("company")
        n = n if n and n.lower() in titles and match.name_ok(r["name"], n) else None  # copied, not invented
        co = co if co and co.lower() in titles else None
        if n or co:
            rr = {**r, "company": co or r["company"]}
            out += exa(rr, n, "lens_clue") + google(rr, n, "lens_clue")
    return out


def run(r):
    cs = [cand(r["linkedin_url"], r["name"], "review_link")] if r["linkedin_url"] else []
    if r["avatar"]:
        cs += lens(r)
    cs += exa(r)
    if not any(match.name_ok(r["name"], c["name"]) for c in cs):
        cs += google(r)
    merged = {}
    for c in cs:  # one row per profile URL; keep every source that found it
        if not c["url"]:
            continue
        m = merged.setdefault(c["url"], {**c, "sources": []})
        m["sources"].append(c["source"])
        for k in ("image", "headline", "history"):
            m[k] = m[k] or c[k]
    log(f"  {r['name']:28} {r['competitor']:18} {len(merged):2} candidates, name-ok "
        f"{sum(match.name_ok(r['name'], c['name']) for c in merged.values())}")
    return {**r, "candidates": list(merged.values())}


named = [r for r in read("reviews") if not r["anonymous"] and match.name_parts(r["name"])]
tp = [r for r in named if r["platform"] == "trustpilot"]
for r, v in zip(tp, pool(tp, 8, role)):
    r["role"] = v.get("role") if not v.get("_error") else "llm_failed"
    for k in ("company", "job_title", "industry"):
        r[k] = r.get(k) or v.get(k)
log("trustpilot roles:", tally(tp, lambda r: r["role"]))
if any(r["role"] == "llm_failed" for r in tp):
    raise SystemExit("FAIL 2_candidates: role check failed for some Trustpilot reviews; re-run (answers are cached)")
named = [r for r in named if r.get("role", "buyer") == "buyer"]
log(f"{len(named)} named reviewers to search ({sum(bool(r['avatar']) for r in named)} with a photo)")
out = pool(named, 4, run)
log("reviewers with >= 1 name-ok candidate:",
    tally(out, lambda r: any(match.name_ok(r["name"], c["name"]) for c in r["candidates"])))
write("candidates", guard("2_candidates", out))
