"""Generate 8 small labelled placeholder GIFs ("Osoba A" ... "Osoba H") for local development.

    uv run python scripts/make_placeholder_gifs.py [output_dir]

The default output, backend/dev-gifs/, is gitignored, and `list.example.json` points at it, so
`python -m app.cli load list.example.json` works end to end. Needs Pillow (a dev dependency).
The images are synthetic; real GIFs live in the private folder and are never committed.
"""

import colorsys
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SIZE = 320
FRAMES = 12
LETTERS = "ABCDEFGH"


def make_gif(label: str, hue: float) -> list[Image.Image]:
    font = ImageFont.load_default(size=44)
    frames: list[Image.Image] = []
    for i in range(FRAMES):
        phase = i / FRAMES
        # Background colour slowly cycles; a ball bounces along the bottom so it visibly loops.
        r, g, b = colorsys.hsv_to_rgb((hue + phase * 0.15) % 1.0, 0.55, 0.95)
        image = Image.new("RGB", (SIZE, SIZE), (int(r * 255), int(g * 255), int(b * 255)))
        draw = ImageDraw.Draw(image)
        draw.text((SIZE / 2, SIZE / 2 - 20), label, font=font, fill="white", anchor="mm")
        x = 30 + (SIZE - 60) * abs(2 * phase - 1)
        draw.ellipse((x - 18, SIZE - 80, x + 18, SIZE - 44), fill="white", outline="black", width=3)
        frames.append(image)
    return frames


def main(argv: list[str]) -> int:
    out = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parent.parent / "dev-gifs"
    out.mkdir(parents=True, exist_ok=True)
    for index, letter in enumerate(LETTERS):
        frames = make_gif(f"Osoba {letter}", index / len(LETTERS))
        frames[0].save(
            out / f"osoba-{letter.lower()}.gif",
            save_all=True,
            append_images=frames[1:],
            duration=100,
            loop=0,
        )
    print(f"Wrote {len(LETTERS)} placeholder GIFs to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
