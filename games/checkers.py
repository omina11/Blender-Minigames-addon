"""Checkers (Dama) - you (white) vs the CPU (black, alpha-beta), or two players.

Rules: 8x8, men move/capture forward only, kings move/capture both ways,
capturing is mandatory (multi-jumps included), reaching the last row crowns
a man and ends the turn. No legal move = you lose.

Pick a difficulty first; the CPU adapts to it (see LEVELS) and the best winning
time is saved for each difficulty (checkers_easy / medium / hard / expert).
"""

import random
import threading
import time
import traceback

from . import _overlay as ov
from . import _record as _rec

GAME_NAME = "Checkers"
GAME_ICON = 'MESH_UVSPHERE'

C_LIGHT = (0.64, 0.59, 0.52, 1.0)
C_DARK_SQ = (0.26, 0.20, 0.17, 1.0)
C_SEL = (1.0, 0.85, 0.2, 0.55)
C_LAST = (0.4, 0.8, 0.5, 0.25)
C_WHITE_P = (0.93, 0.92, 0.88, 1.0)
C_BLACK_P = (0.11, 0.11, 0.14, 1.0)


# ----------------------------------------------------------------------------
# Rules / AI.  Board: list of 64 chars, index = row * 8 + col, row 0 = top.
#   'w' / 'b' = men, 'W' / 'B' = kings, '.' = empty.   White moves UP (row-).
# ----------------------------------------------------------------------------

def initial():
    b = ['.'] * 64
    for r in range(8):
        for c in range(8):
            if (r + c) % 2 == 1:
                if r < 3:
                    b[r * 8 + c] = 'b'
                elif r > 4:
                    b[r * 8 + c] = 'w'
    return b


def _dirs(p):
    if p in 'WB':
        return ((-1, -1), (-1, 1), (1, -1), (1, 1))
    return ((-1, -1), (-1, 1)) if p == 'w' else ((1, -1), (1, 1))


def _jumps(b, piece, side, i, caps, path, out):
    r, c = divmod(i, 8)
    found = False
    for dr, dc in _dirs(piece):
        r2, c2 = r + 2 * dr, c + 2 * dc
        if 0 <= r2 < 8 and 0 <= c2 < 8:
            m, d = (r + dr) * 8 + (c + dc), r2 * 8 + c2
            if b[m] != '.' and b[m].lower() != side and m not in caps and b[d] == '.':
                found = True
                crown = piece.islower() and ((side == 'w' and r2 == 0) or (side == 'b' and r2 == 7))
                if crown:
                    out.append((tuple(path + [d]), tuple(caps + [m])))
                else:
                    _jumps(b, piece, side, d, caps + [m], path + [d], out)
    if not found and caps:
        out.append((tuple(path), tuple(caps)))


def moves(b, side):
    """All legal moves for `side`: list of (path, captured). Captures are forced."""
    jumps, steps = [], []
    for i, p in enumerate(b):
        if p == '.' or p.lower() != side:
            continue
        b[i] = '.'                       # vacate start square while searching
        _jumps(b, p, side, i, [], [i], jumps)
        b[i] = p
        r, c = divmod(i, 8)
        for dr, dc in _dirs(p):
            r2, c2 = r + dr, c + dc
            if 0 <= r2 < 8 and 0 <= c2 < 8 and b[r2 * 8 + c2] == '.':
                steps.append(((i, r2 * 8 + c2), ()))
    return jumps if jumps else steps


def apply(b, mv):
    nb = b[:]
    path, caps = mv
    p = nb[path[0]]
    nb[path[0]] = '.'
    for m in caps:
        nb[m] = '.'
    end = path[-1]
    if p == 'w' and end // 8 == 0:
        p = 'W'
    elif p == 'b' and end // 8 == 7:
        p = 'B'
    nb[end] = p
    return nb


