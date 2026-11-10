"""Make every logo / icon file of the program from assets/logo_source.jpg.

    python tools/make_icons.py

Writes to assets/:
    logo.png         512 px full logo (README)
    logo_small.png   150 px full logo (Help → About)
    icon.png         256 px emblem without the text band (window icon, Linux / macOS app)
    icon_small.png    44 px emblem (window header)
    icon_16/24/32    "EHD" letters drawn pixel by pixel (E and D blood red, H black):
                     the emblem is unreadable that small
    icon.ico         Windows icon: "EHD" at 16/24/32 px, the emblem from 48 to 256 px
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

ASSETS = Path(__file__).resolve().parent.parent / "assets"
SOURCE = ASSETS / "logo_source.jpg"
EMBLEM_BOX = (120, 150, 904, 752)   # part of the 1024 px source above the text band
BLOOD_RED = (138, 3, 3)   # E and D
BLACK = (17, 17, 17)      # H
WHITE = (255, 255, 255)
# "EHD" on a 16 x 16 grid: (colour, col0, row0, col1, row1), cells included
EHD_CELLS = (
    (BLOOD_RED, 0, 3, 1, 12), (BLOOD_RED, 0, 3, 3, 4), (BLOOD_RED, 0, 7, 2, 8), (BLOOD_RED, 0, 11, 3, 12),  # E
    (BLACK, 5, 3, 6, 12), (BLACK, 8, 3, 9, 12), (BLACK, 5, 7, 9, 8),                                         # H
    (BLOOD_RED, 11, 3, 12, 12), (BLOOD_RED, 11, 3, 14, 4), (BLOOD_RED, 11, 11, 14, 12),
    (BLOOD_RED, 14, 5, 15, 10),                                                                              # D
)


def emblem(source: Image.Image) -> Image.Image:
    """The emblem on a white square, 256 px."""
    part = source.crop(EMBLEM_BOX)
    side = max(part.size) + 40
    square = Image.new("RGB", (side, side), WHITE)
    square.paste(part, ((side - part.width) // 2, (side - part.height) // 2))
    return square.resize((256, 256), Image.Resampling.LANCZOS)


def ehd(size: int) -> Image.Image:
    """'EHD' on the 16-cell grid (24 px is drawn at 48 px and reduced)."""
    unit = size // 16 if size % 16 == 0 else 3
    canvas = Image.new("RGB", (16 * unit, 16 * unit), WHITE)
    draw = ImageDraw.Draw(canvas)
    for colour, c0, r0, c1, r1 in EHD_CELLS:
        draw.rectangle((c0 * unit, r0 * unit, (c1 + 1) * unit - 1, (r1 + 1) * unit - 1), fill=colour)
    return canvas if canvas.width == size else canvas.resize((size, size), Image.Resampling.LANCZOS)


def main() -> None:
    """Write all the files."""
    source = Image.open(SOURCE).convert("RGB")
    source.resize((512, 512), Image.Resampling.LANCZOS).save(ASSETS / "logo.png", optimize=True)
    source.resize((150, 150), Image.Resampling.LANCZOS).save(ASSETS / "logo_small.png", optimize=True)
    icon = emblem(source)
    icon.save(ASSETS / "icon.png", optimize=True)
    icon.resize((44, 44), Image.Resampling.LANCZOS).save(ASSETS / "icon_small.png", optimize=True)
    small = {size: ehd(size) for size in (16, 24, 32)}
    for size, image in small.items():
        image.save(ASSETS / f"icon_{size}.png", optimize=True)
    large = [icon.resize((size, size), Image.Resampling.LANCZOS) for size in (48, 64, 128)]
    icon.save(
        ASSETS / "icon.ico",
        sizes=[(size, size) for size in (16, 24, 32, 48, 64, 128, 256)],
        append_images=list(small.values()) + large,
    )
    print(f"Icons written to {ASSETS}")


if __name__ == "__main__":
    main()
