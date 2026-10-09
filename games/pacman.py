"""Pac-Man - keep the mouse over the 3D Viewport and use the keyboard.

Eat every pellet, avoid the four ghosts (each hunts you differently) and grab
a power pellet to turn the tables. The side tunnel wraps around.

Pick a difficulty before you start; the best time to clear the first maze is
saved for each difficulty.
"""

import math
import random
import traceback
from collections import deque

from . import _overlay as ov
from . import _record as _rec

GAME_NAME = "Pac-Man"
GAME_ICON = 'GHOST_ENABLED'

# '#' wall  '.' pellet  'o' power pellet  ' ' empty  '-' ghost door
# 'G' ghost house  'P' Pac-Man start
MAZE = [
    "###################",
    "#........#........#",
    "#o##.###.#.###.##o#",
    "#.................#",
    "#.##.#.#####.#.##.#",
    "#....#...#...#....#",
    "####.###.#.###.####",
    "####.#.......#.####",
    "####.#.##-##.#.####",
    "    .#.#GGG#.#.    ",
    "####.#.#####.#.####",
    "####.#.......#.####",
    "####.###.#.###.####",
    "#........#........#",
    "#.##.###.#.###.##.#",
    "#o.#.....P.....#.o#",
    "##.#.#.#####.#.#.##",
    "#....#...#...#....#",
    "#.######.#.######.#",
    "#.................#",
    "###################",
]
ROWS, COLS = len(MAZE), len(MAZE[0])
TUNNEL_ROW = 9
HOUSE = (9, 9)
DOOR_OUT = (9, 7)
assert all(len(r) == COLS for r in MAZE)

UP, DOWN, LEFT, RIGHT = (0, -1), (0, 1), (-1, 0), (1, 0)   # screen rows grow downward
CHOICES = (UP, LEFT, DOWN, RIGHT)                          # ghost tie-break order
KEYMAP = {
    'UP_ARROW': UP, 'W': UP, 'DOWN_ARROW': DOWN, 'S': DOWN,
    'LEFT_ARROW': LEFT, 'A': LEFT, 'RIGHT_ARROW': RIGHT, 'D': RIGHT,
}

PAC_SPEED, GHOST_SPEED, FRIGHT_SPEED, LEAVE_SPEED, EATEN_SPEED = 7.2, 6.4, 4.0, 4.5, 13.0
SCHEDULE = [(7, 'scatter'), (20, 'chase'), (7, 'scatter'), (20, 'chase'), (5, 'scatter'), (1e9, 'chase')]

C_WALL = (0.07, 0.10, 0.30, 1.0)
C_EDGE = (0.22, 0.42, 1.00, 1.0)
C_PELLET = (1.0, 0.86, 0.72, 1.0)
C_PAC = (1.0, 0.90, 0.10, 1.0)
C_DOOR = (1.0, 0.70, 0.80, 1.0)
C_FRIGHT = (0.15, 0.20, 0.85, 1.0)
C_BG = (0.02, 0.02, 0.04, 1.0)

# lives, gspeed = ghost speed multiplier, fright / fright_min = power pellet seconds,
# release = ghost-house release time multiplier, scatter / chase = schedule multipliers
DIFFS = [
    dict(name="Easy", info="5 lives, slower ghosts, long power pellets",
         lives=5, gspeed=0.85, fright=8.0, fright_min=3.0, release=1.5, scatter=1.5, chase=0.7),
    dict(name="Medium", info="Classic: 3 lives",
         lives=3, gspeed=1.0, fright=7.0, fright_min=2.0, release=1.0, scatter=1.0, chase=1.0),
    dict(name="Hard", info="3 lives, ghosts as fast as you, short pellets",
         lives=3, gspeed=1.12, fright=4.5, fright_min=1.5, release=0.6, scatter=0.5, chase=1.5),
    dict(name="Expert", info="2 lives, ghosts faster than you, no mercy",
         lives=2, gspeed=1.22, fright=3.0, fright_min=1.0, release=0.25, scatter=0.3, chase=2.0),
]

# BFS distances to the ghost house: lets eaten ghosts find their way home
DIST = {HOUSE: 0}
_q = deque([HOUSE])
while _q:
    _c, _r = _q.popleft()
    for _d in CHOICES:
        _n = (_c + _d[0], _r + _d[1])
        if 0 <= _n[0] < COLS and 0 <= _n[1] < ROWS and MAZE[_n[1]][_n[0]] != '#' and _n not in DIST:
            DIST[_n] = DIST[(_c, _r)] + 1
            _q.append(_n)


