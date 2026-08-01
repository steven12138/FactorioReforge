"""A minimal PNG encoder, so rendering a map needs no image library.

Pillow would be a heavy dependency for what this does: write RGB pixels with no
filtering, no palette and no interlacing. That is a few dozen lines against
``zlib`` and ``struct``, both in the standard library, and it keeps
FactorioReforge installable with nothing but PyYAML.
"""

from __future__ import annotations

import struct
import zlib

Colour = tuple[int, int, int]


class Canvas:
    """A mutable RGB image. Origin is top-left, as PNG expects."""

    def __init__(self, width: int, height: int, background: Colour = (0, 0, 0)):
        if width <= 0 or height <= 0:
            raise ValueError("a canvas needs a positive width and height")
        self.width = width
        self.height = height
        self._pixels = bytearray(bytes(background) * (width * height))

    def set(self, x: int, y: int, colour: Colour) -> None:
        if not (0 <= x < self.width and 0 <= y < self.height):
            return
        offset = (y * self.width + x) * 3
        self._pixels[offset:offset + 3] = bytes(colour)

    def get(self, x: int, y: int) -> Colour:
        offset = (y * self.width + x) * 3
        return tuple(self._pixels[offset:offset + 3])  # type: ignore[return-value]

    def fill_rect(self, x: int, y: int, width: int, height: int, colour: Colour) -> None:
        row = bytes(colour) * width
        for line in range(y, y + height):
            if not 0 <= line < self.height:
                continue
            start = max(x, 0)
            end = min(x + width, self.width)
            if end <= start:
                continue
            offset = (line * self.width + start) * 3
            self._pixels[offset:offset + (end - start) * 3] = row[: (end - start) * 3]

    def dot(self, x: int, y: int, radius: int, colour: Colour) -> None:
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                if dx * dx + dy * dy <= radius * radius:
                    self.set(x + dx, y + dy, colour)

    def cross(self, x: int, y: int, size: int, colour: Colour) -> None:
        for offset in range(-size, size + 1):
            self.set(x + offset, y, colour)
            self.set(x, y + offset, colour)

    def to_png(self, *, compress_level: int = 6) -> bytes:
        """Serialise to PNG bytes."""
        raw = bytearray()
        stride = self.width * 3
        for row in range(self.height):
            # Filter type 0 (None) for every scanline. Better filters would
            # shrink the file, but a map thumbnail is small either way.
            raw.append(0)
            raw += self._pixels[row * stride:(row + 1) * stride]

        return b"".join(
            [
                b"\x89PNG\r\n\x1a\n",
                _chunk(b"IHDR", struct.pack(">IIBBBBB", self.width, self.height, 8, 2, 0, 0, 0)),
                _chunk(b"IDAT", zlib.compress(bytes(raw), compress_level)),
                _chunk(b"IEND", b""),
            ]
        )


def _chunk(tag: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )
