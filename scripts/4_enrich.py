"""Stage 4: work email for each reviewer that has a name-matching LinkedIn candidate -> stage output `leads`.

One candidate per reviewer, and `confidence` says how sure the identity is:
  confirmed  stage 3 photo_match / review_link
  probable   stage 3 probable (a human checks the person before any send)
  guess      unresolved: best-ranked name match only (title fit, size fit, most sources). Often the wrong person:
             never send without a human confirming the profile
Email via treg.people.email.find (billed only on a hit), keyed on the LinkedIn URL, plus the current employer's domain
from its LinkedIn company page. Nothing is sent from here.
"""
import re
import urllib.parse

import match
from lib import guard, log, pool, read, tally, treg, write

CONF = {"photo_match": "confirmed", "review_link": "confirmed", "probable": "probable", "unresolved": "guess"}


def pick(r):
    named = list(r["checked"])
    if r["linkedin_url"]:
        return next(e for e in named if e["url"] == r["linkedin_url"])
    named.sort(key=lambda e: (e["title_ok"], e["size_ok"] is True, e["company_ok"] is True, len(e["sources"])), reverse=True)
    return named[0] if named else None


def domain(url):
    p = treg("blitzapi.people.enrich", {"person_linkedin_url": url}, ok=lambda j: "found" in j).get("person") or {}
    for e in (p.get("experiences") or [])[:1]:
        slug = re.search(r"linkedin\.com/company/([^/?#]+)", e.get("company_linkedin_url") or "")
        if slug:
            row = (treg("crawl4ai.linkedin.company.profile", {"company": slug.group(1)}, ok=lambda j: "rows" in j).get("rows") or [{}])[0]
            host = urllib.parse.urlparse(row.get("website") or "").netloc.removeprefix("www.")
            return e.get("company_name"), e.get("job_title"), host or None
    return None, None, None


def run(r):
    e = pick(r)
    company, title, dom = domain(e["url"])
    body = {"full_name": e["name"], "linkedin_url": e["url"]} | ({"domain": dom} if dom else {})
    j = treg("treg.people.email.find", body, ok=lambda j: "output" in j)
    out = j.get("output") or {}
    alts = len(r["checked"]) - 1
    log(f"  {r['name']:24} {CONF[r['status']]:9} {e['name'][:24]:24} {out.get('email') or '-'}")
    return {"review_id": r["review_id"], "platform": r["platform"], "competitor": r["competitor"], "date": r["date"],
            "stars": r["stars"], "review_title": r["title"], "review_text": r["text"], "reviewer_name": r["name"],
            "review_job_title": r["job_title"], "review_size": r["size"], "confidence": CONF[r["status"]],
            "other_name_matches": alts, "full_name": e["name"], "linkedin_url": e["url"], "current_company": company,
            "current_title": title, "company_domain": dom, "email": out.get("email"), "email_verified": out.get("verified"),
            "email_source": (j.get("_treg") or {}).get("served_by"), "photo_bits": e["photo_bits"], "size_ok": e["size_ok"]}


# a first-name-only reviewer without photo proof has no identity to enrich (a first name alone matched the wrong person in testing)
rows = [r for r in read("matches") if r["checked"] and (r["status"] != "unresolved" or match.name_parts(r["name"])[1])]
log(f"{len(rows)} reviewers with a name-matching candidate")
leads = pool(rows, 4, run)
log("confidence:", tally(leads, lambda x: x["confidence"]))
log("email found:", tally(leads, lambda x: (x["confidence"], bool(x["email"]))))
write("leads", guard("4_enrich", leads))
