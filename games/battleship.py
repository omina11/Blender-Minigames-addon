"""Battleship (Battaglia navale) - you vs the CPU, classic 10x10 with 5 ships.

The best winning time (from the first shot to the last ship sunk) is saved for
each CPU level (battleship_easy / battleship_hard).
"""

import random
import time
import traceback

from . import _overlay as ov
from . import _record as _rec

GAME_NAME = "Battleship"
GAME_ICON = 'MOD_OCEAN'

SIZE = 10
FLEET = (5, 4, 3, 3, 2)

C_WATER = (0.14, 0.28, 0.46, 1.0)
C_WATER_HOVER = (0.24, 0.42, 0.65, 1.0)
C_SHIP = (0.58, 0.64, 0.70, 1.0)
C_SHIP_REVEAL = (0.36, 0.40, 0.46, 1.0)
C_SUNK = (0.45, 0.13, 0.13, 1.0)
C_HIT = (1.0, 0.35, 0.25, 1.0)
C_MISS = (0.88, 0.94, 1.0, 0.9)


class Side:
    """One player's fleet and the shots fired AT it."""

    def __init__(self):
        self.occ = {}       # cell -> ship index
        self.ships = []     # list of cell lists
        self.shots = {}     # cell -> 'hit' | 'miss'
        for idx, length in enumerate(FLEET):
            while True:
                horiz = random.random() < 0.5
                if horiz:
                    r, c = random.randrange(SIZE), random.randrange(SIZE - length + 1)
                    cells = [(r, c + k) for k in range(length)]
                else:
                    r, c = random.randrange(SIZE - length + 1), random.randrange(SIZE)
                    cells = [(r + k, c) for k in range(length)]
                if any(x in self.occ for x in cells):
                    continue
                for x in cells:
                    self.occ[x] = idx
                self.ships.append(cells)
                break

    def sunk(self, idx):
        return all(cell in self.shots for cell in self.ships[idx])

    def all_sunk(self):
        return all(self.sunk(i) for i in range(len(self.ships)))

    def remaining(self):
        return sum(1 for i in range(len(self.ships)) if not self.sunk(i))

    def fire(self, cell):
        """Returns ('miss'|'hit'|'sunk', ship_length_or_0)."""
        if cell in self.occ:
            self.shots[cell] = 'hit'
            idx = self.occ[cell]
            if self.sunk(idx):
                return 'sunk', len(self.ships[idx])
            return 'hit', 0
        self.shots[cell] = 'miss'
        return 'miss', 0


