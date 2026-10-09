"""Frogger - keep the mouse over the 3D Viewport and use the keyboard.

Cross the road, hop on logs and turtles over the river and reach one of the
5 glowing gold bays in the top hedge (the dark green hedge itself is deadly).
Turtles dive now and then. Fill all bays to clear the level.
"""

import math
import traceback

from . import _overlay as ov
from . import _record as _rec

GAME_NAME = "Frogger"
GAME_ICON = 'ARMATURE_DATA'

COLS, ROWS = 15, 13          # row 0 = start, 1-5 road, 6 median, 7-11 river, 12 goal
MEDIAN, GOAL = 6, 12
BAYS = (1, 4, 7, 10, 13)     # column of each home bay
TIME_LIMIT = 30.0

KEYMAP = {
    'UP_ARROW': 'UP', 'W': 'UP',
    'DOWN_ARROW': 'DOWN', 'S': 'DOWN',
    'LEFT_ARROW': 'LEFT', 'A': 'LEFT',
    'RIGHT_ARROW': 'RIGHT', 'D': 'RIGHT',
}
DIRS = {'UP': (0, 1), 'DOWN': (0, -1), 'LEFT': (-1, 0), 'RIGHT': (1, 0)}

C_GRASS = (0.20, 0.38, 0.22, 1.0)
C_ROAD = (0.15, 0.16, 0.19, 1.0)
C_WATER = (0.11, 0.23, 0.42, 1.0)
C_HEDGE = (0.10, 0.28, 0.15, 1.0)
C_FROG = (0.36, 0.86, 0.36, 1.0)
C_LOG = (0.47, 0.31, 0.17, 1.0)
C_TURTLE = (0.22, 0.64, 0.40, 1.0)
C_BAY_FRAME = (0.98, 0.82, 0.25, 1.0)
C_BAY_PAD = (0.50, 0.90, 0.45, 1.0)
C_BAY_DONE = (0.30, 0.34, 0.30, 1.0)

# row, kind, length (cells), gap (cells), speed (cells/s, + = right), colour
LANE_SPECS = [
    (1, 'car',    1, 3.5, -2.0, (0.90, 0.30, 0.30, 1.0)),
    (2, 'car',    1, 4.5,  3.0, (0.95, 0.80, 0.25, 1.0)),
    (3, 'truck',  2, 4.0, -1.5, (0.85, 0.85, 0.90, 1.0)),
    (4, 'car',    1, 3.0,  2.2, (0.35, 0.60, 0.95, 1.0)),
    (5, 'truck',  3, 5.0, -1.2, (0.95, 0.55, 0.20, 1.0)),
    (7, 'turtle', 2, 2.2, -1.4, C_TURTLE),
    (8, 'log',    3, 2.6,  1.1, C_LOG),
    (9, 'log',    5, 3.0,  2.0, C_LOG),
    (10, 'turtle', 3, 2.4, -1.1, C_TURTLE),
    (11, 'log',   3, 2.8,  1.5, C_LOG),
]


class Lane:
    def __init__(self, row, kind, length, gap, speed, color):
        self.row, self.kind, self.length, self.speed, self.color = row, kind, length, speed, color
        self.spacing = length + gap
        self.n = math.ceil((COLS + length) / self.spacing)
        self.wrap = self.n * self.spacing
        self.offset = row * 1.7

    def objects(self):
        """[(x_left_in_cells, index)] - the lane is a seamless loop."""
        return [(((self.offset + i * self.spacing) % self.wrap) - self.length, i)
                for i in range(self.n)]


