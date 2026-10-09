"""Memory - find all the matching pairs (drawn as a GPU overlay)."""

import math
import random
import time
from functools import lru_cache

from . import _overlay as ov
from . import _record as _rec

GAME_NAME = "Memory"
GAME_ICON = 'MESH_CUBE'

LEVELS = [          # name, cols, rows
    ("Easy", 4, 3),
    ("Medium", 4, 4),
    ("Hard", 6, 4),
]

FLIP_T = 0.16       # seconds for a card to turn over
MISMATCH_T = 0.8    # seconds a wrong pair stays visible

BEST = {}           # level index -> fewest moves (kept while Blender is open)

C_BACK = (0.27, 0.33, 0.52, 1.0)
C_BACK_H = (0.35, 0.42, 0.64, 1.0)
C_BACK_IN = (0.21, 0.26, 0.43, 1.0)
C_FACE = (0.92, 0.93, 0.96, 1.0)

BLUE = (0.30, 0.55, 1.00, 1.0)
ORANGE = (1.00, 0.58, 0.22, 1.0)
GREEN = (0.30, 0.78, 0.42, 1.0)
RED = (0.93, 0.30, 0.30, 1.0)
PURPLE = (0.66, 0.42, 0.95, 1.0)
GOLD = (0.95, 0.76, 0.15, 1.0)
CYAN = (0.20, 0.78, 0.85, 1.0)
PINK = (0.95, 0.42, 0.70, 1.0)

# 12 distinct (shape, colour) symbols -> up to 12 pairs
SYMBOLS = [
    ('circle', BLUE), ('ring', ORANGE), ('square', GREEN), ('triangle', RED),
    ('diamond', PURPLE), ('star', GOLD), ('cross', CYAN), ('hexagon', PINK),
    ('ring', GREEN), ('triangle', BLUE), ('star', RED), ('square', ORANGE),
]


# ----------------------------------------------------------------------------
# Symbol geometry (unit triangles, radius ~1)
# ----------------------------------------------------------------------------

@lru_cache(maxsize=None)
def _tris(shape):
    def circ(n, r=1.0, rot=0.0):
        return [(r * math.cos(rot + 2 * math.pi * i / n),
                 r * math.sin(rot + 2 * math.pi * i / n)) for i in range(n)]

    def fan(pts):                      # polygon containing the origin
        n = len(pts)
        return [((0.0, 0.0), pts[i], pts[(i + 1) % n]) for i in range(n)]

    def rect(x0, y0, x1, y1):
        a, b, c, d = (x0, y0), (x1, y0), (x1, y1), (x0, y1)
        return [(a, b, c), (a, c, d)]

    if shape == 'circle':
        t = fan(circ(24))
    elif shape == 'hexagon':
        t = fan(circ(6))
    elif shape == 'triangle':
        t = fan([(0, 1.0), (-0.95, -0.75), (0.95, -0.75)])
    elif shape == 'diamond':
        t = fan([(0, 1.1), (0.8, 0), (0, -1.1), (-0.8, 0)])
    elif shape == 'square':
        t = fan([(-0.8, -0.8), (0.8, -0.8), (0.8, 0.8), (-0.8, 0.8)])
    elif shape == 'star':
        t = fan([((1.0 if i % 2 == 0 else 0.45) * math.cos(math.pi / 2 + i * math.pi / 5),
                  (1.0 if i % 2 == 0 else 0.45) * math.sin(math.pi / 2 + i * math.pi / 5))
                 for i in range(10)])
    elif shape == 'cross':
        t = rect(-0.9, -0.3, 0.9, 0.3) + rect(-0.3, -0.9, 0.3, 0.9)
    else:                              # ring
        n, t = 24, []
        o, i_ = circ(n), circ(n, 0.55)
        for k in range(n):
            k2 = (k + 1) % n
            t += [(o[k], o[k2], i_[k2]), (o[k], i_[k2], i_[k])]
    return tuple(t)


# ----------------------------------------------------------------------------
# Game
# ----------------------------------------------------------------------------

