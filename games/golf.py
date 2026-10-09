"""Mini Golf - 9 holes (3 easy, 3 medium, 3 hard), drawn as a GPU overlay.

Aim with the mouse (the ball shoots TOWARDS the cursor, the farther the cursor
the harder the shot) and click to putt. Sand slows the ball, ice makes it slide,
water costs a stroke, windmills and sliding gates move. Fewer strokes is better.

Records are saved for every hole (golf_hole_1 ... golf_hole_9): the best number
of strokes and the best time.
"""

import math
import random
import traceback

from . import _overlay as ov
from . import _record as _rec

GAME_NAME = "Mini Golf"
GAME_ICON = 'SPHERE'

W, H = 640.0, 400.0
CELL = 20.0
GW, GH = 32, 20
BALL_R = 5.0
CUP_R = 8.5
MAX_SPEED = 640.0
MAX_STROKES = 12
SCORE_BASE = 100                      # saved score = SCORE_BASE - strokes (higher is better)

# surface -> (linear friction, constant deceleration)
FRICTION = {'.': (0.9, 38.0), 'S': (4.2, 130.0), 'I': (0.16, 3.0)}

C_BG = (0.05, 0.09, 0.07, 1.0)
C_GRASS_A = (0.27, 0.60, 0.30, 1.0)
C_GRASS_B = (0.24, 0.55, 0.27, 1.0)
C_WALL = (0.42, 0.27, 0.16, 1.0)
C_WALL_TOP = (0.66, 0.46, 0.27, 1.0)
C_WALL_SIDE = (0.28, 0.17, 0.10, 1.0)
C_SAND = (0.88, 0.78, 0.50, 1.0)
C_WATER = (0.16, 0.42, 0.78, 1.0)
C_ICE = (0.70, 0.88, 0.97, 1.0)
C_BALL = (0.98, 0.98, 0.96, 1.0)
C_FLAG = (0.95, 0.25, 0.25, 1.0)
TIER_COL = ((0.35, 0.85, 0.45, 1.0), (1.00, 0.62, 0.22, 1.0), (0.95, 0.32, 0.32, 1.0))
TIER_NAME = ("Easy", "Medium", "Hard")

DIGITS = {n: i for i, n in enumerate(
    ('ONE', 'TWO', 'THREE', 'FOUR', 'FIVE', 'SIX', 'SEVEN', 'EIGHT', 'NINE'))}
DIGITS.update({'NUMPAD_%d' % (i + 1): i for i in range(9)})


# ----------------------------------------------------------------------------
# Holes (built on a 32 x 20 grid: '#' wall, '.' grass, 'S' sand, 'I' ice, 'W' water)
# ----------------------------------------------------------------------------

class Hole:
    def __init__(self, name, par, tier):
        self.name, self.par, self.tier = name, par, tier
        self.g = [['#'] * GW for _ in range(GH)]
        self.tee = (4, 10)
        self.cup = (27, 10)
        self.bumpers = []          # (x, y) in px
        self.blades = []           # (cx, cy, half_length, omega, phase)
        self.sliders = []          # (x0, y0, x1, y1, w, h, period, phase)

    def carve(self, x0, y0, x1, y1, ch='.'):
        for y in range(y0, y1 + 1):
            for x in range(x0, x1 + 1):
                self.g[y][x] = ch
        return self

    def wall(self, x0, y0, x1, y1):
        return self.carve(x0, y0, x1, y1, '#')

    def at(self, x, y):
        if 0 <= x < GW and 0 <= y < GH:
            return self.g[y][x]
        return '#'

    def pos(self, cx, cy):
        """Centre of grid cell (cx, cy) in px (y up)."""
        return (cx + 0.5) * CELL, H - (cy + 0.5) * CELL


