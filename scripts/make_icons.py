"""Regenerate the PNG app icons (needed so browsers offer 'Install app').  python scripts/make_icons.py"""
from pathlib import Path

from PIL import Image, ImageDraw

OUT = Path(__file__).resolve().parent.parent / "static"


def draw(size: int) -> Image.Image:
    s = size / 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=int(16 * s), fill="#3730A3")
    w = max(2, int(5 * s))
    d.line([(10 * s, 42 * s), (54 * s, 42 * s)], fill="white", width=w)
    d.arc([14 * s, 18 * s, 50 * s, 66 * s], 180, 360, fill="#FBBF24", width=w)
    for x, top in ((22, 30), (32, 22), (42, 30)):
        d.line([(x * s, 42 * s), (x * s, top * s)], fill="white", width=max(2, int(4 * s)))
    return img


for px in (192, 512):
    draw(px).save(OUT / f"icon-{px}.png")
    print("wrote", OUT / f"icon-{px}.png")
