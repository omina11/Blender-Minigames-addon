"""Space Invaders - defend Earth from the descending alien formation (GPU overlay).

Pick a difficulty first. The best time to clear the first wave and the best
score are saved for each difficulty.
"""

import math
import random
import traceback

from . import _overlay as ov
from . import _record as _rec

GAME_NAME = "Space Invaders"
GAME_ICON = 'GHOST_ENABLED'

# Logical field (y points up). Everything is scaled to the panel when drawing.
W, H = 480.0, 560.0
PX = 2.7                         # sprite pixel size
COLS, ROWS = 8, 5
DX, DY = 44.0, 36.0              # distance between invaders
X0, Y0 = W / 2 - (COLS - 1) * DX / 2, H - 80.0
PLAYER_Y = 40.0
PLAYER_SPEED = 420.0
NUDGE = 36.0
GROUND_Y = 20.0
SHIELD_X = (60.0, 180.0, 300.0, 420.0)
SH_COLS, SH_ROWS, SH_B = 22, 16, 3.0
SH_BOT = 105.0
SH_TOP = SH_BOT + SH_ROWS * SH_B
BULLET_STEP = 3.0                # max distance moved per collision sub-step

HI = [0]                         # high score of the current difficulty

# lives, speed = formation speed, fire = enemy fire interval multiplier (lower = more shots),
# bspeed = enemy bullet speed, b0 / maxb = enemy bullets on screen, cool = your reload (s),
# drop0 = extra starting descent, shields = shield positions
DIFFS = [
    dict(name="Easy", info="5 lives, slow aliens, 4 shields",
         lives=5, speed=0.8, fire=1.5, bspeed=0.85, b0=2, maxb=4, cool=0.22, drop0=0.0,
         shields=SHIELD_X),
    dict(name="Medium", info="Classic: 3 lives, 4 shields",
         lives=3, speed=1.0, fire=1.0, bspeed=1.0, b0=3, maxb=6, cool=0.28, drop0=0.0,
         shields=SHIELD_X),
    dict(name="Hard", info="2 lives, fast aliens, only 2 shields",
         lives=2, speed=1.35, fire=0.6, bspeed=1.25, b0=4, maxb=8, cool=0.42, drop0=28.0,
         shields=(120.0, 360.0)),
]

C_FIELD = (0.03, 0.04, 0.06, 1.0)
C_SQUID = (0.78, 0.58, 1.00, 1.0)
C_CRAB = (0.40, 0.85, 0.95, 1.0)
C_OCTO = (0.45, 0.92, 0.45, 1.0)
C_CANNON = (0.45, 0.95, 0.55, 1.0)
C_SHIELD = (0.35, 0.80, 0.45, 1.0)
C_UFO = (1.00, 0.35, 0.35, 1.0)
C_EBULLET = (1.00, 0.58, 0.28, 1.0)

# ----------------------------------------------------------------------------
# Sprites (X = pixel). Stored as runs (row, start, length) to keep draw calls low.
# ----------------------------------------------------------------------------

_SQUID = [
    ["...XX...", "..XXXX..", ".XXXXXX.", "XX.XX.XX", "XXXXXXXX", "..X..X..", ".X.XX.X.", "X.X..X.X"],
    ["...XX...", "..XXXX..", ".XXXXXX.", "XX.XX.XX", "XXXXXXXX", ".X.XX.X.", "X......X", ".X....X."],
]
_CRAB = [
    ["..X.....X..", "...X...X...", "..XXXXXXX..", ".XX.XXX.XX.", "XXXXXXXXXXX", "X.XXXXXXX.X", "X.X.....X.X", "...XX.XX..."],
    ["..X.....X..", "X..X...X..X", "X.XXXXXXX.X", "XXX.XXX.XXX", "XXXXXXXXXXX", ".XXXXXXXXX.", "..X.....X..", ".X.......X."],
]
_OCTO = [
    ["....XXXX....", ".XXXXXXXXXX.", "XXXXXXXXXXXX", "XXX..XX..XXX", "XXXXXXXXXXXX", "...XX..XX...", "..XX.XX.XX..", "XX........XX"],
    ["....XXXX....", ".XXXXXXXXXX.", "XXXXXXXXXXXX", "XXX..XX..XXX", "XXXXXXXXXXXX", "..XXX..XXX..", ".XX..XX..XX.", "..XX....XX.."],
]
_CANNON = ["......X......", ".....XXX.....", ".....XXX.....", ".XXXXXXXXXXX.",
           "XXXXXXXXXXXXX", "XXXXXXXXXXXXX", "XXXXXXXXXXXXX", "XXXXXXXXXXXXX"]
