"""python3 scripts/test_parse.py  (no DB, no network). Fixtures are synthetic, shaped like each site's page markup; every name in them is invented."""
import io
import json

from PIL import Image, ImageDraw

import match
import parse

G2 = ('<article class="x" id="example-review-1"><div><a href="https://www.g2.com/users/example">'
      '<img class="a" alt="Mei L." data-deferred-image-src="https://example.com/avatar-g2.png" '
      'src="https://www.g2.com/assets/transparent-ad5b.gif"></a><div><div><meta content="Mei L.">'
      '<div class="y" elv="true">Mei L.</div></div><div elv="true">Chief Data Officer</div>'
      '<div elv="true">Small-Business (50 or fewer emp.)</div></div><div><meta content="2026-02-19"></div>'
      '<div elv="true">"Predatory Pricing"</div><div elv="true"><span>0.5/5</span></div>'
      '<section><div elv="true">What do you dislike?</div><p>Credits do not roll over. '
      '<span>Review collected by and hosted on G2.com.</span></p></section></div></article>'
      '<article id="x-review-2"><img alt="Verified User in Accounting" src="https://www.g2.com/assets/icons/anonymous-avatar-purple.svg">'
      '<meta content="2025-10-17"><span>1/5</span></article>')

CAP = json.dumps({"textReviews": [
    {"reviewId": "Capterra___1", "title": "Regret", "writtenOn": "March 15, 2025", "overallRating": "1.0", "prosText": "x",
     "consText": 'slow "front line" hiring', "anonymityOn": False, "reviewer": {"fullName": "Angela W.", "jobTitle": "TA Manager",
     "industry": "Retail", "companySize": "5,001-10,000 employees", "verifiedLinkedIn": True,
     "profilePicUrl": "https://cdn0.capterra-static.com/profile-images/linkedin/a.jpeg"}},
    {"id": "Capterra___2", "reviewId": "Capterra___2", "title": "ok", "date": "October 28, 2024", "rating": 2,
     "pros": "p", "cons": "c", "reviewer": {"name": "Ed W.", "title": "HR Director", "company": "Food & Beverages",
     "avatarUrl": None}}]}, separators=(",", ":")).replace("\\", "\\\\").replace('"', '\\"')  # escaped as a JS string, like the page

TR = ('<article class="Review_review__5RC6b"><h2 class="h"><a href="/reviews/talentreef-2025-03-10">Extremely Disappointed</a></h2>'
      '<div class="s">Rating: 2 out of 10</div><div>March 11, 2025</div><p>Support never answers.</p>'
      '<div class="Author"><a href="/users/622a" class="b" data-testid="byline"><div data-testid="avatar-container"><span>'
      '<span title="DW">DW</span></span></div><div><div class="n">Dana Whitfield</div><div class="j">Talent Acquisition '
      'Partner in Human Resources at Example Foods (1001-5000 employees)</div></div></a><div class="_verifications_9"></div>'
      '<img data-testid="logo" src="https://media.trustradius.com/vendor-logos/x.JPEG"/>'
      '<a href="https://www.linkedin.com/in/dana-whitfield-example">View profile</a></div></article>')


