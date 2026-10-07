#!/usr/bin/env python3
"""Plan which story goes in which slot.

Rules
- Slots come from config.json ("schedule"). Even-numbered slots (1st, 3rd, ...)
  go to a "fresh" episode (uploaded within fresh_days) when one exists; the
  other slots rotate through the back catalog.
- Within the fresh window a given episode+platform is used at most once per
  fresh_repeat_cooldown_hours, so a new episode alternates YouTube / Spotify
  (and Substack once a link exists).
- Back catalog: least-recently-promoted episodes first, never-promoted ones
  before everything else, ties broken randomly (seeded, so a plan is repeatable).
- The same episode never appears in two consecutive stories, and an
  episode+platform pair is not reused inside repeat_cooldown_days.

Usage:
  python rotation.py --days 7                  # dry run, starting tomorrow
  python rotation.py --days 3 --start 2026-10-08
  python rotation.py --days 7 --save           # also record in state/history.json
"""
import argparse
import json
import random
from datetime import date, datetime, timedelta
from pathlib import Path

HERE = Path(__file__).parent
PLATFORMS = ("youtube", "spotify", "substack")
LINK_KEY = {p: f"{p}_url" for p in PLATFORMS}


def load_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def platforms_of(ep):
    return [p for p in PLATFORMS if ep.get(LINK_KEY[p])]


def plan(episodes, sched, start, days, history, skip=frozenset(), now=None):
    """skip: slot times (ISO, minutes) already filled; now: drop slots at/before it."""
    slots = sched["slots"]
    fresh_days = sched["fresh_days"]
    cooldown = timedelta(days=sched["repeat_cooldown_days"])
    fresh_cd = timedelta(hours=sched.get("fresh_repeat_cooldown_hours", 24))

    last_pair, last_ep = {}, {}
    for h in history:
        t = datetime.fromisoformat(h["when"])
        pk = (h["episode_id"], h["platform"])
        last_pair[pk] = max(t, last_pair.get(pk, datetime.min))
        last_ep[h["episode_id"]] = max(t, last_ep.get(h["episode_id"], datetime.min))
    prev_ep = history[-1]["episode_id"] if history else None

    eps = [e for e in episodes if platforms_of(e)]
    posts = []
    for d in range(days):
        day = start + timedelta(days=d)
        for i, slot in enumerate(slots):
            when = datetime.combine(day, datetime.strptime(slot, "%H:%M").time())
            if when.isoformat(timespec="minutes") in skip or (now and when <= now):
                continue
            rng = random.Random(when.isoformat())
            fresh = [e for e in eps
                     if 0 <= (day - date.fromisoformat(e["date"])).days <= fresh_days]
            fresh_ids = {e["id"] for e in fresh}
            catalog = [e for e in eps if e["id"] not in fresh_ids]

            def pick(pool, cd):
                cands = [(e, p) for e in pool for p in platforms_of(e)
                         if e["id"] != prev_ep
                         and when - last_pair.get((e["id"], p), datetime.min) >= cd]
                if not cands:
                    return None
                return min(cands, key=lambda c: (
                    last_ep.get(c[0]["id"], datetime.min),
                    last_pair.get((c[0]["id"], c[1]), datetime.min),
                    rng.random()))

            choice = pick(fresh, fresh_cd) if (fresh and i % 2 == 0) else None
            choice = choice or pick(catalog, cooldown) or pick(eps, timedelta(0))
            if choice is None:
                continue
            ep, plat = choice
            last_pair[(ep["id"], plat)] = when
            last_ep[ep["id"]] = when
            prev_ep = ep["id"]
            posts.append({
                "when": when.isoformat(timespec="minutes"),
                "episode_id": ep["id"],
                "title": ep["title"],
                "platform": plat,
                "link": ep[LINK_KEY[plat]],
                "fresh": ep["id"] in fresh_ids,
            })
    return posts


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--start", help="YYYY-MM-DD (default: tomorrow)")
    ap.add_argument("--config", default=str(HERE / "config.json"))
    ap.add_argument("--episodes", default=str(HERE / "episodes.json"))
    ap.add_argument("--articles", default=str(HERE / "articles.json"))
    ap.add_argument("--history", default=str(HERE / "state" / "history.json"))
    ap.add_argument("--save", action="store_true", help="append the plan to the history file")
    args = ap.parse_args()

    config = load_json(args.config)
    episodes = load_json(args.episodes)["episodes"]
    # Substack articles are promoted as their own items (one link, "read the article").
    episodes += (load_json(args.articles, default={}) or {}).get("articles", [])
    history = load_json(args.history, default=[])
    start = date.fromisoformat(args.start) if args.start else date.today() + timedelta(days=1)

    posts = plan(episodes, config["schedule"], start, args.days, history)
    last_day = None
    for p in posts:
        w = datetime.fromisoformat(p["when"])
        if w.date() != last_day:
            print()
            last_day = w.date()
        star = "*" if p["fresh"] else " "
        print(f"{w:%a %b %d  %H:%M}  {star} {p['platform'].upper():<8} {p['title']}")
    print("\n* = new-episode slot")

    if args.save:
        hist_path = Path(args.history)
        hist_path.parent.mkdir(parents=True, exist_ok=True)
        keep = ("when", "episode_id", "platform")
        history += [{k: p[k] for k in keep} for p in posts]
        hist_path.write_text(json.dumps(history, indent=2) + "\n", encoding="utf-8")
        print(f"saved {len(posts)} posts to {hist_path}")


if __name__ == "__main__":
    main()
