"""The edit: shots, motion, captions, overlays, and the encode.

Every shot is either a photo with camera motion (push in / pull out / pan towards the sharpest part of the frame,
which is almost always the animal) or a video segment cropped to 9:16 around the subject. Low-resolution landscape
clips are shown full-width over a blurred copy instead of being blown up. Each cut lands on a caption change and
opens with a small punch-in, the rhythm top animal-fact Shorts use.
"""
from __future__ import annotations

import logging
import math
import random
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

from . import config, footage
from .footage import Asset
from .script import Script, Subject

log = logging.getLogger("wild.render")
W, H, FPS = config.W, config.H, config.FPS
FONTS = config.ASSETS / "fonts"
YELLOW = (255, 222, 33)
CAPTION_Y = 0.635            # caption block centre, fraction of height (clear of the Shorts UI)
CAPTION_MAX_W = 860


# ---- captions -----------------------------------------------------------------------------------------------
@dataclass
class Word:
    text: str
    hi: bool
    start: float
    end: float
    line: int


@dataclass
class Chunk:
    words: list[Word]
    start: float
    end: float = 0.0


def make_words(sc: Script, timed: list[dict]) -> list[Word]:
    out, k = [], 0
    for li, line in enumerate(sc.lines):
        for w, hi in zip(line.words, line.hi):
            t = timed[k]
            out.append(Word(w, hi, t["start"], t["end"], li))
            k += 1
    return out


def chunk_words(words: list[Word]) -> list[Chunk]:
    """2-4 word caption groups that break at punctuation and never straddle two sentences."""
    chunks: list[Chunk] = []
    cur: list[Word] = []

    def flush():
        if cur:
            chunks.append(Chunk(list(cur), cur[0].start))
            cur.clear()

    for i, w in enumerate(words):
        if cur and cur[-1].line != w.line:
            flush()
        cur.append(w)
        chars = sum(len(x.text) + 1 for x in cur)
        nxt = words[i + 1] if i + 1 < len(words) else None
        end_punct = w.text[-1:] in ",.!?;:" or w.text.endswith(("—", "-"))
        last_in_line = nxt is None or nxt.line != w.line
        remaining = 0
        if nxt is not None and not last_in_line:
            remaining = sum(1 for x in words[i + 1:] if x.line == w.line)
        keep_with_next = any(ch.isdigit() for ch in w.text) and not end_punct    # "860 / volts" never splits
        if last_in_line:
            flush()
        elif keep_with_next and len(cur) < 5:
            pass
        elif (len(cur) >= 4 or chars >= 20 or (end_punct and len(cur) >= 2)) and remaining >= 2:
            flush()
    for i, c in enumerate(chunks):
        nxt = chunks[i + 1].start if i + 1 < len(chunks) else c.words[-1].end + 0.6
        c.end = min(nxt, c.words[-1].end + 0.9) if i + 1 < len(chunks) else nxt
        if i + 1 < len(chunks):
            c.end = nxt
    return chunks


