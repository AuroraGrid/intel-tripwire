"""Generate AURORA social-share preview images (OG + square)."""
from __future__ import annotations

import math
import random
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "static" / "share"
DESKTOP = Path.home() / "Desktop" / "AURORA-share"

# Brand palette from platform.html
BG = (7, 11, 20)
PANEL = (13, 20, 34)
PANEL2 = (17, 27, 44)
LINE = (34, 49, 74)
TEXT = (238, 244, 255)
MUTED = (143, 162, 194)
CYAN = (98, 215, 255)
BLUE = (71, 125, 255)
GREEN = (66, 211, 146)
YELLOW = (244, 201, 93)
ORANGE = (255, 154, 85)
RED = (255, 101, 119)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = [
        r"C:\Windows\Fonts\segoeuib.ttf" if bold else r"C:\Windows\Fonts\segoeui.ttf",
        r"C:\Windows\Fonts\arialbd.ttf" if bold else r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\calibrib.ttf" if bold else r"C:\Windows\Fonts\calibri.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def lerp(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def radial_glow(img: Image.Image, cx: float, cy: float, radius: float, color, strength: float = 0.35):
    overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    steps = 40
    for i in range(steps, 0, -1):
        t = i / steps
        r = radius * t
        alpha = int(255 * strength * (1 - t) ** 2)
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(*color, alpha))
    img.alpha_composite(overlay)


def rounded_rect(draw, box, radius, fill=None, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def draw_badge(draw, xy, text, color, f):
    x, y = xy
    pad_x, pad_y = 10, 5
    bbox = draw.textbbox((0, 0), text, font=f)
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    rounded_rect(
        draw,
        [x, y, x + w + pad_x * 2, y + h + pad_y * 2],
        999,
        fill=(color[0], color[1], color[2], 40),
        outline=(*color, 160),
        width=1,
    )
    draw.text((x + pad_x, y + pad_y - 1), text, font=f, fill=color)
    return w + pad_x * 2 + 8


def draw_map_panel(base: Image.Image, box):
    """Mini world-map style panel with event dots."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    panel = Image.new("RGBA", (w, h), (*PANEL2, 255))
    d = ImageDraw.Draw(panel)

    # grid
    for gx in range(0, w, 28):
        d.line([(gx, 0), (gx, h)], fill=(*CYAN, 18), width=1)
    for gy in range(0, h, 28):
        d.line([(0, gy), (w, gy)], fill=(*CYAN, 18), width=1)

    # soft continents as abstract blobs (not accurate maps — decorative)
    blobs = [
        (0.12, 0.35, 0.16, 0.22),
        (0.28, 0.42, 0.10, 0.18),
        (0.48, 0.30, 0.18, 0.20),
        (0.68, 0.28, 0.14, 0.16),
        (0.78, 0.55, 0.12, 0.14),
        (0.55, 0.62, 0.14, 0.12),
    ]
    for bx, by, bw, bh in blobs:
        cx, cy = bx * w, by * h
        rw, rh = bw * w, bh * h
        d.ellipse([cx - rw, cy - rh, cx + rw, cy + rh], fill=(20, 37, 60, 220), outline=(47, 82, 109, 180))

    # event markers with realistic-ish distribution
    rng = random.Random(42)
    markers = [
        (0.72, 0.38, RED, "critical"),
        (0.22, 0.42, ORANGE, "high"),
        (0.48, 0.48, YELLOW, "medium"),
        (0.82, 0.62, GREEN, "low"),
        (0.35, 0.55, ORANGE, "high"),
        (0.58, 0.28, RED, "critical"),
        (0.15, 0.58, CYAN, "low"),
        (0.65, 0.55, YELLOW, "medium"),
        (0.40, 0.32, GREEN, "low"),
        (0.88, 0.40, ORANGE, "high"),
    ]
    for mx, my, color, _ in markers:
        px, py = mx * w + rng.uniform(-4, 4), my * h + rng.uniform(-4, 4)
        r = 5
        # glow
        d.ellipse([px - r * 2.2, py - r * 2.2, px + r * 2.2, py + r * 2.2], fill=(*color, 45))
        d.ellipse([px - r, py - r, px + r, py + r], fill=(*color, 255), outline=(255, 255, 255, 220), width=2)

    # header strip
    d.rectangle([0, 0, w, 36], fill=(9, 17, 30, 240))
    d.line([(0, 36), (w, 36)], fill=(*LINE, 255), width=1)
    f = font(14, bold=True)
    d.text((14, 10), "Global event map", font=f, fill=TEXT)
    fm = font(12)
    d.text((w - 150, 11), "31 geolocated · LIVE", font=fm, fill=MUTED)

    # legend
    leg_y = h - 28
    d.rounded_rectangle([10, leg_y - 6, 210, h - 8], radius=8, fill=(5, 10, 17, 210), outline=(*LINE, 200))
    lx = 18
    for label, color in [("Critical", RED), ("High", ORANGE), ("Med", YELLOW), ("Low", GREEN)]:
        d.ellipse([lx, leg_y + 2, lx + 8, leg_y + 10], fill=color)
        d.text((lx + 12, leg_y), label, font=font(11), fill=MUTED)
        lx += 48

    # border
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=14, outline=(*LINE, 255), width=1)

    base.paste(panel, (x0, y0), panel)


def draw_feed_card(base, box, title, badges, action):
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    card = Image.new("RGBA", (w, h), (*PANEL, 255))
    d = ImageDraw.Draw(card)
    d.rounded_rectangle([0, 0, w - 1, h - 1], radius=12, fill=(*PANEL, 255), outline=(*LINE, 255), width=1)
    d.text((14, 12), title, font=font(14, bold=True), fill=TEXT)
    bx = 14
    for label, color in badges:
        bx += draw_badge(d, (bx, 38), label, color, font(11, bold=True))
    d.text((14, h - 28), action, font=font(12, bold=True), fill=CYAN)
    base.paste(card, (x0, y0), card)


def make_og(size=(1200, 630)) -> Image.Image:
    w, h = size
    img = Image.new("RGBA", size, (*BG, 255))
    radial_glow(img, w * 0.78, h * -0.05, 420, BLUE, 0.28)
    radial_glow(img, w * 0.15, h * 0.9, 360, CYAN, 0.14)

    d = ImageDraw.Draw(img)

    # left accent bar
    d.rectangle([0, 0, 6, h], fill=CYAN)

    # brand mark
    mark = Image.new("RGBA", (72, 72), (0, 0, 0, 0))
    md = ImageDraw.Draw(mark)
    md.rounded_rectangle([0, 0, 71, 71], radius=18, fill=(*BLUE, 255))
    # gradient-ish overlay
    for i in range(72):
        md.rectangle([0, i, 71, i + 1], fill=(*lerp(CYAN, BLUE, i / 71), 90))
    md.rounded_rectangle([0, 0, 71, 71], radius=18, outline=(*CYAN, 180), width=1)
    md.text((22, 12), "A", font=font(42, bold=True), fill=(5, 16, 28))
    img.paste(mark, (48, 42), mark)

    d.text((136, 48), "AURORA GRID", font=font(34, bold=True), fill=TEXT)
    d.text((138, 90), "OPERATIONS CONSOLE  ·  PRIVATE BETA", font=font(14, bold=True), fill=MUTED)

    # status pill
    rounded_rect(d, [900, 52, 1148, 92], 999, fill=(18, 40, 32, 230), outline=(*GREEN, 180), width=1)
    d.ellipse([920, 66, 932, 78], fill=GREEN)
    d.text((944, 62), "LIVE INTELLIGENCE", font=font(14, bold=True), fill=GREEN)

    # headline
    d.text((48, 150), "Evidence-first global monitoring", font=font(42, bold=True), fill=TEXT)
    d.text(
        (48, 210),
        "Incidents · verification · geo map · analyst workspace",
        font=font(22),
        fill=MUTED,
    )

    # map panel
    draw_map_panel(img, (48, 270, 720, 580))

    # right stack cards
    draw_feed_card(
        img,
        (744, 270, 1152, 360),
        "GeoNet M3.6 earthquake — Tokomaru Bay",
        [("high", ORANGE), ("disaster", MUTED)],
        "HOLD  ·  independent origins: 2",
    )
    draw_feed_card(
        img,
        (744, 376, 1152, 466),
        "Port incident prompts emergency response",
        [("critical", RED), ("conflict", MUTED)],
        "INVESTIGATE  ·  K-ALIGN: PLAUSIBLE",
    )
    draw_feed_card(
        img,
        (744, 482, 1152, 572),
        "Wildfire remains active — western Canada",
        [("high", ORANGE), ("disaster", MUTED)],
        "MONITOR  ·  confidence 72%",
    )

    # footer
    d.text((48, 596), "Invite-only beta  ·  Bearer token login  ·  No account signup", font=font(14), fill=MUTED)
    d.text((860, 596), "aurora · friends preview", font=font(14, bold=True), fill=CYAN)

    return img.convert("RGB")


def make_square(size=(1080, 1080)) -> Image.Image:
    w, h = size
    img = Image.new("RGBA", size, (*BG, 255))
    radial_glow(img, w * 0.5, h * 0.15, 500, BLUE, 0.3)
    radial_glow(img, w * 0.2, h * 0.85, 400, CYAN, 0.16)

    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, 8, h], fill=CYAN)

    # brand centered top
    mark = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
    md = ImageDraw.Draw(mark)
    md.rounded_rectangle([0, 0, 95, 95], radius=24, fill=(*BLUE, 255))
    for i in range(96):
        md.rectangle([0, i, 95, i + 1], fill=(*lerp(CYAN, BLUE, i / 95), 90))
    md.text((30, 16), "A", font=font(56, bold=True), fill=(5, 16, 28))
    img.paste(mark, ((w - 96) // 2, 72), mark)

    title = "AURORA GRID"
    f = font(48, bold=True)
    tw = d.textbbox((0, 0), title, font=f)[2]
    d.text(((w - tw) // 2, 190), title, font=f, fill=TEXT)

    sub = "OPERATIONS CONSOLE"
    fs = font(18, bold=True)
    sw = d.textbbox((0, 0), sub, font=fs)[2]
    d.text(((w - sw) // 2, 252), sub, font=fs, fill=MUTED)

    pill = "PRIVATE BETA  ·  LIVE"
    fp = font(16, bold=True)
    pw = d.textbbox((0, 0), pill, font=fp)[2]
    px = (w - pw) // 2
    rounded_rect(d, [px - 24, 300, px + pw + 24, 344], 999, fill=(18, 40, 32, 230), outline=(*GREEN, 180))
    d.ellipse([px - 8, 314, px + 4, 326], fill=GREEN)
    d.text((px + 12, 310), pill, font=fp, fill=GREEN)

    # map large
    draw_map_panel(img, (60, 380, w - 60, 820))

    line1 = "See global events, open incident reports,"
    line2 = "and explore the live intelligence map."
    f1 = font(22)
    for i, line in enumerate((line1, line2)):
        lw = d.textbbox((0, 0), line, font=f1)[2]
        d.text(((w - lw) // 2, 860 + i * 34), line, font=f1, fill=MUTED)

    foot = "Friends login with a shared bearer token"
    ff = font(18, bold=True)
    fw = d.textbbox((0, 0), foot, font=ff)[2]
    d.text(((w - fw) // 2, 960), foot, font=ff, fill=CYAN)

    d.text(((w - 280) // 2, 1010), "Works on mobile · desktop · tablet", font=font(16), fill=MUTED)

    return img.convert("RGB")


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    DESKTOP.mkdir(parents=True, exist_ok=True)

    og = make_og((1200, 630))
    square = make_square((1080, 1080))
    # X/Twitter large card also likes 2:1 — 1200x600 variant
    twitter = make_og((1200, 600))

    outputs = {
        "aurora-og-1200x630.png": og,  # Facebook, LinkedIn, Discord, Slack, iMessage
        "aurora-square-1080.png": square,  # Instagram, WhatsApp status-friendly
        "aurora-twitter-1200x600.png": twitter,  # X summary large card-ish
        "aurora-og-1200x630.jpg": og,  # wider compatibility
    }

    for name, im in outputs.items():
        path = OUT_DIR / name
        if name.endswith(".jpg"):
            im.save(path, "JPEG", quality=92, optimize=True)
        else:
            im.save(path, "PNG", optimize=True)
        desk = DESKTOP / name
        if name.endswith(".jpg"):
            im.save(desk, "JPEG", quality=92, optimize=True)
        else:
            im.save(desk, "PNG", optimize=True)
        print("wrote", path, "and", desk, im.size)

    # tiny HTML helper page friends can open offline
    html = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>AURORA GRID — Friends preview</title>
<meta property="og:title" content="AURORA GRID — Operations Console (Private Beta)">
<meta property="og:description" content="Evidence-first global monitoring. Incidents, verification, geo map, analyst workspace.">
<meta property="og:image" content="aurora-og-1200x630.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="AURORA GRID — Operations Console">
<meta name="twitter:description" content="Private beta · live intelligence map · bearer token login">
<meta name="twitter:image" content="aurora-og-1200x630.png">
<style>
body{{margin:0;background:#070b14;color:#eef4ff;font:16px/1.5 system-ui,sans-serif;padding:24px}}
h1{{font-size:22px;letter-spacing:.08em}} img{{max-width:100%;border-radius:12px;border:1px solid #22314a;margin:12px 0}}
.meta{{color:#8fa2c2}} code{{background:#111b2c;padding:2px 6px;border-radius:6px}}
</style></head><body>
<h1>AURORA GRID</h1>
<p class="meta">Share these images with friends so they know what the beta is before they open the link.</p>
<p><strong>Best for links / Discord / Facebook / LinkedIn / Slack:</strong></p>
<img src="aurora-og-1200x630.png" alt="AURORA OG preview 1200x630" width="1200" height="630">
<p><strong>Best for Instagram / square posts:</strong></p>
<img src="aurora-square-1080.png" alt="AURORA square preview" width="1080" height="1080">
<p class="meta">Login: open the share link → paste the bearer token → Connect. No password signup.</p>
</body></html>"""
    (OUT_DIR / "preview.html").write_text(html, encoding="utf-8")
    (DESKTOP / "preview.html").write_text(html, encoding="utf-8")
    print("preview.html written")


if __name__ == "__main__":
    main()
