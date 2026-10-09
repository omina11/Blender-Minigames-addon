"""2048 - slide and merge tiles (drawn as a GPU overlay in the 3D Viewport)."""

import math
import random

from . import _overlay as ov
from . import _record as _rec

GAME_NAME = "2048"
GAME_ICON = 'MESH_PLANE'

N = 4
SLIDE_T = 0.09      # seconds, tiles sliding
POP_T = 0.12        # seconds, merge / spawn pop

BEST = [0]          # best score survives restarts and re-opening the game

C_BOARD = (0.17, 0.18, 0.22, 1.0)
C_LIGHT_TXT = (0.98, 0.97, 0.95, 1.0)
C_DARK_TXT = (0.38, 0.35, 0.32, 1.0)
C_BIG = (0.24, 0.23, 0.20, 1.0)
TILE_COLORS = {
    2: (0.93, 0.89, 0.85, 1.0), 4: (0.93, 0.88, 0.78, 1.0),
    8: (0.95, 0.69, 0.47, 1.0), 16: (0.96, 0.58, 0.39, 1.0),
    32: (0.96, 0.49, 0.37, 1.0), 64: (0.96, 0.37, 0.23, 1.0),
    128: (0.93, 0.81, 0.45, 1.0), 256: (0.93, 0.80, 0.38, 1.0),
    512: (0.93, 0.78, 0.31, 1.0), 1024: (0.93, 0.77, 0.25, 1.0),
    2048: (0.93, 0.76, 0.18, 1.0),
}

DIRS = {
    'LEFT_ARROW': 'L', 'A': 'L',
    'RIGHT_ARROW': 'R', 'D': 'R',
    'UP_ARROW': 'U', 'W': 'U',
    'DOWN_ARROW': 'D', 'S': 'D',
}


# ----------------------------------------------------------------------------
# Game logic (pure Python)
# ----------------------------------------------------------------------------

def _lines(d):
    """Index lines ordered from the edge the tiles slide towards."""
    r4 = range(N)
    if d == 'L':
        return [[r * N + c for c in r4] for r in r4]
    if d == 'R':
        return [[r * N + c for c in reversed(r4)] for r in r4]
    if d == 'U':
        return [[r * N + c for r in r4] for c in r4]
    return [[r * N + c for r in reversed(r4)] for c in r4]


def slide(board, d):
    """Return (new_board, moves, merged, gain).

    moves: list of (src, dst, value) for every tile that exists before the move
    merged: set of destination indices where two tiles merged
    """
    new = [0] * (N * N)
    moves, merged, gain = [], set(), 0
    for line in _lines(d):
        out = []                                    # [value, sources, merged?]
        for i in line:
            v = board[i]
            if not v:
                continue
            if out and out[-1][0] == v and not out[-1][2]:
                out[-1][0] = v * 2
                out[-1][1].append(i)
                out[-1][2] = True
                gain += v * 2
            else:
                out.append([v, [i], False])
        for k, (v, srcs, m) in enumerate(out):
            dst = line[k]
            new[dst] = v
            if m:
                merged.add(dst)
            for s in srcs:
                moves.append((s, dst, board[s]))
    return new, moves, merged, gain


def can_move(board):
    if 0 in board:
        return True
    for r in range(N):
        for c in range(N):
            v = board[r * N + c]
            if c + 1 < N and board[r * N + c + 1] == v:
                return True
            if r + 1 < N and board[(r + 1) * N + c] == v:
                return True
    return False


# ----------------------------------------------------------------------------
# Game (overlay)
# ----------------------------------------------------------------------------

