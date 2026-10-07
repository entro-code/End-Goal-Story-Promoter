#!/usr/bin/env python3
"""Build episodes.json from the exported episodes spreadsheet.

The export has two sheets: "Episodes" (Name, Date Uploaded, Spotify Link) and
"YouTube" (Name, Date Uploaded, YouTube Link). The two sheets word some titles
differently and dates can differ by a day or more, so rows are matched by title
similarity plus date proximity.

Usage: python import_sheet.py export.txt episodes.json
"""
import csv
import io
import json
import re
import sys
from datetime import date
from difflib import SequenceMatcher
from pathlib import Path

SUFFIX = re.compile(r"\s*[|\-–]\s*(?:the )?end goal(?: podcast)?\s*$", re.I)
MAX_DAY_GAP = 14
MIN_SCORE = 0.5
REVIEW_BELOW = 0.75


def parse_sheets(text):
    sheets, name = {}, None
    for line in text.splitlines():
        m = re.match(r"## Sheet name: (.+)", line)
        if m:
            name = m.group(1).strip()
            sheets[name] = []
        elif name is not None:
            sheets[name].append(line)
    out = {}
    for n, lines in sheets.items():
        rows = list(csv.reader(io.StringIO("\n".join(lines).strip())))
        out[n] = [r for r in rows[1:] if len(r) >= 3 and any(c.strip() for c in r)]
    return out


def clean(title):
    t = title.strip().replace("’", "'").replace("“", '"').replace("”", '"')
    t = re.sub(r"\s*\|\s*", " | ", t)
    while SUFFIX.search(t):
        t = SUFFIX.sub("", t)
    return t.strip()


def key(title):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]", " ", clean(title).lower())).strip()


def yt_id(url):
    m = re.search(r"[?&]v=([\w-]{11})", url) or re.search(r"youtu\.be/([\w-]{11})", url)
    return m.group(1) if m else None


def slug(text, d):
    s = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48].strip("-")
    return f"{s}-{d.isoformat()}"


def pair_score(t1, d1, t2, d2):
    """Likelihood two rows are the same episode: title similarity + date closeness.
    Returns 0.0 when the dates are too far apart to be the same release."""
    gap = abs((d1 - d2).days)
    if gap > MAX_DAY_GAP:
        return 0.0
    ratio = SequenceMatcher(None, key(t1), key(t2)).ratio()
    return 0.6 * ratio + 0.4 * max(0.0, 1 - gap / MAX_DAY_GAP)


def main():
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    sheets = parse_sheets(Path(sys.argv[1]).read_text(encoding="utf-8"))
    spot = [(r[0], date.fromisoformat(r[1].strip()), r[2].strip()) for r in sheets["Episodes"]]
    yt = [(r[0], date.fromisoformat(r[1].strip()), r[2].strip()) for r in sheets["YouTube"]]

    # Score every plausible (spotify, youtube) pair, then assign best-first.
    scored = []
    for i, (st, sd, _) in enumerate(spot):
        for j, (yt_title, yd, _) in enumerate(yt):
            score = pair_score(st, sd, yt_title, yd)
            if score:
                scored.append((score, i, j))
    scored.sort(reverse=True)
    s_used, y_used, pairs = set(), set(), {}
    for score, i, j in scored:
        if score < MIN_SCORE or i in s_used or j in y_used:
            continue
        s_used.add(i)
        y_used.add(j)
        pairs[j] = (i, score)

    episodes, review, used_ids = [], [], set()
    for j, (yt_title, yd, yt_url) in enumerate(yt):
        sp = None
        if j in pairs:
            i, score = pairs[j]
            sp = spot[i]
            if score < REVIEW_BELOW:
                review.append((round(score, 2), yt_title, sp[0]))
        titles = [clean(yt_title)] + ([clean(sp[0])] if sp else [])
        title = min(titles, key=len)
        eid = slug(title, yd)
        while eid in used_ids:
            eid += "-b"
        used_ids.add(eid)
        episodes.append({
            "id": eid,
            "title": title,
            "date": yd.isoformat(),
            "youtube_url": yt_url,
            "youtube_id": yt_id(yt_url),
            "spotify_url": sp[2] if sp else None,
            "substack_url": None,
            "thumbnail": None,
        })
    for i, (st, sd, su) in enumerate(spot):
        if i not in s_used:  # Spotify-only episode (no YouTube match)
            eid = slug(clean(st), sd)
            episodes.append({
                "id": eid, "title": clean(st), "date": sd.isoformat(),
                "youtube_url": None, "youtube_id": None, "spotify_url": su,
                "substack_url": None, "thumbnail": None,
            })
            review.append((0.0, "(no YouTube match)", st))

    episodes.sort(key=lambda e: e["date"], reverse=True)
    out = {
        "_comment": ("Episode records. A platform is only promoted if its link is filled in. "
                     "thumbnail is a local image path; when null, the YouTube thumbnail is "
                     "fetched from youtube_id at post time."),
        "episodes": episodes,
    }
    Path(sys.argv[2]).write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    both = sum(1 for e in episodes if e["youtube_url"] and e["spotify_url"])
    print(f"spotify rows: {len(spot)}  youtube rows: {len(yt)}")
    print(f"episodes written: {len(episodes)}  (on both platforms: {both})")
    yt_only = [e["title"] for e in episodes if e["youtube_url"] and not e["spotify_url"]]
    sp_only = [e["title"] for e in episodes if e["spotify_url"] and not e["youtube_url"]]
    print("YouTube-only:", yt_only)
    print("Spotify-only:", sp_only)
    print("Lower-confidence matches to eyeball:")
    for score, a, b in sorted(review):
        print(f"  {score}  YT: {a}\n        SP: {b}")


if __name__ == "__main__":
    main()
