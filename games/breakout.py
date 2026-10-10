"""Brick Breaker - a breakout / arkanoid style game
(drawn as a GPU overlay in the 3D Viewport).

Move the paddle with the mouse (or A / D / arrow keys), launch the ball with a
click or SPACE and clear all the bricks. Silver bricks need two hits, gold
bricks are indestructible. Some bricks drop capsules:

    E  expand paddle      C  catch (ball sticks, release with click/SPACE)
    L  laser (click/SPACE fires)   D  split the ball in three
    S  slow ball          P  extra life

Best score and best level are kept in the record file.
"""

import math
import random

from . import _overlay as ov
from . import _record as _rec

GAME_NAME = "Brick Breaker"
GAME_ICON = 'MESH_GRID'

BEST = [0]                  # best score survives restarts and re-opening the game

FW, FH = 480, 560           # playfield size in design units (scaled to the viewport)
NCOLS = 11
BW, BH = 40, 16             # brick size
BP_X, BP_Y = 42, 18         # brick pitch
BX0 = (FW - (NCOLS * BP_X - 2)) / 2.0
BY0 = FH - 76               # bottom edge of the top brick row
PAD_Y, PAD_H = 40, 10
PAD_W, PAD_WIDE = 80.0, 124.0
BALL_R = 5.5
LIVES = 3
DROP = 0.16                 # chance that a destroyed brick drops a capsule

C_BG = (0.04, 0.05, 0.09, 1.0)
C_WALL = (0.35, 0.38, 0.43, 1.0)
BRICK_COL = {
    'r': (0.85, 0.18, 0.18, 1.0), 'o': (0.95, 0.55, 0.15, 1.0),
    'y': (0.95, 0.85, 0.20, 1.0), 'g': (0.25, 0.75, 0.30, 1.0),
    'c': (0.20, 0.75, 0.85, 1.0), 'b': (0.25, 0.40, 0.90, 1.0),
    'p': (0.70, 0.30, 0.80, 1.0), 'w': (0.90, 0.90, 0.92, 1.0),
}
POINTS = {'w': 50, 'o': 60, 'c': 70, 'g': 80, 'r': 90, 'b': 100, 'p': 110, 'y': 120}
C_SILVER = (0.78, 0.80, 0.84, 1.0)
C_GOLD_BRICK = (0.88, 0.70, 0.15, 1.0)

CAPS = {   # letter: (colour, weight)
    'E': ((0.25, 0.45, 0.95, 1.0), 22),
    'C': ((0.25, 0.75, 0.35, 1.0), 18),
    'L': ((0.90, 0.25, 0.25, 1.0), 18),
    'D': ((0.20, 0.75, 0.85, 1.0), 16),
    'S': ((0.95, 0.60, 0.15, 1.0), 16),
    'P': ((0.85, 0.85, 0.88, 1.0), 4),
}
MODE_COL = {None: (0.85, 0.20, 0.20, 1.0), 'expand': (0.25, 0.45, 0.95, 1.0),
            'catch': (0.25, 0.75, 0.35, 1.0), 'laser': (0.95, 0.60, 0.15, 1.0)}

# '.' empty, letters = coloured brick (1 hit), '*' silver (2+ hits), '#' gold (indestructible)
LEVELS = [
    ["rrrrrrrrrrr",
     "ooooooooooo",
     "yyyyyyyyyyy",
     "ggggggggggg",
     "ccccccccccc"],
    ["....yyy....",
     "...ooooo...",
     "..rrrrrrr..",
     ".ppppppppp.",
     "***********"],
    ["b.b.b.b.b.b",
     ".c.c.c.c.c.",
     "g.g.g.g.g.g",
     ".y.y.y.y.y.",
     "r.r.r.r.r.r",
     "#.#.#.#.#.#"],
    ["#.........#",
     "#rrrrrrrrr#",
     "#ooooooooo#",
     "#yyyyyyyyy#",
     "#ggggggggg#",
     "#*********#"],
    ["....***....",
     "...*ppp*...",
     "..*pbbbp*..",
     ".*pbcccbp*.",
     "*pbcgggcbp*",
     ".*pbcccbp*.",
     "..*pbbbp*..",
     "...*ppp*...",
     "....***...."],
    ["wwwwwwwwwww",
     "rrrrrrrrrrr",
     "#####.#####",
     "ooooooooooo",
     "yyyyyyyyyyy",
     "#####.#####",
     "ggggggggggg",
     "ccccccccccc"],
]


