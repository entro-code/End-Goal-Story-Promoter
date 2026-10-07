#!/usr/bin/env python3
"""End-to-end check of prepare -> queue against a fake Buffer (no network, no keys).

Run:  python tests/test_pipeline.py
Works on a throwaway copy of the project, so real state files are never touched.
"""
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent


def main():
    tmp = Path(tempfile.mkdtemp())
    root = tmp / "p"
    shutil.copytree(SRC, root, ignore=shutil.ignore_patterns(
        "previews", "stories", "art_cache", "state", ".git", "__pycache__"))
    cfgp = root / "config.json"
    cfg = json.loads(cfgp.read_text())
    cfg["schedule"]["lookahead_hours"] = None   # test the full-horizon path
    cfgp.write_text(json.dumps(cfg))
    sys.path.insert(0, str(root))
    os.chdir(root)
    import buffer_client
    import run

    from PIL import Image
    sample = tmp / "thumb.png"                           # stand-in thumbnail
    Image.new("RGB", (1280, 720), (60, 120, 80)).save(sample)
    run.fetch_art = lambda item, cache: sample          # no network for artwork
    run.url_is_public = lambda url: True                 # pretend GitHub is serving it
    os.environ.update(BUFFER_API_KEY="test-key", GITHUB_REPOSITORY="me/repo",
                      GITHUB_REF_NAME="main")

    created, calls = [], []

    def fake_post(payload, key):
        assert key == "test-key"
        q = payload["query"]
        calls.append(q.split("{")[0].strip()[:30])
        if "organizations" in q:
            return {"data": {"account": {"organizations": [{"id": "org1", "name": "o"}]}}}
        if "channels(" in q:
            return {"data": {"channels": [
                {"id": "ch_ig", "name": "endgoalpodcast", "service": "instagram"},
                {"id": "ch_tt", "name": "endgoalpodcast", "service": "tiktok"}]}}
        if "createPost" in q:
            inp = payload["variables"]["input"]
            created.append(inp)
            return {"data": {"createPost": {"post": {"id": f"post{len(created)}",
                                                      "dueAt": inp["dueAt"]}}}}
        raise AssertionError("unexpected query: " + q)

    buffer_client._post = fake_post

    # ---- day 1: 17:30 local, so 20:00 today + four slots tomorrow ----
    assert run.main(["prepare", "--no-sync", "--now", "2026-10-07T17:30"]) == 0
    pending = json.loads((root / "state/pending.json").read_text())["posts"]
    assert len(pending) == 5, f"expected 5 pending, got {len(pending)}"
    for p in pending:
        assert (root / p["image"]).exists(), p["image"]
    assert pending[0]["due_at_utc"] == "2026-10-08T00:00:00.000Z", pending[0]["due_at_utc"]

    assert run.main(["queue"]) == 0
    assert len(created) == 5
    for inp in created:
        assert inp["channelId"] == "ch_ig"
        assert inp["schedulingType"] == "automatic" and inp["mode"] == "customScheduled"
        assert inp["metadata"]["instagram"]["type"] == "story"
        url = inp["assets"][0]["image"]["url"]
        assert url.startswith("https://raw.githubusercontent.com/me/repo/main/stories/"), url
    hist = json.loads((root / "state/history.json").read_text())
    assert len(hist) == 5 and all(h["buffer_post_id"] for h in hist)
    assert json.loads((root / "state/pending.json").read_text())["posts"] == []

    # ---- same moment again: nothing new, no duplicates ----
    run.main(["prepare", "--no-sync", "--now", "2026-10-07T17:30"])
    assert json.loads((root / "state/pending.json").read_text())["posts"] == []

    # ---- next morning: only the new day gets filled ----
    run.main(["prepare", "--no-sync", "--now", "2026-10-08T05:00"])
    nxt = json.loads((root / "state/pending.json").read_text())["posts"]
    assert len(nxt) == 4 and all(p["when"].startswith("2026-10-09") for p in nxt), nxt
    run.main(["queue"])
    hist = json.loads((root / "state/history.json").read_text())
    assert len(hist) == 9
    eps = [h["episode_id"] for h in sorted(hist, key=lambda h: h["when"])]
    assert all(a != b for a, b in zip(eps, eps[1:])), "same episode twice in a row"
    assert len({h["when"] for h in hist}) == 9, "a slot was filled twice"

    # ---- a Buffer failure keeps the post for the next run ----
    def failing_post(payload, key):
        if "createPost" in payload["query"]:
            return {"data": {"createPost": {"message": "Instagram rejected the image"}}}
        return fake_post(payload, key)
    buffer_client._post = failing_post
    run.main(["prepare", "--no-sync", "--now", "2026-10-09T05:00"])
    assert run.main(["queue"]) == 1
    left = json.loads((root / "state/pending.json").read_text())["posts"]
    assert len(left) == 4, "failed posts should stay pending"
    assert len(json.loads((root / "state/history.json").read_text())) == 9, "failures must not count as posted"

    shutil.rmtree(tmp, ignore_errors=True)
    print("OK: prepare -> queue pipeline behaves as expected")


if __name__ == "__main__":
    main()
