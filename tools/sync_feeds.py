#!/usr/bin/env python3
"""Pull new uploads automatically so nobody has to send links each week.

Sources (each optional, configured under "sync" in config.json)
  YouTube   public channel feed, no key needed
  Spotify   Web API "show episodes" (OPTIONAL: the dev app needs Spotify
            Premium; env SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET. Without it, new
            YouTube episodes are assumed to be on Spotify too.)
  Substack  public RSS feed

New YouTube / Spotify items are matched to existing episodes (title similarity +
date) so one episode ends up with both links, whichever platform publishes first.
Substack posts go to articles.json and are promoted as their own items.

Usage:
  python sync_feeds.py                  # fetch live feeds, update the JSON files
  python sync_feeds.py --dry-run        # show what would change, write nothing
  python sync_feeds.py --fixtures DIR   # read DIR/youtube.xml, spotify.json,
                                        # substack.xml instead of the network
"""
import argparse
import base64
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from email.utils import parsedate_to_datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from import_sheet import MIN_SCORE, clean, pair_score, slug  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
UA = {"User-Agent": ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
      "Accept": "application/rss+xml, application/atom+xml, application/json, text/xml, */*"}
NS = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
CONTENT_NS = "http://purl.org/rss/1.0/modules/content/"


def http_get(url, headers=None, data=None, timeout=30):
    req = urllib.request.Request(url, data=data, headers={**UA, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as exc:  # keep the server's explanation
        body = exc.read()[:200].decode("utf-8", "replace").replace("\n", " ")
        raise RuntimeError(f"HTTP {exc.code} from {urllib.parse.urlparse(url).netloc}: {body}") from None


# --------------------------------------------------------------------------
# parsers (pure functions, easy to test)
# --------------------------------------------------------------------------
def parse_youtube(xml_bytes, ignore=()):
    root = ET.fromstring(xml_bytes)
    out = []
    for e in root.findall("a:entry", NS):
        vid = e.findtext("yt:videoId", namespaces=NS)
        link = e.find("a:link[@rel='alternate']", NS)
        if link is None:
            link = e.find("a:link", NS)
        href = link.get("href", "") if link is not None else ""
        if not vid or vid in ignore or "/shorts/" in href:
            continue  # skip Shorts and anything on the ignore list
        published = (e.findtext("a:published", namespaces=NS) or "")[:10]
        out.append({
            "title": clean(e.findtext("a:title", namespaces=NS) or ""),
            "date": published,
            "youtube_id": vid,
            "youtube_url": f"https://www.youtube.com/watch?v={vid}",
        })
    return out


def parse_spotify(payload):
    out = []
    for it in payload.get("items", []):
        url = ((it.get("external_urls") or {}).get("spotify") or "").split("?")[0]
        rd = it.get("release_date") or ""
        if not url or len(rd) < 10:
            continue
        images = it.get("images") or [{}]
        out.append({"title": clean(it.get("name", "")), "date": rd[:10], "spotify_url": url,
                    "image_url": images[0].get("url")})
    return out


def parse_substack(xml_bytes):
    root = ET.fromstring(xml_bytes)
    out = []
    for item in root.iter("item"):
        link = (item.findtext("link") or "").split("?")[0].strip()
        title = (item.findtext("title") or "").strip()
        pub = item.findtext("pubDate")
        if not link or not title or not pub:
            continue
        image = None
        enc = item.find("enclosure")
        if enc is not None and (enc.get("type") or "").startswith("image"):
            image = enc.get("url")
        if not image:
            m = re.search(r'<img[^>]+src="([^"]+)"', item.findtext(f"{{{CONTENT_NS}}}encoded") or "")
            image = m.group(1) if m else None
        out.append({"title": title, "date": parsedate_to_datetime(pub).date().isoformat(),
                    "substack_url": link, "image_url": image})
    return out


# --------------------------------------------------------------------------
# fetchers
# --------------------------------------------------------------------------
def fetch_youtube(channel_id):
    return http_get(f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}")


def fetch_spotify(show_id):
    cid, secret = os.environ.get("SPOTIFY_CLIENT_ID"), os.environ.get("SPOTIFY_CLIENT_SECRET")
    if not (cid and secret):
        raise RuntimeError("SPOTIFY_CLIENT_ID / SPOTIFY_CLIENT_SECRET are not set")
    basic = base64.b64encode(f"{cid}:{secret}".encode()).decode()
    tok = json.loads(http_get(
        "https://accounts.spotify.com/api/token",
        headers={"Authorization": f"Basic {basic}",
                 "Content-Type": "application/x-www-form-urlencoded"},
        data=urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()))
    auth = {"Authorization": f"Bearer {tok['access_token']}"}
    items, url = [], f"https://api.spotify.com/v1/shows/{show_id}/episodes?" + \
        urllib.parse.urlencode({"market": "US", "limit": 10})   # small pages: newer API caps
    for _ in range(30):                                          # ~300 episodes max
        page = json.loads(http_get(url, headers=auth))
        items += page.get("items") or []
        url = page.get("next")
        if not url:
            break
    return {"items": items}


def fetch_substack(feed_url):
    return http_get(feed_url)


# --------------------------------------------------------------------------
# merging
# --------------------------------------------------------------------------
def _unique_id(title, d, taken):
    eid = slug(title, date.fromisoformat(d))
    while eid in taken:
        eid += "-b"
    taken.add(eid)
    return eid


def _best_match(episodes, item, missing):
    """Existing episode (missing the given link) that this item most likely is."""
    best, best_score = None, MIN_SCORE
    for e in episodes:
        if e.get(missing):
            continue
        s = pair_score(item["title"], date.fromisoformat(item["date"]),
                       e["title"], date.fromisoformat(e["date"]))
        if s >= best_score:
            best, best_score = e, s
    return best


def merge_episodes(episodes, yt_items, sp_items, default_spotify=None):
    taken = {e["id"] for e in episodes}
    known_yt = {e.get("youtube_id") for e in episodes}
    known_sp = {e.get("spotify_url") for e in episodes}
    log = []
    for it in yt_items:
        if it["youtube_id"] in known_yt:
            continue
        target = _best_match(episodes, it, "youtube_url")
        if target:
            target.update(youtube_url=it["youtube_url"], youtube_id=it["youtube_id"])
            log.append(f"linked YouTube to: {target['title']}")
        else:
            episodes.append({
                "id": _unique_id(it["title"], it["date"], taken), "title": it["title"],
                "date": it["date"], "youtube_url": it["youtube_url"],
                "youtube_id": it["youtube_id"], "spotify_url": default_spotify,
                "substack_url": None, "thumbnail": None})
            log.append(f"new episode (YouTube): {it['title']}")
        known_yt.add(it["youtube_id"])
    for it in sp_items:
        if it["spotify_url"] in known_sp:
            continue
        target = _best_match(episodes, it, "spotify_url")
        if target:
            target["spotify_url"] = it["spotify_url"]
            log.append(f"linked Spotify to: {target['title']}")
        else:
            episodes.append({
                "id": _unique_id(it["title"], it["date"], taken), "title": it["title"],
                "date": it["date"], "youtube_url": None, "youtube_id": None,
                "spotify_url": it["spotify_url"], "substack_url": None,
                "image_url": it.get("image_url"), "thumbnail": None})
            log.append(f"new episode (Spotify): {it['title']}")
        known_sp.add(it["spotify_url"])
    episodes.sort(key=lambda e: e["date"], reverse=True)
    return log


def merge_articles(articles, items):
    taken = {a["id"] for a in articles}
    known = {a["substack_url"] for a in articles}
    log = []
    for it in items:
        if it["substack_url"] in known:
            continue
        articles.append({"id": _unique_id(it["title"], it["date"], taken), "title": it["title"],
                         "date": it["date"], "substack_url": it["substack_url"],
                         "image_url": it["image_url"], "thumbnail": None})
        known.add(it["substack_url"])
        log.append(f"new article: {it['title']}")
    articles.sort(key=lambda a: a["date"], reverse=True)
    return log


# --------------------------------------------------------------------------
def load(path, key_name):
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {key_name: []}


def save(path, data):
    Path(path).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=str(ROOT / "config.json"))
    ap.add_argument("--episodes", default=str(ROOT / "episodes.json"))
    ap.add_argument("--articles", default=str(ROOT / "articles.json"))
    ap.add_argument("--fixtures", help="directory with youtube.xml, spotify.json, substack.xml")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    cfg = json.loads(Path(args.config).read_text(encoding="utf-8"))["sync"]
    fx = Path(args.fixtures) if args.fixtures else None
    problems, yt_items, sp_items, art_items = [], [], [], []
    status = {}

    def source(name, enabled, fn):
        if not enabled:
            print(f"- {name}: not configured, skipped")
            status[name] = "not configured"
            return []
        try:
            items = fn()
            print(f"- {name}: {len(items)} items")
            status[name] = f"ok, {len(items)} items"
            return items
        except Exception as exc:  # one source failing must not block the others
            problems.append(f"{name}: {exc}")
            print(f"- {name}: FAILED ({exc})")
            status[name] = f"FAILED: {exc}"[:300]
            return []

    yt_items = source("YouTube", cfg.get("youtube_channel_id"), lambda: parse_youtube(
        (fx / "youtube.xml").read_bytes() if fx else fetch_youtube(cfg["youtube_channel_id"]),
        set(cfg.get("ignore_youtube_ids", []))))
    have_sp_keys = bool(os.environ.get("SPOTIFY_CLIENT_ID") and os.environ.get("SPOTIFY_CLIENT_SECRET"))
    sp_items = source("Spotify", (cfg.get("spotify_show_id") and have_sp_keys) or (fx and (fx / "spotify.json").exists()),
                      lambda: parse_spotify(
                          json.loads((fx / "spotify.json").read_text()) if fx
                          else fetch_spotify(cfg["spotify_show_id"])))
    art_items = source("Substack", cfg.get("substack_feed_url") or (fx and (fx / "substack.xml").exists()),
                       lambda: parse_substack(
                           (fx / "substack.xml").read_bytes() if fx
                           else fetch_substack(cfg["substack_feed_url"])))

    if not args.dry_run and not fx:  # lets us see what happened without reading run logs
        save(ROOT / "state" / "sync_status.json", status)
    ep_data, art_data = load(args.episodes, "episodes"), load(args.articles, "articles")
    # Without Spotify API access, assume every new YouTube episode is also on Spotify;
    # stories only say "link in bio", so the show link is enough.
    default_sp = None if sp_items or not cfg.get("spotify_show_id") else \
        f"https://open.spotify.com/show/{cfg['spotify_show_id']}"
    log = merge_episodes(ep_data["episodes"], yt_items, sp_items, default_sp)
    log += merge_articles(art_data.setdefault("articles", []), art_items)

    print(f"\n{len(log)} change(s)")
    for line in log:
        print("  +", line)
    if log and not args.dry_run:
        save(args.episodes, ep_data)
        if art_items:
            save(args.articles, art_data)
        print("files updated")
    if problems:
        print("\nProblems:", *problems, sep="\n  ")
        sys.exit(1 if not (yt_items or sp_items or art_items) else 0)


if __name__ == "__main__":
    main()
