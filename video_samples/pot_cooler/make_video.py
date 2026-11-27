"""Sample "How It Works" explainer reel: the pot-in-pot (zeer) cooler.

Fully programmatic: offline TTS (espeak-ng + mbrola) for the voice, PIL for the
cartoon frames, numpy for music/SFX, ffmpeg for encoding.

Usage: python3 make_video.py <output_dir>
"""
import math
import os
import random
import subprocess
import sys
import wave
from multiprocessing import Pool

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter

OUT = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else "out")
os.makedirs(OUT, exist_ok=True)

FPS = 30
BW, BH = 720, 1280          # design units
K = 2                       # supersample factor for drawing
W, H = BW * K, BH * K
FINAL = (1080, 1920)
VOICE = "mb-us1"
GAP = 0.28                  # silence between sentences

FONT_BLACK = "/usr/share/fonts/opentype/inter/Inter-Black.otf"
FONT_XB = "/usr/share/fonts/opentype/inter/Inter-ExtraBold.otf"
_fc = {}


def font(path, size):
    key = (path, int(size))
    if key not in _fc:
        _fc[key] = ImageFont.truetype(path, int(size))
    return _fc[key]


# ----------------------------------------------------------------- script ---
SCRIPT = [
    "Never pay for electricity to keep your vegetables cold.",
    "In hot, dry places, fresh tomatoes can spoil in just two days.",
    "All you need is two clay pots, some sand, and water.",
    "Put the small pot inside the big one, and fill the gap with wet sand.",
    "Clay is full of tiny holes, so the water slowly creeps outward.",
    "When it reaches the outside, it evaporates.",
    "And every droplet that escapes carries a little heat away with it.",
    "In dry heat, the inside can stay more than ten degrees cooler.",
    "So food that lasted days can last for weeks.",
    "This is the pot in pot cooler.",
    "No motor. No power. Just add water once or twice a day.",
    "It's not really a fridge. It's a pot that sweats for you.",
]
# what the on-screen captions show (same words, nicer punctuation)
CAPTION_TEXT = [s.replace("pot in pot", "pot-in-pot") for s in SCRIPT]
TAIL = 2.6  # seconds of end card after the last line


def tts():
    durs = []
    for i, s in enumerate(SCRIPT):
        p = os.path.join(OUT, f"line_{i:02d}.wav")
        subprocess.run(["espeak-ng", "-v", VOICE, "-s", "158", "-p", "52",
                        "-w", p, s], check=True)
        with wave.open(p) as w:
            durs.append(w.getnframes() / w.getframerate())
    return durs


def build_timeline(durs):
    lines, t = [], 0.35
    for i, d in enumerate(durs):
        lines.append((t, t + d))
        t += d + GAP
    return lines, t + TAIL


def word_times(lines):
    """Spread each line's words across its audio by character weight."""
    words = []
    for li, ((a, b), txt) in enumerate(zip(lines, CAPTION_TEXT)):
        ws = txt.split()
        a2, b2 = a + 0.08, b - 0.12
        wt = [len(w.strip(".,'")) + 2.5 for w in ws]
        tot = sum(wt)
        c = a2
        for w, x in zip(ws, wt):
            d = (b2 - a2) * x / tot
            words.append((c, c + d, w, li, len(words) - sum(1 for x in words if x[3] != li)))
            c += d
    return words


# --------------------------------------------------------------- easing -----
def clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def ease(x):
    x = clamp(x)
    return x * x * (3 - 2 * x)


def ease_out_back(x, s=1.9):
    x = clamp(x) - 1
    return 1 + x * x * ((s + 1) * x + s)


def lerp(a, b, t):
    return a + (b - a) * t


def mix(c1, c2, t):
    return tuple(int(lerp(a, b, t)) for a, b in zip(c1, c2))


# --------------------------------------------------------------- palette ----
GRASS = (147, 201, 92)
GRASS_D = (118, 176, 70)
SOIL = (150, 96, 62)
SOIL_D = (110, 68, 44)
OUTLINE = (58, 44, 36)
CLAY = (204, 112, 64)
CLAY_D = (160, 80, 44)
CLAY_L = (226, 146, 98)
SAND = (232, 200, 132)
SAND_WET = (196, 160, 98)
WATER = (128, 200, 240)
WATER_D = (56, 128, 178)
YELLOW = (255, 210, 63)
RED = (230, 72, 60)
WHITE = (255, 255, 255)


