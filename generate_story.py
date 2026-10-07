#!/usr/bin/env python3
"""Render a 1080x1920 Instagram Story image promoting one link, in the
End Goal Podcast brand style (green gradient, wide heavy type, platform logo).

Usage:
  python generate_story.py --art thumb.jpg --title "Episode title" \
      --platform youtube --out story.png
  python generate_story.py --demo        # renders sample stories into ./previews
"""
import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

W, H = 1080, 1920
# Instagram's own UI covers roughly the top 250px and bottom 340px of a story,
# so everything important stays inside this band.
SAFE_TOP, SAFE_BOTTOM = 260, 1580
SIDE_MARGIN = 72
CONTENT_W = W - 2 * SIDE_MARGIN          # 936
CARD_MAX_H = 540
LOGO_H = 120

HERE = Path(__file__).parent


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------
def load_config(path=None):
    path = Path(path) if path else HERE / "config.json"
    with open(path) as f:
        return json.load(f)


def hex_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def load_font(spec, size):
    """spec = {"path": ..., "axes": [wght, wdth], "fallback": ...}"""
    path = Path(spec["path"])
    if not path.is_absolute():
        path = HERE / path
    if path.exists():
        font = ImageFont.truetype(str(path), size)
        if spec.get("axes"):
            font.set_variation_by_axes(spec["axes"])
        return font
    fb = spec.get("fallback")
    if fb and Path(fb).exists():
        return ImageFont.truetype(fb, size)
    return ImageFont.load_default(size)


