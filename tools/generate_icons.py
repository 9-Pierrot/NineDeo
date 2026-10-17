#!/usr/bin/env python3
"""Génère les icônes de l'extension (et l'icône Windows) à partir de logo.png.

Usage : python tools/generate_icons.py   (nécessite Pillow)
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "logo.png"
OUT = ROOT / "extension" / "icons"


def rounded(img: Image.Image, radius_ratio: float = 0.22) -> Image.Image:
    size = img.size[0]
    mask = Image.new("L", (size * 4, size * 4), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size * 4 - 1, size * 4 - 1), radius=int(size * 4 * radius_ratio), fill=255
    )
    mask = mask.resize((size, size), Image.LANCZOS)
    out = img.copy()
    out.putalpha(mask)
    return out


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    logo = Image.open(SRC).convert("RGBA")
    for size in (16, 19, 32, 38, 48, 64, 96, 128, 256):
        icon = rounded(logo.resize((size, size), Image.LANCZOS))
        icon.save(OUT / f"icon-{size}.png", optimize=True)
    # Icône Windows pour l'exécutable empaqueté
    ico_dir = ROOT / "installer" / "windows"
    ico_dir.mkdir(parents=True, exist_ok=True)
    rounded(logo.resize((256, 256), Image.LANCZOS)).save(
        ico_dir / "ninedeo.ico", sizes=[(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    )
    print("icônes générées dans", OUT)


if __name__ == "__main__":
    main()
