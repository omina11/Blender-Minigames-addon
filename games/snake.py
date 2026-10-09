"""Snake - keep the mouse over the 3D Viewport and use the keyboard."""

import random

from . import _overlay as ov
from . import _record as _rec

GAME_NAME = "Snake"
GAME_ICON = 'CURVE_PATH'

COLS, ROWS = 24, 16

DIRS = {'UP': (0, 1), 'DOWN': (0, -1), 'LEFT': (-1, 0), 'RIGHT': (1, 0)}
KEYMAP = {
    'UP_ARROW': 'UP', 'W': 'UP',
    'DOWN_ARROW': 'DOWN', 'S': 'DOWN',
    'LEFT_ARROW': 'LEFT', 'A': 'LEFT',
    'RIGHT_ARROW': 'RIGHT', 'D': 'RIGHT',
}
OPPOSITE = {'UP': 'DOWN', 'DOWN': 'UP', 'LEFT': 'RIGHT', 'RIGHT': 'LEFT'}

C_BG1 = (0.15, 0.17, 0.20, 1.0)
C_BG2 = (0.13, 0.15, 0.18, 1.0)
C_HEAD = (0.55, 0.95, 0.45, 1.0)
C_TAIL = (0.18, 0.55, 0.30, 1.0)
C_FOOD = (1.0, 0.30, 0.30, 1.0)


class Snake(ov.BaseGame):
    keys = tuple(KEYMAP) + ('SPACE', 'P', 'M')

    def setup(self):
        self.best = 0
        self.wrap = False
        try:
            self.record_mgr = _rec.RecordManager(game_name="snake")
        except Exception:
            self.record_mgr = None
        if self.record_mgr:
            try:
                self.best = int(self.record_mgr.get_records().get("highest_score", 0))
            except Exception:
                pass

    def reset(self):
        cx, cy = COLS // 2, ROWS // 2
        self.snake = [(cx, cy), (cx - 1, cy), (cx - 2, cy)]
        self.dir = 'RIGHT'
        self.queue = []
        self.score = 0
        self.state = 'ready'  # ready | playing | paused | dead | won
        self.acc = 0.0
        self.cs = 30
        self._food()

    # ---- records
    def _record_end(self):
        # Snake e' a punteggio: ogni partita terminata salva lo score
        if self.record_mgr:
            self.record_mgr.add_win_record(score=self.score)

    # ---- logic
    def _food(self):
        taken = set(self.snake)
        free = [(x, y) for x in range(COLS) for y in range(ROWS) if (x, y) not in taken]
        self.food = random.choice(free) if free else None

    def interval(self):
        return max(0.06, 0.15 - 0.003 * self.score)

    def _step(self):
        if self.queue:
            self.dir = self.queue.pop(0)
        dx, dy = DIRS[self.dir]
        hx, hy = self.snake[0]
        nx, ny = hx + dx, hy + dy
        if self.wrap:
            nx, ny = nx % COLS, ny % ROWS
        elif not (0 <= nx < COLS and 0 <= ny < ROWS):
            self.state = 'dead'
            self.best = max(self.best, self.score)
            self._record_end()
            return
        new = (nx, ny)
        eating = new == self.food
        body = self.snake if eating else self.snake[:-1]
        if new in body:
            self.state = 'dead'
            self.best = max(self.best, self.score)
            self._record_end()
            return
        self.snake.insert(0, new)
        if eating:
            self.score += 1
            self._food()
            if self.food is None:
                self.state = 'won'
                self.best = max(self.best, self.score)
                self._record_end()
        else:
            self.snake.pop()

    def update(self, dt):
        if self.state != 'playing':
            return False
        self.acc += dt
        changed = False
        while self.acc >= self.interval() and self.state == 'playing':
            self.acc -= self.interval()
            self._step()
            changed = True
        return changed

    def key(self, key, repeat):
        if key in KEYMAP:
            d = KEYMAP[key]
            if self.state in ('dead', 'won'):
                return
            if self.state == 'paused':
                self.state = 'playing'
            if self.state == 'ready':
                self.state = 'playing'
            last = self.queue[-1] if self.queue else self.dir
            if d != OPPOSITE[last] and d != last and len(self.queue) < 2:
                self.queue.append(d)
        elif key in ('SPACE', 'P'):
            if self.state in ('dead', 'won'):
                self.reset()
                self.state = 'playing'
            elif self.state == 'playing':
                self.state = 'paused'
            else:
                self.state = 'playing'
        elif key == 'M':
            self.wrap = not self.wrap
            self.reset()

    # ---- drawing
    def layout(self, view):
        self.cs = self.fit_cell(view, COLS, ROWS, max_cell=32)
        self.place(view, COLS * self.cs, ROWS * self.cs)

    def draw(self, c):
        u, cs = self.u, self.cs
        self.draw_frame(c)
        self.draw_header(
            c, "SNAKE",
            "Score: %d   Best: %d   Walls: %s" % (self.score, self.best, "wrap" if self.wrap else "deadly"))

        bottom = self.top - ROWS * cs
        for x in range(COLS):
            for y in range(ROWS):
                c.rect(self.left + x * cs, bottom + y * cs, cs, cs,
                       C_BG1 if (x + y) % 2 == 0 else C_BG2)

        if self.food:
            fx, fy = self.food
            c.circle(self.left + fx * cs + cs / 2, bottom + fy * cs + cs / 2, cs * 0.34, C_FOOD)
            c.circle(self.left + fx * cs + cs * 0.4, bottom + fy * cs + cs * 0.6, cs * 0.08,
                     (1, 1, 1, 0.7), 8)

        n = len(self.snake)
        g = max(1.0, cs * 0.05)
        for i in range(n - 1, -1, -1):
            x, y = self.snake[i]
            t = i / max(1, n - 1)
            col = tuple(C_HEAD[k] * (1 - t) + C_TAIL[k] * t for k in range(4))
            c.rect(self.left + x * cs + g, bottom + y * cs + g, cs - 2 * g, cs - 2 * g,
                   ov.C_GOOD if self.state == 'dead' and i == 0 else col)
        hx, hy = self.snake[0]
        ex, ey = DIRS[self.dir]
        cx, cy = self.left + hx * cs + cs / 2, bottom + hy * cs + cs / 2
        for s in (-1, 1):
            px = cx + ex * cs * 0.18 + (-ey) * s * cs * 0.18
            py = cy + ey * cs * 0.18 + ex * s * cs * 0.18
            c.circle(px, py, cs * 0.07, ov.C_DARK, 8)

        bw, bh = COLS * cs, ROWS * cs
        if self.state == 'ready':
            self.banner(c, self.left, bottom, bw, bh, "Ready?", "Press an arrow key / WASD to start")
        elif self.state == 'paused':
            self.banner(c, self.left, bottom, bw, bh, "Paused", "Press SPACE or a direction to resume")
        elif self.state == 'dead':
            self.banner(c, self.left, bottom, bw, bh, "Game over", "Score %d - press SPACE to retry" % self.score)
        elif self.state == 'won':
            self.banner(c, self.left, bottom, bw, bh, "You filled the board!", "Press SPACE to play again")

        self.draw_hint(c, "Arrows/WASD move   SPACE pause   M walls   ESC quit")


RUNNER = ov.Runner("snake", GAME_NAME, Snake, [
    "Arrows / WASD: steer",
    "SPACE or P: pause",
    "M: deadly walls / wrap-around",
    "R: restart   ESC: quit",
    "Keep the mouse over the viewport",
])


def draw_ui(layout, context):
    RUNNER.draw_ui(layout)


def register():
    RUNNER.register()


def unregister():
    RUNNER.unregister()