_UFO = [".....XXXXXX.....", "...XXXXXXXXXX...", "..XXXXXXXXXXXX..", ".XX.XX.XX.XX.XX.",
        "XXXXXXXXXXXXXXXX", "..XXX..XX..XXX..", "...X........X..."]


def _runs(rows):
    out = []
    for r, line in enumerate(rows):
        c = 0
        while c < len(line):
            if line[c] == 'X':
                s = c
                while c < len(line) and line[c] == 'X':
                    c += 1
                out.append((r, s, c - s))
            else:
                c += 1
    return tuple(out)


def _sprite(frames):
    return (tuple(_runs(f) for f in frames), len(frames[0][0]), len(frames[0]))


SPR = {
    'squid': _sprite(_SQUID), 'crab': _sprite(_CRAB), 'octo': _sprite(_OCTO),
    'cannon': _sprite([_CANNON]), 'ufo': _sprite([_UFO]),
}
ROW_TYPE = ('squid', 'crab', 'crab', 'octo', 'octo')
ROW_SCORE = (30, 20, 20, 10, 10)
ROW_COLOR = (C_SQUID, C_CRAB, C_CRAB, C_OCTO, C_OCTO)
HALF_W = {k: SPR[k][1] * PX / 2 for k in SPR}
HALF_H = {k: SPR[k][2] * PX / 2 for k in SPR}


def _shield_template():
    cuts = (4, 2, 1, 0)
    t = []
    for r in range(SH_ROWS):
        row = [True] * SH_COLS
        if r < 4:
            for k in range(cuts[r]):
                row[k] = row[SH_COLS - 1 - k] = False
        if r >= 12:
            lo, hi = (8, 13) if r == 12 else (7, 14)
            for k in range(lo, hi + 1):
                row[k] = False
        t.append(row)
    return t


SH_TEMPLATE = _shield_template()

_rng = random.Random(7)
STARS = [(_rng.uniform(0, W), _rng.uniform(30, H), _rng.uniform(0.15, 0.5)) for _ in range(45)]


