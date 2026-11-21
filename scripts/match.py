"""Identity checks used by stage 3. Pure functions; test_parse.py covers them.

Photo check = the same picture file, not face recognition. Two tests, both must pass:
  dHash (9x8 grey, 64 bits) within PHOTO_MAX_BITS, and the mean pixel difference of 32x32 grey thumbnails
  (brightness-centred) within PHOTO_MAX_MAD. A resized/re-encoded copy of one photo passes both; two different
  photos fail at least one (dHash alone lets smooth, similar-looking images through)."""
import io
import re
import unicodedata

from PIL import Image

# shortcut: initial thresholds, recalibrate on known-true pairs (Capterra LinkedIn photos vs Exa photos)
PHOTO_MAX_BITS = 4
PHOTO_MAX_MAD = 6.0


def _fold(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z ]+", " ", s).split()


def name_parts(name):
    """'Mei L.' -> ('mei', 'l'); 'Tara Reed, SPHR' -> ('tara', 'reed'); 'Rajendra' -> ('rajendra', None)."""
    w = _fold(re.split(r",", name or "")[0])
    while w and w[0] in ("dr", "mr", "mrs", "ms", "miss", "prof"):
        w = w[1:]
    return (w[0], w[-1] if len(w) >= 2 else None) if w and len(w[0]) >= 2 else None


# common English short forms -> one key, so "Nick S." matches "Nicholas Harlow"
_NICK = {n: k for k, v in {
    "nicholas": "nick nicky", "robert": "bob rob bobby robbie", "william": "bill will billy liam", "richard": "rick rich dick",
    "steven": "steve stephen", "michael": "mike mick", "thomas": "tom tommy", "james": "jim jimmy jamie",
    "joseph": "joe joey", "daniel": "dan danny", "david": "dave", "christopher": "chris", "matthew": "matt",
    "anthony": "tony", "andrew": "andy drew", "edward": "ed eddie ted", "benjamin": "ben", "jonathan": "jon john",
    "elizabeth": "liz beth betty eliza lizzie", "katherine": "kate katie kathy cathy kat", "jennifer": "jen jenny",
    "margaret": "maggie meg peggy", "rebecca": "becky becca", "jessica": "jess", "patricia": "pat patty trish",
    "susan": "sue suzy", "deborah": "debi debbie deb", "alexander": "alex", "alexandra": "alex", "samuel": "sam",
    "timothy": "tim", "gregory": "greg", "kenneth": "ken", "ronald": "ron", "donald": "don", "lawrence": "larry",
    "jacqueline": "jackie", "victoria": "vicky tori", "christine": "chris christy", "julia": "juli julie",
}.items() for n in [k, *v.split()]}


def _first(f):
    return _NICK.get(f, f)


def name_ok(review_name, candidate_name):
    """First name equal; review last name equal to, or the initial of, the candidate's last name.
    A first-name-only reviewer matches on first name alone (stage 3 then accepts only photo proof)."""
    a, b = name_parts(review_name), name_parts(candidate_name)
    if not (a and b) or _first(a[0]) != _first(b[0]):
        return False
    if a[1] is None:
        return True
    return bool(b[1]) and (b[1] == a[1] or (len(a[1]) == 1 and b[1].startswith(a[1])))


def dhash(data):
    im = Image.open(io.BytesIO(data)).convert("L").resize((9, 8), Image.LANCZOS)
    px = list(im.getdata())
    return sum(1 << i for i in range(64) if px[(i // 8) * 9 + i % 8] > px[(i // 8) * 9 + i % 8 + 1])


def bits(a, b):
    return bin(a ^ b).count("1")


def thumb(data):
    px = list(Image.open(io.BytesIO(data)).convert("L").resize((32, 32), Image.LANCZOS).getdata())
    m = sum(px) / len(px)
    return [p - m for p in px]


def mad(a, b):
    return sum(abs(x - y) for x, y in zip(a, b)) / len(a)


def same_picture(a, b):
    """(dhash bits, mean abs pixel diff, verdict) for two image byte strings."""
    d, m = bits(dhash(a), dhash(b)), mad(thumb(a), thumb(b))
    return d, round(m, 2), d <= PHOTO_MAX_BITS and m <= PHOTO_MAX_MAD


def flat(data):
    """True for a blank or near-single-colour image (placeholder avatars): its hash matches anything blank."""
    im = Image.open(io.BytesIO(data)).convert("L").resize((16, 16))
    lo, hi = im.getextrema()
    return hi - lo < 24


def size_range(s):
    """Employee band -> (lo, hi). '201-500 employees', '5,001-10,000', '10,001+', 'Mid-Market (51-1000 emp.)',
    'Small-Business (50 or fewer emp.)', 'Enterprise (> 1000 emp.)', 'Self-employed'. None when unknown."""
    t = (s or "").lower().replace(",", "")
    if "self" in t:
        return 1, 1
    n = [int(x) for x in re.findall(r"\d+", t)]
    if "fewer" in t or "or less" in t:
        return 1, n[0] if n else 50
    if "+" in t or ">" in t:
        return (n[0] + (">" in t), 10 ** 7) if n else None
    return (n[0], n[1]) if len(n) >= 2 else None


def size_fits(review_size, company_size):
    """Bands overlap after widening the review's band one step each way (x0.5 .. x2): reviewers pick bands loosely
    (measured: a confirmed photo match said 501-1,000, his employer lists 1,001-5,000). None = cannot tell."""
    a, b = size_range(review_size), size_range(company_size)
    if not (a and b):
        return None
    return b[0] <= a[1] * 2 and b[1] >= a[0] / 2
