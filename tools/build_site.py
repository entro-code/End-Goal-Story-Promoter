#!/usr/bin/env python3
"""Write docs/schedule.json: what the link-in-bio page shows, and when.

The page (docs/index.html) reads this file in the visitor's browser and shows the
most recent story that has gone live, so it switches by itself at each story time.
"""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent


def load(path, default):
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def thumb_for(item):
    if item.get("youtube_id"):
        return f"https://i.ytimg.com/vi/{item['youtube_id']}/hqdefault.jpg"
    return item.get("image_url")


def links_for(item):
    out = {"youtube": item.get("youtube_url"), "spotify": item.get("spotify_url"),
           "substack": item.get("substack_url")}
    return {k: v for k, v in out.items() if v}


def build(root=ROOT, now=None):
    cfg = load(root / "config.json", {})
    tz = ZoneInfo(cfg["schedule"]["timezone"])
    keep = timedelta(hours=(cfg.get("site") or {}).get("keep_hours", 30))
    now = now or datetime.now(timezone.utc)

    episodes = load(root / "episodes.json", {"episodes": []})["episodes"]
    articles = load(root / "articles.json", {"articles": []}).get("articles", [])
    by_id = {i["id"]: i for i in episodes + articles}
    history = load(root / "state" / "history.json", [])

    entries = []
    for h in history:
        item = by_id.get(h["episode_id"])
        if not item:
            continue
        when = datetime.fromisoformat(h["when"]).replace(tzinfo=tz).astimezone(timezone.utc)
        if now - when > keep:
            continue
        entries.append({
            "at": when.strftime("%Y-%m-%dT%H:%M:%SZ"), "platform": h["platform"],
            "title": item["title"], "thumb": thumb_for(item), "links": links_for(item)})
    entries.sort(key=lambda e: e["at"])

    newest = max(episodes, key=lambda e: e["date"], default=None)
    latest = ({"title": newest["title"], "thumb": thumb_for(newest), "links": links_for(newest),
               "platform": "youtube"} if newest else None)
    site = cfg.get("site") or {}
    data = {"hub_url": site.get("hub_url"), "hub_label": site.get("hub_label"),
            "latest": latest, "stories": entries, "freebies": site.get("freebies") or []}
    out = root / "docs" / "schedule.json"
    out.parent.mkdir(exist_ok=True)
    text = json.dumps(data, indent=1, ensure_ascii=False) + "\n"
    if not out.exists() or out.read_text(encoding="utf-8") != text:
        out.write_text(text, encoding="utf-8")
    return data


if __name__ == "__main__":
    d = build()
    print(f"schedule.json: {len(d['stories'])} stories")
