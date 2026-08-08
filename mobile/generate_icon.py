"""Generate the P.E.T.E.R. orb PNG icons (192 & 512) for the mobile PWA.

Uses only the Python standard library (zlib+struct) so no extra deps are
needed. Creates mobile/icon-192.png and mobile/icon-512.png with a dark
orb + red mask/eyes on black, matching the desktop app.
"""

from __future__ import annotations

import os
import struct
import zlib
from pathlib import Path

_HERE = Path(os.path.dirname(os.path.abspath(__file__)))


def _px(x: float, y: float, size: int) -> tuple:
    """Return (r,g,b,a) for a pixel, drawing the P.E.T.E.R orb."""
    cx = cy = size / 2.0
    r_orb = size * 0.46

    # Pointer to center
    dx = x - cx
    dy = y - cy
    dist = (dx * dx + dy * dy) ** 0.5
    theta = (__import__('math').atan2(dy, dx) + __import__('math').pi) / (2 * __import__('math').pi)

    if dist > r_orb:
        return (0, 0, 0, 0)  # transparent outside the orb

    # Base orb gradient (navy center -> darker edge)
    t = dist / r_orb
    r = int(18 + 10 * (1 - t))
    g = int(32 + 14 * (1 - t))
    b = int(74 + 30 * (1 - t))

    # Highlight top-left
    hx = cx - r_orb * 0.4
    hy = cy - r_orb * 0.45
    hd = ((x - hx) ** 2 + (y - hy) ** 2) ** 0.5
    if hd < r_orb * 0.35:
        k = max(0, 1 - hd / (r_orb * 0.35))
        r = min(255, r + int(120 * k))
        g = min(255, g + int(120 * k))
        b = min(255, b + int(120 * k))

    # Red mask covering the middle band
    mask_w = r_orb * 1.15
    mask_h = r_orb * 0.92
    if abs(x - cx) < mask_w / 2 and abs(y - cy) < mask_h / 2:
        # Ellipse mask
        ex = (x - cx) / (mask_w / 2)
        ey = (y - cy) / (mask_h / 2)
        if (ex * ex + ey * ey) <= 1.0:
            r = 230; g = 36; b = 41
            # subtle gradient
            r = min(255, r + int(20 * (1 - t)))

    # White eyes
    eye_y = cy + mask_h * 0.18
    for ex_off in (-r_orb * 0.34, r_orb * 0.34):
        ed = ((x - (cx + ex_off)) ** 2 + (y - eye_y) ** 2) ** 0.5
        if ed < r_orb * 0.16:
            r = 255; g = 255; b = 255

    return (r, g, b, 255)


def make_png(size: int, path: Path) -> None:
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            r, g, b, a = _px(x + 0.5, y + 0.5, size)
            row += bytes((r, g, b, a))
        rows.append(bytes(row))

    def chunk(tag, data):
        return (struct.pack('>I', len(data)) + tag + data +
                struct.pack('>I', zlib.crc32(tag + data) & 0xffffffff))

    ihdr = struct.pack('>IIBBBBB', size, size, 8, 6, 0, 0, 0)
    raw = b''.join(rows)
    png = (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', ihdr) +
           chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))
    path.write_bytes(png)
    print(f'  wrote {path.name} ({size}x{size})')


if __name__ == '__main__':
    for s in (192, 512):
        make_png(s, _HERE / f'icon-{s}.png')
    print('Icons generated.')

