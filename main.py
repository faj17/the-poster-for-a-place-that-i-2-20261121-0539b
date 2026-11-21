#!/usr/bin/env python3
"""NIMBARA - a travel poster for a place that isn't there.

Draws a travel poster in pure Python (standard library only) and writes it
as a PNG to /output/entry.png. No network, no randomness, no time dependence:
the same bytes come out on every run.

Scene: NIMBARA, the floating isles. At sunset, grass-topped islands drift
above a sea of cloud. On the largest isle stands a slender white lighthouse,
its lamp lit and its beam sweeping toward the sun, while a waterfall pours
off the island's edge and dissolves into the clouds below. The invented
place's name is set into the picture in large capitals at the foot.
"""

import math
import os
import struct
import zlib

W = 1024
H = 1024


# ---------------------------------------------------------------------------
# PNG encoding (zlib + struct, no third-party code)
# ---------------------------------------------------------------------------

def _chunk(kind, data):
    out = struct.pack(">I", len(data)) + kind + data
    out += struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    return out


def write_png(path, width, height, rgb):
    """rgb is a bytearray of width*height*3 bytes, row-major, filter 0."""
    raw = bytearray()
    stride = width * 3
    for y in range(height):
        raw.append(0)
        raw += rgb[y * stride:(y + 1) * stride]
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    png = b"\x89PNG\r\n\x1a\n"
    png += _chunk(b"IHDR", ihdr)
    png += _chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += _chunk(b"IEND", b"")
    with open(path, "wb") as f:
        f.write(png)


# ---------------------------------------------------------------------------
# Framebuffer + drawing helpers
# ---------------------------------------------------------------------------

def clamp(v, lo=0, hi=255):
    return lo if v < lo else hi if v > hi else v


def lerp(a, b, t):
    return a + (b - a) * t


def mix(c0, c1, t):
    return (int(lerp(c0[0], c1[0], t)),
            int(lerp(c0[1], c1[1], t)),
            int(lerp(c0[2], c1[2], t)))


def det(i):
    """Deterministic pseudo-random 0..1 from an integer (no RNG)."""
    x = math.sin(i * 127.1 + 311.7) * 43758.5453
    return x - math.floor(x)