# -------------------------------------------------------- draw helpers -----
class Pen:
    """ImageDraw wrapper that takes design-unit coordinates."""

    def __init__(self, img):
        self.img = img
        self.d = ImageDraw.Draw(img, "RGBA")

    def S(self, v):
        return v * K

    def ellipse(self, cx, cy, rx, ry, fill, outline=None, w=0):
        self.d.ellipse([(cx - rx) * K, (cy - ry) * K, (cx + rx) * K, (cy + ry) * K],
                       fill=fill, outline=outline, width=int(w * K))

    def rect(self, x0, y0, x1, y1, fill, r=0, outline=None, w=0):
        if x1 - x0 < 1 or y1 - y0 < 1:
            return
        r = min(r, (x1 - x0) / 2, (y1 - y0) / 2)
        self.d.rounded_rectangle([x0 * K, y0 * K, x1 * K, y1 * K], radius=r * K,
                                 fill=fill, outline=outline, width=int(w * K))

    def poly(self, pts, fill, outline=None, w=0):
        P = [(x * K, y * K) for x, y in pts]
        self.d.polygon(P, fill=fill)
        if outline and w:
            self.d.line(P + [P[0]], fill=outline, width=int(w * K), joint="curve")

    def line(self, pts, fill, w):
        self.d.line([(x * K, y * K) for x, y in pts], fill=fill, width=int(w * K),
                    joint="curve")

    def text(self, cx, cy, s, size, fill, path=FONT_BLACK, stroke=0, stroke_fill=None,
             anchor="mm"):
        self.d.text((cx * K, cy * K), s, font=font(path, size * K), fill=fill,
                    anchor=anchor, stroke_width=int(stroke * K), stroke_fill=stroke_fill)


_bg_cache = {}


def background(night=0.0):
    key = round(night, 2)
    if key in _bg_cache:
        return _bg_cache[key].copy()
    img = Image.new("RGB", (W, H), GRASS)
    p = Pen(img)
    rnd = random.Random(7)
    for _ in range(14):
        x, y = rnd.uniform(0, BW), rnd.uniform(0, BH * 0.55)
        r = rnd.uniform(26, 52)
        p.ellipse(x, y + 6, r, r * 0.8, (100, 150, 60))
        p.ellipse(x, y, r, r * 0.8, (104, 168, 66))
    for _ in range(60):
        x, y = rnd.uniform(0, BW), rnd.uniform(0, BH)
        c = rnd.choice([(255, 240, 200), (250, 190, 200), (255, 225, 120)])
        for k in range(3):
            p.ellipse(x + k * 5 - 5, y + (k % 2) * 4, 2.6, 2.6, c)
    if night > 0:
        ov = Image.new("RGB", (W, H), (20, 40, 60))
        img = Image.blend(img, ov, 0.55 * night)
    _bg_cache[key] = img
    return img.copy()


def droplet(p, cx, cy, s, look=0.0, blink=False, color=WATER, mood="happy", alpha=255):
    """Cute water-drop character. s ~ radius in design units."""
    c = color + (alpha,)
    o = WATER_D + (alpha,)
    # body: circle + tip
    p.poly([(cx - s * 0.80, cy - s * 0.45), (cx, cy - s * 2.0), (cx + s * 0.80, cy - s * 0.45)],
           c)
    p.ellipse(cx, cy, s, s, c, o, 1.6)
    p.line([(cx - s * 0.86, cy - s * 0.42), (cx, cy - s * 2.0), (cx + s * 0.86, cy - s * 0.42)],
           o, 1.6)
    p.ellipse(cx, cy, s * 0.92, s * 0.92, c)
    p.ellipse(cx - s * 0.45, cy - s * 0.35, s * 0.16, s * 0.24, (255, 255, 255, int(alpha * .8)))
    ex = s * 0.32
    for sx in (-1, 1):
        x = cx + sx * ex + look * s * 0.12
        y = cy + s * 0.05
        if blink:
            p.line([(x - s * 0.14, y), (x + s * 0.14, y)], (30, 30, 40, alpha), 1.6)
        else:
            p.ellipse(x, y, s * 0.15, s * 0.2, (25, 30, 45, alpha))
            p.ellipse(x + s * 0.05, y - s * 0.07, s * 0.05, s * 0.06, (255, 255, 255, alpha))
    if mood == "happy":
        p.d.arc([(cx - s * 0.2) * K, (cy + s * 0.18) * K, (cx + s * 0.2) * K, (cy + s * 0.5) * K],
                20, 160, fill=(30, 30, 40, alpha), width=int(1.4 * K))


def pill(p, cx, cy, txt, bg, t=1.0, size=24):
    sc = ease_out_back(t)
    if sc <= 0.01:
        return
    f = font(FONT_XB, size * K * sc)
    tw = p.d.textlength(txt, font=f) / K
    h = (size + 14) * sc
    p.rect(cx - tw / 2 - 14 * sc, cy - h / 2 + 3, cx + tw / 2 + 14 * sc, cy + h / 2 + 3,
           mix(bg, (0, 0, 0), 0.35), r=h / 2)
    p.rect(cx - tw / 2 - 14 * sc, cy - h / 2, cx + tw / 2 + 14 * sc, cy + h / 2, bg, r=h / 2,
           outline=WHITE, w=2)
    p.d.text((cx * K, cy * K), txt, font=f, fill=WHITE, anchor="mm")


def title(p, cx, cy, small, big, t=1.0, size=64):
    sc = ease_out_back(t, 1.4)
    if sc <= 0.01:
        return
    if small:
        p.text(cx - 120, cy - size * 0.75 * sc, small, 24 * sc, WHITE, stroke=3,
               stroke_fill=OUTLINE)
    for i, ln in enumerate(big.split("\n")):
        p.text(cx, cy + i * size * 1.05 * sc, ln, size * sc, YELLOW, stroke=6,
               stroke_fill=OUTLINE)


