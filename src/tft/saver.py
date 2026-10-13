"""
KP4PRA TNC - TFT screen saver: the station callsign crawls around the screen
like the centipede of the Atari arcade game (no mushrooms).

  - One letter per round body segment, red head, green body, wiggling legs.
  - Moves one 30 px grid cell every STEP_S, drawn smoothly in between.
  - Random path: mostly straight, sometimes a 90 degree turn (never straight
    back, avoids running into its own body when it can).
  - Leaving one edge it comes back in on the opposite edge (wrap-around).

Pure Pillow: rendered to an image, so it is testable without hardware.
"""

import math
import random

from PIL import Image, ImageDraw

from tft import render

CELL = 30
GRID = render.W // CELL            # 8 x 8 cells on the 240 x 240 screen
STEP_S = 0.25                      # one cell per step = 120 px/s
RADIUS = 13
P_STRAIGHT = 0.7                   # else turn left / right (0.15 each)

HEAD = render.TX_RED
BODY = render.KP4PRA_GREEN
RIM = render.YELLOW
LEGS = render.RX_GREEN
LETTER = render.WHITE

DIRS = ((1, 0), (0, 1), (-1, 0), (0, -1))


def word_for(call: str) -> str:
    """'KP4PRA-10' -> 'KP4PRA' (base callsign, no SSID)."""
    base = (call or "").split("-")[0].strip().upper()
    return base[:GRID - 1] or "KP4PRA"


class Centipede:
    def __init__(self, word="KP4PRA", rng=None, now=0.0):
        self.word = word_for(word)
        self.rng = rng or random.Random()
        self.dir = self.rng.choice(DIRS)
        hx, hy = self.rng.randrange(GRID), self.rng.randrange(GRID)
        dx, dy = self.dir
        self.cells = [((hx - i * dx) % GRID, (hy - i * dy) % GRID)
                      for i in range(len(self.word))]
        self.prev = list(self.cells)
        self.t_step = now

    # ---- movement ----
    def _next(self, d):
        hx, hy = self.cells[0]
        return ((hx + d[0]) % GRID, (hy + d[1]) % GRID)

    def step(self):
        dx, dy = self.dir
        options = [(self.dir, P_STRAIGHT), ((dy, dx), (1 - P_STRAIGHT) / 2),
                   ((-dy, -dx), (1 - P_STRAIGHT) / 2)]
        body = set(self.cells[1:-1])          # the tail cell moves away
        free = [o for o in options if self._next(o[0]) not in body] or options
        r = self.rng.random() * sum(w for _, w in free)
        for d, w in free:
            r -= w
            if r <= 0:
                break
        self.prev = list(self.cells)
        self.dir = d
        self.cells = [self._next(d)] + self.cells[:-1]

    def update(self, now):
        if now - self.t_step > 4 * STEP_S:     # fell behind (e.g. SPI busy)
            self.t_step = now - STEP_S
        while now - self.t_step >= STEP_S:
            self.step()
            self.t_step += STEP_S

    # ---- drawing ----
    @staticmethod
    def _delta(a, b):
        """b - a on the wrapping grid: -1, 0 or 1."""
        d = b - a
        if d > 1:
            d -= GRID
        elif d < -1:
            d += GRID
        return d

    def positions(self, now):
        """Pixel centers (x, y, dx, dy) of every segment, head first."""
        f = min(max((now - self.t_step) / STEP_S, 0.0), 1.0)
        out = []
        for (px, py), (cx, cy) in zip(self.prev, self.cells):
            dx, dy = self._delta(px, cx), self._delta(py, cy)
            x = ((px + dx * f) * CELL + CELL / 2) % render.W
            y = ((py + dy * f) * CELL + CELL / 2) % render.H
            out.append((x, y, dx, dy))
        return out

    def render(self, now):
        img = Image.new("RGB", (render.W, render.H), render.BLACK)
        d = ImageDraw.Draw(img)
        f = render.font(20, bold=True)
        segs = self.positions(now)
        # tail first so the head is drawn on top
        for i in reversed(range(len(segs))):
            x, y, dx, dy = segs[i]
            if dx == 0 and dy == 0:
                dx, dy = self.dir
            for ox in (-render.W, 0, render.W):          # wrap-around copies
                for oy in (-render.H, 0, render.H):
                    cx, cy = x + ox, y + oy
                    if -CELL < cx < render.W + CELL and -CELL < cy < render.H + CELL:
                        self._segment(d, f, cx, cy, dx, dy, i, now)
        return img

    def _segment(self, d, f, x, y, dx, dy, i, now):
        px, py = -dy, dx                                  # perpendicular
        wig = math.sin(now * 14 + i * 1.3) * 5
        for s in (1, -1):                                 # legs
            x0, y0 = x + s * px * (RADIUS - 2), y + s * py * (RADIUS - 2)
            x1 = x + s * px * (RADIUS + 5) + dx * wig
            y1 = y + s * py * (RADIUS + 5) + dy * wig
            d.line([x0, y0, x1, y1], fill=LEGS, width=2)
        if i == 0:                                        # antennae
            for s in (1, -1):
                d.line([x + dx * RADIUS + s * px * 4, y + dy * RADIUS + s * py * 4,
                        x + dx * (RADIUS + 7) + s * px * 9,
                        y + dy * (RADIUS + 7) + s * py * 9], fill=RIM, width=2)
        d.ellipse([x - RADIUS, y - RADIUS, x + RADIUS, y + RADIUS],
                  fill=HEAD if i == 0 else BODY, outline=RIM, width=2)
        d.text((x, y + 1), self.word[i], font=f, fill=LETTER, anchor="mm")
