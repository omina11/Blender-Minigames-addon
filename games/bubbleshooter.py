"""Bubble Shooter (Puzzle Bobble style).

Aim with the mouse and shoot bubbles: match 3 or more of the same colour to
pop them, anything left hanging falls. Miss a few times and the ceiling
drops. Don't let the bubbles reach the red line.

Pick a difficulty before you start; the best time to clear the first board is
saved for each difficulty.
"""

import math
import random
import traceback

from . import _overlay as ov
from . import _record as _rec

GAME_NAME = "Bubble Shooter"
GAME_ICON = 'SPHERE'

COLS = 11                       # bubbles in an even row (odd rows have COLS - 1)
ROWH = math.sqrt(3) / 2         # row spacing (bubble diameter = 1 cell)
ROWS_VIS = 15                   # visible rows incl. the shooter row
ALLOC = 14                      # rows stored in the grid
LOSE_ROW = 12                   # a bubble in this row (or below) = game over
HC = (ROWS_VIS - 1) * ROWH + 1  # board height in cells
SHOOT = (COLS / 2, HC - 0.5)    # shooter position (cells, y down)
SPEED = 24.0                    # cells / second
DROP_EVERY = 6                  # shots without popping before the ceiling drops
AIM_MIN, AIM_MAX = math.radians(12), math.radians(168)

COLORS = [
    (0.92, 0.28, 0.28, 1.0),    # red
    (0.35, 0.58, 0.96, 1.0),    # blue
    (0.38, 0.82, 0.42, 1.0),    # green
    (0.98, 0.84, 0.26, 1.0),    # yellow
    (0.74, 0.44, 0.92, 1.0),    # purple
    (1.00, 0.62, 0.22, 1.0),    # orange
]
C_BOARD = (0.09, 0.10, 0.13, 1.0)

# rows = starting rows, colors = colours on board 1, drop = shots without popping
# before the ceiling drops, lose = row of the red line, guide = aiming help
DIFFS = [
    dict(name="Easy", info="3 colours, ceiling drops every 8 shots",
         rows=3, colors=3, drop=8, lose=12, guide='full'),
    dict(name="Medium", info="Classic: 4 colours, drops every 6",
         rows=4, colors=4, drop=6, lose=12, guide='full'),
    dict(name="Hard", info="5 colours, drops every 4, short guide",
         rows=6, colors=5, drop=4, lose=11, guide='short'),
    dict(name="Expert", info="6 colours, drops every 3, no guide",
         rows=7, colors=6, drop=3, lose=10, guide='none'),
]