class Canvas:
    def __init__(self, w, h):
        self.w = w
        self.h = h
        self.buf = bytearray(w * h * 3)

    def set(self, x, y, color):
        if 0 <= x < self.w and 0 <= y < self.h:
            i = (y * self.w + x) * 3
            self.buf[i] = color[0]
            self.buf[i + 1] = color[1]
            self.buf[i + 2] = color[2]

    def blend(self, x, y, color, alpha):
        if alpha <= 0.0 or not (0 <= x < self.w and 0 <= y < self.h):
            return
        i = (y * self.w + x) * 3
        b = self.buf
        if alpha >= 1.0:
            b[i] = color[0]
            b[i + 1] = color[1]
            b[i + 2] = color[2]
            return
        inv = 1.0 - alpha
        b[i] = int(color[0] * alpha + b[i] * inv)
        b[i + 1] = int(color[1] * alpha + b[i + 1] * inv)
        b[i + 2] = int(color[2] * alpha + b[i + 2] * inv)

    def hline(self, x0, x1, y, color, alpha=1.0):
        if not (0 <= y < self.h):
            return
        x0 = max(0, int(x0))
        x1 = min(self.w - 1, int(x1))
        if x1 < x0:
            return
        i = (y * self.w + x0) * 3
        n = x1 - x0 + 1
        if alpha >= 1.0:
            self.buf[i:i + n * 3] = bytes(color) * n
        else:
            for x in range(x0, x1 + 1):
                self.blend(x, y, color, alpha)

    def fill_rect(self, x0, y0, x1, y1, color, alpha=1.0):
        for y in range(max(0, int(y0)), min(self.h, int(y1) + 1)):
            self.hline(x0, x1, y, color, alpha)

    def vgrad_rect(self, x0, y0, x1, y1, top, bottom):
        y0i = max(0, int(y0))
        y1i = min(self.h - 1, int(y1))
        span = max(1, y1 - y0)
        for y in range(y0i, y1i + 1):
            t = (y - y0) / span
            self.hline(x0, x1, y, mix(top, bottom, t))

    def vgrad_rect_3(self, x0, y0, x1, y1, c0, c1, c2):
        """Vertical gradient through three stops (c0 top, c1 middle, c2 bottom)."""
        y0i = max(0, int(y0))
        y1i = min(self.h - 1, int(y1))
        span = max(1, y1 - y0)
        for y in range(y0i, y1i + 1):
            t = (y - y0) / span
            if t < 0.5:
                c = mix(c0, c1, t * 2.0)
            else:
                c = mix(c1, c2, (t - 0.5) * 2.0)
            self.hline(x0, x1, y, c)

    def ellipse(self, cx, cy, rx, ry, color, alpha=1.0):
        if rx <= 0 or ry <= 0:
            return
        for y in range(max(0, int(cy - ry)), min(self.h, int(cy + ry) + 1)):
            dy = (y - cy) / ry
            dx = math.sqrt(max(0.0, 1.0 - dy * dy)) * rx
            self.hline(cx - dx, cx + dx, y, color, alpha)

    def soft_ellipse(self, cx, cy, rx, ry, color, peak_alpha, layers=6):
        for k in range(layers, 0, -1):
            t = k / layers
            self.ellipse(cx, cy, rx * t, ry * t, color,
                         peak_alpha * (1.0 - t * 0.75) / layers * 2.2)

    def disc(self, cx, cy, r, color, alpha=1.0):
        self.ellipse(cx, cy, r, r, color, alpha)

    def thick_line(self, x0, y0, x1, y1, width, color, alpha=1.0):
        dx, dy = x1 - x0, y1 - y0
        dist = math.hypot(dx, dy)
        steps = max(1, int(dist))
        r = width / 2.0
        for s in range(steps + 1):
            t = s / steps
            self.disc(x0 + dx * t, y0 + dy * t, r, color, alpha)

    def polygon(self, pts, color, alpha=1.0):
        if len(pts) < 3:
            return
        ys = [p[1] for p in pts]
        y0 = max(0, int(math.ceil(min(ys))))
        y1 = min(self.h - 1, int(math.floor(max(ys))))
        n = len(pts)
        for y in range(y0, y1 + 1):
            xs = []
            for i in range(n):
                x0, y_a = pts[i]
                x1, y_b = pts[(i + 1) % n]
                if (y_a <= y < y_b) or (y_b <= y < y_a):
                    t = (y - y_a) / (y_b - y_a)
                    xs.append(x0 + t * (x1 - x0))
            if len(xs) >= 2:
                xs.sort()
                self.hline(xs[0], xs[-1], y, color, alpha)

    def quad(self, x0, y0, x1, y1, x2, y2, x3, y3, color, alpha=1.0):
        self.polygon([(x0, y0), (x1, y1), (x2, y2), (x3, y3)], color, alpha)


# ---------------------------------------------------------------------------
# Tiny 5x7 bitmap font (capitals, digits, space, dash, dot)
# ---------------------------------------------------------------------------