def pac_ok(c, r):
    if r == TUNNEL_ROW and (c < 0 or c >= COLS):
        return True
    return 0 <= c < COLS and 0 <= r < ROWS and MAZE[r][c] not in '#-G'


def ghost_ok(c, r, state):
    if r == TUNNEL_ROW and (c < 0 or c >= COLS):
        return True
    if not (0 <= c < COLS and 0 <= r < ROWS):
        return False
    ch = MAZE[r][c]
    if ch == '#':
        return False
    if ch == '-':
        return state in ('leaving', 'eaten')
    if ch == 'G':
        return state in ('house', 'leaving', 'eaten')
    return True


class Ent:
    def __init__(self, x, y, d=(0, 0)):
        self.x, self.y, self.dir = float(x), float(y), d


class Ghost(Ent):
    def __init__(self, name, color, x, y, state, release, scatter, d=(0, 0)):
        super().__init__(x, y, d)
        self.name, self.color, self.state = name, color, state
        self.release, self.scatter = release, scatter
        self.fright = False


GHOST_DEFS = [
    ('blinky', (1.00, 0.20, 0.20, 1.0), 9, 7, 'normal', 0.0, (17, -2), LEFT),
    ('pinky', (1.00, 0.55, 0.80, 1.0), 9, 9, 'house', 1.5, (1, -2), (0, 0)),
    ('inky', (0.30, 0.90, 0.95, 1.0), 8, 9, 'house', 5.0, (17, 22), (0, 0)),
    ('clyde', (1.00, 0.65, 0.20, 1.0), 10, 9, 'house', 9.0, (1, 22), (0, 0)),
]


