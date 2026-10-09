"""Minesweeper - playable in the 3D Viewport (drawn as a GPU overlay).

The best winning time is saved for each level (minesweeper_beginner / intermediate / expert).
"""

import math
import random
import time
import traceback

import bpy

from . import _overlay as ov
from . import _record as _rec

GAME_NAME = "Minesweeper"
GAME_ICON = 'MESH_ICOSPHERE'

# ----------------------------------------------------------------------------
# Game logic (pure Python, no Blender dependency)
# ----------------------------------------------------------------------------

LEVELS = {
    'BEGINNER': (9, 9, 10),          # cols, rows, mines
    'INTERMEDIATE': (16, 16, 40),
    'EXPERT': (30, 16, 99),
}


class Board:
    def __init__(self, cols, rows, mines):
        self.cols, self.rows, self.n_mines = cols, rows, mines
        n = cols * rows
        self.mine = [False] * n
        self.count = [0] * n
        self.revealed = [False] * n
        self.flag = [False] * n
        self.state = 'ready'         # ready | playing | won | lost
        self.hit = -1
        self.placed = False
        self.n_revealed = 0
        self.n_flags = 0
        self.t0 = self.t1 = 0.0
        self.nbrs = []
        for i in range(n):
            x, y = i % cols, i // cols
            self.nbrs.append([
                (y + dy) * cols + (x + dx)
                for dy in (-1, 0, 1) for dx in (-1, 0, 1)
                if (dx or dy) and 0 <= x + dx < cols and 0 <= y + dy < rows
            ])

    # -- helpers
    def mines_left(self):
        return self.n_mines - self.n_flags

    def elapsed(self):
        if self.state == 'ready':
            return 0
        end = time.time() if self.state == 'playing' else self.t1
        return int(min(999, end - self.t0))

    def _place(self, safe):
        n = self.cols * self.rows
        excluded = set(self.nbrs[safe]) | {safe}
        candidates = [i for i in range(n) if i not in excluded]
        if len(candidates) < self.n_mines:
            candidates = [i for i in range(n) if i != safe]
        for i in random.sample(candidates, self.n_mines):
            self.mine[i] = True
        for i in range(n):
            self.count[i] = sum(self.mine[j] for j in self.nbrs[i])
        self.placed = True

    # -- actions
    def reveal(self, i):
        if self.state in ('won', 'lost') or self.flag[i] or self.revealed[i]:
            return
        if not self.placed:
            self._place(i)
            self.state = 'playing'
            self.t0 = time.time()
        if self.mine[i]:
            self.state, self.hit, self.t1 = 'lost', i, time.time()
            return
        stack = [i]
        while stack:
            j = stack.pop()
            if self.revealed[j] or self.flag[j]:
                continue
            self.revealed[j] = True
            self.n_revealed += 1
            if self.count[j] == 0:
                stack.extend(k for k in self.nbrs[j] if not self.revealed[k])
        if self.n_revealed == self.cols * self.rows - self.n_mines:
            self.state, self.t1 = 'won', time.time()
            for k, m in enumerate(self.mine):
                if m:
                    self.flag[k] = True
            self.n_flags = self.n_mines

    def chord(self, i):
        if self.state != 'playing' or not self.revealed[i] or self.count[i] == 0:
            return
        if sum(self.flag[k] for k in self.nbrs[i]) != self.count[i]:
            return
        for k in self.nbrs[i]:
            if not self.flag[k] and not self.revealed[k]:
                self.reveal(k)

    def toggle_flag(self, i):
        if self.state in ('won', 'lost') or self.revealed[i]:
            return
        self.flag[i] = not self.flag[i]
        self.n_flags += 1 if self.flag[i] else -1


# ----------------------------------------------------------------------------
# Colours / shapes
# ----------------------------------------------------------------------------