FONT = {
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
    "C": ("01110", "10001", "10000", "10000", "10000", "10001", "01110"),
    "D": ("11110", "10001", "10001", "10001", "10001", "10001", "11110"),
    "E": ("11111", "10000", "10000", "11110", "10000", "00000", "11111"),
    "F": ("11111", "10000", "10000", "11110", "10000", "10000", "10000"),
    "G": ("01110", "10001", "10000", "10111", "10001", "10001", "01111"),
    "H": ("10001", "10001", "10001", "11111", "10001", "10001", "10001"),
    "I": ("01110", "00100", "00100", "00100", "00100", "00100", "01110"),
    "J": ("00111", "00010", "00010", "00010", "00010", "10010", "01100"),
    "K": ("10001", "10010", "10100", "11000", "10100", "10010", "10001"),
    "L": ("10000", "10000", "10000", "10000", "10000", "10000", "11111"),
    "M": ("10001", "11011", "10101", "10101", "10001", "10001", "10001"),
    "N": ("10001", "11001", "10101", "10011", "10001", "10001", "10001"),
    "O": ("01110", "10001", "10001", "10001", "10001", "10001", "01110"),
    "P": ("11110", "10001", "10001", "11110", "10000", "10000", "10000"),
    "Q": ("01110", "10001", "10001", "10001", "10101", "10010", "01101"),
    "R": ("11110", "10001", "10001", "11110", "10100", "10010", "10001"),
    "S": ("01111", "10000", "10000", "01110", "00001", "00001", "11110"),
    "T": ("11111", "00100", "00100", "00100", "00100", "00100", "00100"),
    "U": ("10001", "10001", "10001", "10001", "10001", "10001", "01110"),
    "V": ("10001", "10001", "10001", "10001", "10001", "01010", "00100"),
    "W": ("10001", "10001", "10001", "10101", "10101", "11011", "10001"),
    "X": ("10001", "10001", "01010", "00100", "01010", "10001", "10001"),
    "Y": ("10001", "10001", "01010", "00100", "00100", "00100", "00100"),
    "Z": ("11111", "00001", "00010", "00100", "01000", "10000", "11111"),
    "0": ("01110", "10001", "10011", "10101", "11001", "10001", "01110"),
    "1": ("00100", "01100", "00100", "00100", "00100", "00100", "01110"),
    "2": ("01110", "10001", "00001", "00110", "01000", "10000", "11111"),
    "3": ("11111", "00010", "00100", "00010", "00001", "10001", "01110"),
    "4": ("00010", "00110", "01010", "10010", "11111", "00010", "00010"),
    "5": ("11111", "10000", "11110", "00001", "00001", "10001", "01110"),
    "6": ("00110", "01000", "10000", "11110", "10001", "10001", "01110"),
    "7": ("11111", "00001", "00010", "00100", "01000", "01000", "01000"),
    "8": ("01110", "10001", "10001", "01110", "10001", "10001", "01110"),
    "9": ("01110", "10001", "10001", "01111", "00001", "00010", "01100"),
    " ": ("00000", "00000", "00000", "00000", "00000", "00000", "00000"),
    "-": ("00000", "00000", "00000", "01110", "00000", "00000", "00000"),
    ".": ("00000", "00000", "00000", "00000", "00000", "01100", "01100"),
}


def text_width(text, scale):
    return len(text) * 6 * scale - scale


def draw_text(cv, x, y, text, scale, color, alpha=1.0):
    """Draw text at (x, y) = top-left of the first glyph."""
    cx = 0
    for ch in text:
        glyph = FONT.get(ch, FONT[" "])
        for gy in range(7):
            row = glyph[gy]
            for gx in range(5):
                if row[gx] == "1":
                    for sy in range(scale):
                        for sx in range(scale):
                            cv.blend(x + cx + gx * scale + sx,
                                     y + gy * scale + sy, color, alpha)
        cx += 6 * scale


# ---------------------------------------------------------------------------
# Scene
# ---------------------------------------------------------------------------

SUN_X, SUN_Y, SUN_R = 740, 540, 62
CLOUD_TOP = 618          # where the sea of cloud begins
BAND_TOP = 884           # where the poster's title band begins


def draw_sky(cv):
    cv.vgrad_rect_3(0, 0, W - 1, CLOUD_TOP - 1,
                    (43, 32, 86), (150, 74, 122), (250, 172, 104))
    # Early stars, fading out as the sky warms toward the horizon.
    for i in range(90):
        x = int(det(i * 3 + 1) * W)
        y = int(det(i * 3 + 2) * 380)
        r = 1 if det(i * 3 + 3) < 0.7 else 2
        a = 0.35 + 0.55 * det(i * 7 + 5)
        if y < 340:
            cv.disc(x, y, r, (255, 244, 224), a * (1.0 - y / 420.0))


