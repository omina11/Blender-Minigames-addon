"""Desert Rush - a run-and-gun side scroller in the spirit of the arcade classics (GPU overlay).

Fight your way through the desert: soldiers, knife rushers, mortars, drones and
tanks, rescue the prisoners (POWs) for bonus points and weapons, then bring down
the gunship at the end. One hit kills, you have a few lives.

    A / D (hold)   run          SPACE / W   jump
    Mouse          aim          click / J   start / stop firing (the gun fires by itself while ON)
    G / right click   throw a grenade        M menu

Three difficulties; the best score and the fastest mission clear are saved for
each one (rush_easy / rush_medium / rush_hard).
"""

import math
import random
import traceback

from . import _overlay as ov
from . import _record as _rec

GAME_NAME = "Desert Rush"
GAME_ICON = 'PLAY'

W, H = 640.0, 400.0
GROUND = 70.0
LEVEL_LEN = 6000.0
GRAV = 1150.0
JUMP_V = 440.0
RUN = 150.0

DIFFS = [
    dict(name="Easy", info="5 lives, slower enemies, fewer of them", lives=5, spd=0.85, fire=1.45, dens=0.75,
         hp=0.85, boss=0.8, mult=1.0, col=(0.45, 0.85, 0.45)),
    dict(name="Medium", info="3 lives, classic mission", lives=3, spd=1.0, fire=1.0, dens=1.0,
         hp=1.0, boss=1.0, mult=1.5, col=(1.0, 0.65, 0.25)),
    dict(name="Hard", info="2 lives, fast and aggressive enemies, two tanks", lives=2, spd=1.18, fire=0.68, dens=1.35,
         hp=1.2, boss=1.3, mult=2.0, col=(0.95, 0.30, 0.30)),
]
DIGITS = {'ONE': 0, 'TWO': 1, 'THREE': 2, 'NUMPAD_1': 0, 'NUMPAD_2': 1, 'NUMPAD_3': 2}

WEAPONS = {
    'pistol': dict(rate=0.26, dmg=1, spread=0.0, n=1, speed=560.0, life=0.9, name="PISTOL"),
    'mg': dict(rate=0.075, dmg=1, spread=0.06, n=1, speed=620.0, life=0.9, name="HEAVY MG"),
    'shotgun': dict(rate=0.50, dmg=1, spread=0.30, n=5, speed=520.0, life=0.42, name="SHOTGUN"),
}

KINDS = {
    'rifle': dict(hp=2, w=16, h=34, score=100, speed=42.0, fire=2.1),
    'rusher': dict(hp=1, w=16, h=34, score=80, speed=135.0, fire=0.0),
    'mortar': dict(hp=3, w=20, h=30, score=150, speed=0.0, fire=3.1),
    'drone': dict(hp=2, w=26, h=16, score=200, speed=80.0, fire=2.6),
    'tank': dict(hp=20, w=84, h=42, score=1000, speed=24.0, fire=2.6),
    'crate': dict(hp=3, w=26, h=24, score=50, speed=0.0, fire=0.0),
    'boss': dict(hp=95, w=120, h=50, score=5000, speed=0.0, fire=1.8),
}

SKY = ((0.98, 0.62, 0.34), (0.95, 0.42, 0.36), (0.45, 0.28, 0.52), (0.16, 0.14, 0.34))


def _fmt(t):
    return "--" if t is None else "%d:%02d" % divmod(int(round(t)), 60)


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def _build_level(cfg, seed):
    """[(x, kind, extra)] sorted by x."""
    rng = random.Random(seed)
    ev = []
    dens = cfg['dens']
    x = 520.0
    while x < LEVEL_LEN - 900:
        n = int(round(rng.uniform(1.4, 3.0) * dens))
        for k in range(max(1, n)):
            kind = rng.choices(['rifle', 'rusher', 'rifle', 'drone' if x > 1800 else 'rusher'], k=1)[0]
            ev.append((x + k * rng.uniform(70, 130), kind, {}))
        if rng.random() < 0.5:
            ev.append((x + 190, 'crate', {}))
        x += rng.uniform(300, 430) / (0.8 + 0.2 * dens)
    for px in (1250, 2650, 3900, 4900):
        ev.append((px, 'pow', {}))
    for mx in (3300, 4300) if dens > 0.9 else (3700,):
        ev.append((mx, 'mortar', {}))
    ev.append((2900, 'tank', {}))
    if dens > 1.2:
        ev.append((4500, 'tank', {}))
    ev.append((LEVEL_LEN - 520, 'boss', {}))
    ev.sort(key=lambda e: e[0])
    return ev


class Player:
    def __init__(self):
        self.x, self.y, self.vy = 80.0, 0.0, 0.0
        self.face = 1
        self.lives = 3
        self.inv = 0.0
        self.weapon, self.ammo = 'pistol', 0
        self.gren = 8
        self.walk, self.walk_t = 0, 0.0
        self.fire_cd = 0.0
        self.anim = 0.0
        self.kick = 0.0
        self.dead_t = 0.0
        self.aim = 0.0