def thermometer(p, cx, cy, val, label=True, h=220):
    """val in deg C, scale 0..50"""
    if h < 40:
        return
    top, bot = cy - h / 2, cy + h / 2
    p.rect(cx - 20, top - 4, cx + 20, bot + 10, OUTLINE, r=20)
    p.rect(cx - 15, top, cx + 15, bot, WHITE, r=15)
    p.ellipse(cx, bot + 14, 32, 32, OUTLINE)
    p.ellipse(cx, bot + 14, 27, 27, RED)
    frac = clamp(val / 50)
    fy = bot - (h - 20) * frac
    p.rect(cx - 8, fy, cx + 8, bot + 10, RED, r=8)
    for k in range(6):
        y = bot - (h - 20) * k / 5
        p.line([(cx + 15, y), (cx + 4, y)], (120, 120, 120), 2)
    if label:
        col = RED if val > 30 else (60, 150, 230)
        p.text(cx + 82, fy, f"{val:.0f}°C", 38, col, stroke=4, stroke_fill=WHITE)


def tomato(p, cx, cy, r, wilt=0.0):
    col = mix((232, 60, 48), (150, 92, 50), wilt)
    ry = r * (1 - 0.28 * wilt)
    p.ellipse(cx, cy + (r - ry), r, ry, col, OUTLINE, 2)
    p.ellipse(cx - r * 0.35, cy - ry * 0.3 + (r - ry), r * 0.18, r * 0.12,
              (255, 255, 255, int(160 * (1 - wilt))))
    g = mix((70, 160, 60), (110, 110, 50), wilt)
    top = cy - ry + (r - ry)
    for a in range(5):
        ang = a * 2 * math.pi / 5 - math.pi / 2
        droop = wilt * 0.6
        p.line([(cx, top), (cx + math.cos(ang) * r * 0.45, top + math.sin(ang) * r * 0.2 + droop * r * 0.4)],
               g, 5)
    p.line([(cx, top), (cx + 3, top - r * 0.3)], g, 5)
    if wilt > 0.5:  # spots
        a = int(255 * (wilt - 0.5) * 2)
        p.ellipse(cx + r * 0.3, cy + r * 0.2, r * 0.13, r * 0.1, (90, 60, 30, a))
        p.ellipse(cx - r * 0.2, cy + r * 0.45, r * 0.1, r * 0.08, (90, 60, 30, a))


def sun(p, cx, cy, r, t):
    for i in range(12):
        a = i * math.pi / 6 + t * 0.6
        r1, r2 = r * 1.25, r * (1.6 + 0.1 * math.sin(t * 4 + i))
        p.line([(cx + math.cos(a) * r1, cy + math.sin(a) * r1),
                (cx + math.cos(a) * r2, cy + math.sin(a) * r2)], (255, 200, 60), 9)
    p.ellipse(cx, cy, r, r, (255, 214, 70), (240, 160, 40), 4)


def ground_band(p, y, night=0.0):
    """Cross-section soil band from y to bottom."""
    p.rect(-10, y, BW + 10, BH + 10, mix(SOIL, (40, 40, 50), 0.5 * night))
    p.rect(-10, y - 8, BW + 10, y + 10, mix(GRASS_D, (30, 50, 60), 0.5 * night))
    rnd = random.Random(3)
    for _ in range(26):
        x, yy = rnd.uniform(0, BW), rnd.uniform(y + 30, BH)
        p.ellipse(x, yy, rnd.uniform(5, 11), rnd.uniform(4, 8),
                  mix(SOIL_D, (30, 30, 40), 0.5 * night))
    for _ in range(8):  # roots / cracks
        x = rnd.uniform(0, BW)
        pts, yy = [(x, y + 10)], y + 10
        for k in range(4):
            x += rnd.uniform(-22, 22)
            yy += rnd.uniform(25, 45)
            pts.append((x, yy))
        p.line(pts, mix(SOIL_D, (30, 30, 40), 0.5 * night), 3)


def pot_outside(p, cx, by, w, h, face=False, sweat=0.0, t=0.0, scale=1.0):
    """Closed clay pot seen from outside, bottom-center (cx, by)."""
    w, h = w * scale, h * scale
    body = []
    for i in range(41):
        a = i / 40
        y = by - h * a
        bulge = math.sin(math.pi * (0.15 + 0.85 * a) ** 0.9) * 0.5 + 0.5
        bw = w * (0.32 + 0.22 * bulge) if a < 0.88 else w * 0.38
        body.append((cx - bw, y))
    body = body + [(2 * cx - x, y) for x, y in reversed(body)]
    p.poly(body, CLAY, OUTLINE, 3)
    p.ellipse(cx, by - h * 0.55, w * 0.16, h * 0.18, (255, 255, 255, 30))
    p.rect(cx - w * 0.44, by - h - 14 * scale, cx + w * 0.44, by - h + 6 * scale, CLAY_D,
           r=8 * scale, outline=OUTLINE, w=3)
    # wet patches
    if sweat > 0:
        rnd = random.Random(11)
        for _ in range(9):
            x = cx + rnd.uniform(-w * 0.35, w * 0.35)
            y = by - rnd.uniform(h * 0.15, h * 0.8)
            p.ellipse(x, y, 16 * scale, 22 * scale, (120, 60, 34, int(110 * sweat)))
    if face:
        ey = by - h * 0.55
        for sx in (-1, 1):
            p.ellipse(cx + sx * w * 0.14, ey, 9 * scale, 12 * scale, (30, 25, 25))
            p.ellipse(cx + sx * w * 0.14 + 3, ey - 4, 3 * scale, 3 * scale, WHITE)
        p.d.arc([(cx - 22 * scale) * K, (ey + 10 * scale) * K, (cx + 22 * scale) * K,
                 (ey + 34 * scale) * K], 15, 165, fill=(30, 25, 25), width=int(4 * K))
        if sweat > 0:  # sweat drops sliding down
            for i in range(3):
                ph = (t * 0.6 + i / 3) % 1
                x = cx + (-0.3 + 0.3 * i) * w
                y = by - h * 0.85 + ph * h * 0.7
                droplet(p, x, y, 9 * scale, blink=True, mood="", alpha=int(255 * sweat * (1 - ph)))


