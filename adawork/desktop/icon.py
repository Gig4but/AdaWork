"""The app icon, drawn with Pillow (tray, window and browser tab)."""

from PIL import Image, ImageDraw

TILE = (35, 42, 51)
SPARK = (255, 179, 0)
# The "A with a spark" mark on a 64 x 64 grid: a bold A, and a spark for the local AI.
LETTER = (((16, 52), (30, 16), (44, 52)), 7), (((22.5, 40), (37.5, 40)), 6)
SPARK_POINTS = (
    (50, 7),
    (52.6, 13.4),
    (59, 16),
    (52.6, 18.6),
    (50, 25),
    (47.4, 18.6),
    (41, 16),
    (47.4, 13.4),
)


def make_icon_image(size: int = 64) -> Image.Image:
    scale = 4  # drawn large, then scaled down for smooth edges
    unit = size * scale / 64
    image = Image.new("RGBA", (size * scale, size * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((2 * unit, 2 * unit, 62 * unit, 62 * unit), radius=14 * unit, fill=TILE)
    for points, width in LETTER:
        xy = [(x * unit, y * unit) for x, y in points]
        draw.line(xy, fill="white", width=round(width * unit), joint="curve")
        r = width * unit / 2
        for x, y in (xy[0], xy[-1]):  # round ends
            draw.ellipse((x - r, y - r, x + r, y + r), fill="white")
    draw.polygon([(x * unit, y * unit) for x, y in SPARK_POINTS], fill=SPARK)
    return image.resize((size, size), Image.Resampling.LANCZOS)