def _make_holes():
    hs = []
    h = Hole("Warm-up", 2, 0).carve(2, 7, 29, 12)
    h.tee, h.cup = (4, 9), (27, 9)
    hs.append(h)

    h = Hole("Dog-leg", 3, 0).carve(2, 3, 25, 8).carve(21, 8, 25, 16)
    h.tee, h.cup = (4, 5), (23, 15)
    hs.append(h)

    h = Hole("Sandy Pass", 3, 0).carve(2, 5, 29, 14).carve(13, 5, 17, 10, 'S').wall(9, 10, 11, 14)
    h.tee, h.cup = (4, 8), (27, 11)
    hs.append(h)

    h = Hole("Water Bridge", 3, 1).carve(2, 4, 29, 15).carve(11, 4, 16, 15, 'W').carve(11, 9, 16, 10)
    h.tee, h.cup = (4, 9), (26, 10)
    hs.append(h)

    h = Hole("Zig-Zag", 4, 1).carve(2, 3, 29, 16).wall(9, 3, 9, 12).wall(15, 7, 15, 16).wall(21, 3, 21, 12)
    h.tee, h.cup = (4, 4), (26, 14)
    hs.append(h)

    h = Hole("Windmill", 3, 1).carve(2, 6, 29, 13)
    h.tee, h.cup = (4, 9), (27, 9)
    cx, cy = h.pos(15, 9)
    h.blades.append((cx + 10, cy - 10, 62.0, 1.7, 0.0))
    hs.append(h)

    h = Hole("Ice Rink", 4, 2).carve(2, 3, 29, 16, 'I')
    h.wall(8, 6, 9, 7).wall(14, 11, 15, 13).wall(20, 4, 21, 6).wall(22, 13, 23, 14)
    h.tee, h.cup = (4, 15), (27, 4)
    hs.append(h)

    h = Hole("Sliding Gates", 4, 2).carve(2, 5, 29, 14)
    h.tee, h.cup = (4, 9), (27, 9)
    h.sliders.append((9 * CELL + 10, 6 * CELL, 9 * CELL + 10, 13 * CELL, 18.0, 76.0, 3.2, 0.0))
    h.sliders.append((17 * CELL + 10, 6 * CELL, 17 * CELL + 10, 13 * CELL, 18.0, 76.0, 2.6, 0.5))
    hs.append(h)

    h = Hole("Grand Finale", 5, 2).carve(2, 2, 29, 17)
    h.wall(6, 2, 7, 11).wall(6, 14, 7, 17)
    h.carve(10, 9, 14, 17, 'W').carve(10, 2, 14, 7, 'S')
    h.carve(18, 11, 22, 16, 'S').wall(17, 2, 18, 4)
    h.tee, h.cup = (3, 16), (27, 3)
    h.bumpers += [h.pos(16, 8), h.pos(20, 5)]
    cx, cy = h.pos(24, 8)
    h.blades.append((cx, cy, 52.0, -2.0, 0.0))
    hs.append(h)

    for hole in hs:                                  # tee / cup squares must be playable ground
        for (x, y) in (hole.tee, hole.cup):
            if hole.g[y][x] in 'W#':
                hole.g[y][x] = '.'
    return hs


HOLES = _make_holes()


def _fmt_time(t):
    return "--" if t is None else "%d:%02d" % divmod(int(round(t)), 60)


def _label(strokes, par):
    d = strokes - par
    if strokes == 1:
        return "HOLE IN ONE!"
    return {-3: "Albatross!", -2: "Eagle!", -1: "Birdie!", 0: "Par", 1: "Bogey",
            2: "Double bogey"}.get(d, ("%+d" % d) if d else "Par")


class Ball:
    __slots__ = ('x', 'y', 'vx', 'vy')

    def __init__(self, x, y):
        self.x, self.y, self.vx, self.vy = x, y, 0.0, 0.0

    def speed(self):
        return math.hypot(self.vx, self.vy)