class Game2048(ov.BaseGame):
    keys = tuple(DIRS) + ('U', 'Z')

    def setup(self):
        self.won = False        # 2048 was reached once (don't nag again)
        try:
            self.record_mgr = _rec.RecordManager(game_name="game2048")
            BEST[0] = max(BEST[0], int(
                self.record_mgr.get_records().get("highest_score", 0)))
        except Exception:
            self.record_mgr = None

    def reset(self):
        self.board = [0] * (N * N)
        self.score = 0
        self.over = False
        self.won_pending = False
        self.undo_state = None
        self.anim = None        # dict(moves, merged, spawn)
        self.anim_t = 0.0
        self.cs = 90
        self.won = False
        self._spawn()
        self._spawn()

    # ---- logic
    def _spawn(self):
        empty = [i for i, v in enumerate(self.board) if v == 0]
        if not empty:
            return -1
        i = random.choice(empty)
        self.board[i] = 4 if random.random() < 0.1 else 2
        return i

    def do_move(self, d):
        if self.over:
            return
        self.won_pending = False
        new, moves, merged, gain = slide(self.board, d)
        if new == self.board:
            return
        self.undo_state = (self.board[:], self.score)
        self.board = new
        self.score += gain
        BEST[0] = max(BEST[0], self.score)
        spawn = self._spawn()
        self.anim = {'moves': moves, 'merged': merged, 'spawn': spawn}
        self.anim_t = 0.0
        if not self.won and max(self.board) >= 2048:
            self.won = True
            self.won_pending = True
        if not can_move(self.board):
            self.over = True
        if self.record_mgr:
            self.record_mgr.add_win_record(score=self.score)

    def undo(self):
        if self.undo_state is None:
            return
        self.board, self.score = self.undo_state[0][:], self.undo_state[1]
        self.undo_state = None
        self.anim = None
        self.over = False
        self.won_pending = False

    # ---- input
    def key(self, key, repeat):
        if repeat:
            return
        if key in ('U', 'Z'):
            self.undo()
        else:
            self.do_move(DIRS[key])

    def click(self, x, y, button):
        if self.over:
            self.reset()
        elif self.won_pending:
            self.won_pending = False

    def update(self, dt):
        if self.anim is None:
            return False
        self.anim_t += dt
        if self.anim_t >= SLIDE_T + POP_T:
            self.anim = None
        return True

    def layout(self, view):
        self.cs = self.fit_cell(view, N, N, max_cell=100)
        self.place(view, N * self.cs, N * self.cs)

    # ---- drawing
    def _pos(self, i):
        r, k = divmod(i, N)
        return self.left + k * self.cs, self.top - (r + 1) * self.cs

    def _tile(self, c, x, y, v, scale=1.0):
        cs, u = self.cs, self.u
        gap = max(3.0, 4 * u)
        size = (cs - 2 * gap) * scale
        cx, cy = x + cs / 2, y + cs / 2
        x0, y0 = cx - size / 2, cy - size / 2
        ch = size * 0.12                                   # chamfered corners
        pts = [(x0 + ch, y0), (x0 + size - ch, y0), (x0 + size, y0 + ch),
               (x0 + size, y0 + size - ch), (x0 + size - ch, y0 + size),
               (x0 + ch, y0 + size), (x0, y0 + size - ch), (x0, y0 + ch)]
        c.poly(pts, TILE_COLORS.get(v, C_BIG))
        digits = len(str(v))
        fs = cs * (0.42 if digits <= 2 else 0.34 if digits ==
                   3 else 0.27 if digits == 4 else 0.22)
        c.text(str(v), cx, cy, fs * scale,
               C_DARK_TXT if v <= 4 else C_LIGHT_TXT)

    def draw(self, c):
        u, cs = self.u, self.cs
        self.draw_frame(c)

        if self.over:
            main, col = "Game over", ov.C_BAD
        elif self.won_pending:
            main, col = "You made 2048!", ov.C_GOLD
        else:
            main, col = "2048", ov.C_GOLD
        self.draw_header(c, main, "Score: %d   |   Best: %d" %
                         (self.score, BEST[0]), col)

        c.rect(self.left, self.top - N * cs, N * cs, N * cs, C_BOARD)
        for i in range(N * N):
            x, y = self._pos(i)
            g = max(3.0, 4 * u)
            c.rect(x + g, y + g, cs - 2 * g, cs - 2 * g, ov.C_CELL)

        a = self.anim
        if a is not None and self.anim_t < SLIDE_T:
            p = self.anim_t / SLIDE_T
            p = p * (2 - p)                                # ease out
            for src, dst, v in a['moves']:
                sx, sy = self._pos(src)
                dx, dy = self._pos(dst)
                self._tile(c, sx + (dx - sx) * p, sy + (dy - sy) * p, v)
        else:
            pop = 1.0
            if a is not None:
                pop = min(1.0, (self.anim_t - SLIDE_T) / POP_T)
            for i, v in enumerate(self.board):
                if not v:
                    continue
                s = 1.0
                if a is not None:
                    if i == a['spawn']:
                        s = max(0.3, pop)
                    elif i in a['merged']:
                        s = 1.0 + 0.14 * math.sin(math.pi * pop)
                x, y = self._pos(i)
                self._tile(c, x, y, v, s)

        bx, by, bw = self.left, self.top - N * cs, N * cs
        if self.over:
            self.banner(c, bx, by, bw, bw, "Game over",
                        "Click or press R to play again")
        elif self.won_pending:
            self.banner(c, bx, by, bw, bw, "You made 2048!",
                        "Move or click to keep going")

        self.draw_hint(c, "Arrows / WASD move   U undo   R restart   ESC quit")


RUNNER = ov.Runner("game2048", GAME_NAME, Game2048, [
    "Arrows / WASD: slide tiles",
    "U: undo last move",
    "R: restart   ESC: quit",
])


def draw_ui(layout, context):
    RUNNER.draw_ui(layout)


def register():
    RUNNER.register()


def unregister():
    RUNNER.unregister()