class BubbleShooter(ov.BaseGame):
    keys = ('LEFT_ARROW', 'RIGHT_ARROW', 'A', 'D', 'SPACE', 'C', 'P', 'M',
            'ONE', 'TWO', 'THREE', 'FOUR')

    def setup(self):
        self.best = 0
        self.diff = 1                     # index in DIFFS
        self.menu = True                  # difficulty menu is shown first
        self.menu_hover = -1
        self.record_mgrs = {}             # diff -> RecordManager (bubbleshooter_<name>.json)
        self.best_cache = {}              # diff -> best first-board clear time (s) or None

    # ---- records
    def _mgr(self, d):
        if d not in self.record_mgrs:
            try:
                self.record_mgrs[d] = _rec.RecordManager(
                    game_name="bubbleshooter_" + DIFFS[d]['name'].lower())
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
                m.add_win_record(score=self.score, best_time=self.play_time)
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
        return (self.left + 2 * cs, self.top - (2.0 + 1.5 * i + 1.1) * cs, 7 * cs, 1.1 * cs)

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

    def reset(self):
        self.cfg = DIFFS[self.diff]
        self.play_time = 0.0              # seconds actually played
        self.t_first = None               # time taken to clear the first board
        self.off = 0                      # row parity shift (see _add_row)
        self.score = 0
        self.level = 1
        self.state = 'playing'            # playing | paused | over
        self.msg, self.msg_t = '', 0.0
        self.fx = []                      # pop / fall effects
        self.proj = None                  # flying bubble
        self.misses = 0
        self.aim = math.pi / 2
        self.cs = 30
        self._fill(self.cfg['rows'])
        self.cur = self._pick()
        self.nxt = self._pick()

    # ---- grid helpers (cell units, y points DOWN, bubble radius 0.5)
    def _ncols(self, r):
        return COLS if (r + self.off) % 2 == 0 else COLS - 1

    def _center(self, r, c):
        x = c + 0.5 if (r + self.off) % 2 == 0 else c + 1.0
        return x, 0.5 + r * ROWH

    def _neighbors(self, r, c):
        out = []
        for dc in (-1, 1):
            if 0 <= c + dc < self._ncols(r):
                out.append((r, c + dc))
        cols = (c - 1, c) if (r + self.off) % 2 == 0 else (c, c + 1)
        for rr in (r - 1, r + 1):
            if 0 <= rr < ALLOC:
                for cc in cols:
                    if 0 <= cc < self._ncols(rr):
                        out.append((rr, cc))
        return out

    def _ncolor(self):
        return min(6, self.cfg['colors'] + self.level - 1)

    def _fill(self, nrows):
        self.rows = [[None] * self._ncols(r) for r in range(ALLOC)]
        for r in range(nrows):
            for c in range(self._ncols(r)):
                self.rows[r][c] = random.randrange(self._ncolor())

    def _present(self):
        return {v for row in self.rows for v in row if v is not None}

    def _pick(self):
        present = self._present()
        return random.choice(sorted(present)) if present else random.randrange(self._ncolor())

    def _empty(self):
        return not self._present()

    # ---- matching
    def _cluster(self, r, c):
        col = self.rows[r][c]
        seen, stack = {(r, c)}, [(r, c)]
        while stack:
            cr, cc = stack.pop()
            for n in self._neighbors(cr, cc):
                if n not in seen and self.rows[n[0]][n[1]] == col:
                    seen.add(n)
                    stack.append(n)
        return seen

    def _anchored(self):
        stack = [(0, c) for c in range(self._ncols(0)) if self.rows[0][c] is not None]
        seen = set(stack)
        while stack:
            cr, cc = stack.pop()
            for n in self._neighbors(cr, cc):
                if n not in seen and self.rows[n[0]][n[1]] is not None:
                    seen.add(n)
                    stack.append(n)
        return seen

    def _add_row(self):
        self.off ^= 1
        self.rows.insert(0, [random.randrange(self._ncolor()) for _ in range(self._ncols(0))])
        self.rows.pop()

    def _lost(self):
        return any(v is not None for r in range(self.cfg['lose'], ALLOC) for v in self.rows[r])

    def _hits(self, x, y):
        r0 = int((y - 0.5) / ROWH)
        for r in range(max(0, r0 - 1), min(ALLOC, r0 + 3)):
            for c, v in enumerate(self.rows[r]):
                if v is not None:
                    bx, by = self._center(r, c)
                    if (bx - x) ** 2 + (by - y) ** 2 < 0.81:
                        return True
        return False

    def _snap(self, x, y):
        r0 = int(round((y - 0.5) / ROWH))
        best = None
        for r in range(max(0, r0 - 1), min(ALLOC, r0 + 2)):
            for c in range(self._ncols(r)):
                if self.rows[r][c] is None:
                    bx, by = self._center(r, c)
                    d = (bx - x) ** 2 + (by - y) ** 2
                    if best is None or d < best[0]:
                        best = (d, r, c)
        return None if best is None else (best[1], best[2])

    # ---- shooting
    def _shoot(self):
        if self.proj or self.state != 'playing':
            return
        self.proj = {'x': SHOOT[0], 'y': SHOOT[1], 'vx': math.cos(self.aim),
                     'vy': -math.sin(self.aim), 'col': self.cur}
        self.cur, self.nxt = self.nxt, self._pick()

    def _swap(self):
        if self.state == 'playing':
            self.cur, self.nxt = self.nxt, self.cur

    def _land(self):
        p, self.proj = self.proj, None
        cell = self._snap(p['x'], p['y'])
        if cell is None:
            self._game_over()
            return
        r, c = cell
        self.rows[r][c] = p['col']
        group = self._cluster(r, c)
        if len(group) >= 3:
            gained = 10 * len(group) + 5 * (len(group) - 3)
            for rr, cc in group:
                x, y = self._center(rr, cc)
                self.fx.append({'k': 'pop', 'x': x, 'y': y, 'col': p['col'], 't': 0.0})
                self.rows[rr][cc] = None
            anchored = self._anchored()
            for rr in range(ALLOC):
                for cc in range(self._ncols(rr)):
                    v = self.rows[rr][cc]
                    if v is not None and (rr, cc) not in anchored:
                        x, y = self._center(rr, cc)
                        self.fx.append({'k': 'fall', 'x': x, 'y': y, 'col': v,
                                        'vy': 0.0, 'vx': random.uniform(-1.5, 1.5), 't': 0.0})
                        self.rows[rr][cc] = None
                        gained += 20
            self.score += gained
            self.misses = 0
        else:
            self.misses += 1
            if self.misses >= self.cfg['drop']:
                self.misses = 0
                self._add_row()
        self.best = max(self.best, self.score)

        if self._empty():
            if self.level == 1:           # first board cleared: this is the "win"
                self.t_first = self.play_time
                self._record_clear()
            self.level += 1
            self.score += 500
            self.best = max(self.best, self.score)
            self.msg, self.msg_t = "Level %d!" % self.level, 1.6
            if self.t_first is not None and self.level == 2:
                self.msg = "Cleared in %s!  Level 2" % self._fmt(self.t_first)
            self.off = 0
            self.misses = 0
            self._fill(min(9, self.cfg['rows'] + self.level - 1))
        elif self._lost():
            self._game_over()
            return
        present = self._present()
        if present:
            if self.cur not in present:
                self.cur = random.choice(sorted(present))
            if self.nxt not in present:
                self.nxt = random.choice(sorted(present))

    def _game_over(self):
        if self.state != 'over' and self.level == 1:
            self._record_loss()           # lost before ever clearing the first board
        self.state = 'over'
        self.proj = None
        self.best = max(self.best, self.score)

    def _fly(self, dt):
        p = self.proj
        dist = SPEED * dt
        n = max(1, math.ceil(dist / 0.15))
        step = dist / n
        for _ in range(n):
            p['x'] += p['vx'] * step
            p['y'] += p['vy'] * step
            if p['x'] < 0.5:
                p['x'], p['vx'] = 1.0 - p['x'], -p['vx']
            elif p['x'] > COLS - 0.5:
                p['x'], p['vx'] = 2 * (COLS - 0.5) - p['x'], -p['vx']
            if p['y'] <= 0.5:
                p['y'] = 0.5
                self._land()
                return
            if self._hits(p['x'], p['y']):
                self._land()
                return

    def update(self, dt):
        if self.menu or self.state == 'paused':
            return False
        if self.state == 'playing':
            self.play_time += dt
        changed = False
        if self.msg_t > 0:
            self.msg_t -= dt
            changed = True
        if self.fx:
            for e in self.fx:
                e['t'] += dt
                if e['k'] == 'fall':
                    e['vy'] += 32 * dt
                    e['y'] += e['vy'] * dt
                    e['x'] += e['vx'] * dt
            self.fx = [e for e in self.fx
                       if (e['k'] == 'pop' and e['t'] < 0.25) or (e['k'] == 'fall' and e['y'] < HC + 1)]
            changed = True
        if self.proj and self.state == 'playing':
            self._fly(dt)
            changed = True
        return changed

    # ---- input
    def _set_aim(self, x, y):
        sx = self.left + SHOOT[0] * self.cs
        sy = self.top - SHOOT[1] * self.cs
        dx, dy = x - sx, y - sy
        a = math.atan2(dy, dx) if dy > 0 else (AIM_MIN if dx > 0 else AIM_MAX)
        a = max(AIM_MIN, min(AIM_MAX, a))
        changed = abs(a - self.aim) > 1e-4
        self.aim = a
        return changed

    def move(self, x, y):
        if self.menu:
            h = self._menu_idx(x, y)
            changed = h != self.menu_hover
            self.menu_hover = h
            return changed
        if x < -1e5 or self.state != 'playing':
            return False
        return self._set_aim(x, y)

    def click(self, x, y, button):
        if self.menu:
            if button == 'LEFT':
                i = self._menu_idx(x, y)
                if i >= 0:
                    self._start(i)
            return
        if self.state == 'over':
            self.reset()
        elif self.state == 'paused':
            self.state = 'playing'
        elif button == 'RIGHT':
            self._swap()
        else:
            self._set_aim(x, y)
            self._shoot()

    def key(self, key, repeat):
        if self.menu:
            i = {'ONE': 0, 'TWO': 1, 'THREE': 2, 'FOUR': 3}.get(key)
            if i is not None:
                self._start(i)
            return
        if key == 'M':
            self.menu, self.menu_hover, self.proj = True, -1, None
        elif key in ('LEFT_ARROW', 'A') and self.state == 'playing':
            self.aim = min(AIM_MAX, self.aim + math.radians(2.5))
        elif key in ('RIGHT_ARROW', 'D') and self.state == 'playing':
            self.aim = max(AIM_MIN, self.aim - math.radians(2.5))
        elif key == 'SPACE':
            if self.state == 'over':
                self.reset()
            elif not repeat:
                self._shoot()
        elif key == 'C' and not repeat:
            self._swap()
        elif key == 'P' and self.state != 'over':
            self.state = 'paused' if self.state == 'playing' else 'playing'

    # ---- drawing
    def layout(self, view):
        self.cs = self.fit_cell(view, COLS, HC, max_cell=44)
        self.place(view, COLS * self.cs, HC * self.cs)

    def _p(self, gx, gy):
        return self.left + gx * self.cs, self.top - gy * self.cs

    def _bubble(self, c, gx, gy, col, r=0.5, a=1.0):
        px, py = self._p(gx, gy)
        R = r * self.cs
        col = ov.alpha(col, a)
        c.circle(px, py, R * 0.97, ov.shade(col, 0.65), 18)
        c.circle(px, py, R * 0.86, col, 18)
        c.circle(px - R * 0.30, py + R * 0.32, R * 0.26, (1, 1, 1, 0.55 * a), 10)

    def _trace(self):
        """Guide dots + predicted landing cell for the current aim."""
        x, y = SHOOT
        vx, vy = math.cos(self.aim), -math.sin(self.aim)
        pts, dist, step = [], 0.0, 0.1
        for _ in range(700):
            x += vx * step
            y += vy * step
            dist += step
            if x < 0.5:
                x, vx = 1.0 - x, -vx
            elif x > COLS - 0.5:
                x, vx = 2 * (COLS - 0.5) - x, -vx
            if y <= 0.5 or self._hits(x, y):
                break
            if dist > 1.6 and int(dist / 0.55) > len(pts):
                pts.append((x, y))
        return pts, self._snap(x, max(y, 0.5))

    def draw(self, c):
        u, cs = self.u, self.cs
        self.draw_frame(c)

        main = self.msg if self.msg_t > 0 else "BUBBLE SHOOTER"
        shown = self.t_first if self.t_first is not None else self.play_time
        self.draw_header(c, main, "%s  |  Score %d  Level %d  |  Time %s (best %s)  |  Drop in %d" % (
            self.cfg['name'], self.score, self.level, self._fmt(shown),
            self._fmt(self._best_time(self.diff)), self.cfg['drop'] - self.misses),
            ov.C_GOLD if self.msg_t > 0 else ov.C_WHITE)

        bx, by = self._p(0, HC)
        c.rect(bx, by, COLS * cs, HC * cs, C_BOARD)

        # lose line
        ly = self.top - self.cfg['lose'] * ROWH * cs
        for k in range(COLS * 2):
            c.rect(self.left + k * cs * 0.5 + cs * 0.08, ly - u, cs * 0.34, 2 * u, (1, 0.3, 0.3, 0.55))

        # bubbles
        for r in range(ALLOC):
            for k, v in enumerate(self.rows[r]):
                if v is not None:
                    gx, gy = self._center(r, k)
                    self._bubble(c, gx, gy, COLORS[v])

        # guide + shooter
        if self.state == 'playing' and not self.proj and not self.menu and self.cfg['guide'] != 'none':
            pts, cell = self._trace()
            if self.cfg['guide'] == 'short':
                pts, cell = pts[:7], None
            for gx, gy in pts:
                px, py = self._p(gx, gy)
                c.circle(px, py, cs * 0.07, (1, 1, 1, 0.55), 8)
            if cell:
                gx, gy = self._center(*cell)
                px, py = self._p(gx, gy)
                c.ring(px, py, cs * 0.45, max(1.5, cs * 0.05), (1, 1, 1, 0.30), 24)

        sx, sy = self._p(*SHOOT)
        c.circle(sx, sy, cs * 0.85, (0.20, 0.22, 0.27, 1), 28)
        c.ring(sx, sy, cs * 0.85, max(2, cs * 0.06), ov.C_BORDER, 28)
        if self.state == 'playing':
            ex, ey = sx + math.cos(self.aim) * cs * 1.5, sy + math.sin(self.aim) * cs * 1.5
            c.line((sx, sy), (ex, ey), max(3, cs * 0.14), ov.C_GOLD)
            c.circle(ex, ey, cs * 0.12, ov.C_GOLD, 10)
        if not self.proj:
            self._bubble(c, SHOOT[0], SHOOT[1], COLORS[self.cur])
        c.text("NEXT", *self._p(0.95, HC - 1.35), 10 * u, ov.C_TEXT)
        self._bubble(c, 0.95, HC - 0.55, COLORS[self.nxt], r=0.36)

        if self.proj:
            self._bubble(c, self.proj['x'], self.proj['y'], COLORS[self.proj['col']])

        # effects
        for e in self.fx:
            if e['k'] == 'pop':
                t = e['t'] / 0.25
                self._bubble(c, e['x'], e['y'], COLORS[e['col']], r=0.5 + 0.35 * t, a=max(0.0, 1 - t))
            else:
                self._bubble(c, e['x'], e['y'], COLORS[e['col']])

        bw, bh = COLS * cs, HC * cs
        if self.menu:
            c.rect(bx, by, bw, bh, (0.0, 0.0, 0.0, 0.72))
            c.text("Select difficulty", self.left + bw / 2, self.top - 1.0 * cs, cs * 0.6, ov.C_WHITE)
            for i, d in enumerate(DIFFS):
                x0, y0, w0, h0 = self._menu_rect(i)
                c.rect(x0, y0, w0, h0, ov.C_CELL_HOVER if i == self.menu_hover else ov.C_CELL)
                c.text("%d  %s" % (i + 1, d['name']), x0 + 0.3 * cs, y0 + h0 * 0.68,
                       cs * 0.46, ov.C_WHITE, 'left')
                c.text(d['info'], x0 + 0.3 * cs, y0 + h0 * 0.28, cs * 0.27, ov.C_TEXT, 'left')
                bt = self._best_time(i)
                c.text("Best %s" % self._fmt(bt) if bt is not None else "No clear yet",
                       x0 + w0 - 0.3 * cs, y0 + h0 * 0.68, cs * 0.30,
                       ov.C_GOLD if bt is not None else ov.C_TEXT, 'right')
            c.text("Best = fastest clear of the first board", self.left + bw / 2,
                   self.top - 9.0 * cs, cs * 0.28, ov.C_TEXT)
            self.draw_hint(c, "Click or press 1-4 to pick a difficulty   ESC quit")
            return
        if self.state == 'paused':
            self.banner(c, bx, by, bw, bh, "Paused", "Press P or click to resume")
        elif self.state == 'over':
            self.banner(c, bx, by, bw, bh, "Game over",
                        "Score %d  -  click or SPACE to retry, M for menu" % self.score)

        self.draw_hint(c, "Aim: mouse / A D   Shoot: click / SPACE   Swap: RMB / C   M menu   ESC")


RUNNER = ov.Runner("bubbleshooter", GAME_NAME, BubbleShooter, [
    "Mouse: aim   LMB / SPACE: shoot",
    "RMB / C: swap with the next bubble",
    "A / D (arrows): fine aim",
    "Match 3+ to pop; hanging bubbles fall",
    "M: difficulty menu   P: pause",
    "R: restart   ESC: quit",
])


def draw_ui(layout, context):
    RUNNER.draw_ui(layout)


def register():
    RUNNER.register()


def unregister():
    RUNNER.unregister()
