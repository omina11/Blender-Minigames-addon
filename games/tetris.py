"""Tetris - keep the mouse over the 3D Viewport and use the keyboard."""

import random

from . import _overlay as ov
from . import _record as _rec

GAME_NAME = "Tetris"
GAME_ICON = 'MOD_ARRAY'

W, H = 10, 20

# kind: (box size, cells (x, y) with y pointing DOWN)
PIECES = {
    'I': (4, [(0, 1), (1, 1), (2, 1), (3, 1)]),
    'O': (2, [(0, 0), (1, 0), (0, 1), (1, 1)]),
    'T': (3, [(1, 0), (0, 1), (1, 1), (2, 1)]),
    'S': (3, [(1, 0), (2, 0), (0, 1), (1, 1)]),
    'Z': (3, [(0, 0), (1, 0), (1, 1), (2, 1)]),
    'J': (3, [(0, 0), (0, 1), (1, 1), (2, 1)]),
    'L': (3, [(2, 0), (0, 1), (1, 1), (2, 1)]),
}

COLORS = {
    'I': (0.30, 0.85, 0.90, 1.0), 'O': (0.95, 0.85, 0.25, 1.0),
    'T': (0.70, 0.40, 0.90, 1.0), 'S': (0.40, 0.85, 0.40, 1.0),
    'Z': (0.95, 0.35, 0.35, 1.0), 'J': (0.35, 0.50, 0.95, 1.0),
    'L': (0.95, 0.60, 0.25, 1.0),
}

KINDS = tuple(PIECES)
LINE_SCORE = (0, 100, 300, 500, 800)

# ROT[kind][rotation] -> cells after `rotation` clockwise turns
ROT = {}
for _k, (_n, _cells) in PIECES.items():
    _states = [list(_cells)]
    for _ in range(3):
        _states.append([(_n - 1 - y, x) for x, y in _states[-1]])
    ROT[_k] = _states

C_EMPTY = (0.10, 0.11, 0.13, 1.0)