def img(seed, size=96, color=(30, 60, 120)):
    im = Image.new("RGB", (96, 96))
    d = ImageDraw.Draw(im)
    for y in range(96):
        d.line((0, y, 95, y), fill=(seed + y, 120 + y // 2, 200 - y))
    d.ellipse((25, 10, 70, 55), fill=(60, 40, 30))
    d.rectangle((10, 60, 80, 95), fill=color)
    b = io.BytesIO()
    im.resize((size, size)).save(b, "JPEG", quality=70)
    return b.getvalue()


g = parse.g2(G2, "Apollo")
assert len(g) == 2, g
assert g[0]["name"] == "Mei L." and g[0]["job_title"] == "Chief Data Officer" and g[0]["stars"] == 0.5
assert g[0]["avatar"] == "https://example.com/avatar-g2.png" and g[0]["size"].startswith("Small") and not g[0]["anonymous"]
assert "Credits do not roll over." in g[0]["text"] and "collected" not in g[0]["text"]
assert g[1]["anonymous"] and g[1]["avatar"] is None

c = parse.capterra(CAP, "TalentReef")
assert [x["date"] for x in c] == ["2025-03-15", "2024-10-28"], c
assert c[0]["text"].endswith('Cons: slow "front line" hiring'), c[0]["text"]
assert c[0]["verified_linkedin"] and c[0]["avatar"].endswith("a.jpeg") and c[0]["industry"] == "Retail"
assert c[1]["name"] == "Ed W." and c[1]["job_title"] == "HR Director" and c[1]["stars"] == 2.0

t = parse.trustradius(TR, "TalentReef")
assert len(t) == 1 and t[0]["stars"] == 1.0 and t[0]["date"] == "2025-03-11", t
assert t[0]["company"] == "Example Foods" and t[0]["job_title"] == "Talent Acquisition Partner"
assert t[0]["linkedin_url"] == "https://www.linkedin.com/in/dana-whitfield-example" and t[0]["avatar"] is None
assert t[0]["profile_url"].endswith("/users/622a")

assert match.name_ok("Mei L.", "Mei Lin") and match.name_ok("Dana Whitfield", "Dana Whitfield")
assert not match.name_ok("Mei L.", "Mei Park") and not match.name_ok("Arun P.", "Vikram Rao")
assert match.name_ok("Tara Reed, SPHR, SHRM-SCP", "Tara Reed") and match.name_ok("José R.", "Jose Ramirez")

a, a_big, other = img(40), img(40, size=200), img(140, color=(200, 200, 40))
assert match.same_picture(a, a_big)[2], match.same_picture(a, a_big)  # same picture, other size + re-encode
assert not match.same_picture(a, other)[2], match.same_picture(a, other)
blank = io.BytesIO()
Image.new("RGB", (64, 64), (128, 128, 128)).save(blank, "PNG")
assert match.flat(blank.getvalue()) and not match.flat(a)
assert match.size_range("5,001-10,000 employees") == (5001, 10000) and match.size_range("10,001+ employees") == (10001, 10 ** 7)
assert match.size_range("Small-Business (50 or fewer emp.)") == (1, 50) and match.size_range("Enterprise (> 1000 emp.)") == (1001, 10 ** 7)
assert match.size_range("Mid-Market (51-1000 emp.)") == (51, 1000) and match.size_range(None) is None
assert match.size_fits("501-1,000 employees", "1,001-5,000 employees")  # measured true pair
assert not match.size_fits("Small-Business (50 or fewer emp.)", "10,001+ employees")
assert match.size_fits("201-500 employees", None) is None
assert match.name_ok("Rajendra", "Rajendra Mehta") and not match.name_ok("Rajendra", "Raj Mehta")
assert not match.name_ok("Mei L.", "Mei") and match.name_parts("J") is None
TP = '<script id="__NEXT_DATA__" type="application/json">' + json.dumps({"props": {"pageProps": {"reviews": [
    {"id": "6a1", "rating": 1, "title": "Painful UI", "text": "x", "dates": {"publishedDate": "2026-10-20T06:48:58.000Z"},
     "consumer": {"displayName": "Alex Morgan", "imageUrl": "https://example.com/avatar/73x73.png",
                  "hasImage": True, "countryCode": "MX"}},
    {"id": "6a2", "rating": 2, "title": "t", "text": "y", "dates": {"publishedDate": "2025-03-01T00:00:00.000Z"},
     "consumer": {"displayName": "Alex", "imageUrl": "", "hasImage": False}}]}}}) + "</script>"
tp = parse.trustpilot(TP, "Workday")
assert tp[0]["avatar"].endswith("73x73.png") and tp[0]["country"] == "MX" and tp[0]["date"] == "2026-10-20", tp[0]
assert tp[1]["avatar"] is None and tp[1]["stars"] == 2.0
assert match.name_ok("Dr. Nick Harlow", "Nicholas Harlow") and match.name_ok("Bob B.", "Robert Brooks")
assert not match.name_ok("Nick S.", "Nicole Smith")
print("ok")