@lru_cache(maxsize=8)
def _font(name: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(str(FONTS / name), size)


def _layout(words: list[Word], font) -> list[list[Word]]:
    """Greedy lines within CAPTION_MAX_W, balanced to two lines when it is long."""
    sp = font.getlength(" ")
    widths = [font.getlength(w.text.upper()) for w in words]
    total = sum(widths) + sp * (len(words) - 1)
    if total <= CAPTION_MAX_W and len(words) <= 3:
        return [words]
    best, best_score = [words], 1e9
    for cut in range(1, len(words)):
        a = sum(widths[:cut]) + sp * (cut - 1)
        b = sum(widths[cut:]) + sp * (len(words) - cut - 1)
        if max(a, b) > CAPTION_MAX_W:
            continue
        score = abs(a - b)
        if score < best_score:
            best, best_score = [words[:cut], words[cut:]], score
    return best


def render_caption(words: list[Word]) -> np.ndarray:
    """RGBA (premultiplied later) caption image: white, black outline, soft shadow, yellow keywords."""
    size = 76
    font = _font("Montserrat-Black.ttf", size)
    lines = _layout(words, font)
    while any(sum(font.getlength(w.text.upper()) for w in ln) > CAPTION_MAX_W for ln in lines) and size > 50:
        size -= 4
        font = _font("Montserrat-Black.ttf", size)
        lines = _layout(words, font)
    stroke = max(5, size // 9)
    lh = int(size * 1.12)
    cw, chh = CAPTION_MAX_W + 120, lh * len(lines) + 60
    img = Image.new("RGBA", (cw, chh), (0, 0, 0, 0))
    shadow = Image.new("RGBA", (cw, chh), (0, 0, 0, 0))
    d, ds = ImageDraw.Draw(img), ImageDraw.Draw(shadow)
    sp = font.getlength(" ")
    for li, ln in enumerate(lines):
        texts = [w.text.upper() for w in ln]
        lw = sum(font.getlength(t) for t in texts) + sp * (len(texts) - 1)
        x = (cw - lw) / 2
        y = 30 + li * lh
        for w, t in zip(ln, texts):
            fill = YELLOW if w.hi else (255, 255, 255)
            ds.text((x + 4, y + 7), t, font=font, fill=(0, 0, 0, 170), stroke_width=stroke, stroke_fill=(0, 0, 0, 170))
            d.text((x, y), t, font=font, fill=fill, stroke_width=stroke, stroke_fill=(0, 0, 0))
            x += font.getlength(t) + sp
    from PIL import ImageFilter
    shadow = shadow.filter(ImageFilter.GaussianBlur(6))
    out = Image.alpha_composite(shadow, img)
    return np.array(out)


def text_image(text: str, font_name: str, size: int, alpha: int, shadow: bool = True) -> np.ndarray:
    font = _font(font_name, size)
    w = int(font.getlength(text)) + 30
    img = Image.new("RGBA", (w, size + 24), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    if shadow:
        d.text((17, 14), text, font=font, fill=(0, 0, 0, int(alpha * 0.8)))
    d.text((15, 12), text, font=font, fill=(255, 255, 255, alpha))
    return np.array(img)


def blend(frame: np.ndarray, rgba: np.ndarray, cx: float, cy: float, scale: float = 1.0, opacity: float = 1.0) -> None:
    """Alpha-composite an RGBA image (centred at cx, cy) onto a BGR frame in place."""
    if scale != 1.0:
        rgba = cv2.resize(rgba, None, fx=scale, fy=scale, interpolation=cv2.INTER_LINEAR)
    h, w = rgba.shape[:2]
    x0, y0 = int(round(cx - w / 2)), int(round(cy - h / 2))
    fx0, fy0 = max(0, x0), max(0, y0)
    fx1, fy1 = min(W, x0 + w), min(H, y0 + h)
    if fx1 <= fx0 or fy1 <= fy0:
        return
    src = rgba[fy0 - y0:fy1 - y0, fx0 - x0:fx1 - x0]
    a = (src[:, :, 3:4].astype(np.float32) / 255.0) * opacity
    roi = frame[fy0:fy1, fx0:fx1].astype(np.float32)
    rgb = src[:, :, 2::-1].astype(np.float32)          # RGBA -> BGR
    frame[fy0:fy1, fx0:fx1] = (roi * (1 - a) + rgb * a).astype(np.uint8)


# ---- image helpers ------------------------------------------------------------------------------------------
_GRADE = None


def grade_lut() -> np.ndarray:
    global _GRADE
    if _GRADE is None:
        x = np.arange(256) / 255.0
        s = x + 0.10 * np.sin(2 * np.pi * x) / (2 * np.pi) * 2.2       # gentle S-curve
        _GRADE = np.clip(s * 255, 0, 255).astype(np.uint8)
    return _GRADE


def saturate(img: np.ndarray, amount: float = 1.12) -> np.ndarray:
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV).astype(np.float32)
    hsv[:, :, 1] = np.clip(hsv[:, :, 1] * amount, 0, 255)
    return cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)


@lru_cache(maxsize=1)
def vignette() -> np.ndarray:
    y, x = np.ogrid[:H, :W]
    r = np.sqrt(((x - W / 2) / (W / 2)) ** 2 * 0.8 + ((y - H / 2) / (H / 2)) ** 2)
    v = 1 - 0.28 * np.clip((r - 0.55) / 0.75, 0, 1) ** 1.6
    return v.astype(np.float32)[:, :, None]


def focus_point(img: np.ndarray) -> tuple[float, float]:
    """Centre of the sharpest detail (fraction of width/height): the in-focus animal, not the blurred backdrop."""
    h, w = img.shape[:2]
    k = 256 / max(h, w)
    g = cv2.cvtColor(cv2.resize(img, (max(8, int(w * k)), max(8, int(h * k))), interpolation=cv2.INTER_AREA),
                     cv2.COLOR_BGR2GRAY).astype(np.float32)
    lap = np.abs(cv2.Laplacian(g, cv2.CV_32F, ksize=3))
    lap = cv2.GaussianBlur(lap, (0, 0), 9)
    thr = np.quantile(lap, 0.9)
    m = np.where(lap >= thr, lap, 0)
    tot = m.sum()
    if tot <= 0:
        return 0.5, 0.5
    ys, xs = np.indices(m.shape)
    fx = float((m * xs).sum() / tot / m.shape[1])
    fy = float((m * ys).sum() / tot / m.shape[0])
    # lean towards the centre: photographers usually frame the animal near it
    return 0.65 * fx + 0.35 * 0.5, 0.65 * fy + 0.35 * 0.5


def photo_ok(img: np.ndarray) -> bool:
    if img is None or min(img.shape[:2]) < 600:
        return False
    small = cv2.resize(img, (96, 96), interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    if gray.mean() < 28 or gray.std() < 18:
        return False
    if (gray > 238).mean() > 0.35:            # specimen-on-white, scans, diagrams
        return False
    hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
    return hsv[:, :, 1].mean() > 22                          # colour photos only (no old black-and-white shots)


_HOG = None


def has_people(img: np.ndarray) -> bool:
    """Zoo visitors, keepers, tourists: an upright person in frame (OpenCV's pedestrian detector)."""
    global _HOG
    if _HOG is None:
        _HOG = cv2.HOGDescriptor()
        _HOG.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
    h, w = img.shape[:2]
    k = 480 / max(h, w)
    small = cv2.resize(img, (int(w * k), int(h * k)), interpolation=cv2.INTER_AREA)
    rects, weights = _HOG.detectMultiScale(small, winStride=(8, 8), padding=(8, 8), scale=1.06)
    return any(float(wt) > 0.9 for wt in np.ravel(weights)) if len(rects) else False


def appeal(img: np.ndarray) -> float:
    """How eye-catching a frame is: colourfulness (Hasler-Suesstrunk) plus fine detail."""
    small = cv2.resize(img, (240, int(240 * img.shape[0] / img.shape[1])), interpolation=cv2.INTER_AREA)
    b, g, r = [c.astype(np.float32) for c in cv2.split(small)]
    rg, yb = r - g, 0.5 * (r + g) - b
    colour = np.sqrt(rg.std() ** 2 + yb.std() ** 2) + 0.3 * np.sqrt(rg.mean() ** 2 + yb.mean() ** 2)
    sharp = cv2.Laplacian(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), cv2.CV_32F).var() ** 0.5
    return colour / 40 + sharp / 30


# ---- shots --------------------------------------------------------------------------------------------------
@dataclass
class Shot:
    start: float
    end: float
    subject: Subject
    asset: Asset | None = None
    path: Path | None = None
    move: str = "in"
    clip_start: float = 0.0
    speed: float = 1.0
    seed: int = 0
    first: bool = False

    @property
    def dur(self) -> float:
        return self.end - self.start


def plan_shots(chunks: list[Chunk], words: list[Word], sc: Script, total: float, rng: random.Random) -> list[Shot]:
    """Cut on caption changes, about every SHOT_SECONDS, one subject per sentence (its visual override)."""
    shots: list[Shot] = []
    cut_times = [c.start for c in chunks]
    line_start = {}
    for c in chunks:
        line_start.setdefault(c.words[0].line, c.start)
    lines = sorted(line_start)
    for i, li in enumerate(lines):
        a = 0.0 if i == 0 else line_start[li]
        b = line_start[lines[i + 1]] if i + 1 < len(lines) else total
        span = b - a
        n = max(1, round(span / config.SHOT_SECONDS))
        if i == 0 and span > 2.2:
            n = max(n, 2)                     # the hook gets a fast second image
        inner = [t for t in cut_times if a + 0.9 < t < b - 0.9]
        cuts = [a]
        for k in range(1, n):
            target = a + span * k / n
            if inner:
                t = min(inner, key=lambda t: abs(t - target))
                if t - cuts[-1] > 1.1 and abs(t - target) < 1.2:
                    cuts.append(t)
        cuts.append(b)
        for k in range(len(cuts) - 1):
            shots.append(Shot(cuts[k], cuts[k + 1], sc.lines[li].visual, seed=rng.randrange(1 << 30),
                              first=(i == 0 and k == 0)))
    return shots


class Picker:
    """Hands out footage per subject without repeats, fetching and checking lazily."""

    def __init__(self, rng: random.Random):
        self.rng = rng
        self.pools: dict[str, list[Asset]] = {}
        self.used: dict[str, int] = {}
        self.good: dict[str, list[tuple[Asset, Path]]] = {}

    def _pool(self, s: Subject) -> list[Asset]:
        if s.key not in self.pools:
            self.pools[s.key] = footage.pool(s)
            log.info("%s: %d candidates (%d video)", s.common, len(self.pools[s.key]),
                     sum(a.kind == "video" for a in self.pools[s.key]))
        return self.pools[s.key]

    def hero(self, s: Subject, need: float) -> tuple[Asset, Path] | None:
        """The opening image: the most eye-catching of the top candidates, never a small or letterboxed one."""
        best, best_score = None, -1.0
        tried = 0
        for a in self._pool(s):
            if tried >= 8:
                break
            if min(a.w, a.h) < 1000 or (a.kind == "video" and a.duration and a.duration < need):
                continue
            p = footage.fetch(a)
            if not p:
                continue
            tried += 1
            if a.kind == "photo":
                img = cv2.imread(str(p))
            else:
                d, w, h = footage.probe_video(p)
                if min(w, h) < 1000 or max(W / w, H / h) > 2.3:
                    continue
                img = _grab(p, min(1.0, d / 3))
            if img is None or (a.kind == "photo" and not photo_ok(img)) or has_people(img):
                continue
            sc = appeal(img) + (0.6 if a.kind == "video" else 0) + (0.4 if a.h > a.w else 0)
            if sc > best_score:
                best, best_score = (a, p), sc
        if best:
            self.used[best[0].id] = 1
            self.good.setdefault(s.key, []).append(best)
        return best

    def take(self, s: Subject, need: float) -> tuple[Asset, Path] | None:
        return self._take(s, need, strict=True) or self._take(s, need, strict=False)

    def _take(self, s: Subject, need: float, strict: bool) -> tuple[Asset, Path] | None:
        pool = self._pool(s)
        for a in pool:
            if a.id in self.used:
                continue
            if strict and (max(W / max(1, a.w), H / max(1, a.h)) > (2.2 if a.kind == "photo" else 2.3)):
                continue                   # too small / too wide to fill the frame: only if nothing better is left
            if a.kind == "video" and a.duration and a.duration < need * 0.6:
                continue
            p = footage.fetch(a)
            self.used[a.id] = 1
            if not p:
                continue
            if a.kind == "photo":
                img = cv2.imread(str(p))
                if not photo_ok(img) or has_people(img):
                    continue
            else:
                d, w, h = footage.probe_video(p)
                if d < max(2.0, need * 0.6) or min(w, h) < 360 or not clip_has_room(p, min(need, d) * 0.6):
                    continue
                if strict and max(W / w, H / h) > 2.3:
                    self.used.pop(a.id, None)      # real size was smaller than listed: leave it for the relaxed pass
                    continue
                frame = _grab(p, min(d / 2, 2.0))
                if frame is not None and has_people(frame):
                    continue
                a.duration, a.w, a.h = d, w, h
            self.good.setdefault(s.key, []).append((a, p))
            return a, p
        # out of fresh material: reuse the least-used good one (a different move/crop keeps it from feeling repeated)
        good = self.good.get(s.key, [])
        if good:
            a, p = min(good, key=lambda ap: self.used.get(ap[0].id, 0))
            self.used[a.id] = self.used.get(a.id, 0) + 1
            return a, p
        return None


def assign(shots: list[Shot], subject: Subject, picker: Picker, rng: random.Random) -> None:
    moves = ["in", "out", "pan", "in", "rise", "out", "pan"]
    last_move = ""
    for i, sh in enumerate(shots):
        got = picker.hero(sh.subject, sh.dur) if sh.first else None
        got = got or picker.take(sh.subject, sh.dur)
        if got is None and sh.subject.key != subject.key:
            got = picker.take(subject, sh.dur)
        if got is None:
            raise RuntimeError(f"no usable footage for {sh.subject.common!r}")
        sh.asset, sh.path = got
        mv = rng.choice([m for m in moves if m != last_move])
        sh.move = "in" if sh.first else mv
        last_move = sh.move
        if sh.asset.kind == "video":
            d = sh.asset.duration
            sh.speed = 1.0 if d >= sh.dur + 0.4 else max(0.6, (d - 0.3) / sh.dur)
            sh.clip_start = clean_window(sh.path, sh.dur * sh.speed, rng)


def _grab(path: Path, t: float) -> np.ndarray | None:
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(path), "-frames:v", "1", "-f", "image2pipe",
                        "-vcodec", "png", "-"], capture_output=True)
    if not r.stdout:
        return None
    return cv2.imdecode(np.frombuffer(r.stdout, np.uint8), cv2.IMREAD_COLOR)


