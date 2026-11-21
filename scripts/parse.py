"""Review page parsers. Pure functions (no DB, no network) so test_parse.py can run them on saved pages.
Each returns a list of review dicts with the same keys (ROW); missing facts stay None, never guessed."""
import html as htmllib
import json
import re
from datetime import datetime

ROW = ("platform", "competitor", "review_id", "url", "date", "stars", "title", "text", "name", "job_title",
       "industry", "size", "company", "avatar", "profile_url", "linkedin_url", "anonymous", "verified_linkedin")
_MONTH_DATE = r"(?:January|February|March|April|May|June|July|August|September|October|November|December) \d{1,2}, 20\d\d"


def row(**kw):
    return {k: kw.get(k) for k in ROW}


def _text(s):
    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", " ", s or ""))).strip() or None


def _iso(s):
    return datetime.strptime(s, "%B %d, %Y").strftime("%Y-%m-%d")


def _anon(name):
    return not name or bool(re.match(r"(?i)(verified (user|reviewer)|anonymous)", name))


# ---------- G2: the reviews_and_filters frame, one <article> per review ----------

_KEEP_ATTR = re.compile(r'\s(?!(?:href|src|content|id|alt|title)=)[\w:-]+="[^"]*"')


def g2(page_html, competitor):
    out = []
    for m in re.finditer(r'<article\b[^>]*>[\s\S]*?</article>', page_html):
        raw_img = re.search(r"<img[^>]*>", m.group(0))
        # G2 lazy-loads the photo: the real URL sits in data-deferred-image-src, src is a placeholder gif
        src = [s for s in re.findall(r'"(https://[^"]+)"', raw_img.group(0) if raw_img else "")
               if "transparent" not in s and "anonymous-avatar" not in s]
        a = re.sub(r"<(script|style|svg)\b[\s\S]*?</\1>", "", m.group(0))
        a = _KEEP_ATTR.sub("", a)
        rid = re.search(r'id="[\w-]*-review-(\d+)"', a)
        date = re.search(r'<meta content="(\d{4}-\d\d-\d\d)">', a)
        if not (rid and date):
            continue
        img = re.search(r"<img[^>]*>", a)
        name = re.search(r'alt="([^"]*)"', img.group(0)).group(1) if img and 'alt="' in img.group(0) else None
        name = htmllib.unescape(name) if name else None
        fields = []
        if name:
            f = re.search(r"<div>" + re.escape(htmllib.escape(name, quote=False)) + r"</div></div>((?:<div>[^<]*</div>)*)", a) \
                or re.search(r"<div>" + re.escape(name) + r"</div></div>((?:<div>[^<]*</div>)*)", a)
            fields = [_text(x) for x in re.findall(r"<div>([^<]*)</div>", f.group(1))] if f else []
        size = next((x for x in fields if x and "emp." in x), None)
        rest = [x for x in fields if x and x != size]
        stars = re.search(r"<span>([\d.]+)/5</span>", a)
        title = re.search(r"<div>\s*(?:&quot;|\"|“)(.+?)(?:&quot;|\"|”)\s*</div>", a)
        qa = re.findall(r"<section><div>([^<]*)</div><p>([\s\S]*?)</p></section>", a)
        text = "\n".join(f"{_text(q)} {_text(re.sub(r'<span>Review collected[\s\S]*?</span>', '', p))}" for q, p in qa)
        prof = re.search(r'href="(https://www\.g2\.com/users/[^"]+)"', a)
        out.append(row(platform="g2", competitor=competitor, review_id=f"g2_{rid.group(1)}",
                       url=f"https://www.g2.com/survey_responses/{rid.group(1)}", date=date.group(1),
                       stars=float(stars.group(1)) if stars else None, title=_text(title.group(1)) if title else None,
                       text=text or None, name=name, job_title=rest[0] if rest else None,
                       industry=rest[1] if len(rest) > 1 else None, size=size, avatar=src[0] if src else None,
                       profile_url=prof.group(1) if prof else None, anonymous=_anon(name) or not prof,
                       verified_linkedin=None))
    return out


# ---------- Capterra: review JSON embedded in the Next.js payload ----------