C_BOX = (0.05, 0.05, 0.06, 1.0)
C_LED = (1.0, 0.25, 0.2, 1.0)
C_UP = (0.62, 0.66, 0.73, 1.0)
C_UP_HOVER = (0.72, 0.77, 0.85, 1.0)
C_HI = (0.85, 0.88, 0.94, 1.0)
C_SH = (0.30, 0.33, 0.39, 1.0)
C_GRID = (0.10, 0.11, 0.13, 1.0)
C_OPEN = (0.22, 0.24, 0.29, 1.0)
C_HIT = (0.78, 0.16, 0.16, 1.0)
C_FLAG = (0.95, 0.25, 0.25, 1.0)
C_MINE = (0.93, 0.93, 0.96, 1.0)
C_FACE = (1.0, 0.82, 0.2, 1.0)
NUM_COLORS = {
    1: (0.45, 0.65, 1.0, 1), 2: (0.35, 0.85, 0.45, 1), 3: (1.0, 0.45, 0.45, 1),
    4: (0.65, 0.55, 1.0, 1), 5: (1.0, 0.65, 0.3, 1), 6: (0.3, 0.9, 0.9, 1),
    7: (0.95, 0.95, 0.95, 1), 8: (0.7, 0.7, 0.7, 1),
}


def _draw_mine(c, cx, cy, cs):
    r = cs * 0.2
    for k in range(4):
        a = k * math.pi / 4
        dx, dy = math.cos(a) * cs * 0.3, math.sin(a) * cs * 0.3
        c.line((cx - dx, cy - dy), (cx + dx, cy + dy), max(2, cs * 0.07), C_MINE)
    c.circle(cx, cy, r, C_MINE, 20)
    c.circle(cx - r * 0.35, cy + r * 0.35, r * 0.28, ov.C_DARK, 10)


def _draw_flag(c, cx, cy, cs):
    px = cx + cs * 0.06
    w = max(2, cs * 0.08)
    y0, y1 = cy - cs * 0.27, cy + cs * 0.3
    c.rect(px - w / 2, y0, w, y1 - y0, ov.C_DARK)
    c.rect(cx - cs * 0.2, y0 - w * 0.4, cs * 0.4, w * 1.4, ov.C_DARK)
    c.tri((px, y1), (px - cs * 0.32, y1 - cs * 0.15), (px, y1 - cs * 0.3), C_FLAG)


# ----------------------------------------------------------------------------
# Game (overlay)
# ----------------------------------------------------------------------------

