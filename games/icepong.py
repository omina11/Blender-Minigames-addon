"""Ice Pong - air-hockey style pong on a slippery rink (GPU overlay).

You can move FREELY in your half (forwards, backwards, up and down) but the
paddle slides on the ice: it accelerates and brakes slowly. The puck changes
speed with every hit: it depends on how fast your paddle is moving, where on the
paddle the puck lands and whether you SMASH (click / SPACE, short cooldown) just
before contact. First to 7 goals wins.

Move the mouse to steer the paddle (or tap W A S D to push it). Records per
difficulty (icepong_easy / medium / hard): best score and fastest win.
"""

import math
import random
import traceback

from . import _overlay as ov
from . import _record as _rec

GAME_NAME = "Ice Pong"
GAME_ICON = 'MOD_FLUIDSIM'

W, H = 640.0, 400.0
MID = W / 2
GOAL_Y0, GOAL_Y1 = 125.0, 275.0           # goal mouth (y range)
PR, BR = 22.0, 10.0                         # paddle / puck radius
WIN_GOALS = 7
MAX_PUCK = 1100.0
MIN_HIT = 210.0
ICE_SLIDE = 0.25                            # 0 = fully responsive mouse, 1 = old slippery feel

DIFFS = [
    dict(name="Easy", info="Slow, sloppy rival - learn how the ice feels",
         vmax=300.0, acc=900.0, react=0.34, noise=30.0, smash=0.0, predict=False, mult=1.0,
         col=(0.95, 0.40, 0.40)),
    dict(name="Medium", info="Reads the rebounds and hits back hard",
         vmax=430.0, acc=1350.0, react=0.17, noise=12.0, smash=0.25, predict=True, mult=1.5,
         col=(0.80, 0.50, 1.00)),
    dict(name="Hard", info="Lightning reflexes, smashes and aims at the corners",
         vmax=570.0, acc=1800.0, react=0.05, noise=3.0, smash=0.6, predict=True, mult=2.0,
         col=(1.00, 0.78, 0.20)),
]
DIGITS = {'ONE': 0, 'TWO': 1, 'THREE': 2, 'NUMPAD_1': 0, 'NUMPAD_2': 1, 'NUMPAD_3': 2}

C_ICE_A = (0.80, 0.92, 0.98, 1.0)
C_ICE_B = (0.74, 0.88, 0.96, 1.0)
C_RED = (0.88, 0.20, 0.25, 1.0)
C_BLUE = (0.22, 0.45, 0.88, 1.0)
P1_COL = (0.28, 0.62, 1.00, 1.0)


def _fmt(t):
    return "--" if t is None else "%d:%02d" % divmod(int(round(t)), 60)


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


class Paddle:
    def __init__(self, x, y, col, vmax, acc, left):
        self.x, self.y, self.vx, self.vy = x, y, 0.0, 0.0
        self.col = col
        self.vmax, self.acc = vmax, acc
        self.left = left
        self.tx, self.ty = x, y
        self.smash_t = 0.0
        self.cool = 0.0
        self.hit_t = 0.0

    def bounds(self):
        if self.left:
            return PR + 12.0, MID - PR - 3.0, PR + 12.0, H - PR - 12.0
        return MID + PR + 3.0, W - PR - 12.0, PR + 12.0, H - PR - 12.0


