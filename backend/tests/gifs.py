"""Tiny synthetic GIFs, generated at runtime so no binary is ever committed."""

import io

from PIL import Image


def make_gif(width: int = 16, height: int = 16, frames: int = 3) -> bytes:
    images = [Image.new("RGB", (width, height), (60 * i % 256, 90, 160)) for i in range(frames)]
    buffer = io.BytesIO()
    images[0].save(buffer, format="GIF", save_all=True, append_images=images[1:], duration=100)
    return buffer.getvalue()


def make_still(fmt: str, width: int = 16, height: int = 16) -> bytes:
    """A tiny synthetic JPEG or PNG ("JPEG" / "PNG")."""
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (200, 80, 40)).save(buffer, format=fmt)
    return buffer.getvalue()