class MiniGolf(ov.BaseGame):
    keys = tuple(DIGITS) + ('SPACE', 'N', 'M', 'LEFT_ARROW', 'RIGHT_ARROW', 'UP_ARROW', 'DOWN_ARROW')

    # ---------------------------------------------------------------- setup
    def setup(self):
        self.hole_i = 0
        self.menu = True
        self.menu_hover = -1
        self.record_mgrs = {}             # hole -> RecordManager (golf_hole_N.json)
        self.best_cache = {}              # hole -> (best strokes or None, best time or None)
        self.sc, self.fx0, self.fy0 = 1.0, 0.0, 0.0
        self.mouse = (W / 2, H / 2)
        self.t_world = 0.0
        self.shown = 0

    def reset(self):
        self._load(self.hole_i)

    def _load(self, i):
        self.hole_i = i
        self.hole = HOLES[i]
        hx, hy = self.hole.pos(*self.hole.tee)
        self.ball = Ball(hx, hy)
        self.rest = (hx, hy)
        self.cup = self.hole.pos(*self.hole.cup)
        self.strokes = 0
        self.play_t = 0.0
        self.started = False
        self.state = 'aim'                # aim | roll | splash | sunk | done | fail
        self.timer = 0.0
        self.trail = []
        self.bump_t = [0.0] * len(self.hole.bumpers)
        self.aim_ang = 0.0
        self.aim_pow = 0.5
        self.msg = ""
        self.new_strokes = self.new_time = False
        self.result = ""
        self.sink_r = 1.0
        self.splash = None
        self.recorded = False

    # -------------------------------------------------------------- records
    def _mgr(self, i):
        if i not in self.record_mgrs:
            try:
                self.record_mgrs[i] = _rec.RecordManager(game_name="golf_hole_%d" % (i + 1))
            except Exception:
                self.record_mgrs[i] = None
        return self.record_mgrs[i]

    def _best(self, i):
        if i not in self.best_cache:
            m = self._mgr(i)
            try:
                r = m.get_records() if m else {}
                hs = int(r.get("highest_score", 0) or 0)
                self.best_cache[i] = ((SCORE_BASE - hs) if hs > 0 else None, r.get("best_time"))
            except Exception:
                self.best_cache[i] = (None, None)
        return self.best_cache[i]

    def _record_win(self):
        if self.recorded:
            return
        self.recorded = True
        prev_s, prev_t = self._best(self.hole_i)
        m = self._mgr(self.hole_i)
        self.new_strokes = prev_s is None or self.strokes < prev_s
        self.new_time = prev_t is None or self.play_t < prev_t
        if not m:
            return
        try:
            m.add_win_record(score=SCORE_BASE - self.strokes, best_time=self.play_t)
            self.best_cache.pop(self.hole_i, None)
        except Exception:
            traceback.print_exc()

    def _record_fail(self):
        if self.recorded:
            return
        self.recorded = True
        m = self._mgr(self.hole_i)
        if m:
            try:
                m.add_lose_record()
            except Exception:
                traceback.print_exc()

    # -------------------------------------------------------------- physics
    def _surface(self, x, y):
        return self.hole.at(int(x // CELL), int((H - y) // CELL))

    def _rect_hit(self, b, x0, y0, x1, y1, ox=0.0, oy=0.0, e=0.82):
        nx, ny = min(max(b.x, x0), x1), min(max(b.y, y0), y1)
        dx, dy = b.x - nx, b.y - ny
        d2 = dx * dx + dy * dy
        if d2 >= BALL_R * BALL_R:
            return False
        if d2 > 1e-9:
            d = math.sqrt(d2)
            nx_, ny_, pen = dx / d, dy / d, BALL_R - d
        else:                                           # centre inside the rect: leave by the nearest side
            opts = ((b.x - x0, -1, 0), (x1 - b.x, 1, 0), (b.y - y0, 0, -1), (y1 - b.y, 0, 1))
            dist, nx_, ny_ = min(opts, key=lambda o: o[0])
            pen = BALL_R + dist
        b.x += nx_ * pen
        b.y += ny_ * pen
        rvx, rvy = b.vx - ox, b.vy - oy
        vn = rvx * nx_ + rvy * ny_
        if vn < 0:
            rvx -= (1 + e) * vn * nx_
            rvy -= (1 + e) * vn * ny_
        b.vx, b.vy = rvx + ox, rvy + oy
        return True

    def _slider_rect(self, s, t):
        x0, y0, x1, y1, w, h, period, ph = s
        k = 0.5 - 0.5 * math.cos(2 * math.pi * (t / period + ph))
        cx, cy = x0 + (x1 - x0) * k, y0 + (y1 - y0) * k
        # velocity of the block
        dk = math.pi / period * math.sin(2 * math.pi * (t / period + ph))
        return (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2), ((x1 - x0) * dk, (y1 - y0) * dk)

    def _blade_ends(self, bl, t):
        cx, cy, half, om, ph = bl
        a = om * t + ph
        return (cx, cy), (math.cos(a) * half, math.sin(a) * half), om

    def _collide(self, b, t, live):
        hole = self.hole
        cx0, cx1 = int((b.x - BALL_R) // CELL), int((b.x + BALL_R) // CELL)
        cy0, cy1 = int((H - b.y - BALL_R) // CELL), int((H - b.y + BALL_R) // CELL)
        for cy in range(cy0, cy1 + 1):
            for cx in range(cx0, cx1 + 1):
                if hole.at(cx, cy) == '#':
                    self._rect_hit(b, cx * CELL, H - (cy + 1) * CELL, (cx + 1) * CELL, H - cy * CELL)
        if live:
            for s in hole.sliders:
                r, v = self._slider_rect(s, t)
                self._rect_hit(b, r[0], r[1], r[2], r[3], v[0], v[1], 0.9)
            for bl in hole.blades:
                (cx, cy), (dx, dy), om = self._blade_ends(bl, t)
                ax, ay, bx, by = cx - dx, cy - dy, cx + dx, cy + dy
                abx, aby = bx - ax, by - ay
                u = max(0.0, min(1.0, ((b.x - ax) * abx + (b.y - ay) * aby) / (abx * abx + aby * aby)))
                qx, qy = ax + abx * u, ay + aby * u
                ex, ey = b.x - qx, b.y - qy
                d = math.hypot(ex, ey)
                if d < BALL_R + 2.5:
                    if d < 1e-6:
                        ex, ey, d = -aby, abx, math.hypot(abx, aby)
                    nx, ny = ex / d, ey / d
                    pen = BALL_R + 2.5 - d
                    b.x += nx * pen
                    b.y += ny * pen
                    sx, sy = -om * (qy - cy), om * (qx - cx)        # surface velocity at the contact
                    rvx, rvy = b.vx - sx, b.vy - sy
                    vn = rvx * nx + rvy * ny
                    if vn < 0:
                        rvx -= 1.9 * vn * nx
                        rvy -= 1.9 * vn * ny
                    b.vx, b.vy = rvx + sx, rvy + sy
        for k, (px, py) in enumerate(hole.bumpers):
            ex, ey = b.x - px, b.y - py
            d = math.hypot(ex, ey)
            if d < BALL_R + 10.0:
                d = d or 1e-6
                nx, ny = ex / d, ey / d
                pen = BALL_R + 10.0 - d
                b.x += nx * pen
                b.y += ny * pen
                vn = b.vx * nx + b.vy * ny
                if vn < 0:
                    b.vx -= 2 * vn * nx
                    b.vy -= 2 * vn * ny
                    sp = b.speed()
                    f = min(1.18, 560.0 / sp) if sp > 1 else 1.0
                    b.vx, b.vy = b.vx * f, b.vy * f
                    if live:
                        self.bump_t[k] = 0.25

    def _advance(self, b, dt, t, live=True):
        """Move the ball for dt seconds. Returns None, 'sunk' or 'water'."""
        sp = b.speed()
        n = max(1, int(sp * dt / 2.5) + 1)
        h = dt / n
        for _ in range(n):
            b.x += b.vx * h
            b.y += b.vy * h
            self._collide(b, t + h, live)
            surf = self._surface(b.x, b.y)
            if surf == 'W':
                return 'water'
            lin, const = FRICTION.get(surf, FRICTION['.'])
            s = b.speed()
            if s > 0:
                s2 = max(0.0, s * math.exp(-lin * h) - const * h)
                k = s2 / s
                b.vx, b.vy = b.vx * k, b.vy * k
            dx, dy = self.cup[0] - b.x, self.cup[1] - b.y
            d = math.hypot(dx, dy)
            s = b.speed()
            if d < CUP_R and s < 430.0:
                return 'sunk'
            if d < 16.0 and s < 190.0 and d > 1e-6:      # the cup pulls slow balls in
                b.vx += dx / d * 520.0 * h
                b.vy += dy / d * 520.0 * h
        return None

    # --------------------------------------------------------------- shots
    def _aim_from(self, x, y):
        bx, by = self.ball.x, self.ball.y
        dx, dy = x - bx, y - by
        d = math.hypot(dx, dy)
        if d > 3:
            self.aim_ang = math.atan2(dy, dx)
        self.aim_pow = max(0.06, min(1.0, (d - 10.0) / 150.0))

    def _shoot(self):
        if self.state != 'aim' or self.menu:
            return
        b = self.ball
        sp = 70.0 + (MAX_SPEED - 70.0) * self.aim_pow
        b.vx, b.vy = math.cos(self.aim_ang) * sp, math.sin(self.aim_ang) * sp
        self.rest = (b.x, b.y)
        self.strokes += 1
        self.started = True
        self.state = 'roll'
        self.trail = []

    def _next_hole(self):
        self._load((self.hole_i + 1) % len(HOLES))

    # ----------------------------------------------------------------- update
    def update(self, dt):
        if self.menu:
            return False
        dt = min(dt, 0.05)
        self.t_world += dt
        changed = False
        for k in range(len(self.bump_t)):
            if self.bump_t[k] > 0:
                self.bump_t[k] = max(0.0, self.bump_t[k] - dt)
                changed = True
        if self.started and self.state in ('aim', 'roll', 'splash', 'sunk'):
            self.play_t += dt
            s = int(self.play_t)
            if s != self.shown:
                self.shown = s
                changed = True
        if self.hole.blades or self.hole.sliders:
            changed = True
        b = self.ball
        if self.state == 'roll':
            res = self._advance(b, dt, self.t_world)
            self.trail.append((b.x, b.y))
            if len(self.trail) > 16:
                self.trail.pop(0)
            if res == 'sunk':
                b.vx = b.vy = 0.0
                b.x, b.y = self.cup
                self.state, self.timer = 'sunk', 0.7
            elif res == 'water':
                self.splash = (b.x, b.y)
                b.vx = b.vy = 0.0
                self.state, self.timer = 'splash', 1.0
                self.strokes += 1                    # water penalty
                self.msg = "Splash! +1 stroke"
            elif b.speed() < 14.0:
                b.vx = b.vy = 0.0
                self.state = 'aim'
                if self.strokes >= MAX_STROKES:
                    self.state = 'fail'
                    self._record_fail()
            return True
        if self.state == 'splash':
            self.timer -= dt
            if self.timer <= 0:
                b.x, b.y = self.rest
                self.splash = None
                self.state = 'aim'
                if self.strokes >= MAX_STROKES:
                    self.state = 'fail'
                    self._record_fail()
            return True
        if self.state == 'sunk':
            self.timer -= dt
            self.sink_r = max(0.0, self.timer / 0.7)
            if self.timer <= 0:
                self.state = 'done'
                self.result = _label(self.strokes, self.hole.par)
                self._record_win()
            return True
        return changed

    # ------------------------------------------------------------------ input
    def _logical(self, x, y):
        return (x - self.fx0) / self.sc, (y - self.fy0) / self.sc

    def _card(self, i):
        col, row = i % 3, i // 3
        w, h = 200.0, 104.0
        return 10.0 + col * (w + 10.0), 262.0 - row * (h + 9.0), w, h

    def _card_idx(self, x, y):
        lx, ly = self._logical(x, y)
        for i in range(len(HOLES)):
            cx, cy, w, h = self._card(i)
            if cx <= lx <= cx + w and cy <= ly <= cy + h:
                return i
        return -1

    def _start_hole(self, i):
        self.menu = False
        self._load(i)

    def move(self, x, y):
        if self.menu:
            h = self._card_idx(x, y) if x > -1e5 else -1
            changed = h != self.menu_hover
            self.menu_hover = h
            return changed
        if x < -1e5:
            return False
        lx, ly = self._logical(x, y)
        self.mouse = (lx, ly)
        if self.state == 'aim':
            self._aim_from(lx, ly)
            return True
        return False

    def click(self, x, y, button):
        if self.menu:
            if button == 'LEFT':
                i = self._card_idx(x, y)
                if i >= 0:
                    self._start_hole(i)
            return
        if button != 'LEFT':
            return
        if self.state in ('done', 'fail'):
            if self.state == 'done':
                self._next_hole()
            else:
                self._load(self.hole_i)
            return
        if self.state == 'aim':
            lx, ly = self._logical(x, y)
            self.mouse = (lx, ly)
            self._aim_from(lx, ly)
            self._shoot()

    def key(self, key, repeat):
        if self.menu:
            i = DIGITS.get(key)
            if i is not None and i < len(HOLES):
                self._start_hole(i)
            return
        if key == 'M':
            self.menu, self.menu_hover = True, -1
        elif key == 'N':
            if self.state in ('done', 'fail', 'aim'):
                self._next_hole()
        elif key == 'SPACE':
            if self.state == 'aim':
                self._shoot()
            elif self.state == 'done':
                self._next_hole()
            elif self.state == 'fail':
                self._load(self.hole_i)
        elif self.state == 'aim':
            if key == 'LEFT_ARROW':
                self.aim_ang += math.radians(2.0)
            elif key == 'RIGHT_ARROW':
                self.aim_ang -= math.radians(2.0)
            elif key == 'UP_ARROW':
                self.aim_pow = min(1.0, self.aim_pow + 0.04)
            elif key == 'DOWN_ARROW':
                self.aim_pow = max(0.06, self.aim_pow - 0.04)

    # ----------------------------------------------------------------- layout
    def layout(self, view):
        cs = self.fit_cell(view, 8, 5, max_cell=100, min_cell=36)
        self.place(view, 8 * cs, 5 * cs)
        self.sc = cs / 80.0
        self.fx0 = self.left
        self.fy0 = self.top - 5 * cs

    # ---------------------------------------------------------------- drawing
    def _X(self, v):
        return self.fx0 + v * self.sc

    def _Y(self, v):
        return self.fy0 + v * self.sc

    def _rect(self, c, x, y, w, h, col):
        c.rect(self._X(x), self._Y(y), w * self.sc, h * self.sc, col)

    def _circ(self, c, x, y, r, col, seg=20):
        c.circle(self._X(x), self._Y(y), r * self.sc, col, seg)

    def _text(self, c, s, x, y, size, col=ov.C_TEXT, align='center'):
        c.text(s, self._X(x), self._Y(y), size * self.sc, col, align)

    def _draw_course(self, c):
        hole, sc = self.hole, self.sc
        t = self.t_world
        self._rect(c, 0, 0, W, H, C_BG)
        for row in range(GH):
            y = H - (row + 1) * CELL
            col = 0
            while col < GW:
                ch = hole.g[row][col]
                s = col
                while col < GW and hole.g[row][col] == ch:
                    col += 1
                x0, w = s * CELL, (col - s) * CELL
                if ch == '#':
                    self._rect(c, x0, y, w, CELL, C_WALL)
                    above = hole.at(s, row - 1)
                    if above != '#':
                        self._rect(c, x0, y + CELL - 4, w, 4, C_WALL_TOP)
                    self._rect(c, x0, y, w, 3, C_WALL_SIDE)
                else:
                    for k in range(s, col):
                        base = C_GRASS_A if (k + row) % 2 == 0 else C_GRASS_B
                        if ch == 'S':
                            base = C_SAND
                        elif ch == 'W':
                            base = ov.shade(C_WATER, 0.95 + 0.07 * math.sin(t * 2 + k * 0.7 + row))
                        elif ch == 'I':
                            base = ov.shade(C_ICE, 1.0 if (k + row) % 2 == 0 else 0.95)
                        self._rect(c, k * CELL, y, CELL, CELL, base)
                        if ch == 'S' and (k * 7 + row * 13) % 3 == 0:
                            self._circ(c, k * CELL + 6 + (row * 5 % 9), y + 7 + (k * 3 % 8), 1.4,
                                       (0.70, 0.60, 0.36, 1.0), 6)
                        if ch == 'W':
                            yy = y + 6 + 5 * math.sin(t * 2.2 + k)
                            self._rect(c, k * CELL + 3, yy, CELL - 6, 1.6, (1, 1, 1, 0.28))
                        if ch == 'I':
                            self._rect(c, k * CELL + 3, y + 12, CELL - 6, 1.4, (1, 1, 1, 0.45))
                            self._rect(c, k * CELL + 8, y + 5, 6, 1.4, (1, 1, 1, 0.35))
                        if ch != '#' and hole.at(k, row - 1) == '#':
                            self._rect(c, k * CELL, y + CELL - 5, CELL, 5, (0, 0, 0, 0.22))   # wall shadow
        # tee mat and cup
        tx, ty = hole.pos(*hole.tee)
        self._rect(c, tx - 13, ty - 9, 26, 18, (0.20, 0.45, 0.24, 1.0))
        self._rect(c, tx - 11, ty - 7, 22, 14, (0.78, 0.86, 0.80, 0.55))
        cx, cy = self.cup
        self._circ(c, cx, cy - 1, CUP_R + 2.5, (0.0, 0.0, 0.0, 0.30), 24)
        self._circ(c, cx, cy, CUP_R + 1.0, (0.12, 0.30, 0.15, 1.0), 24)
        self._circ(c, cx, cy, CUP_R, (0.02, 0.02, 0.03, 1.0), 24)
        # flag
        wave = 2.0 * math.sin(t * 5.0)
        c.line((self._X(cx + 1), self._Y(cy)), (self._X(cx + 1), self._Y(cy + 38)), 2.0 * sc + 0.5, (0.92, 0.92, 0.95, 1.0))
        c.tri((self._X(cx + 2), self._Y(cy + 38)), (self._X(cx + 2), self._Y(cy + 25)),
              (self._X(cx + 20 + wave), self._Y(cy + 31)), C_FLAG)
        self._circ(c, cx + 1, cy + 39, 2.2, ov.C_GOLD, 8)
        # bumpers
        for k, (px, py) in enumerate(hole.bumpers):
            pulse = self.bump_t[k] / 0.25
            self._circ(c, px, py - 2, 12.5, (0, 0, 0, 0.3), 20)
            self._circ(c, px, py, 11.0 + 2.5 * pulse, (0.78, 0.14, 0.20, 1.0), 22)
            self._circ(c, px, py, 7.5, (1.0, 0.45, 0.50, 1.0), 20)
            c.ring(self._X(px), self._Y(py), 11.0 * sc, 2.0 * sc + 0.5, (1, 1, 1, 0.9), 22)
        # sliding gates
        for s in hole.sliders:
            r, _ = self._slider_rect(s, self.t_world)
            self._rect(c, r[0] + 2, r[1] - 3, r[2] - r[0], r[3] - r[1], (0, 0, 0, 0.30))
            self._rect(c, r[0], r[1], r[2] - r[0], r[3] - r[1], (0.88, 0.88, 0.92, 1.0))
            nstripe = int((r[3] - r[1]) // 12)
            for i in range(nstripe):
                if i % 2 == 0:
                    self._rect(c, r[0], r[1] + i * 12, r[2] - r[0], 12, (0.90, 0.25, 0.25, 1.0))
            self._rect(c, r[0], r[3] - 3, r[2] - r[0], 3, (0, 0, 0, 0.18))
        # windmills
        for bl in hole.blades:
            (cx0, cy0), (dx, dy), om = self._blade_ends(bl, self.t_world)
            self._circ(c, cx0 + 3, cy0 - 3, 11, (0, 0, 0, 0.28), 16)
            c.line((self._X(cx0 - dx), self._Y(cy0 - dy)), (self._X(cx0 + dx), self._Y(cy0 + dy)),
                   6.0 * sc, (0.95, 0.92, 0.85, 1.0))
            c.line((self._X(cx0 - dx), self._Y(cy0 - dy)), (self._X(cx0 + dx), self._Y(cy0 + dy)),
                   2.4 * sc, (0.85, 0.30, 0.30, 1.0))
            for sgn in (-1, 1):
                self._circ(c, cx0 + sgn * dx, cy0 + sgn * dy, 4.0, (0.95, 0.75, 0.20, 1.0), 10)
            self._circ(c, cx0, cy0, 8.0, (0.35, 0.22, 0.14, 1.0), 16)
            self._circ(c, cx0, cy0, 4.0, (0.80, 0.60, 0.30, 1.0), 12)

    def _draw_ball(self, c):
        b = self.ball
        if self.state == 'splash':
            if self.splash:
                p = 1.0 - self.timer
                sx, sy = self.splash
                for k in range(3):
                    r = 6 + 20 * p + k * 6
                    c.ring(self._X(sx), self._Y(sy), r * self.sc, 1.6 * self.sc + 0.5,
                           (1, 1, 1, max(0.0, 0.8 - p - k * 0.15)), 22)
            return
        r = BALL_R * (self.sink_r if self.state in ('sunk',) else 1.0)
        if self.state == 'done':
            return
        for i, (tx, ty) in enumerate(self.trail):
            a = (i + 1) / (len(self.trail) + 1)
            self._circ(c, tx, ty, BALL_R * 0.75 * a, (1, 1, 1, 0.12 * a), 8)
        self._circ(c, b.x + 1.5, b.y - 2.0, r, (0, 0, 0, 0.30), 14)
        self._circ(c, b.x, b.y, r, (0.80, 0.82, 0.84, 1.0), 16)
        self._circ(c, b.x - 0.4, b.y + 0.4, r * 0.88, C_BALL, 16)
        self._circ(c, b.x - r * 0.3, b.y + r * 0.35, r * 0.28, (1, 1, 1, 1), 8)

    def _draw_aim(self, c):
        b = self.ball
        tier = self.hole.tier
        horizon = (2.4, 0.8, 0.22)[tier]
        ca, sa = math.cos(self.aim_ang), math.sin(self.aim_ang)
        sp = 70.0 + (MAX_SPEED - 70.0) * self.aim_pow
        p = Ball(b.x, b.y)
        p.vx, p.vy = ca * sp, sa * sp
        pts, t, acc = [], 0.0, 0.0
        step = 1.0 / 60.0
        while t < horizon and p.speed() > 20.0:
            res = self._advance(p, step, self.t_world + t, False)
            t += step
            acc += p.speed() * step
            if acc > 11.0:
                acc = 0.0
                pts.append((p.x, p.y))
            if res:
                break
        for i, (x, y) in enumerate(pts):
            a = 0.9 * (1.0 - i / max(1, len(pts)) * 0.7)
            self._circ(c, x, y, 1.7, (1, 1, 1, a), 8)
        # arrow + power
        L = 16.0 + 46.0 * self.aim_pow
        ex, ey = b.x + ca * L, b.y + sa * L
        col = (0.35 + 0.65 * self.aim_pow, 0.95 - 0.55 * self.aim_pow, 0.30, 1.0)
        c.line((self._X(b.x + ca * 9), self._Y(b.y + sa * 9)), (self._X(ex), self._Y(ey)), 3.0 * self.sc + 0.5, col)
        c.tri((self._X(ex + ca * 7), self._Y(ey + sa * 7)),
              (self._X(ex - sa * 5), self._Y(ey + ca * 5)),
              (self._X(ex + sa * 5), self._Y(ey - ca * 5)), col)
        # power bar
        self._rect(c, 12, 10, 112, 11, (0, 0, 0, 0.55))
        self._rect(c, 14, 12, 108 * self.aim_pow, 7, col)
        self._text(c, "POWER", 68, 29, 9, ov.C_TEXT)

    def _draw_menu(self, c):
        self._rect(c, 0, 0, W, H, (0.04, 0.07, 0.06, 1.0))
        for k in range(8):                                     # soft stripes
            self._rect(c, 0, k * 50, W, 25, (1, 1, 1, 0.012))
        self._text(c, "SELECT A HOLE", W / 2, 383, 20, ov.C_WHITE)
        for i, hole in enumerate(HOLES):
            x, y, w, h = self._card(i)
            hov = i == self.menu_hover
            col = TIER_COL[hole.tier]
            self._rect(c, x + 2, y - 3, w, h, (0, 0, 0, 0.35))
            self._rect(c, x, y, w, h, ov.C_CELL_HOVER if hov else ov.C_CELL)
            self._rect(c, x, y, 7, h, col)
            self._rect(c, x + 7, y + h - 3, w - 7, 3, ov.alpha(col, 0.7))
            self._text(c, "%d  %s" % (i + 1, hole.name), x + 18, y + h - 22, 16, ov.C_WHITE, 'left')
            self._text(c, "%s   -   Par %d" % (TIER_NAME[hole.tier], hole.par), x + 18, y + h - 44, 11, col, 'left')
            bs, bt = self._best(i)
            if bs is None:
                self._text(c, "Not played yet", x + 18, y + 24, 12, ov.C_TEXT, 'left')
            else:
                self._text(c, "Best  %d stroke%s" % (bs, "" if bs == 1 else "s"), x + 18, y + 38, 12, ov.C_GOLD, 'left')
                self._text(c, "Best time  %s" % _fmt_time(bt), x + 18, y + 20, 12, ov.C_GOLD, 'left')
            # mini preview
            pw, ph = 62.0, 39.0
            px0, py0 = x + w - pw - 10, y + 12
            self._rect(c, px0 - 1, py0 - 1, pw + 2, ph + 2, (0, 0, 0, 0.5))
            for row in range(GH):
                for k in range(GW):
                    ch = hole.g[row][k]
                    if ch == '#':
                        continue
                    cc = {'S': C_SAND, 'W': C_WATER, 'I': C_ICE}.get(ch, C_GRASS_A)
                    self._rect(c, px0 + k * pw / GW, py0 + (GH - 1 - row) * ph / GH, pw / GW + 0.3, ph / GH + 0.3, cc)

    def draw(self, c):
        self.draw_frame(c)
        hole = self.hole
        bs, bt = self._best(self.hole_i)
        if self.menu:
            self.draw_header(c, "MINI GOLF", "9 holes   -   fewer strokes and faster time = better", ov.C_GOLD)
            self._draw_menu(c)
            self.draw_hint(c, "Click a hole or press 1-9   ESC quit")
            return
        main = "Hole %d - %s" % (self.hole_i + 1, hole.name)
        sub = "Par %d   |   Strokes %d   |   Time %s   |   Best %s (%s)" % (
            hole.par, self.strokes, _fmt_time(self.play_t),
            "--" if bs is None else "%d" % bs, _fmt_time(bt))
        self.draw_header(c, main, sub, TIER_COL[hole.tier])
        self._draw_course(c)
        if self.state == 'aim':
            self._draw_aim(c)
        self._draw_ball(c)
        if self.msg and self.state in ('splash', 'aim'):
            self._text(c, self.msg, W / 2, H - 14, 13, ov.C_BAD)
        bw, bh = W * self.sc, H * self.sc
        if self.state == 'done':
            lines = []
            if self.new_strokes:
                lines.append("NEW BEST STROKES!")
            if self.new_time:
                lines.append("NEW BEST TIME!")
            sub = "%d stroke%s (par %d)   -   %s   -   %s" % (
                self.strokes, "" if self.strokes == 1 else "s", hole.par, _fmt_time(self.play_t),
                "   ".join(lines) or "click / N: next hole   M: menu")
            self.banner(c, self.fx0, self.fy0, bw, bh, self.result, sub)
        elif self.state == 'fail':
            self.banner(c, self.fx0, self.fy0, bw, bh, "Stroke limit reached",
                        "%d strokes - click or R to retry   M: menu" % MAX_STROKES)
        if self.state == 'aim':
            self.draw_hint(c, "Mouse: aim & power   Click / SPACE: putt   Arrows: fine aim   M holes   R retry")
        else:
            self.draw_hint(c, "N next hole   M holes   R retry   ESC quit")


RUNNER = ov.Runner("minigolf", GAME_NAME, MiniGolf, [
    "Mouse: aim (cursor distance = power)",
    "LMB / SPACE: putt",
    "Arrows: fine aim and power",
    "Sand slows, ice slides, water = +1 stroke",
    "Best strokes and best time per hole",
    "M: hole menu   N: next   R: retry   ESC: quit",
])


def draw_ui(layout, context):
    RUNNER.draw_ui(layout)


def register():
    RUNNER.register()


def unregister():
    RUNNER.unregister()