class Invaders(ov.BaseGame):
    keys = ('A', 'D', 'LEFT_ARROW', 'RIGHT_ARROW', 'SPACE', 'UP_ARROW', 'W', 'P',
            'M', 'ONE', 'TWO', 'THREE')

    def setup(self):
        self.diff = 1                     # index in DIFFS
        self.menu = True                  # difficulty menu is shown first
        self.menu_hover = -1
        self.record_mgrs = {}             # diff -> RecordManager (invaders_<name>.json)
        self.best_cache = {}              # diff -> (best first-wave time, best score)

    # ---- records
    def _mgr(self, d):
        if d not in self.record_mgrs:
            try:
                self.record_mgrs[d] = _rec.RecordManager(game_name="invaders_" + DIFFS[d]['name'].lower())
            except Exception:
                self.record_mgrs[d] = None
        return self.record_mgrs[d]

    def _best(self, d):
        if d not in self.best_cache:
            m = self._mgr(d)
            try:
                r = m.get_records() if m else {}
                self.best_cache[d] = (r.get("best_time"), int(r.get("highest_score", 0) or 0))
            except Exception:
                self.best_cache[d] = (None, 0)
        return self.best_cache[d]

    @staticmethod
    def _fmt(t):
        return "--" if t is None else "%d:%02d" % divmod(int(round(t)), 60)

    def _record_win(self, **kw):
        m = self._mgr(self.diff)
        if not m:
            return
        try:
            try:
                m.add_win_record(score=self.score, **kw)
            except TypeError:             # _record.py without best_time support
                m.add_win_record(score=self.score)
            self.best_cache.pop(self.diff, None)
        except Exception:
            traceback.print_exc()

    def _record_loss(self):
        m = self._mgr(self.diff)
        if m:
            try:
                m.add_lose_record()
            except Exception:
                traceback.print_exc()

    # ---- difficulty menu (logical coordinates)
    def _menu_rect(self, i):
        return 60.0, 380.0 - 105.0 * i, 360.0, 85.0

    def _menu_idx(self, x, y):
        if self.sc <= 0:
            return -1
        lx, ly = (x - self.fx0) / self.sc, (y - self.fy0) / self.sc
        for i in range(len(DIFFS)):
            bx, by, bw, bh = self._menu_rect(i)
            if bx <= lx <= bx + bw and by <= ly <= by + bh:
                return i
        return -1

    def _start(self, i):
        self.diff = i
        self.menu = False
        self.reset()
        HI[0] = self._best(i)[1]

    def reset(self):
        self.cfg = DIFFS[self.diff]
        self.level = 1
        self.score = 0
        self.lives = self.cfg['lives']
        self.run_time = 0.0          # seconds played on wave 1
        self.t_first = None          # time taken to clear wave 1
        self.state = 'ready'         # ready | play | dying | cleared | paused | over
        self.px = W / 2
        self.tgt = W / 2
        self.timer = 0.0
        self.invuln = 0.0
        self.cool = 0.0
        self.sc = 1.0
        self.fx0 = self.fy0 = 0.0
        self.fx_pop = []             # floating score texts [x, y, text, t]
        self.booms = []              # explosions [x, y, t]
        self._new_wave()

    def _new_wave(self):
        self.alive = [[True] * COLS for _ in range(ROWS)]
        self.n_alive = ROWS * COLS
        self.ox = 0.0
        self.oy = -14.0 * min(self.level - 1, 6) - (self.cfg['drop0'] if self.level == 1 else 0.0)
        self.dir = 1
        self.anim = 0.0
        self.shields = [[row[:] for row in SH_TEMPLATE] for _ in self.cfg['shields']]
        self.pbullets = []           # [x, y]
        self.ebullets = []           # [x, y, phase]
        self.fire_t = 1.0
        self.ufo = None              # [x, dir, value]
        self.ufo_t = random.uniform(15, 25)

    # ---- helpers
    def _inv_pos(self, r, c):
        return X0 + c * DX + self.ox, Y0 - r * DY + self.oy

    def _speed(self):
        done = 1.0 - self.n_alive / (ROWS * COLS)
        v = (24.0 + 6.0 * (self.level - 1) + 120.0 * done ** 1.4) * self.cfg['speed']
        return min(230.0 * self.cfg['speed'], v)

    def _damage_shield(self, x, y, rad=2):
        for si, sx in enumerate(self.cfg['shields']):
            sx0 = sx - SH_COLS * SH_B / 2
            if sx0 <= x < sx0 + SH_COLS * SH_B and SH_BOT <= y < SH_TOP:
                c = int((x - sx0) / SH_B)
                r = int((SH_TOP - y) / SH_B)
                sh = self.shields[si]
                if sh[r][c]:
                    for dr in range(-rad, rad + 1):
                        for dc in range(-rad, rad + 1):
                            rr, cc = r + dr, c + dc
                            if 0 <= rr < SH_ROWS and 0 <= cc < SH_COLS and sh[rr][cc]:
                                if (dr == 0 and dc == 0) or random.random() < 0.55 - 0.1 * (abs(dr) + abs(dc)):
                                    sh[rr][cc] = False
                    return True
        return False

    def _erase_shields_in(self, x0, y0, x1, y1):
        for si, sx in enumerate(self.cfg['shields']):
            sx0 = sx - SH_COLS * SH_B / 2
            if x1 < sx0 or x0 > sx0 + SH_COLS * SH_B or y1 < SH_BOT or y0 > SH_TOP:
                continue
            sh = self.shields[si]
            for r in range(SH_ROWS):
                cy = SH_TOP - (r + 0.5) * SH_B
                if cy < y0 or cy > y1:
                    continue
                for c in range(SH_COLS):
                    cx = sx0 + (c + 0.5) * SH_B
                    if x0 <= cx <= x1:
                        sh[r][c] = False

    def _fire(self):
        if self.state == 'play' and len(self.pbullets) < 2 and self.cool <= 0:
            self.pbullets.append([self.px, PLAYER_Y + 14.0])
            self.cool = self.cfg['cool']

    # ---- update
    def update(self, dt):
        dt = min(dt, 0.05)
        if self.menu:
            return False
        for lst in (self.booms, self.fx_pop):
            for e in lst:
                e[-1] += dt
        self.booms = [e for e in self.booms if e[2] < 0.3]
        self.fx_pop = [e for e in self.fx_pop if e[3] < 1.0]

        if self.state in ('ready', 'paused', 'over'):
            # let the cannon follow the mouse even before the first shot
            moved = self._move_player(dt) if self.state == 'ready' else False
            return moved or bool(self.booms) or bool(self.fx_pop)
        if self.state == 'dying':
            self.timer -= dt
            if self.timer <= 0:
                if self.lives <= 0:
                    self.state = 'over'
                    HI[0] = max(HI[0], self.score)
                    if self.level == 1:
                        self._record_loss()            # died before clearing wave 1
                    elif self.score > self._best(self.diff)[1]:
                        self._record_win()             # new best score
                else:
                    self.state = 'play'
                    self.invuln = 1.5
            return True
        if self.state == 'cleared':
            self.timer -= dt
            if self.timer <= 0:
                self.level += 1
                self._new_wave()
                self.state = 'play'
            return True

        self.cool = max(0.0, self.cool - dt)
        self.invuln = max(0.0, self.invuln - dt)
        self._move_player(dt)
        self._move_invaders(dt)
        self._move_pbullets(dt)
        self._move_ebullets(dt)
        self._update_ufo(dt)
        self._invader_fire(dt)
        self.run_time += dt
        if self.state == 'play':
            if self.n_alive == 0:
                self.state, self.timer = 'cleared', 1.6
                self.ebullets = []
                if self.level == 1:                    # wave 1 cleared: this is the "win"
                    self.t_first = self.run_time
                    self._record_win(best_time=self.run_time)
            elif self._lowest() <= PLAYER_Y + 16.0:
                self.lives = 0
                self._player_hit()
        return True

    def _move_player(self, dt):
        t = max(20.0, min(W - 20.0, self.tgt))
        step = PLAYER_SPEED * dt
        diff = t - self.px
        new = t if abs(diff) <= step else self.px + math.copysign(step, diff)
        moved = new != self.px
        self.px = new
        return moved

    def _lowest(self):
        low = H
        for r in range(ROWS):
            if any(self.alive[r]):
                low = min(low, Y0 - r * DY + self.oy - HALF_H['crab'])
        return low

    def _move_invaders(self, dt):
        if self.n_alive == 0:
            return
        cols = [c for c in range(COLS) if any(self.alive[r][c] for r in range(ROWS))]
        lo, hi = min(cols), max(cols)
        dx = self.dir * self._speed() * dt
        self.ox += dx
        self.anim += abs(dx)
        left = X0 + lo * DX + self.ox - 17.0
        right = X0 + hi * DX + self.ox + 17.0
        if self.dir > 0 and right >= W - 8.0:
            self.ox -= right - (W - 8.0)
            self.dir, self.oy = -1, self.oy - 16.0
        elif self.dir < 0 and left <= 8.0:
            self.ox += 8.0 - left
            self.dir, self.oy = 1, self.oy - 16.0
        if self._lowest() <= SH_TOP + 6.0:                    # they chew through the shields
            for r in range(ROWS):
                for c in range(COLS):
                    if self.alive[r][c]:
                        x, y = self._inv_pos(r, c)
                        self._erase_shields_in(x - 16, y - 11, x + 16, y + 11)

    def _kill_invader(self, r, c):
        x, y = self._inv_pos(r, c)
        self.alive[r][c] = False
        self.n_alive -= 1
        self.score += ROW_SCORE[r]
        self.booms.append([x, y, 0.0])

    def _move_pbullets(self, dt):
        speed = 480.0
        keep = []
        for b in self.pbullets:
            dist = speed * dt
            n = max(1, int(dist / BULLET_STEP) + 1)
            h = dist / n
            hit = False
            for _ in range(n):
                b[1] += h
                if b[1] > H - 4:
                    hit = True
                    break
                if self._damage_shield(b[0], b[1] + 4, 1):   # your own shots only nick the shields
                    hit = True
                    break
                if self._bullet_vs_ufo(b) or self._bullet_vs_invaders(b):
                    hit = True
                    break
            if not hit:
                keep.append(b)
        self.pbullets = keep

    def _bullet_vs_invaders(self, b):
        for r in range(ROWS):
            ty = ROW_TYPE[r]
            hw, hh = HALF_W[ty] + 1.5, HALF_H[ty] + 4
            for c in range(COLS):
                if self.alive[r][c]:
                    x, y = self._inv_pos(r, c)
                    if abs(b[0] - x) <= hw and abs(b[1] - y) <= hh:
                        self._kill_invader(r, c)
                        return True
        return False

    def _bullet_vs_ufo(self, b):
        u = self.ufo
        if u is None:
            return False
        if abs(b[0] - u[0]) <= HALF_W['ufo'] + 1.5 and abs(b[1] - (H - 40.0)) <= HALF_H['ufo'] + 4:
            self.score += u[2]
            self.booms.append([u[0], H - 40.0, 0.0])
            self.fx_pop.append([u[0], H - 40.0, str(u[2]), 0.0])
            self.ufo = None
            return True
        return False

    def _move_ebullets(self, dt):
        speed = (190.0 + 10.0 * self.level) * self.cfg['bspeed']
        keep = []
        for b in self.ebullets:
            dist = speed * dt
            n = max(1, int(dist / BULLET_STEP) + 1)
            h = dist / n
            gone = False
            b[2] += dt
            for _ in range(n):
                b[1] -= h
                if b[1] < GROUND_Y + 2:
                    gone = True
                    break
                if self._damage_shield(b[0], b[1] - 4):
                    gone = True
                    break
                if (self.state == 'play' and self.invuln <= 0 and abs(b[0] - self.px) <= 16.0
                        and PLAYER_Y - 11.0 <= b[1] <= PLAYER_Y + 11.0):
                    self._player_hit()
                    gone = True
                    break
                for p in self.pbullets:                       # bullets cancel each other
                    if abs(p[0] - b[0]) <= 4.0 and abs(p[1] - b[1]) <= 9.0:
                        self.pbullets.remove(p)
                        gone = True
                        break
                if gone:
                    break
            if not gone:
                keep.append(b)
        self.ebullets = keep if self.state == 'play' else []

    def _player_hit(self):
        self.lives = max(0, self.lives - 1)
        self.state, self.timer = 'dying', 1.4
        self.booms.append([self.px, PLAYER_Y, 0.0])
        self.ebullets = []
        self.pbullets = []

    def _update_ufo(self, dt):
        if self.ufo is not None:
            self.ufo[0] += self.ufo[1] * 110.0 * dt
            if self.ufo[0] < -30 or self.ufo[0] > W + 30:
                self.ufo = None
            return
        self.ufo_t -= dt
        if self.ufo_t <= 0 and self.n_alive > 8:
            d = random.choice((-1, 1))
            self.ufo = [-25.0 if d > 0 else W + 25.0, d, random.choice((50, 100, 150, 300))]
            self.ufo_t = random.uniform(18, 28)

    def _invader_fire(self, dt):
        self.fire_t -= dt
        if self.fire_t > 0 or self.n_alive == 0:
            return
        progress = 1.0 - self.n_alive / (ROWS * COLS)
        self.fire_t = (max(0.30, 1.05 - 0.07 * (self.level - 1) - 0.4 * progress)
                       * self.cfg['fire'] * random.uniform(0.6, 1.3))
        if len(self.ebullets) >= min(self.cfg['maxb'], self.cfg['b0'] + self.level // 2):
            return
        cols = [c for c in range(COLS) if any(self.alive[r][c] for r in range(ROWS))]
        c = random.choice(cols)
        for r in range(ROWS - 1, -1, -1):
            if self.alive[r][c]:
                x, y = self._inv_pos(r, c)
                self.ebullets.append([x, y - 12.0, 0.0])
                return

    # ---- input
    def move(self, x, y):
        if self.menu:
            h = self._menu_idx(x, y)
            changed = h != self.menu_hover
            self.menu_hover = h
            return changed
        if self.state in ('ready', 'play') and self.hit(x, y) and self.sc > 0:
            self.tgt = max(20.0, min(W - 20.0, (x - self.fx0) / self.sc))
        return False

    def click(self, x, y, button):
        if button != 'LEFT':
            return
        if self.menu:
            i = self._menu_idx(x, y)
            if i >= 0:
                self._start(i)
            return
        if self.state in ('ready', 'paused'):
            self.state = 'play'
        elif self.state == 'over':
            self.reset()
        else:
            self._fire()

    def key(self, key, repeat):
        if self.menu:
            i = {'ONE': 0, 'TWO': 1, 'THREE': 2}.get(key)
            if i is not None:
                self._start(i)
            return
        if key == 'M':
            self.menu, self.menu_hover = True, -1
            return
        if key in ('ONE', 'TWO', 'THREE'):
            return
        if key == 'P':
            if self.state == 'play':
                self.state = 'paused'
            elif self.state == 'paused':
                self.state = 'play'
            return
        if key in ('SPACE', 'UP_ARROW', 'W'):
            if self.state in ('ready', 'paused'):
                self.state = 'play'
            elif self.state == 'over':
                self.reset()
            else:
                self._fire()
            return
        d = -1 if key in ('A', 'LEFT_ARROW') else 1
        if self.state == 'ready':
            self.state = 'play'
        self.tgt = max(20.0, min(W - 20.0, self.tgt + d * NUDGE))

    def layout(self, view):
        cs = self.fit_cell(view, 6, 7, max_cell=80, min_cell=30)
        self.place(view, 6 * cs, 7 * cs)
        self.sc = cs / 80.0
        self.fx0 = self.left
        self.fy0 = self.top - 7 * cs

    # ---- drawing
    def _sprite(self, c, name, cx, cy, col, frame=0, scale=1.0):
        runs, nc, nr = SPR[name]
        p = PX * scale
        x0 = cx - nc * p / 2
        ytop = cy + nr * p / 2
        sc, fx0, fy0 = self.sc, self.fx0, self.fy0
        for r, s, ln in runs[frame % len(runs)]:
            c.rect(fx0 + (x0 + s * p) * sc, fy0 + (ytop - (r + 1) * p) * sc, ln * p * sc + 0.5, p * sc + 0.5, col)

    def draw(self, c):
        sc, fx0, fy0, u = self.sc, self.fx0, self.fy0, self.u

        def X(v):
            return fx0 + v * sc

        def Y(v):
            return fy0 + v * sc

        self.draw_frame(c)
        shown = self.t_first if self.t_first is not None else self.run_time
        bt = self._best(self.diff)[0]
        self.draw_header(c, "Space Invaders", "%s | Score %d  Hi %d  Wave %d  Lives %d | Time %s (best %s)" % (
            self.cfg['name'], self.score, max(HI[0], self.score), self.level, self.lives,
            self._fmt(shown), self._fmt(bt)), ov.C_GOOD)

        c.rect(fx0, fy0, W * sc, H * sc, C_FIELD)
        for sx, sy, a in STARS:
            c.rect(X(sx), Y(sy), 1.5 * sc + 0.5, 1.5 * sc + 0.5, (1.0, 1.0, 1.0, a))
        c.rect(X(0), Y(GROUND_Y - 2), W * sc, 2 * sc + 0.5, C_CANNON)

        # shields
        for si, shx in enumerate(self.cfg['shields']):
            sx0 = shx - SH_COLS * SH_B / 2
            sh = self.shields[si]
            for r in range(SH_ROWS):
                col = 0
                while col < SH_COLS:
                    if sh[r][col]:
                        s = col
                        while col < SH_COLS and sh[r][col]:
                            col += 1
                        c.rect(X(sx0 + s * SH_B), Y(SH_TOP - (r + 1) * SH_B),
                               (col - s) * SH_B * sc + 0.5, SH_B * sc + 0.5, C_SHIELD)
                    else:
                        col += 1

        # invaders
        frame = int(self.anim / 12.0) % 2
        for r in range(ROWS):
            for col in range(COLS):
                if self.alive[r][col]:
                    x, y = self._inv_pos(r, col)
                    self._sprite(c, ROW_TYPE[r], x, y, ROW_COLOR[r], frame)

        if self.ufo is not None:
            self._sprite(c, 'ufo', self.ufo[0], H - 40.0, C_UFO)

        # bullets
        for b in self.pbullets:
            c.rect(X(b[0] - 1.2), Y(b[1] - 5), 2.4 * sc, 10 * sc, ov.C_WHITE)
        for b in self.ebullets:
            wob = 1.6 if int(b[2] * 12) % 2 else -1.6
            c.rect(X(b[0] + wob - 1.2), Y(b[1] - 3), 2.4 * sc, 6 * sc, C_EBULLET)
            c.rect(X(b[0] - wob - 1.2), Y(b[1] + 2), 2.4 * sc, 6 * sc, C_EBULLET)

        # cannon
        if self.state == 'dying':
            if int(self.timer * 10) % 2 == 0:
                self._sprite(c, 'cannon', self.px, PLAYER_Y, ov.C_BAD)
        elif self.state != 'over':
            if self.invuln <= 0 or int(self.invuln * 12) % 2 == 0:
                self._sprite(c, 'cannon', self.px, PLAYER_Y, C_CANNON)

        # explosions
        for x, y, t in self.booms:
            p = t / 0.3
            for k in range(8):
                a = k * math.pi / 4
                r0, r1 = 4 + 6 * p, 9 + 12 * p
                c.line((X(x + math.cos(a) * r0), Y(y + math.sin(a) * r0)),
                       (X(x + math.cos(a) * r1), Y(y + math.sin(a) * r1)),
                       2.0 * sc + 0.5, (1.0, 0.85, 0.4, 1.0 - p))
        for x, y, text, t in self.fx_pop:
            c.text(text, X(x), Y(y + 14 + 14 * t), 15 * sc, ov.C_GOLD)

        # spare lives
        for i in range(max(0, self.lives - (1 if self.state in ('play', 'cleared') else 0))):
            self._sprite(c, 'cannon', 22 + i * 26, 9, C_CANNON, 0, 0.5)

        bw, bh = W * sc, H * sc
        if self.menu:
            c.rect(fx0, fy0, bw, bh, (0.0, 0.0, 0.0, 0.80))
            c.text("Select difficulty", X(W / 2), Y(515), 28 * sc, ov.C_WHITE)
            for i, d in enumerate(DIFFS):
                mx, my, mw, mh = self._menu_rect(i)
                c.rect(X(mx), Y(my), mw * sc, mh * sc,
                       ov.C_CELL_HOVER if i == self.menu_hover else ov.C_CELL)
                c.text("%d  %s" % (i + 1, d['name']), X(mx + 16), Y(my + 58), 24 * sc, ov.C_WHITE, 'left')
                c.text(d['info'], X(mx + 16), Y(my + 22), 14 * sc, ov.C_TEXT, 'left')
                tb, sb = self._best(i)
                c.text("Best %s / %d" % (self._fmt(tb), sb) if tb is not None else "No clear yet",
                       X(mx + mw - 16), Y(my + 58), 14 * sc,
                       ov.C_GOLD if tb is not None else ov.C_TEXT, 'right')
            c.text("Best = fastest clear of wave 1 / high score", X(W / 2), Y(50), 13 * sc, ov.C_TEXT)
        elif self.state == 'ready':
            self.banner(c, fx0, fy0, bw, bh, "Space Invaders", "Click or press SPACE to start")
        elif self.state == 'paused':
            self.banner(c, fx0, fy0, bw, bh, "Paused", "Press P or click to resume")
        elif self.state == 'cleared':
            c.text("Wave %d cleared!" % self.level, X(W / 2), Y(H / 2), 26 * u, ov.C_GOLD)
        elif self.state == 'over':
            self.banner(c, fx0, fy0, bw, bh, "Game over", "Score %d - click or R to retry, M for menu" % self.score)

        self.draw_hint(c, "Click / press 1-3 to pick a difficulty   ESC quit" if self.menu else
                       "Mouse / A D move   Click / SPACE fire   P pause   M menu   R restart   ESC quit")


RUNNER = ov.Runner("invaders", GAME_NAME, Invaders, [
    "Mouse or A/D (arrows): move the cannon",
    "LMB / SPACE: fire (2 shots at a time)",
    "Hide behind the shields, they crumble",
    "Shoot the red UFO for bonus points",
    "M: difficulty menu   P: pause",
    "R: restart   ESC: quit",
])


def draw_ui(layout, context):
    RUNNER.draw_ui(layout)


def register():
    RUNNER.register()


def unregister():
    RUNNER.unregister()