def draw_sun(cv):
    # Wide warm glow, then the disc itself, then a hot core.
    cv.soft_ellipse(SUN_X, SUN_Y, 220, 220, (255, 190, 120), 0.30)
    cv.soft_ellipse(SUN_X, SUN_Y, 130, 130, (255, 214, 150), 0.45)
    cv.disc(SUN_X, SUN_Y, SUN_R, (255, 232, 178))
    cv.disc(SUN_X, SUN_Y, SUN_R - 14, (255, 242, 205))
    cv.disc(SUN_X - 12, SUN_Y - 14, 26, (255, 250, 228), 0.9)


def draw_birds(cv):
    # Small distant birds: simple "v" strokes.
    for (bx, by, s) in ((180, 250, 9), (230, 225, 7), (300, 300, 8),
                        (620, 210, 8), (680, 250, 6), (850, 300, 7),
                        (420, 180, 6)):
        cv.thick_line(bx - s, by, bx, by + s * 0.55, 2, (52, 36, 70), 0.8)
        cv.thick_line(bx, by + s * 0.55, bx + s, by, 2, (52, 36, 70), 0.8)


def draw_cloud_sea(cv):
    # Base: warm cream darkening toward the title band.
    cv.vgrad_rect(0, CLOUD_TOP, W - 1, BAND_TOP - 1,
                  (246, 208, 182), (206, 158, 178))
    # Long horizontal cloud bands, darker underneath for depth.
    for (cx, cy, rx, ry, col, a) in (
            (180, 660, 260, 34, (252, 226, 200), 0.9),
            (520, 700, 320, 40, (244, 204, 186), 0.9),
            (860, 665, 260, 32, (252, 224, 196), 0.9),
            (300, 780, 300, 44, (232, 186, 186), 0.9),
            (700, 800, 330, 46, (226, 178, 182), 0.9),
            (120, 850, 240, 36, (220, 172, 184), 0.9),
            (880, 855, 240, 34, (216, 166, 182), 0.9)):
        cv.ellipse(cx, cy, rx, ry, col, a)
    # Puffy highlights on the nearer clouds.
    for (cx, cy, rx, ry) in ((180, 648, 180, 22), (520, 688, 230, 26),
                             (860, 654, 180, 20), (300, 768, 210, 26),
                             (700, 788, 240, 28)):
        cv.soft_ellipse(cx, cy, rx, ry, (255, 238, 214), 0.35, layers=12)


def draw_island(cv, x0, top_y, x1, tip_y, grass_top, grass_bot,
                rock_top, rock_bot):
    """One floating island: grassy plateau, rocky underside tapering to a
    point, with a soft shadow in the cloud sea beneath it."""
    tip_x = (x0 + x1) / 2.0
    # Shadow on the clouds below (many thin layers: no visible rings).
    cv.soft_ellipse(tip_x, tip_y + 46, (x1 - x0) * 0.52, 26,
                    (120, 80, 100), 0.22, layers=14)
    # Rocky underside: tapering polygon, shaded top to bottom.
    span = max(1.0, tip_y - top_y)
    y = int(top_y)
    while y < int(tip_y):
        t = (y - top_y) / span
        # Width shrinks linearly from full width to a point.
        half = ((x1 - x0) / 2.0) * (1.0 - t) + 2
        col = mix(rock_top, rock_bot, t)
        cv.hline(tip_x - half, tip_x + half, y, col)
        y += 1
    # A few short darker cracks running down from the plateau, kept well
    # inside the island's tapering edge.
    for k in range(3):
        fx = x0 + (x1 - x0) * (0.28 + 0.22 * k)
        fy = top_y + 26 + 14 * k
        cv.thick_line(fx, fy, fx + 14, fy + 30, 3, (58, 40, 52), 0.30)
    # Grassy plateau: slightly domed top.
    for yy in range(int(top_y) - 14, int(top_y) + 10):
        t = (yy - (top_y - 14)) / 24.0
        dome = math.sin(min(1.0, max(0.0, t)) * math.pi) * 10
        col = mix(grass_top, grass_bot, t)
        cv.hline(x0 + 4, x1 - 4, yy, col)
        if yy < top_y:
            cv.hline(x0 + 4 + dome * 0.4, x1 - 4 - dome * 0.4, yy, col)
    # Bright rim of grass catching the sunset on the plateau edge.
    cv.hline(x0 + 6, x1 - 6, int(top_y) - 13, (196, 220, 130), 0.85)
    cv.hline(x0 + 6, x1 - 6, int(top_y) - 12, (150, 196, 110), 0.7)


