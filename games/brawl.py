"""Neon Brawl - a 1v1 fighting game (best of 3 rounds) drawn as a GPU overlay.

Controls (the viewport only reports key presses, so hold a key to keep moving):
    A / D (or arrows)   walk            W / UP / SPACE   jump
    S / DOWN            block (hold)    J punch   K kick   L special fireball (needs 40 meter)

Three difficulties (the CPU gets faster, blocks and counters more). The score
rewards damage, combos, health and time left; the best score and the fastest
match win are saved for each difficulty (brawl_easy / medium / hard).
"""

import math
import random
import traceback

from . import _overlay as ov
from . import _record as _rec

GAME_NAME = "Neon Brawl"
GAME_ICON = 'ARMATURE_DATA'

W, H = 720.0, 400.0
GROUND = 64.0
ARENA_L, ARENA_R = 40.0, W - 40.0
ROUND_TIME = 45.0
GRAVITY = 1500.0
JUMP_V = 560.0
WALK = 185.0
HURT_W, HURT_H = 17.0, 112.0

ATTACKS = {
    'punch': dict(dur=0.28, hit=(0.07, 0.16), reach=62.0, y0=46.0, y1=108.0, dmg=6, stun=0.30, kb=75.0, gain=7.0),
    'kick': dict(dur=0.46, hit=(0.15, 0.27), reach=86.0, y0=8.0, y1=100.0, dmg=11, stun=0.42, kb=190.0, gain=10.0),
    'special': dict(dur=0.62, spawn=0.26, cost=40.0, dmg=13, stun=0.46, kb=170.0),
}

DIFFS = [
    dict(name="Easy", info="Slow reactions, rarely blocks - learn the moves",
         think=0.34, atk=0.55, block=0.15, fire=0.08, dodge=0.0, combo=0.05, spd=0.88, dmg=0.9, hp=100.0,
         col=(0.92, 0.30, 0.30), mult=1.0),
    dict(name="Medium", info="Balanced fighter that mixes punches and kicks",
         think=0.22, atk=0.68, block=0.34, fire=0.25, dodge=0.3, combo=0.28, spd=1.00, dmg=1.08, hp=112.0,
         col=(0.70, 0.36, 0.95), mult=1.5),
    dict(name="Hard", info="Fast, blocks, dodges fireballs and chains combos",
         think=0.18, atk=0.88, block=0.4, fire=0.45, dodge=0.65, combo=0.3, spd=1.05, dmg=1.1, hp=118.0,
         col=(0.97, 0.74, 0.14), mult=2.0),
]

SKIN = (0.97, 0.78, 0.62, 1.0)
OUTLINE = (0.05, 0.04, 0.08, 1.0)
P1_COL = (0.22, 0.58, 0.97, 1.0)

DIGITS = {'ONE': 0, 'TWO': 1, 'THREE': 2, 'NUMPAD_1': 0, 'NUMPAD_2': 1, 'NUMPAD_3': 2}


def _fmt_time(t):
    return "--" if t is None else "%d:%02d" % divmod(int(round(t)), 60)


def _lerp(a, b, t):
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t)


def _bend(a, b, amt, prefer):
    """Joint between a and b pushed sideways by amt (towards the `prefer` side)."""
    mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    dx, dy = b[0] - a[0], b[1] - a[1]
    ln = math.hypot(dx, dy) or 1.0
    nx, ny = -dy / ln, dx / ln
    if nx * prefer[0] + ny * prefer[1] < 0:
        nx, ny = -nx, -ny
    return (mx + nx * amt, my + ny * amt)


def _bump(p):
    """0 -> 1 -> 0 over p in [0, 1] (fast out, slower back)."""
    if p < 0.4:
        return math.sin(p / 0.4 * math.pi / 2)
    return max(0.0, math.cos((p - 0.4) / 0.6 * math.pi / 2))


class ClipCanvas:
    """Wraps a Canvas and clips everything to the rectangle (x0, y0, w, h) in screen pixels."""

    def __init__(self, c, x0, y0, w, h):
        self.c, self.x0, self.y0, self.x1, self.y1 = c, x0, y0, x0 + w, y0 + h

    def _clip(self, pts):
        def run(pl, inside, inter):
            out = []
            for i in range(len(pl)):
                a, b = pl[i], pl[(i + 1) % len(pl)]
                ia, ib = inside(a), inside(b)
                if ia != ib:
                    out.append(inter(a, b))
                if ib:
                    out.append(b)
            return out
        X0, X1, Y0, Y1 = self.x0, self.x1, self.y0, self.y1
        ix = lambda xv: (lambda a, b: (xv, a[1] + (b[1] - a[1]) * (xv - a[0]) / ((b[0] - a[0]) or 1e-9)))
        iy = lambda yv: (lambda a, b: (a[0] + (b[0] - a[0]) * (yv - a[1]) / ((b[1] - a[1]) or 1e-9), yv))
        pl = list(pts)
        for inside, inter in ((lambda p: p[0] >= X0, ix(X0)), (lambda p: p[0] <= X1, ix(X1)),
                              (lambda p: p[1] >= Y0, iy(Y0)), (lambda p: p[1] <= Y1, iy(Y1))):
            if not pl:
                return []
            pl = run(pl, inside, inter)
        return pl

    def _inside(self, pts):
        return all(self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1 for x, y in pts)

    def poly(self, pts, col):
        if self._inside(pts):
            self.c.poly(pts, col)
        else:
            cl = self._clip(pts)
            if len(cl) >= 3:
                self.c.poly(cl, col)

    def tri(self, a, b, c_, col):
        self.poly([a, b, c_], col)

    def rect(self, x, y, w, h, col):
        x0, y0 = max(x, self.x0), max(y, self.y0)
        x1, y1 = min(x + w, self.x1), min(y + h, self.y1)
        if x1 > x0 and y1 > y0:
            self.c.rect(x0, y0, x1 - x0, y1 - y0, col)

    def circle(self, cx, cy, r, col, seg=24):
        if cx + r < self.x0 or cx - r > self.x1 or cy + r < self.y0 or cy - r > self.y1:
            return
        if cx - r >= self.x0 and cx + r <= self.x1 and cy - r >= self.y0 and cy + r <= self.y1:
            self.c.circle(cx, cy, r, col, seg)
            return
        n = max(12, seg)
        self.poly([(cx + r * math.cos(2 * math.pi * k / n), cy + r * math.sin(2 * math.pi * k / n)) for k in range(n)], col)

    def ring(self, cx, cy, r, th, col, seg=28):
        ro = r + th / 2
        if cx + ro < self.x0 or cx - ro > self.x1 or cy + ro < self.y0 or cy - ro > self.y1:
            return
        if cx - ro >= self.x0 and cx + ro <= self.x1 and cy - ro >= self.y0 and cy + ro <= self.y1:
            self.c.ring(cx, cy, r, th, col, seg)
            return
        ri, n = max(0.0, r - th / 2), max(16, seg)
        for i in range(n):
            a0, a1 = 2 * math.pi * i / n, 2 * math.pi * (i + 1) / n
            self.poly([(cx + ro * math.cos(a0), cy + ro * math.sin(a0)), (cx + ro * math.cos(a1), cy + ro * math.sin(a1)),
                       (cx + ri * math.cos(a1), cy + ri * math.sin(a1)), (cx + ri * math.cos(a0), cy + ri * math.sin(a0))], col)

    def line(self, p, q, w, col):
        dx, dy = q[0] - p[0], q[1] - p[1]
        ln = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / ln * w / 2, dx / ln * w / 2
        self.poly([(p[0] + nx, p[1] + ny), (p[0] - nx, p[1] - ny), (q[0] - nx, q[1] - ny), (q[0] + nx, q[1] + ny)], col)

    def polyline(self, pts, w, col):
        for a, b in zip(pts, pts[1:]):
            self.line(a, b, w, col)

    def text(self, s, x, y, size, col=(1, 1, 1, 1), align='center'):
        if self.x0 - 40 <= x <= self.x1 + 40 and self.y0 - 10 <= y <= self.y1 + 10:
            self.c.text(s, x, y, size, col, align)


