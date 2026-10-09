"""Mastermind - crack the secret colour code (drawn as a GPU overlay).

After every guess you get green pegs (right colour, right spot) and white
pegs (right colour, wrong spot). The pegs say nothing about *which* position.

Easy additionally marks every single peg of your guess (green ring = right
colour and spot, white ring = right colour wrong spot, X = not in the code).
Medium and Hard only give the generic green / white counts.
Pick a difficulty first; best time and best score are saved for each one.
"""

import random
import time
import traceback

from . import _overlay as ov
from . import _record as _rec

GAME_NAME = "Mastermind"
GAME_ICON = 'COLOR'

LEVELS = [             # name, pegs, colours, tries
    ("Easy", 4, 6, 10),
    ("Medium", 5, 6, 10),
    ("Hard", 6, 8, 12),
]
LEVEL_INFO = ["4 pegs, 6 colours, every peg is marked",
              "5 pegs, 6 colours, green / white counts",
              "6 pegs, 8 colours, green / white counts"]
HINTS = (True, False, False)       # per-peg marks (only on Easy)

COLORS = [
    (0.92, 0.28, 0.28, 1.0),    # 1 red
    (1.00, 0.60, 0.20, 1.0),    # 2 orange
    (0.97, 0.85, 0.25, 1.0),    # 3 yellow
    (0.30, 0.78, 0.38, 1.0),    # 4 green
    (0.28, 0.55, 0.98, 1.0),    # 5 blue
    (0.66, 0.40, 0.92, 1.0),    # 6 purple
    (0.95, 0.45, 0.70, 1.0),    # 7 pink
    (0.25, 0.82, 0.85, 1.0),    # 8 cyan
]

C_EXACT = (0.35, 0.92, 0.45, 1.0)
C_MISPLACED = (0.96, 0.96, 0.98, 1.0)
C_ROW = (0.17, 0.18, 0.22, 1.0)
C_ROW_CUR = (0.25, 0.29, 0.37, 1.0)
C_EMPTY = (0.08, 0.09, 0.11, 1.0)
C_WRONG = (0.95, 0.30, 0.30, 1.0)

DIGIT_KEYS = {}
for _i, _n in enumerate(('ONE', 'TWO', 'THREE', 'FOUR', 'FIVE', 'SIX', 'SEVEN', 'EIGHT')):
    DIGIT_KEYS[_n] = _i
    DIGIT_KEYS['NUMPAD_%d' % (_i + 1)] = _i


def feedback(secret, guess):
    """Return (exact, misplaced) for a guess, with correct handling of repeats."""
    exact = 0
    sc, gc = {}, {}
    for s, g in zip(secret, guess):
        if s == g:
            exact += 1
        else:
            sc[s] = sc.get(s, 0) + 1
            gc[g] = gc.get(g, 0) + 1
    misplaced = sum(min(n, gc.get(col, 0)) for col, n in sc.items())
    return exact, misplaced


def marks(secret, guess):
    """Per-peg result of a guess: 2 = right colour and spot, 1 = right colour wrong spot, 0 = not in the code."""
    res = [0] * len(guess)
    rem = {}
    for i, (s, g) in enumerate(zip(secret, guess)):
        if s == g:
            res[i] = 2
        else:
            rem[s] = rem.get(s, 0) + 1
    for i, g in enumerate(guess):
        if res[i] == 0 and rem.get(g, 0) > 0:
            res[i] = 1
            rem[g] -= 1
    return res