# ---------------------------------------------------- cross-section scene ---
XS = dict(cx=360, by=900, ow=470, oh=430, wall=24, iw=300, ih=330)


def outer_shape(cx, by, ow, oh):
    pts = []
    for i in range(31):
        a = i / 30
        y = by - oh * a
        bw = ow / 2 * (0.62 + 0.38 * math.sin(math.pi * (0.25 + 0.75 * a)))
        pts.append((cx - bw, y))
    return pts


def draw_xsection(p, t, gl, inner_drop=1.0, sand_fill=1.0, wet=1.0, seep=0.0,
                  evap=0.0, heat=0.0, temp_in=40.0, show_thermo=False, therm_t=1.0):
    cx, by, ow, oh, wl = XS["cx"], XS["by"], XS["ow"], XS["oh"], XS["wall"]
    left = outer_shape(cx, by, ow, oh)
    right = [(2 * cx - x, y) for x, y in left]
    outer = left + list(reversed(right))
    # outer pot wall (filled) then hollow
    inner_cav = [(x + wl, y - (wl if i == 0 else 0)) for i, (x, y) in enumerate(left)]
    inner_cav_r = [(2 * cx - x, y) for x, y in inner_cav]
    p.poly(outer + [], CLAY, OUTLINE, 3)
    cav = inner_cav + list(reversed(inner_cav_r))
    # sand in the gap
    sand_col = mix(SAND, SAND_WET, wet)
    p.poly(cav, (250, 236, 210))
    if sand_fill > 0:
        top = by - wl - (oh - wl - 10) * ease(sand_fill)
        clip = [(x, max(y, top)) for x, y in cav]
        p.poly(clip, sand_col)
        rnd = random.Random(5)
        for _ in range(140):
            x = rnd.uniform(cx - ow / 2, cx + ow / 2)
            y = rnd.uniform(by - oh, by)
            if y > top + 4:
                p.ellipse(x, y, 2, 2, mix(sand_col, (120, 90, 50), 0.35))
    # clay pores (tiny holes) on outer wall
    for i, (x, y) in enumerate(left[2:-3]):
        if i % 2 == 0:
            p.ellipse(x + wl * 0.5, y, 3.2, 3.2, CLAY_D)
            p.ellipse(2 * cx - x - wl * 0.5, y, 3.2, 3.2, CLAY_D)
    # inner pot (drops in from above)
    iw, ih = XS["iw"], XS["ih"]
    iy = by - wl - 6 - lerp(-520, 0, ease_out_back(inner_drop, 1.2)) * -1
    iy = lerp(by - 700, by - wl - 6, ease_out_back(inner_drop, 1.1))
    ip = [(cx - iw / 2, iy - ih), (cx - iw / 2 + 18, iy), (cx + iw / 2 - 18, iy), (cx + iw / 2, iy - ih)]
    p.poly(ip, CLAY_L, OUTLINE, 3)
    ic = [(cx - iw / 2 + 18, iy - ih), (cx - iw / 2 + 32, iy - 18), (cx + iw / 2 - 32, iy - 18),
          (cx + iw / 2 - 18, iy - ih)]
    cool = clamp((40 - temp_in) / 15)
    p.poly(ic, mix((255, 238, 220), (205, 235, 255), cool))
    # veggies inside
    for i, (dx, dy, r) in enumerate([(-60, -60, 34), (8, -52, 38), (72, -64, 30), (-28, -118, 32),
                                     (46, -120, 30)]):
        tomato(p, cx + dx, iy + dy, r, wilt=0)
    # lid line
    p.rect(cx - iw / 2 - 10, iy - ih - 16, cx + iw / 2 + 10, iy - ih - 2, (240, 240, 230),
           r=7, outline=OUTLINE, w=2)
    # water droplets in sand
    rnd = random.Random(21)
    if wet > 0.05:
        for side in (-1, 1):
            for k in range(4):
                fy = by - 70 - k * 78
                gap_x = cx + side * (iw / 2 + 30)
                wall_x = cx + side * (ow / 2 * (0.62 + 0.38 * math.sin(math.pi * (0.25 + 0.75 * (by - fy) / oh))))
                ph = (seep * 1.0 + k * 0.23 + (side > 0) * 0.11) % 1 if seep > 0 else 0
                x = lerp(gap_x, wall_x + side * 6, ease(ph * 1.25) if seep > 0 else 0)
                bob = math.sin(gl * 5 + k + side) * 2
                droplet(p, x, fy + bob, 13, look=side, blink=(int(gl * 2 + k) % 9 == 0),
                        alpha=int(255 * clamp(wet * 1.4)))
    # evaporation: droplets leave outer surface upward, carrying heat
    if evap > 0:
        for side in (-1, 1):
            for k in range(6):
                ph = (gl * 0.45 + k / 6 + (side > 0) * 0.08) % 1
                sy = by - 60 - (k % 4) * 85
                sx = cx + side * (ow / 2 * (0.62 + 0.38 * math.sin(math.pi * (0.25 + 0.75 * (by - sy) / oh))) + 10)
                x = sx + side * ph * 120 + math.sin(ph * 9 + k) * 10
                y = sy - ph * 330
                a = int(255 * evap * (1 - ph) ** 0.7)
                droplet(p, x, y, 11 * (1 - 0.4 * ph), look=side, alpha=a,
                        color=(178, 222, 246))
                if heat > 0:
                    ha = int(a * heat)
                    p.ellipse(x + side * 16, y + 10, 8, 8, (240, 80, 50, ha))
                    p.ellipse(x + side * 16, y + 10, 12, 12, (255, 140, 80, ha // 3))
    if show_thermo:
        s = ease_out_back(therm_t)
        if s > 0.02:
            thermometer(p, 120, 470, temp_in, h=200 * s)


# ------------------------------------------------------------- scenes -------
def scene_hook(p, t, gl, L, idx):
    """Lines 0-1: hot sun, paying for a fridge, tomatoes spoiling."""
    sun(p, 590, 160, 62, gl)
    thermometer(p, 110, 330, 40 + 2 * math.sin(gl * 2))
    # ground band
    ground_band(p, 990)
    # fridge with $ and plug
    fx, fy = 470, 620
    a0 = L[0][0]
    if idx == 0:
        k = clamp((gl - a0) / 0.5)
        p.rect(fx - 85, fy - 170, fx + 85, fy + 170, (235, 240, 245), r=18, outline=OUTLINE, w=4)
        p.line([(fx - 85, fy - 40), (fx + 85, fy - 40)], OUTLINE, 4)
        p.rect(fx + 52, fy - 130, fx + 64, fy - 70, (160, 170, 180), r=5)
        p.rect(fx + 52, fy - 10, fx + 64, fy + 80, (160, 170, 180), r=5)
        p.line([(fx - 85, fy + 120), (fx - 160, fy + 160), (fx - 250, fy + 150), (fx - 300, fy + 250)],
               (40, 40, 40), 7)
        pill(p, fx, fy - 215, "ELECTRICITY", RED, clamp((gl - a0 - 0.6) / 0.35))
        bill = clamp((gl - a0 - 1.2) / 0.4)
        if bill > 0:
            p.text(fx - 150, fy - 120 - 20 * ease(bill), "$96", 66 * ease_out_back(bill),
                   YELLOW, stroke=5, stroke_fill=OUTLINE)
        # big red X near end of line
        xk = clamp((gl - (L[0][1] - 0.7)) / 0.3)
        if xk > 0:
            s = 120 * ease_out_back(xk)
            p.line([(fx - s, fy - s), (fx + s, fy + s)], RED, 22)
            p.line([(fx - s, fy + s), (fx + s, fy - s)], RED, 22)
    else:
        k = clamp((gl - L[1][0]) / (L[1][1] - L[1][0]))
        # basket of tomatoes spoiling
        p.rect(170, 800, 550, 960, (196, 140, 80), r=20, outline=OUTLINE, w=4)
        for j in range(6):
            p.line([(190 + j * 66, 812), (190 + j * 66, 950)], (160, 108, 60), 4)
        for j, (dx, dy) in enumerate([(-120, -20), (-40, -36), (40, -22), (120, -34), (-80, -80),
                                      (0, -90), (80, -78)]):
            tomato(p, 360 + dx, 800 + dy, 46, wilt=ease(k * 1.15 - j * 0.03))
        day = 1 if k < 0.5 else 2
        pill(p, 360, 640, f"DAY {day}", (240, 140, 40) if day == 1 else RED,
             1.0 if k > 0.04 else k / 0.04, size=26)
        # flies
        if k > 0.55:
            for f in range(3):
                a = gl * 3 + f * 2.1
                fxp, fyp = 360 + math.cos(a) * 140, 690 + math.sin(a * 1.3) * 40
                p.ellipse(fxp, fyp, 6, 6, (30, 30, 30))
                p.ellipse(fxp - 6, fyp - 6, 6, 4, (255, 255, 255, 160))


def scene_ingredients(p, t, gl, L, idx):
    a, b = L[2]
    items = [("CLAY POT", CLAY_D, 0.10), ("SAND", (200, 150, 60), 0.47), ("WATER", WATER_D, 0.72)]
    ground_band(p, 1000)
    for i, (lab, col, at) in enumerate(items):
        k = clamp((gl - (a + (b - a) * at)) / 0.45)
        if k <= 0:
            continue
        s = ease_out_back(k)
        x = [160, 360, 560][i]
        y = 760
        if i == 0:
            pot_outside(p, x - 10, y + 120, 200, 210, scale=s)
            pot_outside(p, x + 70, y + 120, 140, 140, scale=s)
        elif i == 1:
            p.poly([(x - 95 * s, y + 120), (x, y + 120 - 150 * s), (x + 95 * s, y + 120)], SAND,
                   OUTLINE, 3)
            rnd = random.Random(2)
            for _ in range(30):
                xx = x + rnd.uniform(-60, 60) * s
                yy = y + 120 - rnd.uniform(5, 100) * s
                if abs(xx - x) < (y + 120 - yy) * 0.55 + 1 and False:
                    pass
                p.ellipse(xx, yy, 2.4, 2.4, (190, 150, 90)) if abs(xx - x) < (150 * s - (y + 120 - yy)) * 0.6 else None
        else:
            p.rect(x - 55 * s, y + 120 - 170 * s, x + 55 * s, y + 120, (200, 230, 250, 220), r=14,
                   outline=OUTLINE, w=3)
            p.rect(x - 48 * s, y + 120 - 110 * s, x + 48 * s, y + 114, WATER + (230,), r=10)
            droplet(p, x, y + 120 - 60 * s, 22 * s, look=-1, blink=int(gl * 2) % 7 == 0)
        pill(p, x, y - 120, lab, col, k, size=22)


def scene_assemble(p, t, gl, L, idx):
    ground_band(p, 1020)
    a, b = L[3]
    span = b - a
    inner = clamp((gl - a) / (span * 0.45))
    sand = clamp((gl - a - span * 0.5) / (span * 0.45))
    wet = clamp((gl - a - span * 0.75) / (span * 0.3))
    draw_xsection(p, t, gl, inner_drop=inner, sand_fill=sand, wet=wet)
    pill(p, 360, 380, "WET SAND", (200, 150, 60), clamp((gl - a - span * 0.55) / 0.3))


def scene_seep(p, t, gl, L, idx):
    ground_band(p, 1020)
    a, b = L[idx]
    k = clamp((gl - a) / (b - a))
    if idx == 4:
        draw_xsection(p, t, gl, seep=(gl - a) * 0.35)
        pill(p, 360, 390, "TINY HOLES", CLAY_D, clamp((gl - a) / 0.4))
        # magnifier on wall pores
        mk = clamp((gl - a - 0.3) / 0.5)
        if mk > 0:
            s = ease_out_back(mk)
            mx, my = 600, 620
            p.ellipse(mx, my, 70 * s, 70 * s, (255, 255, 255, 230), OUTLINE, 6)
            for i in range(5):
                for j in range(5):
                    if (i + j) % 2 == 0:
                        p.ellipse(mx - 44 * s + i * 22 * s, my - 44 * s + j * 22 * s, 6 * s, 6 * s, CLAY_D)
            p.line([(mx + 50 * s, my + 50 * s), (mx + 95 * s, my + 95 * s)], OUTLINE, 14)
    elif idx == 5:
        draw_xsection(p, t, gl, seep=(gl - a) * 0.35 + 1, evap=ease(k * 2))
        pill(p, 360, 300, "EVAPORATION", WATER_D, clamp((gl - a - 0.6) / 0.35))
    elif idx == 6:
        draw_xsection(p, t, gl, seep=(gl - a) * 0.35 + 2, evap=1, heat=ease(k * 3))
        pill(p, 360, 300, "HEAT OUT", RED, clamp((gl - a - 1.0) / 0.35))
    elif idx == 7:
        temp = lerp(40, 26, ease(clamp((gl - a - 0.6) / (b - a - 0.6))))
        draw_xsection(p, t, gl, seep=(gl - a) * 0.35 + 3, evap=1, heat=1, temp_in=temp,
                      show_thermo=True, therm_t=clamp((gl - a) / 0.4))
        big = clamp((gl - (b - 1.0)) / 0.4)
        if big > 0:
            p.text(560, 300, "-10°C+", 70 * ease_out_back(big), (90, 180, 255), stroke=6,
                   stroke_fill=OUTLINE)


def scene_weeks(p, t, gl, L, idx):
    a, b = L[8]
    k = clamp((gl - a) / (b - a))
    ground_band(p, 1020)
    # left: day 2 spoiled, right: week 3 fresh
    p.rect(60, 760, 320, 900, (196, 140, 80), r=18, outline=OUTLINE, w=4)
    for j, dx in enumerate([-70, 0, 70]):
        tomato(p, 190 + dx, 750, 40, wilt=1.0)
    pill(p, 190, 600, "WITHOUT: 2 DAYS", RED, clamp((gl - a) / 0.35), size=21)
    rk = clamp((gl - a - (b - a) * 0.45) / 0.45)
    if rk > 0:
        s = ease_out_back(rk)
        pot_outside(p, 530, 940, 230, 270, face=False, sweat=0.6, scale=s)
        for j, dx in enumerate([-50, 10, 60]):
            tomato(p, 530 + dx * s, 660 - 10 * (j % 2), 34 * s, wilt=0)
        pill(p, 530, 560, "WITH: WEEKS", (60, 160, 80), rk, size=21)


def scene_title(p, t, gl, L, idx):
    a, b = L[9]
    k = clamp((gl - a) / 0.5)
    ground_band(p, 1040)
    s = 1 + 0.03 * math.sin(gl * 2)
    pot_outside(p, 360, 1000, 330, 380, face=True, sweat=0.0, t=gl, scale=s)
    title(p, 380, 330, "the", "pot-in-pot\ncooler", k, size=78)


def scene_nomotor(p, t, gl, L, idx):
    a, b = L[10]
    ground_band(p, 1040)
    pot_outside(p, 360, 1000, 330, 380, face=True, sweat=0.5, t=gl)
    pill(p, 360, 330, "NO MOTOR", RED, clamp((gl - a) / 0.3), size=30)
    pill(p, 360, 400, "NO POWER", RED, clamp((gl - a - 0.75) / 0.3), size=30)
    wk = clamp((gl - a - (b - a) * 0.5) / 0.4)
    if wk > 0:
        # watering can pouring onto sand
        s = ease_out_back(wk)
        x, y = 560, 470
        p.rect(x - 60 * s, y - 40 * s, x + 50 * s, y + 40 * s, (90, 160, 210), r=14, outline=OUTLINE, w=4)
        p.line([(x - 55 * s, y), (x - 130 * s, y - 35 * s)], (90, 160, 210), 16)
        for i in range(5):
            ph = (gl * 1.5 + i / 5) % 1
            p.ellipse(x - 135 * s - ph * 30, y - 30 * s + ph * 140, 5, 7, WATER + (200,))
        pill(p, 360, 480, "1-2x A DAY", WATER_D, wk, size=26)


def scene_sweat(p, t, gl, L, idx):
    ground_band(p, 1040)
    pot_outside(p, 360, 1000, 330, 380, face=True, sweat=1.0, t=gl)


def night_sky(p, gl, night):
    al = int(255 * clamp(night / 0.9))
    sky = mix(GRASS, (20, 40, 60), 0.6 * night)
    p.ellipse(590, 170, 48, 48, (250, 245, 220, al))
    p.ellipse(612, 155, 44, 44, sky + (al,))
    rnd = random.Random(9)
    for _ in range(25):
        x, y = rnd.uniform(0, BW), rnd.uniform(0, 500)
        tw = 0.5 + 0.5 * math.sin(gl * 3 + x)
        p.ellipse(x, y, 2.2, 2.2, (255, 255, 230, int(al * tw)))


SCENES = [(0, scene_hook), (1, scene_hook), (2, scene_ingredients), (3, scene_assemble),
          (4, scene_seep), (5, scene_seep), (6, scene_seep), (7, scene_seep), (8, scene_weeks),
          (9, scene_title), (10, scene_nomotor), (11, scene_sweat)]


# --------------------------------------------------------------- frame ------
CTX = {}


def caption(p, gl, words):
    cur = None
    for i, (a, b, w, li, wi) in enumerate(words):
        if a <= gl < b + 0.05:
            cur = i
    if cur is None:
        return
    # show pairs of words within the same sentence
    i0 = cur - (words[cur][4] % 2)
    chunk = [w for (_, _, w, li, _) in words[i0:i0 + 2] if li == words[cur][3]]
    s = " ".join(chunk).strip(",.").lower()
    pop = ease_out_back(clamp((gl - words[i0][0]) / 0.12), 1.2)
    p.text(360, 1150, s, 50 * (0.85 + 0.15 * pop), WHITE, path=FONT_XB, stroke=6,
           stroke_fill=(40, 40, 40))


def cta(p, gl, total):
    k = clamp((gl - (total - TAIL + 0.2)) / 0.45)
    if k <= 0:
        return
    s = ease_out_back(k)
    p.rect(70, 470 - 70 * s, 650, 470 + 70 * s, (255, 255, 255, 235), r=30, outline=OUTLINE, w=4)
    p.text(360, 448, "Would this work", 38 * s, OUTLINE)
    p.text(360, 496, "where you live?", 38 * s, OUTLINE)
    ay = 548 + 12 * math.sin(gl * 6)
    p.poly([(340, ay), (380, ay), (360, ay + 30)], YELLOW, OUTLINE, 3)


def render_frame(fi):
    L, words, total = CTX["L"], CTX["words"], CTX["total"]
    gl = fi / FPS
    # which line are we in? (stay on previous line's scene during gaps)
    idx = 0
    for i, (a, b) in enumerate(L):
        if gl >= a - GAP * 0.5:
            idx = i
    night = 0.0
    if idx == 11:
        night = ease(clamp((gl - L[11][0]) / 1.2)) * 0.9
    img = background()
    p = Pen(img)
    a, b = L[idx]
    SCENES[idx][1](p, clamp((gl - a) / (b - a)), gl, L, idx)
    if night > 0.01:
        img = Image.blend(img, Image.new("RGB", (W, H), (20, 40, 60)), 0.6 * night)
        p = Pen(img)
        night_sky(p, gl, night)
    caption(p, gl, words)
    cta(p, gl, total)
    # gentle per-line camera push-in
    z = 1 + 0.035 * ease(clamp((gl - a + GAP) / (b - a + GAP * 2)))
    if z > 1.001:
        cw, ch = W / z, H / z
        x0, y0 = (W - cw) / 2, (H - ch) * 0.45
        img = img.crop((int(x0), int(y0), int(x0 + cw), int(y0 + ch)))
    img = img.resize(FINAL, Image.LANCZOS if False else Image.BILINEAR, reducing_gap=None)
    # quick white flash on scene change
    for (la, lb) in L[1:]:
        d = gl - (la - GAP * 0.5)
        if 0 <= d < 0.12:
            img = Image.blend(img, Image.new("RGB", FINAL, WHITE), 0.35 * (1 - d / 0.12))
    return img.tobytes()


def render_chunk(args):
    ci, f0, f1 = args
    path = os.path.join(OUT, f"seg_{ci:02d}.mp4")
    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{FINAL[0]}x{FINAL[1]}", "-r", str(FPS), "-i", "-",
         "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", path],
        stdin=subprocess.PIPE)
    for fi in range(f0, f1):
        proc.stdin.write(render_frame(fi))
    proc.stdin.close()
    proc.wait()
    return path


