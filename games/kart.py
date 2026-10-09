"""Neon Kart - a top-down kart racer with items and "augments" (GPU overlay).

Steer with the MOUSE (the kart turns towards the cursor) or with A / D.
The kart accelerates by itself; hold S to brake. Drive through the rainbow boxes
to get an item and use it with SPACE or a click:
    Boost - speed burst        Rocket - homing missile at the kart ahead
    Oil   - slippery puddle behind you        Shield - blocks one hit
Before the race you pick 1 of 3 random augments (upgrades for the whole race).
Score = finishing position + coins + hits + time bonus. A podium finish is a
win; the best score and the best race time are saved for every track.
"""

import math
import random
import traceback

from . import _overlay as ov
from . import _record as _rec

GAME_NAME = "Neon Kart"
GAME_ICON = 'AUTO'

W, H = 640.0, 400.0
LAPS = 3
N_PTS = 360                       # samples of the track centre line
KART_R = 11.0
TOP_SPEED = 330.0
N_KARTS = 6
POINTS_FOR_POS = (1000, 700, 500, 350, 250, 150)

C_GRASS_A = (0.20, 0.50, 0.24, 1.0)
C_GRASS_B = (0.18, 0.46, 0.22, 1.0)
C_ASPHALT = (0.20, 0.21, 0.25, 1.0)
C_CURB_A = (0.92, 0.18, 0.22, 1.0)
C_CURB_B = (0.96, 0.96, 0.98, 1.0)
KART_COLORS = ((0.25, 0.60, 1.00, 1.0), (0.95, 0.30, 0.30, 1.0), (0.98, 0.78, 0.20, 1.0),
               (0.40, 0.85, 0.45, 1.0), (0.80, 0.45, 0.95, 1.0), (1.00, 0.55, 0.20, 1.0))
NAMES = ("YOU", "Blaze", "Volt", "Mint", "Orchid", "Ember")

TRACKS = [
    dict(name="Green Circuit", key="green", info="Wide and friendly - gentle curves", width=84.0, cpu=0.86, rubber=0.03,
         pts=[(0, 0), (520, -80), (1020, 40), (1260, 420), (1040, 800), (520, 900), (-60, 760), (-330, 420), (-240, 120)]),
    dict(name="Sunset Coast", key="sunset", info="Medium: S-bends and a long sweeper", width=72.0, cpu=0.93, rubber=0.05,
         pts=[(0, 0), (460, -120), (900, 60), (1180, -80), (1500, 260), (1240, 640), (820, 520), (520, 840),
              (60, 900), (-360, 640), (-300, 220)]),
    dict(name="Neon Skyline", key="neon", info="Hard: hairpins and tight chicanes", width=62.0, cpu=1.00, rubber=0.07,
         pts=[(0, 0), (400, -180), (860, -100), (1060, 160), (820, 360), (1180, 620), (900, 980), (480, 760),
              (260, 1000), (-200, 960), (-420, 600), (-120, 420), (-440, 200)]),
]

AUGMENTS = [
    ("turbo", "Turbo Engine", "+8% top speed"),
    ("grip", "Grip Tires", "Sharper turns, less grass slowdown"),
    ("magnet", "Coin Magnet", "Coins pull in and score x1.5"),
    ("armor", "Reactive Armor", "Start every lap with a shield"),
    ("luck", "Lucky Boxes", "Better items from the boxes"),
]

DIGITS = {'ONE': 0, 'TWO': 1, 'THREE': 2, 'NUMPAD_1': 0, 'NUMPAD_2': 1, 'NUMPAD_3': 2}


def _fmt(t):
    if t is None:
        return "--"
    return "%d:%04.1f" % divmod(t, 60) if t >= 60 else "%.1f s" % t


def _wrap(a):
    while a > math.pi:
        a -= 2 * math.pi
    while a < -math.pi:
        a += 2 * math.pi
    return a


def _catmull(pts, n):
    """Closed Catmull-Rom spline resampled at n points of equal arc length."""
    m = len(pts)
    dense = []
    for i in range(m):
        p0, p1, p2, p3 = pts[(i - 1) % m], pts[i], pts[(i + 1) % m], pts[(i + 2) % m]
        for k in range(40):
            t = k / 40.0
            t2, t3 = t * t, t * t * t
            dense.append(tuple(0.5 * ((2 * p1[j]) + (-p0[j] + p2[j]) * t + (2 * p0[j] - 5 * p1[j] + 4 * p2[j] - p3[j]) * t2
                                      + (-p0[j] + 3 * p1[j] - 3 * p2[j] + p3[j]) * t3) for j in (0, 1)))
    cum = [0.0]
    for i in range(len(dense)):
        a, b = dense[i], dense[(i + 1) % len(dense)]
        cum.append(cum[-1] + math.hypot(b[0] - a[0], b[1] - a[1]))
    total = cum[-1]
    out, j = [], 0
    for i in range(n):
        target = total * i / n
        while cum[j + 1] < target:
            j += 1
        a, b = dense[j], dense[(j + 1) % len(dense)]
        seg = cum[j + 1] - cum[j] or 1.0
        u = (target - cum[j]) / seg
        out.append((a[0] + (b[0] - a[0]) * u, a[1] + (b[1] - a[1]) * u))
    return out, total


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