class Tetris(ov.BaseGame):
    keys = ('LEFT_ARROW', 'RIGHT_ARROW', 'DOWN_ARROW', 'UP_ARROW', 'SPACE',
            'A', 'D', 'S', 'W', 'X', 'Z', 'C', 'P')

    def setup(self):
        self.best = 0
        try:
            self.record_mgr = _rec.RecordManager(game_name="tetris")
        except Exception:
            self.record_mgr = None
        if self.record_mgr:
            try:
                self.best = int(
                    self.record_mgr.get_records().get("highest_score", 0))
            except Exception:
                pass

    def reset(self):
        self.board = [[None] * W for _ in range(H)]
        self.bag = []
        self.score = 0
        self.lines = 0
        self.level = 1
        self.state = 'playing'  # playing | paused | over
        self.acc = 0.0
        self.hold = None
        self.can_hold = True
        self.cs = 28
        self.next = self._bag_kind()
        self._spawn()

    # ---- records
    def _record_game_over(self):
        # Tetris non ha vittoria/sconfitta: ogni partita salva il punteggio
        if self.record_mgr:
            self.record_mgr.add_win_record(score=self.score)

    # ---- logic
    def _bag_kind(self):
        if not self.bag:
            self.bag = random.sample(KINDS, len(KINDS))
        return self.bag.pop()

    def _collide(self, kind, rot, x, y):
        for cx, cy in ROT[kind][rot]:
            bx, by = x + cx, y + cy
            if bx < 0 or bx >= W or by >= H:
                return True
            if by >= 0 and self.board[by][bx] is not None:
                return True
        return False

    def _spawn(self, kind=None):
        if kind is None:
            kind = self.next
            self.next = self._bag_kind()
        self.kind, self.rot = kind, 0
        self.x, self.y = (W - PIECES[kind][0]) // 2, 0
        self.acc = 0.0
        if self._collide(self.kind, self.rot, self.x, self.y):
            self.state = 'over'
            self.best = max(self.best, self.score)
            self._record_game_over()

    def _ghost_y(self):
        y = self.y
        while not self._collide(self.kind, self.rot, self.x, y + 1):
            y += 1
        return y

    def _lock(self):
        for cx, cy in ROT[self.kind][self.rot]:
            bx, by = self.x + cx, self.y + cy
            if 0 <= by < H:
                self.board[by][bx] = self.kind
        full = [r for r in range(H) if all(self.board[r])]
        if full:
            self.board = [[None] * W for _ in full] + \
                [row for i, row in enumerate(self.board) if i not in full]
            self.lines += len(full)
            self.score += LINE_SCORE[len(full)] * self.level
            self.level = self.lines // 10 + 1
        self.can_hold = True
        self._spawn()

    def interval(self):
        return max(0.05, 0.8 * 0.85 ** (self.level - 1))

    def update(self, dt):
        if self.state != 'playing':
            return False
        self.acc += dt
        if self.acc >= self.interval():
            self.acc = 0.0
            if not self._collide(self.kind, self.rot, self.x, self.y + 1):
                self.y += 1
            else:
                self._lock()
            return True
        return False

    def _rotate(self, d):
        nr = (self.rot + d) % 4
        for dx in (0, -1, 1, -2, 2):
            if not self._collide(self.kind, nr, self.x + dx, self.y):
                self.rot, self.x = nr, self.x + dx
                return

    def key(self, key, repeat):
        if key == 'P':
            if self.state == 'playing':
                self.state = 'paused'
            elif self.state == 'paused':
                self.state = 'playing'
            return
        if key == 'SPACE' and self.state == 'over':
            self.reset()
            return
        if self.state != 'playing':
            return
        if key in ('LEFT_ARROW', 'A'):
            if not self._collide(self.kind, self.rot, self.x - 1, self.y):
                self.x -= 1
        elif key in ('RIGHT_ARROW', 'D'):
            if not self._collide(self.kind, self.rot, self.x + 1, self.y):
                self.x += 1
        elif key in ('DOWN_ARROW', 'S'):
            if not self._collide(self.kind, self.rot, self.x, self.y + 1):
                self.y += 1
                self.score += 1
                self.acc = 0.0
        elif key in ('UP_ARROW', 'W', 'X'):
            if not repeat:
                self._rotate(1)
        elif key == 'Z':
            if not repeat:
                self._rotate(-1)
        elif key == 'SPACE':
            if not repeat:
                gy = self._ghost_y()
                self.score += 2 * (gy - self.y)
                self.y = gy
                self._lock()
        elif key == 'C':
            if self.can_hold and not repeat:
                held, self.hold = self.hold, self.kind
                self.can_hold = False
                self._spawn(held)

    # ---- drawing
    def layout(self, view):
        self.cs = self.fit_cell(view, W + 5.6, H, max_cell=34)
        self.gap = self.cs * 0.6
        self.side = self.cs * 5
        self.place(view, W * self.cs + self.gap + self.side, H * self.cs)

    def _block(self, c, x, y, cs, col):
        b = max(2, cs * 0.12)
        c.bevel(x + 1, y + 1, cs - 2, cs - 2, b, col)

    def _preview(self, c, kind, cx, cy, size):
        cells = ROT[kind][0]
        xs = [p[0] for p in cells]
        ys = [p[1] for p in cells]
        mx, my = (min(xs) + max(xs) + 1) / 2, (min(ys) + max(ys) + 1) / 2
        for px, py in cells:
            self._block(c, cx + (px - mx) * size, cy -
                        (py - my + 1) * size, size, COLORS[kind])

    def draw(self, c):
        u, cs = self.u, self.cs
        self.draw_frame(c)
        status = {'playing': "TETRIS", 'paused': "PAUSED",
                  'over': "GAME OVER"}[self.state]
        self.draw_header(c, status, "Score: %d   Lines: %d   Level: %d   Best: %d" % (
            self.score, self.lines, self.level, self.best))

        bottom = self.top - H * cs
        for r in range(H):
            for k in range(W):
                kind = self.board[r][k]
                x, y = self.left + k * cs, self.top - (r + 1) * cs
                if kind:
                    self._block(c, x, y, cs, COLORS[kind])
                else:
                    c.rect(x + 1, y + 1, cs - 2, cs - 2, C_EMPTY)

        if self.state != 'over':
            gy = self._ghost_y()
            for cx, cy in ROT[self.kind][self.rot]:
                if gy + cy >= 0:
                    c.rect(self.left + (self.x + cx) * cs + 1, self.top - (gy + cy + 1) * cs + 1,
                           cs - 2, cs - 2, ov.alpha(COLORS[self.kind], 0.22))
            for cx, cy in ROT[self.kind][self.rot]:
                if self.y + cy >= 0:
                    self._block(c, self.left + (self.x + cx) * cs,
                                self.top - (self.y + cy + 1) * cs, cs, COLORS[self.kind])

        # side panel
        sx = self.left + W * cs + self.gap
        box = self.side
        c.text("NEXT", sx + box / 2, self.top - 0.4 * cs, 13 * u, ov.C_TEXT)
        c.rect(sx, self.top - 0.9 * cs - 3 * cs, box, 3 * cs, C_EMPTY)
        self._preview(c, self.next, sx + box / 2, self.top -
                      0.9 * cs - 1.5 * cs, cs * 0.75)
        hy = self.top - 5 * cs
        c.text("HOLD", sx + box / 2, hy - 0.4 * cs, 13 * u, ov.C_TEXT)
        c.rect(sx, hy - 0.9 * cs - 3 * cs, box, 3 * cs, C_EMPTY)
        if self.hold:
            col = COLORS[self.hold] if self.can_hold else ov.shade(
                COLORS[self.hold], 0.45)
            self._preview(c, self.hold, sx + box / 2, hy -
                          0.9 * cs - 1.5 * cs, cs * 0.75)
            if not self.can_hold:
                c.rect(sx, hy - 0.9 * cs - 3 * cs,
                       box, 3 * cs, (0, 0, 0, 0.45))

        bw, bh = W * cs, H * cs
        if self.state == 'paused':
            self.banner(c, self.left, bottom, bw, bh,
                        "Paused", "Press P to resume")
        elif self.state == 'over':
            self.banner(c, self.left, bottom, bw, bh,
                        "Game over", "Press SPACE or R to retry")

        self.draw_hint(
            c, "<- -> move   Up rotate   Down soft   SPACE drop   C hold   P")


RUNNER = ov.Runner("tetris", GAME_NAME, Tetris, [
    "Left/Right (A/D): move",
    "Up (W/X) / Z: rotate",
    "Down (S): soft drop",
    "SPACE: hard drop   C: hold",
    "P: pause   R: restart   ESC: quit",
    "Keep the mouse over the viewport",
])


def draw_ui(layout, context):
    RUNNER.draw_ui(layout)


def register():
    RUNNER.register()


def unregister():
    RUNNER.unregister()