def aa_round_rect(img, box, radius, fill):
    """Anti-aliased rounded rectangle. fill is (r, g, b) or (r, g, b, a)."""
    x0, y0, x1, y1 = [int(v) for v in box]
    w, h = x1 - x0, y1 - y0
    s = 4
    mask = Image.new("L", (w * s, h * s), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, w * s - 1, h * s - 1), radius=radius * s, fill=255
    )
    mask = mask.resize((w, h), Image.LANCZOS)
    alpha = fill[3] if len(fill) == 4 else 255
    if alpha < 255:
        mask = mask.point(lambda p: p * alpha // 255)
    img.paste(tuple(fill[:3]), (x0, y0, x1, y1), mask)


def draw_text(img, xy, text, font, fill, anchor="ma", shadow=True):
    """Text with a soft drop shadow, like the brand panels."""
    d = ImageDraw.Draw(img)
    if shadow:
        l, t, r, b = d.textbbox(xy, text, font=font, anchor=anchor)
        pad = 40
        box = (l - pad, t - pad, r + pad, b + pad)
        layer = Image.new("L", (box[2] - box[0], box[3] - box[1]), 0)
        ImageDraw.Draw(layer).text(
            (xy[0] - box[0], xy[1] - box[1] + 5), text, font=font, fill=150, anchor=anchor
        )
        layer = layer.filter(ImageFilter.GaussianBlur(9))
        img.paste((0, 0, 0), box, layer)
    d.text(xy, text, font=font, fill=fill, anchor=anchor)


# --------------------------------------------------------------------------
# background: the brand gradient (dark green corners, bright glow bottom-left,
# near-black centre)
# --------------------------------------------------------------------------
def make_background(theme):
    g = theme["gradient"]
    corners = Image.new("RGB", (2, 2))
    corners.putpixel((0, 0), hex_rgb(g["top_left"]))
    corners.putpixel((1, 0), hex_rgb(g["top_right"]))
    corners.putpixel((0, 1), hex_rgb(g["bottom_left"]))
    corners.putpixel((1, 1), hex_rgb(g["bottom_right"]))
    bg = corners.resize((W, H), Image.BILINEAR)

    # Pull the middle toward near-black with a big soft radial.
    dark = Image.new("RGB", (W, H), hex_rgb(g["center"]))
    # Sized to reach past every canvas edge so the glow never ends in a hard seam.
    rw, rh = int(W * 1.5), int(H * 1.25)
    radial = Image.radial_gradient("L").resize((rw, rh), Image.BILINEAR)
    radial = radial.point(lambda p: int(255 - (255 - p) * 0.9))   # 0 = fully dark
    mask = Image.new("L", (W, H), 255)
    cx, cy = int(W * 0.62), int(H * 0.45)
    mask.paste(radial, (cx - rw // 2, cy - rh // 2))
    return Image.composite(bg, dark, mask)


# --------------------------------------------------------------------------
# pieces
# --------------------------------------------------------------------------
def draw_card(img, art, x, y, cw, ch, radius=36):
    pad = 70
    shadow = Image.new("L", (cw + pad * 2, ch + pad * 2), 0)
    ImageDraw.Draw(shadow).rounded_rectangle(
        (pad, pad, pad + cw, pad + ch), radius=radius, fill=190
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(28))
    img.paste((0, 0, 0), (x - pad, y - pad + 24, x + cw + pad, y + ch + pad + 24), shadow)

    fitted = ImageOps.fit(art.convert("RGB"), (cw, ch), Image.LANCZOS)
    s = 4
    mask = Image.new("L", (cw * s, ch * s), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, cw * s - 1, ch * s - 1), radius=radius * s, fill=255
    )
    mask = mask.resize((cw, ch), Image.LANCZOS)
    img.paste(fitted, (x, y), mask)


def substack_logo(size, orange):
    """Simple Substack-style mark: orange tile, two bars and a bookmark."""
    s = 4
    big = size * s
    tile = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    d = ImageDraw.Draw(tile)
    d.rounded_rectangle((0, 0, big - 1, big - 1), radius=int(big * 0.14), fill=orange + (255,))
    white = (255, 255, 255, 255)
    d.rectangle((big * 0.2, big * 0.21, big * 0.8, big * 0.30), fill=white)
    d.rectangle((big * 0.2, big * 0.37, big * 0.8, big * 0.46), fill=white)
    d.polygon([(big * 0.2, big * 0.54), (big * 0.8, big * 0.54),
               (big * 0.8, big * 0.86), (big * 0.5, big * 0.68),
               (big * 0.2, big * 0.86)], fill=white)
    return tile.resize((size, size), Image.LANCZOS)


def load_logo(plat, theme):
    if plat.get("logo"):
        logo = Image.open(HERE / plat["logo"]).convert("RGBA")
        w = int(logo.width * LOGO_H / logo.height)
        return logo.resize((w, LOGO_H), Image.LANCZOS)
    return substack_logo(LOGO_H, hex_rgb(theme["substack_orange"]))


def wrap_lines(draw, text, font, max_w):
    lines, cur = [], ""
    for w in text.split():
        trial = f"{cur} {w}".strip()
        if draw.textlength(trial, font=font) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def balanced_wrap(draw, text, font, max_w):
    """Fewest lines, then the narrowest width that keeps that line count, so
    lines come out even and no lone word is left on the last line."""
    lines = wrap_lines(draw, text, font, max_w)
    n = len(lines)
    if n < 2:
        return lines
    lo, hi, best = int(max_w * 0.5), max_w, lines
    while lo < hi:
        mid = (lo + hi) // 2
        trial = wrap_lines(draw, text, font, mid)
        if len(trial) <= n and all(draw.textlength(l, font=font) <= mid for l in trial):
            best, hi = trial, mid
        else:
            lo = mid + 1
    return best


def fit_text(draw, text, font_spec, max_w, max_lines, sizes):
    """Largest size that fits in max_lines (ellipsize as a last resort)."""
    for size in sizes:
        font = load_font(font_spec, size)
        if len(wrap_lines(draw, text, font, max_w)) <= max_lines:
            return font, balanced_wrap(draw, text, font, max_w)
    font = load_font(font_spec, sizes[-1])
    lines = wrap_lines(draw, text, font, max_w)[:max_lines]
    while lines[-1] and draw.textlength(lines[-1] + "…", font=font) > max_w:
        lines[-1] = lines[-1][:-1]
    lines[-1] = lines[-1].rstrip() + "…"
    return font, lines


# --------------------------------------------------------------------------
# main render
# --------------------------------------------------------------------------
def render_story(art_path, title, platform, out_path, config):
    theme, fonts = config["theme"], config["fonts"]
    plat = config["platforms"][platform]
    text_rgb, muted_rgb = hex_rgb(theme["text"]), hex_rgb(theme["muted_text"])

    img = make_background(theme)
    draw = ImageDraw.Draw(img)

    art = Image.open(art_path)
    aspect = min(max(art.width / art.height, 1.0), 16 / 9)
    ch = min(CARD_MAX_H, int(CONTENT_W / aspect))
    cw = CONTENT_W if ch < CARD_MAX_H else int(ch * aspect)

    title_font, title_lines = fit_text(
        draw, title.upper(), fonts["title"], CONTENT_W, 3, (54, 48, 44, 40, 36))
    title_lh = int(title_font.size * 1.18)

    cta_font, cta_lines = fit_text(
        draw, plat["cta"], fonts["display"], CONTENT_W, 3, (72, 66, 60, 54))
    cta_lh = int(cta_font.size * 1.1)

    small_font = load_font(fonts["small"], 28)
    logo = load_logo(plat, theme)
    pill_h = 62
    gaps = dict(card=48, title=46, logo=30, cta=34)

    total = (ch + gaps["card"] + title_lh * len(title_lines) + gaps["title"]
             + LOGO_H + gaps["logo"] + cta_lh * len(cta_lines) + gaps["cta"] + pill_h)
    y = SAFE_TOP + max(0, (SAFE_BOTTOM - SAFE_TOP - total) // 2)

    # artwork card
    draw_card(img, art, (W - cw) // 2, y, cw, ch)
    y += ch + gaps["card"]

    # episode / article title
    for line in title_lines:
        draw_text(img, (W // 2, y), line, title_font, text_rgb)
        y += title_lh
    y += gaps["title"]

    # platform logo
    img.paste(logo, ((W - logo.width) // 2, y), logo)
    y += LOGO_H + gaps["logo"]

    # call to action, brand-panel style
    for line in cta_lines:
        draw_text(img, (W // 2, y), line, cta_font, text_rgb)
        y += cta_lh
    y += gaps["cta"]

    # "link in bio" pill
    cap = config.get("caption", "LINK IN BIO")
    pw = int(draw.textlength(cap, font=small_font)) + 72
    aa_round_rect(img, ((W - pw) // 2, y, (W + pw) // 2, y + pill_h), pill_h // 2,
                  (255, 255, 255, 38))
    ImageDraw.Draw(img).text((W // 2, y + pill_h // 2 + 1), cap, font=small_font,
                             fill=muted_rgb, anchor="mm")

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, "PNG", optimize=True)
    return out_path


# --------------------------------------------------------------------------
# demo mode: placeholder artwork so the layout can be judged without real assets
# --------------------------------------------------------------------------
def placeholder_art(path, size, c1, c2, label, config):
    w, h = size
    base = Image.linear_gradient("L").resize((w, h))
    a, b = Image.new("RGB", (w, h), c1), Image.new("RGB", (w, h), c2)
    art = Image.composite(b, a, base.rotate(-30, expand=False, fillcolor=0))
    f = ImageFont.truetype(str(HERE / "fonts/Archivo-Variable.ttf"), int(h * 0.1))
    f.set_variation_by_axes([900, 125])
    ImageDraw.Draw(art).text((w // 2, h // 2), label, font=f, fill=(255, 255, 255), anchor="mm")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    art.save(path, quality=92)
    return path


def run_demo(config, out_dir):
    out_dir, samples = Path(out_dir), HERE / "samples"
    title = "They Kept Breaking Their Promises. Now We're Sick of It"
    yt = placeholder_art(samples / "youtube_thumb.jpg", (1280, 720),
                         (30, 20, 60), (200, 70, 60), "THUMBNAIL", config)
    sp = placeholder_art(samples / "spotify_art.jpg", (1000, 1000),
                         (10, 60, 50), (40, 150, 90), "COVER ART", config)
    sub = placeholder_art(samples / "substack_cover.jpg", (1456, 816),
                          (60, 30, 10), (230, 120, 40), "ARTICLE COVER", config)
    jobs = [(yt, title, "youtube"), (sp, title, "spotify"),
            (sub, "Why Our Generation Stopped Trusting the Plan", "substack")]
    return [render_story(a, t, p, out_dir / f"story_{p}.png", config) for a, t, p in jobs]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", help="path to config.json")
    ap.add_argument("--demo", action="store_true", help="render sample stories")
    ap.add_argument("--art", help="thumbnail / cover image path")
    ap.add_argument("--title")
    ap.add_argument("--platform", choices=["youtube", "spotify", "substack"])
    ap.add_argument("--out", default="story.png")
    args = ap.parse_args()

    config = load_config(args.config)
    if args.demo:
        for p in run_demo(config, HERE / "previews"):
            print("wrote", p)
        return
    if not (args.art and args.title and args.platform):
        ap.error("--art, --title and --platform are required (or use --demo)")
    print("wrote", render_story(args.art, args.title, args.platform, args.out, config))


if __name__ == "__main__":
    main()