_SCAN: dict[str, list[tuple[float, bool, float]]] = {}


def scan_clip(path: Path) -> list[tuple[float, bool, float]]:
    """(time, usable, interest) at 4 fps. Unusable: title cards / slides, black frames, and the frames around cuts."""
    key = str(path)
    if key in _SCAN:
        return _SCAN[key]
    w, h = 96, 160
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-an", "-vf", f"fps=4,scale={w}:{h}", "-pix_fmt", "gray",
           "-f", "rawvideo", "-"]
    raw = subprocess.run(cmd, capture_output=True).stdout
    n = len(raw) // (w * h)
    frames = np.frombuffer(raw[: n * w * h], np.uint8).reshape(n, h, w).astype(np.float32) if n else np.zeros((0, h, w))
    out = []
    prev = None
    for i, g in enumerate(frames):
        dark, bright = (g < 22).mean(), (g > 235).mean()
        mid = ((g >= 22) & (g <= 235)).mean()
        slide = dark > 0.55 or bright > 0.55 or mid < 0.35 or g.std() < 10
        cut = prev is not None and np.abs(g - prev).mean() > 28
        interest = float(cv2.Laplacian(g, cv2.CV_32F).var() ** 0.5) + (float(np.abs(g - prev).mean()) if prev is not None else 0)
        out.append([i / 4, not slide, interest])
        if cut:
            for j in range(max(0, i - 1), i + 1):
                out[j][1] = False
        prev = g
    res = [tuple(x) for x in out]
    _SCAN[key] = res
    return res


