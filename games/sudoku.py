"""Sudoku - every puzzle is generated on the fly and has exactly one solution.

Click a cell, then type 1-9 (or use the number buttons). Conflicting digits
turn red. Pencil notes, undo and hints are built in.

Pick a difficulty first. The best solving time (without hints) is saved for
each difficulty in its own record file (sudoku_easy / medium / hard).
"""

import random
import traceback

from . import _overlay as ov
from . import _record as _rec

GAME_NAME = "Sudoku"
GAME_ICON = 'GRID'

LEVELS = [("Easy", 40), ("Medium", 33), ("Hard", 28)]    # (name, clues)
LEVEL_INFO = ["40 clues - relaxed", "33 clues - needs some logic", "28 clues - for experts"]

BOX = [(i // 27) * 3 + (i % 9) // 3 for i in range(81)]
POP = [bin(i).count('1') for i in range(1024)]
PEERS = []
for _i in range(81):
    _r, _c = divmod(_i, 9)
    PEERS.append({j for j in range(81) if j != _i and (j // 9 == _r or j % 9 == _c or BOX[j] == BOX[_i])})

DIGIT_KEYS = {n: d for d, n in enumerate(
    ['ONE', 'TWO', 'THREE', 'FOUR', 'FIVE', 'SIX', 'SEVEN', 'EIGHT', 'NINE'], 1)}
DIGIT_KEYS.update({'NUMPAD_%d' % d: d for d in range(1, 10)})
ERASE_KEYS = ('ZERO', 'NUMPAD_0', 'BACK_SPACE', 'DEL', 'X')
ARROWS = {'LEFT_ARROW': (-1, 0), 'RIGHT_ARROW': (1, 0), 'UP_ARROW': (0, -1), 'DOWN_ARROW': (0, 1)}

C_CELL = (0.20, 0.22, 0.27, 1.0)
C_PEER = (0.25, 0.28, 0.36, 1.0)
C_SAME = (0.30, 0.36, 0.52, 1.0)
C_SEL = (0.36, 0.48, 0.82, 1.0)
C_LINE = (0.55, 0.58, 0.66, 1.0)
C_GIVEN = (0.93, 0.94, 0.97, 1.0)
C_USER = (0.55, 0.78, 1.00, 1.0)
C_HINT = (0.50, 0.92, 0.60, 1.0)
C_BAD_T = (1.00, 0.42, 0.42, 1.0)
C_NOTE = (0.62, 0.66, 0.74, 1.0)
C_BTN = (0.26, 0.29, 0.36, 1.0)
C_BTN_HOVER = (0.34, 0.38, 0.48, 1.0)
C_BTN_ON = (0.85, 0.55, 0.20, 1.0)


# ----------------------------------------------------------------------------
# Solver / generator
# ----------------------------------------------------------------------------

def solve(grid, limit=1, randomize=False):
    """Backtracking solver (most-constrained-cell first).
    Returns (number_of_solutions_found_up_to_limit, first_solution_or_None)."""
    g = list(grid)
    rows, cols, boxes = [0] * 9, [0] * 9, [0] * 9
    for i, v in enumerate(g):
        if v:
            b = 1 << v
            rows[i // 9] |= b
            cols[i % 9] |= b
            boxes[BOX[i]] |= b
    res = {'n': 0, 'sol': None}

    def rec(empt):
        if not empt:
            res['n'] += 1
            if res['sol'] is None:
                res['sol'] = g[:]
            return res['n'] >= limit
        best, bm, bn = -1, 0, 10
        for i in empt:
            m = ~(rows[i // 9] | cols[i % 9] | boxes[BOX[i]]) & 0x3FE
            n = POP[m]
            if n < bn:
                best, bm, bn = i, m, n
                if n <= 1:
                    break
        if bn == 0:
            return False
        digits = [d for d in range(1, 10) if bm >> d & 1]
        if randomize:
            random.shuffle(digits)
        rest = [e for e in empt if e != best]
        r, c, b = best // 9, best % 9, BOX[best]
        for d in digits:
            bit = 1 << d
            g[best] = d
            rows[r] |= bit
            cols[c] |= bit
            boxes[b] |= bit
            stop = rec(rest)
            rows[r] ^= bit
            cols[c] ^= bit
            boxes[b] ^= bit
            g[best] = 0
            if stop:
                return True
        return False

    rec([i for i, v in enumerate(g) if not v])
    return res['n'], res['sol']


def make_puzzle(clues):
    """(puzzle, solution) - puzzle has exactly one solution."""
    sol = solve([0] * 81, 1, True)[1]
    puz = sol[:]
    order = list(range(81))
    random.shuffle(order)
    left = 81
    for i in order:
        if left <= clues:
            break
        keep, puz[i] = puz[i], 0
        if solve(puz, 2)[0] != 1:
            puz[i] = keep
        else:
            left -= 1
    return puz, sol


def fmt_time(t):
    t = int(t)
    return "%02d:%02d" % (t // 60, t % 60)


# ----------------------------------------------------------------------------

class Sudoku(ov.BaseGame):
    keys = tuple(DIGIT_KEYS) + ERASE_KEYS + tuple(ARROWS) + ('N', 'H', 'Z', 'D', 'P')

    def setup(self):
        self.level = 0
        self.menu = True                  # difficulty menu is shown first
        self.menu_hover = -1
        self.record_mgrs = {}             # level -> RecordManager (sudoku_<name>.json)
        self.best_cache = {}              # level -> best time in seconds (or None)

    # ---- records (direct _record link, one file per difficulty)
    def _mgr(self, lv):
        if lv not in self.record_mgrs:
            try:
                self.record_mgrs[lv] = _rec.RecordManager(game_name="sudoku_" + LEVELS[lv][0].lower())
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

    def _save_win(self):
        """Record the win; returns True if it is a new best time (hints = no time)."""
        t = self.time if self.hints == 0 else None
        prev = self._best_time(self.level)
        m = self._mgr(self.level)
        if m:
            try:
                try:
                    m.add_win_record(score=0, best_time=t)
                except TypeError:         # _record.py without best_time support
                    m.add_win_record(score=0)
                self.best_cache.pop(self.level, None)
            except Exception:
                traceback.print_exc()
        return t is not None and (prev is None or t < prev)

    # ---- difficulty menu
    def _menu_rect(self, i):
        cs = self.cs
        return (self.left + 1.0 * cs, self.top - (2.0 + 2.3 * i + 1.8) * cs, 7.0 * cs, 1.8 * cs)

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

    def reset(self):
        self.puz, self.sol = make_puzzle(LEVELS[self.level][1])
        self.given = [v != 0 for v in self.puz]
        self.val = self.puz[:]
        self.notes = [0] * 81
        self.hinted = set()
        self.undo = []
        self.sel = -1
        self.notes_mode = False
        self.mistakes = 0
        self.hints = 0
        self.time = 0.0
        self.state = 'playing'            # playing | paused | won
        self.new_record = False
        self.hover = None
        self.cs = 40

    def _lv(self):
        return LEVELS[self.level][0].lower()

    # ---- editing
    def _check_win(self):
        if all(self.val[i] == self.sol[i] for i in range(81)):
            self.state = 'won'
            # best time per level; a puzzle solved with hints doesn't count for the time record
            self.new_record = self._save_win()

    def _clear_peer_notes(self, i, d, changes):
        for p in PEERS[i]:
            if self.notes[p] >> d & 1:
                changes.append((p, self.val[p], self.notes[p]))
                self.notes[p] &= ~(1 << d)

    def _digit(self, d):
        i = self.sel
        if self.state != 'playing' or i < 0 or self.given[i]:
            return
        if self.notes_mode:
            if self.val[i] == 0:
                self.undo.append([(i, 0, self.notes[i])])
                self.notes[i] ^= 1 << d
            return
        changes = [(i, self.val[i], self.notes[i])]
        new = 0 if self.val[i] == d else d
        self.val[i], self.notes[i] = new, 0
        if new:
            self._clear_peer_notes(i, new, changes)
            if new != self.sol[i]:
                self.mistakes += 1
        self.undo.append(changes)
        self._check_win()

    def _erase(self):
        i = self.sel
        if self.state != 'playing' or i < 0 or self.given[i] or not (self.val[i] or self.notes[i]):
            return
        self.undo.append([(i, self.val[i], self.notes[i])])
        self.val[i], self.notes[i] = 0, 0

    def _undo(self):
        if self.state != 'playing' or not self.undo:
            return
        for i, v, n in reversed(self.undo.pop()):
            self.val[i], self.notes[i] = v, n

    def _hint(self):
        if self.state != 'playing':
            return
        i = self.sel
        if not (i >= 0 and not self.given[i] and self.val[i] != self.sol[i]):
            wrong = [k for k in range(81) if not self.given[k] and self.val[k] != self.sol[k]]
            if not wrong:
                return
            i = random.choice(wrong)
        changes = [(i, self.val[i], self.notes[i])]
        self.val[i], self.notes[i] = self.sol[i], 0
        self._clear_peer_notes(i, self.sol[i], changes)
        self.undo.append(changes)
        self.hinted.add(i)
        self.hints += 1
        self.sel = i
        self._check_win()

    def _action(self, k):
        if k == 0:
            self.notes_mode = not self.notes_mode
        elif k == 1:
            self._erase()
        elif k == 2:
            self._undo()
        elif k == 3:
            self._hint()

    # ---- input
    def key(self, key, repeat):
        if self.menu:
            d = DIGIT_KEYS.get(key)
            if d is not None and d <= len(LEVELS):
                self._start(d - 1)
            return
        if key == 'P':
            if self.state == 'playing':
                self.state = 'paused'
            elif self.state == 'paused':
                self.state = 'playing'
            return
        if key == 'D':                    # back to the difficulty menu
            self.menu, self.menu_hover = True, -1
            return
        if self.state == 'won':
            return
        if self.state == 'paused':
            self.state = 'playing'
            return
        if key in DIGIT_KEYS:
            self._digit(DIGIT_KEYS[key])
        elif key in ERASE_KEYS:
            self._erase()
        elif key in ARROWS:
            dx, dy = ARROWS[key]
            if self.sel < 0:
                self.sel = 40
            else:
                r, c = divmod(self.sel, 9)
                self.sel = max(0, min(8, r + dy)) * 9 + max(0, min(8, c + dx))
        elif key == 'N':
            self.notes_mode = not self.notes_mode
        elif key == 'H' and not repeat:
            self._hint()
        elif key == 'Z':
            self._undo()

    def update(self, dt):
        if self.menu or self.state != 'playing':
            return False
        before = int(self.time)
        self.time += dt
        return int(self.time) != before

    # ---- geometry
    def layout(self, view):
        self.cs = self.fit_cell(view, 9, 11.0, max_cell=56)
        cs = self.cs
        self.place(view, 9 * cs, 11.0 * cs)
        self.num_top = self.top - 9.3 * cs
        self.num_bot = self.num_top - 0.9 * cs
        self.act_top = self.num_bot - 0.2 * cs
        self.act_bot = self.act_top - 0.6 * cs

    def _act_rect(self, k):
        gap = 0.15 * self.cs
        w = (9 * self.cs - 3 * gap) / 4
        return self.left + k * (w + gap), self.act_bot, w, self.act_top - self.act_bot

    def _hit_test(self, x, y):
        cs = self.cs
        cr = ov.cell_at(x, y, self.left, self.top, cs, 9, 9)
        if cr:
            return ('cell', cr[1] * 9 + cr[0])
        if self.num_bot <= y <= self.num_top and self.left <= x < self.left + 9 * cs:
            return ('num', int((x - self.left) // cs) + 1)
        for k in range(4):
            ax, ay, aw, ah = self._act_rect(k)
            if ax <= x <= ax + aw and ay <= y <= ay + ah:
                return ('act', k)
        return None

    def move(self, x, y):
        if self.menu:
            h = self._menu_idx(x, y) if x > -1e5 else -1
            changed = h != self.menu_hover
            self.menu_hover = h
            return changed
        h = self._hit_test(x, y) if x > -1e5 else None
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
        if self.state == 'won':
            self.reset()
            return
        if self.state == 'paused':
            self.state = 'playing'
            return
        h = self._hit_test(x, y)
        if not h:
            return
        if h[0] == 'cell':
            self.sel = h[1]
        elif h[0] == 'num':
            self._digit(h[1])
        else:
            self._action(h[1])

    # ---- drawing
    def _conflicts(self):
        bad = set()
        for i in range(81):
            v = self.val[i]
            if v and any(self.val[p] == v for p in PEERS[i]):
                bad.add(i)
        return bad

    def draw(self, c):
        u, cs = self.u, self.cs
        self.draw_frame(c)

        name = LEVELS[self.level][0]
        main = "SOLVED!" if self.state == 'won' else "SUDOKU"
        best = self._best_time(self.level)
        sub = "%s   |   %s   |   Mistakes: %d   |   Hints: %d" % (
            name, fmt_time(self.time), self.mistakes, self.hints)
        if best is not None:
            sub += "   |   Best: %s" % fmt_time(best)
        self.draw_header(c, main, sub, ov.C_GOOD if self.state == 'won' else ov.C_WHITE)

        sel = self.sel
        sel_val = self.val[sel] if sel >= 0 else 0
        bad = self._conflicts()
        board_bot = self.top - 9 * cs
        c.rect(self.left, board_bot, 9 * cs, 9 * cs, C_LINE)

        thin, thick = 0.75 * u, 2.5 * u
        hid = self.state == 'paused'
        for i in range(81):
            r, k = divmod(i, 9)
            x0 = self.left + k * cs + (thick if k % 3 == 0 else thin)
            x1 = self.left + (k + 1) * cs - (thick if k % 3 == 2 else thin)
            y1 = self.top - r * cs - (thick if r % 3 == 0 else thin)
            y0 = self.top - (r + 1) * cs + (thick if r % 3 == 2 else thin)
            col = C_CELL
            if sel >= 0:
                if i == sel:
                    col = C_SEL
                elif sel_val and self.val[i] == sel_val:
                    col = C_SAME
                elif i in PEERS[sel]:
                    col = C_PEER
            c.rect(x0, y0, x1 - x0, y1 - y0, col)
            if hid:
                continue
            cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
            v = self.val[i]
            if v:
                tc = (C_BAD_T if i in bad else C_GIVEN if self.given[i]
                      else C_HINT if i in self.hinted else C_USER)
                c.text(str(v), cx, cy, cs * 0.58, tc)
            elif self.notes[i]:
                for d in range(1, 10):
                    if self.notes[i] >> d & 1:
                        nx = self.left + k * cs + ((d - 1) % 3 + 0.5) * cs / 3
                        ny = self.top - r * cs - ((d - 1) // 3 + 0.5) * cs / 3
                        c.text(str(d), nx, ny, cs * 0.21,
                               ov.C_WHITE if (sel_val == d) else C_NOTE)

        # number buttons
        counts = [0] * 10
        for i in range(81):
            if self.val[i] and self.val[i] == self.sol[i]:
                counts[self.val[i]] += 1
        for d in range(1, 10):
            x0 = self.left + (d - 1) * cs + 2 * u
            done = counts[d] >= 9
            hov = self.hover == ('num', d)
            col = C_BTN_HOVER if hov and not done else C_BTN
            c.rect(x0, self.num_bot, cs - 4 * u, self.num_top - self.num_bot, col)
            c.text(str(d), x0 + (cs - 4 * u) / 2, (self.num_bot + self.num_top) / 2 + cs * 0.05,
                   cs * 0.5, (0.4, 0.42, 0.48, 1) if done else
                   (C_BTN_ON if self.notes_mode else ov.C_WHITE))

        # action buttons
        labels = ["Notes: %s" % ("ON" if self.notes_mode else "off"), "Erase", "Undo", "Hint"]
        for k, label in enumerate(labels):
            ax, ay, aw, ah = self._act_rect(k)
            col = C_BTN_ON if (k == 0 and self.notes_mode) else (
                C_BTN_HOVER if self.hover == ('act', k) else C_BTN)
            c.rect(ax, ay, aw, ah, col)
            c.text(label, ax + aw / 2, ay + ah / 2, 13 * u, ov.C_WHITE)

        bw = 9 * cs
        if self.state == 'paused':
            c.rect(self.left, board_bot, bw, bw, (0.07, 0.08, 0.10, 1.0))
            c.text("Paused", self.left + bw / 2, board_bot + bw / 2 + 12 * u, 26 * u, ov.C_WHITE)
            c.text("Press P or click to resume", self.left + bw / 2, board_bot + bw / 2 - 16 * u,
                   13 * u, ov.C_TEXT)
        elif self.state == 'won':
            if self.new_record:
                sub = "NEW RECORD!   Click or press R for a new puzzle"
            elif self.hints:
                sub = "Hints used - no time record   (click or R: new puzzle)"
            else:
                sub = "Click or press R for a new puzzle   (D: change level)"
            self.banner(c, self.left, board_bot, bw, bw, "Solved in %s!" % fmt_time(self.time), sub)

        if self.menu:
            c.rect(self.left, self.top - 11.0 * cs, 9 * cs, 11.0 * cs, (0.0, 0.0, 0.0, 0.88))
            c.text("Select difficulty", self.left + 4.5 * cs, self.top - 1.0 * cs, cs * 0.6, ov.C_WHITE)
            for i, (name, clues) in enumerate(LEVELS):
                bx, by, bw, bh = self._menu_rect(i)
                c.rect(bx, by, bw, bh, C_BTN_HOVER if i == self.menu_hover else C_BTN)
                c.text("%d  %s" % (i + 1, name), bx + 0.4 * cs, by + bh * 0.68, cs * 0.55, ov.C_WHITE, 'left')
                c.text(LEVEL_INFO[i], bx + 0.4 * cs, by + bh * 0.27, cs * 0.30, C_NOTE, 'left')
                bt = self._best_time(i)
                c.text("Best %s" % fmt_time(bt) if bt is not None else "No record",
                       bx + bw - 0.4 * cs, by + bh * 0.68, cs * 0.34,
                       ov.C_GOLD if bt is not None else C_NOTE, 'right')
            c.text("Best time counts only without hints", self.left + 4.5 * cs, self.top - 9.5 * cs,
                   cs * 0.30, C_NOTE)
            self.draw_hint(c, "Click or press 1-3 to pick a difficulty   ESC quit")
            return
        self.draw_hint(c, "Cell + 1-9   N notes   H hint   Z undo   D menu   P pause")


RUNNER = ov.Runner("sudoku", GAME_NAME, Sudoku, [
    "Click a cell, then 1-9 (or buttons)",
    "Arrows: move   0 / Backspace: erase",
    "N: pencil notes   Z: undo",
    "H: hint (fills a cell)",
    "D: difficulty menu (Easy / Medium / Hard)",
    "P: pause   R: new puzzle   ESC: quit",
])


def draw_ui(layout, context):
    RUNNER.draw_ui(layout)


def register():
    RUNNER.register()


def unregister():
    RUNNER.unregister()