def evaluate(b, side):
    s = 0
    for i, p in enumerate(b):
        if p == '.':
            continue
        r, c = divmod(i, 8)
        if p.isupper():
            v = 160
        else:
            v = 100 + ((7 - r) if p == 'w' else r) * 3
        if 2 <= c <= 5 and 2 <= r <= 5:
            v += 2
        s += v if p.lower() == side else -v
    return s


def negamax(b, side, depth, alpha, beta):
    mv = moves(b, side)
    if not mv:
        return -10000 - depth
    if depth == 0:
        return evaluate(b, side)
    opp = 'b' if side == 'w' else 'w'
    best = -10 ** 9
    for m in mv:
        v = -negamax(apply(b, m), opp, depth - 1, -beta, -alpha)
        if v > best:
            best = v
            if best > alpha:
                alpha = best
        if alpha >= beta:
            break
    return best


# name, search depth, random-move chance, time limit in s (None = fixed depth), description
LEVELS = [
    ("Easy", 1, 0.45, None, "Sees 1 move ahead, often plays randomly"),
    ("Medium", 3, 0.10, None, "Sees 3 moves ahead, rare mistakes"),
    ("Hard", 8, 0.0, 1.2, "Deep search, about 1 s per move"),
    ("Expert", 14, 0.0, 3.5, "Very deep search, about 3.5 s per move"),
]


class _Stop(Exception):
    pass


def _negamax_t(b, side, depth, alpha, beta, ctl):
    ctl['n'] += 1
    if not (ctl['n'] & 255):
        if ctl['cancel'] or (ctl['deadline'] and time.time() > ctl['deadline']):
            raise _Stop()
    mv = moves(b, side)
    if not mv:
        return -10000 - depth
    if depth == 0:
        return evaluate(b, side)
    opp = 'b' if side == 'w' else 'w'
    best = -10 ** 9
    for m in mv:
        v = -_negamax_t(apply(b, m), opp, depth - 1, -beta, -alpha, ctl)
        if v > best:
            best = v
            if best > alpha:
                alpha = best
        if alpha >= beta:
            break
    return best


def search_move(b, side, max_depth, tlimit, ctl):
    """Iterative deepening (time limited). Returns the best move found, None if cancelled."""
    ctl['deadline'] = time.time() + tlimit if tlimit else 0
    opp = 'b' if side == 'w' else 'w'
    root = moves(b, side)
    if len(root) == 1:
        return root[0]
    best = random.choice(root)
    order = root
    for d in range(1, max_depth + 1):
        try:
            scored = []
            alpha = -10 ** 9
            for m in order:
                v = -_negamax_t(apply(b, m), opp, d - 1, -10 ** 9, -alpha, ctl)
                scored.append((v, m))
                if v > alpha:
                    alpha = v
        except _Stop:
            if ctl['cancel']:
                return None
            break
        top = max(s for s, _ in scored)
        best = random.choice([m for s, m in scored if s == top])
        scored.sort(key=lambda t: -t[0])
        order = [m for _, m in scored]
        if top > 9000 or top < -9000:
            break
    return best


def best_move(b, side, depth):
    opp = 'b' if side == 'w' else 'w'
    scored = [(-negamax(apply(b, m), opp, depth - 1, -10 ** 9, 10 ** 9), m)
              for m in moves(b, side)]
    top = max(s for s, _ in scored)
    return random.choice([m for s, m in scored if s == top])


# ----------------------------------------------------------------------------

