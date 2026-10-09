"""Connect Four (Forza 4) - you (red) vs the CPU (yellow, alpha-beta), or two players."""

import random

from . import _overlay as ov
from . import _record as _rec

GAME_NAME = "Connect Four"
GAME_ICON = 'MESH_CIRCLE'

COLS, ROWS = 7, 6
C_RED = (0.92, 0.22, 0.22, 1.0)
C_YEL = (1.00, 0.85, 0.20, 1.0)
C_BOARD = (0.14, 0.30, 0.72, 1.0)
C_HOLE = (0.09, 0.10, 0.14, 1.0)
PCOL = {1: C_RED, 2: C_YEL}
ORDER = (3, 2, 4, 1, 5, 0, 6)

# Every group of four in a row (index = row * COLS + col, row 0 = bottom)
WINDOWS = []
for _r in range(ROWS):
    for _c in range(COLS):
        for _dr, _dc in ((0, 1), (1, 0), (1, 1), (1, -1)):
            cells = [(_r + _dr * k, _c + _dc * k) for k in range(4)]
            if all(0 <= r < ROWS and 0 <= c < COLS for r, c in cells):
                WINDOWS.append(tuple(r * COLS + c for r, c in cells))


def drop_row(b, c):
    for r in range(ROWS):
        if b[r * COLS + c] == 0:
            return r
    return -1


def find_win(b):
    for w in WINDOWS:
        v = b[w[0]]
        if v and v == b[w[1]] == b[w[2]] == b[w[3]]:
            return w
    return None


def score_pos(b, me):
    opp = 3 - me
    s = 3 * sum(1 for r in range(ROWS) if b[r * COLS + 3] == me)
    for w in WINDOWS:
        vals = [b[i] for i in w]
        m, o, e = vals.count(me), vals.count(opp), vals.count(0)
        if m == 3 and e == 1:
            s += 6
        elif m == 2 and e == 2:
            s += 2
        if o == 3 and e == 1:
            s -= 5
    return s


def minimax(b, depth, alpha, beta, maxing, me):
    if find_win(b):
        # the previous mover won
        return -100000 - depth if maxing else 100000 + depth
    moves = [c for c in ORDER if b[(ROWS - 1) * COLS + c] == 0]
    if not moves:
        return 0
    if depth == 0:
        return score_pos(b, me)
    piece = me if maxing else 3 - me
    best = -10 ** 9 if maxing else 10 ** 9
    for c in moves:
        i = drop_row(b, c) * COLS + c
        b[i] = piece
        v = minimax(b, depth - 1, alpha, beta, not maxing, me)
        b[i] = 0
        if maxing:
            best = max(best, v)
            alpha = max(alpha, best)
        else:
            best = min(best, v)
            beta = min(beta, best)
        if alpha >= beta:
            break
    return best


def pick_move(b, me, depth):
    moves = [c for c in ORDER if b[(ROWS - 1) * COLS + c] == 0]
    scored = []
    for c in moves:
        i = drop_row(b, c) * COLS + c
        b[i] = me
        if find_win(b):
            v = 10 ** 6
        else:
            v = minimax(b, depth - 1, -10 ** 9, 10 ** 9, False, me)
        b[i] = 0
        scored.append((v, c))
    top = max(v for v, _ in scored)
    return random.choice([c for v, c in scored if v == top])


