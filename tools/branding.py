"""Channel art: profile picture (800x800) and banner (2560x1440), drawn from scratch (no stock art, no fonts
beyond the bundled OFL Montserrat).

    python tools/branding.py "Untamed Facts" "One wild animal fact every day"
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "channel_art"
FONTS = ROOT / "assets" / "fonts"
BG = (10, 22, 16)
GOLD = (255, 205, 40)


def eye(size: int, rng: np.random.Generator) -> Image.Image:
    """A predator's eye: amber iris with radial fibres, a vertical slit pupil, a wet highlight."""
    s = size
    y, x = np.mgrid[0:s, 0:s].astype(np.float32)
    cx = cy = s / 2
    r = np.hypot(x - cx, y - cy) / (s / 2)
    ang = np.arctan2(y - cy, x - cx)
    fib = np.zeros_like(r)
    for k, amp in ((37, 0.10), (61, 0.07), (113, 0.05), (197, 0.04)):
        fib += amp * np.sin(ang * k + rng.uniform(0, 6.3) + 2.0 * np.sin(r * 9 + rng.uniform(0, 6.3)))
    base = np.clip(1.0 - 0.55 * r ** 1.6 + fib, 0, 1)
    col = np.zeros((s, s, 3), np.float32)
    inner = np.array([255, 226, 90], np.float32)
    outer = np.array([196, 92, 12], np.float32)
    t = np.clip(r, 0, 1)[..., None]
    col[:] = inner * (1 - t) + outer * t
    col *= (0.55 + 0.45 * base)[..., None]
    rim = np.clip((r - 0.86) / 0.14, 0, 1)[..., None]                  # dark limbal ring
    col = col * (1 - 0.85 * rim)
    pupil_w = 0.17 * (1 - ((y - cy) / (s / 2)) ** 2) ** 0.5 * (s / 2)   # vertical slit, tapered
    pupil = np.abs(x - cx) < pupil_w
    col[pupil] = (8, 6, 4)
    edge = np.clip(1 - np.abs(np.abs(x - cx) - pupil_w) / 3.0, 0, 1)[..., None] * (~pupil)[..., None]
    col = col * (1 - 0.6 * edge)
    alpha = (r <= 1.0).astype(np.float32) * 255
    img = Image.fromarray(np.dstack([np.clip(col, 0, 255), alpha]).astype(np.uint8), "RGBA")
    hl = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(hl)
    d.ellipse([s * 0.28, s * 0.20, s * 0.42, s * 0.33], fill=(255, 255, 255, 230))
    d.ellipse([s * 0.58, s * 0.62, s * 0.64, s * 0.68], fill=(255, 255, 255, 120))
    hl = hl.filter(ImageFilter.GaussianBlur(s * 0.008))
    return Image.alpha_composite(img, hl)


def avatar(path: Path) -> None:
    S = 800
    img = Image.new("RGB", (S, S), BG)
    glow = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([90, 90, S - 90, S - 90], fill=(255, 170, 30, 110))
    img.paste(glow.filter(ImageFilter.GaussianBlur(70)), (0, 0), glow.filter(ImageFilter.GaussianBlur(70)))
    e = eye(560, np.random.default_rng(7))
    img.paste(e, (120, 120), e)
    ring = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    ImageDraw.Draw(ring).ellipse([112, 112, S - 112, S - 112], outline=(*GOLD, 255), width=10)
    img.paste(ring, (0, 0), ring)
    img.save(path, quality=95)


def banner(path: Path, name: str, tagline: str) -> None:
    W, H = 2560, 1440
    y = np.linspace(0, 1, H)[:, None, None]
    x = np.linspace(0, 1, W)[None, :, None]
    top, bot = np.array([16, 40, 28]), np.array([6, 14, 10])
    arr = top * (1 - y) + bot * y + 30 * np.exp(-(((x - 0.30) / 0.18) ** 2 + ((y - 0.5) / 0.25) ** 2))[..., :] * np.array([1.0, 0.6, 0.1])
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8), "RGB")
    # everything important stays in YouTube's 1546x423 safe area (centred)
    e = eye(330, np.random.default_rng(7))
    sx = (W - 1546) // 2
    img.paste(e, (sx + 40, (H - 330) // 2), e)
    d = ImageDraw.Draw(img)
    f1 = ImageFont.truetype(str(FONTS / "Montserrat-Black.ttf"), 132)
    f2 = ImageFont.truetype(str(FONTS / "Montserrat-Bold.ttf"), 54)
    tx = sx + 430
    d.text((tx, H / 2 - 120), name.upper(), font=f1, fill=(255, 255, 255))
    d.text((tx + 4, H / 2 + 50), tagline, font=f2, fill=GOLD)
    img.save(path, quality=92)


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "Untamed Facts"
    tag = sys.argv[2] if len(sys.argv) > 2 else "One wild animal fact every day"
    OUT.mkdir(exist_ok=True)
    avatar(OUT / "profile.jpg")
    banner(OUT / "banner.jpg", name, tag)
    print("saved", OUT / "profile.jpg", OUT / "banner.jpg")
