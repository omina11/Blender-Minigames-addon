"""Penalty Shootout - take penalties against the CPU keeper and defend against the CPU
taker (GPU overlay).

SHOOTING: move the mouse to aim inside the goal, CLICK to start the run-up (the
power bar swings), CLICK again (or SPACE) to shoot. Aim for the sweet zone of the
bar: too soft is easy to save, too hard flies over the bar. Corners score more.
SAVING: when the CPU takes its kick, CLICK where you want the keeper to dive
(you can commit before the kick - it is a guess!).

Best of 5 kicks each, then sudden death. Three CPU difficulties; best score and
fastest win saved for each (penalty_easy / medium / hard).
"""

import math
import random
import traceback

from . import _overlay as ov
from . import _record as _rec

GAME_NAME = "Penalty Shootout"
GAME_ICON = 'MATSHADERBALL'

W, H = 640.0, 400.0
SPOT = (320.0, 78.0)
GL, GR = 170.0, 470.0                 # goal posts (x)
GB, GT = 160.0, 262.0                 # ground line / crossbar (y)
KEEPER_STAND = (320.0, 205.0)

DIFFS = [
    dict(name="Easy", info="Keeper guesses blindly, slow dives; CPU shoots soft", read=0.18, react=0.30, reach=31.0,
         cpu_t=0.78, cpu_corner=0.45, mult=1.0, col=(0.45, 0.85, 0.45)),
    dict(name="Medium", info="Keeper reads many shots; CPU picks the corners", read=0.42, react=0.20, reach=37.0,
         cpu_t=0.62, cpu_corner=0.70, mult=1.5, col=(1.0, 0.65, 0.25)),
    dict(name="Hard", info="Sharp keeper, fast dives; CPU hits the top corners", read=0.62, react=0.12, reach=43.0,
         cpu_t=0.50, cpu_corner=0.92, mult=2.0, col=(0.95, 0.30, 0.30)),
]
DIGITS = {'ONE': 0, 'TWO': 1, 'THREE': 2, 'NUMPAD_1': 0, 'NUMPAD_2': 1, 'NUMPAD_3': 2}


def _fmt(t):
    return "--" if t is None else "%d:%02d" % divmod(int(round(t)), 60)


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def _ease(p):
    return p * p * (3 - 2 * p)


