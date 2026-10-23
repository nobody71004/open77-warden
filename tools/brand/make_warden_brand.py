"""Make Open77 Warden's icon and emblem (drawn, no source image).

    python tools/brand/make_warden_brand.py          (a dev tool: needs Pillow and numpy)

The mark: a watching eye in a hexagon, with a radar's two arcs -- the hologram cyan of the live map
and the WARDEN yellow.  Written to:
    host-warden/app.ico                    the exe, its windows, the taskbar, shortcuts, the setup
    host-warden/emblem.png                 the mark on its own: the start screen and the installer
    fivem2open77/warden/web/img/icon-32.png, icon-192.png    the pages' favicons
The icon is the mark on a navy rounded tile; at 16 to 24 px it keeps only the hexagon and the eye's
yellow iris, so it still reads.
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from make_brand import ICO_BMP, ICO_PNG, RADIUS, write_ico  # noqa: E402

HOST = os.path.join(ROOT, "host-warden")
WEB_IMG = os.path.join(ROOT, "fivem2open77", "warden", "web", "img")

CYAN = (71, 212, 255)
YELLOW = (255, 210, 63)
NAVY_TOP, NAVY_BOTTOM = (16, 28, 48), (5, 9, 17)
S = 1024                                   # drawing size (downsampled after)


def hexagon(c, r, rot=-90):
    return [(c[0] + r * math.cos(math.radians(rot + 60 * i)), c[1] + r * math.sin(math.radians(rot + 60 * i))) for i in range(6)]


def glow(layer: Image.Image, radius: float, strength: float) -> Image.Image:
    """The layer with a soft halo of its own colour under it."""
    halo = layer.filter(ImageFilter.GaussianBlur(radius))
    a = np.asarray(halo).astype(np.float32)
    a[..., 3] = np.clip(a[..., 3] * strength, 0, 255)
    out = Image.fromarray(a.astype(np.uint8), "RGBA")
    out.alpha_composite(layer)
    return out


def mark(size: int, simple=False) -> Image.Image:
    """The mark on a transparent square of `size` px (drawn at S and scaled)."""
    im = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    lines = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(lines)
    c = (S / 2, S / 2)
    w = 46 if not simple else 92
    # the hexagon
    hx = hexagon(c, S * 0.43)
    d.line(hx + hx[:2], fill=CYAN + (255,), width=w, joint="curve")      # (past the first corner: a clean join there)
    if not simple:
        # the radar's two arcs inside the frame
        r = S * 0.33
        box = [c[0] - r, c[1] - r, c[0] + r, c[1] + r]
        d.arc(box, 200, 290, fill=YELLOW + (235,), width=22)
        d.arc(box, 20, 110, fill=YELLOW + (235,), width=22)
        # the eye: an almond of two arcs (each lid a circle through both corners and its middle)
        ew, eh = S * 0.30, S * 0.16
        k = (ew * ew + eh * eh) / (2 * eh)
        off = k - eh
        a = math.degrees(math.atan2(off, ew))
        up = c[1] + off                                       # the upper lid's circle is below the eye
        d.arc([c[0] - k, up - k, c[0] + k, up + k], 180 + a, 360 - a, fill=CYAN + (255,), width=30)
        lo = c[1] - off                                       # the lower lid's, above it
        d.arc([c[0] - k, lo - k, c[0] + k, lo + k], a, 180 - a, fill=CYAN + (255,), width=30)
    im.alpha_composite(glow(lines, S * 0.02, 1.6))
    # the iris and the pupil
    top = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    t = ImageDraw.Draw(top)
    ir = S * (0.105 if not simple else 0.16)
    t.ellipse([c[0] - ir, c[1] - ir, c[0] + ir, c[1] + ir], fill=YELLOW + (255,))
    pr = ir * 0.42
    t.ellipse([c[0] - pr, c[1] - pr, c[0] + pr, c[1] + pr], fill=(6, 10, 18, 255))
    if not simple:
        hr = pr * 0.38
        t.ellipse([c[0] - hr + pr * 0.25, c[1] - hr - pr * 0.25, c[0] + hr + pr * 0.25, c[1] + hr - pr * 0.25], fill=CYAN + (255,))
    im.alpha_composite(glow(top, S * 0.025, 1.3))
    return im.resize((size, size), Image.LANCZOS)


def tile(size: int) -> Image.Image:
    """The icon: the mark on a navy rounded tile."""
    big = 512
    bg = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    grad = np.zeros((big, big, 4), np.uint8)
    for y in range(big):
        f = y / (big - 1)
        grad[y, :, :3] = [int(NAVY_TOP[i] * (1 - f) + NAVY_BOTTOM[i] * f) for i in range(3)]
    grad[..., 3] = 255
    g = Image.fromarray(grad, "RGBA")
    m = Image.new("L", (big, big), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, big - 1, big - 1], radius=int(big * RADIUS), fill=255)
    bg.paste(g, (0, 0), m)
    simple = size <= 24
    inner = int(big * (0.94 if simple else 0.86))
    mk = mark(inner, simple=simple)
    bg.alpha_composite(mk, ((big - inner) // 2, (big - inner) // 2))
    out = bg.resize((size, size), Image.LANCZOS)
    if size <= 32:
        out = out.filter(ImageFilter.UnsharpMask(radius=0.6, percent=80, threshold=1))
    return out


def main():
    os.makedirs(HOST, exist_ok=True)
    os.makedirs(WEB_IMG, exist_ok=True)
    images = {s: tile(s) for s in ICO_BMP + ICO_PNG}
    write_ico(os.path.join(HOST, "app.ico"), images)
    mark(560).save(os.path.join(HOST, "emblem.png"), optimize=True)
    tile(32).save(os.path.join(WEB_IMG, "icon-32.png"), optimize=True)
    tile(192).save(os.path.join(WEB_IMG, "icon-192.png"), optimize=True)
    print("host-warden/app.ico, host-warden/emblem.png, warden/web/img/icon-32.png, icon-192.png")


if __name__ == "__main__":
    main()