class PacMan(ov.BaseGame):
    keys = tuple(KEYMAP) + ('SPACE', 'P', 'M', 'ONE', 'TWO', 'THREE', 'FOUR')

    def setup(self):
        self.best = 0
        self.diff = 1                     # index in DIFFS
        self.menu = True                  # difficulty menu is shown first
        self.menu_hover = -1
        self.record_mgrs = {}             # diff -> RecordManager (pacman_<name>.json)
        self.best_cache = {}              # diff -> best first-maze clear time (s) or None

    # ---- records
    def _mgr(self, d):
        if d not in self.record_mgrs:
            try:
                self.record_mgrs[d] = _rec.RecordManager(game_name="pacman_" + DIFFS[d]['name'].lower())
            except Exception:
                self.record_mgrs[d] = None
        return self.record_mgrs[d]

    def _best_time(self, d):
        if d not in self.best_cache:
            m = self._mgr(d)
            try:
                self.best_cache[d] = m.get_records().get("best_time") if m else None
            except Exception:
                self.best_cache[d] = None
        return self.best_cache[d]

    @staticmethod
    def _fmt(t):
        return "--" if t is None else "%d:%02d" % divmod(int(round(t)), 60)

    def _record_clear(self):
        m = self._mgr(self.diff)
        if not m:
            return
        try:
            try:
                m.add_win_record(score=self.score, best_time=self.run_time)
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

    # ---- difficulty menu
    def _menu_rect(self, i):
        cs = self.cs
        return (self.left + 2 * cs, self.top - (3.0 + 2.2 * i + 1.8) * cs, 15 * cs, 1.8 * cs)

    def _menu_idx(self, x, y):
        for i in range(len(DIFFS)):
            bx, by, bw, bh = self._menu_rect(i)
            if bx <= x <= bx + bw and by <= y <= by + bh:
                return i
        return -1

    def _start(self, i):
        self.diff = i
        self.menu = False
        self.reset()

    def move(self, x, y):
        if self.menu:
            h = self._menu_idx(x, y)
            changed = h != self.menu_hover
            self.menu_hover = h
            return changed
        return False

    def click(self, x, y, button):
        if self.menu and button == 'LEFT':
            i = self._menu_idx(x, y)
            if i >= 0:
                self._start(i)

    def reset(self):
        self.cfg = DIFFS[self.diff]
        self.run_time = 0.0               # seconds played on the first maze (all lives)
        self.t_first = None               # time taken to clear the first maze
        self.level, self.score, self.lives = 1, 0, self.cfg['lives']
        self.blink = 0.0
        self.cs = 28
        self._new_board()
        self._reset_actors()
        self.state = 'ready'      # ready | playing | dying | clear | paused | over

    # ---- setup helpers
    def _new_board(self):
        self.pellets = {(c, r) for r in range(ROWS) for c in range(COLS) if MAZE[r][c] == '.'}
        self.power = {(c, r) for r in range(ROWS) for c in range(COLS) if MAZE[r][c] == 'o'}

    def _reset_actors(self):
        self.pac = Ent(9, 15)
        self.want = (0, 0)
        self.face = LEFT
        self.ghosts = [Ghost(*d) for d in GHOST_DEFS]
        for g in self.ghosts:
            g.release *= self.cfg['release']
        self.play_t = 0.0
        self.sched_i, self.sched_t, self.gmode = 0, self._sched_len(0), 'scatter'
        self.fright_t = 0.0
        self.combo = 0
        self.anim = 0.0
        self.die_t = self.clear_t = 0.0
        self.pop = None           # (x, y, points, age)

    def _sched_len(self, i):
        dur, mode = SCHEDULE[i]
        return dur * (self.cfg['scatter'] if mode == 'scatter' else self.cfg['chase'])

    def _factor(self):
        return min(1.4, 1 + 0.05 * (self.level - 1))

    # ---- movement
    def _pac_choose(self, e, c, r):
        w = self.want
        if w != (0, 0) and pac_ok(c + w[0], r + w[1]):
            e.dir = w
        elif e.dir != (0, 0) and pac_ok(c + e.dir[0], r + e.dir[1]):
            pass
        else:
            e.dir = (0, 0)
        if e.dir != (0, 0):
            self.face = e.dir

    def _target(self, g):
        if self.gmode == 'scatter':
            return g.scatter
        pc, pr = round(self.pac.x), round(self.pac.y)
        dx, dy = self.face
        if g.name == 'blinky':
            return pc, pr
        if g.name == 'pinky':
            return pc + 4 * dx, pr + 4 * dy
        if g.name == 'inky':
            b = self.ghosts[0]
            return 2 * (pc + 2 * dx) - round(b.x), 2 * (pr + 2 * dy) - round(b.y)
        if (g.x - self.pac.x) ** 2 + (g.y - self.pac.y) ** 2 > 64:
            return pc, pr
        return g.scatter

    def _ghost_choose(self, g, c, r):
        if g.state == 'house':
            g.dir = (0, 0)
            return
        if g.state == 'eaten' and (c, r) == HOUSE:
            g.state, g.fright, g.dir = 'house', False, (0, 0)
            g.release = self.play_t + 1.0
            return
        if g.state == 'leaving':
            if c < HOUSE[0]:
                g.dir = RIGHT
            elif c > HOUSE[0]:
                g.dir = LEFT
            elif r > DOOR_OUT[1]:
                g.dir = UP
            if r <= DOOR_OUT[1] and c == HOUSE[0]:
                g.state = 'normal'
            else:
                return
        back = (-g.dir[0], -g.dir[1])
        opts = [d for d in CHOICES
                if d != back and ghost_ok(c + d[0], r + d[1], g.state)]
        if not opts:
            opts = [back] if g.dir != (0, 0) else [d for d in CHOICES if ghost_ok(c + d[0], r + d[1], g.state)]
        if not opts:
            g.dir = (0, 0)
            return
        if g.state == 'eaten':
            g.dir = min(opts, key=lambda d: DIST.get((c + d[0], r + d[1]), 999))
        elif g.fright:
            g.dir = random.choice(opts)
        else:
            tx, ty = self._target(g)
            g.dir = min(opts, key=lambda d: (c + d[0] - tx) ** 2 + (r + d[1] - ty) ** 2)

    def _advance(self, e, dist, choose, is_pac=False, g=None):
        while dist > 1e-9:
            cx, cy = round(e.x), round(e.y)
            at = abs(e.x - cx) < 1e-6 and abs(e.y - cy) < 1e-6
            if is_pac and e.dir != (0, 0) and self.want == (-e.dir[0], -e.dir[1]):
                e.dir = self.want
            if at:
                e.x, e.y = float(cx), float(cy)
                choose(e, cx, cy)
            if e.dir == (0, 0):
                return
            dx, dy = e.dir
            v = e.x if dx else e.y
            s = dx or dy
            if at:
                target = v + s
            elif s > 0:
                target = math.floor(v) + 1
            else:
                target = math.ceil(v) - 1
            d = abs(target - v)
            step = min(dist, d, 0.25)
            if step >= d - 1e-9:
                nv = float(target)
            else:
                nv = v + s * step
            if dx:
                e.x = nv
            else:
                e.y = nv
            dist -= step
            if e.x < -0.5:
                e.x += COLS
            elif e.x > COLS - 0.5:
                e.x -= COLS

    # ---- rules
    def _reverse_ghosts(self):
        for g in self.ghosts:
            if g.state == 'normal' and g.dir != (0, 0):
                g.dir = (-g.dir[0], -g.dir[1])

    def _start_fright(self):
        self.fright_t = max(self.cfg['fright_min'], self.cfg['fright'] - 0.8 * (self.level - 1))
        self.combo = 0
        for g in self.ghosts:
            if g.state != 'eaten':
                g.fright = True
        self._reverse_ghosts()

    def _gspeed(self, g):
        if g.state == 'house':
            return 0.0
        if g.state == 'eaten':
            return EATEN_SPEED
        if g.state == 'leaving':
            return LEAVE_SPEED
        if g.fright:
            return FRIGHT_SPEED
        return GHOST_SPEED * self._factor() * self.cfg['gspeed']

    def _die(self):
        self.state = 'dying'
        self.die_t = 0.0
        self.lives -= 1
        self.best = max(self.best, self.score)
        if self.lives <= 0 and self.level == 1:
            self._record_loss()       # lost before ever clearing the first maze

    def update(self, dt):
        self.blink += dt
        if self.menu:
            return True
        if self.state == 'ready':
            return True
        if self.state == 'dying':
            self.die_t += dt
            if self.die_t >= 1.6:
                if self.lives <= 0:
                    self.state = 'over'
                else:
                    self._reset_actors()
                    self.state = 'ready'
            return True
        if self.state == 'clear':
            self.clear_t += dt
            if self.clear_t >= 2.4:
                self.level += 1
                self._new_board()
                self._reset_actors()
                self.state = 'ready'
            return True
        if self.state != 'playing':
            return False

        self.play_t += dt
        self.run_time += dt
        if self.pop:
            self.pop = (self.pop[0], self.pop[1], self.pop[2], self.pop[3] + dt)
            if self.pop[3] > 0.8:
                self.pop = None

        # scatter / chase schedule (paused while ghosts are frightened)
        if self.fright_t > 0:
            self.fright_t -= dt
            if self.fright_t <= 0:
                for g in self.ghosts:
                    g.fright = False
        else:
            self.sched_t -= dt
            if self.sched_t <= 0 and self.sched_i < len(SCHEDULE) - 1:
                self.sched_i += 1
                self.sched_t = self._sched_len(self.sched_i)
                self.gmode = SCHEDULE[self.sched_i][1]
                self._reverse_ghosts()

        # Pac-Man
        f = self._factor()
        self._advance(self.pac, PAC_SPEED * f * dt, self._pac_choose, is_pac=True)
        if self.pac.dir != (0, 0):
            self.anim += dt * 14
        tile = (round(self.pac.x), round(self.pac.y))
        if tile in self.pellets:
            self.pellets.discard(tile)
            self.score += 10
        elif tile in self.power:
            self.power.discard(tile)
            self.score += 50
            self._start_fright()
        self.best = max(self.best, self.score)
        if not self.pellets and not self.power:
            self.state = 'clear'
            self.clear_t = 0.0
            if self.level == 1:           # first maze cleared: this is the "win"
                self.t_first = self.run_time
                self._record_clear()
            return True

        # ghosts
        for g in self.ghosts:
            if g.state == 'house' and self.play_t >= g.release:
                g.state = 'leaving'
            sp = self._gspeed(g)
            if sp:
                self._advance(g, sp * dt, lambda e, c, r, g=g: self._ghost_choose(g, c, r), g=g)
            if g.state in ('house', 'eaten'):
                continue
            if math.hypot(g.x - self.pac.x, g.y - self.pac.y) < 0.6:
                if g.fright:
                    g.state, g.fright = 'eaten', False
                    pts = 200 * 2 ** self.combo
                    self.combo += 1
                    self.score += pts
                    self.pop = (g.x, g.y, pts, 0.0)
                else:
                    self._die()
                    return True
        return True

    # ---- input
    def key(self, key, repeat):
        if self.menu:
            i = {'ONE': 0, 'TWO': 1, 'THREE': 2, 'FOUR': 3}.get(key)
            if i is not None:
                self._start(i)
            return
        if key == 'M':
            self.menu, self.menu_hover = True, -1
        elif key in KEYMAP:
            if self.state in ('over', 'dying', 'clear'):
                return
            if self.state == 'paused':
                self.state = 'playing'
            if self.state == 'ready':
                self.state = 'playing'
            self.want = KEYMAP[key]
        elif key in ('SPACE', 'P'):
            if self.state == 'over':
                self.reset()
            elif self.state == 'playing':
                self.state = 'paused'
            elif self.state in ('paused', 'ready'):
                self.state = 'playing'

    # ---- drawing
    def layout(self, view):
        self.cs = self.fit_cell(view, COLS, ROWS, max_cell=32)
        self.place(view, COLS * self.cs, ROWS * self.cs)

    def _px(self, x, y):
        return self.left + (x + 0.5) * self.cs, self.top - (y + 0.5) * self.cs

    def _draw_pac(self, c, x, y, mouth):
        px, py = self._px(x, y)
        r = self.cs * 0.42
        ang = math.atan2(-self.face[1], self.face[0])
        if mouth >= math.pi:
            return
        n = 22
        a0, a1 = ang + mouth, ang + 2 * math.pi - mouth
        pts = [(px + r * math.cos(a0 + (a1 - a0) * i / n), py + r * math.sin(a0 + (a1 - a0) * i / n))
               for i in range(n + 1)]
        for p, q in zip(pts, pts[1:]):
            c.tri((px, py), p, q, C_PAC)

    def _draw_ghost(self, c, g):
        cs = self.cs
        px, py = self._px(g.x, g.y)
        if g.state == 'house':
            py += math.sin(self.blink * 6 + g.release) * cs * 0.06
        r = cs * 0.40
        if g.state != 'eaten':
            if g.fright:
                flash = self.fright_t < 2.0 and int(self.fright_t * 4) % 2 == 0
                col = ov.C_WHITE if flash else C_FRIGHT
            else:
                col = g.color
            c.circle(px, py + r * 0.1, r, col, 20)
            c.rect(px - r, py - r * 0.95, 2 * r, r * 1.05, col)
            w = 2 * r / 3
            for k in range(3):
                x0 = px - r + k * w
                c.tri((x0, py - r * 0.95), (x0 + w, py - r * 0.95), (x0 + w / 2, py - r * 1.3), C_BG)
        if g.fright and g.state != 'eaten':
            for s in (-1, 1):
                c.circle(px + s * r * 0.38, py + r * 0.2, r * 0.13, ov.C_WHITE if col == C_FRIGHT else C_FRIGHT, 8)
            return
        dx, dy = g.dir
        for s in (-1, 1):
            ex, ey = px + s * r * 0.40, py + r * 0.25
            c.circle(ex, ey, r * 0.27, ov.C_WHITE, 10)
            c.circle(ex + dx * r * 0.12, ey - dy * r * 0.12, r * 0.13, (0.1, 0.1, 0.4, 1), 8)

    def draw(self, c):
        u, cs = self.u, self.cs
        self.draw_frame(c)

        if self.state == 'over':
            main, col = "GAME OVER", ov.C_BAD
        elif self.state == 'clear':
            main, col = "LEVEL CLEAR!", ov.C_GOLD
        elif self.state == 'ready':
            main, col = "READY!", C_PAC
        else:
            main, col = "PAC-MAN", C_PAC
        if self.menu:
            main, col = "PAC-MAN", C_PAC
        shown = self.t_first if self.t_first is not None else self.run_time
        self.draw_header(c, main, "%s | Score %d  Lives %d  Level %d | Time %s (best %s)" % (
            self.cfg['name'], self.score, max(0, self.lives), self.level,
            self._fmt(shown), self._fmt(self._best_time(self.diff))), col)

        bx, by = self.left, self.top - ROWS * cs
        c.rect(bx, by, COLS * cs, ROWS * cs, C_BG)

        # maze
        flash = self.state == 'clear' and int(self.clear_t * 4) % 2 == 0
        edge = ov.C_WHITE if flash else C_EDGE
        t = max(2, cs * 0.12)
        for r in range(ROWS):
            for k in range(COLS):
                ch = MAZE[r][k]
                x, y = self.left + k * cs, self.top - (r + 1) * cs
                if ch == '#':
                    c.rect(x, y, cs, cs, C_WALL)
                    if r > 0 and MAZE[r - 1][k] != '#':
                        c.rect(x, y + cs - t, cs, t, edge)
                    if r < ROWS - 1 and MAZE[r + 1][k] != '#':
                        c.rect(x, y, cs, t, edge)
                    if k > 0 and MAZE[r][k - 1] != '#':
                        c.rect(x, y, t, cs, edge)
                    if k < COLS - 1 and MAZE[r][k + 1] != '#':
                        c.rect(x + cs - t, y, t, cs, edge)
                elif ch == '-':
                    c.rect(x, y + cs * 0.42, cs, cs * 0.16, C_DOOR)

        for (k, r) in self.pellets:
            px, py = self._px(k, r)
            c.circle(px, py, cs * 0.08, C_PELLET, 8)
        if int(self.blink * 3) % 2 == 0 or self.state != 'playing':
            for (k, r) in self.power:
                px, py = self._px(k, r)
                c.circle(px, py, cs * 0.22, C_PELLET, 14)

        # actors
        if self.state != 'dying' or self.die_t < 0.4:
            for g in self.ghosts:
                self._draw_ghost(c, g)
        if self.state == 'dying':
            self._draw_pac(c, self.pac.x, self.pac.y, 0.2 + math.pi * min(1.0, self.die_t / 1.4))
        elif self.state != 'over':
            mouth = 0.05 + 0.55 * abs(math.sin(self.anim)) if self.pac.dir != (0, 0) else 0.35
            self._draw_pac(c, self.pac.x, self.pac.y, mouth)
        if self.pop:
            px, py = self._px(self.pop[0], self.pop[1])
            c.text(str(self.pop[2]), px, py, 12 * u, ov.C_WHITE)
        if self.state == 'ready':
            px, py = self._px(9, 11)
            c.text("READY!", px, py, 14 * u, C_PAC)

        bw, bh = COLS * cs, ROWS * cs
        if self.menu:
            c.rect(bx, by, bw, bh, (0.0, 0.0, 0.0, 0.78))
            c.text("Select difficulty", self.left + bw / 2, self.top - 1.5 * cs, cs * 0.9, ov.C_WHITE)
            for i, d in enumerate(DIFFS):
                x0, y0, w0, h0 = self._menu_rect(i)
                c.rect(x0, y0, w0, h0, ov.C_CELL_HOVER if i == self.menu_hover else ov.C_CELL)
                c.text("%d  %s" % (i + 1, d['name']), x0 + 0.5 * cs, y0 + h0 * 0.70,
                       cs * 0.75, ov.C_WHITE, 'left')
                c.text(d['info'], x0 + 0.5 * cs, y0 + h0 * 0.28, cs * 0.45, ov.C_TEXT, 'left')
                bt = self._best_time(i)
                c.text("Best %s" % self._fmt(bt) if bt is not None else "No clear yet",
                       x0 + w0 - 0.5 * cs, y0 + h0 * 0.70, cs * 0.5,
                       ov.C_GOLD if bt is not None else ov.C_TEXT, 'right')
            c.text("Best = fastest clear of the first maze", self.left + bw / 2,
                   self.top - 13.5 * cs, cs * 0.45, ov.C_TEXT)
            self.draw_hint(c, "Click or press 1-4 to pick a difficulty   ESC quit")
            return
        if self.state == 'paused':
            self.banner(c, bx, by, bw, bh, "Paused", "Press SPACE or a direction to resume")
        elif self.state == 'over':
            self.banner(c, bx, by, bw, bh, "Game over", "Score %d  -  SPACE to retry, M for menu" % self.score)

        self.draw_hint(c, "Arrows/WASD move   SPACE pause   M menu   R restart   ESC quit")


RUNNER = ov.Runner("pacman", GAME_NAME, PacMan, [
    "Arrows / WASD: move",
    "Eat all pellets, dodge the ghosts",
    "Big pellets: ghosts turn blue, eat them!",
    "SPACE or P: pause   M: difficulty menu",
    "R: restart   ESC: quit",
    "Keep the mouse over the viewport",
])


def draw_ui(layout, context):
    RUNNER.draw_ui(layout)


def register():
    RUNNER.register()


def unregister():
    RUNNER.unregister()