def clean_window(path: Path, span: float, rng: random.Random) -> float:
    """Start time of a window of `span` seconds with no title cards or cuts, preferring the liveliest one."""
    sc = scan_clip(path)
    if not sc:
        return 0.0
    k = max(1, int(math.ceil(span * 4)) + 1)
    best, best_v = 0.0, -1.0
    for i in range(0, max(1, len(sc) - k + 1)):
        win = sc[i:i + k]
        if len(win) < k or not all(u for _, u, _ in win):
            continue
        v = sum(x for _, _, x in win) / k + rng.random() * 2
        if v > best_v:
            best, best_v = win[0][0], v
    if best_v < 0:                       # no clean window: the longest usable run
        run_start, best_len, cur = 0.0, 0, 0
        for i, (t, u, _) in enumerate(sc):
            cur = cur + 1 if u else 0
            if cur > best_len:
                best_len, run_start = cur, sc[i - cur + 1][0]
        return run_start
    return best


def clip_has_room(path: Path, span: float) -> bool:
    sc = scan_clip(path)
    k = max(1, int(math.ceil(span * 4)) + 1)
    run = 0
    for _, u, _ in sc:
        run = run + 1 if u else 0
        if run >= k:
            return True
    return False


# ---- frame sources ------------------------------------------------------------------------------------------
def _ease(t: float) -> float:
    return 0.5 - 0.5 * math.cos(math.pi * min(1.0, max(0.0, t)))