class Fighter:
    def __init__(self, x, face, col, hp, is_cpu=False):
        self.x, self.y, self.vx, self.vy = x, 0.0, 0.0, 0.0
        self.face = face
        self.col = col
        self.hp = self.hp_max = hp
        self.trail = hp
        self.meter = 0.0
        self.state, self.t = 'idle', 0.0
        self.walk, self.walk_t = 0, 0.0
        self.block_t = 0.0
        self.hit_done = False
        self.fired = False
        self.flash = 0.0
        self.combo = 0
        self.is_cpu = is_cpu
        self.ai_t = 0.0
        self.react = False
        self.clock = 0.0

    @property
    def ground(self):
        return self.y <= 0.0 and self.vy <= 0.0

    def set(self, state):
        self.state, self.t = state, 0.0
        self.hit_done = False
        self.fired = False


class Brawl(ov.BaseGame):
    keys = ('A', 'D', 'W', 'S', 'J', 'K', 'L', 'SPACE', 'M', 'LEFT_ARROW', 'RIGHT_ARROW',
            'UP_ARROW', 'DOWN_ARROW', 'ONE', 'TWO', 'THREE', 'NUMPAD_1', 'NUMPAD_2', 'NUMPAD_3')

    # ------------------------------------------------------------------ setup
    def setup(self):
        self.diff = 1
        self.menu = True
        self.menu_hover = -1
        self.record_mgrs = {}
        self.best_cache = {}
        self.sc, self.fx0, self.fy0 = 1.0, 0.0, 0.0
        rng = random.Random(11)
        self.far = [(x, rng.uniform(40, 120), rng.uniform(26, 48)) for x in range(-20, 760, 38)]
        self.near = [(x, rng.uniform(60, 170), rng.uniform(44, 70), rng.random()) for x in range(-30, 780, 62)]
        self.stars = [(rng.uniform(0, W), rng.uniform(190, H), rng.random() * 6) for _ in range(40)]
        self.time = 0.0

    def reset(self):
        self.cfg = DIFFS[self.diff]
        self.pl = Fighter(250.0, 1, P1_COL, 100.0)
        self.cpu = Fighter(470.0, -1, tuple(self.cfg['col']) + (1.0,), self.cfg['hp'], True)
        self.score = 0
        self.wins = [0, 0]
        self.round_no = 1
        self.match_t = 0.0
        self.freeze = 0.0
        self.shake = 0.0
        self.sparks = []
        self.shots = []
        self.pop = []
        self.slow = 1.0
        self.phase = 'intro'              # intro | fight | ko | over
        self.timer = 1.9
        self.round_t = ROUND_TIME
        self.hurt_taken = False
        self.last_win = None
        self.combo_n, self.combo_t = 0, 0.0
        self.recorded = False
        self.new_best_score = self.new_best_time = False
        self.final = {}
        self.shown = 0

    def _new_round(self):
        self.pl.x, self.cpu.x = 250.0, 470.0
        for f, face in ((self.pl, 1), (self.cpu, -1)):
            f.y = f.vx = f.vy = 0.0
            f.face = face
            f.hp = f.hp_max
            f.trail = f.hp
            f.state, f.t = 'idle', 0.0
            f.walk = 0
            f.block_t = f.flash = 0.0
            f.combo = 0
            f.ai_t = 0.4
        self.pl.meter = min(self.pl.meter, 60.0)
        self.shots, self.sparks = [], []
        self.round_t = ROUND_TIME
        self.hurt_taken = False
        self.phase, self.timer = 'intro', 1.9
        self.slow = 1.0

    # --------------------------------------------------------------- records
    def _mgr(self, d):
        if d not in self.record_mgrs:
            try:
                self.record_mgrs[d] = _rec.RecordManager(game_name="brawl_" + DIFFS[d]['name'].lower())
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

    def _record_match(self, won):
        if self.recorded:
            return
        self.recorded = True
        m = self._mgr(self.diff)
        bs, bt = self._best(self.diff)
        if won:
            self.new_best_score = self.score > bs
            self.new_best_time = bt is None or self.match_t < bt
        if not m:
            return
        try:
            if won:
                m.add_win_record(score=int(self.score), best_time=self.match_t)
            else:
                m.add_lose_record()
            self.best_cache.pop(self.diff, None)
        except Exception:
            traceback.print_exc()

    # ----------------------------------------------------------------- logic
    def _can_act(self, f):
        return f.state in ('idle', 'walk')

    def _attack(self, f, kind):
        if f.state in ('hurt', 'ko', 'punch', 'kick', 'special', 'block') or self.phase != 'fight':
            return False
        if kind == 'special':
            if f.meter < ATTACKS['special']['cost'] or f.y > 0:
                return False
            f.meter -= ATTACKS['special']['cost']
        f.set(kind)
        f.walk = 0
        return True

    def _jump(self, f):
        if f.state in ('idle', 'walk', 'block') and f.ground and self.phase == 'fight':
            f.vy = JUMP_V
            f.vx = f.walk * WALK * (0.9 if not f.is_cpu else self.cfg['spd'] * 0.9)
            f.set('jump')

    def _block(self, f):
        if f.state in ('idle', 'walk', 'block') and f.ground and self.phase == 'fight':
            if f.state != 'block':
                f.set('block')
            f.block_t = 0.38
            f.walk = 0

    def _hurtbox(self, f):
        return f.x - HURT_W, f.y, f.x + HURT_W, f.y + HURT_H

    def _hitbox(self, f, spec):
        x0 = f.x + f.face * 8
        x1 = f.x + f.face * spec['reach']
        return min(x0, x1), f.y + spec['y0'], max(x0, x1), f.y + spec['y1']

    @staticmethod
    def _overlap(a, b):
        return a[0] < b[2] and a[2] > b[0] and a[1] < b[3] and a[3] > b[1]

    def _spark(self, x, y, col, big=False):
        self.sparks.append([x, y, 0.0, col, 1.6 if big else 1.0])

    def _hit(self, att, dfd, spec, spark_xy, owner_is_player, dmg_mult=1.0):
        if dfd.state == 'ko':
            return
        dmg = spec['dmg'] * dmg_mult
        # a defender blocks when guarding and facing the attacker
        facing_att = (att.x - dfd.x) * dfd.face > 0
        blocked = dfd.state == 'block' and facing_att
        if blocked:
            chip = max(1.0, dmg * 0.12)
            dfd.hp = max(1.0, dfd.hp - chip)
            dfd.vx = att.face * 55.0
            dfd.block_t = max(dfd.block_t, 0.22)
            self._spark(spark_xy[0], spark_xy[1], (0.45, 0.85, 1.0, 1.0))
            att.meter = min(100.0, att.meter + 3.0)
            dfd.meter = min(100.0, dfd.meter + 2.0)
            self.freeze = 0.03
            self.shake = max(self.shake, 2.0)
            return
        dfd.hp -= dmg
        was_hurt = dfd.state == 'hurt'
        dfd.combo = dfd.combo + 1 if was_hurt else 1
        dfd.set('hurt')
        dfd.t = 0.0
        dfd.stun = spec['stun']
        dfd.vx = att.face * spec['kb']
        if dfd.y > 0 or spec['kb'] > 150:
            dfd.vy = max(dfd.vy, 180.0)
        dfd.walk = 0
        dfd.flash = 0.18
        dfd.react = False
        att.meter = min(100.0, att.meter + spec.get('gain', 8.0))
        dfd.meter = min(100.0, dfd.meter + 5.0)
        self._spark(spark_xy[0], spark_xy[1], (1.0, 0.92, 0.5, 1.0) if spec['dmg'] < 10 else (1.0, 0.45, 0.25, 1.0),
                    spec['dmg'] >= 10)
        self.freeze = 0.07 if spec['dmg'] >= 10 else 0.045
        self.shake = max(self.shake, 7.0 if spec['dmg'] >= 10 else 4.0)
        if owner_is_player:
            self.combo_n = dfd.combo
            self.combo_t = 1.6
            self.score += int(dmg * 10 * (1 + 0.2 * (dfd.combo - 1)))
        else:
            self.hurt_taken = True
        if dfd.hp <= 0:
            dfd.hp = 0.0
            dfd.set('ko')
            dfd.vy, dfd.vx = 330.0, att.face * 210.0
            self._end_round(att)

    def _end_round(self, winner):
        if self.phase != 'fight':
            return
        self.phase, self.timer = 'ko', 2.4
        self.slow = 0.35
        self.last_win = winner
        idx = 0 if winner is self.pl else 1
        self.wins[idx] += 1
        if winner is self.pl:
            bonus = int(self.pl.hp) * 5 + int(self.round_t) * 10 + (200 if not self.hurt_taken else 0)
            self.score += bonus
            self.pop.append(["ROUND BONUS +%d" % bonus, 0.0])
        self.round_reason = "K.O."

    def _time_up(self):
        if self.phase != 'fight':
            return
        a, b = self.pl.hp / self.pl.hp_max, self.cpu.hp / self.cpu.hp_max
        winner = self.pl if a > b else self.cpu
        self._end_round(winner)
        self.round_reason = "TIME UP"

    # ---------------------------------------------------------------- CPU AI
    def _ai(self, dt):
        e, p, cfg = self.cpu, self.pl, self.cfg
        if e.state in ('hurt', 'ko', 'punch', 'kick', 'special'):
            return
        dist = abs(p.x - e.x)
        dirn = 1 if p.x > e.x else -1
        if e.state == 'jump':                                   # air attack on the way down
            if e.y > 18 and dist < 100 and random.random() < 0.22 * cfg['atk']:
                e.set('kick' if random.random() < 0.5 else 'punch')
            return
        attacking = p.state in ('punch', 'kick') and p.t < ATTACKS[p.state]['hit'][1] + 0.03
        recovering = p.state in ('punch', 'kick') and not attacking
        # react to an incoming attack (once per attack)
        if attacking and dist < 125 and not e.react:
            e.react = True
            if random.random() < cfg['block']:
                self._block(e)
                e.ai_t = 0.2
                return
        if not attacking:
            e.react = False
        # dodge fireballs
        for s in self.shots:
            if s['owner'] is p and abs(s['x'] - e.x) < 140 and (s['x'] - e.x) * s['vx'] < 0:
                if random.random() < cfg['dodge'] * 0.2:
                    if random.random() < 0.5 and e.state != 'block':
                        e.walk = 0
                        self._jump(e)
                    else:
                        self._block(e)
                    return
        if e.state == 'block' and e.block_t > 0:
            if recovering and dist < 100 and random.random() < cfg['combo']:     # counter after blocking
                e.block_t = 0.0
                e.set('idle')
                self._attack(e, 'punch' if dist < 74 else 'kick')
            return
        e.ai_t -= dt
        # keep hitting a stunned player / punish a missed attack
        if (p.state == 'hurt' or recovering) and dist < 100 and random.random() < cfg['combo'] * dt * 25:
            self._attack(e, 'punch' if dist < 70 else 'kick')
            e.ai_t = cfg['think'] * 0.8
            return
        if e.ai_t > 0:
            return
        e.ai_t = cfg['think'] * random.uniform(0.7, 1.3)
        if dist > 230 and e.meter >= 40 and random.random() < cfg['fire']:
            self._attack(e, 'special')
            return
        if p.y > 40 and dist < 110 and random.random() < cfg['atk'] + 0.15:     # anti-air
            self._attack(e, 'punch')
            return
        if dist > 100:
            e.walk, e.walk_t = dirn, e.ai_t + 0.08
            if 120 < dist < 220 and random.random() < 0.10 * cfg['dodge']:
                self._jump(e)
            return
        if dist < 40 and random.random() < 0.5:                                  # too close: back off
            e.walk, e.walk_t = -dirn, 0.28
            return
        if random.random() < cfg['atk']:
            if dist <= 74 and (random.random() < 0.5 or cfg['spd'] < 1.0):
                self._attack(e, 'punch')
            else:
                self._attack(e, 'kick')
            if random.random() < 0.35 * cfg['dodge']:                            # hit and run
                e.walk, e.walk_t = -dirn, 0.4
        elif random.random() < 0.5:
            e.walk, e.walk_t = -dirn, 0.3
        else:
            e.walk = 0

    # ---------------------------------------------------------------- update
    def _step_fighter(self, f, o, dt):
        f.clock += dt
        f.t += dt
        f.flash = max(0.0, f.flash - dt)
        spd = WALK * (self.cfg['spd'] if f.is_cpu else 1.0)
        if f.walk_t > 0:
            f.walk_t -= dt
            if f.walk_t <= 0:
                f.walk = 0
        if f.state == 'block':
            f.block_t -= dt
            if f.block_t <= 0:
                f.set('idle')
        # state machines
        if f.state in ('idle', 'walk'):
            f.state = 'walk' if f.walk else 'idle'
            f.vx = f.walk * spd if f.ground else f.vx
        elif f.state == 'jump':
            pass
        elif f.state in ('punch', 'kick'):
            spec = ATTACKS[f.state]
            if f.ground:
                f.vx *= 0.0
            if not f.hit_done and spec['hit'][0] <= f.t <= spec['hit'][1]:
                hb = self._hitbox(f, spec)
                if self._overlap(hb, self._hurtbox(o)):
                    f.hit_done = True
                    cx = (min(hb[2], o.x + HURT_W) + max(hb[0], o.x - HURT_W)) / 2
                    cy = min(max((hb[1] + hb[3]) / 2, o.y + 10), o.y + 100)
                    self._hit(f, o, spec, (cx, cy), not f.is_cpu, self.cfg['dmg'] if f.is_cpu else 1.0)
            if f.t >= spec['dur']:
                f.set('idle' if f.ground else 'jump')
        elif f.state == 'special':
            spec = ATTACKS['special']
            f.vx = 0.0
            if not f.fired and f.t >= spec['spawn']:
                f.fired = True
                self.shots.append(dict(x=f.x + f.face * 40, y=f.y + 82, vx=f.face * 380.0, owner=f, t=0.0,
                                       dmg=self.cfg['dmg'] if f.is_cpu else 1.0))
            if f.t >= spec['dur']:
                f.set('idle')
        elif f.state == 'hurt':
            f.vx *= max(0.0, 1.0 - 5.0 * dt)
            if f.t >= getattr(f, 'stun', 0.3):
                f.set('idle' if f.ground else 'jump')
        elif f.state == 'ko':
            f.vx *= max(0.0, 1.0 - 2.5 * dt)
        # physics
        f.x += f.vx * dt
        if f.y > 0 or f.vy > 0:
            f.vy -= GRAVITY * dt
            f.y += f.vy * dt
            if f.y <= 0:
                f.y, f.vy = 0.0, 0.0
                if f.state == 'jump':
                    f.set('idle')
                    f.vx = 0.0
                elif f.state in ('punch', 'kick') and f.vx:
                    f.vx = 0.0
        f.x = min(ARENA_R, max(ARENA_L, f.x))
        # face the opponent while on the ground and free
        if f.ground and f.state in ('idle', 'walk', 'block'):
            f.face = 1 if o.x > f.x else -1

    def _separate(self):
        a, b = self.pl, self.cpu
        if a.state == 'ko' or b.state == 'ko':
            return
        if a.y < 70 and b.y < 70:
            gap = 36.0 - abs(a.x - b.x)
            if gap > 0:
                s = 1 if a.x <= b.x else -1
                a.x -= s * gap / 2
                b.x += s * gap / 2
                a.x = min(ARENA_R, max(ARENA_L, a.x))
                b.x = min(ARENA_R, max(ARENA_L, b.x))

    def _update_shots(self, dt):
        keep = []
        for s in self.shots:
            s['x'] += s['vx'] * dt
            s['t'] += dt
            if s['x'] < -20 or s['x'] > W + 20:
                continue
            target = self.cpu if s['owner'] is self.pl else self.pl
            if self._overlap((s['x'] - 12, s['y'] - 12, s['x'] + 12, s['y'] + 12), self._hurtbox(target)):
                self._hit(s['owner'], target, dict(ATTACKS['special'], gain=10.0), (s['x'], s['y']),
                          s['owner'] is self.pl, s['dmg'])
                self._spark(s['x'], s['y'], (0.5, 0.85, 1.0, 1.0), True)
                continue
            clash = False
            for o in self.shots:
                if o is not s and o['owner'] is not s['owner'] and abs(o['x'] - s['x']) < 18:
                    clash = True
            if clash:
                self._spark(s['x'], s['y'], (1, 1, 1, 1), True)
                continue
            keep.append(s)
        self.shots = keep

    def update(self, dt):
        if self.menu:
            self.time += dt
            return True
        dt = min(dt, 0.05)
        self.time += dt
        for sp in self.sparks:
            sp[2] += dt
        self.sparks = [sp for sp in self.sparks if sp[2] < 0.32]
        for pp in self.pop:
            pp[1] += dt
        self.pop = [pp for pp in self.pop if pp[1] < 1.8]
        self.shake = max(0.0, self.shake - 30.0 * dt)
        self.combo_t = max(0.0, self.combo_t - dt)
        if self.freeze > 0:
            self.freeze -= dt
            return True
        dt *= self.slow
        if self.phase == 'intro':
            self.timer -= dt
            if self.timer <= 0:
                self.phase = 'fight'
            return True
        if self.phase in ('fight', 'ko'):
            if self.phase == 'fight':
                self.round_t -= dt
                self.match_t += dt
                if self.round_t <= 0:
                    self.round_t = 0.0
                    self._time_up()
                else:
                    self._ai(dt)
            self._step_fighter(self.pl, self.cpu, dt)
            self._step_fighter(self.cpu, self.pl, dt)
            self._separate()
            self._update_shots(dt)
            for f in (self.pl, self.cpu):
                f.trail = max(f.hp, f.trail - 28.0 * dt)
            if self.phase == 'ko':
                self.timer -= dt / self.slow
                if self.timer < 1.5:
                    self.slow = 1.0
                if self.timer <= 0:
                    self._after_round()
            return True
        return True

    def _after_round(self):
        if self.wins[0] >= 2 or self.wins[1] >= 2 or self.round_no >= 3:
            won = self.wins[0] > self.wins[1]
            if won:
                bonus = int(300 * self.cfg['mult'])
                self.score += bonus
            self.final = dict(won=won)
            self.phase = 'over'
            self._record_match(won)
            return
        self.round_no += 1
        self._new_round()

    # ----------------------------------------------------------------- input
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
            if key in ('SPACE',):
                self.reset()
            return
        p = self.pl
        if self.phase != 'fight' or p.state in ('hurt', 'ko'):
            return
        if key in ('A', 'LEFT_ARROW'):
            if self._can_act(p) or p.state == 'jump':
                p.walk, p.walk_t = -1, 0.40
        elif key in ('D', 'RIGHT_ARROW'):
            if self._can_act(p) or p.state == 'jump':
                p.walk, p.walk_t = 1, 0.40
        elif key in ('W', 'UP_ARROW', 'SPACE'):
            if not repeat:
                self._jump(p)
        elif key in ('S', 'DOWN_ARROW'):
            self._block(p)
        elif not repeat:
            if key == 'J':
                self._attack(p, 'punch') if p.state != 'jump' else self._air(p, 'punch')
            elif key == 'K':
                self._attack(p, 'kick') if p.state != 'jump' else self._air(p, 'kick')
            elif key == 'L':
                self._attack(p, 'special')

    def _air(self, f, kind):
        if f.state == 'jump' and f.y > 0:
            f.set(kind)

    def _start(self, i):
        self.diff = i
        self.menu = False
        self.reset()

    # ---------------------------------------------------------------- layout
    def layout(self, view):
        cs = self.fit_cell(view, 9, 5, max_cell=100, min_cell=36)
        self.place(view, 9 * cs, 5 * cs)
        self.sc = cs / 80.0
        self.fx0 = self.left
        self.fy0 = self.top - 5 * cs

    def _card(self, i):
        return 120.0, 270.0 - i * 92.0, 480.0, 80.0

    def _card_idx(self, x, y):
        lx, ly = (x - self.fx0) / self.sc, (y - self.fy0) / self.sc
        for i in range(len(DIFFS)):
            cx, cy, w, h = self._card(i)
            if cx <= lx <= cx + w and cy <= ly <= cy + h:
                return i
        return -1

    def move(self, x, y):
        if self.menu:
            h = self._card_idx(x, y) if x > -1e5 else -1
            changed = h != self.menu_hover
            self.menu_hover = h
            return changed
        return False

    def click(self, x, y, button):
        if self.menu:
            if button == 'LEFT':
                i = self._card_idx(x, y)
                if i >= 0:
                    self._start(i)
            return
        if self.phase == 'over' and button == 'LEFT':
            self.reset()

    # --------------------------------------------------------------- drawing
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

    def _background(self, c):
        t = self.time
        bands = 16
        top, mid, low = (0.05, 0.02, 0.17), (0.55, 0.12, 0.45), (1.0, 0.50, 0.28)
        hgt = H - GROUND
        for i in range(bands):
            k = i / (bands - 1)
            col = _lerp3(low, mid, k * 2) if k < 0.5 else _lerp3(mid, top, (k - 0.5) * 2)
            self._rect(c, 0, GROUND + hgt * i / bands, W, hgt / bands + 1, col + (1.0,))
        for x, y, ph in self.stars:
            a = 0.35 + 0.45 * abs(math.sin(t * 1.5 + ph))
            self._rect(c, x, y, 1.6, 1.6, (1, 1, 1, a))
        # retro sun with slits
        sx, sy, r = 520.0, 205.0, 62.0
        for k in range(4):
            self._circ(c, sx, sy, r + 26 - k * 7, (1.0, 0.6, 0.3, 0.05 + 0.03 * k), 40)
        self._circ(c, sx, sy, r, (1.0, 0.80, 0.32, 1.0), 40)
        self._circ(c, sx, sy - 14, r * 0.78, (1.0, 0.55, 0.30, 0.55), 40)
        for k in range(6):
            yy = sy - r + 8 + k * 11
            self._rect(c, sx - r - 2, yy, 2 * r + 4, 2.0 + k * 0.9, _lerp3((0.55, 0.12, 0.45), (1.0, 0.50, 0.28), 0.5) + (1.0,))
        mid_x = (self.pl.x + self.cpu.x) / 2 if hasattr(self, 'pl') else W / 2
        off = -(mid_x - W / 2) * 0.03
        for x, h, w in self.far:
            self._rect(c, x + off, GROUND, w, h, (0.20, 0.07, 0.30, 1.0))
        off2 = -(mid_x - W / 2) * 0.07
        for x, h, w, r_ in self.near:
            self._rect(c, x + off2, GROUND, w, h, (0.09, 0.04, 0.17, 1.0))
            self._rect(c, x + off2, GROUND + h - 3, w, 3, (0.30, 0.15, 0.45, 1.0))
            for wy in range(int(GROUND + 10), int(GROUND + h - 8), 14):
                for wx in range(int(x + off2 + 6), int(x + off2 + w - 8), 12):
                    if (wx * 7 + wy * 3 + int(r_ * 10)) % 5 < 2:
                        self._rect(c, wx, wy, 5, 6, (1.0, 0.85, 0.40, 0.75))
            if r_ > 0.55:                                                    # neon sign
                col = (0.2, 0.95, 0.9, 0.9) if r_ > 0.8 else (1.0, 0.3, 0.7, 0.9)
                self._rect(c, x + off2 + 6, GROUND + h * 0.55, w - 12, 5, col)
        # floor
        self._rect(c, 0, 0, W, GROUND, (0.10, 0.05, 0.17, 1.0))
        self._rect(c, 0, GROUND - 3, W, 3, (0.35, 0.95, 1.0, 0.85))
        for k in range(1, 6):
            yy = GROUND - 3 - (k ** 1.7) * 2.6
            if yy > 0:
                self._rect(c, 0, yy, W, 1.2, (0.35, 0.95, 1.0, 0.30))
        for k in range(-14, 15):
            x0 = W / 2 + k * 26
            x1 = W / 2 + k * 70
            self._line(c, (x0, GROUND - 4), (x1, 0), 1.2, (0.35, 0.95, 1.0, 0.22))
        # light cones
        for cx, col in ((130.0, (1.0, 0.4, 0.8, 0.07)), (590.0, (0.3, 0.9, 1.0, 0.07))):
            self._poly(c, [(cx - 6, H), (cx + 6, H), (cx + 90, GROUND), (cx - 90, GROUND)], col)

    def _pose(self, f):
        """Local joint positions (x forward, y up, feet at the origin)."""
        t, st = f.t, f.state
        bob = math.sin(f.clock * 5.0) * 1.6
        hip = [0.0, 58.0]
        chest = [0.0, 93.0]
        head = [2.0, 113.0]
        fh, bh = (26.0, 98.0 + bob), (11.0, 102.0 + bob)           # front / back hand (guard)
        ff, bf = (17.0, 0.0), (-15.0, 0.0)
        if st == 'walk':
            ph = f.clock * 11.0 * (1 if f.walk * f.face > 0 else -1)
            ff = (15.0 + 11.0 * math.sin(ph), max(0.0, 7.0 * math.cos(ph)))
            bf = (-15.0 - 11.0 * math.sin(ph), max(0.0, -7.0 * math.cos(ph)))
            hip[1] += abs(math.sin(ph)) * 1.5
        elif st == 'jump' or (f.y > 0 and st in ('punch', 'kick')):
            ff, bf = (14.0, 24.0), (-8.0, 16.0)
            fh, bh = (24.0, 104.0), (8.0, 106.0)
        if st == 'punch':
            e = _bump(min(1.0, t / ATTACKS['punch']['dur']))
            fh = _lerp(fh, (70.0, 99.0), e)
            chest[0] += 6 * e
            head[0] += 7 * e
        elif st == 'kick':
            e = _bump(min(1.0, t / ATTACKS['kick']['dur']))
            ff = _lerp(ff, (88.0, 80.0), e)
            chest[0] -= 8 * e
            head[0] -= 9 * e
            fh, bh = (6.0, 92.0), (-14.0, 98.0)
            if f.y <= 0:
                bf = (-14.0, 0.0)
        elif st == 'special':
            p = min(1.0, t / ATTACKS['special']['dur'])
            if t < ATTACKS['special']['spawn']:
                k = t / ATTACKS['special']['spawn']
                fh, bh = _lerp(fh, (-4.0, 66.0), k), _lerp(bh, (-8.0, 72.0), k)
                chest[0] -= 4 * k
            else:
                k = min(1.0, (t - ATTACKS['special']['spawn']) / 0.12)
                fh, bh = _lerp((-4.0, 66.0), (62.0, 94.0), k), _lerp((-8.0, 72.0), (56.0, 86.0), k)
                chest[0] += 5 * k
            ff, bf = (24.0, 0.0), (-20.0, 0.0)
        elif st == 'block':
            fh, bh = (24.0, 106.0), (22.0, 92.0)
            hip[1] -= 5.0
            chest[1] -= 5.0
            head[1] -= 6.0
            ff, bf = (20.0, 0.0), (-18.0, 0.0)
        elif st == 'hurt':
            k = min(1.0, t / 0.08)
            chest[0] -= 8 * k
            head = [head[0] - 15 * k, head[1] - 4 * k]
            fh, bh = (-10.0, 84.0), (-16.0, 70.0)
            ff, bf = (18.0, 0.0), (-8.0, 0.0)
        elif st == 'ko':
            fh, bh = (14.0, 70.0), (-12.0, 74.0)
        fs, bs = (7.0, chest[1] - 3), (-7.0, chest[1] - 3)
        pts = dict(head=tuple(head), chest=tuple(chest), hip=tuple(hip), fs=fs, bs=bs,
                   fh=fh, bh=bh, ff=ff, bf=bf)
        pts['fe'] = _bend(fs, fh, -7.0, (0, -1))
        pts['be'] = _bend(bs, bh, -7.0, (0, -1))
        pts['fk'] = _bend(tuple(hip), ff, 8.0, (1, 0))
        pts['bk'] = _bend(tuple(hip), bf, 8.0, (1, 0))
        if st == 'ko':                                           # topple backwards
            ang = min(1.0, t / 0.35) * math.radians(82)
            ca, sa = math.cos(ang), math.sin(ang)
            for k in list(pts):
                x, y = pts[k]
                pts[k] = (x * ca - y * sa, x * sa + y * ca)
        return pts

    def _fighter(self, c, f):
        P = self._pose(f)
        fc = f.face
        base = f.col
        gi = (min(1, base[0] * 1.0), min(1, base[1]), min(1, base[2]), 1.0)
        gi_d = ov.shade(gi, 0.62)
        pants = ov.shade(gi, 0.55)
        wx = lambda p: (f.x + fc * p[0], GROUND + f.y + p[1])
        W_ = {k: wx(v) for k, v in P.items()}
        flash = f.flash > 0 and int(f.flash * 40) % 2 == 0
        if flash:
            gi, gi_d, pants = (1, 1, 1, 1), (0.85, 0.85, 0.9, 1), (0.9, 0.9, 0.95, 1)
        skin = (1, 1, 1, 1) if flash else SKIN
        # shadow
        sh = max(0.35, 1.0 - f.y / 180.0)
        self._poly(c, [(f.x + math.cos(a) * 32 * sh, GROUND - 4 + math.sin(a) * 7 * sh)
                       for a in [k * math.pi / 8 for k in range(16)]], (0, 0, 0, 0.38))

        def limb(a, b, w, col):
            self._line(c, a, b, w + 3.5, OUTLINE)
            self._line(c, a, b, w, col)

        def joint(p, r, col):
            self._circ(c, p[0], p[1], r + 1.6, OUTLINE, 10)
            self._circ(c, p[0], p[1], r, col, 10)

        hipw = W_['hip']
        # back leg + back arm
        limb(hipw, W_['bk'], 11, pants)
        limb(W_['bk'], W_['bf'], 10, pants)
        joint(W_['bf'], 6.2, (0.12, 0.10, 0.16, 1.0))
        limb(W_['bs'], W_['be'], 8.5, gi_d)
        limb(W_['be'], W_['bh'], 7.5, skin)
        joint(W_['bh'], 5.4, skin)
        # torso
        sh_l, sh_r = W_['bs'], W_['fs']
        torso = [(hipw[0] - 10, hipw[1]), (hipw[0] + 10, hipw[1]), (sh_r[0] + 4, sh_r[1] + 1), (sh_l[0] - 4, sh_l[1] + 1)]
        self._poly(c, [(p[0] + (-2 if i in (0, 3) else 2) * 0, p[1]) for i, p in enumerate(torso)], OUTLINE)
        t2 = [(hipw[0] - 8.2, hipw[1] + 1.5), (hipw[0] + 8.2, hipw[1] + 1.5), (sh_r[0] + 2.4, sh_r[1] - 1), (sh_l[0] - 2.4, sh_l[1] - 1)]
        self._poly(c, t2, gi)
        self._line(c, (sh_l[0] + 1, sh_l[1] - 3), (hipw[0] + 3 * fc, hipw[1] + 6), 3.0, gi_d)       # gi lapel
        belt_y = hipw[1] + 3.5
        self._line(c, (hipw[0] - 9.5, belt_y), (hipw[0] + 9.5, belt_y), 5.0, (0.95, 0.95, 0.96, 1.0) if not f.is_cpu else (0.12, 0.12, 0.15, 1.0))
        self._line(c, (hipw[0] - 3 * fc, belt_y - 2), (hipw[0] - 10 * fc, belt_y - 9 + math.sin(f.clock * 6) * 2), 2.5,
                   (0.95, 0.95, 0.96, 1.0) if not f.is_cpu else (0.12, 0.12, 0.15, 1.0))
        # front leg
        limb(hipw, W_['fk'], 12, pants)
        limb(W_['fk'], W_['ff'], 10.5, pants)
        joint(W_['ff'], 6.8, (0.12, 0.10, 0.16, 1.0))
        # head
        hx, hy = W_['head']
        self._circ(c, hx, hy, 14.4, OUTLINE, 20)
        self._circ(c, hx, hy, 12.8, skin, 20)
        hair = (0.10, 0.08, 0.12, 1.0) if not f.is_cpu else ov.shade(base, 0.35)
        self._poly(c, [(hx - 12.8, hy + 2), (hx - 9, hy + 11), (hx, hy + 14.5), (hx + 9, hy + 11), (hx + 12.8, hy + 2)], hair)
        band_col = (1.0, 0.25, 0.30, 1.0) if not f.is_cpu else (0.95, 0.95, 0.96, 1.0)
        self._line(c, (hx - 12.8, hy + 3), (hx + 12.8, hy + 3), 4.0, band_col)
        fl = math.sin(f.clock * 9) * 3
        self._line(c, (hx - 12 * fc, hy + 3), (hx - 25 * fc, hy + 1 + fl), 3.0, band_col)
        self._line(c, (hx - 12 * fc, hy + 3), (hx - 23 * fc, hy - 4 + fl), 3.0, ov.shade(band_col, 0.8))
        ex = hx + 5.5 * fc
        if f.state in ('ko',):
            for dx_ in (-1, 1):
                self._line(c, (ex + dx_ * 4 - 2, hy - 1 - 2), (ex + dx_ * 4 + 2, hy - 1 + 2), 1.4, OUTLINE)
                self._line(c, (ex + dx_ * 4 - 2, hy - 1 + 2), (ex + dx_ * 4 + 2, hy - 1 - 2), 1.4, OUTLINE)
        elif f.state == 'hurt':
            self._line(c, (ex - 3, hy), (ex + 3, hy - 1), 1.6, OUTLINE)
            self._line(c, (ex - 1 + 6 * fc, hy - 6), (ex + 4 + 6 * fc, hy - 6), 1.6, OUTLINE)
        else:
            self._circ(c, ex, hy, 3.4, (1, 1, 1, 1), 8)
            self._circ(c, ex + 1.2 * fc, hy, 1.7, OUTLINE, 6)
            self._line(c, (ex - 3.5, hy + 5), (ex + 4.5, hy + 4 - (2 if f.state in ('punch', 'kick', 'special') else 0)), 1.5, OUTLINE)
            mw = 3.0 if f.state in ('punch', 'kick', 'special') else 0.0
            self._line(c, (ex + 1, hy - 7), (ex + 5 * fc, hy - 7 - mw * 0.0), 1.4, OUTLINE)
        # front arm (on top)
        limb(W_['fs'], W_['fe'], 9, gi)
        limb(W_['fe'], W_['fh'], 8, skin)
        joint(W_['fh'], 6.0, skin)
        if f.state in ('punch',) and f.t < 0.2:
            self._circ(c, W_['fh'][0] + 4 * fc, W_['fh'][1], 9.0 * (1 - f.t / 0.2), (1, 1, 1, 0.25), 12)
        if f.state == 'kick' and f.t < 0.3:
            fx, fy = W_['ff']
            self._line(c, (fx - 24 * fc, fy - 6), (fx, fy), 6.0, (1, 1, 1, 0.20))
        if f.state == 'special' and f.t < ATTACKS['special']['spawn'] + 0.05:
            k = min(1.0, f.t / ATTACKS['special']['spawn'])
            gx, gy = (W_['fh'][0] + W_['bh'][0]) / 2, (W_['fh'][1] + W_['bh'][1]) / 2
            for j in range(3):
                self._circ(c, gx, gy, (8 + 12 * k) * (1 - j * 0.28), (0.4, 0.8, 1.0, 0.25 + 0.2 * j), 16)
        if f.state == 'block':
            cx_, cy_ = f.x + fc * 28, GROUND + f.y + 86
            pts = [(cx_ + fc * math.cos(a) * 30, cy_ + math.sin(a) * 46) for a in [-1.2 + k * 0.4 for k in range(7)]]
            self._poly(c, [(cx_ - fc * 6, cy_)] + pts, (0.4, 0.85, 1.0, 0.18))
            for k in range(len(pts) - 1):
                self._line(c, pts[k], pts[k + 1], 2.4, (0.55, 0.95, 1.0, 0.75))

    def _shots(self, c):
        for s in self.shots:
            x, y, t = s['x'], GROUND + s['y'], s['t']
            d = 1 if s['vx'] > 0 else -1
            for k in range(5):
                self._circ(c, x - d * k * 11, y, 10 - k * 1.6, (0.3, 0.7, 1.0, 0.30 - k * 0.05), 14)
            self._circ(c, x, y, 15 + 1.5 * math.sin(t * 25), (0.35, 0.75, 1.0, 0.30), 18)
            self._circ(c, x, y, 10.5, (0.55, 0.88, 1.0, 1.0), 18)
            self._circ(c, x, y, 6.0, (1, 1, 1, 1), 14)

    def _sparks(self, c):
        for x, y, t, col, sz in self.sparks:
            k = t / 0.32
            y = GROUND + y
            for i in range(8):
                a = i * math.pi / 4 + 0.3
                r0, r1 = (5 + 14 * k) * sz, (12 + 32 * k) * sz
                self._line(c, (x + math.cos(a) * r0, y + math.sin(a) * r0), (x + math.cos(a) * r1, y + math.sin(a) * r1),
                           (4.0 - 3.0 * k) * sz, (col[0], col[1], col[2], 1.0 - k))
            c.ring(self._X(x), self._Y(y), (8 + 26 * k) * sz * self.sc, 3.0 * self.sc * (1 - k) + 0.5,
                   (1, 1, 1, 0.8 * (1 - k)), 20)
            self._circ(c, x, y, (10 - 8 * k) * sz, (1, 1, 1, 0.9 * (1 - k)), 12)

    def _hud(self, c):
        # health bars
        for f, left in ((self.pl, True), (self.cpu, False)):
            x0, x1 = (22.0, 306.0) if left else (698.0, 414.0)
            y0, y1 = H - 40.0, H - 22.0
            sk = 8.0 if left else -8.0
            ratio = max(0.0, f.hp / f.hp_max)
            lag = max(0.0, f.trail / f.hp_max)

            def bar(a, col):
                xe = x0 + (x1 - x0) * a
                self._poly(c, [(x0, y0), (xe, y0), (xe + sk if a > 0 else x0, y1), (x0 + sk, y1)] if left else
                           [(x0, y0), (xe, y0), (xe + sk if a > 0 else x0, y1), (x0 + sk, y1)], col)
            full = [(x0, y0), (x1, y0), (x1 + sk, y1), (x0 + sk, y1)]
            self._poly(c, [(p[0] - (2 if left else -2), p[1] - 2) if i < 2 else (p[0] + (2 if left else -2), p[1] + 2)
                           for i, p in enumerate(full)], (0.95, 0.95, 1.0, 0.9))
            self._poly(c, full, (0.10, 0.06, 0.16, 1.0))
            bar(lag, (1.0, 0.95, 0.85, 0.9))
            col = (0.35, 0.95, 0.45, 1.0) if ratio > 0.5 else (1.0, 0.82, 0.25, 1.0) if ratio > 0.25 else (1.0, 0.30, 0.30, 1.0)
            bar(ratio, col)
            self._rect(c, min(x0, x1) + 6, y1 - 4, abs(x1 - x0) - 12, 2, (1, 1, 1, 0.22))
            nm = "KAI" if left else ("RIVAL - " + self.cfg['name'].upper())
            self._text(c, nm, x0 if left else x0, y1 + 9, 10, (0.9, 0.95, 1.0, 1.0), 'left' if left else 'right')
            # super meter
            my0 = y0 - 10.0
            mw = abs(x1 - x0) * 0.62
            mx = x0 if left else x0 - mw
            self._rect(c, mx, my0, mw, 5, (0.05, 0.05, 0.1, 1))
            full_m = f.meter >= ATTACKS['special']['cost']
            mc = (0.4, 0.9, 1.0, 1.0) if not full_m else ov.shade((1.0, 0.85, 0.3, 1.0), 0.85 + 0.15 * math.sin(self.time * 12))
            self._rect(c, mx, my0, mw * f.meter / 100.0, 5, mc)
            self._rect(c, mx + mw * 0.4 - 0.5, my0 - 1, 1.2, 7, (1, 1, 1, 0.6))
            if full_m and left:
                self._text(c, "SPECIAL READY - L", mx + mw + 8, my0 + 2, 8, ov.C_GOLD, 'left')
            # round pips
            wins = self.wins[0 if left else 1]
            for k in range(2):
                px = (x0 + 12 + k * 15) if left else (x0 - 12 - k * 15)
                self._circ(c, px, my0 - 11, 5.2, (0.05, 0.05, 0.1, 1), 12)
                if k < wins:
                    self._circ(c, px, my0 - 11, 4.0, ov.C_GOLD, 12)
        # timer
        tx, ty = W / 2, H - 31.0
        self._poly(c, [(tx - 26, ty + 15), (tx + 26, ty + 15), (tx + 34, ty), (tx + 26, ty - 15), (tx - 26, ty - 15), (tx - 34, ty)],
                   (0.06, 0.04, 0.12, 1.0))
        self._text(c, "%d" % max(0, math.ceil(self.round_t)), tx, ty, 22,
                   ov.C_BAD if self.round_t < 10 else ov.C_WHITE)
        # score + combo
        self._text(c, "SCORE %d" % self.score, 24, 14, 12, ov.C_GOLD, 'left')
        if self.combo_t > 0 and self.combo_n >= 2:
            k = min(1.0, self.combo_t / 0.3)
            self._text(c, "%d HIT COMBO!" % self.combo_n, 24, 40, 17 + 3 * math.sin(self.time * 18), ov.alpha(ov.C_GOLD, k), 'left')

    def _banner_text(self, c):
        if self.phase == 'intro':
            k = 1.9 - self.timer
            if k < 1.0:
                s = 1.0 + max(0.0, 0.5 - k * 1.5)
                self._text(c, "ROUND %d" % self.round_no, W / 2, 230, 44 * s, ov.C_WHITE)
                self._text(c, "KAI  vs  %s" % self.cfg['name'].upper(), W / 2, 196, 14, ov.C_TEXT)
            else:
                s = 1.0 + 0.4 * max(0.0, 1.3 - k)
                self._text(c, "FIGHT!", W / 2, 230, 56 * s, (1.0, 0.45 + 0.4 * math.sin(self.time * 14) ** 2, 0.2, 1.0))
        elif self.phase == 'ko':
            s = 1.0 + 0.15 * math.sin(self.time * 16)
            if getattr(self, 'round_reason', 'K.O.') == "K.O.":
                self._text(c, "K.O.!", W / 2, 230, 74 * s, (1.0, 0.25, 0.25, 1.0))
            else:
                self._text(c, "TIME UP", W / 2, 230, 52 * s, ov.C_WHITE)
            who = "KAI WINS THE ROUND" if self.last_win is self.pl else "RIVAL WINS THE ROUND"
            self._text(c, who, W / 2, 190, 15, ov.C_GOLD if self.last_win is self.pl else ov.C_TEXT)
        for t_, k in self.pop:
            self._text(c, t_, W / 2, 160 + 14 * min(1.0, k * 2), 14, ov.alpha(ov.C_GOLD, max(0.0, 1.0 - k / 1.8)))

    def _menu(self, c):
        self._rect(c, 0, 0, W, H, (0, 0, 0, 0.62))
        self._text(c, "NEON BRAWL", W / 2, 362, 40, (1.0, 0.45 + 0.35 * math.sin(self.time * 3) ** 2, 0.55, 1.0))
        self._text(c, "Choose your rival", W / 2, 330, 14, ov.C_TEXT)
        for i, d in enumerate(DIFFS):
            x, y, w, h = self._card(i)
            hov = i == self.menu_hover
            self._rect(c, x + 3, y - 4, w, h, (0, 0, 0, 0.4))
            self._rect(c, x, y, w, h, ov.C_CELL_HOVER if hov else ov.C_CELL)
            self._rect(c, x, y, 9, h, d['col'] + (1.0,))
            self._text(c, "%d  %s" % (i + 1, d['name']), x + 24, y + h - 24, 20, ov.C_WHITE, 'left')
            self._text(c, d['info'], x + 24, y + 22, 11, ov.C_TEXT, 'left')
            bs, bt = self._best(i)
            if bs:
                self._text(c, "Best score  %d" % bs, x + w - 14, y + h - 26, 13, ov.C_GOLD, 'right')
                self._text(c, "Fastest win  %s" % _fmt_time(bt), x + w - 14, y + 24, 12, ov.C_GOLD, 'right')
            else:
                self._text(c, "No win yet", x + w - 14, y + h - 26, 12, ov.C_TEXT, 'right')
        self._text(c, "A/D move   W jump   S block   J punch   K kick   L fireball", W / 2, 34, 12, ov.C_TEXT)

    def draw(self, c):
        raw = c
        c = ClipCanvas(raw, self.fx0, self.fy0, W * self.sc, H * self.sc)
        self._sx = self._sy = 0.0
        self.draw_frame(raw)
        bs, bt = self._best(self.diff)
        if self.menu:
            self.draw_header(raw, "NEON BRAWL", "Best of 3 rounds   -   score + fastest win saved per difficulty", ov.C_GOLD)
            self._background(c)
            self._menu(c)
            self.draw_hint(raw, "Click or press 1-3 to pick a difficulty   ESC quit")
            return
        self.draw_header(raw, "NEON BRAWL - %s" % self.cfg['name'],
                         "Round %d   |   Score %d   |   Best %d   |   Fastest win %s" % (
                             self.round_no, self.score, bs, _fmt_time(bt)), self.cfg['col'] + (1.0,))
        if self.shake > 0:
            self._sx = random.uniform(-1, 1) * self.shake
            self._sy = random.uniform(-1, 1) * self.shake * 0.6
        self._background(c)
        for f in sorted((self.pl, self.cpu), key=lambda f: f.state == 'ko'):
            self._fighter(c, f)
        self._shots(c)
        self._sparks(c)
        sx_, sy_ = self._sx, self._sy
        self._sx = self._sy = 0.0
        self._hud(c)
        self._banner_text(c)
        self._sx, self._sy = sx_, sy_
        if self.phase == 'over':
            won = self.final.get('won')
            self._rect(c, 0, 0, W, H, (0, 0, 0, 0.66))
            self._sx = self._sy = 0.0
            self._text(c, "YOU WIN!" if won else "YOU LOSE", W / 2, 285, 50, ov.C_GOLD if won else ov.C_BAD)
            self._text(c, "Rounds  %d - %d" % (self.wins[0], self.wins[1]), W / 2, 238, 16, ov.C_WHITE)
            self._text(c, "Score  %d%s" % (self.score, "   NEW BEST!" if (won and self.new_best_score) else ""), W / 2, 208, 22, ov.C_GOLD)
            if won:
                self._text(c, "Time  %s%s" % (_fmt_time(self.match_t), "   NEW BEST TIME!" if self.new_best_time else ""),
                           W / 2, 178, 14, ov.C_GOLD if self.new_best_time else ov.C_TEXT)
            self._text(c, "SPACE / click: rematch     M: change difficulty", W / 2, 130, 13, ov.C_TEXT)
        self.draw_hint(raw, "A/D move  W jump  S block  J punch  K kick  L fireball  M menu  R restart")


def _lerp3(a, b, t):
    t = max(0.0, min(1.0, t))
    return (a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t)


RUNNER = ov.Runner("brawl", GAME_NAME, Brawl, [
    "A/D: walk (hold)   W / SPACE: jump",
    "S: block (hold)",
    "J: punch   K: kick",
    "L: fireball (needs 40 meter)",
    "Best of 3 rounds, 45 s each",
    "M: difficulty   R: restart   ESC: quit",
])


def draw_ui(layout, context):
    RUNNER.draw_ui(layout)


def register():
    RUNNER.register()


def unregister():
    RUNNER.unregister()
