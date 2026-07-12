from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

from .paths import resource_path


WARD_ASSET_DIR = resource_path("assets", "wards")


def load_map_image(path: Path = WARD_ASSET_DIR / "minimap_dota2_1024.jpg") -> Image.Image:
    if path.exists():
        return Image.open(path).convert("RGBA")
    size = 1024
    image = Image.new("RGBA", (size, size), "#203040")
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, size, size), fill="#243447")
    draw.polygon((0, 0, size, 0, 0, size), fill="#294a36")
    draw.polygon((size, 0, size, size, 0, size), fill="#4a2d33")
    for step in range(0, size + 1, 128):
        draw.line((step, 0, step, size), fill="#ffffff18", width=1)
        draw.line((0, step, size, step), fill="#ffffff18", width=1)
    draw.line((0, size, size, 0), fill="#d8c36a55", width=8)
    draw.ellipse((448, 448, 576, 576), outline="#ffffff88", width=5)
    return image


def load_ward_icon(path: Path, color: str) -> Image.Image:
    if path.exists():
        return Image.open(path).convert("RGBA")
    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((9, 9, 55, 55), fill=color, outline="#ffffff", width=4)
    draw.ellipse((24, 24, 40, 40), fill="#111827")
    return image