class Track:
    def __init__(self, spec):
        self.spec = spec
        self.pts, self.length = _catmull(spec['pts'], N_PTS)
        self.w = spec['width']
        self.dir = []
        for i in range(N_PTS):
            a, b = self.pts[i], self.pts[(i + 1) % N_PTS]
            self.dir.append(math.atan2(b[1] - a[1], b[0] - a[0]))
        rng = random.Random(5)
        self.trees = []
        for i in range(0, N_PTS, 3):
            if rng.random() < 0.7:
                x, y = self.pts[i]
                a = self.dir[i] + (math.pi / 2) * rng.choice((-1, 1))
                d = self.w / 2 + rng.uniform(55, 190)
                self.trees.append((x + math.cos(a) * d, y + math.sin(a) * d, rng.uniform(9, 17), rng.random()))
        self.boxes = []
        for i in range(12, N_PTS, N_PTS // 6):
            for off in (-self.w * 0.28, 0.0, self.w * 0.28):
                self.boxes.append([i, off])
        self.coins = []
        for i in range(6, N_PTS, 7):
            self.coins.append([i, math.sin(i * 0.31) * self.w * 0.30])

    def pos(self, idx, off=0.0):
        x, y = self.pts[idx % N_PTS]
        a = self.dir[idx % N_PTS] + math.pi / 2
        return x + math.cos(a) * off, y + math.sin(a) * off

    def nearest(self, x, y, around, span=14):
        best, bi = 1e18, around
        for k in range(-span, span + 1):
            i = (around + k) % N_PTS
            px, py = self.pts[i]
            d = (px - x) ** 2 + (py - y) ** 2
            if d < best:
                best, bi = d, i
        return bi, math.sqrt(best)


class Kart:
    def __init__(self, i, track, slot):
        self.i = i
        self.col = KART_COLORS[i]
        self.name = NAMES[i]
        row, side = slot // 2, (-1 if slot % 2 == 0 else 1)
        idx = (-6 - row * 6) % N_PTS
        self.x, self.y = track.pos(idx, side * track.w * 0.22)
        self.a = track.dir[idx]
        self.speed = 0.0
        self.idx = idx
        self.prog = float(-6 - row * 6)            # progress in samples (negative = behind the line)
        self.spin = 0.0
        self.boost = 0.0
        self.shield = 0.0
        self.item = None
        self.item_t = 0.0
        self.finished = None
        self.dist_c = 0.0
        self.offroad = False
        self.ai_t = random.uniform(0.2, 0.9)
        self.skill = random.uniform(0.96, 1.03)
        self.lap = 0


class NeonKart(ov.BaseGame):
    keys = ('A', 'D', 'S', 'SPACE', 'M', 'LEFT_ARROW', 'RIGHT_ARROW', 'DOWN_ARROW',
            'ONE', 'TWO', 'THREE', 'NUMPAD_1', 'NUMPAD_2', 'NUMPAD_3')

    # ------------------------------------------------------------------ setup
    def setup(self):
        self.track_i = 0
        self.phase = 'menu'                  # menu | augment | countdown | race | finished
        self.menu_hover = -1
        self.aug = None
        self.aug_choices = []
        self.record_mgrs = {}
        self.best_cache = {}
        self.sc, self.fx0, self.fy0 = 1.0, 0.0, 0.0
        self.tracks = [Track(s) for s in TRACKS]
        self.time = 0.0
        self.mouse = None
        self.key_steer, self.key_t = 0.0, 0.0
        self.track = self.tracks[0]

    def reset(self):
        self.track = self.tracks[self.track_i]
        self.cfg = TRACKS[self.track_i]
        self.karts = [Kart(i, self.track, i) for i in range(N_KARTS)]
        self.pl = self.karts[0]
        self.oil = []                        # [x, y, t, owner]
        self.rockets = []                    # dict
        self.coin_got = set()
        self.box_t = {}
        self.fx = []                         # (x, y, t, kind)
        self.coins = 0
        self.hits = 0
        self.race_t = 0.0
        self.count_t = 3.2
        self.score_extra = 0
        self.final = {}
        self.recorded = False
        self.new_best_score = self.new_best_time = False
        self.shake = 0.0
        self.msg, self.msg_t = "", 0.0
        self.cam = [self.pl.x, self.pl.y]
        self.zoom = 0.9
        self.pl_lap_seen = 0
        if self.aug and self.phase != 'menu':
            self._apply_aug()
        if self.phase in ('countdown', 'race', 'finished'):
            self.phase = 'countdown'

    def _apply_aug(self):
        if self.aug == 'armor':
            self.pl.shield = 6.0

    # ---------------------------------------------------------------- records
    def _mgr(self, i):
        if i not in self.record_mgrs:
            try:
                self.record_mgrs[i] = _rec.RecordManager(game_name="kart_" + TRACKS[i]['key'])
            except Exception:
                self.record_mgrs[i] = None
        return self.record_mgrs[i]

    def _best(self, i):
        if i not in self.best_cache:
            m = self._mgr(i)
            try:
                r = m.get_records() if m else {}
                self.best_cache[i] = (int(r.get("highest_score", 0) or 0), r.get("best_time"))
            except Exception:
                self.best_cache[i] = (0, None)
        return self.best_cache[i]

    def _score(self):
        s = self.coins * (37 if self.aug == 'magnet' else 25) + self.hits * 120 + self.score_extra
        if self.pl.finished:
            pos = self._rank(self.pl)
            s += POINTS_FOR_POS[pos - 1] + int(max(0.0, 100.0 - self.pl.finished) * 10)
        return int(s)

    def _record(self):
        if self.recorded:
            return
        self.recorded = True
        pos = self._rank(self.pl)
        score = self._score()
        bs, bt = self._best(self.track_i)
        podium = pos <= 3
        if podium:
            self.new_best_score = score > bs
            self.new_best_time = bt is None or self.pl.finished < bt
        self.final = dict(pos=pos, score=score, podium=podium)
        m = self._mgr(self.track_i)
        if not m:
            return
        try:
            if podium:
                m.add_win_record(score=score, best_time=self.pl.finished)
            else:
                m.add_lose_record()
            self.best_cache.pop(self.track_i, None)
        except Exception:
            traceback.print_exc()

    # ------------------------------------------------------------------ logic
    def _rank(self, k):
        order = sorted(self.karts, key=lambda q: (q.finished is None, q.finished if q.finished else -q.prog))
        return order.index(k) + 1

    def _top_speed(self, k):
        top = TOP_SPEED
        if k is self.pl:
            if self.aug == 'turbo':
                top *= 1.08
        else:
            top *= self.cfg['cpu'] * k.skill
            behind = self.pl.prog - k.prog
            if behind > 25:                               # rubber band
                top *= 1.0 + self.cfg['rubber'] * min(1.0, behind / 120.0)
            elif behind < -40:
                top *= 1.0 - self.cfg['rubber'] * 0.7 * min(1.0, -behind / 120.0)
        if k.offroad:
            top *= 0.62 if (k is self.pl and self.aug == 'grip') else 0.48
        if k.boost > 0:
            top *= 1.55
        return top

    def _give_item(self, k):
        rank = self._rank(k)
        lucky = 1.4 if (k is self.pl and self.aug == 'luck') else 1.0
        back = (rank - 1) / (N_KARTS - 1.0)
        w = {'oil': 38 * (1 - back) + 6, 'shield': 28, 'boost': (18 + 26 * back) * lucky,
             'rocket': (8 + 34 * back) * lucky}
        r = random.uniform(0, sum(w.values()))
        for name, wt in w.items():
            r -= wt
            if r <= 0:
                return name
        return 'boost'

    def _use_item(self, k):
        if not k.item or k.spin > 0 or k.finished:
            return
        it, k.item = k.item, None
        if it == 'boost':
            k.boost = 1.7
            k.speed = max(k.speed, 300.0)
        elif it == 'shield':
            k.shield = 7.0
        elif it == 'oil':
            self.oil.append([k.x - math.cos(k.a) * 22, k.y - math.sin(k.a) * 22, 14.0, k])
        elif it == 'rocket':
            target = None
            for q in sorted(self.karts, key=lambda q: q.prog):
                if q is not k and q.prog > k.prog and not q.finished:
                    target = q
                    break
            self.rockets.append(dict(x=k.x + math.cos(k.a) * 18, y=k.y + math.sin(k.a) * 18, a=k.a,
                                     t=0.0, owner=k, target=target))

    def _hit_kart(self, k, by):
        if k.finished:
            return
        if k.shield > 0:
            k.shield = 0.0
            self.fx.append([k.x, k.y, 0.0, 'pop'])
            return
        if k.spin <= 0:
            k.spin = 1.1
            k.speed *= 0.2
            self.fx.append([k.x, k.y, 0.0, 'hit'])
            if by is self.pl and k is not self.pl:
                self.hits += 1
                self.msg, self.msg_t = "HIT! +120", 1.2
            if k is self.pl:
                self.shake = 7.0

    def _steer_cpu(self, k, dt):
        look = int(8 + k.speed / 28)
        tx, ty = self.track.pos(k.idx + look, math.sin(k.i * 1.7 + k.idx * 0.02) * self.track.w * 0.2)
        want = math.atan2(ty - k.y, tx - k.x)
        err = _wrap(want - k.a)
        k.ai_t -= dt
        if k.item and k.ai_t <= 0:
            it = k.item
            use = it in ('boost', 'shield')
            if it == 'rocket':
                use = any(q is not k and 0 < q.prog - k.prog < 40 and not q.finished for q in self.karts)
            elif it == 'oil':
                use = any(q is not k and 0 < k.prog - q.prog < 25 for q in self.karts)
            if use or k.item_t > 5.0:
                self._use_item(k)
            k.ai_t = random.uniform(0.6, 1.4)
        return max(-1.0, min(1.0, err * 2.6)), abs(err)

    def _step_kart(self, k, dt, steer, brake):
        if k.spin > 0:
            k.spin -= dt
            k.a += 9.0 * dt
            k.speed = max(0.0, k.speed - 300.0 * dt)
        else:
            grip = 1.18 if (k is self.pl and self.aug == 'grip') else 1.0
            k.a += steer * 2.7 * grip * min(1.0, k.speed / 110.0 + 0.2) * dt
            top = self._top_speed(k) if not k.finished else 140.0
            if brake:
                k.speed = max(0.0, k.speed - 520.0 * dt)
            elif k.speed < top:
                k.speed = min(top, k.speed + (680.0 if k.boost > 0 else 250.0) * dt)
            else:
                k.speed = max(top, k.speed - 380.0 * dt)
        k.boost = max(0.0, k.boost - dt)
        k.shield = max(0.0, k.shield - dt)
        k.item_t += dt if k.item else 0.0
        k.x += math.cos(k.a) * k.speed * dt
        k.y += math.sin(k.a) * k.speed * dt
        idx, d = self.track.nearest(k.x, k.y, k.idx)
        delta = idx - k.idx
        if delta < -N_PTS / 2:
            delta += N_PTS
        elif delta > N_PTS / 2:
            delta -= N_PTS
        k.prog += delta
        k.idx, k.dist_c = idx, d
        half = self.track.w / 2
        k.offroad = d > half
        lim = half + 48.0
        if d > lim:                                     # soft barrier at the edge of the grass
            px, py = self.track.pts[idx]
            k.x += (px - k.x) * min(1.0, (d - lim) / d + 0.04)
            k.y += (py - k.y) * min(1.0, (d - lim) / d + 0.04)
            k.speed *= 0.97
        lap = int(k.prog // N_PTS)
        if lap > k.lap:
            k.lap = lap
            if k is self.pl and self.aug == 'armor':
                k.shield = 6.0
        if k.prog >= LAPS * N_PTS and not k.finished:
            k.finished = self.race_t

    def _collide_karts(self):
        ks = self.karts
        for i in range(len(ks)):
            for j in range(i + 1, len(ks)):
                a, b = ks[i], ks[j]
                dx, dy = b.x - a.x, b.y - a.y
                d = math.hypot(dx, dy)
                if d < KART_R * 2 and d > 1e-6:
                    nx, ny = dx / d, dy / d
                    push = (KART_R * 2 - d) / 2
                    a.x -= nx * push
                    a.y -= ny * push
                    b.x += nx * push
                    b.y += ny * push
                    sa, sb = a.speed, b.speed
                    a.speed = sa * 0.9 + sb * 0.05
                    b.speed = sb * 0.9 + sa * 0.05

    def _pickups(self, dt):
        t = self.time
        for n, (idx, off) in enumerate(self.track.boxes):
            if self.box_t.get(n, 0.0) > t:
                continue
            bx, by = self.track.pos(idx, off)
            for k in self.karts:
                if k.item is None and not k.finished and (k.x - bx) ** 2 + (k.y - by) ** 2 < 24 ** 2:
                    k.item, k.item_t = self._give_item(k), 0.0
                    self.box_t[n] = t + 5.0
                    self.fx.append([bx, by, 0.0, 'box'])
                    if k is self.pl:
                        self.msg, self.msg_t = "Item: %s" % k.item.upper(), 1.0
                    break
        pl = self.pl
        reach = 70.0 if self.aug == 'magnet' else 17.0
        for n, (idx, off) in enumerate(self.track.coins):
            key = (n, pl.lap if pl.lap < LAPS else LAPS)
            if key in self.coin_got:
                continue
            cx, cy = self.track.pos(idx, off)
            if (pl.x - cx) ** 2 + (pl.y - cy) ** 2 < reach * reach:
                self.coin_got.add(key)
                self.coins += 1
                self.fx.append([cx, cy, 0.0, 'coin'])
        for o in list(self.oil):
            o[2] -= dt
            if o[2] <= 0:
                self.oil.remove(o)
                continue
            for k in self.karts:
                if (k.x - o[0]) ** 2 + (k.y - o[1]) ** 2 < 17 ** 2 and (k is not o[3] or o[2] < 11.0):
                    self.oil.remove(o)
                    self._hit_kart(k, o[3])
                    break

    def _update_rockets(self, dt):
        for r in list(self.rockets):
            r['t'] += dt
            tgt = r['target']
            if tgt is not None and not tgt.finished:
                want = math.atan2(tgt.y - r['y'], tgt.x - r['x'])
                r['a'] += max(-4.5 * dt, min(4.5 * dt, _wrap(want - r['a'])))
            r['x'] += math.cos(r['a']) * 430.0 * dt
            r['y'] += math.sin(r['a']) * 430.0 * dt
            hit = None
            for k in self.karts:
                if k is not r['owner'] and (k.x - r['x']) ** 2 + (k.y - r['y']) ** 2 < 16 ** 2 and not k.finished:
                    hit = k
                    break
            if hit is not None:
                self.rockets.remove(r)
                self._hit_kart(hit, r['owner'])
            elif r['t'] > 4.0:
                self.rockets.remove(r)

    # ----------------------------------------------------------------- update
    def update(self, dt):
        dt = min(dt, 0.05)
        self.time += dt
        if self.phase in ('menu', 'augment'):
            return True
        for f in self.fx:
            f[2] += dt
        self.fx = [f for f in self.fx if f[2] < 0.6]
        self.msg_t = max(0.0, self.msg_t - dt)
        self.shake = max(0.0, self.shake - 24.0 * dt)
        self.key_t = max(0.0, self.key_t - dt)
        if self.phase == 'countdown':
            self.count_t -= dt
            if self.count_t <= 0:
                self.phase = 'race'
                self.pl.speed = 120.0 if self.count_t > -0.35 else 0.0
            self._camera(dt)
            return True
        if self.phase in ('race', 'finished'):
            self.race_t += dt
            pl = self.pl
            steer = brake = 0.0
            if self.key_t > 0:
                steer = self.key_steer
            elif self.mouse is not None:
                sx, sy = self._world_to_logical(pl.x, pl.y)
                dx, dy = self.mouse[0] - sx, self.mouse[1] - sy
                if math.hypot(dx, dy) > 26:
                    err = _wrap(math.atan2(dy, dx) - pl.a)
                    steer = max(-1.0, min(1.0, err * 2.4))
            if pl.finished:                                       # auto-pilot after the flag
                look = self.track.pos(pl.idx + 10)
                steer = max(-1.0, min(1.0, _wrap(math.atan2(look[1] - pl.y, look[0] - pl.x) - pl.a) * 2.6))
            self._step_kart(pl, dt, steer, self._brake_t > 0 if hasattr(self, '_brake_t') else False)
            for k in self.karts[1:]:
                st, err = self._steer_cpu(k, dt)
                if k.finished:
                    look = self.track.pos(k.idx + 10)
                    st = max(-1.0, min(1.0, _wrap(math.atan2(look[1] - k.y, look[0] - k.x) - k.a) * 2.6))
                self._step_kart(k, dt, st, err > 0.9 and k.speed > 180)
            self._collide_karts()
            self._pickups(dt)
            self._update_rockets(dt)
            self._camera(dt)
            if pl.finished and self.phase == 'race':
                self.phase = 'finished'
                self.score_extra += 0
                self._record()
                self.fin_t = 0.0
            if self.phase == 'finished':
                self.fin_t += dt
            if hasattr(self, '_brake_t'):
                self._brake_t = max(0.0, self._brake_t - dt)
        return True

    def _camera(self, dt):
        pl = self.pl
        lead = 0.28
        tx, ty = pl.x + math.cos(pl.a) * pl.speed * lead, pl.y + math.sin(pl.a) * pl.speed * lead
        k = min(1.0, 5.0 * dt)
        self.cam[0] += (tx - self.cam[0]) * k
        self.cam[1] += (ty - self.cam[1]) * k
        want = 0.95 - 0.16 * min(1.0, pl.speed / 480.0)
        self.zoom += (want - self.zoom) * min(1.0, 2.5 * dt)

    # ------------------------------------------------------------------ input
    def _card(self, i):
        return 70.0, 258.0 - i * 92.0, 500.0, 82.0

    def _acard(self, i):
        return 28.0 + i * 202.0, 90.0, 190.0, 190.0

    def _logical(self, x, y):
        return (x - self.fx0) / self.sc, (y - self.fy0) / self.sc

    def _hit_idx(self, x, y, fn, n):
        lx, ly = self._logical(x, y)
        for i in range(n):
            cx, cy, w, h = fn(i)
            if cx <= lx <= cx + w and cy <= ly <= cy + h:
                return i
        return -1

    def move(self, x, y):
        if x < -1e5:
            self.mouse = None
            return False
        if self.phase == 'menu':
            h = self._hit_idx(x, y, self._card, 3)
        elif self.phase == 'augment':
            h = self._hit_idx(x, y, self._acard, 3)
        else:
            self.mouse = self._logical(x, y)
            return False
        changed = h != self.menu_hover
        self.menu_hover = h
        return changed

    def _pick_track(self, i):
        self.track_i = i
        self.aug_choices = random.sample(AUGMENTS, 3)
        self.phase = 'augment'
        self.menu_hover = -1

    def _pick_aug(self, i):
        self.aug = self.aug_choices[i][0]
        self.phase = 'countdown'
        self.reset()
        self.phase = 'countdown'
        self._apply_aug()

    def click(self, x, y, button):
        if self.phase == 'menu':
            i = self._hit_idx(x, y, self._card, 3)
            if i >= 0 and button == 'LEFT':
                self._pick_track(i)
        elif self.phase == 'augment':
            i = self._hit_idx(x, y, self._acard, 3)
            if i >= 0 and button == 'LEFT':
                self._pick_aug(i)
        elif self.phase == 'race':
            self._use_item(self.pl)
        elif self.phase == 'finished' and self.fin_t > 1.0 and button == 'LEFT':
            self.phase = 'countdown'
            self.reset()
            self.phase = 'countdown'

    def key(self, key, repeat):
        if self.phase == 'menu':
            i = DIGITS.get(key)
            if i is not None:
                self._pick_track(i)
            return
        if self.phase == 'augment':
            i = DIGITS.get(key)
            if i is not None:
                self._pick_aug(i)
            elif key == 'M':
                self.phase = 'menu'
            return
        if key == 'M':
            self.phase = 'menu'
            return
        if key in ('A', 'LEFT_ARROW'):
            self.key_steer, self.key_t = -1.0, 0.35
        elif key in ('D', 'RIGHT_ARROW'):
            self.key_steer, self.key_t = 1.0, 0.35
        elif key in ('S', 'DOWN_ARROW'):
            self._brake_t = 0.3
        elif key == 'SPACE':
            if self.phase == 'race':
                self._use_item(self.pl)
            elif self.phase == 'finished' and self.fin_t > 1.0:
                self.phase = 'countdown'
                self.reset()
                self.phase = 'countdown'

    # ----------------------------------------------------------------- layout
    def layout(self, view):
        cs = self.fit_cell(view, 8, 5, max_cell=100, min_cell=36)
        self.place(view, 8 * cs, 5 * cs)
        self.sc = cs / 80.0
        self.fx0 = self.left
        self.fy0 = self.top - 5 * cs

    # ---------------------------------------------------------------- drawing
    def _w2l(self, x, y):
        """World -> logical field coords (camera centred, y up)."""
        shx = random.uniform(-1, 1) * self.shake if self.shake > 0 else 0.0
        return (W / 2 + (x - self.cam[0]) * self.zoom + shx, H / 2 + (y - self.cam[1]) * self.zoom)

    def _world_to_logical(self, x, y):
        return W / 2 + (x - self.cam[0]) * self.zoom, H / 2 + (y - self.cam[1]) * self.zoom

    def _P(self, lx, ly):
        return self.fx0 + lx * self.sc, self.fy0 + ly * self.sc

    def _wp(self, x, y):
        return self._P(*self._w2l(x, y))

    def _rect(self, c, x, y, w, h, col):
        c.rect(self.fx0 + x * self.sc, self.fy0 + y * self.sc, w * self.sc, h * self.sc, col)

    def _text(self, c, s, x, y, size, col=ov.C_WHITE, align='center'):
        c.text(s, self.fx0 + x * self.sc, self.fy0 + y * self.sc, size * self.sc, col, align)

    def _visible(self, x, y, pad=60.0):
        lx, ly = self._world_to_logical(x, y)
        return -pad < lx < W + pad and -pad < ly < H + pad

    def _draw_world(self, c):
        z, sc = self.zoom, self.sc
        # grass checker
        tile = 140.0
        x0 = math.floor((self.cam[0] - W / 2 / z) / tile) * tile
        y0 = math.floor((self.cam[1] - H / 2 / z) / tile) * tile
        c.rect(self.fx0, self.fy0, W * sc, H * sc, C_GRASS_A)
        nx = int(W / z / tile) + 3
        ny = int(H / z / tile) + 3
        for i in range(nx):
            for j in range(ny):
                if (int(x0 / tile) + i + int(y0 / tile) + j) % 2:
                    wx, wy = x0 + i * tile, y0 + j * tile
                    a = self._w2l(wx, wy)
                    b = self._w2l(wx + tile, wy + tile)
                    lx0, ly0 = max(0.0, a[0]), max(0.0, a[1])
                    lx1, ly1 = min(W, b[0]), min(H, b[1])
                    if lx1 > lx0 and ly1 > ly0:
                        self._rect(c, lx0, ly0, lx1 - lx0, ly1 - ly0, C_GRASS_B)
        tr = self.track
        # trees (behind the track)
        for x, y, r, v in tr.trees:
            if self._visible(x, y, 50):
                px, py = self._wp(x, y)
                rr = r * z * sc
                c.circle(px + rr * 0.3, py - rr * 0.35, rr * 1.0, (0, 0, 0, 0.22), 10)
                c.circle(px, py, rr, (0.10, 0.34 + 0.1 * v, 0.14, 1.0), 12)
                c.circle(px - rr * 0.2, py + rr * 0.25, rr * 0.62, (0.18, 0.50 + 0.1 * v, 0.22, 1.0), 10)
        # track: curb, asphalt, centre line
        vis = [i for i in range(N_PTS) if self._visible(*tr.pts[i], 130)]
        wpx = tr.w * z * sc
        for i in vis:
            a, b = self._wp(*tr.pts[i]), self._wp(*tr.pts[(i + 1) % N_PTS])
            curb = C_CURB_A if (i // 3) % 2 == 0 else C_CURB_B
            c.line(a, b, wpx + 12 * z * sc, curb)
            c.circle(a[0], a[1], (wpx + 12 * z * sc) / 2, curb, 10)
        for i in vis:
            a, b = self._wp(*tr.pts[i]), self._wp(*tr.pts[(i + 1) % N_PTS])
            c.line(a, b, wpx, C_ASPHALT)
            c.circle(a[0], a[1], wpx / 2, C_ASPHALT, 10)
        for i in vis:
            if (i // 4) % 2 == 0:
                a, b = self._wp(*tr.pts[i]), self._wp(*tr.pts[(i + 1) % N_PTS])
                c.line(a, b, 3.0 * z * sc + 0.5, (1, 1, 1, 0.5))
        # start / finish line
        sx, sy = tr.pos(0)
        if self._visible(sx, sy, 120):
            ang = tr.dir[0] + math.pi / 2
            for k in range(-4, 4):
                for r in range(2):
                    col = (1, 1, 1, 1) if (k + r) % 2 == 0 else (0.05, 0.05, 0.07, 1)
                    off = (k + 0.5) * tr.w / 8
                    p = self._wp(sx + math.cos(ang) * off + math.cos(tr.dir[0]) * (r - 0.5) * 9,
                                 sy + math.sin(ang) * off + math.sin(tr.dir[0]) * (r - 0.5) * 9)
                    c.circle(p[0], p[1], 4.6 * z * sc, col, 4)
        # coins
        for n, (idx, off) in enumerate(tr.coins):
            if (n, min(self.pl.lap, LAPS)) in self.coin_got:
                continue
            cx, cy = tr.pos(idx, off)
            if self._visible(cx, cy, 30):
                p = self._wp(cx, cy)
                sq = abs(math.cos(self.time * 5 + n)) * 0.7 + 0.3
                c.circle(p[0], p[1], 6.3 * z * sc, (0.75, 0.55, 0.05, 1.0), 10)
                c.circle(p[0], p[1], 5.0 * z * sc * sq + 0.5, (1.0, 0.85, 0.2, 1.0), 10)
        # item boxes
        for n, (idx, off) in enumerate(tr.boxes):
            if self.box_t.get(n, 0.0) > self.time:
                continue
            bx, by = tr.pos(idx, off)
            if self._visible(bx, by, 40):
                p = self._wp(bx, by)
                r = 10.0 * z * sc
                hue = (self.time * 0.8 + n * 0.17) % 1.0
                col = _hsv(hue)
                c.poly([(p[0], p[1] + r * 1.2), (p[0] + r * 1.2, p[1]), (p[0], p[1] - r * 1.2), (p[0] - r * 1.2, p[1])], col)
                c.poly([(p[0], p[1] + r * 0.7), (p[0] + r * 0.7, p[1]), (p[0], p[1] - r * 0.7), (p[0] - r * 0.7, p[1])], (1, 1, 1, 0.85))
                self._text_px(c, "?", p[0], p[1], r * 1.3, ov.C_DARK)
        # oil
        for o in self.oil:
            if self._visible(o[0], o[1], 30):
                p = self._wp(o[0], o[1])
                r = 14.0 * z * sc
                c.circle(p[0], p[1], r, (0.03, 0.03, 0.06, 0.9), 14)
                c.circle(p[0] - r * 0.25, p[1] + r * 0.25, r * 0.4, (0.35, 0.2, 0.55, 0.7), 10)
        # rockets
        for r in self.rockets:
            p = self._wp(r['x'], r['y'])
            ca, sa = math.cos(r['a']), math.sin(r['a'])
            rs = z * sc
            c.line((p[0] - ca * 16 * rs, p[1] - sa * 16 * rs), (p[0] + ca * 8 * rs, p[1] + sa * 8 * rs), 7 * rs, (0.9, 0.9, 0.95, 1))
            c.circle(p[0] + ca * 9 * rs, p[1] + sa * 9 * rs, 4.2 * rs, (1.0, 0.25, 0.2, 1), 8)
            c.circle(p[0] - ca * 18 * rs, p[1] - sa * 18 * rs, (5 + 3 * math.sin(self.time * 40)) * rs, (1.0, 0.65, 0.15, 0.85), 8)
        # karts (far ones first)
        for k in sorted(self.karts, key=lambda q: -q.y):
            if self._visible(k.x, k.y, 40):
                self._draw_kart(c, k)
        for x, y, t, kind in self.fx:
            if self._visible(x, y, 40):
                p = self._wp(x, y)
                kk = t / 0.6
                col = {'box': (1, 1, 1, 1), 'coin': (1, 0.9, 0.3, 1), 'hit': (1, 0.5, 0.2, 1), 'pop': (0.5, 0.9, 1, 1)}[kind]
                for i in range(6):
                    a = i * math.pi / 3 + 0.4
                    r0, r1 = (6 + 16 * kk) * z * sc, (12 + 30 * kk) * z * sc
                    c.line((p[0] + math.cos(a) * r0, p[1] + math.sin(a) * r0), (p[0] + math.cos(a) * r1, p[1] + math.sin(a) * r1),
                           3.0 * z * sc, (col[0], col[1], col[2], 1 - kk))

    def _text_px(self, c, s, px, py, size, col):
        c.text(s, px, py, size, col)

    def _draw_kart(self, c, k):
        z, sc = self.zoom, self.sc
        p = self._wp(k.x, k.y)
        ca, sa = math.cos(k.a), math.sin(k.a)
        s = z * sc

        def T(lx, ly):
            return (p[0] + (ca * lx - sa * ly) * s, p[1] + (sa * lx + ca * ly) * s)
        c.circle(p[0] + 2 * s, p[1] - 3 * s, 14 * s, (0, 0, 0, 0.30), 12)
        if k.boost > 0:
            fl = 8 + 6 * math.sin(self.time * 50)
            c.poly([T(-12, 5), T(-12 - fl - 8, 0), T(-12, -5)], (1.0, 0.75, 0.2, 0.95))
            c.poly([T(-12, 3), T(-12 - fl, 0), T(-12, -3)], (1.0, 1.0, 0.8, 1.0))
        for wx, wy in ((8, 9), (8, -9), (-9, 9), (-9, -9)):
            c.poly([T(wx - 4.5, wy - 2.5), T(wx + 4.5, wy - 2.5), T(wx + 4.5, wy + 2.5), T(wx - 4.5, wy + 2.5)], (0.05, 0.05, 0.07, 1))
        body = ov.shade(k.col, 0.62)
        c.poly([T(15, 0), T(7, 7.5), T(-10, 8), T(-13, 0), T(-10, -8), T(7, -7.5)], body)
        c.poly([T(13, 0), T(6, 5.8), T(-9, 6.2), T(-11, 0), T(-9, -6.2), T(6, -5.8)], k.col)
        c.poly([T(-11, 8.5), T(-15, 8.5), T(-15, -8.5), T(-11, -8.5)], ov.shade(k.col, 0.5))
        c.poly([T(5, 2), T(11, 0), T(5, -2)], (1, 1, 1, 0.55))
        c.circle(*T(-2, 0), 4.4 * s, (0.96, 0.78, 0.62, 1), 10)
        c.circle(*T(-2.6, 0), 4.7 * s, ov.shade(k.col, 1.2), 10) if False else None
        c.poly([T(-1, 4.2), T(2.5, 3), T(2.5, -3), T(-1, -4.2)], (0.1, 0.1, 0.14, 1))
        if k.shield > 0:
            a = 0.35 + 0.15 * math.sin(self.time * 12)
            blink = k.shield > 1.5 or int(k.shield * 8) % 2 == 0
            if blink:
                c.circle(p[0], p[1], 19 * s, (0.4, 0.85, 1.0, 0.20), 18)
                c.ring(p[0], p[1], 19 * s, 2.2 * s + 0.5, (0.6, 0.95, 1.0, a + 0.4), 20)
        if k.spin > 0:
            for i in range(3):
                an = self.time * 8 + i * 2.1
                c.circle(p[0] + math.cos(an) * 10 * s, p[1] + 17 * s + math.sin(an) * 3 * s, 2.4 * s, (1, 0.95, 0.4, 1), 6)
        if k is self.pl:
            c.poly([(p[0], p[1] + 25 * s), (p[0] - 5 * s, p[1] + 32 * s), (p[0] + 5 * s, p[1] + 32 * s)], (1, 1, 1, 0.9))

    def _draw_minimap(self, c):
        tr = self.track
        xs = [p[0] for p in tr.pts]
        ys = [p[1] for p in tr.pts]
        x0, x1, y0, y1 = min(xs), max(xs), min(ys), max(ys)
        bw, bh = 104.0, 80.0
        k = min(bw / (x1 - x0), bh / (y1 - y0))
        ox, oy = W - bw - 10 + (bw - (x1 - x0) * k) / 2, 12 + (bh - (y1 - y0) * k) / 2
        self._rect(c, W - bw - 16, 6, bw + 12, bh + 12, (0, 0, 0, 0.45))

        def M(x, y):
            return ox + (x - x0) * k, oy + (y - y0) * k
        for i in range(0, N_PTS, 4):
            a, b = M(*tr.pts[i]), M(*tr.pts[(i + 4) % N_PTS])
            c.line((self.fx0 + a[0] * self.sc, self.fy0 + a[1] * self.sc), (self.fx0 + b[0] * self.sc, self.fy0 + b[1] * self.sc),
                   3.0 * self.sc, (0.8, 0.8, 0.9, 0.9))
        for q in reversed(self.karts):
            m = M(q.x, q.y)
            c.circle(self.fx0 + m[0] * self.sc, self.fy0 + m[1] * self.sc, (3.6 if q is self.pl else 2.6) * self.sc,
                     (1, 1, 1, 1) if q is self.pl else q.col, 8)

    def _draw_hud(self, c):
        pl = self.pl
        pos = self._rank(pl)
        suf = {1: "st", 2: "nd", 3: "rd"}.get(pos, "th")
        col = ov.C_GOLD if pos == 1 else ov.C_WHITE
        self._text(c, "%d%s" % (pos, suf), 44, 362, 34, col)
        self._text(c, "/ %d" % N_KARTS, 44, 338, 11, ov.C_TEXT)
        lap = min(LAPS, max(1, pl.lap + 1)) if pl.prog >= 0 else 1
        self._text(c, "LAP %d/%d" % (lap, LAPS), 120, 370, 15, ov.C_WHITE, 'left')
        self._text(c, _fmt(pl.finished if pl.finished else self.race_t), 120, 349, 13, ov.C_TEXT, 'left')
        self._text(c, "COINS %d" % self.coins, 120, 330, 11, ov.C_GOLD, 'left')
        # item slot
        self._rect(c, 12, 12, 62, 62, (0, 0, 0, 0.45))
        self._rect(c, 14, 14, 58, 58, (0.12, 0.13, 0.17, 0.95))
        if pl.item:
            self._item_icon(c, pl.item, 43, 43, 20)
            self._text(c, "SPACE", 43, 20, 8, ov.C_TEXT)
        else:
            self._text(c, "ITEM", 43, 43, 10, (0.45, 0.48, 0.55, 1.0))
        # speed bar
        sp = min(1.0, pl.speed / (TOP_SPEED * 1.55))
        self._rect(c, 84, 14, 130, 8, (0, 0, 0, 0.5))
        self._rect(c, 85, 15, 128 * sp, 6, (0.4 + 0.6 * sp, 0.9 - 0.4 * sp, 0.3, 1))
        self._text(c, "SCORE %d" % self._score(), 84, 36, 12, ov.C_GOLD, 'left')
        if self.aug:
            self._text(c, [a[1] for a in AUGMENTS if a[0] == self.aug][0], 84, 52, 9, ov.C_TEXT, 'left')
        self._draw_minimap(c)
        if self.msg_t > 0:
            self._text(c, self.msg, W / 2, 330, 18, ov.alpha(ov.C_GOLD, min(1.0, self.msg_t * 2)))

    def _item_icon(self, c, it, x, y, r):
        s = self.sc
        px, py = self.fx0 + x * s, self.fy0 + y * s
        R = r * s
        if it == 'boost':
            c.poly([(px - R * 0.3, py + R), (px + R * 0.7, py + R * 0.05), (px + R * 0.05, py + R * 0.05)], (1.0, 0.8, 0.2, 1))
            c.poly([(px + R * 0.3, py - R), (px - R * 0.7, py - R * 0.05), (px - R * 0.05, py - R * 0.05)], (1.0, 0.55, 0.15, 1))
        elif it == 'shield':
            c.poly([(px - R * 0.8, py + R * 0.8), (px + R * 0.8, py + R * 0.8), (px + R * 0.8, py - R * 0.1),
                    (px, py - R), (px - R * 0.8, py - R * 0.1)], (0.45, 0.85, 1.0, 1))
            c.poly([(px - R * 0.45, py + R * 0.5), (px + R * 0.45, py + R * 0.5), (px + R * 0.45, py - R * 0.05),
                    (px, py - R * 0.6), (px - R * 0.45, py - R * 0.05)], (0.8, 0.95, 1.0, 1))
        elif it == 'oil':
            c.circle(px, py, R, (0.05, 0.05, 0.09, 1), 16)
            c.circle(px - R * 0.3, py + R * 0.3, R * 0.35, (0.45, 0.3, 0.7, 1), 10)
        elif it == 'rocket':
            c.line((px - R, py - R * 0.7), (px + R * 0.5, py + R * 0.5), R * 0.55, (0.9, 0.9, 0.95, 1))
            c.circle(px + R * 0.7, py + R * 0.7, R * 0.33, (1.0, 0.25, 0.2, 1), 8)
            c.circle(px - R * 1.05, py - R * 0.75, R * 0.4, (1.0, 0.65, 0.15, 1), 8)

    def _menu(self, c):
        self._rect(c, 0, 0, W, H, (0.05, 0.06, 0.10, 1))
        for k in range(10):
            self._rect(c, 0, k * 42, W, 20, (1, 1, 1, 0.012))
        hue = _hsv((self.time * 0.2) % 1.0)
        self._text(c, "NEON KART", W / 2, 362, 40, hue)
        self._text(c, "Pick a track   -   %d laps, %d karts" % (LAPS, N_KARTS), W / 2, 332, 13, ov.C_TEXT)
        for i, t in enumerate(TRACKS):
            x, y, w, h = self._card(i)
            self._rect(c, x + 3, y - 4, w, h, (0, 0, 0, 0.4))
            self._rect(c, x, y, w, h, ov.C_CELL_HOVER if i == self.menu_hover else ov.C_CELL)
            self._rect(c, x, y, 9, h, (0.4, 0.9, 0.5, 1.0) if i == 0 else (1.0, 0.65, 0.25, 1.0) if i == 1 else (0.95, 0.3, 0.5, 1.0))
            self._text(c, "%d  %s" % (i + 1, t['name']), x + 24, y + h - 24, 20, ov.C_WHITE, 'left')
            self._text(c, t['info'], x + 24, y + 22, 11, ov.C_TEXT, 'left')
            bs, bt = self._best(i)
            if bs:
                self._text(c, "Best score  %d" % bs, x + w - 14, y + h - 26, 13, ov.C_GOLD, 'right')
                self._text(c, "Best time  %s" % _fmt(bt), x + w - 14, y + 22, 12, ov.C_GOLD, 'right')
            else:
                self._text(c, "No podium yet", x + w - 14, y + h - 26, 12, ov.C_TEXT, 'right')
            tr = self.tracks[i]
            xs = [p[0] for p in tr.pts]
            ys = [p[1] for p in tr.pts]
            kx = 70.0 / (max(xs) - min(xs))
            ky = 56.0 / (max(ys) - min(ys))
            kk = min(kx, ky)
            for j in range(0, N_PTS, 6):
                a, b = tr.pts[j], tr.pts[(j + 6) % N_PTS]
                pa = (x + w - 100 + (a[0] - min(xs)) * kk, y + 10 + (a[1] - min(ys)) * kk)
                pb = (x + w - 100 + (b[0] - min(xs)) * kk, y + 10 + (b[1] - min(ys)) * kk)
                c.line((self.fx0 + pa[0] * self.sc, self.fy0 + pa[1] * self.sc), (self.fx0 + pb[0] * self.sc, self.fy0 + pb[1] * self.sc),
                       2.2 * self.sc, (0.8, 0.82, 0.9, 0.8))

    def _augment_screen(self, c):
        self._rect(c, 0, 0, W, H, (0.05, 0.06, 0.10, 1))
        self._text(c, "CHOOSE YOUR AUGMENT", W / 2, 350, 26, ov.C_GOLD)
        self._text(c, "%s  -  one upgrade for the whole race" % TRACKS[self.track_i]['name'], W / 2, 320, 12, ov.C_TEXT)
        for i, (key, name, desc) in enumerate(self.aug_choices):
            x, y, w, h = self._acard(i)
            hov = i == self.menu_hover
            self._rect(c, x + 3, y - 4, w, h, (0, 0, 0, 0.4))
            self._rect(c, x - (3 if hov else 0), y - (3 if hov else 0), w + (6 if hov else 0), h + (6 if hov else 0),
                       ov.C_CELL_HOVER if hov else ov.C_CELL)
            ic = {'turbo': (1.0, 0.65, 0.2, 1), 'grip': (0.4, 0.9, 0.5, 1), 'magnet': (1.0, 0.85, 0.25, 1),
                  'armor': (0.45, 0.85, 1.0, 1), 'luck': (0.85, 0.5, 1.0, 1)}[key]
            self._rect(c, x, y + h - 6, w, 6, ic)
            cx, cy = x + w / 2, y + h - 62
            px, py = self.fx0 + cx * self.sc, self.fy0 + cy * self.sc
            R = 24 * self.sc
            if key == 'turbo':
                self._item_icon(c, 'boost', cx, cy, 26)
            elif key == 'armor':
                self._item_icon(c, 'shield', cx, cy, 26)
            elif key == 'magnet':
                c.ring(px, py, R * 0.8, 7 * self.sc, ic, 20)
                c.circle(px, py, R * 0.4, (1.0, 0.85, 0.2, 1), 12)
            elif key == 'grip':
                for k in range(3):
                    c.line((px - R * 0.9 + k * R * 0.9, py - R * 0.8), (px - R * 0.4 + k * R * 0.9, py + R * 0.8), 6 * self.sc, ic)
            else:
                c.poly([(px, py + R), (px + R, py), (px, py - R), (px - R, py)], ic)
                self._text(c, "?", cx, cy, 22, ov.C_DARK)
            self._text(c, "%d  %s" % (i + 1, name), x + w / 2, y + 78, 14, ov.C_WHITE)
            self._text(c, desc, x + w / 2, y + 52, 9.5, ov.C_TEXT)
            self._text(c, "click or press %d" % (i + 1), x + w / 2, y + 18, 9, (0.5, 0.52, 0.6, 1))

    def draw(self, c):
        self.draw_frame(c)
        bs, bt = self._best(self.track_i)
        if self.phase == 'menu':
            self.draw_header(c, "NEON KART", "Best score and best time saved for every track", ov.C_GOLD)
            self._menu(c)
            self.draw_hint(c, "Click or press 1-3 to choose a track   ESC quit")
            return
        if self.phase == 'augment':
            self.draw_header(c, "NEON KART", "Augment draft", ov.C_GOLD)
            self._augment_screen(c)
            self.draw_hint(c, "Click or press 1-3   M back to tracks")
            return
        self.draw_header(c, "%s" % self.cfg['name'],
                         "Score %d   |   Best %d   |   Best time %s" % (self._score(), bs, _fmt(bt)), ov.C_GOLD)
        self._draw_world(ClipCanvas(c, self.fx0, self.fy0, W * self.sc, H * self.sc))
        self._draw_hud(c)
        if self.phase == 'countdown':
            n = int(math.ceil(self.count_t - 0.2))
            self._text(c, str(n) if n > 0 else "GO!", W / 2, 230, 70 if n > 0 else 80,
                       ov.C_WHITE if n > 0 else (0.4, 1.0, 0.5, 1.0))
        if self.phase == 'finished':
            self._rect(c, 0, 0, W, H, (0, 0, 0, 0.55))
            f = self.final
            pos = f['pos']
            self._text(c, ("%d%s PLACE" % (pos, {1: 'ST', 2: 'ND', 3: 'RD'}.get(pos, 'TH'))), W / 2, 318, 44,
                       ov.C_GOLD if pos <= 3 else ov.C_TEXT)
            self._text(c, "PODIUM!" if f['podium'] else "Missed the podium", W / 2, 280, 15, ov.C_GOOD if f['podium'] else ov.C_BAD)
            rows = [("Position", POINTS_FOR_POS[pos - 1]), ("Coins x%d" % self.coins, self.coins * (37 if self.aug == 'magnet' else 25)),
                    ("Hits x%d" % self.hits, self.hits * 120), ("Time bonus", int(max(0.0, 100.0 - self.pl.finished) * 10))]
            for i, (n, v) in enumerate(rows):
                self._text(c, n, 230, 238 - i * 22, 13, ov.C_TEXT, 'left')
                self._text(c, "+%d" % v, 410, 238 - i * 22, 13, ov.C_WHITE, 'right')
            self._text(c, "SCORE  %d%s" % (f['score'], "   NEW BEST!" if self.new_best_score else ""), W / 2, 140, 22, ov.C_GOLD)
            self._text(c, "Time  %s%s" % (_fmt(self.pl.finished), "   NEW BEST TIME!" if self.new_best_time else ""), W / 2, 112, 13,
                       ov.C_GOLD if self.new_best_time else ov.C_TEXT)
            self._text(c, "SPACE / click: race again     M: tracks", W / 2, 70, 12, ov.C_TEXT)
        else:
            self.draw_hint(c, "Mouse / A D steer   S brake   SPACE / click item   M tracks   R restart")


def _hsv(h):
    i = int(h * 6) % 6
    f = h * 6 - int(h * 6)
    p, q, t = 0.35, 1.0 - 0.65 * f, 0.35 + 0.65 * f
    return [(1.0, t, p, 1.0), (q, 1.0, p, 1.0), (p, 1.0, t, 1.0), (p, q, 1.0, 1.0), (t, p, 1.0, 1.0), (1.0, p, q, 1.0)][i]


RUNNER = ov.Runner("neonkart", GAME_NAME, NeonKart, [
    "Mouse (or A / D): steer - the kart accelerates by itself",
    "S: brake    SPACE / click: use item",
    "Rainbow boxes: Boost, Rocket, Oil, Shield",
    "Pick 1 of 3 augments before every race",
    "Podium = win: best score + best time saved per track",
    "M: tracks   R: restart race   ESC: quit",
])


def draw_ui(layout, context):
    RUNNER.draw_ui(layout)


def register():
    RUNNER.register()


def unregister():
    RUNNER.unregister()