def _clamp(v, a, b):
    return a if v < a else b if v > b else v


class Ball:
    __slots__ = ('x', 'y', 'dx', 'dy', 'stuck', 'off')

    def __init__(self, x, y):
        self.x, self.y = x, y
        self.dx, self.dy = 0.0, 1.0     # unit direction
        self.stuck = True               # riding on the paddle
        self.off = 0.0                  # x offset from the paddle centre when stuck


class BrickBreaker(ov.BaseGame):
    keys = ('SPACE', 'RET', 'LEFT_ARROW', 'RIGHT_ARROW', 'A', 'D', 'P')

    def setup(self):
        self.k = 1.0
        self.base = 0.0
        self.score = 0
        self.level = 1
        self.saved = True
        rnd = random.Random(12345)              # fixed background stars
        self.stars = [(rnd.uniform(4, FW - 4), rnd.uniform(4, FH - 4), rnd.choice((1, 1, 2)))
                      for _ in range(45)]
        try:
            self.record_mgr = _rec.RecordManager(game_name="breakout")
            BEST[0] = max(BEST[0], int(self.record_mgr.get_records().get("highest_score", 0)))
        except Exception:
            self.record_mgr = None

    def reset(self):
        if self.score > 0 and not self.saved:
            self._flush(True)
        self.score = 0
        self.lives = LIVES
        self.saved = False
        self.px = FW / 2.0
        self.pw = PAD_W
        self.kdir = 0
        self.khold = 0.0
        self.timer = 0.0
        self.prev_state = 'ready'
        self._load_level(1)

    # ------------------------------------------------------------------ levels
    def _load_level(self, n):
        self.level = n
        pat = LEVELS[(n - 1) % len(LEVELS)]
        self.bricks = {}                        # (row, col) -> [hp, kind]
        for r, row in enumerate(pat):
            for c, ch in enumerate(row):
                if ch == '.':
                    continue
                if ch == '#':
                    self.bricks[(r, c)] = [999, '#']
                elif ch == '*':
                    self.bricks[(r, c)] = [2 + (n - 1) // 6, '*']
                else:
                    self.bricks[(r, c)] = [1, ch]
        self.remaining = sum(1 for b in self.bricks.values() if b[1] != '#')
        self.base_speed = min(430.0, 290.0 + 14.0 * (n - 1))
        self.speed = self.base_speed
        self.caps = []                          # falling capsules [x, y, letter]
        self.bullets = []                       # laser shots [x, y]
        self.mode = None                        # None / expand / catch / laser
        self.slow_t = 0.0
        self.fire_cd = 0.0
        self.balls = [self._stuck_ball()]
        self.state = 'ready'

    def _stuck_ball(self):
        return Ball(self.px, PAD_Y + PAD_H / 2 + BALL_R)

    def _flush(self, final):
        """Write best score / level (and the games counter when a run ends)."""
        BEST[0] = max(BEST[0], self.score)
        if final:
            self.saved = True
        if self.record_mgr is None:
            return
        try:
            rec = self.record_mgr._load_file()
            if self.score > rec.get("highest_score", 0):
                rec["highest_score"] = self.score
            if self.level > rec.get("best_level", 0):
                rec["best_level"] = self.level
            if final:
                rec["total_games_played"] = rec.get("total_games_played", 0) + 1
            self.record_mgr._save_file(rec)
        except Exception:
            pass

    def _level_clear(self):
        self.state = 'clear'
        self.timer = 2.2
        self.balls, self.caps, self.bullets = [], [], []
        self._add_score(500)
        self._flush(False)

    def _lose_life(self):
        self.lives -= 1
        self.caps, self.bullets = [], []
        self.mode = None
        self.slow_t = 0.0
        if self.lives <= 0:
            self.state = 'gameover'
            self.balls = []
            self._flush(True)
        else:
            self.speed = self.base_speed
            self.balls = [self._stuck_ball()]
            self.state = 'ready'

    def _add_score(self, pts):
        old = self.score
        self.score += pts
        BEST[0] = max(BEST[0], self.score)
        if self.score // 10000 > old // 10000 and self.lives < 9:
            self.lives += 1

    # ------------------------------------------------------------------ actions
    def _release(self):
        for b in self.balls:
            if b.stuck:
                ang = math.radians(_clamp(b.off / (self.pw / 2.0), -1, 1) * 55)
                if abs(ang) < 0.14:
                    ang = 0.14 if random.random() < 0.5 else -0.14
                b.dx, b.dy = math.sin(ang), math.cos(ang)
                b.stuck = False
        self.state = 'play'

    def _fire(self):
        if self.mode != 'laser' or self.fire_cd > 0 or len(self.bullets) >= 8:
            return
        y = PAD_Y + PAD_H
        self.bullets += [[self.px - self.pw * 0.3, y], [self.px + self.pw * 0.3, y]]
        self.fire_cd = 0.28

    def _action(self):
        if self.state == 'gameover':
            self.reset()
        elif self.state == 'paused':
            self.state = self.prev_state
        elif self.state == 'ready':
            self._release()
        elif self.state == 'play':
            if any(b.stuck for b in self.balls):
                self._release()
            else:
                self._fire()

    def _toggle_pause(self):
        if self.state in ('play', 'ready'):
            self.prev_state = self.state
            self.state = 'paused'
        elif self.state == 'paused':
            self.state = self.prev_state

    def _apply(self, letter):
        self._add_score(100)
        if letter in ('E', 'C', 'L'):
            self.mode = {'E': 'expand', 'C': 'catch', 'L': 'laser'}[letter]
        elif letter == 'S':
            self.slow_t = 12.0
        elif letter == 'P':
            self.lives = min(9, self.lives + 1)
        elif letter == 'D':
            moving = [b for b in self.balls if not b.stuck]
            if moving and len(self.balls) < 9:
                src = moving[0]
                for da in (-0.45, 0.45):
                    nb = Ball(src.x, src.y)
                    nb.stuck = False
                    a = math.atan2(src.dx, src.dy) + da
                    nb.dx, nb.dy = math.sin(a), math.cos(a)
                    self._fix(nb)
                    self.balls.append(nb)

    # ------------------------------------------------------------------ input
    def key(self, key, repeat):
        if key in ('LEFT_ARROW', 'A', 'RIGHT_ARROW', 'D'):
            d = -1 if key in ('LEFT_ARROW', 'A') else 1
            if self.state in ('play', 'ready'):
                if not repeat:                        # a tap moves the paddle right away
                    self.px = _clamp(self.px + d * 14, self.pw / 2, FW - self.pw / 2)
                self.kdir = d
                self.khold = 0.16                     # "held" for a short while
            return
        if repeat:
            return
        if key in ('SPACE', 'RET'):
            self._action()
        elif key == 'P':
            self._toggle_pause()

    def click(self, x, y, button):
        if button == 'LEFT':
            self._action()
        else:
            self._toggle_pause()

    def move(self, x, y):
        if x < -1e5 or self.state not in ('play', 'ready'):
            return False
        nx = _clamp((x - self.left) / self.k, self.pw / 2, FW - self.pw / 2)
        if nx != self.px:
            self.px = nx
            return True
        return False

    # ------------------------------------------------------------------ physics
    def _fix(self, b):
        """Keep the direction a unit vector and never (almost) horizontal."""
        if abs(b.dy) < 0.22:
            b.dy = 0.22 if b.dy >= 0 else -0.22
        n = math.hypot(b.dx, b.dy) or 1.0
        b.dx, b.dy = b.dx / n, b.dy / n

    def _walls(self, b):
        if b.x < BALL_R:
            b.x = BALL_R
            b.dx = abs(b.dx)
        elif b.x > FW - BALL_R:
            b.x = FW - BALL_R
            b.dx = -abs(b.dx)
        if b.y > FH - BALL_R:
            b.y = FH - BALL_R
            b.dy = -abs(b.dy)
            self._fix(b)

    def _paddle_hit(self, b):
        top = PAD_Y + PAD_H / 2
        if b.dy >= 0 or b.y - BALL_R > top or b.y < PAD_Y - PAD_H / 2:
            return
        if abs(b.x - self.px) > self.pw / 2 + BALL_R * 0.6:
            return
        off = _clamp((b.x - self.px) / (self.pw / 2.0), -1.0, 1.0)
        b.y = top + BALL_R
        if self.mode == 'catch':
            b.stuck = True
            b.off = b.x - self.px
            return
        ang = math.radians(off * 58)
        b.dx, b.dy = math.sin(ang), math.cos(ang)
        self._fix(b)

    def _damage(self, key):
        """Hit a brick once. Returns True if it was a real brick."""
        br = self.bricks.get(key)
        if br is None or br[1] == '#':
            return False
        br[0] -= 1
        if br[0] > 0:
            return True
        del self.bricks[key]
        self.remaining -= 1
        self._add_score(50 * min(self.level, 8) if br[1] == '*' else POINTS[br[1]])
        self.speed = min(500.0, self.speed + 1.0)
        if random.random() < DROP:
            letters = list(CAPS)
            letter = random.choices(letters, [CAPS[l][1] for l in letters])[0]
            r, c = key
            self.caps.append([BX0 + c * BP_X + BW / 2, BY0 - r * BP_Y + BH / 2, letter])
        return True

    def _ball_bricks(self, b):
        r = BALL_R
        c0 = int((b.x - r - BX0) // BP_X)
        c1 = int((b.x + r - BX0) // BP_X)
        r0 = int((BY0 + BH - (b.y + r)) // BP_Y)
        r1 = int((BY0 + BH - (b.y - r)) // BP_Y)
        flip_x = flip_y = 0
        pushed = False
        for row in range(max(0, r0), r1 + 1):
            for col in range(max(0, c0), min(NCOLS - 1, c1) + 1):
                if (row, col) not in self.bricks:
                    continue
                bx, by = BX0 + col * BP_X, BY0 - row * BP_Y
                px = _clamp(b.x, bx, bx + BW)
                py = _clamp(b.y, by, by + BH)
                dx, dy = b.x - px, b.y - py
                d2 = dx * dx + dy * dy
                if d2 > r * r:
                    continue
                if d2 < 1e-9:                         # centre inside the brick
                    ox = min(b.x - bx, bx + BW - b.x)
                    oy = min(b.y - by, by + BH - b.y)
                    horiz = ox < oy
                    sx = -1 if b.x - bx < bx + BW - b.x else 1
                    sy = -1 if b.y - by < by + BH - b.y else 1
                else:
                    horiz = abs(dx) > abs(dy)
                    sx = 1 if dx >= 0 else -1
                    sy = 1 if dy >= 0 else -1
                    if not pushed:                    # push the ball out of the brick
                        d = math.sqrt(d2)
                        b.x += dx / d * (r - d)
                        b.y += dy / d * (r - d)
                        pushed = True
                if horiz:
                    flip_x = sx
                else:
                    flip_y = sy
                self._damage((row, col))
        if flip_x:
            b.dx = abs(b.dx) * flip_x
        if flip_y:
            b.dy = abs(b.dy) * flip_y
        if flip_x or flip_y:
            self._fix(b)

    def _step_balls(self, dt):
        spd = min(520.0, self.speed) * (0.7 if self.slow_t > 0 else 1.0)
        n = max(1, int(math.ceil(spd * dt / 2.5)))
        h = dt / n
        for _ in range(n):
            for b in self.balls:
                if b.stuck:
                    continue
                b.x += b.dx * spd * h
                b.y += b.dy * spd * h
                self._walls(b)
                self._paddle_hit(b)
                self._ball_bricks(b)
        self.balls = [b for b in self.balls if b.stuck or b.y > -BALL_R * 2]

    def _step_bullets(self, dt):
        alive = []
        for s in self.bullets:
            s[1] += 520.0 * dt
            tip = s[1] + 5
            if tip > FH:
                continue
            col = int((s[0] - BX0) // BP_X)
            hit = False
            if 0 <= col < NCOLS and 0 <= s[0] - BX0 - col * BP_X <= BW:
                row = int((BY0 + BH - tip) // BP_Y)
                for rr in (row, row + 1):
                    br = self.bricks.get((rr, col))
                    if br is None or rr < 0:
                        continue
                    by = BY0 - rr * BP_Y
                    if by <= tip <= by + BH:
                        self._damage((rr, col))
                        hit = True
                        break
            if not hit:
                alive.append(s)
        self.bullets = alive

    def _step_caps(self, dt):
        alive = []
        for cp in self.caps:
            cp[1] -= 110.0 * dt
            if abs(cp[1] - PAD_Y) <= 11 and abs(cp[0] - self.px) <= self.pw / 2 + 14:
                self._apply(cp[2])
            elif cp[1] > -12:
                alive.append(cp)
        self.caps = alive

    def update(self, dt):
        st = self.state
        if st == 'clear':
            self.timer -= dt
            if self.timer <= 0:
                self._load_level(self.level + 1)
            return True
        if st not in ('play', 'ready'):
            return False
        if self.khold > 0:                            # keyboard paddle
            self.px += self.kdir * 620.0 * dt
            self.khold -= dt
        target = PAD_WIDE if self.mode == 'expand' else PAD_W
        self.pw += _clamp(target - self.pw, -240 * dt, 240 * dt)
        self.px = _clamp(self.px, self.pw / 2, FW - self.pw / 2)
        self.fire_cd = max(0.0, self.fire_cd - dt)
        self.slow_t = max(0.0, self.slow_t - dt)
        for b in self.balls:                          # balls riding on the paddle
            if b.stuck:
                b.x = _clamp(self.px + b.off, BALL_R, FW - BALL_R)
                b.y = PAD_Y + PAD_H / 2 + BALL_R
        if st == 'play':
            self._step_balls(dt)
            self._step_bullets(dt)
            self._step_caps(dt)
            if self.remaining <= 0:
                self._level_clear()
            elif not self.balls:
                self._lose_life()
        return True

    # ------------------------------------------------------------------ layout
    def layout(self, view):
        u = view.u
        k = min(1.3 * u, (view.w - 24 * u) / FW, (view.h - 112 * u) / FH)
        self.k = max(0.4 * u, k)
        self.place(view, FW * self.k, FH * self.k)
        self.base = self.top - FH * self.k

    # ------------------------------------------------------------------ drawing
    def _rr(self, c, x, y, w, h, ch, col):
        c.poly([(x + ch, y), (x + w - ch, y), (x + w, y + ch), (x + w, y + h - ch),
                (x + w - ch, y + h), (x + ch, y + h), (x, y + h - ch), (x, y + ch)], col)

    def draw(self, c):
        k, u = self.k, self.u
        L, B = self.left, self.base

        def fx(v):
            return L + v * k

        def fy(v):
            return B + v * k

        self.draw_frame(c)
        self.draw_header(c, "Brick Breaker",
                         "Score: %d   |   Level: %d   |   Best: %d" %
                         (self.score, self.level, BEST[0]), ov.C_GOLD)

        # --- field
        t = max(2.0, 3 * u)
        c.rect(L - t, B - t, FW * k + 2 * t, FH * k + t, C_WALL)
        c.rect(L, B, FW * k, FH * k, C_BG)
        for sx, sy, ss in self.stars:
            c.rect(fx(sx), fy(sy), ss * k, ss * k, (0.55, 0.60, 0.80, 0.55))

        # --- bricks
        bv = max(1.0, 1.6 * k)
        for (row, col), (hp, kind) in self.bricks.items():
            x, y = fx(BX0 + col * BP_X), fy(BY0 - row * BP_Y)
            if kind == '#':
                face = C_GOLD_BRICK
            elif kind == '*':
                face = C_SILVER if hp >= 2 else ov.shade(C_SILVER, 0.7)
            else:
                face = BRICK_COL[kind]
            c.bevel(x, y, BW * k, BH * k, bv, face)

        # --- capsules
        for cx, cy, letter in self.caps:
            w, h = 30 * k, 13 * k
            col = CAPS[letter][0]
            self._rr(c, fx(cx) - w / 2, fy(cy) - h / 2, w, h, h * 0.4, ov.shade(col, 0.55))
            self._rr(c, fx(cx) - w / 2 + 1.5 * k, fy(cy) - h / 2 + 1.5 * k,
                     w - 3 * k, h - 3 * k, h * 0.3, col)
            c.text(letter, fx(cx), fy(cy), 10 * k, ov.C_WHITE)

        # --- laser shots
        for sx, sy in self.bullets:
            c.rect(fx(sx) - 1.5 * k, fy(sy), 3 * k, 10 * k, (1.0, 0.85, 0.3, 1.0))

        # --- paddle
        if self.state != 'gameover':
            cap = MODE_COL[self.mode]
            pw, ph = self.pw, PAD_H
            body = ov.shade((0.62, 0.68, 0.78, 1.0), 1.0)
            c.bevel(fx(self.px - pw / 2 + 6), fy(PAD_Y - ph / 2), (pw - 12) * k, ph * k,
                    max(1.0, 1.5 * k), body)
            for sgn in (-1, 1):
                c.circle(fx(self.px + sgn * (pw / 2 - 5)), fy(PAD_Y), ph / 2 * k + 0.5, cap, 14)
            if self.mode == 'laser':
                for sgn in (-1, 1):
                    c.rect(fx(self.px + sgn * pw * 0.3) - 2 * k, fy(PAD_Y + ph / 2),
                           4 * k, 6 * k, (0.95, 0.85, 0.3, 1.0))

        # --- balls
        for b in self.balls:
            c.circle(fx(b.x) + 1.2 * k, fy(b.y) - 1.2 * k, BALL_R * k, (0, 0, 0, 0.35), 14)
            c.circle(fx(b.x), fy(b.y), BALL_R * k, (0.96, 0.97, 1.0, 1.0), 16)
            c.circle(fx(b.x) - 1.5 * k, fy(b.y) + 1.5 * k, BALL_R * 0.35 * k, (1, 1, 1, 1), 8)

        # --- lives
        for i in range(max(0, self.lives)):
            c.rect(fx(8 + i * 20), fy(8), 14 * k, 5 * k, (0.62, 0.68, 0.78, 1.0))
            c.rect(fx(8 + i * 20), fy(8), 3 * k, 5 * k, MODE_COL[None])
            c.rect(fx(8 + i * 20 + 11), fy(8), 3 * k, 5 * k, MODE_COL[None])

        # --- status / messages
        tags = []
        if self.mode:
            tags.append(self.mode.upper())
        if self.slow_t > 0:
            tags.append("SLOW")
        if tags:
            c.text("  ".join(tags), fx(FW - 70), fy(10), 11 * k, ov.C_GOLD)
        if self.state == 'ready':
            c.text("Click or SPACE to launch", fx(FW / 2), fy(110), 14 * k, ov.C_TEXT)
        bx, by, bw, bh = L, B + FH * k / 2 - 60 * k, FW * k, 120 * k
        if self.state == 'paused':
            self.banner(c, bx, by, bw, bh, "Paused", "Press P or click to continue")
        elif self.state == 'clear':
            self.banner(c, bx, by, bw, bh, "Level %d cleared!" % self.level,
                        "+500 bonus")
        elif self.state == 'gameover':
            self.banner(c, bx, by, bw, bh, "Game over",
                        "Score %d   -   click or press R to play again" % self.score)

        self.draw_hint(c, "Mouse / A D move   Click / SPACE launch & fire   "
                          "P pause   R restart   ESC quit")


RUNNER = ov.Runner("breakout", GAME_NAME, BrickBreaker, [
    "Mouse (or A / D, arrows): move the paddle",
    "Click / SPACE: launch the ball, fire the laser",
    "Capsules: E expand  C catch  L laser",
    "              D split  S slow  P extra life",
    "P: pause   R: restart   ESC: quit",
])


def draw_ui(layout, context):
    RUNNER.draw_ui(layout)


def register():
    RUNNER.register()


def unregister():
    RUNNER.unregister()