class Minesweeper(ov.BaseGame):
    keys = ('ONE', 'TWO', 'THREE')

    # ---- records (one file per level)
    def _mgr(self, level):
        if level not in self.record_mgrs:
            try:
                self.record_mgrs[level] = _rec.RecordManager(game_name="minesweeper_" + level.lower())
            except Exception:
                self.record_mgrs[level] = None
        return self.record_mgrs[level]

    def best_time(self, level):
        if level not in self.best_cache:
            m = self._mgr(level)
            try:
                self.best_cache[level] = m.get_records().get("best_time") if m else None
            except Exception:
                self.best_cache[level] = None
        return self.best_cache[level]

    @staticmethod
    def _fmt(t):
        return "--" if t is None else "%.1f s" % t

    def _check_end(self):
        """Save the result once, as soon as the board is won or lost."""
        b = self.board
        if self.recorded or b.state not in ('won', 'lost'):
            return
        self.recorded = True
        m = self._mgr(self.level)
        if not m:
            return
        try:
            if b.state == 'won':
                t = b.t1 - b.t0
                prev = self.best_time(self.level)
                try:
                    m.add_win_record(score=0, best_time=t)
                except TypeError:         # _record.py without best_time support
                    m.add_win_record(score=0)
                self.new_best = prev is None or t < prev
                self.last_time = t
                self.best_cache.pop(self.level, None)
            else:
                m.add_lose_record()
        except Exception:
            traceback.print_exc()

    def setup(self):
        self.record_mgrs = {}             # level -> RecordManager
        self.best_cache = {}              # level -> best time in seconds (or None)
        try:
            self.level = bpy.context.window_manager.minesweeper_level
        except Exception:
            self.level = 'BEGINNER'

    def reset(self):
        self.board = Board(*LEVELS[self.level])
        self.recorded = False
        self.new_best = False
        self.last_time = 0.0
        self.hover = -1
        self.hover_face = False
        self.shown_t = 0
        self.cs = 40
        self.face_s = 40
        self.face_x = self.face_y = 0.0
        self.box_w = self.box_h = self.box_y = 0.0
        self.mines_x = self.timer_x = 0.0

    def set_level(self, level):
        self.level = level
        self.reset()

    # ---- layout
    def layout(self, view):
        b = self.board
        self.cs = self.fit_cell(view, b.cols, b.rows, max_cell=40, header=52, min_cell=12)
        self.place(view, b.cols * self.cs, b.rows * self.cs, header=52, min_w=400)

        u = self.u
        px, _, pw, _ = self.panel
        pad = 12 * u
        self.face_s = 40 * u
        self.face_x = px + pw / 2 - self.face_s / 2
        self.face_y = self.hy - self.face_s / 2
        self.box_w, self.box_h = 84 * u, 36 * u
        self.box_y = self.hy - self.box_h / 2
        self.mines_x = px + pad
        self.timer_x = px + pw - pad - self.box_w

    def _in_face(self, x, y):
        return (self.face_x <= x <= self.face_x + self.face_s and
                self.face_y <= y <= self.face_y + self.face_s)

    def _idx(self, x, y):
        b = self.board
        cr = ov.cell_at(x, y, self.left, self.top, self.cs, b.cols, b.rows)
        return -1 if cr is None else cr[1] * b.cols + cr[0]

    # ---- input
    def move(self, x, y):
        h, hf = self._idx(x, y), self._in_face(x, y)
        changed = (h != self.hover or hf != self.hover_face)
        self.hover, self.hover_face = h, hf
        return changed

    def click(self, x, y, button):
        self._click(x, y, button)
        self._check_end()

    def _click(self, x, y, button):
        if button == 'LEFT' and self._in_face(x, y):
            self.reset()
            return
        i = self._idx(x, y)
        if i < 0:
            return
        b = self.board
        if button == 'RIGHT':
            b.toggle_flag(i)
        elif b.revealed[i]:
            b.chord(i)
        else:
            b.reveal(i)

    def key(self, key, repeat):
        level = {'ONE': 'BEGINNER', 'TWO': 'INTERMEDIATE', 'THREE': 'EXPERT'}[key]
        self.set_level(level)
        try:
            bpy.context.window_manager.minesweeper_level = level   # sync the sidebar
        except Exception:
            pass

    def update(self, dt):
        # only redraw when the displayed second changes
        if self.board.state == 'playing':
            t = self.board.elapsed()
            if t != self.shown_t:
                self.shown_t = t
                return True
        return False

    # ---- drawing
    def _draw_face(self, c):
        u = self.u
        s = self.board.state
        c.bevel(self.face_x, self.face_y, self.face_s, self.face_s, max(2, 2 * u),
                C_UP_HOVER if self.hover_face else C_UP, C_HI, C_SH)
        cx, cy = self.face_x + self.face_s / 2, self.face_y + self.face_s / 2
        c.circle(cx, cy, 15 * u, C_FACE, 28)
        lw = max(1.5, 1.8 * u)
        dark = ov.C_DARK
        if s == 'lost':
            for sg in (-1, 1):
                ex, ey = cx + sg * 5 * u, cy + 4 * u
                c.line((ex - 2.5 * u, ey - 2.5 * u), (ex + 2.5 * u, ey + 2.5 * u), lw, dark)
                c.line((ex - 2.5 * u, ey + 2.5 * u), (ex + 2.5 * u, ey - 2.5 * u), lw, dark)
            pts = [(cx + 7 * u * math.cos(math.radians(a)),
                    cy - 9 * u + 7 * u * math.sin(math.radians(a))) for a in range(30, 151, 15)]
            c.polyline(pts, lw, dark)
            return
        if s == 'won':
            c.rect(cx - 11 * u, cy + 1 * u, 9 * u, 6 * u, dark)
            c.rect(cx + 2 * u, cy + 1 * u, 9 * u, 6 * u, dark)
            c.line((cx - 2 * u, cy + 5 * u), (cx + 2 * u, cy + 5 * u), lw, dark)
        else:
            for sg in (-1, 1):
                c.circle(cx + sg * 5 * u, cy + 4 * u, 1.9 * u, dark, 8)
        pts = [(cx + 8 * u * math.cos(math.radians(a)),
                cy + 2 * u + 8 * u * math.sin(math.radians(a))) for a in range(210, 331, 15)]
        c.polyline(pts, lw, dark)

    def draw(self, c):
        u, cs = self.u, self.cs
        b = self.board
        self.draw_frame(c)

        for bx, val in ((self.mines_x, b.mines_left()), (self.timer_x, b.elapsed())):
            c.rect(bx, self.box_y, self.box_w, self.box_h, C_BOX)
            c.text("%03d" % max(-99, min(999, val)),
                   bx + self.box_w / 2, self.box_y + self.box_h / 2, 22 * u, C_LED)

        self._draw_face(c)

        lost = b.state == 'lost'
        bev = max(2, cs * 0.08)
        for r in range(b.rows):
            for k in range(b.cols):
                i = r * b.cols + k
                x, y = self.left + k * cs, self.top - (r + 1) * cs
                cx, cy = x + cs / 2, y + cs / 2
                show_mine = lost and b.mine[i] and not b.flag[i]
                wrong_flag = lost and b.flag[i] and not b.mine[i]

                if b.revealed[i] or show_mine or wrong_flag:
                    c.rect(x, y, cs, cs, C_GRID)
                    c.rect(x + 1, y + 1, cs - 2, cs - 2, C_HIT if i == b.hit else C_OPEN)
                    if show_mine or wrong_flag:
                        _draw_mine(c, cx, cy, cs)
                        if wrong_flag:
                            d = cs * 0.3
                            w = max(2, cs * 0.09)
                            c.line((cx - d, cy - d), (cx + d, cy + d), w, C_FLAG)
                            c.line((cx - d, cy + d), (cx + d, cy - d), w, C_FLAG)
                    elif b.count[i] > 0:
                        c.text(str(b.count[i]), cx, cy, cs * 0.62, NUM_COLORS[b.count[i]])
                else:
                    hovered = (i == self.hover and b.state in ('ready', 'playing'))
                    c.bevel(x + 1, y + 1, cs - 2, cs - 2, bev,
                            C_UP_HOVER if hovered else C_UP, C_HI, C_SH)
                    if b.flag[i]:
                        _draw_flag(c, cx, cy, cs)

        if b.state == 'won':
            msg = "You win!  %s%s  -  best %s  -  press R to play again" % (
                self._fmt(self.last_time), "  NEW BEST!" if self.new_best else "",
                self._fmt(self.best_time(self.level)))
        elif b.state == 'lost':
            msg = "Boom!  Press R to try again   (best %s)" % self._fmt(self.best_time(self.level))
        else:
            msg = "LMB reveal   RMB flag   R restart   1/2/3 level   Best: %s   ESC quit" % (
                self._fmt(self.best_time(self.level)))
        self.draw_hint(c, msg)