def _punch(t_in: float, strength: float) -> float:
    """Zoom multiplier right after a cut: lands slightly tight and settles over ~0.3 s."""
    if t_in >= 0.32:
        return 1.0
    return 1.0 + strength * (1 - t_in / 0.32) ** 3


def _cover_matrix(sw: int, sh: int, cx: float, cy: float, s: float) -> np.ndarray:
    """Affine: source pixel -> output pixel, scale s, source point (cx, cy) at the output centre (clamped)."""
    vw, vh = W / s, H / s
    cx = min(max(cx, vw / 2), sw - vw / 2) if sw > vw else sw / 2
    cy = min(max(cy, vh / 2), sh - vh / 2) if sh > vh else sh / 2
    return np.float32([[s, 0, W / 2 - s * cx], [0, s, H / 2 - s * cy]])


def _blur_bg(img: np.ndarray) -> np.ndarray:
    h, w = img.shape[:2]
    s = max(W / w, H / h)
    small = cv2.resize(img, (max(2, int(w * s / 8)), max(2, int(h * s / 8))), interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (0, 0), 6)
    big = cv2.resize(small, (int(w * s), int(h * s)), interpolation=cv2.INTER_LINEAR)
    y0, x0 = (big.shape[0] - H) // 2, (big.shape[1] - W) // 2
    return (big[y0:y0 + H, x0:x0 + W] * 0.55).astype(np.uint8)