class Penalty(ov.BaseGame):
    keys = ('SPACE', 'M', 'ONE', 'TWO', 'THREE', 'NUMPAD_1', 'NUMPAD_2', 'NUMPAD_3')

    # ------------------------------------------------------------------ setup
    def setup(self):
        self.diff = 1
        self.menu = True
        self.menu_hover = -1
        self.record_mgrs = {}
        self.best_cache = {}
        self.sc, self.fx0, self.fy0 = 1.0, 0.0, 0.0
        self.time = 0.0
        self.aim = (320.0, 215.0)
        rng = random.Random(8)
        self.crowd = [(rng.uniform(0, W), rng.uniform(292, 392), rng.random() * 6.28, rng.randrange(6)) for _ in range(330)]
        self.lights = [(70.0, 392.0), (W - 70.0, 392.0), (230.0, 396.0), (410.0, 396.0)]

    def reset(self):
        self.cfg = DIFFS[self.diff]
        self.kicks = []                  # (who, outcome) outcome: goal / save / miss / post
        self.k = 0                       # index of the next kick (even = you shoot)
        self.score_pts = 0
        self.match_t = 0.0
        self.goals = [0, 0]
        self.saves = 0
        self.phase = 'intro'             # intro | aim | power | wait | flight | result | over
        self.timer = 1.8
        self.power = 0.0
        self.power_t = 0.0
        self.ball = [SPOT[0], SPOT[1], 14.0]
        self.trail = []
        self.shot = None
        self.keeper = dict(x=KEEPER_STAND[0], y=KEEPER_STAND[1], ang=0.0, start=None, tgt=None, t0=0.0, dur=0.34)
        self.msg, self.msg_t, self.msg_col = "", 0.0, ov.C_WHITE
        self.shake = 0.0
        self.recorded = False
        self.final = {}
        self.new_best_score = self.new_best_time = False
        self.run = 0.0
        self.cpu_runup = 0.0
        self.celebrate = 0.0
        self.net_t = 0.0

    # ---------------------------------------------------------------- records
    def _mgr(self, d):
        if d not in self.record_mgrs:
            try:
                self.record_mgrs[d] = _rec.RecordManager(game_name="penalty_" + DIFFS[d]['name'].lower())
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
        won = self.goals[0] > self.goals[1]
        if won:
            self.score_pts += int(500 * self.cfg['mult'])
        bs, bt = self._best(self.diff)
        self.final = dict(won=won, score=self.score_pts)
        if won:
            self.new_best_score = self.score_pts > bs
            self.new_best_time = bt is None or self.match_t < bt
        if self.recorded:
            return
        self.recorded = True
        m = self._mgr(self.diff)
        if m:
            try:
                if won:
                    m.add_win_record(score=int(self.score_pts), best_time=self.match_t)
                else:
                    m.add_lose_record()
                self.best_cache.pop(self.diff, None)
            except Exception:
                traceback.print_exc()

    # ------------------------------------------------------------------ logic
    def _player_kick(self):
        return self.k % 2 == 0

    def _begin_kick(self):
        self.keeper.update(x=KEEPER_STAND[0], y=KEEPER_STAND[1], ang=0.0, start=None, tgt=None)
        self.ball = [SPOT[0], SPOT[1], 14.0]
        self.trail = []
        self.shot = None
        self.run = 0.0
        if self._player_kick():
            self.phase = 'aim'
        else:
            self.phase, self.timer = 'wait', 1.4
            # the CPU decides its target now; the keeper (you) may commit before it is struck
            c = self.cfg
            if random.random() < c['cpu_corner']:
                side = random.choice((-1, 1))
                tx = 320 + side * random.uniform(100, 143)
                ty = GB + random.uniform(10, 88)
            else:
                tx, ty = 320 + random.uniform(-70, 70), GB + random.uniform(20, 75)
            self.cpu_target = (tx + random.gauss(0, 4), ty + random.gauss(0, 4))

    def _dive(self, who_keeper_is_player, tx, ty, delay):
        kp = self.keeper
        kp['tgt'] = (_clamp(tx, GL + 8, GR - 8), _clamp(ty, GB + 18, GT - 10))
        kp['start'] = (kp['x'], kp['y'])
        kp['t0'] = self.time + delay
        kp['dur'] = 0.34

    def _keeper_pos(self, at):
        kp = self.keeper
        if kp['tgt'] is None:
            return KEEPER_STAND, 0.0
        p = _clamp((at - kp['t0']) / kp['dur'], 0.0, 1.0)
        e = _ease(p)
        x = kp['start'][0] + (kp['tgt'][0] - kp['start'][0]) * e
        y = kp['start'][1] + (kp['tgt'][1] - kp['start'][1]) * e
        dx = kp['tgt'][0] - KEEPER_STAND[0]
        ang = _clamp(dx / 120.0, -1.0, 1.0) * 1.15 * e
        return (x, y), ang

    def _shoot(self, power, aim):
        """Player kick."""
        sweet = abs(power - 0.80)
        sigma = 2.5 + 55.0 * sweet * sweet * 2.2
        tx = aim[0] + random.gauss(0, sigma)
        ty = aim[1] + random.gauss(0, sigma * 0.8)
        if power > 0.96:
            ty += 70.0
        T = 0.78 - 0.36 * power
        self._launch(tx, ty, T, True)

    def _launch(self, tx, ty, T, by_player):
        c = self.cfg
        self.shot = dict(tx=tx, ty=ty, T=T, t=0.0, by_player=by_player)
        self.phase = 'flight'
        if by_player:
            # CPU keeper decision
            if random.random() < c['read']:
                gx, gy = tx + random.gauss(0, 14), ty + random.gauss(0, 10)
            else:
                side = random.choice((-1, 0, 1))
                gx, gy = 320 + side * random.uniform(60, 135), GB + random.uniform(25, 80)
            self._dive(False, gx, gy, c['react'])
        else:
            if self.keeper['tgt'] is None:                   # the player did not dive: stay (a tiny shuffle)
                pass

    def _resolve(self):
        s = self.shot
        tx, ty = s['tx'], s['ty']
        by_player = s['by_player']
        out = tx < GL - 5 or tx > GR + 5 or ty > GT + 6
        near_post = (abs(tx - GL) < 7 or abs(tx - GR) < 7 or abs(ty - GT) < 7) and not out
        kpos, _ = self._keeper_pos(self.time)
        reach = self.cfg['reach'] if not by_player else 40.0
        if by_player:
            reach = self.cfg['reach']
        else:
            reach = 38.0                                       # your keeper's reach
        saved = (not out) and math.hypot(kpos[0] - tx, kpos[1] - ty) < reach and self.keeper['tgt'] is not None
        if out:
            res, text, col = 'miss', ("OVER THE BAR!" if ty > GT + 6 else "WIDE!"), (1.0, 0.55, 0.4, 1.0)
        elif near_post and random.random() < 0.55:
            res, text, col = 'post', "OFF THE POST!", (1.0, 0.85, 0.3, 1.0)
        elif saved:
            res, text, col = 'save', "SAVED!", (0.5, 0.85, 1.0, 1.0)
        else:
            res, text, col = 'goal', "GOAL!", (0.5, 1.0, 0.55, 1.0)
        self.kicks.append((0 if by_player else 1, res))
        if res == 'goal':
            self.goals[0 if by_player else 1] += 1
            if by_player:
                bonus = 200 + (50 if (abs(tx - 320) > 105 and ty > GB + 30) or abs(tx - 320) > 125 else 0)
                self.score_pts += bonus
                text += "  +%d" % bonus
            self.celebrate = 1.3
            self.shake = 6.0
            self.net_t = 0.6
        elif res == 'save':
            if not by_player:
                self.saves += 1
                self.score_pts += 150
                text += "  +150"
            self.shake = 3.0
        self.msg, self.msg_t, self.msg_col = text, 1.5, col
        self.phase, self.timer = 'result', 1.9
        self.k += 1

    def _match_over(self):
        pk, ck = (self.k + 1) // 2, self.k // 2           # kicks already taken by you / CPU
        if self.k <= 10:
            left_p, left_c = 5 - pk, 5 - ck
            if self.goals[0] > self.goals[1] + left_c or self.goals[1] > self.goals[0] + left_p:
                return True
            return self.k == 10 and self.goals[0] != self.goals[1]
        return self.k % 2 == 0 and self.goals[0] != self.goals[1]

    # ----------------------------------------------------------------- update
    def update(self, dt):
        self.time += dt
        if self.menu:
            return True
        dt = min(dt, 0.05)
        self.msg_t = max(0.0, self.msg_t - dt)
        self.shake = max(0.0, self.shake - 18.0 * dt)
        self.celebrate = max(0.0, self.celebrate - dt)
        self.net_t = max(0.0, self.net_t - dt)
        if self.phase not in ('intro', 'over'):
            self.match_t += dt
        if self.phase == 'intro':
            self.timer -= dt
            if self.timer <= 0:
                self._begin_kick()
        elif self.phase == 'power':
            self.power_t += dt
            self.power = 0.5 - 0.5 * math.cos(self.power_t * 2.9)
            self.run = min(1.0, self.run + dt * 2.2)
        elif self.phase == 'wait':
            self.timer -= dt
            self.cpu_runup = 1.0 - max(0.0, self.timer / 1.4)
            if self.timer <= 0:
                self._launch(self.cpu_target[0], self.cpu_target[1], self.cfg['cpu_t'], False)
        elif self.phase == 'flight':
            s = self.shot
            s['t'] += dt
            p = min(1.0, s['t'] / s['T'])
            # ball path: spot -> target, with a little arc and perspective shrink
            self.ball[0] = SPOT[0] + (s['tx'] - SPOT[0]) * p
            self.ball[1] = SPOT[1] + (s['ty'] - SPOT[1]) * p + math.sin(p * math.pi) * 22
            self.ball[2] = 14.0 - 7.0 * p
            self.trail.append((self.ball[0], self.ball[1], self.ball[2]))
            if len(self.trail) > 14:
                self.trail.pop(0)
            if p >= 1.0:
                self._resolve()
        elif self.phase == 'result':
            self.timer -= dt
            if self.shot:                                   # ball settles / rolls
                self.ball[0] += (self.shot['tx'] - self.ball[0]) * 0.0
            if self.timer <= 0:
                if self._match_over():
                    self.phase = 'over'
                    self._finish_match()
                else:
                    self._begin_kick()
        return True

    # ------------------------------------------------------------------ input
    def _logical(self, x, y):
        return (x - self.fx0) / self.sc, (y - self.fy0) / self.sc

    def _card(self, i):
        return 110.0, 262.0 - i * 92.0, 420.0, 80.0

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
        lx, ly = self._logical(x, y)
        self.aim = (_clamp(lx, GL - 25, GR + 25), _clamp(ly, GB + 5, GT + 35))
        return True

    def _start(self, i):
        self.diff = i
        self.menu = False
        self.reset()

    def _fire_or_dive(self, lx, ly):
        if self.phase == 'aim':
            self.phase = 'power'
            self.power_t = 0.0
            self.power = 0.0
        elif self.phase == 'power':
            self._shoot(self.power, self.aim)
        elif self.phase == 'wait' and self.keeper['tgt'] is None:
            self._dive(True, lx, ly, 0.08)
        elif self.phase == 'flight' and not self.shot['by_player'] and self.keeper['tgt'] is None:
            self._dive(True, lx, ly, 0.08)

    def click(self, x, y, button):
        if self.menu:
            if button == 'LEFT':
                i = self._card_idx(x, y)
                if i >= 0:
                    self._start(i)
            return
        lx, ly = self._logical(x, y)
        if self.phase == 'over':
            if button == 'LEFT':
                self.reset()
            return
        self.aim = (_clamp(lx, GL - 25, GR + 25), _clamp(ly, GB + 5, GT + 35))
        self._fire_or_dive(lx, ly)

    def key(self, key, repeat):
        if self.menu:
            i = DIGITS.get(key)
            if i is not None:
                self._start(i)
            return
        if key == 'M':
            self.menu, self.menu_hover = True, -1
        elif key == 'SPACE':
            if self.phase == 'over':
                self.reset()
            else:
                self._fire_or_dive(self.aim[0], self.aim[1])

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

    def _circ(self, c, x, y, r, col, seg=18):
        c.circle(self._X(x), self._Y(y), r * self.sc, col, seg)

    def _line(self, c, p, q, w, col):
        c.line((self._X(p[0]), self._Y(p[1])), (self._X(q[0]), self._Y(q[1])), max(1.0, w * self.sc), col)

    def _poly(self, c, pts, col):
        c.poly([(self._X(x), self._Y(y)) for x, y in pts], col)

    def _text(self, c, s, x, y, size, col=ov.C_WHITE, align='center'):
        c.text(s, self._X(x), self._Y(y), size * self.sc, col, align)

    def _stadium(self, c):
        t = self.time
        # night sky
        for i in range(8):
            k = i / 7.0
            self._rect(c, 0, 280 + i * 15, W, 16, (0.03 + 0.05 * k, 0.04 + 0.06 * k, 0.12 + 0.12 * k, 1))
        # stands
        self._rect(c, 0, 290, W, 105, (0.10, 0.11, 0.18, 1))
        for x, y, ph, kind in self.crowd:
            col = [(0.9, 0.2, 0.25), (0.95, 0.85, 0.3), (0.3, 0.55, 0.95), (0.95, 0.95, 0.95), (0.4, 0.85, 0.5), (0.9, 0.5, 0.2)][kind]
            wob = math.sin(t * 4 + ph) * (2.2 if self.celebrate > 0 else 0.6)
            self._circ(c, x, y + wob, 3.4, (col[0], col[1], col[2], 0.85), 6)
            self._rect(c, x - 2.4, y + wob - 8, 4.8, 5.5, (col[0] * 0.6, col[1] * 0.6, col[2] * 0.6, 0.9))
        # floodlights
        for lx, ly in self.lights:
            for k in range(4):
                self._circ(c, lx, ly, 40 - k * 9, (1, 1, 0.85, 0.04 + 0.03 * k), 20)
            self._circ(c, lx, ly, 7, (1, 1, 0.9, 1), 10)
        # ad boards
        self._rect(c, 0, 262, W, 28, (0.07, 0.08, 0.12, 1))
        cols = ((0.95, 0.25, 0.3), (0.2, 0.55, 0.95), (0.95, 0.8, 0.2), (0.3, 0.8, 0.45))
        for i in range(8):
            c0 = cols[i % 4]
            self._rect(c, i * 80 + 3, 265, 74, 22, (c0[0], c0[1], c0[2], 1))
            self._rect(c, i * 80 + 3, 283, 74, 4, (1, 1, 1, 0.18))
            self._text(c, ("GOAL", "KICK", "PRO", "ARENA")[i % 4], i * 80 + 40, 276, 10, (1, 1, 1, 0.9))
        # pitch with stripes
        for i in range(9):
            col = (0.14, 0.50, 0.20, 1) if i % 2 == 0 else (0.12, 0.46, 0.18, 1)
            y0 = i * (262 / 9.0)
            self._rect(c, 0, y0, W, 262 / 9.0 + 0.8, col)
        self._rect(c, 0, 0, W, 6, (0, 0, 0, 0.2))
        # penalty box lines (perspective)
        self._line(c, (60, 0), (170, 150), 2.0, (1, 1, 1, 0.55))
        self._line(c, (W - 60, 0), (470, 150), 2.0, (1, 1, 1, 0.55))
        self._line(c, (60, 0), (W - 60, 0), 2.0, (1, 1, 1, 0.55))
        self._line(c, (170, 150), (470, 150), 2.5, (1, 1, 1, 0.9))
        self._line(c, (120, 60), (520, 60), 2.0, (1, 1, 1, 0.45))
        self._circ(c, SPOT[0], SPOT[1] - 5, 4, (1, 1, 1, 0.8), 8)

    def _goal(self, c):
        # backdrop + net
        self._rect(c, GL, GB, GR - GL, GT - GB, (0.05, 0.06, 0.08, 0.85))
        sway = math.sin(self.time * 7) * 3 * (self.net_t / 0.6)
        for i in range(0, 31):
            x = GL + i * (GR - GL) / 30.0
            self._line(c, (x, GB), (x + sway * 0.4, GT), 0.8, (1, 1, 1, 0.22))
        for j in range(0, 11):
            y = GB + j * (GT - GB) / 10.0
            self._line(c, (GL, y), (GR, y + sway), 0.8, (1, 1, 1, 0.22))
        # posts, bar (with a lit edge)
        for (a, b) in (((GL, GB), (GL, GT)), ((GR, GB), (GR, GT)), ((GL, GT), (GR, GT))):
            self._line(c, a, b, 7.0, (0.62, 0.64, 0.68, 1))
            self._line(c, a, b, 4.6, (0.98, 0.98, 1.0, 1))
        self._line(c, (GL, GT + 2), (GR, GT + 2), 1.4, (1, 1, 1, 0.5))

    def _rot(self, pts, cx, cy, ang):
        ca, sa = math.cos(ang), math.sin(ang)
        return [(cx + x * ca - y * sa, cy + x * sa + y * ca) for x, y in pts]

    def _keeper(self, c):
        kp = self.keeper
        pos, ang = self._keeper_pos(self.time)
        cx, cy = pos
        ang = -ang
        gk = (0.98, 0.78, 0.10, 1.0) if self.phase != 'x' else (1, 1, 1, 1)
        skin = (0.95, 0.76, 0.6, 1)
        outline = (0.05, 0.05, 0.08, 1)
        stretch = 1.0 + (0.18 if kp['tgt'] and abs(ang) > 0.3 else 0.0)
        sh = [(-14, -52), (14, -52), (10, -52), (-10, -52)]
        self._poly(c, [(cx - 18 + i, 160 - 2 + j) for i, j in ((0, 0), (36, 0), (30, 5), (6, 5))], (0, 0, 0, 0.3))

        def P(pts):
            return self._rot(pts, cx, cy, ang)
        legs = [[(-9, -50), (-2, -50), (-4, -2), (-12, -2)], [(2, -50), (9, -50), (12, -2), (4, -2)]]
        for lg in legs:
            self._poly(c, P([(x * 1.0, y * stretch * 0.6 - 4) for x, y in lg]), (0.12, 0.12, 0.2, 1))
        self._poly(c, P([(-15, -8), (15, -8), (13, 22), (-13, 22)]), outline)
        self._poly(c, P([(-13.5, -6), (13.5, -6), (12, 20), (-12, 20)]), gk)
        self._poly(c, P([(-13.5, -6), (13.5, -6), (13, 0), (-13, 0)]), (0.15, 0.15, 0.2, 1))
        arm_up = 0.0 if kp['tgt'] is None else _clamp(abs(kp['tgt'][1] - KEEPER_STAND[1]) / 60.0 + 0.4, 0.4, 1.0)
        for sgn in (-1, 1):
            hand = (sgn * (30 + 16 * arm_up), 12 + 24 * arm_up)
            a = P([(sgn * 12, 14)])[0]
            b = P([hand])[0]
            self._line(c, a, b, 7.5, outline)
            self._line(c, a, b, 5.2, gk)
            self._circ(c, b[0], b[1], 6.2, outline, 10)
            self._circ(c, b[0], b[1], 4.8, (0.95, 0.95, 0.98, 1), 10)
        hx, hy = P([(0, 30)])[0]
        self._circ(c, hx, hy, 8.8, outline, 14)
        self._circ(c, hx, hy, 7.4, skin, 14)
        self._rect(c, hx - 7.4, hy + 1.5, 14.8, 5.8, (0.18, 0.12, 0.08, 1)) if abs(ang) < 0.01 else None

    def _taker(self, c):
        """The player taking the kick (back view)."""
        mine = self._player_kick()
        run = self.run if self.phase in ('power', 'aim') else (self.cpu_runup if self.phase == 'wait' else 1.0)
        kick_t = self.shot['t'] if self.shot else 0.0
        back = (0.2, 0.55, 0.98, 1.0) if mine else tuple(self.cfg['col']) + (1.0,)
        x = 262.0 + run * 40 - (kick_t > 0) * 6
        y = 40.0
        sw = math.sin(self.time * 16) * 6 * run * (1 if run < 1 else 0)
        outline = (0.05, 0.05, 0.08, 1)
        self._poly(c, [(x - 16, 3), (x + 16, 3), (x + 12, 8), (x - 12, 8)], (0, 0, 0, 0.3))
        for sg, off in ((-1, sw), (1, -sw)):
            self._line(c, (x + sg * 4, y + 22), (x + sg * 6 + off, y - 2), 7.0, outline)
            self._line(c, (x + sg * 4, y + 22), (x + sg * 6 + off, y - 2), 5.0, (0.95, 0.95, 0.98, 1))
        kick = _clamp(kick_t * 6, 0, 1)
        if kick > 0:
            self._line(c, (x + 4, y + 22), (x + 4 + 22 * kick, y + 22 - 4), 7.0, outline)
            self._line(c, (x + 4, y + 22), (x + 4 + 22 * kick, y + 22 - 4), 5.0, (0.95, 0.95, 0.98, 1))
        self._poly(c, [(x - 11, y + 20), (x + 11, y + 20), (x + 9, y + 50), (x - 9, y + 50)], outline)
        self._poly(c, [(x - 9.5, y + 22), (x + 9.5, y + 22), (x + 8, y + 48), (x - 8, y + 48)], back)
        self._text(c, "9" if mine else "7", x, y + 36, 13, (1, 1, 1, 0.9))
        for sg in (-1, 1):
            self._line(c, (x + sg * 10, y + 46), (x + sg * 14, y + 30 + sw * sg * 0.3), 5.0, back)
        self._circ(c, x, y + 56, 6.4, outline, 12)
        self._circ(c, x, y + 56, 5.2, (0.22, 0.15, 0.10, 1), 12)

    def _ball(self, c):
        b = self.ball
        x, y, r = b
        sh_y = SPOT[1] - 6 + (y - SPOT[1]) * 0.15 if self.phase == 'flight' else y - 4
        self._poly(c, [(x + math.cos(a) * r * 1.0, sh_y + math.sin(a) * r * 0.35) for a in [k * math.pi / 8 for k in range(16)]],
                   (0, 0, 0, 0.30))
        for i, (tx, ty, tr) in enumerate(self.trail):
            a = (i + 1) / (len(self.trail) + 1)
            self._circ(c, tx, ty, tr * 0.7 * a, (1, 1, 1, 0.12 * a), 10)
        self._circ(c, x, y, r, (0.15, 0.15, 0.18, 1), 18)
        self._circ(c, x, y, r * 0.9, (0.98, 0.98, 0.98, 1), 18)
        spin = (self.shot['t'] * 18) if self.shot else 0.0
        for k in range(5):
            a = spin + k * 1.2566
            self._circ(c, x + math.cos(a) * r * 0.55, y + math.sin(a) * r * 0.55, r * 0.22, (0.12, 0.12, 0.16, 1), 6)
        self._circ(c, x, y, r * 0.28, (0.12, 0.12, 0.16, 1), 8)
        self._circ(c, x - r * 0.3, y + r * 0.3, r * 0.2, (1, 1, 1, 0.8), 6)

    def _hud(self, c):
        # kick tracker
        for who in (0, 1):
            col = (0.35, 0.65, 1.0, 1.0) if who == 0 else tuple(self.cfg['col']) + (1.0,)
            x0 = 18 if who == 0 else W - 18 - 5 * 22
            y = 384 - who * 0
            self._text(c, "YOU" if who == 0 else "CPU", x0 + (0 if who == 0 else 5 * 22), y - 14, 9, col, 'left' if who == 0 else 'right')
            outcomes = [r for w_, r in self.kicks if w_ == who]
            n = max(5, len(outcomes) + (1 if len(outcomes) >= 5 else 0))
            n = min(n, 5)
            for i in range(5):
                cx = x0 + 9 + i * 22 if who == 0 else x0 + 9 + i * 22
                self._circ(c, cx, y, 8, (0, 0, 0, 0.55), 12)
                if i < len(outcomes):
                    o = outcomes[i]
                    colr = {'goal': (0.3, 0.9, 0.4, 1), 'save': (0.95, 0.35, 0.3, 1) if who == 0 else (0.3, 0.8, 1.0, 1),
                            'miss': (0.95, 0.35, 0.3, 1), 'post': (1.0, 0.8, 0.3, 1)}[o]
                    self._circ(c, cx, y, 6, colr, 12)
        self._text(c, "%d - %d" % tuple(self.goals), W / 2, 380, 24, ov.C_WHITE)
        self._text(c, "SCORE %d" % self.score_pts, W / 2, 358, 10, ov.C_GOLD)
        rnd = self.k // 2 + 1
        self._text(c, ("Round %d" % rnd) if rnd <= 5 else "SUDDEN DEATH", W / 2, 342, 9, ov.C_TEXT)

    def _menu(self, c):
        self._sx = self._sy = 0.0
        self._stadium(c)
        self._rect(c, 0, 0, W, H, (0, 0, 0, 0.55))
        self._text(c, "PENALTY SHOOTOUT", W / 2, 362, 36, (0.6 + 0.4 * math.sin(self.time * 2) ** 2, 1.0, 0.7, 1.0))
        self._text(c, "5 kicks each - shoot AND save", W / 2, 330, 13, ov.C_TEXT)
        for i, d in enumerate(DIFFS):
            x, y, w, h = self._card(i)
            hov = i == self.menu_hover
            self._rect(c, x + 3, y - 4, w, h, (0, 0, 0, 0.4))
            self._rect(c, x, y, w, h, ov.C_CELL_HOVER if hov else ov.C_CELL)
            self._rect(c, x, y, 9, h, tuple(d['col']) + (1.0,))
            self._text(c, "%d  %s" % (i + 1, d['name']), x + 24, y + h - 24, 20, ov.C_WHITE, 'left')
            self._text(c, d['info'], x + 24, y + 22, 10, ov.C_TEXT, 'left')
            bs, bt = self._best(i)
            if bs:
                self._text(c, "Best score  %d" % bs, x + w - 14, y + h - 26, 13, ov.C_GOLD, 'right')
                self._text(c, "Fastest win  %s" % _fmt(bt), x + w - 14, y + 22, 12, ov.C_GOLD, 'right')
            else:
                self._text(c, "No win yet", x + w - 14, y + h - 26, 12, ov.C_TEXT, 'right')

    def draw(self, c):
        self._sx = self._sy = 0.0
        self.draw_frame(c)
        bs, bt = self._best(self.diff)
        if self.menu:
            self.draw_header(c, "PENALTY SHOOTOUT", "Best score and fastest win saved for every difficulty", ov.C_GOLD)
            self._menu(c)
            self.draw_hint(c, "Click or press 1-3 to pick a difficulty   ESC quit")
            return
        self.draw_header(c, "PENALTY SHOOTOUT - %s" % self.cfg['name'],
                         "Score %d   |   Best %d   |   Fastest win %s   |   Saves %d" % (self.score_pts, bs, _fmt(bt), self.saves),
                         tuple(self.cfg['col']) + (1.0,))
        if self.shake > 0:
            self._sx = random.uniform(-1, 1) * self.shake
            self._sy = random.uniform(-1, 1) * self.shake * 0.6
        self._stadium(c)
        self._goal(c)
        self._keeper(c)
        if self.phase != 'over':
            self._taker(c)
        self._ball(c)
        # re-draw the net over the ball once it is inside the goal
        if self.phase == 'result' and self.shot and self.kicks and self.kicks[-1][1] == 'goal':
            self._rect(c, GL, GB, GR - GL, GT - GB, (1, 1, 1, 0.05))
        sx_, sy_ = self._sx, self._sy
        self._sx = self._sy = 0.0
        # aim reticle / power bar
        if self.phase in ('aim', 'power'):
            ax, ay = self.aim
            self._circ(c, ax, ay, 13, (1, 1, 1, 0.12), 20)
            c.ring(self.fx0 + ax * self.sc, self.fy0 + ay * self.sc, 13 * self.sc, 2.0 * self.sc, (1, 1, 1, 0.9), 22)
            self._line(c, (ax - 20, ay), (ax - 8, ay), 1.6, (1, 1, 1, 0.9))
            self._line(c, (ax + 8, ay), (ax + 20, ay), 1.6, (1, 1, 1, 0.9))
            self._line(c, (ax, ay - 20), (ax, ay - 8), 1.6, (1, 1, 1, 0.9))
            self._line(c, (ax, ay + 8), (ax, ay + 20), 1.6, (1, 1, 1, 0.9))
            bx, by, bw, bh = 20.0, 22.0, 150.0, 12.0
            self._rect(c, bx - 2, by - 2, bw + 4, bh + 4, (0, 0, 0, 0.6))
            self._rect(c, bx + bw * 0.68, by, bw * 0.24, bh, (0.3, 0.9, 0.4, 0.4))
            self._rect(c, bx + bw * 0.93, by, bw * 0.07, bh, (0.95, 0.3, 0.3, 0.45))
            self._rect(c, bx, by, bw * 0.3, bh, (0.9, 0.7, 0.3, 0.25))
            if self.phase == 'power':
                col = (0.3, 0.9, 0.4, 1) if abs(self.power - 0.80) < 0.12 else (1.0, 0.7, 0.3, 1) if self.power < 0.93 else (0.95, 0.3, 0.3, 1)
                self._rect(c, bx, by, bw * self.power, bh, col)
                self._rect(c, bx + bw * self.power - 1.5, by - 3, 3, bh + 6, (1, 1, 1, 1))
            self._text(c, "POWER" if self.phase == 'power' else "click to run up", bx + bw / 2, by + 24, 9, ov.C_WHITE)
        if self.phase == 'wait' or (self.phase == 'flight' and self.shot and not self.shot['by_player']):
            if self.keeper['tgt'] is None:
                self._text(c, "CLICK TO DIVE!", W / 2, 322, 18, (1.0, 0.9, 0.4, 0.7 + 0.3 * math.sin(self.time * 12)))
        if self.phase == 'intro':
            self._text(c, "GET READY", W / 2, 330, 36, ov.C_WHITE)
        if self.msg_t > 0:
            k = min(1.0, self.msg_t * 2)
            self._text(c, self.msg, W / 2, 322, 30 + 4 * math.sin(self.time * 14), (self.msg_col[0], self.msg_col[1], self.msg_col[2], k))
        self._hud(c)
        if self.phase == 'over':
            won = self.final.get('won')
            self._rect(c, 0, 0, W, H, (0, 0, 0, 0.62))
            self._text(c, "YOU WIN!" if won else "YOU LOSE", W / 2, 290, 48, ov.C_GOLD if won else ov.C_BAD)
            self._text(c, "%d - %d" % tuple(self.goals), W / 2, 244, 24, ov.C_WHITE)
            self._text(c, "Score  %d%s" % (self.score_pts, "   NEW BEST!" if (won and self.new_best_score) else ""), W / 2, 210, 20, ov.C_GOLD)
            if won:
                self._text(c, "Time  %s%s" % (_fmt(self.match_t), "   NEW BEST TIME!" if self.new_best_time else ""), W / 2, 182, 13,
                           ov.C_GOLD if self.new_best_time else ov.C_TEXT)
            self._text(c, "SPACE / click: rematch     M: difficulty", W / 2, 130, 12, ov.C_TEXT)
        self._sx, self._sy = sx_, sy_
        if self.phase in ('aim', 'power'):
            self.draw_hint(c, "Aim with the mouse   click: run up   click again: SHOOT (aim for the green zone)   M menu")
        elif self.phase in ('wait', 'flight'):
            self.draw_hint(c, "Click where your keeper should dive   M menu")
        else:
            self.draw_hint(c, "M menu   R restart   ESC quit")


RUNNER = ov.Runner("penalty", GAME_NAME, Penalty, [
    "Shooting: aim with the mouse, click to run up,",
    "click again to shoot (green zone = best power)",
    "Saving: click where the keeper should dive",
    "5 kicks each, then sudden death",
    "Best score + fastest win saved per difficulty",
    "M: difficulty   R: restart   ESC: quit",
])


def draw_ui(layout, context):
    RUNNER.draw_ui(layout)


def register():
    RUNNER.register()


def unregister():
    RUNNER.unregister()
