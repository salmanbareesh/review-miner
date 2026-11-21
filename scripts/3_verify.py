"""Stage 3 (auditor, separate from the search in stage 2): decide which candidate IS the reviewer -> `matches`.

status, strongest first:
  photo_match  name matches AND the review avatar and the LinkedIn photo are the same picture (match.same_picture)
  review_link  name matches AND the review site itself linked this LinkedIn profile (TrustRadius)
  probable     no photo proof; exactly one name match whose title/company fits the review. A human checks it.
               Employer size (LinkedIn company page, current or previous job) only breaks ties between several fits:
               reviewers pick size bands loosely (3 of 3 confirmed people were outside their review's band, 2026-10-26)
  unresolved   anything else. Nothing downstream contacts these.
"""
import re
import urllib.request

import match
from lib import guard, log, pool, read, tally, treg, write

STOP = {"and", "the", "of", "for", "manager", "senior", "sr", "director", "head", "lead", "specialist", "associate"}


def fetch_image(url):
    """Avatar bytes (not a paid API: public image hosts). None when unreachable or a blank placeholder."""
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            data = r.read(3_000_000)
        return None if match.flat(data) else data
    except Exception:  # dead link, non-image, decoder error: no photo evidence, never a match
        return None


def li_photo(c):
    if c.get("image"):
        return c["image"]
    j = treg("enrichlayer.person.profile-picture", {"person_profile_url": c["url"]}, method="GET",
             ok=lambda j: bool(j.get("tmp_profile_pic_url")))
    return j.get("tmp_profile_pic_url")


def employer_sizes(c):
    """Sizes of the candidate's current and previous employer, from their LinkedIn company pages."""
    p = treg("blitzapi.people.enrich", {"person_linkedin_url": c["url"]}, ok=lambda j: "found" in j).get("person") or {}
    out = []
    for e in (p.get("experiences") or [])[:2]:
        slug = re.search(r"linkedin\.com/company/([^/?#]+)", e.get("company_linkedin_url") or "")
        if slug:
            j = treg("crawl4ai.linkedin.company.profile", {"company": slug.group(1)}, ok=lambda j: "rows" in j)
            row = (j.get("rows") or [{}])[0]
            out.append({"company": row.get("name") or e.get("company_name"), "size": row.get("size")})
    return out


def words(s):
    return {w for w in re.findall(r"[a-z]{3,}", (s or "").lower()) if w not in STOP}


def fits(r, c):
    titles = " ".join([c.get("headline") or ""] + [h.get("title") or "" for h in c.get("history") or []])
    companies = " ".join(h.get("company") or "" for h in c.get("history") or []) + " " + (c.get("headline") or "")
    title_ok = bool(words(r["job_title"]) & words(titles))
    company_ok = None if not r.get("company") else bool(words(r["company"]) & words(companies))
    return title_ok, company_ok


def run(r):
    named = [c for c in r["candidates"] if match.name_ok(r["name"], c["name"])]
    avatar = fetch_image(r["avatar"])
    checked = []
    for c in named:
        title_ok, company_ok = fits(r, c)
        ev = {"url": c["url"], "name": c["name"], "sources": c["sources"], "title_ok": title_ok,
              "company_ok": company_ok, "photo_bits": None, "photo_mad": None, "photo_same": False}
        ev["employers"] = employer_sizes(c)
        fitsz = [match.size_fits(r["size"], e["size"]) for e in ev["employers"]]
        ev["size_ok"] = True if True in fitsz else (False if False in fitsz else None)
        img = avatar and fetch_image(li_photo(c))
        if img:
            ev["photo_bits"], ev["photo_mad"], ev["photo_same"] = match.same_picture(avatar, img)
        checked.append(ev)
    photo = [e for e in checked if e["photo_same"]]
    linked = [e for e in checked if "review_link" in e["sources"]]
    fit = [e for e in checked if e["title_ok"] and e["company_ok"] is not False]
    if len(fit) > 1 and sum(e["size_ok"] is True for e in fit) == 1:
        fit = [e for e in fit if e["size_ok"] is True]
    if len(photo) == 1:
        status, best = "photo_match", photo[0]
    elif len(linked) == 1:
        status, best = "review_link", linked[0]
    elif len(fit) == 1 and len(photo) == 0 and match.name_parts(r["name"])[1]:  # first name alone is too weak
        status, best = "probable", fit[0]
    else:
        status, best = "unresolved", None
    log(f"  {r['name']:28} {status:12} {best and best['url'] or ''}")
    keep = ("review_id", "platform", "competitor", "date", "stars", "title", "text", "name", "job_title", "industry",
            "size", "company", "avatar", "url")
    return {**{k: r.get(k) for k in keep}, "status": status, "linkedin_url": best and best["url"],
            "has_avatar": avatar is not None, "checked": checked}


out = pool(read("candidates"), 4, run)
log("status:", tally(out, lambda r: r["status"]))
log("photo (bits, mad) seen, for calibration:",
    sorted((e["photo_bits"], e["photo_mad"]) for r in out for e in r["checked"] if e["photo_bits"] is not None))
write("matches", guard("3_verify", out))