class Frogger(ov.BaseGame):
    keys = tuple(KEYMAP) + ('SPACE', 'P')

    def setup(self):
        self.best = 0
        self.record_mgrs = {}        # level -> RecordManager (frogger_level_N.json)
        self.best_cache = {}         # level -> best clear time in seconds (or None)

    def reset(self):
        self.lanes = [Lane(*s) for s in LANE_SPECS]
        self.by_row = {l.row: l for l in self.lanes}
        self.score, self.lives, self.level, self.mult = 0, 3, 1, 1.0
        self.filled = [False] * len(BAYS)
        self.t = 0.0
        self.level_time = 0.0     # seconds actually played in the current level
        self.state = 'ready'      # ready | playing | dying | paused | over
        self.die_t = 0.0
        self.cause = ''
        self.cs = 30
        self._respawn()

    # ---- records (best clear time per level)
    def _mgr(self, level):
        if level not in self.record_mgrs:
            try:
                self.record_mgrs[level] = _rec.RecordManager(game_name="frogger_level_%d" % level)
            except Exception:
                self.record_mgrs[level] = None
        return self.record_mgrs[level]

    def _best_time(self, level):
        if level not in self.best_cache:
            m = self._mgr(level)
            try:
                self.best_cache[level] = m.get_records().get("best_time") if m else None
            except Exception:
                self.best_cache[level] = None
        return self.best_cache[level]

    @staticmethod
    def _fmt(t):
        return "--" if t is None else "%d:%02d" % divmod(int(round(t)), 60)

    def _record_clear(self):
        m = self._mgr(self.level)
        if not m:
            return
        try:
            try:
                m.add_win_record(score=self.score, best_time=self.level_time)
            except TypeError:        # _record.py without best_time support
                m.add_win_record(score=self.score)
            self.best_cache.pop(self.level, None)
        except Exception:
            traceback.print_exc()

    def _record_game_over(self):
        m = self._mgr(self.level)
        if m:
            try:
                m.add_lose_record()
            except Exception:
                traceback.print_exc()

    # ---- logic
    def _respawn(self):
        self.fx = COLS / 2
        self.row = 0
        self.max_row = 0
        self.clock = TIME_LIMIT
        self.face = 'UP'

    def die(self, cause):
        if self.state != 'playing':
            return
        self.state = 'dying'
        self.die_t = 1.0
        self.cause = cause
        self.lives -= 1
        if self.lives <= 0:
            self.best = max(self.best, self.score)
            self._record_game_over()

    def _dive(self, lane, i):
        """0 = floating, 1 = sinking (still safe), 2 = submerged."""
        phase = (self.t + i * 2.3 + lane.row * 1.1) % 9.0
        return 2 if phase >= 7.8 else 1 if phase >= 6.6 else 0

    def _supported(self, lane):
        for x, i in lane.objects():
            if x <= self.fx <= x + lane.length:
                if lane.kind == 'turtle' and self._dive(lane, i) == 2:
                    continue
                return True
        return False

    def _check(self):
        r = self.row
        if 1 <= r <= 5:
            for x, _ in self.by_row[r].objects():
                L = self.by_row[r].length
                if self.fx + 0.32 > x + 0.08 and self.fx - 0.32 < x + L - 0.08:
                    self.die('car')
                    return
        elif 7 <= r <= 11:
            if not (0 <= self.fx <= COLS) or not self._supported(self.by_row[r]):
                self.die('water')

    def _hop(self, d):
        dx, dy = DIRS[d]
        if dy:
            nr = self.row + dy
            if nr < 0:
                return
            self.row = nr
            if not 7 <= nr <= 11:      # land: snap to the grid
                self.fx = min(COLS - 0.5, max(0.5, math.floor(self.fx) + 0.5))
            if nr > self.max_row:
                self.max_row = nr
                self.score += 10
            if nr == GOAL:
                self._goal()
                return
        else:
            nx = self.fx + dx
            if nx < 0.3 or nx > COLS - 0.3:
                return
            self.fx = nx
        self._check()

    def _goal(self):
        cell = int(self.fx)
        if cell in BAYS and not self.filled[BAYS.index(cell)]:
            self.filled[BAYS.index(cell)] = True
            self.score += 50 + int(self.clock) * 2
            if all(self.filled):
                self._record_clear()
                self.level_time = 0.0
                self.level += 1
                self.mult = 1 + 0.15 * (self.level - 1)
                self.score += 200
                self.filled = [False] * len(BAYS)
            self._respawn()
        else:
            self.die('taken' if cell in BAYS else 'hedge')

    def update(self, dt):
        if self.state not in ('ready', 'playing', 'dying'):
            return False
        self.t += dt
        for l in self.lanes:
            l.offset += l.speed * self.mult * dt
        if self.state == 'playing':
            self.clock -= dt
            self.level_time += dt
            if 7 <= self.row <= 11:
                self.fx += self.by_row[self.row].speed * self.mult * dt
            self._check()
            if self.state == 'playing' and self.clock <= 0:
                self.die('time')
        elif self.state == 'dying':
            self.die_t -= dt
            if self.die_t <= 0:
                if self.lives <= 0:
                    self.state = 'over'
                else:
                    self._respawn()
                    self.state = 'playing'
        return True

    def key(self, key, repeat):
        if key in KEYMAP:
            if repeat or self.state in ('over', 'dying'):
                return
            if self.state == 'paused':
                self.state = 'playing'
                return
            if self.state == 'ready':
                self.state = 'playing'
            d = KEYMAP[key]
            self.face = d
            self._hop(d)
        elif key in ('SPACE', 'P'):
            if self.state == 'over':
                self.reset()
                self.state = 'playing'
            elif self.state == 'playing':
                self.state = 'paused'
            elif self.state in ('paused', 'ready'):
                self.state = 'playing'

    # ---- drawing
    def layout(self, view):
        self.cs = self.fit_cell(view, COLS, ROWS, max_cell=36)
        self.place(view, COLS * self.cs, ROWS * self.cs)

    def _r(self, c, x0, x1, y, h, col):
        """Rect from cell-x x0 to x1, clipped to the board."""
        lo, hi = max(x0, 0.0), min(x1, float(COLS))
        if hi > lo:
            c.rect(self.left + lo * self.cs, y, (hi - lo) * self.cs, h, col)

    def _inside(self, x0, x1):
        return x0 >= 0 and x1 <= COLS

    def _vehicle(self, c, lane, x, y):
        cs, L = self.cs, lane.length
        bh = cs * 0.66
        by = y + (cs - bh) / 2
        fwd = 1 if lane.speed > 0 else -1
        self._r(c, x + 0.06, x + L - 0.06, by, bh, lane.color)
        if lane.kind == 'truck':
            cab = 0.9
            x0, x1 = (x + L - 0.06 - cab, x + L - 0.06) if fwd > 0 else (x + 0.06, x + 0.06 + cab)
            self._r(c, x0, x1, by, bh, ov.shade(lane.color, 0.7))
        w = 0.24
        wx0 = x + L - 0.16 - w if fwd > 0 else x + 0.16
        if self._inside(wx0, wx0 + w):
            c.rect(self.left + wx0 * cs, by + bh * 0.15, w * cs, bh * 0.7, (0.75, 0.88, 1.0, 0.9))
        wheels = [0.3, L - 0.3] + ([L / 2] if L >= 3 else [])
        for wxo in wheels:
            wx = x + wxo
            if self._inside(wx - 0.15, wx + 0.15):
                for wy in (by - 0.05 * cs, by + bh - 0.05 * cs):
                    c.rect(self.left + (wx - 0.15) * cs, wy, 0.3 * cs, 0.1 * cs, ov.C_DARK)

    def _log(self, c, lane, x, y):
        cs, L = self.cs, lane.length
        bh = cs * 0.62
        by = y + (cs - bh) / 2
        self._r(c, x + 0.04, x + L - 0.04, by, bh, C_LOG)
        self._r(c, x + 0.04, x + 0.18, by, bh, ov.shade(C_LOG, 0.7))
        self._r(c, x + L - 0.18, x + L - 0.04, by, bh, ov.shade(C_LOG, 0.7))
        for k in (0.3, 0.7):
            self._r(c, x + 0.3, x + L - 0.3, by + bh * k, max(1.5, cs * 0.04), ov.shade(C_LOG, 0.75))

    def _turtles(self, c, lane, i, x, y):
        cs = self.cs
        d = self._dive(lane, i)
        a = 1.0 if d == 0 else 0.55 if d == 1 else 0.18
        bh = cs * 0.62
        by = y + (cs - bh) / 2
        for k in range(int(lane.length)):
            x0 = x + k
            self._r(c, x0 + 0.08, x0 + 0.92, by, bh, ov.alpha(C_TURTLE, a))
            self._r(c, x0 + 0.25, x0 + 0.75, by + bh * 0.2, bh * 0.6, ov.alpha(ov.shade(C_TURTLE, 1.35), a))

    def _frog(self, c, cx, cy, face, dead=False):
        cs = self.cs
        dx, dy = DIRS[face]
        px, py = -dy, dx
        body = ov.shade(C_FROG, 0.6) if dead else C_FROG
        for sx in (-1, 1):
            for fwd in (-1, 1):
                c.circle(cx + px * sx * cs * 0.28 + dx * fwd * cs * 0.22,
                         cy + py * sx * cs * 0.28 + dy * fwd * cs * 0.22,
                         cs * 0.11, ov.shade(body, 0.8), 10)
        c.circle(cx, cy, cs * 0.30, body, 20)
        c.circle(cx - dx * cs * 0.05, cy - dy * cs * 0.05, cs * 0.17, ov.shade(body, 1.2), 14)
        if dead:
            for s in (-1, 1):
                ex, ey = cx + dx * cs * 0.16 + px * s * cs * 0.12, cy + dy * cs * 0.16 + py * s * cs * 0.12
                d = cs * 0.05
                c.line((ex - d, ey - d), (ex + d, ey + d), max(1.5, cs * 0.04), ov.C_DARK)
                c.line((ex - d, ey + d), (ex + d, ey - d), max(1.5, cs * 0.04), ov.C_DARK)
        else:
            for s in (-1, 1):
                ex, ey = cx + dx * cs * 0.16 + px * s * cs * 0.12, cy + dy * cs * 0.16 + py * s * cs * 0.12
                c.circle(ex, ey, cs * 0.075, ov.C_WHITE, 10)
                c.circle(ex + dx * cs * 0.02, ey + dy * cs * 0.02, cs * 0.035, ov.C_DARK, 8)

    def draw(self, c):
        u, cs = self.u, self.cs
        L = self.left
        bottom = self.top - ROWS * cs
        self.draw_frame(c)

        if self.state == 'dying':
            main = {'car': "Splat!", 'water': "Splash!", 'time': "Out of time!",
                    'hedge': "Hedge! Enter a bay", 'taken': "Bay already used!"}.get(self.cause, "Ouch!")
            col = ov.C_BAD
        else:
            main, col = "FROGGER", ov.C_WHITE
        self.draw_header(c, main, "Score %d (Hi %d)   Lives %d   Level %d   Time %s   Best %s" % (
            self.score, self.best, max(0, self.lives), self.level,
            self._fmt(self.level_time), self._fmt(self._best_time(self.level))), col)

        # time bar
        ratio = max(0.0, min(1.0, self.clock / TIME_LIMIT))
        c.rect(L, self.top + 3 * u, COLS * cs, 5 * u, (0, 0, 0, 0.45))
        c.rect(L, self.top + 3 * u, COLS * cs * ratio, 5 * u, (1 - ratio, 0.35 + 0.55 * ratio, 0.25, 1.0))

        # terrain
        for r in range(ROWS):
            y = bottom + r * cs
            col = (C_GRASS if r in (0, MEDIAN) else C_ROAD if r < MEDIAN
                   else C_HEDGE if r == GOAL else C_WATER)
            c.rect(L, y, COLS * cs, cs, col)
        for r in range(2, 6):                                  # lane markings
            y = bottom + r * cs
            for k in range(COLS):
                c.rect(L + k * cs + cs * 0.15, y - 1.5 * u, cs * 0.7, 3 * u, (0.8, 0.8, 0.6, 0.35))
        gy = bottom + GOAL * cs
        for k in range(COLS):                                   # deadly hedge: bushes
            if k in BAYS:
                continue
            c.circle(L + (k + 0.30) * cs, gy + cs * 0.36, cs * 0.24, ov.shade(C_HEDGE, 1.35), 10)
            c.circle(L + (k + 0.72) * cs, gy + cs * 0.62, cs * 0.24, ov.shade(C_HEDGE, 0.80), 10)
        c.rect(L, gy, COLS * cs, max(2.0, 2 * u), (0, 0, 0, 0.35))
        pulse = 0.5 + 0.5 * math.sin(self.t * 4.0)
        for idx, bc in enumerate(BAYS):                         # goal bays: glowing gold
            x, cxb, cyb = L + bc * cs, L + (bc + 0.5) * cs, gy + cs / 2
            if self.filled[idx]:
                c.rect(x + 1, gy + 1, cs - 2, cs - 2, C_BAY_DONE)
                self._frog(c, cxb, cyb, 'DOWN')
            else:
                c.rect(x + 1, gy + 1, cs - 2, cs - 2, C_BAY_FRAME)
                c.rect(x + 4, gy + 4, cs - 8, cs - 8, C_BAY_PAD)
                c.circle(cxb, cyb, cs * 0.26, ov.shade(C_BAY_PAD, 0.75), 16)
                c.ring(cxb, cyb, cs * (0.40 + 0.08 * pulse), max(1.5, cs * 0.05),
                       (1.0, 0.95, 0.5, 0.35 + 0.5 * pulse))

        # moving objects
        for lane in self.lanes:
            y = bottom + lane.row * cs
            for x, i in lane.objects():
                if x + lane.length <= 0 or x >= COLS:
                    continue
                if lane.kind in ('car', 'truck'):
                    self._vehicle(c, lane, x, y)
                elif lane.kind == 'log':
                    self._log(c, lane, x, y)
                else:
                    self._turtles(c, lane, i, x, y)

        # frog
        cx = L + max(0.0, min(float(COLS), self.fx)) * cs
        cy = bottom + self.row * cs + cs / 2
        self._frog(c, cx, cy, self.face, dead=self.state in ('dying', 'over'))

        bw, bh = COLS * cs, ROWS * cs
        if self.state == 'ready':
            self.banner(c, L, bottom, bw, bh, "Ready?", "Reach a glowing gold bay at the top - the hedge is deadly")
        elif self.state == 'paused':
            self.banner(c, L, bottom, bw, bh, "Paused", "Press SPACE or a direction to resume")
        elif self.state == 'over':
            self.banner(c, L, bottom, bw, bh, "Game over",
                        "Score %d  -  press SPACE to retry" % self.score)

        self.draw_hint(c, "Arrows/WASD hop   SPACE pause   R restart   ESC quit")


RUNNER = ov.Runner("frogger", GAME_NAME, Frogger, [
    "Arrows / WASD: hop",
    "Cross the road, ride logs & turtles",
    "Reach a glowing gold bay (not the hedge)",
    "Turtles dive!  Best time is saved per level",
    "SPACE or P: pause",
    "R: restart   ESC: quit",
    "Keep the mouse over the viewport",
])


def draw_ui(layout, context):
    RUNNER.draw_ui(layout)


def register():
    RUNNER.register()


def unregister():
    RUNNER.unregister()