class Memory(ov.BaseGame):
    keys = ('D',)

    def setup(self):
        self.level = 1
        self.record_mgrs = {}
        try:
            for i, lv in enumerate(LEVELS):
                self.record_mgrs[i] = _rec.RecordManager(
                    game_name="memory_" + lv[0].lower())
        except Exception:
            self.record_mgrs = {}

    def reset(self):
        _, self.cols, self.rows = LEVELS[self.level]
        n = self.cols * self.rows
        ids = random.sample(range(len(SYMBOLS)), n // 2) * 2
        random.shuffle(ids)
        self.cards = ids
        self.state = ['down'] * n          # down | up | matched
        self.flip = [0.0] * n              # 0 = back, 1 = face
        self.first = self.second = -1
        self.pending = 0.0                 # countdown for a wrong pair
        self.moves = 0
        self.found = 0
        self.t0 = None
        self.t1 = 0.0
        self.over = False
        self.hover = -1
        self.shown_t = 0
        self.cs = 80

    # ---- logic
    def elapsed(self):
        if self.t0 is None:
            return 0
        end = self.t1 if self.over else time.time()
        return int(min(5999, end - self.t0))

    def _resolve(self):
        for j in (self.first, self.second):
            if j >= 0 and self.state[j] == 'up':
                self.state[j] = 'down'
        self.first = self.second = -1
        self.pending = 0.0

    def flip_card(self, i):
        if self.pending > 0:               # don't make the player wait
            self._resolve()
        if self.state[i] != 'down':
            return
        if self.t0 is None:
            self.t0 = time.time()
        self.state[i] = 'up'
        if self.first < 0:
            self.first = i
            return
        self.second = i
        self.moves += 1
        if self.cards[self.first] == self.cards[self.second]:
            self.state[self.first] = self.state[self.second] = 'matched'
            self.first = self.second = -1
            self.found += 1
            if self.found == len(self.cards) // 2:
                self.over = True
                self.t1 = time.time()
                mgr = self.record_mgrs.get(self.level)
                if mgr:
                    mgr.add_win_record(best_time=self.t1 - self.t0)
                b = BEST.get(self.level)
                if b is None or self.moves < b:
                    BEST[self.level] = self.moves
        else:
            self.pending = MISMATCH_T

    # ---- input
    def layout(self, view):
        self.cs = self.fit_cell(view, self.cols, self.rows, max_cell=90)
        self.place(view, self.cols * self.cs, self.rows * self.cs)

    def _idx(self, x, y):
        cr = ov.cell_at(x, y, self.left, self.top,
                        self.cs, self.cols, self.rows)
        return -1 if cr is None else cr[1] * self.cols + cr[0]

    def move(self, x, y):
        h = self._idx(x, y)
        changed = h != self.hover
        self.hover = h
        return changed

    def click(self, x, y, button):
        if button != 'LEFT':
            return
        if self.over:
            self.reset()
            return
        i = self._idx(x, y)
        if i >= 0:
            self.flip_card(i)

    def key(self, key, repeat):
        if key == 'D':
            self.level = (self.level + 1) % len(LEVELS)
            self.reset()

    def update(self, dt):
        changed = False
        if self.pending > 0:
            self.pending -= dt
            if self.pending <= 0:
                self._resolve()
            changed = True
        step = dt / FLIP_T
        for i, s in enumerate(self.state):
            target = 0.0 if s == 'down' else 1.0
            f = self.flip[i]
            if f != target:
                f = min(target, f + step) if target > f else max(target, f - step)
                self.flip[i] = f
                changed = True
        if self.t0 is not None and not self.over:
            t = self.elapsed()
            if t != self.shown_t:
                self.shown_t = t
                changed = True
        return changed

    # ---- drawing
    def _draw_card(self, c, i, x, y):
        cs, u = self.cs, self.u
        g = max(3.0, 4 * u)
        w = cs - 2 * g
        f = self.flip[i]
        # horizontal squash while turning
        sx = abs(1 - 2 * f)
        cx, cy = x + cs / 2, y + cs / 2
        ww = max(2.0, w * sx)
        x0, y0 = cx - ww / 2, y + g

        if f > 0.5:
            if self.state[i] == 'matched':
                c.rect(x0, y0, ww, w, ov.C_GOOD)
                b = min(max(2.0, w * 0.05), ww / 2 - 0.5)
                c.rect(x0 + b, y0 + b, ww - 2 * b, w - 2 * b, C_FACE)
            else:
                c.rect(x0, y0, ww, w, C_FACE)
            shape, col = SYMBOLS[self.cards[i]]
            r = w * 0.30
            for a, b_, d in _tris(shape):
                c.tri((cx + a[0] * r * sx, cy + a[1] * r),
                      (cx + b_[0] * r * sx, cy + b_[1] * r),
                      (cx + d[0] * r * sx, cy + d[1] * r), col)
        else:
            hov = i == self.hover and self.state[i] == 'down' and not self.over
            base = C_BACK_H if hov else C_BACK
            c.rect(x0, y0, ww, w, base)
            inset = w * 0.12
            if ww > 2 * inset * sx + 2:
                c.rect(x0 + inset * sx, y0 + inset, ww - 2 *
                       inset * sx, w - 2 * inset, C_BACK_IN)
                r = w * 0.14
                c.poly([(cx, cy + r), (cx + r * sx, cy),
                       (cx, cy - r), (cx - r * sx, cy)], base)

    def draw(self, c):
        cs = self.cs
        name = LEVELS[self.level][0]
        self.draw_frame(c)

        best = BEST.get(self.level)
        sub = "Moves: %d   Pairs: %d/%d   Time: %ds   Best: %s" % (
            self.moves, self.found, len(self.cards) // 2, self.elapsed(),
            "-" if best is None else best)
        mgr = self.record_mgrs.get(self.level)
        bt = mgr.get_records().get("best_time") if mgr else None
        sub += " Record: %s" % ("%.1fs" % bt if bt is not None else "-")
        if self.over:
            self.draw_header(c, "You win!", sub, ov.C_GOLD)
        else:
            self.draw_header(c, "Memory - %s" % name, sub, ov.C_WHITE)

        for i in range(len(self.cards)):
            r, k = divmod(i, self.cols)
            x, y = self.left + k * cs, self.top - (r + 1) * cs
            self._draw_card(c, i, x, y)

        if self.over:
            bw, bh = self.cols * cs, self.rows * cs
            self.banner(c, self.left, self.top - bh, bw, bh, "You win!",
                        "%d moves in %d s" % (self.moves, self.elapsed()))
            self.draw_hint(c, "Click the board or press R to play again")
        else:
            self.draw_hint(c, "Click a card   D level   R restart   ESC quit")


RUNNER = ov.Runner("memory", GAME_NAME, Memory, [
    "LMB: turn a card",
    "D: change level (easy/medium/hard)",
    "R: restart   ESC: quit",
])


def draw_ui(layout, context):
    RUNNER.draw_ui(layout)


def register():
    RUNNER.register()


def unregister():
    RUNNER.unregister()