class ConnectFour(ov.BaseGame):
    keys = ('M', 'D')

    def setup(self):
        self.mode = 'CPU'
        self.hard = True
        self.score = {1: 0, 2: 0, 0: 0}
        self.starter = 1
        try:
            self.record_mgr = _rec.RecordManager(game_name="connect4")
        except Exception:
            self.record_mgr = None

    def reset(self):
        self.b = [0] * (ROWS * COLS)
        self.turn = self.starter
        self.over = False
        self.win = None
        self.hover = -1
        self.cpu_t = 0.0
        self.cs = 60

    def _record(self, who):
        if not self.record_mgr:
            return
        if who == 0:
            self.record_mgr.add_draw_record()
        elif self.mode == 'CPU' and who == 2:
            self.record_mgr.add_lose_record()
        else:
            self.record_mgr.add_win_record(score=1)

    # ---- logic
    def can_play(self):
        return not self.over and (self.mode == 'PVP' or self.turn == 1)

    def drop(self, c):
        r = drop_row(self.b, c)
        if r < 0:
            return False
        self.b[r * COLS + c] = self.turn
        w = find_win(self.b)
        if w:
            self.over, self.win = True, w
            self.score[self.turn] += 1
            self._record(self.turn)
        elif all(self.b[(ROWS - 1) * COLS + k] for k in range(COLS)):
            self.over = True
            self.score[0] += 1
            self._record(0)
        else:
            self.turn = 3 - self.turn
            return True
        self.starter = 3 - self.starter
        return True

    def update(self, dt):
        if self.mode == 'CPU' and self.turn == 2 and not self.over:
            self.cpu_t += dt
            if self.cpu_t >= 0.4:
                self.cpu_t = 0.0
                free = [c for c in range(
                    COLS) if self.b[(ROWS - 1) * COLS + c] == 0]
                if not self.hard and random.random() < 0.3:
                    self.drop(random.choice(free))
                else:
                    self.drop(pick_move(self.b, 2, 5 if self.hard else 2))
                return True
        return False

    def key(self, key, repeat):
        if key == 'M':
            self.mode = 'PVP' if self.mode == 'CPU' else 'CPU'
            self.score = {1: 0, 2: 0, 0: 0}
            self.starter = 1
            self.reset()
        elif key == 'D':
            self.hard = not self.hard

    # ---- input
    def layout(self, view):
        self.cs = self.fit_cell(view, COLS, ROWS, max_cell=68)
        self.place(view, COLS * self.cs, ROWS * self.cs)

    def _col(self, x, y):
        cr = ov.cell_at(x, y, self.left, self.top, self.cs, COLS, ROWS)
        return -1 if cr is None else cr[0]

    def move(self, x, y):
        h = self._col(x, y)
        changed = h != self.hover
        self.hover = h
        return changed

    def click(self, x, y, button):
        if button != 'LEFT':
            return
        if self.over:
            self.reset()
            return
        c = self._col(x, y)
        if c >= 0 and self.can_play():
            self.drop(c)

    # ---- drawing
    def draw(self, c):
        u, cs = self.u, self.cs
        self.draw_frame(c)

        cpu = self.mode == 'CPU'
        if self.over:
            if not self.win:
                main, col = "Draw!", ov.C_WHITE
            else:
                p = self.b[self.win[0]]
                col = PCOL[p]
                if cpu:
                    main = "You win!" if p == 1 else "CPU wins"
                else:
                    main = "Red wins!" if p == 1 else "Yellow wins!"
        else:
            col = PCOL[self.turn]
            if cpu:
                main = "Your turn (red)" if self.turn == 1 else "CPU is thinking..."
            else:
                main = "Red's turn" if self.turn == 1 else "Yellow's turn"
        mode = ("vs CPU (%s)" %
                ("hard" if self.hard else "easy")) if cpu else "2 players"
        sub = "%s   |   Red: %d   Draws: %d   Yellow: %d" % (
            mode, self.score[1], self.score[0], self.score[2])
        if self.record_mgr:
            r = self.record_mgr.get_records()
            sub += " | Totale W %d L %d D %d" % (
                r['wins'], r['losses'], r['draws'])
        self.draw_header(c, main, sub, col)

        bottom = self.top - ROWS * cs
        c.rect(self.left, bottom, COLS * cs, ROWS * cs, C_BOARD)

        if self.hover >= 0 and self.can_play():
            c.rect(self.left + self.hover * cs, bottom,
                   cs, ROWS * cs, (1, 1, 1, 0.08))

        for r in range(ROWS):
            for k in range(COLS):
                v = self.b[r * COLS + k]
                cx, cy = self.left + k * cs + cs / 2, bottom + r * cs + cs / 2
                c.circle(cx, cy, cs * 0.40, PCOL.get(v, C_HOLE), 28)

        if self.hover >= 0 and self.can_play():
            r = drop_row(self.b, self.hover)
            if r >= 0:
                cx = self.left + self.hover * cs + cs / 2
                cy = bottom + r * cs + cs / 2
                c.circle(cx, cy, cs * 0.40,
                         ov.alpha(PCOL[self.turn], 0.40), 28)

        if self.win:
            for i in self.win:
                r, k = divmod(i, COLS)
                c.ring(self.left + k * cs + cs / 2, bottom + r * cs + cs / 2,
                       cs * 0.40, max(2, cs * 0.07), ov.C_WHITE)

        if self.over:
            self.draw_hint(c, "Click the board or press R for a new round")
        else:
            self.draw_hint(
                c, "Click a column   M mode   D level   R restart   ESC quit")


RUNNER = ov.Runner("connect4", GAME_NAME, ConnectFour, [
    "LMB: drop a disc in a column",
    "M: vs CPU / 2 players",
    "D: easy / hard CPU",
    "R: restart   ESC: quit",
])


def draw_ui(layout, context):
    RUNNER.draw_ui(layout)


def register():
    RUNNER.register()


def unregister():
    RUNNER.unregister()
