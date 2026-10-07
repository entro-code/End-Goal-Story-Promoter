#!/usr/bin/env python3
"""Cut the YouTube and Spotify logos out of the brand panels as transparent PNGs.

Usage: python extract_logos.py youtube_panel.png spotify_panel.png out_dir
"""
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageOps

# Logos sit in the middle of the panel; the headline text is lower down.
SEARCH_BOX = (0, 500, 1080, 1250)


def is_youtube_red(r, g, b):
    return r > 180 and g < 90 and b < 90


def is_spotify_green(r, g, b):
    return g > 150 and r < 90 and b < 150


def cut(panel_path, keep, out_path):
    im = Image.open(panel_path).convert("RGB").crop(SEARCH_BOX)
    w, h = im.size
    px = im.load()
    mask = Image.new("L", (w, h), 0)
    mp = mask.load()
    for y in range(h):
        for x in range(w):
            if keep(*px[x, y]):
                mp[x, y] = 255
    bbox = mask.getbbox()
    if bbox is None:
        raise SystemExit(f"no logo found in {panel_path}")
    im, mask = im.crop(bbox), mask.crop(bbox)

    # Fill the interior (the white play triangle / dark sound bars) so the whole
    # logo shape is opaque, not just the coloured part.
    padded = ImageOps.expand(mask, border=3, fill=0)
    ImageDraw.floodfill(padded, (0, 0), 128)
    interior = padded.point(lambda p: 0 if p == 128 else 255)
    interior = interior.crop((3, 3, 3 + mask.width, 3 + mask.height))
    alpha = interior.filter(ImageFilter.GaussianBlur(0.8))

    out = im.convert("RGBA")
    out.putalpha(alpha)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.save(out_path)
    print("wrote", out_path, out.size)


def main():
    if len(sys.argv) != 4:
        raise SystemExit(__doc__)
    yt_panel, sp_panel, out_dir = sys.argv[1], sys.argv[2], Path(sys.argv[3])
    cut(yt_panel, is_youtube_red, out_dir / "logo_youtube.png")
    cut(sp_panel, is_spotify_green, out_dir / "logo_spotify.png")


if __name__ == "__main__":
    main()