def init_worker(ctx):
    CTX.update(ctx)


# ---------------------------------------------------------------- audio -----
SR = 48000


def make_audio(L, total):
    n = int(total * SR) + SR
    mixbuf = np.zeros(n, dtype=np.float32)
    # voice
    for i, (a, b) in enumerate(L):
        with wave.open(os.path.join(OUT, f"line_{i:02d}.wav")) as w:
            sr = w.getframerate()
            x = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768
        # resample to SR
        idx = np.linspace(0, len(x) - 1, int(len(x) * SR / sr))
        x = np.interp(idx, np.arange(len(x)), x)
        x = x / (np.abs(x).max() + 1e-6) * 0.85
        s = int(a * SR)
        mixbuf[s:s + len(x)] += x[: n - s]
    # soft music bed: plucky arpeggio over pad, C - Am - F - G
    tt = np.arange(n) / SR
    chords = [[261.6, 329.6, 392.0], [220.0, 261.6, 329.6], [174.6, 220.0, 261.6], [196.0, 246.9, 293.7]]
    bar = 2.4
    music = np.zeros(n, dtype=np.float32)
    for bi in range(int(total / bar) + 1):
        ch = chords[bi % 4]
        s0 = int(bi * bar * SR)
        seg = np.arange(int(bar * SR)) / SR
        pad = sum(np.sin(2 * np.pi * f * seg) for f in ch) / 3
        env = np.minimum(1, seg / 0.4) * np.minimum(1, (bar - seg) / 0.4)
        e = s0 + len(seg)
        music[s0:min(e, n)] += (0.05 * pad * env)[: n - s0]
        for k in range(8):  # pluck notes
            f = ch[k % 3] * (2 if k % 4 == 3 else 1)
            ps = s0 + int(k * bar / 8 * SR)
            pl = np.arange(int(0.35 * SR)) / SR
            note = np.sin(2 * np.pi * f * 2 * pl) * np.exp(-pl * 9) * 0.07
            pe = min(ps + len(pl), n)
            if ps < n:
                music[ps:pe] += note[: pe - ps]
    fade = np.minimum(1, tt / 1.0) * np.minimum(1, (total + 0.5 - tt) / 1.5).clip(0)
    mixbuf += music * fade
    # pops at scene changes
    for (a, b) in L[1:]:
        s = int((a - GAP * 0.5) * SR)
        pl = np.arange(int(0.12 * SR)) / SR
        pop = np.sin(2 * np.pi * (900 - 3000 * pl) * pl) * np.exp(-pl * 35) * 0.25
        mixbuf[s:s + len(pl)] += pop[: max(0, n - s)]
    mixbuf = np.clip(mixbuf, -1, 1)
    p = os.path.join(OUT, "mix.wav")
    with wave.open(p, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes((mixbuf * 32767).astype(np.int16).tobytes())
    return p


def main():
    durs = tts()
    L, total = build_timeline(durs)
    words = word_times(L)
    ctx = dict(L=L, words=words, total=total)
    CTX.update(ctx)
    nfr = int(total * FPS)
    print(f"duration {total:.1f}s, {nfr} frames")
    if os.environ.get("PREVIEW"):
        for tsec in map(float, os.environ["PREVIEW"].split(",")):
            Image.frombytes("RGB", FINAL, render_frame(int(tsec * FPS))).save(
                os.path.join(OUT, f"preview_{tsec:05.1f}.jpg"), quality=85)
        return
    nproc = os.cpu_count() or 2
    step = math.ceil(nfr / nproc)
    jobs = [(i, i * step, min(nfr, (i + 1) * step)) for i in range(nproc)]
    with Pool(nproc, initializer=init_worker, initargs=(ctx,)) as pool:
        segs = pool.map(render_chunk, jobs)
    lst = os.path.join(OUT, "segs.txt")
    with open(lst, "w") as f:
        for s in segs:
            f.write(f"file '{s}'\n")
    audio = make_audio(L, total)
    final = os.path.join(OUT, "pot_cooler_sample.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "concat", "-safe", "0", "-i", lst,
                    "-i", audio, "-c:v", "copy", "-c:a", "aac", "-b:a", "160k", "-shortest",
                    "-movflags", "+faststart", final], check=True)
    print("wrote", final)


if __name__ == "__main__":
    main()
