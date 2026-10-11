"""Weekly promo stories (e.g. a freebie) that run on a fixed weekday and slot,
outside the episode rotation. Configured under "promos" in config.json:

  {"id": "...", "title": "...", "image": "docs/freebies/x.png",   # file in the repo
   "site_image": "freebies/x.png",                                 # same file, as the page sees it
   "url": "https://...", "platform": "substack",
   "weekday": 2,            # 0 = Monday ... 6 = Sunday
   "slot": 1,               # 0-based index into schedule.slots
   "cta": "FREE ON SUBSTACK"  # optional, replaces the platform's call to action
  }
"""
from datetime import datetime, timedelta


def items(cfg):
    """Promos shaped like episodes/articles, so history and the bio page can look them up."""
    out = []
    for p in cfg.get("promos") or []:
        out.append({"id": p["id"], "title": p["title"], "date": "2000-01-01",
                    f"{p['platform']}_url": p["url"], "thumbnail": p["image"],
                    "image_url": p.get("site_image"), "promo": True,
                    "cta": p.get("cta")})
    return out


def plan(cfg, start_day, days, now=None, skip=frozenset()):
    """Promo story slots in the window, same shape as rotation.plan() posts."""
    sched, posts = cfg["schedule"], []
    for p in cfg.get("promos") or []:
        for d in range(days):
            day = start_day + timedelta(days=d)
            if day.weekday() != p["weekday"]:
                continue
            slot = sched["slots"][p["slot"]]
            when = datetime.combine(day, datetime.strptime(slot, "%H:%M").time())
            iso = when.isoformat(timespec="minutes")
            if iso in skip or (now and when <= now):
                continue
            posts.append({"when": iso, "episode_id": p["id"], "title": p["title"],
                          "platform": p["platform"], "link": p["url"], "fresh": False})
    return posts