class Battleship(ov.BaseGame):
    keys = ('S', 'SPACE', 'RET', 'D')

    def setup(self):
        self.hard = True
        self.score = {'me': 0, 'cpu': 0}
        self.record_mgrs = {}             # 'easy' / 'hard' -> RecordManager
        self.best_cache = {}              # 'easy' / 'hard' -> best time in seconds (or None)

    # ---- records
    def _lv(self):
        return 'hard' if self.hard else 'easy'

    def _mgr(self, lv):
        if lv not in self.record_mgrs:
            try:
                self.record_mgrs[lv] = _rec.RecordManager(game_name="battleship_" + lv)
            except Exception:
                self.record_mgrs[lv] = None
        return self.record_mgrs[lv]

    def _best_time(self, lv):
        if lv not in self.best_cache:
            m = self._mgr(lv)
            try:
                self.best_cache[lv] = m.get_records().get("best_time") if m else None
            except Exception:
                self.best_cache[lv] = None
        return self.best_cache[lv]

    @staticmethod
    def _fmt(t):
        return "--" if t is None else "%d:%02d" % divmod(int(round(t)), 60)

    def _finish(self, winner):
        """Save the result once the game is over (level changed mid-game = no time)."""
        self.t_end = time.time()
        lv = self.play_lv
        m = self._mgr(lv)
        if not m:
            return
        try:
            if winner == 'me':
                t = None if self.changed else self.t_end - self.t0
                prev = self._best_time(lv)
                try:
                    m.add_win_record(score=0, best_time=t)
                except TypeError:         # _record.py without best_time support
                    m.add_win_record(score=0)
                self.new_best = t is not None and (prev is None or t < prev)
                self.best_cache.pop(lv, None)
            else:
                m.add_lose_record()
        except Exception:
            traceback.print_exc()

    def reset(self):
        self.me, self.foe = Side(), Side()
        self.phase = 'place'      # place | play | over
        self.turn = 'me'
        self.msg = ""
        self.winner = None
        self.hover = None
        self.cpu_t = 0.0
        self.cs = 30
        self.t0 = self.t_end = 0.0
        self.shown = 0
        self.changed = False
        self.new_best = False
        self.play_lv = self._lv()

    # ---- logic
    def _describe(self, who, res):
        kind, length = res
        if kind == 'sunk':
            return "%s sank a %d-cell ship!" % (who, length)
        return "%s: %s" % (who, "Hit!" if kind == 'hit' else "Miss.")

    def cpu_pick(self):
        shots = self.me.shots
        free = [(r, c) for r in range(SIZE) for c in range(SIZE) if (r, c) not in shots]
        if not self.hard:
            return random.choice(free)
        hits = [cell for cell, v in shots.items()
                if v == 'hit' and not self.me.sunk(self.me.occ[cell])]
        if hits:
            cands = []
            rows = {r for r, _ in hits}
            cols = {c for _, c in hits}
            if len(hits) >= 2 and len(rows) == 1:
                r = next(iter(rows))
                cands += [(r, min(cols) - 1), (r, max(cols) + 1)]
            elif len(hits) >= 2 and len(cols) == 1:
                c = next(iter(cols))
                cands += [(min(rows) - 1, c), (max(rows) + 1, c)]
            cands = [x for x in cands if x in free]
            if not cands:
                for r, c in hits:
                    cands += [(r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)]
                cands = [x for x in cands if x in free]
            if cands:
                return random.choice(cands)
        parity = [x for x in free if (x[0] + x[1]) % 2 == 0]
        return random.choice(parity or free)

    def update(self, dt):
        r = self._update_core(dt)
        if self.phase == 'play':                  # redraw when the timer second changes
            s = int(time.time() - self.t0)
            if s != self.shown:
                self.shown = s
                r = True
        return r

    def _update_core(self, dt):
        if self.phase == 'play' and self.turn == 'cpu':
            self.cpu_t += dt
            if self.cpu_t >= 0.6:
                self.cpu_t = 0.0
                res = self.me.fire(self.cpu_pick())
                self.msg = self._describe("Enemy", res)
                if self.me.all_sunk():
                    self.phase, self.winner = 'over', 'cpu'
                    self.score['cpu'] += 1
                    self._finish('cpu')
                else:
                    self.turn = 'me'
                return True
        return False

    def key(self, key, repeat):
        if key == 'D':
            self.hard = not self.hard
            if self.phase == 'play':
                self.changed = True               # no time record if you switch level mid-game
            else:
                self.play_lv = self._lv()
        elif self.phase == 'place':
            if key == 'S':
                self.me = Side()
            elif key in ('SPACE', 'RET'):
                self.phase = 'play'
                self.play_lv = self._lv()
                self.t0 = time.time()
                self.msg = "Fire at the enemy waters!"
        elif self.phase == 'over' and key in ('SPACE', 'RET'):
            self.reset()

    # ---- input
    def layout(self, view):
        self.cs = self.fit_cell(view, 2 * SIZE + 1, SIZE, max_cell=36, extra_h=22)
        cs, u = self.cs, view.u
        self.place(view, (2 * SIZE + 1) * cs, SIZE * cs + 22 * u, header=50)
        self.btop = self.top - 22 * u              # top of the boards
        self.lx = self.left                        # my board (left)
        self.rx = self.left + (SIZE + 1) * cs      # enemy board (right)

    def _foe_cell(self, x, y):
        cr = ov.cell_at(x, y, self.rx, self.btop, self.cs, SIZE, SIZE)
        return None if cr is None else (cr[1], cr[0])      # (row, col)

    def move(self, x, y):
        h = self._foe_cell(x, y)
        changed = h != self.hover
        self.hover = h
        return changed

    def click(self, x, y, button):
        if button != 'LEFT':
            return
        if self.phase == 'over':
            self.reset()
            return
        if self.phase != 'play' or self.turn != 'me':
            return
        cell = self._foe_cell(x, y)
        if cell is None or cell in self.foe.shots:
            return
        res = self.foe.fire(cell)
        self.msg = self._describe("You", res)
        if self.foe.all_sunk():
            self.phase, self.winner = 'over', 'me'
            self.score['me'] += 1
            self._finish('me')
        else:
            self.turn, self.cpu_t = 'cpu', 0.0

    # ---- drawing
    def _board(self, c, side, bx, mine):
        cs, u = self.cs, self.u
        for r in range(SIZE):
            for k in range(SIZE):
                cell = (r, k)
                x, y = bx + k * cs, self.btop - (r + 1) * cs
                col = C_WATER
                ship = cell in side.occ
                if ship and (mine or side.sunk(side.occ[cell])):
                    col = C_SUNK if side.sunk(side.occ[cell]) else C_SHIP
                elif ship and self.phase == 'over':
                    col = C_SHIP_REVEAL
                elif (not mine and cell == self.hover and self.phase == 'play'
                      and self.turn == 'me' and cell not in side.shots):
                    col = C_WATER_HOVER
                c.rect(x + 1, y + 1, cs - 2, cs - 2, col)
                v = side.shots.get(cell)
                cx, cy = x + cs / 2, y + cs / 2
                if v == 'hit':
                    c.circle(cx, cy, cs * 0.28, C_HIT, 16)
                    c.circle(cx, cy, cs * 0.12, (1, 0.85, 0.4, 1), 12)
                elif v == 'miss':
                    c.circle(cx, cy, cs * 0.11, C_MISS, 12)

    def draw(self, c):
        u, cs = self.u, self.cs
        self.draw_frame(c)

        if self.phase == 'place':
            main, col = "Position your fleet", ov.C_WHITE
            sub = "SPACE: start     S: shuffle     D: CPU %s     Best: %s" % (
                self._lv(), self._fmt(self._best_time(self._lv())))
        elif self.phase == 'over':
            won = self.winner == 'me'
            main, col = ("Victory!" if won else "Defeat!"), (ov.C_GOOD if won else ov.C_BAD)
            sub = "Wins  You %d - %d CPU   |   %s%s   |   click or SPACE for a new game" % (
                self.score['me'], self.score['cpu'],
                ("Time %s" % self._fmt(self.t_end - self.t0)) if won else "CPU " + self.play_lv,
                "  NEW BEST!" if self.new_best else "")
        else:
            main = "Your turn - fire!" if self.turn == 'me' else "Enemy is aiming..."
            col = ov.C_WHITE
            sub = "%s   |   %s   Time %s (best %s)" % (
                self.msg, self.play_lv, self._fmt(time.time() - self.t0),
                self._fmt(self._best_time(self.play_lv)))
        self.draw_header(c, main, sub, col)

        c.text("Your fleet  (%d left)" % self.me.remaining(), self.lx + SIZE * cs / 2,
               self.top - 11 * u, 12 * u, ov.C_TEXT)
        c.text("Enemy waters  (%d left)" % self.foe.remaining(), self.rx + SIZE * cs / 2,
               self.top - 11 * u, 12 * u, ov.C_TEXT)
        self._board(c, self.me, self.lx, True)
        self._board(c, self.foe, self.rx, False)

        if self.phase == 'place':
            self.draw_hint(c, "SPACE start   S shuffle   D level   R restart   ESC quit")
        else:
            self.draw_hint(c, "Click enemy waters   D level   R restart   ESC quit")


RUNNER = ov.Runner("battleship", GAME_NAME, Battleship, [
    "S: shuffle your fleet, SPACE: start",
    "LMB: fire at enemy waters",
    "D: easy / hard CPU",
    "R: restart   ESC: quit",
])


def draw_ui(layout, context):
    RUNNER.draw_ui(layout)


def register():
    RUNNER.register()


def unregister():
    RUNNER.unregister()