def capterra(page_html, competitor):
    # the payload is JSON inside a JS string: undo one level of escaping in one pass (\\" -> \", \\\\ -> \\)
    h = re.sub(r'\\(["\\])', r"\1", page_html)
    dec, out, seen = json.JSONDecoder(), [], set()
    # two payload layouts: the reviews page ({"reviewId", writtenOn, overallRating, prosText, reviewer.fullName ...})
    # and the product page ({"id", "reviewId", date, rating, pros, reviewer.name, reviewer.company = industry ...})
    for m in re.finditer(r'\{"(?:id|reviewId)":"', h):
        try:
            r, _ = dec.raw_decode(h, m.start())
        except ValueError:
            continue
        rv = r.get("reviewer") if isinstance(r.get("reviewer"), dict) else {}
        when, stars = r.get("writtenOn") or r.get("date"), r.get("overallRating") or r.get("rating")
        if not r.get("reviewId") or r["reviewId"] in seen or not when or not stars:
            continue
        seen.add(r["reviewId"])
        try:
            date = _iso(when)
        except ValueError:
            continue
        name = rv.get("fullName") or rv.get("name")
        text = "\n".join(f"{k}: {v}" for k, v in (("Pros", r.get("prosText") or r.get("pros")),
                                                  ("Cons", r.get("consText") or r.get("cons")),
                                                  ("Comments", r.get("generalComments") or r.get("content"))) if v)
        out.append(row(platform="capterra", competitor=competitor, review_id=f"capterra_{r['reviewId']}",
                       url=None, date=date, stars=float(stars), title=r.get("title"),
                       text=text.replace("\\r\\n", "\n").replace("\\n", "\n") or None, name=name,
                       job_title=rv.get("jobTitle") or rv.get("title") or None,
                       industry=rv.get("industry") or rv.get("company") or None,
                       size=rv.get("companySize") or None, avatar=rv.get("profilePicUrl") or rv.get("avatarUrl") or None,
                       anonymous=bool(r.get("anonymityOn") or rv.get("anonymityOn")) or _anon(name),
                       verified_linkedin=rv.get("verifiedLinkedIn")))
    return out


def capterra_total(page_html):
    m = re.search(r"Showing \d+-\d+ of ([\d,]+) Reviews", _text(page_html) or "")
    return int(m.group(1).replace(",", "")) if m else None


# ---------- TrustRadius: one <article class="Review_review..."> per review ----------

_TR_ROLE = re.compile(r"^(?P<title>.*?)(?: in (?P<dept>.*?))?(?: at (?P<company>.*?))?(?: \((?P<size>[^)]*employees)\))?$")


def trustradius(page_html, competitor):
    out = []
    for m in re.finditer(r'<article class="Review_review[\s\S]*?</article>', page_html):
        a = re.sub(r"<svg\b[\s\S]*?</svg>", "", m.group(0))
        rating = re.search(r"Rating: (\d+) out of 10", a)
        date = re.search(_MONTH_DATE, a)
        link = re.search(r'<h2[^>]*><a href="(/reviews/[^"]+)">([\s\S]*?)</a>', a)
        if not (rating and date and link):
            continue
        bi = a.find('data-testid="byline"')
        # byline = <a href="/users/.."> for named reviewers, a plain block for "Verified User"; it ends at the verification badges
        seg = a[bi:a.find("_verifications", bi)] if bi >= 0 else ""
        divs = [d for d in (_text(x) for x in re.findall(r"<div[^>]*>([^<]+)</div>", seg)) if d]
        name, role = (divs + [None, None])[:2]
        rm = _TR_ROLE.match(role or "")
        img = re.search(r'data-testid="avatar-container"><span[^>]*>(?:<span[^>]*>)?<img[^>]*src="([^"]+)"', seg)
        li = re.search(r'href="(https://(?:\w+\.)?linkedin\.com/in/[^"]+)"', a)
        prof = re.search(r'href="(/users/[^"]+)"[^>]*data-testid="byline"', a)
        body = a[link.end():bi if bi >= 0 else len(a)]
        out.append(row(platform="trustradius", competitor=competitor, review_id=f"tr_{link.group(1).rsplit('/', 1)[-1]}",
                       url="https://www.trustradius.com" + link.group(1), date=_iso(date.group(0)),
                       stars=int(rating.group(1)) / 2, title=_text(link.group(2)), text=_text(body), name=name,
                       job_title=(rm.group("title") or None) if rm else None, industry=rm.group("dept") if rm else None,
                       company=rm.group("company") if rm else None, size=rm.group("size") if rm else None,
                       avatar=htmllib.unescape(img.group(1)) if img else None,
                       profile_url="https://www.trustradius.com" + prof.group(1) if prof else None,
                       linkedin_url=li.group(1) if li else None, anonymous=_anon(name), verified_linkedin=None))
    return out



# ---------- Trustpilot: review list in the page's __NEXT_DATA__ JSON (has the reviewer photo) ----------

def trustpilot(page_html, competitor):
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>([\s\S]*?)</script>', page_html or "")
    if not m:
        return []
    out = []
    for r in json.loads(m.group(1))["props"]["pageProps"].get("reviews") or []:
        c = r.get("consumer") or {}
        name = c.get("displayName")
        out.append(row(platform="trustpilot", competitor=competitor, review_id=f"tp_{r['id']}",
                       url=f"https://www.trustpilot.com/reviews/{r['id']}", date=(r.get("dates") or {}).get("publishedDate", "")[:10],
                       stars=float(r["rating"]), title=r.get("title"), text=r.get("text"), name=name,
                       avatar=c.get("imageUrl") if c.get("hasImage") else None, anonymous=_anon(name),
                       verified_linkedin=None) | {"country": c.get("countryCode")})
    return out