class IcePong(ov.BaseGame):
    keys = ('W', 'A', 'S', 'D', 'SPACE', 'M', 'LEFT_ARROW', 'RIGHT_ARROW', 'UP_ARROW', 'DOWN_ARROW',
            'ONE', 'TWO', 'THREE', 'NUMPAD_1', 'NUMPAD_2', 'NUMPAD_3')

    # ------------------------------------------------------------------ setup
    def setup(self):
        self.diff = 1
        self.menu = True
        self.menu_hover = -1
        self.record_mgrs = {}
        self.best_cache = {}
        self.sc, self.fx0, self.fy0 = 1.0, 0.0, 0.0
        self.time = 0.0
        self.mouse = None
        rng = random.Random(3)
        self.scratch = [(rng.uniform(20, W - 20), rng.uniform(15, H - 15), rng.uniform(10, 46), rng.uniform(0, 6.28))
                        for _ in range(55)]
        self.crowd = [(x, rng.uniform(8, 17), rng.random()) for x in range(0, 660, 11)]

    def reset(self):
        self.cfg = DIFFS[self.diff]
        self.pl = Paddle(120.0, H / 2, P1_COL, 640.0, 1500.0, True)
        c = self.cfg
        self.cpu = Paddle(W - 120.0, H / 2, tuple(c['col']) + (1.0,), c['vmax'], c['acc'], False)
        self.ball = [MID, H / 2, 0.0, 0.0]
        self.score = [0, 0]
        self.phase = 'intro'               # intro | play | goal | over
        self.timer = 2.6
        self.match_t = 0.0
        self.top_speed = 0.0
        self.points = 0
        self.rally = 0
        self.sparks = []
        self.trail = []
        self.shards = []
        self.shake = 0.0
        self.freeze = 0.0
        self.flash = 0.0
        self.goal_by = None
        self.serve_to = 0 if random.random() < 0.5 else 1
        self.ai_t = 0.0
        self.ai_goal = (self.cpu.x, self.cpu.y)
        self.still_t = 0.0
        self.recorded = False
        self.final = {}
        self.new_best_score = self.new_best_time = False
        self.msg, self.msg_t = "", 0.0
        self.last_hit_speed = 0.0

    def _serve(self):
        side = -1 if self.serve_to == 0 else 1
        ang = random.uniform(-0.45, 0.45)
        self.ball = [MID, H / 2 + random.uniform(-60, 60), side * math.cos(ang) * 250.0, math.sin(ang) * 250.0]
        self.phase = 'play'
        self.rally = 0
        self.trail = []
        self.still_t = 0.0

    # ---------------------------------------------------------------- records
    def _mgr(self, d):
        if d not in self.record_mgrs:
            try:
                self.record_mgrs[d] = _rec.RecordManager(game_name="icepong_" + DIFFS[d]['name'].lower())
            except Exception:
                self.record_mgrs[d] = None
        return self.record_mgrs[d]

    def _best(self, d):
        if d not in self.best_cache:
            m = self._mgr(d)
            try:
                r = m.get_records() if m else {}
                self.best_cache[d] = (int(r.get("highest_score", 0) or 0), r.get("best_time"))
            except Exception:
                self.best_cache[d] = (0, None)
        return self.best_cache[d]

    def _finish_match(self):
        won = self.score[0] > self.score[1]
        if won:
            pts = self.points + int(500 * self.cfg['mult']) + (self.score[0] - self.score[1]) * 60
            self.points = pts
        bs, bt = self._best(self.diff)
        self.final = dict(won=won, score=self.points)
        if won:
            self.new_best_score = self.points > bs
            self.new_best_time = bt is None or self.match_t < bt
        if self.recorded:
            return
        self.recorded = True
        m = self._mgr(self.diff)
        if not m:
            return
        try:
            if won:
                m.add_win_record(score=int(self.points), best_time=self.match_t)
            else:
                m.add_lose_record()
            self.best_cache.pop(self.diff, None)
        except Exception:
            traceback.print_exc()

    # ---------------------------------------------------------------- physics
    def _move_paddle(self, p, tx, ty, dt, vmax=None, acc=None, gain=7.0, brake_mul=0.8):
        vmax = vmax or p.vmax
        acc = acc or p.acc
        x0, x1, y0, y1 = p.bounds()
        tx, ty = _clamp(tx, x0, x1), _clamp(ty, y0, y1)
        dx, dy = tx - p.x, ty - p.y
        d = math.hypot(dx, dy)
        spd = min(vmax, d * gain)
        dvx, dvy = (dx / d * spd, dy / d * spd) if d > 1e-6 else (0.0, 0.0)
        # ice: slow acceleration, even slower braking
        ex, ey = dvx - p.vx, dvy - p.vy
        e = math.hypot(ex, ey)
        brake = (p.vx * ex + p.vy * ey) < 0
        a = acc * (brake_mul if brake else 1.0) * dt
        if e > a:
            ex, ey = ex / e * a, ey / e * a
        p.vx += ex
        p.vy += ey
        p.vx *= math.exp(-0.25 * dt)
        p.vy *= math.exp(-0.25 * dt)
        p.x += p.vx * dt
        p.y += p.vy * dt
        if p.x < x0:
            p.x, p.vx = x0, abs(p.vx) * 0.3
        elif p.x > x1:
            p.x, p.vx = x1, -abs(p.vx) * 0.3
        if p.y < y0:
            p.y, p.vy = y0, abs(p.vy) * 0.3
        elif p.y > y1:
            p.y, p.vy = y1, -abs(p.vy) * 0.3

    def _spark(self, x, y, col, n=7, big=1.0):
        for _ in range(n):
            a = random.uniform(0, 6.28)
            s = random.uniform(60, 220) * big
            self.shards.append([x, y, math.cos(a) * s, math.sin(a) * s, 0.0, col])
        self.sparks.append([x, y, 0.0, col, big])

    def _paddle_hit(self, p, owner_is_player):
        b = self.ball
        dx, dy = b[0] - p.x, b[1] - p.y
        d = math.hypot(dx, dy)
        if d >= PR + BR or d < 1e-6:
            return False
        nx, ny = dx / d, dy / d
        pen = PR + BR - d
        b[0] += nx * pen
        b[1] += ny * pen
        rvx, rvy = b[2] - p.vx, b[3] - p.vy
        vn = rvx * nx + rvy * ny
        if vn >= 0:
            return False
        e = 0.92
        rvx -= (1 + e) * vn * nx
        rvy -= (1 + e) * vn * ny
        b[2], b[3] = rvx + p.vx, rvy + p.vy
        sp = math.hypot(b[2], b[3])
        # centre hits drive the puck harder than glancing ones
        centre = abs(vn) / (math.hypot(rvx, rvy) + abs(vn) + 1e-6)
        want = max(MIN_HIT, sp * (0.9 + 0.35 * centre))
        smash = p.smash_t > 0
        if smash:
            want = want * 1.5 + 140.0
            p.smash_t = 0.0
            self.shake = max(self.shake, 6.0)
            self.freeze = 0.05
        want = min(MAX_PUCK, want)
        if sp > 1e-6:
            b[2], b[3] = b[2] / sp * want, b[3] / sp * want
        else:
            b[2], b[3] = nx * want, ny * want
        p.hit_t = 0.18
        self.last_hit_speed = want
        self.top_speed = max(self.top_speed, want)
        self.rally += 1
        self._spark(b[0] - nx * BR, b[1] - ny * BR, (1.0, 0.95, 0.6, 1.0) if smash else (0.9, 0.97, 1.0, 1.0),
                    10 if smash else 5, 1.4 if smash else 0.8)
        if owner_is_player:
            self.points += int(want / 40) + (40 if smash else 0)
        return True

    def _step_ball(self, dt):
        b = self.ball
        n = max(1, int(math.hypot(b[2], b[3]) * dt / 5.0) + 1)
        h = dt / n
        for _ in range(n):
            b[0] += b[2] * h
            b[1] += b[3] * h
            # top / bottom walls
            if b[1] < BR:
                b[1], b[3] = BR, abs(b[3]) * 0.96
                self._spark(b[0], 2, (0.9, 0.97, 1.0, 1.0), 2, 0.5)
            elif b[1] > H - BR:
                b[1], b[3] = H - BR, -abs(b[3]) * 0.96
                self._spark(b[0], H - 2, (0.9, 0.97, 1.0, 1.0), 2, 0.5)
            # end walls with a goal mouth
            in_mouth = GOAL_Y0 + BR * 0.4 < b[1] < GOAL_Y1 - BR * 0.4
            if b[0] < BR and not in_mouth:
                b[0], b[2] = BR, abs(b[2]) * 0.96
            elif b[0] > W - BR and not in_mouth:
                b[0], b[2] = W - BR, -abs(b[2]) * 0.96
            for px in (0.0, W):                                # goal posts
                for py in (GOAL_Y0, GOAL_Y1):
                    dx, dy = b[0] - px, b[1] - py
                    d = math.hypot(dx, dy)
                    if d < BR + 4.0 and d > 1e-6:
                        nx, ny = dx / d, dy / d
                        b[0] += nx * (BR + 4.0 - d)
                        b[1] += ny * (BR + 4.0 - d)
                        vn = b[2] * nx + b[3] * ny
                        if vn < 0:
                            b[2] -= 1.9 * vn * nx
                            b[3] -= 1.9 * vn * ny
            self._paddle_hit(self.pl, True)
            self._paddle_hit(self.cpu, False)
            if b[0] < -BR * 1.4:
                self._goal(1)
                return
            if b[0] > W + BR * 1.4:
                self._goal(0)
                return
        sp = math.hypot(b[2], b[3])
        k = math.exp(-0.28 * dt)                                # very little friction on ice
        b[2] *= k
        b[3] *= k
        sp = math.hypot(b[2], b[3])
        if sp < 40.0 and self.phase == 'play':
            self.still_t += dt
            if self.still_t > 2.2:                              # nudge a stuck puck
                a = random.uniform(0, 6.28)
                b[2], b[3] = math.cos(a) * 160.0, math.sin(a) * 160.0
                self.still_t = 0.0
        else:
            self.still_t = 0.0
        self.trail.append((b[0], b[1], sp))
        if len(self.trail) > 18:
            self.trail.pop(0)

    def _goal(self, scorer):
        self.score[scorer] += 1
        self.phase, self.timer = 'goal', 1.5
        self.goal_by = scorer
        self.flash = 0.5
        self.shake = 9.0
        b = self.ball
        self._spark(0.0 if scorer == 1 else W, b[1], (1.0, 0.8, 0.3, 1.0), 16, 2.0)
        if scorer == 0:
            self.points += 100 + int(self.rally) * 5
            self.msg, self.msg_t = "GOAL!  +%d" % (100 + self.rally * 5), 1.4
        else:
            self.points = max(0, self.points - 30)
            self.msg, self.msg_t = "Goal for the rival", 1.4
        self.serve_to = 1 - scorer
        b[2] = b[3] = 0.0
        if max(self.score) >= WIN_GOALS:
            self.phase, self.timer = 'goal', 1.8

    # -------------------------------------------------------------------- AI
    def _predict(self, x_line):
        """Where the puck will cross x = x_line (walls included), or None if it moves away."""
        x, y, vx, vy = self.ball
        if abs(vx) < 20 or (x_line - x) * vx <= 0:
            return None
        t = (x_line - x) / vx
        yy = y + vy * t
        span = H - 2 * BR
        yy = (yy - BR) % (2 * span)
        if yy > span:
            yy = 2 * span - yy
        return yy + BR

    def _ai(self, dt):
        c, p, b = self.cfg, self.cpu, self.ball
        self.ai_t -= dt
        if self.ai_t <= 0:
            self.ai_t = c['react'] * random.uniform(0.8, 1.2)
            bx, by, bvx, bvy = b
            speed = math.hypot(bvx, bvy)
            def_x = W - 58.0
            in_half = bx > MID - 10
            if in_half and speed < 420 and bx < p.x + 18 and bx > MID + 6:
                ty0 = H / 2 + random.choice((-1, 1)) * (random.uniform(30, 62) if c['predict'] and c['smash'] > 0.4 else random.uniform(0, 40))
                dx, dy = 0.0 - bx, ty0 - by
                d = math.hypot(dx, dy)
                ux, uy = dx / d, dy / d
                pre = (bx - ux * (PR + BR + 34), by - uy * (PR + BR + 34))
                if math.hypot(p.x - pre[0], p.y - pre[1]) > 30 and p.x > bx - 5:
                    tgt = pre
                else:
                    tgt = (bx - ux * (PR + BR - 8), by - uy * (PR + BR - 8))
                    if random.random() < c['smash'] and p.cool <= 0:
                        p.smash_t, p.cool = 0.3, 1.2
            else:
                py = self._predict(def_x) if c['predict'] else None
                if py is None:
                    py = by if bvx > 0 or in_half else H / 2
                tgt = (def_x - (30 if (bvx > 0 and c['predict']) else 0), py)
            tgt = (tgt[0] + random.gauss(0, c['noise']), tgt[1] + random.gauss(0, c['noise']))
            self.ai_goal = tgt
        self._move_paddle(p, self.ai_goal[0], self.ai_goal[1], dt)

    # ----------------------------------------------------------------- update
    def update(self, dt):
        self.time += dt
        if self.menu:
            return True
        dt = min(dt, 0.05)
        for s in self.sparks:
            s[2] += dt
        self.sparks = [s for s in self.sparks if s[2] < 0.3]
        for sh in self.shards:
            sh[0] += sh[2] * dt
            sh[1] += sh[3] * dt
            sh[2] *= 0.94
            sh[3] *= 0.94
            sh[4] += dt
        self.shards = [s for s in self.shards if s[4] < 0.55]
        self.shake = max(0.0, self.shake - 26.0 * dt)
        self.flash = max(0.0, self.flash - dt)
        self.msg_t = max(0.0, self.msg_t - dt)
        for p in (self.pl, self.cpu):
            p.smash_t = max(0.0, p.smash_t - dt)
            p.cool = max(0.0, p.cool - dt)
            p.hit_t = max(0.0, p.hit_t - dt)
        if self.freeze > 0:
            self.freeze -= dt
            return True
        if self.phase == 'intro':
            self.timer -= dt
            self._player_step(dt)
            self._move_paddle(self.cpu, W - 120.0, H / 2, dt)
            if self.timer <= 0:
                self._serve()
            return True
        if self.phase in ('play', 'goal'):
            self._player_step(dt)
            if self.phase == 'play':
                self.match_t += dt
                self._ai(dt)
                self._step_ball(dt)
            else:
                self._move_paddle(self.cpu, W - 120.0, H / 2, dt, acc=900.0)
                self.timer -= dt
                if self.timer <= 0:
                    if max(self.score) >= WIN_GOALS:
                        self.phase = 'over'
                        self._finish_match()
                    else:
                        self._serve()
            return True
        return True

    def _player_step(self, dt):
        p = self.pl
        if self.mouse is not None:
            # responsive mouse control: the paddle chases the cursor quickly and
            # brakes just as fast (only a tiny hint of ice inertia is left).
            # Raise ICE_SLIDE towards 1.0 for a slippier feel, lower it for even more precision.
            tx, ty = self.mouse
            k = ICE_SLIDE
            self._move_paddle(p, tx, ty, dt, vmax=1200.0, acc=3000.0 + 9000.0 * (1.0 - k),
                              gain=10.0 + 10.0 * (1.0 - k), brake_mul=1.0)
        else:
            # WASD nudges keep the original slippery behaviour
            self._move_paddle(p, p.x, p.y, dt, 700.0)

    # ------------------------------------------------------------------ input
    def _card(self, i):
        return 110.0, 262.0 - i * 92.0, 420.0, 80.0

    def _logical(self, x, y):
        return (x - self.fx0) / self.sc, (y - self.fy0) / self.sc

    def _card_idx(self, x, y):
        lx, ly = self._logical(x, y)
        for i in range(len(DIFFS)):
            cx, cy, w, h = self._card(i)
            if cx <= lx <= cx + w and cy <= ly <= cy + h:
                return i
        return -1

    def move(self, x, y):
        if x < -1e5:
            return False
        if self.menu:
            h = self._card_idx(x, y)
            changed = h != self.menu_hover
            self.menu_hover = h
            return changed
        self.mouse = self._logical(x, y)
        return False

    def _smash(self):
        p = self.pl
        if self.phase in ('play', 'goal', 'intro') and p.cool <= 0:
            p.smash_t, p.cool = 0.28, 0.9

    def click(self, x, y, button):
        if self.menu:
            if button == 'LEFT':
                i = self._card_idx(x, y)
                if i >= 0:
                    self._start(i)
            return
        if self.phase == 'over' and button == 'LEFT':
            self.reset()
            return
        self.mouse = self._logical(x, y)
        self._smash()

    def _start(self, i):
        self.diff = i
        self.menu = False
        self.reset()

    def key(self, key, repeat):
        if self.menu:
            i = DIGITS.get(key)
            if i is not None:
                self._start(i)
            return
        if key == 'M':
            self.menu, self.menu_hover = True, -1
            return
        if self.phase == 'over':
            if key == 'SPACE':
                self.reset()
            return
        p = self.pl
        if key == 'SPACE':
            self._smash()
        elif key in ('A', 'LEFT_ARROW'):
            p.vx -= 230.0
            self.mouse = None
        elif key in ('D', 'RIGHT_ARROW'):
            p.vx += 230.0
            self.mouse = None
        elif key in ('W', 'UP_ARROW'):
            p.vy += 230.0
            self.mouse = None
        elif key in ('S', 'DOWN_ARROW'):
            p.vy -= 230.0
            self.mouse = None

    # ----------------------------------------------------------------- layout
    def layout(self, view):
        cs = self.fit_cell(view, 8, 5, max_cell=100, min_cell=36)
        self.place(view, 8 * cs, 5 * cs)
        self.sc = cs / 80.0
        self.fx0 = self.left
        self.fy0 = self.top - 5 * cs

    # ---------------------------------------------------------------- drawing
    def _X(self, v):
        return self.fx0 + (v + self._sx) * self.sc

    def _Y(self, v):
        return self.fy0 + (v + self._sy) * self.sc

    def _rect(self, c, x, y, w, h, col):
        c.rect(self._X(x), self._Y(y), w * self.sc, h * self.sc, col)

    def _circ(self, c, x, y, r, col, seg=20):
        c.circle(self._X(x), self._Y(y), r * self.sc, col, seg)

    def _ring(self, c, x, y, r, th, col, seg=28):
        c.ring(self._X(x), self._Y(y), r * self.sc, max(1.0, th * self.sc), col, seg)

    def _line(self, c, p, q, w, col):
        c.line((self._X(p[0]), self._Y(p[1])), (self._X(q[0]), self._Y(q[1])), max(1.0, w * self.sc), col)

    def _text(self, c, s, x, y, size, col=ov.C_WHITE, align='center'):
        c.text(s, self._X(x), self._Y(y), size * self.sc, col, align)

    def _rink(self, c):
        # crowd behind the boards
        self._rect(c, -30, H, W + 60, 60, (0.05, 0.06, 0.12, 1))
        # ice
        for k in range(10):
            self._rect(c, 0, k * H / 10, W, H / 10 + 0.6, C_ICE_A if k % 2 == 0 else C_ICE_B)
        for x, y, ln, a in self.scratch:
            self._line(c, (x, y), (x + math.cos(a) * ln, y + math.sin(a) * ln * 0.4), 0.9, (1, 1, 1, 0.42))
        # glow from light spots
        for k in range(5):
            self._circ(c, MID, H / 2, 260 - k * 40, (1, 1, 1, 0.025), 40)
        # markings
        self._rect(c, MID - 2.5, 0, 5, H, (C_RED[0], C_RED[1], C_RED[2], 0.85))
        self._ring(c, MID, H / 2, 58, 4, (C_BLUE[0], C_BLUE[1], C_BLUE[2], 0.8), 40)
        self._circ(c, MID, H / 2, 7, C_BLUE, 16)
        for gx, sgn in ((0.0, 1), (W, -1)):
            for k in range(14):
                a0 = -math.pi / 2 + k * math.pi / 14
                a1 = a0 + math.pi / 14
                col = (C_BLUE[0], C_BLUE[1], C_BLUE[2], 0.55)
                r = 78
                p0 = (gx + sgn * math.cos(a0) * r, H / 2 + math.sin(a0) * r)
                p1 = (gx + sgn * math.cos(a1) * r, H / 2 + math.sin(a1) * r)
                self._line(c, p0, p1, 3.0, col)
            self._rect(c, gx - (14 if sgn > 0 else 0) - (0 if sgn > 0 else 0), GOAL_Y0, 14 if sgn > 0 else 0, GOAL_Y1 - GOAL_Y0, (0, 0, 0, 0))
        # goals (dark nets)
        for gx, sgn in ((-34.0, 1), (W, -1)):
            self._rect(c, gx, GOAL_Y0, 34, GOAL_Y1 - GOAL_Y0, (0.12, 0.13, 0.20, 1))
            for k in range(1, 8):
                self._rect(c, gx, GOAL_Y0 + k * (GOAL_Y1 - GOAL_Y0) / 8, 34, 1, (1, 1, 1, 0.18))
            for k in range(1, 4):
                self._rect(c, gx + k * 8.5, GOAL_Y0, 1, GOAL_Y1 - GOAL_Y0, (1, 1, 1, 0.18))
        # boards
        bt = 7.0
        boards = (0.18, 0.20, 0.30, 1.0)
        self._rect(c, 0, -bt, W, bt, boards)
        self._rect(c, 0, H, W, bt, boards)
        for (y0, y1) in ((0, GOAL_Y0), (GOAL_Y1, H)):
            self._rect(c, -bt, y0, bt, y1 - y0, boards)
            self._rect(c, W, y0, bt, y1 - y0, boards)
        self._rect(c, 0, -bt, W, 2, (1, 1, 1, 0.55))
        self._rect(c, 0, H + bt - 2, W, 2, (1, 1, 1, 0.55))
        for gx in (0.0, W):
            for gy in (GOAL_Y0, GOAL_Y1):
                self._circ(c, gx, gy, 4.5, (0.95, 0.30, 0.30, 1), 10)

    def _paddle(self, c, p):
        x, y = p.x, p.y
        sp = math.hypot(p.vx, p.vy)
        # ice shavings / motion streak
        if sp > 120:
            for k in range(1, 5):
                a = 0.16 - k * 0.03
                self._circ(c, x - p.vx * 0.012 * k, y - p.vy * 0.012 * k, PR * (1 - k * 0.12), (1, 1, 1, a), 18)
        self._circ(c, x + 3, y - 4, PR + 1, (0, 0, 0, 0.28), 22)
        col = p.col
        self._circ(c, x, y, PR, ov.shade(col, 0.55), 26)
        self._circ(c, x, y, PR - 3.2, col, 26)
        self._ring(c, x, y, PR * 0.62, 3.0, ov.shade(col, 1.25), 22)
        self._circ(c, x, y, PR * 0.36, ov.shade(col, 0.72), 18)
        self._circ(c, x - 4, y + 5, 5.2, (1, 1, 1, 0.36), 10)
        if p.smash_t > 0:
            k = 0.6 + 0.4 * math.sin(self.time * 40)
            self._ring(c, x, y, PR + 4 + 2 * k, 3.5, (1.0, 0.85, 0.3, 0.9), 26)
            self._circ(c, x, y, PR + 6, (1.0, 0.8, 0.25, 0.12), 24)
        elif p.cool > 0 and p is self.pl:
            c.ring(self._X(x), self._Y(y), (PR + 4) * self.sc, 2.0 * self.sc, (1, 1, 1, 0.20), 20)
        if p.hit_t > 0:
            self._ring(c, x, y, PR + 3 + (0.18 - p.hit_t) * 80, 2.2, (1, 1, 1, p.hit_t / 0.18), 26)

    def _puck(self, c):
        b = self.ball
        for i, (x, y, sp) in enumerate(self.trail):
            a = (i + 1) / (len(self.trail) + 1)
            col = _heat(sp)
            self._circ(c, x, y, BR * (0.4 + 0.6 * a), (col[0], col[1], col[2], 0.35 * a), 10)
        sp = math.hypot(b[2], b[3])
        col = _heat(sp)
        self._circ(c, b[0] + 2.5, b[1] - 3, BR, (0, 0, 0, 0.32), 16)
        self._circ(c, b[0], b[1], BR + 2.4 + min(5.0, sp / 200), (col[0], col[1], col[2], 0.22), 20)
        self._circ(c, b[0], b[1], BR, (0.05, 0.05, 0.08, 1), 18)
        self._circ(c, b[0], b[1], BR - 2.2, col, 18)
        self._circ(c, b[0] - 2.4, b[1] + 2.8, 2.6, (1, 1, 1, 0.65), 8)

    def _hud(self, c):
        self._text(c, "%d" % self.score[0], MID - 54, 354, 46, (P1_COL[0], P1_COL[1], P1_COL[2], 0.78))
        self._text(c, "%d" % self.score[1], MID + 54, 354, 46, tuple(self.cfg['col']) + (0.78,))
        self._text(c, "KAI", MID - 54, 322, 11, (0.25, 0.3, 0.45, 1))
        self._text(c, "RIVAL", MID + 54, 322, 11, (0.25, 0.3, 0.45, 1))
        # puck speed meter
        b = self.ball
        sp = math.hypot(b[2], b[3])
        self._rect(c, MID - 90, 12, 180, 9, (0, 0, 0, 0.35))
        self._rect(c, MID - 89, 13, 178 * min(1.0, sp / MAX_PUCK), 7, _heat(sp))
        self._text(c, "PUCK  %d km/h" % int(sp * 0.11), MID, 32, 10, (0.20, 0.25, 0.40, 1))
        # smash meter
        p = self.pl
        k = 1.0 if p.cool <= 0 else 1.0 - p.cool / 0.9
        self._rect(c, 14, 12, 110, 9, (0, 0, 0, 0.35))
        self._rect(c, 15, 13, 108 * k, 7, (1.0, 0.8, 0.25, 1) if k >= 1 else (0.5, 0.55, 0.65, 1))
        self._text(c, "SMASH  (click / SPACE)", 14, 32, 9, (0.20, 0.25, 0.40, 1), 'left')
        self._text(c, "SCORE %d" % self.points, W - 14, 22, 12, (0.55, 0.40, 0.05, 1), 'right')

    def _menu(self, c):
        self._rect(c, 0, 0, W, H, (0.06, 0.09, 0.16, 1))
        for k in range(10):
            self._rect(c, 0, k * 42, W, 20, (1, 1, 1, 0.014))
        self._ring(c, MID, H / 2, 150, 3, (0.3, 0.6, 1.0, 0.25), 50)
        self._text(c, "ICE PONG", W / 2, 362, 42, (0.65 + 0.3 * math.sin(self.time * 2) ** 2, 0.88, 1.0, 1.0))
        self._text(c, "Slide, swing and smash - first to %d goals" % WIN_GOALS, W / 2, 330, 13, ov.C_TEXT)
        for i, d in enumerate(DIFFS):
            x, y, w, h = self._card(i)
            hov = i == self.menu_hover
            self._rect(c, x + 3, y - 4, w, h, (0, 0, 0, 0.4))
            self._rect(c, x, y, w, h, ov.C_CELL_HOVER if hov else ov.C_CELL)
            self._rect(c, x, y, 9, h, tuple(d['col']) + (1.0,))
            self._text(c, "%d  %s" % (i + 1, d['name']), x + 24, y + h - 24, 20, ov.C_WHITE, 'left')
            self._text(c, d['info'], x + 24, y + 22, 10.5, ov.C_TEXT, 'left')
            bs, bt = self._best(i)
            if bs:
                self._text(c, "Best score  %d" % bs, x + w - 14, y + h - 26, 13, ov.C_GOLD, 'right')
                self._text(c, "Fastest win  %s" % _fmt(bt), x + w - 14, y + 22, 12, ov.C_GOLD, 'right')
            else:
                self._text(c, "No win yet", x + w - 14, y + h - 26, 12, ov.C_TEXT, 'right')
        self._text(c, "Mouse: slide your paddle (forwards, backwards, sideways)   Click / SPACE: smash", W / 2, 34, 11, ov.C_TEXT)

    def draw(self, c):
        self._sx = self._sy = 0.0
        self.draw_frame(c)
        bs, bt = self._best(self.diff)
        if self.menu:
            self.draw_header(c, "ICE PONG", "Best score and fastest win saved for every difficulty", ov.C_GOLD)
            self._menu(c)
            self.draw_hint(c, "Click or press 1-3 to pick a difficulty   ESC quit")
            return
        self.draw_header(c, "ICE PONG - %s" % self.cfg['name'],
                         "Score %d   |   Best %d   |   Fastest win %s   |   Top puck %d km/h" % (
                             self.points, bs, _fmt(bt), int(self.top_speed * 0.11)), tuple(self.cfg['col']) + (1.0,))
        if self.shake > 0:
            self._sx = random.uniform(-1, 1) * self.shake
            self._sy = random.uniform(-1, 1) * self.shake * 0.6
        self._rink(c)
        for sh in self.shards:
            a = 1.0 - sh[4] / 0.55
            self._circ(c, sh[0], sh[1], 1.8, (sh[5][0], sh[5][1], sh[5][2], a), 6)
        self._paddle(c, self.cpu)
        self._paddle(c, self.pl)
        if self.phase != 'goal' or self.timer > 0.6:
            self._puck(c)
        for x, y, t, col, big in self.sparks:
            k = t / 0.3
            for i in range(8):
                a = i * math.pi / 4 + 0.2
                self._line(c, (x + math.cos(a) * (4 + 12 * k) * big, y + math.sin(a) * (4 + 12 * k) * big),
                           (x + math.cos(a) * (9 + 26 * k) * big, y + math.sin(a) * (9 + 26 * k) * big),
                           3.0 * (1 - k) + 0.6, (col[0], col[1], col[2], 1 - k))
        sx_, sy_ = self._sx, self._sy
        self._sx = self._sy = 0.0
        if self.flash > 0:
            self._rect(c, 0, 0, W, H, (1.0, 0.95, 0.7, self.flash * 0.5))
        self._hud(c)
        if self.phase == 'intro':
            n = int(math.ceil(self.timer - 0.3))
            self._text(c, str(n) if n > 0 else "FACE OFF!", W / 2, 200, 70 if n > 0 else 52, (0.15, 0.25, 0.55, 1))
        if self.msg_t > 0:
            self._text(c, self.msg, W / 2, 238, 22, ov.alpha((0.65, 0.4, 0.0, 1.0), min(1.0, self.msg_t * 2)))
        if self.phase == 'over':
            won = self.final.get('won')
            self._rect(c, 0, 0, W, H, (0, 0, 0, 0.62))
            self._text(c, "YOU WIN!" if won else "YOU LOSE", W / 2, 285, 50, ov.C_GOLD if won else ov.C_BAD)
            self._text(c, "%d - %d" % (self.score[0], self.score[1]), W / 2, 238, 22, ov.C_WHITE)
            self._text(c, "Score  %d%s" % (self.points, "   NEW BEST!" if (won and self.new_best_score) else ""), W / 2, 205, 20, ov.C_GOLD)
            if won:
                self._text(c, "Time  %s%s" % (_fmt(self.match_t), "   NEW BEST TIME!" if self.new_best_time else ""),
                           W / 2, 176, 13, ov.C_GOLD if self.new_best_time else ov.C_TEXT)
            self._text(c, "SPACE / click: rematch     M: difficulty", W / 2, 128, 12, ov.C_TEXT)
        self._sx, self._sy = sx_, sy_
        self.draw_hint(c, "Mouse slide   click / SPACE smash   WASD nudge   M menu   R restart")


def _heat(sp):
    k = min(1.0, sp / 800.0)
    if k < 0.5:
        t = k / 0.5
        return (0.25 + 0.7 * t, 0.75 + 0.15 * t, 1.0 - 0.7 * t, 1.0)
    t = (k - 0.5) / 0.5
    return (0.95 + 0.05 * t, 0.9 - 0.65 * t, 0.3 - 0.1 * t, 1.0)


RUNNER = ov.Runner("icepong", GAME_NAME, IcePong, [
    "Mouse: slide your paddle (you can go forwards and back)",
    "Ice: you accelerate and brake slowly",
    "Click / SPACE: SMASH (hit while it glows = fast puck)",
    "The puck speed depends on your speed and where it lands",
    "First to 7 goals - best score / fastest win saved",
    "M: difficulty   R: restart   ESC: quit",
])


def draw_ui(layout, context):
    RUNNER.draw_ui(layout)


def register():
    RUNNER.register()


def unregister():
    RUNNER.unregister()