def photo_frames(sh: Shot, n: int):
    img = cv2.imread(str(sh.path))
    img = saturate(cv2.LUT(img, grade_lut()), 1.1)
    hh, ww = img.shape[:2]
    cover = max(W / ww, H / hh)
    fx, fy = focus_point(img)
    if cover > 2.4:                          # tiny/very wide photo: show it whole over a blurred copy
        bg = _blur_bg(img)
        s0 = W / ww * 0.98
        for i in range(n):
            t = i / max(1, n - 1)
            s = s0 * (1 + 0.06 * _ease(t)) * _punch(i / FPS, 0.05)
            M = np.float32([[s, 0, W / 2 - s * ww / 2], [0, s, H * 0.46 - s * hh / 2]])
            frame = bg.copy()
            cv2.warpAffine(img, M, (W, H), dst=frame, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_TRANSPARENT)
            yield frame
        return
    if cover > 1.25:
        img = cv2.addWeighted(img, 1.35, cv2.GaussianBlur(img, (0, 0), 2.2), -0.35, 0)   # mild unsharp for upscales
    rng = random.Random(sh.seed)
    z = 0.13 if sh.dur < 3.5 else 0.16
    px, py = fx * ww, fy * hh
    if sh.move == "in":
        sa, sb, ca, cb = cover * 1.02, cover * (1 + z), (ww / 2, hh / 2), (px, py)
    elif sh.move == "out":
        sa, sb, ca, cb = cover * (1 + z), cover * 1.02, (px, py), (ww / 2 * 0.5 + px * 0.5, hh / 2 * 0.5 + py * 0.5)
    elif sh.move == "rise":
        s = cover * (1 + z * 0.8)
        sa = sb = s
        dy = (H / s) * 0.18
        ca, cb = (px, py + dy), (px, py - dy)
    else:                                    # pan across the wider axis, towards the subject
        s = cover * (1 + z * 0.9)
        sa = sb = s
        dx = (W / s) * 0.22 * (1 if rng.random() < 0.5 else -1)
        ca, cb = (px - dx, py), (px + dx, py)
    for i in range(n):
        t = _ease(i / max(1, n - 1)) * 0.85 + (i / max(1, n - 1)) * 0.15
        s = (sa + (sb - sa) * t) * _punch(i / FPS, 0.07 if not sh.first else 0.12)
        cx = ca[0] + (cb[0] - ca[0]) * t
        cy = ca[1] + (cb[1] - ca[1]) * t
        M = _cover_matrix(ww, hh, cx, cy, s)
        yield cv2.warpAffine(img, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)