class Checkers(ov.BaseGame):
    keys = ('M', 'D', 'ONE', 'TWO', 'THREE', 'FOUR')

    def setup(self):
        self.mode = 'CPU'
        self.level = 1                    # index in LEVELS
        self.score = {'w': 0, 'b': 0}
        self.menu = True                  # difficulty menu is shown first
        self.menu_hover = -1
        self.token = 0
        self.ctl = None
        self.result = None
        self.thinking = False
        self.record_mgrs = {}             # level -> RecordManager (checkers_<name>.json)
        self.best_cache = {}              # level -> best winning time (s) or None

    # ---- records
    def _mgr(self, lv):
        if lv not in self.record_mgrs:
            try:
                self.record_mgrs[lv] = _rec.RecordManager(game_name="checkers_" + LEVELS[lv][0].lower())
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

    def _record_end(self):
        if self.mode != 'CPU' or self.recorded or not self.over:
            return
        self.recorded = True
        self.t_end = time.time()
        m = self._mgr(self.level)
        if not m:
            return
        try:
            if self.over == 'w':
                t = self.t_end - self.t0
                prev = self._best_time(self.level)
                nw = sum(1 for p in self.b if p.lower() == 'w')
                try:
                    m.add_win_record(score=nw, best_time=t)
                except TypeError:         # _record.py without best_time support
                    m.add_win_record(score=nw)
                self.new_best = prev is None or t < prev
                self.best_cache.pop(self.level, None)
            elif self.over == 'b':
                m.add_lose_record()
            else:
                m.add_draw_record()
        except Exception:
            traceback.print_exc()

    # ---- difficulty menu
    def _menu_rect(self, i):
        cs = self.cs
        return (self.left + 1.0 * cs, self.top - (1.4 + 1.2 * i + 1.0) * cs, 6.0 * cs, 1.0 * cs)

    def _menu_idx(self, x, y):
        for i in range(len(LEVELS) + 1):
            bx, by, bw, bh = self._menu_rect(i)
            if bx <= x <= bx + bw and by <= y <= by + bh:
                return i
        return -1

    def _start(self, i):
        self._cancel()
        if i >= len(LEVELS):
            self.mode = 'PVP'
        else:
            self.mode, self.level = 'CPU', i
        self.score = {'w': 0, 'b': 0}
        self.menu = False
        self.reset()

    def _cancel(self):
        self.token += 1
        if self.ctl is not None:
            self.ctl['cancel'] = True
        self.thinking = False
        self.result = None

    def reset(self):
        self.b = initial()
        self.turn = 'w'
        self.sel = -1
        self.legal = moves(self.b, 'w')
        self.over = None          # None | 'w' | 'b' | 'draw'
        self.quiet = 0
        self.last = ()
        self.cpu_t = 0.0
        self.hover = -1
        self.cs = 50
        self._cancel()
        self.t0 = time.time()
        self.t_end = 0.0
        self.recorded = False
        self.new_best = False
        self.shown = 0

    # ---- logic
    def human_turn(self):
        return not self.over and (self.mode == 'PVP' or self.turn == 'w')

    def do_move(self, mv):
        piece = self.b[mv[0][0]]
        self.b = apply(self.b, mv)
        self.quiet = 0 if (mv[1] or piece.islower()) else self.quiet + 1
        self.last = (mv[0][0], mv[0][-1])
        mover = self.turn
        self.turn = 'b' if mover == 'w' else 'w'
        self.sel = -1
        self.legal = moves(self.b, self.turn)
        if not self.legal:
            self.over = mover
            self.score[mover] += 1
        elif self.quiet >= 80:
            self.over = 'draw'
        self._record_end()

    def _start_think(self):
        self.token += 1
        tok = self.token
        _, depth, p_rand, tlimit, _ = LEVELS[self.level]
        ctl = {'n': 0, 'cancel': False, 'deadline': 0}
        self.ctl, self.thinking, self.result, self.cpu_t = ctl, True, None, 0.0
        b, legal = self.b[:], list(self.legal)

        def run():
            mv = None
            try:
                if p_rand and random.random() < p_rand:
                    mv = random.choice(legal)
                else:
                    mv = search_move(b, 'b', depth, tlimit, ctl)
            except Exception:
                traceback.print_exc()
            self.result = (tok, mv)

        threading.Thread(target=run, daemon=True).start()

    def update(self, dt):
        if self.menu:
            return False
        changed = False
        if not self.over:                         # redraw when the timer second changes
            s = int(time.time() - self.t0)
            if s != self.shown:
                self.shown = s
                changed = True
        if self.mode == 'CPU' and self.turn == 'b' and not self.over:
            if not self.thinking:
                self._start_think()
                return True
            self.cpu_t += dt
            r = self.result
            if r is not None and r[0] == self.token and self.cpu_t >= 0.5:
                self.thinking, self.result = False, None
                if r[1] is not None:
                    self.do_move(r[1])
                return True
        return changed

    def key(self, key, repeat):
        if self.menu:
            i = {'ONE': 0, 'TWO': 1, 'THREE': 2, 'FOUR': 3, 'M': 4}.get(key)
            if i is not None:
                self._start(i)
            return
        if key == 'M':
            self.mode = 'PVP' if self.mode == 'CPU' else 'CPU'
            self.score = {'w': 0, 'b': 0}
            self.reset()
        elif key == 'D':                      # back to the difficulty menu
            self._cancel()
            self.menu, self.menu_hover = True, -1

    # ---- input
    def layout(self, view):
        self.cs = self.fit_cell(view, 8, 8, max_cell=64)
        self.place(view, 8 * self.cs, 8 * self.cs)

    def _idx(self, x, y):
        cr = ov.cell_at(x, y, self.left, self.top, self.cs, 8, 8)
        return -1 if cr is None else cr[1] * 8 + cr[0]

    def move(self, x, y):
        if self.menu:
            h = self._menu_idx(x, y)
            changed = h != self.menu_hover
            self.menu_hover = h
            return changed
        h = self._idx(x, y)
        changed = h != self.hover
        self.hover = h
        return changed

    def click(self, x, y, button):
        if button != 'LEFT':
            return
        if self.menu:
            i = self._menu_idx(x, y)
            if i >= 0:
                self._start(i)
            return
        if self.over:
            self.reset()
            return
        i = self._idx(x, y)
        if i < 0 or not self.human_turn():
            return
        if self.sel >= 0:
            for mv in self.legal:
                if mv[0][0] == self.sel and mv[0][-1] == i:
                    self.do_move(mv)
                    return
        if any(mv[0][0] == i for mv in self.legal):
            self.sel = i
        else:
            self.sel = -1

    # ---- drawing
    def draw(self, c):
        u, cs = self.u, self.cs
        self.draw_frame(c)

        nw = sum(1 for p in self.b if p.lower() == 'w')
        nb = sum(1 for p in self.b if p.lower() == 'b')
        cpu = self.mode == 'CPU'
        if self.over == 'draw':
            main, col = "Draw!", ov.C_WHITE
        elif self.over:
            col = C_WHITE_P if self.over == 'w' else (0.6, 0.6, 0.7, 1.0)
            if cpu:
                main = "You win!" if self.over == 'w' else "CPU wins"
            else:
                main = "White wins!" if self.over == 'w' else "Black wins!"
        else:
            col = ov.C_WHITE
            if cpu:
                main = "Your turn (white)" if self.turn == 'w' else "CPU is thinking..."
            else:
                main = "White's turn" if self.turn == 'w' else "Black's turn"
            if self.legal and self.legal[0][1]:
                main += "  -  capture!"
        mode = ("vs CPU (%s)" % LEVELS[self.level][0]) if cpu else "2 players"
        if cpu:
            t_now = (self.t_end if self.over else time.time()) - self.t0
            sub = "%s  |  %d v %d  |  Time %s (best %s)  |  Wins %d - %d" % (
                mode, nw, nb, self._fmt(t_now), self._fmt(self._best_time(self.level)),
                self.score['w'], self.score['b'])
            if self.over == 'w' and self.new_best:
                sub += "  NEW BEST!"
        else:
            sub = "%s   |   White: %d   Black: %d   |   Wins %d - %d" % (
                mode, nw, nb, self.score['w'], self.score['b'])
        if self.menu:
            main, col, sub = "CHECKERS", ov.C_WHITE, "Choose a difficulty to start"
        self.draw_header(c, main, sub, col)

        targets = {}
        if self.sel >= 0:
            for mv in self.legal:
                if mv[0][0] == self.sel:
                    targets[mv[0][-1]] = mv

        for r in range(8):
            for k in range(8):
                i = r * 8 + k
                x, y = self.left + k * cs, self.top - (r + 1) * cs
                dark = (r + k) % 2 == 1
                c.rect(x, y, cs, cs, C_DARK_SQ if dark else C_LIGHT)
                if i in self.last:
                    c.rect(x, y, cs, cs, C_LAST)
                if i == self.sel:
                    c.rect(x, y, cs, cs, C_SEL)
                cx, cy = x + cs / 2, y + cs / 2
                p = self.b[i]
                if p != '.':
                    white = p.lower() == 'w'
                    base = C_WHITE_P if white else C_BLACK_P
                    edge = (0.62, 0.62, 0.58, 1.0) if white else (0.32, 0.32, 0.38, 1.0)
                    c.circle(cx, cy + cs * 0.03, cs * 0.38, (0, 0, 0, 0.35), 28)
                    c.circle(cx, cy, cs * 0.38, edge, 28)
                    c.circle(cx, cy, cs * 0.32, base, 28)
                    c.ring(cx, cy, cs * 0.20, max(1.5, cs * 0.04), edge, 24)
                    if p.isupper():
                        c.ring(cx, cy, cs * 0.12, max(2, cs * 0.06), ov.C_GOLD, 20)
                        c.circle(cx, cy, cs * 0.06, ov.C_GOLD, 12)
                if i in targets:
                    c.circle(cx, cy, cs * 0.14, ov.alpha(ov.C_GOOD, 0.9), 16)
                elif i == self.hover and self.human_turn() and any(m[0][0] == i for m in self.legal):
                    c.ring(cx, cy, cs * 0.42, max(2, cs * 0.05), ov.alpha(ov.C_GOLD, 0.8), 28)

        if self.menu:
            c.rect(self.left, self.top - 8 * cs, 8 * cs, 8 * cs, (0.0, 0.0, 0.0, 0.80))
            c.text("Select difficulty", self.left + 4 * cs, self.top - 0.7 * cs, cs * 0.5, ov.C_WHITE)
            for i in range(len(LEVELS) + 1):
                bx, by, bw, bh = self._menu_rect(i)
                c.rect(bx, by, bw, bh, ov.C_CELL_HOVER if i == self.menu_hover else ov.C_CELL)
                if i < len(LEVELS):
                    name, _, _, _, info = LEVELS[i]
                    c.text("%d  %s" % (i + 1, name), bx + 0.25 * cs, by + bh * 0.68, cs * 0.30, ov.C_WHITE, 'left')
                    c.text(info, bx + 0.25 * cs, by + bh * 0.27, cs * 0.16, ov.C_TEXT, 'left')
                    bt = self._best_time(i)
                    c.text("Best %s" % self._fmt(bt) if bt is not None else "No win yet",
                           bx + bw - 0.25 * cs, by + bh * 0.68, cs * 0.20,
                           ov.C_GOLD if bt is not None else ov.C_TEXT, 'right')
                else:
                    c.text("M  2 players", bx + 0.25 * cs, by + bh * 0.5, cs * 0.30, ov.C_WHITE, 'left')
            self.draw_hint(c, "Click or press 1-4 to pick a difficulty   M two players   ESC quit")
        elif self.over:
            self.draw_hint(c, "Click the board or press R for a new game   D menu")
        else:
            self.draw_hint(c, "Click piece, then square   M mode   D menu   ESC quit")


RUNNER = ov.Runner("checkers", GAME_NAME, Checkers, [
    "LMB: select a piece, then a square",
    "Captures are mandatory",
    "M: vs CPU / 2 players",
    "D: difficulty menu (easy / medium / hard / expert)",
    "R: restart   ESC: quit",
])


def draw_ui(layout, context):
    RUNNER.draw_ui(layout)


def register():
    RUNNER.register()


def unregister():
    RUNNER.unregister()