def draw_tree(cv, x, y, s):
    """A small round-topped tree on an island plateau."""
    cv.thick_line(x, y, x, y - 10 * s / 6.0, 3, (52, 40, 44))
    cv.disc(x, y - 12 * s / 6.0, 7 * s / 6.0, (34, 96, 74))
    cv.disc(x - 5 * s / 6.0, y - 8 * s / 6.0, 5 * s / 6.0, (44, 112, 84))
    cv.disc(x + 5 * s / 6.0, y - 8 * s / 6.0, 5 * s / 6.0, (28, 84, 66))
    cv.disc(x - 2, y - 14 * s / 6.0, 3, (120, 190, 120), 0.8)


def draw_cottage(cv, x, y):
    """A tiny cottage with one warm lit window."""
    cv.fill_rect(x, y - 22, x + 34, y, (232, 214, 188))
    cv.polygon([(x - 4, y - 22), (x + 17, y - 36), (x + 38, y - 22)],
               (150, 74, 78))
    cv.fill_rect(x + 12, y - 12, x + 20, y, (96, 58, 52))
    cv.fill_rect(x + 24, y - 18, x + 31, y - 9, (255, 214, 130))
    cv.fill_rect(x + 24, y - 18, x + 31, y - 9, (255, 236, 180), 0.5)


def draw_lighthouse(cv, x, base_y):
    """The landmark: a slender white lighthouse, lamp lit, beam sweeping
    toward the setting sun."""
    top_y = base_y - 150
    # Beam first (behind the tower): a warm translucent cone to the right,
    # layered in many thin steps so no single edge shows.
    for k in range(10):
        spread = 66 - k * 5.5
        alpha = 0.045 + k * 0.011
        cv.polygon([(x + 14, top_y + 12 - 8), (1010, top_y - 40 - spread),
                    (1010, top_y + 60 + spread), (x + 14, top_y + 12 + 8)],
                   (255, 214, 130), alpha)
    # Tower: tapered, cream white with a warm sunset side.
    cv.polygon([(x - 16, base_y), (x - 10, top_y + 22),
                (x + 10, top_y + 22), (x + 16, base_y)],
               (246, 238, 226))
    cv.polygon([(x + 2, base_y), (x + 5, top_y + 22),
                (x + 10, top_y + 22), (x + 16, base_y)],
               (232, 204, 170), 0.9)
    # Gallery + lamp room.
    cv.fill_rect(x - 18, top_y + 10, x + 18, top_y + 22, (52, 44, 62))
    cv.fill_rect(x - 12, top_y - 8, x + 12, top_y + 10, (255, 226, 150))
    cv.fill_rect(x - 12, top_y - 8, x + 12, top_y + 10, (255, 244, 200), 0.6)
    cv.polygon([(x - 16, top_y - 8), (x, top_y - 24), (x + 16, top_y - 8)],
               (52, 44, 62))
    # Lamp glow.
    cv.soft_ellipse(x, top_y + 2, 46, 30, (255, 214, 130), 0.5)
    cv.disc(x, top_y + 2, 7, (255, 246, 214))
    # Three small warm windows down the tower.
    for wy in (base_y - 40, base_y - 72, base_y - 104):
        cv.fill_rect(x - 4, wy, x + 4, wy + 10, (255, 206, 120))
    # A thin gold ring at the tower's foot.
    cv.fill_rect(x - 18, base_y - 4, x + 18, base_y, (196, 160, 90))