def _decode(path: Path, start: float, dur: float, speed: float, size: tuple[int, int] | None):
    vf = []
    if speed != 1.0:
        vf.append(f"setpts={1 / speed:.4f}*PTS")
    vf.append(f"fps={FPS}")
    if size:
        vf.append(f"scale={size[0]}:{size[1]}:flags=area")
    cmd = ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-i", str(path), "-t", f"{dur * speed + 0.5:.3f}",
           "-an", "-vf", ",".join(vf), "-pix_fmt", "bgr24", "-f", "rawvideo", "-"]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)


def video_frames(sh: Shot, n: int):
    a = sh.asset
    _, vw, vh = footage.probe_video(sh.path)
    # native-res decode is heavy for 4K; cap the long side at 2160 px
    k = min(1.0, 2160 / max(vw, vh))
    vw, vh = int(vw * k) // 2 * 2, int(vh * k) // 2 * 2
    cover = max(W / vw, H / vh)
    fit = cover > 2.3
    # pass 1 (small): where is the subject in each frame?
    sw = 160
    shh = max(2, int(vh * sw / vw) // 2 * 2)
    pr = _decode(sh.path, sh.clip_start, sh.dur, sh.speed, (sw, shh))
    pts = []
    while len(pts) < n:
        buf = pr.stdout.read(sw * shh * 3)
        if len(buf) < sw * shh * 3:
            break
        if len(pts) % 6 == 0:
            pts.append(focus_point(np.frombuffer(buf, np.uint8).reshape(shh, sw, 3)))
        else:
            pts.append(pts[-1])
    pr.kill()
    if not pts:
        pts = [(0.5, 0.5)]
    xs = np.array([p[0] for p in pts])
    xs = np.convolve(np.pad(xs, 15, mode="edge"), np.ones(31) / 31, mode="valid")[: len(pts)]
    # keep the crop steady: a slow drift at most
    xs = xs.mean() + (xs - xs.mean()) * 0.5
    proc = _decode(sh.path, sh.clip_start, sh.dur, sh.speed, (vw, vh))
    lut = grade_lut()
    last = None
    for i in range(n):
        buf = proc.stdout.read(vw * vh * 3)
        if len(buf) < vw * vh * 3:
            if last is None:
                raise RuntimeError(f"could not decode {a.id}")
            yield last                      # hold the last frame if the clip ran short
            continue
        fr = cv2.LUT(np.frombuffer(buf, np.uint8).reshape(vh, vw, 3), lut)
        t = i / max(1, n - 1)
        if fit:
            bg = _blur_bg(fr)
            s = W / vw * (1 + 0.04 * t) * _punch(i / FPS, 0.04)
            M = np.float32([[s, 0, W / 2 - s * vw / 2], [0, s, H * 0.46 - s * vh / 2]])
            cv2.warpAffine(fr, M, (W, H), dst=bg, flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_TRANSPARENT)
            last = bg
        else:
            s = cover * (1.0 + 0.05 * t) * _punch(i / FPS, 0.06 if not sh.first else 0.1)
            fx = xs[min(i, len(xs) - 1)]
            M = _cover_matrix(vw, vh, fx * vw, vh / 2, s)
            last = cv2.warpAffine(fr, M, (W, H), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REFLECT)
        yield last
    proc.kill()


# ---- the whole video ----------------------------------------------------------------------------------------
@dataclass
class Edit:
    shots: list[Shot]
    chunks: list[Chunk]
    total: float
    credits: list[Asset] = field(default_factory=list)


def build_edit(sc: Script, timed: list[dict], speech_end: float, seed: int) -> Edit:
    rng = random.Random(seed)
    words = make_words(sc, timed)
    chunks = chunk_words(words)
    total = speech_end + 0.45
    chunks[-1].end = total
    shots = plan_shots(chunks, words, sc, total, rng)
    picker = Picker(rng)
    assign(shots, sc.subject, picker, rng)
    seen, credits = set(), []
    for s in shots:
        if s.asset.id not in seen:
            seen.add(s.asset.id)
            credits.append(s.asset)
    return Edit(shots, chunks, total, credits)


def encode(edit: Edit, audio: Path, out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{W}x{H}", "-r", str(FPS),
           "-i", "-", "-i", str(audio), "-map", "0:v", "-map", "1:a", "-c:v", "libx264", "-preset", "medium",
           "-crf", "18", "-pix_fmt", "yuv420p", "-profile:v", "high", "-c:a", "aac", "-b:a", "192k", "-ar", "48000",
           "-shortest", "-movflags", "+faststart", str(out)]
    enc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    vig = vignette()
    wm = text_image(config.WATERMARK, "Montserrat-Bold.ttf", 40, 120) if config.WATERMARK else None
    cap_cache: dict[int, np.ndarray] = {}
    total_frames = int(round(edit.total * FPS))
    ci = 0
    fidx = 0
    try:
        for sh in edit.shots:
            f0, f1 = int(round(sh.start * FPS)), int(round(sh.end * FPS))
            f1 = min(f1, total_frames) if sh is edit.shots[-1] else f1
            if sh is edit.shots[-1]:
                f1 = total_frames
            n = f1 - f0
            if n <= 0:
                continue
            gen = photo_frames(sh, n) if sh.asset.kind == "photo" else video_frames(sh, n)
            credit = text_image("© " + sh.asset.credit(), "Montserrat-Bold.ttf", 24, 165)
            for frame in gen:
                t = fidx / FPS
                frame = (frame.astype(np.float32) * vig).astype(np.uint8)
                while ci + 1 < len(edit.chunks) and edit.chunks[ci + 1].start <= t:
                    ci += 1
                ch = edit.chunks[ci]
                if ch.start <= t < ch.end + 0.001:
                    if ci not in cap_cache:
                        cap_cache.clear()
                        cap_cache[ci] = render_caption(ch.words)
                    age = t - ch.start
                    if age < 0.05:
                        sc_, op = 0.80 + 4.4 * age, min(1.0, age / 0.05 + 0.25)
                    elif age < 0.13:
                        sc_, op = 1.02 + 0.06 * (0.13 - age) / 0.08, 1.0
                    else:
                        sc_, op = 1.0, 1.0
                    blend(frame, cap_cache[ci], W / 2, H * CAPTION_Y, sc_, op)
                if wm is not None:
                    blend(frame, wm, W / 2, H * 0.075)
                blend(frame, credit, W / 2, H * 0.962)
                enc.stdin.write(frame.tobytes())
                fidx += 1
        enc.stdin.close()
        if enc.wait() != 0:
            raise RuntimeError("ffmpeg encode failed")
    finally:
        if enc.poll() is None:
            enc.kill()