class DesertRush(ov.BaseGame):
    keys = ('A', 'D', 'W', 'SPACE', 'J', 'G', 'F', 'M', 'LEFT_ARROW', 'RIGHT_ARROW', 'UP_ARROW',
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
        self.mouse = (W * 0.7, 200.0)
        self.firing = True                 # click / J toggles it
        rng = random.Random(21)
        self.ruins = [(x, rng.uniform(60, 150), rng.uniform(40, 90), rng.random()) for x in range(0, 1400, 70)]
        self.dunes = [(x, rng.uniform(20, 50), rng.uniform(140, 260)) for x in range(-200, 1800, 130)]
        self.stars = [(rng.uniform(0, 1200), rng.uniform(250, 395), rng.random() * 6) for _ in range(35)]
        self.props = [(x, rng.choice(('barrel', 'sandbag', 'sandbag', 'cactus'))) for x in range(300, int(LEVEL_LEN), 170)]

    def reset(self):
        self.cfg = DIFFS[self.diff]
        self.p = Player()
        self.p.lives = self.cfg['lives']
        self.cam = 0.0
        self.cam_max = LEVEL_LEN - W
        self.events = _build_level(self.cfg, 100 + self.diff)
        self.ev_i = 0
        self.enemies = []
        self.pows = []
        self.items = []
        self.bullets = []          # player bullets
        self.ebullets = []         # enemy bullets
        self.grens = []
        self.booms = []
        self.smoke = []
        self.casings = []
        self.score = 0
        self.kills = 0
        self.pow_saved = 0
        self.clear_t = 0.0
        self.msg, self.msg_t = "MISSION START", 2.0
        self.phase = 'play'          # play | dying | clear | over
        self.timer = 0.0
        self.boss = None
        self.boss_dead_t = 0.0
        self.shake = 0.0
        self.flash = 0.0
        self.recorded = False
        self.new_best_score = self.new_best_time = False
        self.final = {}
        self.respawn_x = 80.0

    # ---------------------------------------------------------------- records
    def _mgr(self, d):
        if d not in self.record_mgrs:
            try:
                self.record_mgrs[d] = _rec.RecordManager(game_name="desertrush_" + DIFFS[d]['name'].lower())
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

    def _finish(self, won):
        if self.recorded:
            return
        self.recorded = True
        p = self.p
        bonus = 0
        if won:
            bonus = int((max(0.0, 300.0 - self.clear_t) * 5 + p.lives * 500) * self.cfg['mult'])
            self.score += bonus
        bs, bt = self._best(self.diff)
        self.final = dict(won=won, bonus=bonus, score=self.score)
        if won:
            self.new_best_score = self.score > bs
            self.new_best_time = bt is None or self.clear_t < bt
        m = self._mgr(self.diff)
        if not m:
            return
        try:
            if won:
                m.add_win_record(score=int(self.score), best_time=self.clear_t)
            else:
                m.add_lose_record()
            self.best_cache.pop(self.diff, None)
        except Exception:
            traceback.print_exc()

    # ------------------------------------------------------------------ spawn
    def _spawn(self, x, kind):
        cfg = self.cfg
        if kind == 'pow':
            self.pows.append(dict(x=x, t=random.random() * 6, freed=0.0))
            return
        k = KINDS[kind]
        e = dict(kind=kind, x=x, y=GROUND, vx=0.0, hp=max(1, int(round(k['hp'] * cfg['hp']))) if kind != 'boss' else int(k['hp'] * cfg['boss']),
                 t=random.random() * 3, fire_t=random.uniform(0.8, 1.8) * cfg['fire'], anim=random.random() * 6,
                 hit=0.0, face=-1, state='walk', var=random.random())
        e['hp_max'] = e['hp']
        if kind == 'drone':
            e['y'] = random.uniform(190, 280)
            e['x0'] = x
        elif kind == 'boss':
            e['y'] = 250.0
            e['x'] = self.cam + W - 150
            self.boss = e
            self.cam_max = self.cam                    # lock the screen
            self.msg, self.msg_t = "WARNING!  GUNSHIP", 2.4
        elif kind == 'tank':
            e['y'] = GROUND
        self.enemies.append(e)

    # ------------------------------------------------------------------ logic
    def _hurt_player(self):
        p = self.p
        if p.inv > 0 or self.phase != 'play':
            return
        p.lives -= 1
        self.phase, self.timer = 'dying', 1.6
        self.booms.append([p.x, GROUND + p.y + 18, 0.0, 28.0])
        self.shake = 8.0
        p.vy = 300.0

    def _explode(self, x, y, r, dmg, by_player=True):
        self.booms.append([x, y, 0.0, r])
        self.shake = max(self.shake, 5.0 + r * 0.05)
        for e in self.enemies:
            if e['kind'] in ('crate',) and False:
                continue
            ex, ey = e['x'], e['y'] + KINDS[e['kind']]['h'] / 2
            if math.hypot(ex - x, ey - y) < r + KINDS[e['kind']]['w'] / 2:
                self._damage(e, dmg)
        if not by_player:
            p = self.p
            if math.hypot(p.x - x, GROUND + p.y + 17 - y) < r * 0.8:
                self._hurt_player()

    def _damage(self, e, dmg):
        if e['hp'] <= 0:
            return
        e['hp'] -= dmg
        e['hit'] = 0.1
        if e['hp'] <= 0:
            self._kill(e)

    def _kill(self, e):
        k = KINDS[e['kind']]
        self.score += k['score']
        self.kills += 1
        h = k['h']
        big = e['kind'] in ('tank', 'boss')
        self.booms.append([e['x'], e['y'] + h / 2, 0.0, 46.0 if big else 20.0])
        if e['kind'] == 'crate':
            self.items.append(dict(x=e['x'], y=GROUND + 6, kind=random.choice(['mg', 'shotgun', 'gren', 'gren']), t=0.0))
        elif e['kind'] == 'boss':
            self.boss_dead_t = 0.01
            for i in range(8):
                self.booms.append([e['x'] + random.uniform(-50, 50), e['y'] + random.uniform(-20, 30), -i * 0.18, random.uniform(22, 44)])
        elif e['kind'] == 'tank':
            self.items.append(dict(x=e['x'], y=GROUND + 6, kind='mg', t=0.0))
            self.shake = 10.0
        elif random.random() < 0.08:
            self.items.append(dict(x=e['x'], y=GROUND + 6, kind='gren', t=0.0))

    def _fire(self):
        p = self.p
        if p.fire_cd > 0 or self.phase != 'play':
            return
        w = WEAPONS[p.weapon]
        gx, gy = p.x + p.face * 14, GROUND + p.y + 22
        ang = p.aim
        for _ in range(w['n']):
            a = ang + random.uniform(-w['spread'], w['spread'])
            self.bullets.append(dict(x=gx, y=gy, vx=math.cos(a) * w['speed'], vy=math.sin(a) * w['speed'], t=0.0,
                                     life=w['life'], dmg=w['dmg']))
        p.fire_cd = w['rate']
        p.kick = 0.08
        self.casings.append([gx - p.face * 4, gy, -p.face * random.uniform(30, 70), random.uniform(80, 140), 0.0])
        if p.weapon != 'pistol':
            p.ammo -= 1
            if p.ammo <= 0:
                p.weapon = 'pistol'
                self.msg, self.msg_t = "Out of ammo", 0.8

    def _toggle_fire(self):
        if self.phase != 'play':
            return
        self.firing = not self.firing
        self.msg, self.msg_t = "Firing %s" % ("ON" if self.firing else "OFF"), 0.9

    def _throw(self):
        p = self.p
        if p.gren <= 0 or self.phase != 'play':
            return
        p.gren -= 1
        mx = self.cam + self.mouse[0]
        dx = _clamp(mx - p.x, -300, 300)
        self.grens.append(dict(x=p.x + p.face * 10, y=GROUND + p.y + 24, vx=dx * 0.95 if abs(dx) > 40 else p.face * 150.0,
                               vy=300.0, t=0.0))

    def _enemy_shoot(self, e, kind='bullet', speed=230.0, ox=0.0, oy=24.0):
        p = self.p
        sx, sy = e['x'] + ox, e['y'] + oy
        tx, ty = p.x, GROUND + p.y + 18
        d = math.hypot(tx - sx, ty - sy) or 1.0
        sp = speed * self.cfg['spd']
        self.ebullets.append(dict(x=sx, y=sy, vx=(tx - sx) / d * sp, vy=(ty - sy) / d * sp, kind=kind, t=0.0))

    def _update_enemy(self, e, dt):
        p, cfg = self.p, self.cfg
        k = KINDS[e['kind']]
        e['t'] += dt
        e['anim'] += dt * 8
        e['hit'] = max(0.0, e['hit'] - dt)
        dx = p.x - e['x']
        e['face'] = 1 if dx > 0 else -1
        spd = k['speed'] * cfg['spd']
        kind = e['kind']
        e['fire_t'] -= dt
        if kind == 'rifle':
            dist = abs(dx)
            if dist > 280:
                e['x'] += e['face'] * spd * dt
                e['state'] = 'walk'
            elif dist < 150:
                e['x'] -= e['face'] * spd * 0.8 * dt
                e['state'] = 'walk'
            else:
                e['state'] = 'aim'
            if e['fire_t'] <= 0 and dist < 420 and self.phase == 'play':
                e['fire_t'] = k['fire'] * cfg['fire'] * random.uniform(0.8, 1.3)
                self._enemy_shoot(e, speed=215.0, ox=e['face'] * 12, oy=22)
                e['flash'] = 0.08
        elif kind == 'rusher':
            if abs(dx) < 300:
                e['x'] += e['face'] * spd * dt
                e['state'] = 'run'
            else:
                e['x'] += e['face'] * spd * 0.3 * dt
            if abs(dx) < 20 and p.y < 28:
                self._hurt_player()
        elif kind == 'mortar':
            if e['fire_t'] <= 0 and abs(dx) < 520 and self.phase == 'play':
                e['fire_t'] = k['fire'] * cfg['fire'] * random.uniform(0.9, 1.3)
                tx = p.x + random.uniform(-30, 30)
                T = 1.15
                self.ebullets.append(dict(x=e['x'], y=e['y'] + 30, vx=(tx - e['x']) / T, vy=0.5 * 700.0 * T - 0.0,
                                          kind='mortar', t=0.0))
        elif kind == 'drone':
            e['x'] += e['face'] * spd * dt * 0.9
            e['y'] = e['y'] + math.sin(e['t'] * 2.2) * 24 * dt
            if e['fire_t'] <= 0 and abs(dx) < 70 and self.phase == 'play':
                e['fire_t'] = k['fire'] * cfg['fire']
                self.ebullets.append(dict(x=e['x'], y=e['y'] - 4, vx=0.0, vy=-40.0, kind='bomb', t=0.0))
        elif kind == 'tank':
            if abs(dx) > 230:
                e['x'] += e['face'] * spd * dt
            if e['fire_t'] <= 0 and abs(dx) < 520 and self.phase == 'play':
                e['fire_t'] = k['fire'] * cfg['fire'] * random.uniform(0.9, 1.2)
                self._enemy_shoot(e, 'shell', 260.0, e['face'] * 40, 34)
                e['flash'] = 0.12
            e['mg_t'] = e.get('mg_t', 0.0) - dt
            if e['mg_t'] <= 0 and abs(dx) < 380 and self.phase == 'play':
                e['mg_t'] = 0.55 * cfg['fire']
                self._enemy_shoot(e, 'bullet', 230.0, e['face'] * 10, 40)
        elif kind == 'boss':
            e['y'] = 250.0 + math.sin(e['t'] * 0.9) * 36
            e['x'] = self.cam + W - 150 + math.sin(e['t'] * 0.55) * 90
            enraged = e['hp'] < e['hp_max'] * 0.5
            if e['fire_t'] <= 0 and self.phase == 'play':
                e['fire_t'] = (1.5 if enraged else 2.1) * cfg['fire']
                for a in (-0.22, 0.0, 0.22):
                    sx, sy = e['x'] - 40, e['y'] - 10
                    ang = math.atan2(GROUND + p.y + 18 - sy, p.x - sx) + a
                    sp = 230.0 * cfg['spd']
                    self.ebullets.append(dict(x=sx, y=sy, vx=math.cos(ang) * sp, vy=math.sin(ang) * sp, kind='bullet', t=0.0))
            e['mg_t'] = e.get('mg_t', 4.0) - dt
            if e['mg_t'] <= 0 and self.phase == 'play':
                e['mg_t'] = (4.2 if enraged else 6.0) * cfg['fire']
                for i in range(3 if not enraged else 5):
                    mx = p.x + random.uniform(-160, 160)
                    self.ebullets.append(dict(x=mx, y=430.0 + i * 55, vx=0.0, vy=-170.0 * cfg['spd'], kind='missile', t=0.0))
        e['x'] = _clamp(e['x'], self.cam - 100, LEVEL_LEN)

    # ----------------------------------------------------------------- update
    def update(self, dt):
        self.time += dt
        if self.menu:
            return True
        dt = min(dt, 0.05)
        p = self.p
        self.msg_t = max(0.0, self.msg_t - dt)
        self.shake = max(0.0, self.shake - 24.0 * dt)
        self.flash = max(0.0, self.flash - dt)
        for b in self.booms:
            b[2] += dt
        self.booms = [b for b in self.booms if b[2] < 0.55]
        for s in self.smoke:
            s[0] += s[3] * dt
            s[1] += s[4] * dt
            s[2] += dt
        self.smoke = [s for s in self.smoke if s[2] < 1.0]
        for c in self.casings:
            c[0] += c[2] * dt
            c[1] += c[3] * dt
            c[3] -= 600 * dt
            c[4] += dt
        self.casings = [c for c in self.casings if c[4] < 0.5]
        if self.phase == 'over' or (self.phase == 'clear' and self.timer > 3.0 and False):
            return True
        if self.phase == 'clear':
            self.timer += dt
            self._world(dt, player_alive=False)
            return True
        if self.phase == 'dying':
            self.timer -= dt
            self._world(dt, player_alive=False)
            if self.timer <= 0:
                if p.lives <= 0:
                    self.phase = 'over'
                    self._finish(False)
                else:
                    self.phase = 'play'
                    p.x = max(self.cam + 70, p.x - 40)
                    p.y, p.vy = 0.0, 0.0
                    p.inv = 2.6
                    p.weapon = 'pistol'
            return True
        self.clear_t += dt
        self._player(dt)
        self._world(dt, player_alive=True)
        return True

    def _player(self, dt):
        p = self.p
        p.inv = max(0.0, p.inv - dt)
        p.fire_cd = max(0.0, p.fire_cd - dt)
        p.kick = max(0.0, p.kick - dt)
        if p.walk_t > 0:
            p.walk_t -= dt
            if p.walk_t <= 0:
                p.walk = 0
        px, py = self.cam + self.mouse[0], self.mouse[1]
        p.face = 1 if px >= p.x else -1
        gy = GROUND + p.y + 22
        p.aim = math.atan2(py - gy, px - (p.x + p.face * 14))
        if p.y <= 0 and p.aim < -0.35:
            p.aim = -0.35 if p.face > 0 else -math.pi + 0.35 if p.aim < 0 else p.aim
        if p.walk:
            p.x += p.walk * RUN * dt
            p.anim += dt * 11
        else:
            p.anim += dt * 2
        p.x = _clamp(p.x, self.cam + 22, self.cam + W - 22)
        if p.y > 0 or p.vy > 0:
            p.vy -= GRAV * dt
            p.y += p.vy * dt
            if p.y <= 0:
                p.y, p.vy = 0.0, 0.0
        if self.firing:
            self._fire()
        # pickups
        for it in self.items:
            it['t'] += dt
            if abs(it['x'] - p.x) < 20 and p.y < 30:
                if it['kind'] == 'mg':
                    p.weapon, p.ammo = 'mg', 140
                    self.msg, self.msg_t = "HEAVY MACHINE GUN!", 1.2
                elif it['kind'] == 'shotgun':
                    p.weapon, p.ammo = 'shotgun', 28
                    self.msg, self.msg_t = "SHOTGUN!", 1.2
                else:
                    p.gren += 6
                    self.msg, self.msg_t = "+6 GRENADES", 1.0
                self.score += 50
                it['kind'] = None
        self.items = [i for i in self.items if i['kind']]
        for pw in self.pows:
            pw['t'] += dt
            if pw['freed'] > 0:
                pw['freed'] += dt
            elif abs(pw['x'] - p.x) < 24 and p.y < 40:
                pw['freed'] = 0.01
                self.pow_saved += 1
                self.score += 500
                self.msg, self.msg_t = "PRISONER RESCUED!  +500", 1.4
                self.items.append(dict(x=pw['x'] + 24, y=GROUND + 6, kind=random.choice(['mg', 'shotgun', 'gren']), t=0.0))
        self.pows = [w for w in self.pows if w['freed'] < 2.2]
        # camera
        target = p.x - 220
        if target > self.cam:
            self.cam = min(self.cam_max, target)
        self.cam = _clamp(self.cam, 0.0, self.cam_max if self.boss is None else self.cam_max)

    def _world(self, dt, player_alive):
        p, cfg = self.p, self.cfg
        # spawns
        while self.ev_i < len(self.events) and self.events[self.ev_i][0] <= self.cam + W + 50:
            x, kind, _ = self.events[self.ev_i]
            self._spawn(x, kind)
            self.ev_i += 1
        # player bullets
        for b in self.bullets:
            b['x'] += b['vx'] * dt
            b['y'] += b['vy'] * dt
            b['t'] += dt
        keep = []
        for b in self.bullets:
            if b['t'] > b['life'] or b['y'] < GROUND - 2 or b['y'] > H + 20 or b['x'] < self.cam - 30 or b['x'] > self.cam + W + 30:
                continue
            hit = False
            for e in self.enemies:
                k = KINDS[e['kind']]
                if e['hp'] > 0 and abs(b['x'] - e['x']) < k['w'] / 2 and e['y'] - 2 <= b['y'] <= e['y'] + k['h'] + 2:
                    self._damage(e, b['dmg'])
                    self.smoke.append([b['x'], b['y'], 0.0, random.uniform(-20, 20), random.uniform(10, 40)])
                    hit = True
                    break
            if not hit:
                keep.append(b)
        self.bullets = keep
        # grenades
        for g in list(self.grens):
            g['t'] += dt
            g['vy'] -= 800.0 * dt
            g['x'] += g['vx'] * dt
            g['y'] += g['vy'] * dt
            boom = g['y'] <= GROUND
            if not boom:
                for e in self.enemies:
                    k = KINDS[e['kind']]
                    if e['hp'] > 0 and abs(g['x'] - e['x']) < k['w'] / 2 and e['y'] <= g['y'] <= e['y'] + k['h']:
                        boom = True
                        break
            if boom or g['t'] > 3.0:
                self.grens.remove(g)
                self._explode(g['x'], max(GROUND, g['y']), 58.0, 6, True)
        # enemies
        for e in self.enemies:
            if e['hp'] > 0:
                self._update_enemy(e, dt)
        alive = []
        for e in self.enemies:
            if e['hp'] > 0 and e['x'] > self.cam - 140:
                alive.append(e)
        self.enemies = alive
        # enemy bullets
        keep = []
        for b in self.ebullets:
            b['t'] += dt
            if b['kind'] == 'mortar':
                b['vy'] -= 700.0 * dt
            elif b['kind'] == 'bomb':
                b['vy'] -= 520.0 * dt
            elif b['kind'] == 'missile':
                pass
            b['x'] += b['vx'] * dt
            b['y'] += b['vy'] * dt
            if b['y'] <= GROUND:
                if b['kind'] in ('mortar', 'bomb', 'missile', 'shell'):
                    self._explode(b['x'], GROUND, 40.0 if b['kind'] != 'bomb' else 32.0, 0, False)
                continue
            if b['x'] < self.cam - 40 or b['x'] > self.cam + W + 40 or b['y'] > H + 200:
                continue
            if player_alive and p.inv <= 0 and abs(b['x'] - p.x) < 11 and GROUND + p.y - 2 <= b['y'] <= GROUND + p.y + 40:
                if b['kind'] in ('mortar', 'bomb', 'missile', 'shell'):
                    self._explode(b['x'], b['y'], 36.0, 0, False)
                self._hurt_player()
                continue
            keep.append(b)
        self.ebullets = keep
        # boss defeat -> mission clear
        if self.boss is not None and self.boss['hp'] <= 0 and self.phase == 'play':
            self.phase, self.timer = 'clear', 0.0
            self.msg, self.msg_t = "MISSION COMPLETE!", 4.0
            self.ebullets = []
            self._finish(True)
        if self.boss is not None and self.boss['hp'] <= 0:
            self.boss = None
        if self.phase == 'clear' and self.timer > 4.0:
            pass

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

    def _start(self, i):
        self.diff = i
        self.menu = False
        self.reset()

    def click(self, x, y, button):
        if self.menu:
            if button == 'LEFT':
                i = self._card_idx(x, y)
                if i >= 0:
                    self._start(i)
            return
        self.mouse = self._logical(x, y)
        if self.phase == 'over' or (self.phase == 'clear' and self.timer > 2.5):
            self.reset()
            return
        if button == 'RIGHT':
            self._throw()
        else:
            self._toggle_fire()

    def key(self, key, repeat):
        if self.menu:
            i = DIGITS.get(key)
            if i is not None:
                self._start(i)
            return
        if key == 'M':
            self.menu, self.menu_hover = True, -1
            return
        p = self.p
        if self.phase in ('over', 'clear'):
            if key == 'SPACE' and (self.phase == 'over' or self.timer > 2.5):
                self.reset()
            return
        if self.phase != 'play':
            return
        if key in ('A', 'LEFT_ARROW'):
            p.walk, p.walk_t = -1, 0.38
        elif key in ('D', 'RIGHT_ARROW'):
            p.walk, p.walk_t = 1, 0.38
        elif key in ('W', 'SPACE', 'UP_ARROW'):
            if not repeat and p.y <= 0:
                p.vy = JUMP_V
        elif key in ('J', 'F'):
            if not repeat:
                self._toggle_fire()
        elif key == 'G':
            if not repeat:
                self._throw()

    # ----------------------------------------------------------------- layout
    def layout(self, view):
        cs = self.fit_cell(view, 8, 5, max_cell=100, min_cell=36)
        self.place(view, 8 * cs, 5 * cs)
        self.sc = cs / 80.0
        self.fx0 = self.left
        self.fy0 = self.top - 5 * cs

    # ---------------------------------------------------------------- drawing
    def _X(self, wx):
        return self.fx0 + (wx - self.cam + self._sx) * self.sc

    def _Y(self, v):
        return self.fy0 + (v + self._sy) * self.sc

    # ---- drawing helpers: everything is clipped to the game field so nothing leaks over the UI
    def _clip_poly(self, pts):
        """Sutherland-Hodgman against the field rectangle (logical coords)."""
        def clip(pl, inside, inter):
            out = []
            for i in range(len(pl)):
                a, b = pl[i], pl[(i + 1) % len(pl)]
                ia, ib = inside(a), inside(b)
                if ia and ib:
                    out.append(b)
                elif ia and not ib:
                    out.append(inter(a, b))
                elif not ia and ib:
                    out.append(inter(a, b))
                    out.append(b)
            return out

        def ix(xv):
            return lambda a, b: (xv, a[1] + (b[1] - a[1]) * (xv - a[0]) / ((b[0] - a[0]) or 1e-9))

        def iy(yv):
            return lambda a, b: (a[0] + (b[0] - a[0]) * (yv - a[1]) / ((b[1] - a[1]) or 1e-9), yv)
        pl = list(pts)
        for inside, inter in ((lambda p: p[0] >= 0.0, ix(0.0)), (lambda p: p[0] <= W, ix(W)),
                              (lambda p: p[1] >= 0.0, iy(0.0)), (lambda p: p[1] <= H, iy(H))):
            if not pl:
                return []
            pl = clip(pl, inside, inter)
        return pl

    def _to_screen(self, p):
        return (self.fx0 + p[0] * self.sc, self.fy0 + p[1] * self.sc)

    def _rect(self, c, wx, y, w, h, col):
        x0 = max(wx - self.cam + self._sx, 0.0)
        x1 = min(wx - self.cam + self._sx + w, W)
        y0 = max(y + self._sy, 0.0)
        y1 = min(y + self._sy + h, H)
        if x1 > x0 and y1 > y0:
            c.rect(self.fx0 + x0 * self.sc, self.fy0 + y0 * self.sc, (x1 - x0) * self.sc, (y1 - y0) * self.sc, col)

    def _circ(self, c, wx, y, r, col, seg=16):
        cx, cy = wx - self.cam + self._sx, y + self._sy
        if cx + r < 0 or cx - r > W or cy + r < 0 or cy - r > H:
            return
        if cx - r >= 0 and cx + r <= W and cy - r >= 0 and cy + r <= H:
            c.circle(self.fx0 + cx * self.sc, self.fy0 + cy * self.sc, r * self.sc, col, seg)
            return
        n = max(12, seg)
        pts = self._clip_poly([(cx + r * math.cos(2 * math.pi * k / n), cy + r * math.sin(2 * math.pi * k / n)) for k in range(n)])
        if len(pts) >= 3:
            c.poly([self._to_screen(p) for p in pts], col)

    def _line(self, c, p, q, w, col):
        x0, y0 = p[0] - self.cam + self._sx, p[1] + self._sy
        x1, y1 = q[0] - self.cam + self._sx, q[1] + self._sy
        m = min(w / 2.0, 6.0)                                  # keep the thick stroke inside the field
        lo_x, hi_x, lo_y, hi_y = m, W - m, m, H - m
        dx, dy = x1 - x0, y1 - y0
        t0, t1 = 0.0, 1.0
        for pk, qk in ((-dx, x0 - lo_x), (dx, hi_x - x0), (-dy, y0 - lo_y), (dy, hi_y - y0)):
            if abs(pk) < 1e-12:
                if qk < 0:
                    return
            else:
                r = qk / pk
                if pk < 0:
                    if r > t1:
                        return
                    t0 = max(t0, r)
                else:
                    if r < t0:
                        return
                    t1 = min(t1, r)
        a = (x0 + dx * t0, y0 + dy * t0)
        b = (x0 + dx * t1, y0 + dy * t1)
        c.line(self._to_screen(a), self._to_screen(b), max(1.0, w * self.sc), col)

    def _poly(self, c, pts, col):
        lp = [(x - self.cam + self._sx, y + self._sy) for x, y in pts]
        if all(0 <= x <= W and 0 <= y <= H for x, y in lp):
            c.poly([self._to_screen(p) for p in lp], col)
            return
        cl = self._clip_poly(lp)
        if len(cl) >= 3:
            c.poly([self._to_screen(p) for p in cl], col)

    def _text(self, c, s, lx, ly, size, col=ov.C_WHITE, align='center'):
        px, py = lx + self._sx, ly + self._sy
        if -40 <= px <= W + 40 and -10 <= py <= H + 10:
            c.text(s, self.fx0 + px * self.sc, self.fy0 + py * self.sc, size * self.sc, col, align)

    def _background(self, c):
        cam = self.cam
        # sky bands
        n = 14
        for i in range(n):
            k = i / (n - 1.0)
            seg = k * (len(SKY) - 1)
            a, b = SKY[int(seg)], SKY[min(len(SKY) - 1, int(seg) + 1)]
            f = seg - int(seg)
            col = tuple(a[j] + (b[j] - a[j]) * f for j in range(3)) + (1.0,)
            self._rect(c, cam - 20, GROUND + (H - GROUND) * i / n, W + 40, (H - GROUND) / n + 1.5, col)
        for x, y, ph in self.stars:
            a = 0.2 + 0.4 * abs(math.sin(self.time + ph))
            self._rect(c, cam + (x * 0.55) % W, y, 1.5, 1.5, (1, 1, 1, a * (y - 250) / 150))
        # sun
        sx = cam + 430
        for k in range(4):
            self._circ(c, sx, 160, 70 - k * 12 + 28, (1.0, 0.75, 0.35, 0.06 + 0.04 * k), 36)
        self._circ(c, sx, 160, 52, (1.0, 0.88, 0.55, 1.0), 36)
        # far dunes + ruins (parallax)
        for x, h, w in self.dunes:
            wx = x + cam * 0.88
            sx_ = ((wx - cam * 0.88) % 1800) + cam * 0.88 - 200
            self._poly(c, [(sx_, GROUND), (sx_ + w * 0.35, GROUND + h), (sx_ + w * 0.7, GROUND + h * 0.8), (sx_ + w, GROUND)], (0.62, 0.34, 0.36, 1.0))
        for x, h, w, r in self.ruins:
            px = ((x - cam * 0.6) % 1500) + cam - 180
            self._rect(c, px, GROUND, w, h, (0.36, 0.22, 0.32, 1.0))
            self._rect(c, px, GROUND + h - 3, w, 3, (0.5, 0.32, 0.42, 1.0))
            if r > 0.4:
                for wy in range(int(GROUND + 14), int(GROUND + h - 10), 18):
                    self._rect(c, px + 8, wy, 7, 9, (0.12, 0.08, 0.16, 1.0))
                    if r > 0.75:
                        self._rect(c, px + w - 16, wy, 7, 9, (1.0, 0.8, 0.4, 0.55))
            if r > 0.55:
                self._poly(c, [(px + w * 0.15, GROUND + h), (px + w * 0.3, GROUND + h + 14), (px + w * 0.5, GROUND + h)], (0.30, 0.18, 0.26, 1.0))
        # ground
        self._rect(c, cam - 20, 0, W + 40, GROUND, (0.78, 0.55, 0.32, 1.0))
        self._rect(c, cam - 20, GROUND - 6, W + 40, 6, (0.88, 0.66, 0.40, 1.0))
        self._rect(c, cam - 20, 0, W + 40, 22, (0.62, 0.42, 0.25, 1.0))
        for k in range(24):
            gx = (k * 61 - (cam % 61) * 1) + cam - cam % 61 - 20
            self._rect(c, gx, 28 + (k * 7) % 30, 18, 2, (0.62, 0.42, 0.25, 0.7))
        # props
        for x, kind in self.props:
            if cam - 40 < x < cam + W + 40:
                if kind == 'barrel':
                    self._rect(c, x - 9, GROUND - 4, 18, 26, (0.55, 0.20, 0.16, 1))
                    self._rect(c, x - 9, GROUND + 6, 18, 3, (0.25, 0.1, 0.1, 1))
                elif kind == 'sandbag':
                    for r_ in range(2):
                        for j in range(3 - r_):
                            self._rect(c, x - 22 + j * 15 + r_ * 8, GROUND - 4 + r_ * 9, 15, 10, (0.74, 0.62, 0.40, 1))
                            self._rect(c, x - 22 + j * 15 + r_ * 8, GROUND - 4 + r_ * 9, 15, 2, (0.88, 0.76, 0.52, 1))
                else:
                    self._rect(c, x - 3, GROUND - 4, 6, 34, (0.25, 0.5, 0.25, 1))
                    self._rect(c, x - 10, GROUND + 14, 6, 14, (0.25, 0.5, 0.25, 1))
                    self._rect(c, x + 4, GROUND + 10, 6, 16, (0.25, 0.5, 0.25, 1))

    def _soldier(self, c, x, y, face, anim, col, helmet, state='run', aim=0.0, gun=True, dead=False, hit=False):
        """Pixel-ish soldier; (x, y) = feet."""
        f = face
        run = state in ('run', 'walk')
        sw = math.sin(anim) if run else 0.0
        skin = (0.95, 0.76, 0.58, 1.0) if not hit else (1, 1, 1, 1)
        body = col if not hit else (1, 1, 1, 1)
        self._rect(c, x - 8 + 1.5, y - 1, 16, 2, (0, 0, 0, 0.25))
        # legs
        self._line(c, (x - f * 2, y + 15), (x - f * 2 + sw * 8, y), 5.5, (0.22, 0.20, 0.18, 1))
        self._line(c, (x + f * 2, y + 15), (x + f * 2 - sw * 8, y), 5.5, ov.shade((0.30, 0.27, 0.22, 1), 1.0))
        # torso
        self._rect(c, x - 7, y + 14, 14, 17, body)
        self._rect(c, x - 7, y + 14, 14, 4, ov.shade(body, 0.7))
        self._rect(c, x - 3 * f - 1.5, y + 17, 3, 10, ov.shade(body, 1.25))
        # head
        self._circ(c, x + f * 1, y + 36, 6.4, skin, 12)
        self._rect(c, x - 6.5 + f * 0.5, y + 37, 13, 5, helmet)
        self._circ(c, x + f * 1, y + 40, 6.6, helmet, 12)
        self._rect(c, x + f * 3 - 1, y + 35, 2, 1.8, (0.1, 0.1, 0.12, 1))
        if helmet[0] > 0.7 and helmet[1] < 0.4:                      # bandana tail
            self._line(c, (x - f * 5, y + 38), (x - f * 12, y + 35 + math.sin(self.time * 9) * 2), 2.4, helmet)
        if gun:
            gx, gy = x + f * 5, y + 23
            ex, ey = gx + math.cos(aim) * 16, gy + math.sin(aim) * 16
            self._line(c, (gx, gy), (ex, ey), 4.0, (0.15, 0.15, 0.18, 1))
            self._line(c, (x, y + 25), (gx, gy), 4.5, skin)
        else:
            self._line(c, (x, y + 25), (x + f * 10, y + 20 + sw * 3), 4.5, skin)

    def _player_draw(self, c):
        p = self.p
        if self.phase == 'dying' and self.timer < 1.2:
            return
        if p.inv > 0 and int(p.inv * 14) % 2 == 0:
            return
        y = GROUND + p.y
        state = 'run' if (p.walk and p.y <= 0) else 'idle'
        a = p.aim if p.face > 0 else p.aim
        self._soldier(c, p.x, y, p.face, p.anim, (0.28, 0.50, 0.26, 1.0), (0.86, 0.18, 0.20, 1.0), state, a)
        if p.kick > 0:
            gx, gy = p.x + p.face * 14 + math.cos(a) * 16, y + 22 + math.sin(a) * 16
            self._circ(c, gx, gy, 6, (1.0, 0.9, 0.4, 0.9), 8)
            self._circ(c, gx, gy, 3.2, (1, 1, 1, 1), 8)

    def _enemy_draw(self, c, e):
        k = e['kind']
        x, y = e['x'], e['y']
        hit = e['hit'] > 0
        if k in ('rifle', 'rusher'):
            col = (0.74, 0.60, 0.40, 1.0) if k == 'rifle' else (0.62, 0.40, 0.30, 1.0)
            helm = (0.42, 0.45, 0.28, 1.0) if k == 'rifle' else (0.2, 0.2, 0.22, 1.0)
            ang = math.atan2(GROUND + self.p.y + 20 - (y + 22), self.p.x - x)
            self._soldier(c, x, y, e['face'], e['anim'], col, helm, 'run' if (k == 'rusher' or e['state'] == 'walk') else 'idle',
                          ang, gun=(k == 'rifle'), hit=hit)
            if k == 'rusher':
                self._line(c, (x + e['face'] * 8, y + 22), (x + e['face'] * 19, y + 28), 2.2, (0.85, 0.88, 0.95, 1))
            if e.get('flash', 0) > 0:
                e['flash'] -= 0.016
                self._circ(c, x + e['face'] * 20, y + 24, 4.5, (1, 0.9, 0.4, 0.9), 8)
        elif k == 'mortar':
            self._rect(c, x - 12, y - 2, 24, 8, (0.3, 0.28, 0.25, 1))
            self._line(c, (x - 3, y + 6), (x + e['face'] * 8, y + 28), 7, (0.38, 0.40, 0.36, 1))
            self._soldier(c, x - e['face'] * 18, y, e['face'], 0, (0.74, 0.60, 0.40, 1.0), (0.42, 0.45, 0.28, 1.0), 'idle', 0.0, gun=False, hit=hit)
        elif k == 'drone':
            sp = e['anim'] * 3
            col = (0.75, 0.2, 0.25, 1.0) if not hit else (1, 1, 1, 1)
            self._poly(c, [(x - 14, y), (x - 7, y + 9), (x + 7, y + 9), (x + 14, y), (x + 7, y - 7), (x - 7, y - 7)], (0.1, 0.1, 0.14, 1))
            self._poly(c, [(x - 12, y), (x - 6, y + 7), (x + 6, y + 7), (x + 12, y), (x + 6, y - 5), (x - 6, y - 5)], col)
            self._circ(c, x + e['face'] * 4, y + 1, 3.2, (1.0, 0.85, 0.3, 1), 8)
            for sgn in (-1, 1):
                self._line(c, (x + sgn * 10, y + 9), (x + sgn * 10, y + 13), 2, (0.2, 0.2, 0.2, 1))
                a = math.cos(sp) * 12
                self._line(c, (x + sgn * 10 - a, y + 14), (x + sgn * 10 + a, y + 14), 2.4, (0.85, 0.85, 0.9, 0.9))
        elif k == 'crate':
            self._rect(c, x - 13, y - 2, 26, 24, (0.50, 0.34, 0.18, 1) if not hit else (1, 1, 1, 1))
            self._rect(c, x - 13, y + 18, 26, 4, (0.66, 0.46, 0.24, 1))
            self._line(c, (x - 12, y), (x + 12, y + 20), 2.4, (0.32, 0.20, 0.10, 1))
            self._line(c, (x - 12, y + 20), (x + 12, y), 2.4, (0.32, 0.20, 0.10, 1))
        elif k == 'tank':
            f = e['face']
            body = (0.50, 0.44, 0.28, 1.0) if not hit else (1, 1, 1, 1)
            self._rect(c, x - 40, y, 80, 12, (0.14, 0.14, 0.16, 1))
            for j in range(5):
                self._circ(c, x - 32 + j * 16, y + 6, 6.5, (0.28, 0.28, 0.30, 1), 10)
                self._circ(c, x - 32 + j * 16, y + 6, 2.4, (0.1, 0.1, 0.12, 1), 6)
            self._poly(c, [(x - 38, y + 10), (x + 38, y + 10), (x + 30, y + 24), (x - 30, y + 24)], body)
            self._poly(c, [(x - 20, y + 24), (x + 20, y + 24), (x + 14, y + 36), (x - 14, y + 36)], ov.shade(body, 0.85))
            ang = math.atan2(GROUND + self.p.y + 20 - (y + 32), self.p.x - x)
            self._line(c, (x, y + 31), (x + math.cos(ang) * 40, y + 31 + math.sin(ang) * 40), 6, (0.25, 0.24, 0.20, 1))
            self._circ(c, x, y + 31, 8, ov.shade(body, 0.7), 12)
            self._rect(c, x - 38, y + 11, 76, 2.5, (1, 1, 1, 0.12))
            if e.get('flash', 0) > 0:
                e['flash'] -= 0.016
                self._circ(c, x + math.cos(ang) * 44, y + 31 + math.sin(ang) * 44, 8, (1, 0.8, 0.3, 0.9), 10)
            self._hp_bar(c, x, y + 46, e)
        elif k == 'boss':
            body = (0.30, 0.34, 0.40, 1.0) if not hit else (1, 1, 1, 1)
            self._poly(c, [(x - 60, y), (x - 32, y + 22), (x + 36, y + 24), (x + 62, y + 4), (x + 38, y - 18), (x - 34, y - 20)], (0.08, 0.09, 0.12, 1))
            self._poly(c, [(x - 56, y), (x - 30, y + 18), (x + 34, y + 20), (x + 56, y + 3), (x + 36, y - 14), (x - 32, y - 16)], body)
            self._poly(c, [(x + 20, y + 14), (x + 52, y + 4), (x + 40, y - 8), (x + 18, y - 4)], (0.45, 0.80, 0.95, 0.9))
            self._line(c, (x - 56, y + 4), (x - 118, y + 14), 10, ov.shade(body, 0.8))
            self._poly(c, [(x - 114, y + 14), (x - 126, y + 40), (x - 118, y + 42), (x - 106, y + 14)], ov.shade(body, 0.7))
            self._line(c, (x - 140 + math.cos(self.time * 40) * 6, y + 26 + math.sin(self.time * 40) * 20),
                       (x - 112 - math.cos(self.time * 40) * 6, y + 26 - math.sin(self.time * 40) * 20), 3, (0.8, 0.8, 0.85, 0.7))
            self._rect(c, x - 3, y + 20, 6, 10, (0.2, 0.2, 0.24, 1))
            ang = self.time * 38
            for j in range(3):
                a = ang + j * 2.094
                self._line(c, (x, y + 30), (x + math.cos(a) * 82, y + 30 + math.sin(a) * 6), 4.0, (0.2, 0.2, 0.24, 0.85))
            self._line(c, (x - 82, y + 30), (x + 82, y + 30), 2.0, (0.9, 0.9, 0.95, 0.18))
            self._rect(c, x - 2, y - 24, 4, 8, (0.2, 0.2, 0.24, 1))
            self._line(c, (x - 20, y - 18), (x + 26, y - 18), 3, (0.2, 0.2, 0.24, 1))
            self._hp_bar(c, x, y + 46, e)

    def _hp_bar(self, c, x, y, e):
        w = 70.0
        self._rect(c, x - w / 2 - 1, y - 1, w + 2, 6, (0, 0, 0, 0.6))
        self._rect(c, x - w / 2, y, w * max(0.0, e['hp'] / e['hp_max']), 4, (0.95, 0.3, 0.3, 1))

    def _effects(self, c):
        for b in self.bullets:
            sp = math.hypot(b['vx'], b['vy']) or 1
            self._line(c, (b['x'] - b['vx'] / sp * 11, b['y'] - b['vy'] / sp * 11), (b['x'], b['y']), 2.4, (1.0, 0.95, 0.5, 1))
            self._circ(c, b['x'], b['y'], 2.2, (1, 1, 1, 1), 6)
        for b in self.ebullets:
            k = b['kind']
            if k in ('bullet',):
                self._circ(c, b['x'], b['y'], 5, (1.0, 0.45, 0.2, 0.3), 8)
                self._circ(c, b['x'], b['y'], 3, (1.0, 0.75, 0.3, 1), 8)
            elif k == 'shell':
                self._circ(c, b['x'], b['y'], 7, (1.0, 0.5, 0.1, 0.35), 10)
                self._circ(c, b['x'], b['y'], 4.4, (0.9, 0.25, 0.1, 1), 10)
            elif k == 'missile':
                self._line(c, (b['x'], b['y'] + 14), (b['x'], b['y'] - 6), 5, (0.85, 0.85, 0.9, 1))
                self._circ(c, b['x'], b['y'] - 8, 4, (1.0, 0.6, 0.2, 0.9), 8)
                self._circ(c, b['x'], b['y'] + 16, 3, (1, 0.3, 0.2, 1), 8)
            else:
                self._circ(c, b['x'], b['y'], 5.5, (0.15, 0.15, 0.18, 1), 8)
                self._circ(c, b['x'] - 1.5, b['y'] + 1.5, 1.8, (0.6, 0.6, 0.65, 1), 6)
        for g in self.grens:
            self._circ(c, g['x'], g['y'], 4.8, (0.12, 0.30, 0.14, 1), 8)
            self._circ(c, g['x'] - 1, g['y'] + 1, 1.7, (0.5, 0.8, 0.5, 1), 6)
        for cs_ in self.casings:
            self._rect(c, cs_[0], cs_[1], 2.4, 1.6, (0.95, 0.78, 0.25, 1 - cs_[4] * 2))
        for s in self.smoke:
            self._circ(c, s[0], s[1], 3 + 6 * s[2], (0.6, 0.6, 0.6, 0.4 * (1 - s[2])), 8)
        for bx, by, t, r in self.booms:
            if t < 0:
                continue
            k = t / 0.55
            self._circ(c, bx, by, r * (0.5 + 1.2 * k), (1.0, 0.45, 0.1, 0.7 * (1 - k)), 18)
            self._circ(c, bx, by, r * (0.3 + 0.9 * k), (1.0, 0.8, 0.25, 0.9 * (1 - k)), 16)
            self._circ(c, bx, by, r * 0.5 * (1 - k), (1, 1, 0.9, 1), 12)
            if k > 0.3:
                self._circ(c, bx, by + r * k * 0.8, r * 0.7 * k, (0.25, 0.22, 0.22, 0.45 * (1 - k)), 12)

    def _items(self, c):
        for it in self.items:
            y = GROUND + 12 + math.sin(it['t'] * 5) * 3
            col = {'mg': (1.0, 0.55, 0.2, 1), 'shotgun': (0.4, 0.85, 1.0, 1), 'gren': (0.45, 0.85, 0.4, 1)}[it['kind']]
            self._rect(c, it['x'] - 11, y - 7, 22, 15, (0, 0, 0, 0.55))
            self._rect(c, it['x'] - 9, y - 5, 18, 11, col)
            self._text(c, {'mg': "H", 'shotgun': "S", 'gren': "G"}[it['kind']], (it['x'] - self.cam), y, 10, ov.C_DARK)
        for pw in self.pows:
            x, y = pw['x'], GROUND
            if pw['freed'] > 0:
                k = pw['freed']
                self._soldier(c, x + k * 70, y, 1, k * 14, (0.9, 0.9, 0.85, 1), (0.5, 0.35, 0.2, 1), 'run', 0.0, gun=False)
                self._text(c, "THANK YOU!", (x + k * 70 - self.cam), y + 60 + k * 10, 10, ov.alpha(ov.C_GOLD, max(0.0, 1 - k / 2.2)))
            else:
                self._rect(c, x - 3, y - 4, 6, 30, (0.45, 0.30, 0.18, 1))
                self._soldier(c, x, y, -1, 0, (0.9, 0.9, 0.85, 1), (0.5, 0.35, 0.2, 1), 'idle', 0.0, gun=False)
                self._line(c, (x - 7, y + 14), (x + 7, y + 22), 2.4, (0.7, 0.5, 0.3, 1))
                self._text(c, "POW", (x - self.cam), y + 52 + math.sin(pw['t'] * 4) * 2, 9, ov.C_GOLD)

    def _hud(self, c):
        p = self.p
        self._text(c, "SCORE %d" % self.score, 14, 380, 14, ov.C_GOLD, 'left')
        for i in range(max(0, p.lives)):
            self._soldier_icon(c, 20 + i * 18, 352)
        w = WEAPONS[p.weapon]
        self._text(c, "%s%s" % (w['name'], "" if p.weapon == 'pistol' else "  %d" % p.ammo), 14, 332, 11, ov.C_WHITE, 'left')
        self._text(c, "GRENADES %d" % p.gren, 14, 316, 10, (0.6, 0.9, 0.55, 1), 'left')
        self._text(c, "FIRE %s  (click / J)" % ("ON" if self.firing else "OFF"), 14, 300, 9.5,
                   (1.0, 0.55, 0.35, 1.0) if self.firing else ov.C_TEXT, 'left')
        # mission progress
        prog = min(1.0, self.p.x / (LEVEL_LEN - 300))
        self._rect(c, W - 168, 382, 154, 8, (0, 0, 0, 0.5))
        self._rect(c, W - 167, 383, 152 * prog, 6, (0.4, 0.9, 0.5, 1))
        self._rect(c, W - 17, 380, 3, 12, (0.95, 0.3, 0.3, 1))
        self._text(c, "TIME %s   POW %d" % (_fmt(self.clear_t), self.pow_saved), W - 14, 366, 10, ov.C_TEXT, 'right')
        if self.boss is not None:
            b = self.boss
            self._rect(c, W / 2 - 100, 354, 200, 8, (0, 0, 0, 0.6))
            self._rect(c, W / 2 - 99, 355, 198 * max(0, b['hp'] / b['hp_max']), 6, (0.95, 0.3, 0.3, 1))
            self._text(c, "GUNSHIP", W / 2, 370, 10, ov.C_BAD)
        if self.msg_t > 0:
            self._text(c, self.msg, W / 2, 262, 24 if "COMPLETE" not in self.msg else 34, ov.alpha(ov.C_GOLD, min(1.0, self.msg_t * 1.5)))
        # crosshair
        mx, my = self.mouse
        ring_col = (1.0, 0.45, 0.35, 0.95) if self.firing else (1, 1, 1, 0.8)      # red while firing
        c.ring(self.fx0 + mx * self.sc, self.fy0 + my * self.sc, 8 * self.sc, 1.6 * self.sc + 0.5, ring_col, 16)
        c.circle(self.fx0 + mx * self.sc, self.fy0 + my * self.sc, 1.6 * self.sc,
                 (1, 0.3, 0.3, 1) if self.firing else (0.7, 0.7, 0.75, 1), 6)

    def _soldier_icon(self, c, x, y):
        s = self.sc
        px, py = self.fx0 + x * s, self.fy0 + y * s
        c.circle(px, py + 6 * s, 4 * s, (0.95, 0.76, 0.58, 1), 8)
        c.rect(px - 3.5 * s, py - 6 * s, 7 * s, 9 * s, (0.28, 0.5, 0.26, 1))
        c.rect(px - 4 * s, py + 7 * s, 8 * s, 2.5 * s, (0.86, 0.18, 0.2, 1))

    def _menu(self, c):
        self._sx = self._sy = 0.0
        self.cam = (self.time * 40) % 600
        self._background(c)
        self._rect(c, self.cam - 20, 0, W + 40, H, (0, 0, 0, 0.5))
        self._text(c, "DESERT RUSH", W / 2, 360, 44, (1.0, 0.7 + 0.2 * math.sin(self.time * 3) ** 2, 0.3, 1.0))
        self._text(c, "Run, gun and rescue - bring down the gunship", W / 2, 328, 13, ov.C_TEXT)
        for i, d in enumerate(DIFFS):
            x, y, w, h = self._card(i)
            x += self.cam
            hov = i == self.menu_hover
            self._rect(c, x + 3, y - 4, w, h, (0, 0, 0, 0.4))
            self._rect(c, x, y, w, h, ov.C_CELL_HOVER if hov else ov.C_CELL)
            self._rect(c, x, y, 9, h, tuple(d['col']) + (1.0,))
            self._text(c, "%d  %s" % (i + 1, d['name']), x - self.cam + 24, y + h - 24, 20, ov.C_WHITE, 'left')
            self._text(c, d['info'], x - self.cam + 24, y + 22, 10.5, ov.C_TEXT, 'left')
            bs, bt = self._best(i)
            if bs:
                self._text(c, "Best score  %d" % bs, x - self.cam + w - 14, y + h - 26, 13, ov.C_GOLD, 'right')
                self._text(c, "Fastest clear  %s" % _fmt(bt), x - self.cam + w - 14, y + 22, 12, ov.C_GOLD, 'right')
            else:
                self._text(c, "Not cleared yet", x - self.cam + w - 14, y + h - 26, 12, ov.C_TEXT, 'right')
        self._text(c, "A/D run   SPACE jump   mouse aim   click / J start-stop firing   G / right-click grenade", W / 2, 34, 11, ov.C_WHITE)

    def draw(self, c):
        self._sx = self._sy = 0.0
        self.draw_frame(c)
        bs, bt = self._best(self.diff)
        if self.menu:
            self.draw_header(c, "DESERT RUSH", "Best score and fastest mission clear saved per difficulty", ov.C_GOLD)
            self._menu(c)
            self.draw_hint(c, "Click or press 1-3 to pick a difficulty   ESC quit")
            return
        self.draw_header(c, "DESERT RUSH - %s" % self.cfg['name'],
                         "Score %d   |   Best %d   |   Fastest clear %s" % (self.score, bs, _fmt(bt)),
                         tuple(self.cfg['col']) + (1.0,))
        if self.shake > 0:
            self._sx = random.uniform(-1, 1) * self.shake
            self._sy = random.uniform(-1, 1) * self.shake * 0.7
        self._background(c)
        self._items(c)
        for e in sorted(self.enemies, key=lambda q: q['kind'] == 'boss'):
            if self.cam - 130 < e['x'] < self.cam + W + 130:
                self._enemy_draw(c, e)
        self._player_draw(c)
        self._effects(c)
        sx_, sy_ = self._sx, self._sy
        self._sx = self._sy = 0.0
        self._hud(c)
        if self.phase == 'over':
            self._rect(c, self.cam, 0, W, H, (0, 0, 0, 0.62))
            self._text(c, "MISSION FAILED", W / 2, 270, 42, ov.C_BAD)
            self._text(c, "Score %d" % self.score, W / 2, 224, 22, ov.C_GOLD)
            self._text(c, "SPACE / click: retry     M: difficulty", W / 2, 170, 13, ov.C_TEXT)
        elif self.phase == 'clear' and self.timer > 1.6:
            self._rect(c, self.cam, 0, W, H, (0, 0, 0, 0.55))
            f = self.final
            self._text(c, "MISSION COMPLETE", W / 2, 300, 38, ov.C_GOLD)
            self._text(c, "Time bonus + lives bonus  +%d" % f['bonus'], W / 2, 262, 13, ov.C_TEXT)
            self._text(c, "SCORE  %d%s" % (f['score'], "   NEW BEST!" if self.new_best_score else ""), W / 2, 228, 26, ov.C_GOLD)
            self._text(c, "Clear time  %s%s" % (_fmt(self.clear_t), "   NEW BEST TIME!" if self.new_best_time else ""), W / 2, 196, 14,
                       ov.C_GOLD if self.new_best_time else ov.C_TEXT)
            self._text(c, "Prisoners %d   Kills %d" % (self.pow_saved, self.kills), W / 2, 170, 12, ov.C_TEXT)
            self._text(c, "SPACE / click: play again     M: difficulty", W / 2, 128, 12, ov.C_TEXT)
        self._sx, self._sy = sx_, sy_
        self.draw_hint(c, "A/D run  SPACE jump  mouse aim  click / J: fire on/off  G grenade  M menu")


RUNNER = ov.Runner("desertrush", GAME_NAME, DesertRush, [
    "A / D (hold): run    SPACE / W: jump",
    "Mouse: aim    click / J: start / stop firing",
    "G / right click: grenade",
    "Free the POWs, grab H / S / G crates",
    "Beat the gunship at the end of the level",
    "Best score + fastest clear saved per difficulty",
])


def draw_ui(layout, context):
    RUNNER.draw_ui(layout)


def register():
    RUNNER.register()


def unregister():
    RUNNER.unregister()
