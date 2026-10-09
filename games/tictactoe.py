"""Tic Tac Toe – you (X) vs CPU, or two players."""

import random
from functools import lru_cache

from . import _overlay as ov
from . import _record as _rec

GAME_NAME = "Tic Tac Toe"
GAME_ICON = 'MESH_GRID'

C_X = (0.45, 0.70, 1.00, 1.0)
C_O = (1.00, 0.62, 0.30, 1.0)
C_WIN = (0.20, 0.45, 0.30, 1.0)

LINES = ((0, 1, 2), (3, 4, 5), (6, 7, 8),
         (0, 3, 6), (1, 4, 7), (2, 5, 8),
         (0, 4, 8), (2, 4, 6))


def winner(b):
    for l in LINES:
        if b[l[0]] != ' ' and b[l[0]] == b[l[1]] == b[l[2]]:
            return b[l[0]], l
    return None


@lru_cache(maxsize=None)
def _score(board, player):
    w = winner(board)
    if w:
        return 1 if w[0] == 'O' else -1
    if ' ' not in board:
        return 0
    nxt = 'X' if player == 'O' else 'O'
    vals = [_score(board[:i] + player + board[i+1:], nxt)
            for i in range(9) if board[i] == ' ']
    return max(vals) if player == 'O' else min(vals)


def best_move(board):
    scored = [(_score(board[:i]+'O'+board[i+1:], 'X'), i)
              for i in range(9) if board[i] == ' ']
    top = max(s for s, _ in scored)
    return random.choice([i for s, i in scored if s == top])


class TicTacToe(ov.BaseGame):
    keys = ('M', 'D', 'C')  # 'C' = reset records

    # ---- setup ----------------------------------------------------------------
    def setup(self):
        self.mode = 'CPU'
        self.hard = True
        self.score = {'X': 0, 'O': 0, 'D': 0}
        self.starter = 'X'
        try:
            self.record_mgr = _rec.RecordManager(game_name="tictactoe")
        except Exception:
            self.record_mgr = None

    def reset(self):
        self.board = ' ' * 9
        self.turn = self.starter
        self.over = False
        self.line = None
        self.hover = -1
        self.cpu_t = 0.0
        self.cs = 80

    # ---- logic ----------------------------------------------------------------
    def can_play(self):
        return not self.over and (self.mode == 'PVP' or self.turn == 'X')

    def play(self, i):
        self.board = self.board[:i] + self.turn + self.board[i+1:]
        w = winner(self.board)
        if w:
            self.over, self.line = True, w[1]
            self.score[w[0]] += 1
            self._record_result(w[0])
        elif ' ' not in self.board:
            self.over = True
            self.score['D'] += 1
            self._record_draw()
        else:
            self.turn = 'O' if self.turn == 'X' else 'X'
            return
        self.starter = 'O' if self.starter == 'X' else 'X'

    # ---- records ----------------------------------------------------------------
    def _record_result(self, winner_mark):
        if not self.record_mgr:
            return
        if self.mode == 'CPU':
            if winner_mark == 'X':
                self.record_mgr.add_win_record(score=1)
            else:
                self.record_mgr.add_lose_record()
        else:
            # PVP: ogni vittoria conta come "vittoria"
            self.record_mgr.add_win_record(score=1)

    def _record_draw(self):
        if self.record_mgr:
            self.record_mgr.add_draw_record()

    # ---- update ---------------------------------------------------------------
    def update(self, dt):
        if self.mode == 'CPU' and self.turn == 'O' and not self.over:
            self.cpu_t += dt
            if self.cpu_t >= 0.45:
                self.cpu_t = 0.0
                empties = [i for i in range(9) if self.board[i] == ' ']
                if not self.hard and random.random() < 0.45:
                    self.play(random.choice(empties))
                else:
                    self.play(best_move(self.board))
                return True
        return False

    # ---- key ------------------------------------------------------------------
    def key(self, key, repeat):
        if key == 'M':
            self.mode = 'PVP' if self.mode == 'CPU' else 'CPU'
            self.score = {'X': 0, 'O': 0, 'D': 0}
            self.starter = 'X'
            self.reset()
        elif key == 'D':
            self.hard = not self.hard
        elif key == 'C':
            if self.record_mgr:
                self.record_mgr.reset_record()

    # ---- input ----------------------------------------------------------------
    def layout(self, view):
        self.cs = self.fit_cell(view, 3, 3, max_cell=110)
        self.place(view, 3 * self.cs, 3 * self.cs)

    def _idx(self, x, y):
        cr = ov.cell_at(x, y, self.left, self.top, self.cs, 3, 3)
        return -1 if cr is None else cr[1] * 3 + cr[0]

    def move(self, x, y):
        h = self._idx(x, y)
        changed = h != self.hover
        self.hover = h
        return changed

    def click(self, x, y, button):
        if button != 'LEFT':
            return
        if self.over:
            self.reset()
            return
        i = self._idx(x, y)
        if i >= 0 and self.board[i] == ' ' and self.can_play():
            self.play(i)

    # ---- drawing ----------------------------------------------------------------
    def draw(self, c):
        u, cs = self.u, self.cs
        self.draw_frame(c)

        cpu = self.mode == 'CPU'
        if self.over:
            w = winner(self.board)
            if not w:
                main, col = "Draw!", ov.C_WHITE
            elif w[0] == 'X':
                main, col = ("You win!" if cpu else "X wins!"), C_X
            else:
                main, col = ("CPU wins" if cpu else "O wins!"), C_O
        else:
            col = C_X if self.turn == 'X' else C_O
            if cpu:
                main = "Your turn (X)" if self.turn == 'X' else "CPU is thinking..."
            else:
                main = "Player %s's turn" % self.turn

        mode = ("vs CPU (%s)" %
                ("hard" if self.hard else "easy")) if cpu else "2 players"
        sub = "%s | X: %d Draws: %d O: %d" % (
            mode, self.score['X'], self.score['D'], self.score['O'])

        # record persistenti (totale, non solo la sessione)
        if self.record_mgr:
            rec = self.record_mgr.get_records()
            sub += "\nTotale: %d W %d L %d D (giocate: %d)" % (
                rec['wins'], rec['losses'], rec['draws'],
                rec['total_games_played'])

        self.draw_header(c, main, sub, col)

        # griglia
        for i in range(9):
            r, k = divmod(i, 3)
            x, y = self.left + k * cs, self.top - (r + 1) * cs
            bg = ov.C_CELL
            if self.line and i in self.line:
                bg = C_WIN
            elif i == self.hover and self.board[i] == ' ' and self.can_play():
                bg = ov.C_CELL_HOVER
            c.rect(x + 2*u, y + 2*u, cs - 4*u, cs - 4*u, bg)

            m = self.board[i]
            cx, cy = x + cs/2, y + cs/2
            if m == 'X':
                d, w = cs * 0.24, cs * 0.09
                c.line((cx-d, cy-d), (cx+d, cy+d), w, C_X)
                c.line((cx-d, cy+d), (cx+d, cy-d), w, C_X)
            elif m == 'O':
                c.ring(cx, cy, cs * 0.27, cs * 0.09, C_O)

        if self.over:
            self.draw_hint(c, "Click the board or press R for a new round")
        else:
            self.draw_hint(
                c, "Click a cell   M mode   D level   C reset records   R restart   ESC quit")


RUNNER = ov.Runner("tictactoe", GAME_NAME, TicTacToe, [
    "LMB: place your mark",
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