def draw_waterfall(cv, x, y0, y1):
    """A waterfall pouring off an island's edge into the cloud sea."""
    for k, (dx, w, a) in enumerate(((-6, 7, 0.85), (2, 9, 0.9), (10, 5, 0.7))):
        cv.fill_rect(x + dx, y0, x + dx + w, y1, (214, 232, 244), a)
        cv.fill_rect(x + dx + 1, y0, x + dx + 3, y1, (240, 248, 252), 0.8)
    # Mist where it meets the clouds.
    cv.soft_ellipse(x + 4, y1 + 8, 44, 18, (250, 240, 228), 0.5)
    cv.soft_ellipse(x + 4, y1 + 22, 30, 12, (250, 240, 228), 0.35)


def draw_title_band(cv):
    # Deep indigo band, gold rule, and the place name in large capitals.
    cv.fill_rect(0, BAND_TOP, W - 1, H - 1, (26, 22, 54))
    cv.vgrad_rect(0, BAND_TOP, W - 1, BAND_TOP + 26, (34, 28, 66),
                  (26, 22, 54))
    cv.fill_rect(0, BAND_TOP - 4, W - 1, BAND_TOP - 1, (212, 170, 90))
    cv.fill_rect(0, BAND_TOP - 5, W - 1, BAND_TOP - 5, (240, 204, 130), 0.9)
    name = "NIMBARA"
    sc = 13
    w = text_width(name, sc)
    x = (W - w) // 2
    y = BAND_TOP + 34
    # Gold shadow offset, then cream letters.
    draw_text(cv, x + 3, y + 4, name, sc, (150, 110, 52), alpha=0.9)
    draw_text(cv, x, y, name, sc, (250, 238, 210))
    # Small gold diamonds flanking the name.
    for dx in (x - 52, x + w + 30):
        cy = y + 7 * sc // 2
        cv.polygon([(dx, cy - 9), (dx + 9, cy), (dx, cy + 9), (dx - 9, cy)],
                   (212, 170, 90))


def main():
    cv = Canvas(W, H)
    draw_sky(cv)
    draw_sun(cv)
    draw_birds(cv)
    draw_cloud_sea(cv)

    # Far tiny isle, a silhouette against the sunset.
    draw_island(cv, 836, 396, 956, 500, (96, 120, 96), (66, 84, 76),
                (96, 62, 66), (52, 38, 46))

    # Left isle with two trees.
    draw_island(cv, 88, 428, 282, 596, (64, 148, 112), (34, 96, 82),
                (126, 88, 82), (70, 50, 60))
    draw_tree(cv, 150, 424, 6)
    draw_tree(cv, 216, 422, 5)

    # Main isle: cottages, trees, and the lighthouse.
    draw_island(cv, 300, 470, 736, 700, (72, 156, 118), (38, 102, 86),
                (130, 92, 86), (72, 52, 62))
    draw_cottage(cv, 350, 468)
    draw_cottage(cv, 610, 470)
    draw_tree(cv, 330, 466, 6)
    draw_tree(cv, 660, 466, 6)
    draw_tree(cv, 700, 468, 5)
    draw_lighthouse(cv, 512, 472)
    draw_waterfall(cv, 312, 486, 640)

    # Near clouds drifting in front of the isles' undersides.
    cv.soft_ellipse(300, 660, 150, 26, (250, 230, 208), 0.4, layers=12)
    cv.soft_ellipse(720, 690, 170, 28, (246, 224, 202), 0.4, layers=12)

    draw_title_band(cv)
    os.makedirs("/output", exist_ok=True)
    write_png("/output/entry.png", W, H, cv.buf)
    print("wrote /output/entry.png")


if __name__ == "__main__":
    main()