RUNNER = ov.Runner("minesweeper", GAME_NAME, Minesweeper, [
    "LMB: reveal / chord",
    "RMB: flag",
    "R: restart   1/2/3: level",
    "ESC: quit",
])


# ----------------------------------------------------------------------------
# UI / registration (called by the Minigames loader)
# ----------------------------------------------------------------------------

def draw_ui(layout, context):
    layout.column(align=True).prop(context.window_manager, "minesweeper_level", expand=True)
    g = RUNNER.game
    if g is not None and hasattr(g, 'best_time'):
        col = layout.column(align=True)
        col.label(text="Best times")
        for lv, label in (('BEGINNER', "Beginner"), ('INTERMEDIATE', "Intermediate"), ('EXPERT', "Expert")):
            col.label(text="%s: %s" % (label, g._fmt(g.best_time(lv))))
    layout.separator()
    RUNNER.draw_ui(layout)


def _level_update(self, context):
    g = RUNNER.game
    if RUNNER.running and g is not None and g.level != self.minesweeper_level:
        g.set_level(self.minesweeper_level)
        ov._redraw_all()


def register():
    bpy.types.WindowManager.minesweeper_level = bpy.props.EnumProperty(
        name="Level",
        items=[
            ('BEGINNER', "Beginner (9x9, 10 mines)", ""),
            ('INTERMEDIATE', "Intermediate (16x16, 40)", ""),
            ('EXPERT', "Expert (30x16, 99)", ""),
        ],
        default='BEGINNER',
        update=_level_update,
    )
    RUNNER.register()


def unregister():
    RUNNER.unregister()
    del bpy.types.WindowManager.minesweeper_level
