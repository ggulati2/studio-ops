"""Reading project folder names pasted from a drive ("E:\\2026\\Amit_Tanya_Wedding")
into a couple name, the films mentioned and an event date."""
import re
from datetime import date

# words that name a film type -> the deliverable on Projects
FILM_WORDS = [
    (r"full\s*films?|fullfilm|long\s*film|documentary", "Full Film"),
    (r"traditional(\s*film)?|trad", "Traditional Film"),
    (r"same\s*day\s*edit|sde", "Same Day Edit"),
    (r"teasers?", "Teaser"),
    (r"trailers?", "Trailer"),
    (r"highlights?", "Highlight"),
    (r"reels?|insta(gram)?|shorts?", "Reels"),
    (r"cleanup(\s*shots)?|clean\s*up", "Cleanup Shots"),
    (r"event\s*video|after\s*movie|aftermovie", "Event Video"),
    (r"ads?|corporate|promo|commercial|tvc", "Ad / Corporate"),
]
# words in folder names that aren't anybody's name
NOISE = set("""wedding weddings shaadi shadi sangeet haldi mehendi mehndi mehandi reception engagement roka cocktail
ring ceremony pre prewedding post postwedding party function day night raw footage footages data final finals project
projects edit edits edited backup copy cam camera cameras drone photo photos video videos film films clips audio sound
music new old folder misc other others the of a an to for by in at on ji wala vala part side bride groom family
jan feb mar apr may jun jul aug sep sept oct nov dec january february march april june july august september october
november december""".split())
JOINERS = {"&", "+", "and", "weds", "wed", "x", "with", "n", "aur"}


def clean_path(line):
    s = (line or "").strip().strip('"').strip("'").strip()
    s = re.sub(r"[\\/]+", "/", s).rstrip("/")
    s = re.sub(r"^[a-zA-Z]:/", "", s)              # E:/  (Windows drive letter)
    s = re.sub(r"^/Volumes/[^/]+/", "", s)         # /Volumes/HDD/ (Mac)
    return s.strip("/ ")


def _date(text):
    pats = [(r"(20\d\d)[-_. ]?(\d\d)[-_. ]?(\d\d)", "ymd"), (r"(\d\d)[-_. ](\d\d)[-_. ](20\d\d)", "dmy"),
            (r"(\d\d)[-_.](\d\d)[-_.](\d\d)(?!\d)", "dmy2")]
    for pat, kind in pats:
        m = re.search(pat, text)
        if not m:
            continue
        a, b, c = (int(x) for x in m.groups())
        try:
            d = date(a, b, c) if kind == "ymd" else date(c if kind == "dmy" else 2000 + c, b, a)
        except ValueError:
            continue
        return d, text[:m.start()] + " " + text[m.end():]
    return None, text


def parse(line):
    """-> {"path", "name", "couple", "films": [...], "date"} or None for an empty line."""
    path = clean_path(line)
    if not path:
        return None
    name = path.split("/")[-1][:200]
    d, rest = _date(name)
    films = []
    low = " " + re.sub(r"[_\-.]+", " ", rest.lower()) + " "
    for pat, film in FILM_WORDS:
        rx = r"(?<![a-z])(" + pat + r")(?![a-z])"
        if re.search(rx, low):
            films.append(film)
            low = re.sub(rx, " ", low)
    words = re.findall(r"[a-z\u0900-\u097f]+|[&+]", low)
    has_joiner = any(w in JOINERS for w in words)
    people, cur = [], []
    for w in words:
        if w in JOINERS:
            if cur:
                people.append(cur)
            cur = []
        elif w not in NOISE and len(w) > 1:
            cur.append(w.capitalize())
    if cur:
        people.append(cur)
    if has_joiner and len(people) >= 2:
        couple = " & ".join(" ".join(p) for p in people[:2])
    else:
        flat = [w for p in people for w in p]
        couple = " & ".join(flat) if len(flat) == 2 and "Ad / Corporate" not in films else " ".join(flat)
    return {"path": path[:300], "name": name, "couple": (couple or name)[:120],
            "films": [f for f in dict.fromkeys(films)], "date": d}


def parse_many(text):
    out, seen = [], set()
    for line in (text or "").splitlines():
        r = parse(line)
        if r and r["path"].lower() not in seen:
            seen.add(r["path"].lower())
            out.append(r)
    return out
