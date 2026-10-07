#!/usr/bin/env python3
"""Daily job: find new uploads -> plan the next stories -> render -> queue in Buffer.

  python run.py prepare   sync feeds, plan, render images into stories/, write
                          state/pending.json
  python run.py queue     send pending.json to Buffer, record successes in
                          state/history.json

Both accept --dry-run. In GitHub Actions the rendered images are committed and
pushed between the two steps so Buffer can fetch them from a public URL.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import buffer_client  # noqa: E402
import generate_story  # noqa: E402
import rotation  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (story-promoter)"}
STATE = ROOT / "state"
STORIES = ROOT / "stories"
KEEP_DAYS = 5


def load(path, default):
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def save(path, data):
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# --------------------------------------------------------------------------
# artwork
# --------------------------------------------------------------------------
def _download(url, dest):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=30) as r:
        dest.write_bytes(r.read())


def _valid_image(path):
    from PIL import Image
    try:
        with Image.open(path) as im:
            im.verify()
        return path.stat().st_size > 5000
    except Exception:
        return False


def fetch_art(item, cache_dir):
    """Path to the artwork for an item (YouTube thumbnail, else cover image)."""
    local = item.get("thumbnail")
    if local and (ROOT / local).exists():
        return ROOT / local
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"{item['id']}.jpg"
    if cached.exists() and _valid_image(cached):
        return cached
    urls = []
    if item.get("youtube_id"):
        vid = item["youtube_id"]
        urls += [f"https://i.ytimg.com/vi/{vid}/{n}.jpg"
                 for n in ("maxresdefault", "sddefault", "hqdefault")]
    if item.get("image_url"):
        urls.append(item["image_url"])
    for url in urls:
        try:
            _download(url, cached)
            if _valid_image(cached):
                return cached
        except Exception:
            continue
    return None


# --------------------------------------------------------------------------
# prepare
# --------------------------------------------------------------------------
def run_sync():
    r = subprocess.run([sys.executable, str(ROOT / "tools" / "sync_feeds.py")], cwd=ROOT)
    if r.returncode != 0:
        print("WARNING: feed sync reported problems; continuing with known episodes")


def cleanup_old_images(today):
    if not STORIES.exists():
        return
    for f in STORIES.glob("*.png"):
        try:
            stamp = datetime.strptime(f.name[:8], "%Y%m%d").date()
        except ValueError:
            continue
        if (today - stamp).days > KEEP_DAYS:
            f.unlink()


def prepare(args):
    cfg = generate_story.load_config()
    sched = cfg["schedule"]
    tz = ZoneInfo(sched["timezone"])
    now = datetime.fromisoformat(args.now) if args.now else datetime.now(tz).replace(tzinfo=None)

    if not args.no_sync:
        run_sync()
    episodes = load(ROOT / "episodes.json", {"episodes": []})["episodes"]
    articles = load(ROOT / "articles.json", {"articles": []}).get("articles", [])
    items = episodes + articles
    by_id = {i["id"]: i for i in items}
    history = load(STATE / "history.json", [])

    posts = rotation.plan(items, sched, now.date(), sched.get("horizon_days", 2), history,
                          skip={h["when"] for h in history}, now=now)
    look = sched.get("lookahead_hours")
    if look:  # keep few posts waiting in Buffer (free plan caps scheduled posts)
        limit = (now + timedelta(hours=look)).isoformat(timespec="minutes")
        posts = [p for p in posts if p["when"] <= limit]
    print(f"{len(posts)} new story slot(s) to fill")

    pending = []
    for p in posts:
        item = by_id[p["episode_id"]]
        when = datetime.fromisoformat(p["when"])
        label = f"{when:%a %b %d %H:%M}  {p['platform']:<8} {p['title']}"
        if args.dry_run:
            print("  would render:", label)
            continue
        art = fetch_art(item, ROOT / "art_cache")
        if art is None:
            print("  SKIPPED (no artwork):", label)
            continue
        name = f"{when:%Y%m%d-%H%M}-{p['platform']}-{item['id'][:30]}.png"
        generate_story.render_story(art, item["title"], p["platform"], STORIES / name, cfg)
        due = when.replace(tzinfo=tz).astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        pending.append({**{k: p[k] for k in ("when", "episode_id", "platform", "title", "link")},
                        "due_at_utc": due, "image": f"stories/{name}"})
        print("  rendered:", label)

    if not args.dry_run:
        save(STATE / "pending.json", {"posts": pending})
        cleanup_old_images(now.date())


# --------------------------------------------------------------------------
# queue
# --------------------------------------------------------------------------
def url_is_public(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=20) as r:
            return r.status == 200 and r.headers.get("Content-Type", "").startswith("image")
    except Exception:
        return False


def wait_until_public(url, tries=8, delay=15):
    for _ in range(tries):
        if url_is_public(url):
            return True
        time.sleep(delay)
    return False


def media_base_url(cfg):
    base = (cfg.get("media") or {}).get("base_url")
    if base:
        return base.rstrip("/")
    repo, ref = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_REF_NAME", "main")
    if repo:
        return f"https://raw.githubusercontent.com/{repo}/{ref}"
    raise SystemExit("No public image address: set media.base_url in config.json "
                     "(or run inside GitHub Actions)")


def queue(args):
    cfg = generate_story.load_config()
    pending = load(STATE / "pending.json", {"posts": []})["posts"]
    if not pending:
        print("Nothing to queue.")
        return 0
    if args.dry_run:
        for p in pending:
            print(f"  would queue {p['due_at_utc']}  {p['platform']:<8} {p['title']}")
        return 0

    key = os.environ.get("BUFFER_API_KEY")
    if not key:
        raise SystemExit("BUFFER_API_KEY is not set")
    base = media_base_url(cfg)
    channel = buffer_client.instagram_channel(
        key, (cfg.get("buffer") or {}).get("instagram_channel_name"))

    history, failed = load(STATE / "history.json", []), []
    for p in pending:
        url = f"{base}/{p['image']}"
        try:
            if not wait_until_public(url):
                raise buffer_client.BufferError(f"image not publicly reachable yet: {url}")
            post = buffer_client.create_story(key, channel, url, p["due_at_utc"])
        except buffer_client.BufferError as exc:
            print(f"  FAILED {p['when']} {p['title']}: {exc}")
            failed.append(p)
            continue
        history.append({"when": p["when"], "episode_id": p["episode_id"],
                        "platform": p["platform"], "buffer_post_id": post.get("id")})
        save(STATE / "history.json", history)  # keep progress even if a later post fails
        print(f"  queued {p['when']}  {p['platform']:<8} {p['title']}")
    save(STATE / "pending.json", {"posts": failed})
    return 1 if failed else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("step", choices=["prepare", "queue"])
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-sync", action="store_true", help="skip the feed sync")
    ap.add_argument("--now", help="pretend it is this local time (YYYY-MM-DDTHH:MM)")
    args = ap.parse_args(argv)
    if args.step == "prepare":
        prepare(args)
        return 0
    return queue(args)


if __name__ == "__main__":
    sys.exit(main())