class Mastermind(ov.BaseGame):
    keys = tuple(DIGIT_KEYS) + ('RET', 'NUMPAD_ENTER', 'SPACE', 'BACK_SPACE', 'DEL',
                                'LEFT_ARROW', 'RIGHT_ARROW', 'D', 'X', 'L')

    def setup(self):
        self.level = 1             # index in LEVELS
        self.repeats = True        # the secret may use the same colour more than once
        self.labels = False        # print the colour number on the pegs
        self.wins = 0
        self.losses = 0
        self.menu = True           # difficulty menu is shown first
        self.menu_hover = -1
        self.record_mgrs = {}      # level -> RecordManager (mastermind_<name>.json)
        self.best_cache = {}       # level -> (best_time, best_score)

    # ---- records
    def _mgr(self, lv):
        if lv not in self.record_mgrs:
            try:
                self.record_mgrs[lv] = _rec.RecordManager(game_name="mastermind_" + LEVELS[lv][0].lower())
            except Exception:
                self.record_mgrs[lv] = None
        return self.record_mgrs[lv]

    def _best(self, lv):
        if lv not in self.best_cache:
            m = self._mgr(lv)
            try:
                r = m.get_records() if m else {}
                self.best_cache[lv] = (r.get("best_time"), int(r.get("highest_score", 0) or 0))
            except Exception:
                self.best_cache[lv] = (None, 0)
        return self.best_cache[lv]

    @staticmethod
    def _fmt(t):
        return "--" if t is None else "%d:%02d" % divmod(int(round(t)), 60)

    def _record_win(self):
        m = self._mgr(self.level)
        if not m:
            return
        try:
            try:
                m.add_win_record(score=self.score, best_time=self.elapsed)
            except TypeError:              # _record.py without best_time support
                m.add_win_record(score=self.score)
            self.best_cache.pop(self.level, None)
        except Exception:
            traceback.print_exc()

    def _record_loss(self):
        m = self._mgr(self.level)
        if m:
            try:
                m.add_lose_record()
            except Exception:
                traceback.print_exc()

    # ---- difficulty menu
    def _menu_rect(self, i):
        cs = self.cs
        return (self.left + 0.3 * cs, self.top - (2.0 + 1.9 * i + 1.5) * cs, self.bw - 0.6 * cs, 1.5 * cs)

    def _menu_idx(self, x, y):
        for i in range(len(LEVELS)):
            bx, by, bw, bh = self._menu_rect(i)
            if bx <= x <= bx + bw and by <= y <= by + bh:
                return i
        return -1

    def _start(self, lv):
        self.level = lv
        self.menu = False
        self.reset()

    def update(self, dt):
        if self.menu or self.state != 'play':
            return False
        s = int(time.time() - self.t0)
        if s != self.shown:
            self.shown = s
            return True
        return False

    def reset(self):
        _, self.n, self.k, self.tries = LEVELS[self.level]
        if self.repeats:
            self.secret = tuple(random.randrange(self.k) for _ in range(self.n))
        else:
            self.secret = tuple(random.sample(range(self.k), self.n))
        self.guesses = []          # (guess, exact, misplaced)
        self.row = [None] * self.n
        self.cur = 0
        self.state = 'play'        # play | win | lose
        self.hover = None
        self.cs = 40
        self.t0 = time.time()
        self.elapsed = 0.0
        self.score = 0
        self.shown = 0

    # ---- logic
    def _put(self, col):
        if self.state != 'play':
            return
        self.row[self.cur] = col
        for step in range(1, self.n + 1):
            j = (self.cur + step) % self.n
            if self.row[j] is None:
                self.cur = j
                break

    def _delete(self):
        if self.state != 'play':
            return
        if self.row[self.cur] is None and self.cur > 0:
            self.cur -= 1
        self.row[self.cur] = None

    def _clear_row(self):
        self.row = [None] * self.n
        self.cur = 0

    def _submit(self):
        if self.state != 'play' or None in self.row:
            return
        guess = tuple(self.row)
        ex, mi = feedback(self.secret, guess)
        self.guesses.append((guess, ex, mi, marks(self.secret, guess)))
        if ex == self.n:
            self.state = 'win'
            self.wins += 1
            self.elapsed = time.time() - self.t0
            # fewer attempts and less time = higher score
            self.score = (self.tries - len(self.guesses) + 1) * 100 + max(0, 200 - int(self.elapsed))
            self._record_win()
        elif len(self.guesses) >= self.tries:
            self.state = 'lose'
            self.losses += 1
            self._record_loss()
        else:
            self._clear_row()

    # ---- geometry (all derived from the current cell size)
    def layout(self, view):
        cols = self.n + 1.9
        rows = self.tries + 3.0
        self.cs = self.fit_cell(view, cols, rows, max_cell=52, min_cell=16)
        self.bw, self.bh = cols * self.cs, rows * self.cs
        self.place(view, self.bw, self.bh)

    def _row_y(self, k):
        """Bottom y of attempt row k (0 = first attempt, drawn just below the secret)."""
        return self.top - (2 + k) * self.cs

    def _bottom(self):
        return self.top - self.bh

    def _pal_pos(self, i):
        sp = self.bw / self.k
        return self.left + (i + 0.5) * sp, self._bottom() + 1.45 * self.cs, min(0.32 * self.cs, sp * 0.42)

    def _buttons(self):
        cs = self.cs
        x0 = self.left + (self.bw - 3.9 * cs) / 2
        y0 = self._bottom() + 0.15 * cs
        return {'submit': (x0, y0, 2.2 * cs, 0.7 * cs),
                'clear': (x0 + 2.5 * cs, y0, 1.4 * cs, 0.7 * cs)}

    def _target_at(self, x, y):
        cs = self.cs
        for i in range(self.k):
            px, py, r = self._pal_pos(i)
            if (x - px) ** 2 + (y - py) ** 2 <= (r * 1.15) ** 2:
                return ('pal', i)
        for name, (bx, by, bw, bh) in self._buttons().items():
            if bx <= x <= bx + bw and by <= y <= by + bh:
                return (name, 0)
        if self.state == 'play':
            ry = self._row_y(len(self.guesses))
            for i in range(self.n):
                sx, sy = self.left + (i + 0.5) * cs, ry + cs / 2
                if (x - sx) ** 2 + (y - sy) ** 2 <= (0.42 * cs) ** 2:
                    return ('slot', i)
        return None

    # ---- input
    def move(self, x, y):
        if self.menu:
            h = self._menu_idx(x, y)
            changed = h != self.menu_hover
            self.menu_hover = h
            return changed
        h = self._target_at(x, y)
        changed = h != self.hover
        self.hover = h
        return changed

    def click(self, x, y, button):
        if self.menu:
            if button == 'LEFT':
                i = self._menu_idx(x, y)
                if i >= 0:
                    self._start(i)
            return
        if self.state != 'play':
            self.reset()
            return
        t = self._target_at(x, y)
        if t is None:
            return
        kind, i = t
        if kind == 'slot':
            self.cur = i
            if button == 'RIGHT':
                self.row[i] = None
        elif button == 'RIGHT':
            return
        elif kind == 'pal':
            self._put(i)
        elif kind == 'submit':
            self._submit()
        elif kind == 'clear':
            self._clear_row()

    def key(self, key, repeat):
        if self.menu:
            i = DIGIT_KEYS.get(key)
            if i is not None and i < len(LEVELS):
                self._start(i)
            return
        if key in DIGIT_KEYS:
            if DIGIT_KEYS[key] < self.k:
                self._put(DIGIT_KEYS[key])
        elif key in ('RET', 'NUMPAD_ENTER', 'SPACE'):
            if self.state != 'play':
                self.reset()
            else:
                self._submit()
        elif key in ('BACK_SPACE', 'DEL'):
            self._delete()
        elif key == 'LEFT_ARROW':
            self.cur = (self.cur - 1) % self.n
        elif key == 'RIGHT_ARROW':
            self.cur = (self.cur + 1) % self.n
        elif key == 'D':                   # back to the difficulty menu
            self.menu, self.menu_hover = True, -1
        elif key == 'X':
            self.repeats = not self.repeats
            self.reset()
        elif key == 'L':
            self.labels = not self.labels

    # ---- drawing helpers
    def _peg(self, c, cx, cy, r, ci, label=False):
        col = COLORS[ci]
        c.circle(cx, cy, r * 1.08, ov.shade(col, 0.55), 20)
        c.circle(cx, cy, r, col, 20)
        c.circle(cx - r * 0.32, cy + r * 0.32, r * 0.26, ov.shade(col, 1.35), 10)
        if label:
            c.text(str(ci + 1), cx, cy, r * 1.05, ov.C_DARK)

    def _feedback_pegs(self, c, ry, ex, mi):
        cs = self.cs
        x0 = self.left + self.n * cs + 0.15 * cs
        w = 1.75 * cs
        ncol = (self.n + 1) // 2
        sp = w / ncol
        r = min(0.14 * cs, sp * 0.36)
        for j in range(self.n):
            cx = x0 + (j // 2 + 0.5) * sp
            cy = ry + cs * (0.68 if j % 2 == 0 else 0.32)
            if j < ex:
                col = C_EXACT
            elif j < ex + mi:
                col = C_MISPLACED
            else:
                col = None
            if col is None:
                c.circle(cx, cy, r * 0.7, C_EMPTY, 10)
            else:
                c.circle(cx, cy, r, col, 12)

    # ---- drawing
    def draw(self, c):
        cs, u = self.cs, self.u
        n = self.n
        self.draw_frame(c)

        if self.state == 'win':
            main, col = "You cracked the code!", ov.C_GOOD
        elif self.state == 'lose':
            main, col = "Out of tries - here is the code", ov.C_BAD
        else:
            main, col = "Attempt %d of %d" % (len(self.guesses) + 1, self.tries), ov.C_WHITE
        lv = LEVELS[self.level][0]
        bt, bs = self._best(self.level)
        if self.state == 'win':
            sub = "%s  |  %d tries, %s  |  Score %d (best %d)" % (
                lv, len(self.guesses), self._fmt(self.elapsed), self.score, max(bs, self.score))
        else:
            cur_t = self.elapsed if self.state != 'play' else time.time() - self.t0
            sub = "%s  |  Time %s (best %s)  |  Best score %d  |  Won %d  Lost %d" % (
                lv, self._fmt(cur_t), self._fmt(bt), bs, self.wins, self.losses)
        if self.menu:
            main, col, sub = "MASTERMIND", ov.C_WHITE, "Choose a difficulty to start"
        self.draw_header(c, main, sub, col)

        # secret row
        sy = self.top - cs
        c.rect(self.left, sy + 2, self.bw, cs - 4, C_EMPTY)
        for i in range(n):
            cx, cy = self.left + (i + 0.5) * cs, sy + cs / 2
            if self.state == 'play':
                c.circle(cx, cy, 0.30 * cs, C_ROW, 18)
                c.text("?", cx, cy, cs * 0.42, ov.C_TEXT)
            else:
                self._peg(c, cx, cy, 0.32 * cs, self.secret[i], self.labels)
        fx = self.left + n * cs + 0.15 * cs + 0.875 * cs
        c.text(LEVELS[self.level][0], fx, sy + cs * 0.66, cs * 0.30, ov.C_WHITE)
        c.text("repeats ok" if self.repeats else "no repeats", fx, sy + cs * 0.32, cs * 0.24, ov.C_TEXT)

        # attempt rows
        cur_k = len(self.guesses)
        for k in range(self.tries):
            ry = self._row_y(k)
            current = (k == cur_k and self.state == 'play')
            c.rect(self.left, ry + 2, self.bw, cs - 4, C_ROW_CUR if current else C_ROW)
            for i in range(n):
                cx, cy = self.left + (i + 0.5) * cs, ry + cs / 2
                if k < cur_k:
                    self._peg(c, cx, cy, 0.30 * cs, self.guesses[k][0][i], self.labels)
                    if HINTS[self.level]:          # Easy: mark every single peg
                        mk = self.guesses[k][3][i]
                        if mk == 2:
                            c.ring(cx, cy, 0.40 * cs, 0.07 * cs, C_EXACT, 24)
                        elif mk == 1:
                            c.ring(cx, cy, 0.40 * cs, 0.07 * cs, C_MISPLACED, 24)
                        else:
                            c.circle(cx, cy, 0.30 * cs, (0.0, 0.0, 0.0, 0.55), 18)
                            d = 0.15 * cs
                            c.line((cx - d, cy - d), (cx + d, cy + d), 0.06 * cs, C_WRONG)
                            c.line((cx - d, cy + d), (cx + d, cy - d), 0.06 * cs, C_WRONG)
                elif current and self.row[i] is not None:
                    self._peg(c, cx, cy, 0.30 * cs, self.row[i], self.labels)
                else:
                    c.circle(cx, cy, 0.24 * cs, C_EMPTY, 16)
                if current and i == self.cur:
                    c.ring(cx, cy, 0.41 * cs, 0.05 * cs, ov.C_GOLD, 24)
            if k < cur_k:
                self._feedback_pegs(c, ry, self.guesses[k][1], self.guesses[k][2])
            else:
                self._feedback_pegs(c, ry, 0, 0)

        # palette
        pal_y = self._bottom() + 1.45 * cs
        c.rect(self.left, pal_y - 0.5 * cs, self.bw, cs, C_ROW)
        for i in range(self.k):
            px, py, r = self._pal_pos(i)
            if self.hover == ('pal', i) and self.state == 'play':
                c.ring(px, py, r * 1.28, 0.06 * cs, ov.C_WHITE, 24)
            self._peg(c, px, py, r, i)
            c.text(str(i + 1), px, py, r * 1.05, ov.C_DARK)

        # buttons
        full = None not in self.row and self.state == 'play'
        for name, (bx, by, bw, bh) in self._buttons().items():
            hov = self.hover == (name, 0) and self.state == 'play'
            if name == 'submit' and full:
                bg = ov.shade(ov.C_GOOD, 0.85 if not hov else 1.05)
                tc = ov.C_DARK
            else:
                bg = ov.C_CELL_HOVER if hov else ov.C_CELL
                tc = ov.C_TEXT if (name == 'clear' or not full) else ov.C_DARK
            c.rect(bx, by, bw, bh, bg)
            c.text("Submit" if name == 'submit' else "Clear row", bx + bw / 2, by + bh / 2, cs * 0.30, tc)

        if self.menu:
            c.rect(self.left, self._bottom(), self.bw, self.bh, (0.0, 0.0, 0.0, 0.80))
            c.text("Select difficulty", self.left + self.bw / 2, self.top - 1.0 * cs, cs * 0.5, ov.C_WHITE)
            for i, (name, n_, k_, t_) in enumerate(LEVELS):
                bx, by, bw, bh = self._menu_rect(i)
                c.rect(bx, by, bw, bh, ov.C_CELL_HOVER if i == self.menu_hover else ov.C_CELL)
                c.text("%d  %s" % (i + 1, name), bx + 0.3 * cs, by + bh * 0.72, cs * 0.36, ov.C_WHITE, 'left')
                tb, sb = self._best(i)
                c.text("Best %s / %d" % (self._fmt(tb), sb) if tb is not None else "No win yet",
                       bx + bw - 0.3 * cs, by + bh * 0.72, cs * 0.24,
                       ov.C_GOLD if tb is not None else ov.C_TEXT, 'right')
                c.text("%s, %d tries" % (LEVEL_INFO[i], t_), bx + 0.3 * cs, by + bh * 0.28,
                       cs * 0.20, ov.C_TEXT, 'left')
            self.draw_hint(c, "Click or press 1-3 to pick a difficulty   ESC quit")
        elif self.state == 'play':
            if HINTS[self.level]:
                self.draw_hint(c, "Green ring: right spot  White ring: wrong spot  X: not in code  D menu")
            else:
                self.draw_hint(c, "Click / 1-%d colour  ENTER submit  BKSP delete  D menu  X repeats  R new" % self.k)
        else:
            self.draw_hint(c, "Click or press R / SPACE for a new code   D menu")


RUNNER = ov.Runner("mastermind", GAME_NAME, Mastermind, [
    "Click a colour (or keys 1-8) to fill the row",
    "LMB on a slot: choose it   RMB: clear it",
    "ENTER / Submit: check your guess",
    "Green = right colour & spot, white = right colour only",
    "D: difficulty menu   X: repeats on/off   L: show numbers",
    "Easy marks each peg: green ring / white ring / X",
    "R: new code   ESC: quit",
])


def draw_ui(layout, context):
    RUNNER.draw_ui(layout)


def register():
    RUNNER.register()


def unregister():
    RUNNER.unregister()
