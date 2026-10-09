"""Shared framework for overlay games (drawn in the 3D Viewport).

Not a game itself (leading underscore => ignored by the game loader).

A game subclasses BaseGame and is wrapped in a Runner:

    RUNNER = ov.Runner("mygame", GAME_NAME, MyGame, ["help line", ...])

    def draw_ui(layout, context): RUNNER.draw_ui(layout)
    def register():   RUNNER.register()
    def unregister(): RUNNER.unregister()

The Runner creates/registers the "Play" operator, the draw handler, the timer
and takes care of input: ESC quits, R calls game.reset(), keys listed in
game.keys go to game.key(), mouse clicks inside game.panel go to game.click().
"""

import math
import time
import traceback

import blf
import bpy
import gpu
from bpy.app.handlers import persistent
from gpu_extras.batch import batch_for_shader

# ----------------------------------------------------------------------------
# Palette / colour helpers
# ----------------------------------------------------------------------------

C_BORDER = (0.35, 0.38, 0.43, 1.0)
C_PANEL = (0.13, 0.14, 0.17, 0.96)
C_TEXT = (0.75, 0.78, 0.84, 1.0)
C_WHITE = (0.95, 0.96, 0.98, 1.0)
C_DARK = (0.08, 0.08, 0.10, 1.0)
C_CELL = (0.22, 0.24, 0.29, 1.0)
C_CELL_HOVER = (0.30, 0.33, 0.40, 1.0)
C_GOOD = (0.40, 0.85, 0.50, 1.0)
C_BAD = (1.0, 0.40, 0.40, 1.0)
C_GOLD = (1.0, 0.82, 0.25, 1.0)


def alpha(col, a):
    return (col[0], col[1], col[2], a)


def shade(col, f):
    return (min(1.0, col[0] * f), min(1.0, col[1] * f), min(1.0, col[2] * f), col[3])


# ----------------------------------------------------------------------------
# View (usable area of the viewport, avoiding overlapping sidebar/toolbar)
# ----------------------------------------------------------------------------

class View:
    def __init__(self, x0, y0, x1, y1, u):
        self.x0, self.y0, self.x1, self.y1, self.u = x0, y0, x1, y1, u
        self.w = max(50, x1 - x0)
        self.h = max(50, y1 - y0)


def compute_view(area, win):
    ps = bpy.context.preferences.system
    try:
        u = ps.ui_scale * ps.pixel_size
    except Exception:
        u = ps.ui_scale
    x0, x1, y1 = 0, win.width, win.height
    for r in area.regions:
        if r.width <= 1 or r.height <= 1:
            continue
        if r.type == 'UI':
            x1 = min(x1, r.x - win.x)
        elif r.type == 'TOOLS':
            x0 = max(x0, r.x + r.width - win.x)
        elif r.type in {'HEADER', 'TOOL_HEADER'} and r.y > win.y:
            y1 = min(y1, r.y - win.y)
    return View(x0, 0, x1, y1, u)


# ----------------------------------------------------------------------------
# Canvas: batched 2D triangles + text
# ----------------------------------------------------------------------------

_shader = None


def _get_shader():
    global _shader
    if _shader is None:
        try:
            _shader = gpu.shader.from_builtin('FLAT_COLOR')
        except Exception:
            _shader = gpu.shader.from_builtin('2D_FLAT_COLOR')
    return _shader


def _font_size(size):
    size = max(6, int(size))
    try:
        blf.size(0, size)
    except TypeError:
        blf.size(0, size, 72)


class Canvas:
    def __init__(self):
        self.v, self.c, self.t = [], [], []

    def tri(self, a, b, c, col):
        self.v += [a, b, c]
        self.c += [col, col, col]

    def rect(self, x, y, w, h, col):
        a, b, c, d = (x, y), (x + w, y), (x + w, y + h), (x, y + h)
        self.tri(a, b, c, col)
        self.tri(a, c, d, col)

    def poly(self, pts, col):
        """Filled convex polygon."""
        for i in range(1, len(pts) - 1):
            self.tri(pts[0], pts[i], pts[i + 1], col)

    def circle(self, cx, cy, r, col, seg=24):
        for i in range(seg):
            a0, a1 = 2 * math.pi * i / seg, 2 * math.pi * (i + 1) / seg
            self.tri((cx, cy),
                     (cx + r * math.cos(a0), cy + r * math.sin(a0)),
                     (cx + r * math.cos(a1), cy + r * math.sin(a1)), col)

    def ring(self, cx, cy, r, th, col, seg=28):
        ro, ri = r + th / 2, max(0.0, r - th / 2)
        for i in range(seg):
            a0, a1 = 2 * math.pi * i / seg, 2 * math.pi * (i + 1) / seg
            p0 = (cx + ro * math.cos(a0), cy + ro * math.sin(a0))
            p1 = (cx + ro * math.cos(a1), cy + ro * math.sin(a1))
            q0 = (cx + ri * math.cos(a0), cy + ri * math.sin(a0))
            q1 = (cx + ri * math.cos(a1), cy + ri * math.sin(a1))
            self.tri(p0, p1, q1, col)
            self.tri(p0, q1, q0, col)

    def line(self, p, q, w, col):
        dx, dy = q[0] - p[0], q[1] - p[1]
        ln = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / ln * w / 2, dx / ln * w / 2
        a, b = (p[0] + nx, p[1] + ny), (p[0] - nx, p[1] - ny)
        c, d = (q[0] - nx, q[1] - ny), (q[0] + nx, q[1] + ny)
        self.tri(a, b, c, col)
        self.tri(a, c, d, col)

    def polyline(self, pts, w, col):
        for p, q in zip(pts, pts[1:]):
            self.line(p, q, w, col)

    def bevel(self, x, y, w, h, b, face, hi=None, sh=None):
        hi = hi or shade(face, 1.35)
        sh = sh or shade(face, 0.55)
        self.rect(x, y, w, h, sh)
        self.rect(x, y + b, w - b, h - b, hi)
        self.rect(x + b, y + b, w - 2 * b, h - 2 * b, face)

    def text(self, s, x, y, size, col=C_TEXT, align='center'):
        self.t.append((s, x, y, size, col, align))

    def flush(self):
        if self.v:
            shader = _get_shader()
            batch = batch_for_shader(shader, 'TRIS', {"pos": self.v, "color": self.c})
            gpu.state.blend_set('ALPHA')
            batch.draw(shader)
            gpu.state.blend_set('NONE')
        for s, x, y, size, col, align in self.t:
            _font_size(size)
            w, _ = blf.dimensions(0, s)
            _, h0 = blf.dimensions(0, "0")
            if align == 'center':
                x -= w / 2
            elif align == 'right':
                x -= w
            blf.color(0, *col)
            blf.position(0, x, y - h0 / 2, 0)
            blf.draw(0, s)


def cell_at(x, y, left, top, cs, cols, rows):
    """(col, row) of the cell under (x, y) - row 0 is the TOP row - or None."""
    dx, dy = x - left, top - y
    if dx < 0 or dy < 0:
        return None
    c, r = int(dx // cs), int(dy // cs)
    if c >= cols or r >= rows:
        return None
    return c, r


# ----------------------------------------------------------------------------
# BaseGame
# ----------------------------------------------------------------------------

class BaseGame:
    keys = ()          # event.type strings this game wants (e.g. 'SPACE', 'A')

    def __init__(self):
        self.u = 1.0
        self.panel = (0, 0, 0, 0)
        self.setup()
        self.reset()

    # ---- hooks to override
    def setup(self):
        """Called once: put persistent state here (scores, settings)."""

    def reset(self):
        """Start a new round."""

    def layout(self, view):
        """Compute geometry for the given View (called before draw/events)."""

    def draw(self, c):
        """Draw using the Canvas c."""

    def click(self, x, y, button):
        """Mouse press inside the panel. button: 'LEFT' or 'RIGHT'."""

    def move(self, x, y):
        """Mouse moved. Return True if a redraw is needed."""
        return False

    def key(self, key, repeat):
        """A key from self.keys was pressed."""

    def update(self, dt):
        """Timer tick (~30 Hz). Return True if a redraw is needed."""
        return False

    # ---- helpers
    def hit(self, x, y):
        px, py, pw, ph = self.panel
        return px <= x <= px + pw and py <= y <= py + ph

    def fit_cell(self, view, cols, rows, max_cell=40, header=50, footer=26,
                 pad=12, min_cell=10, extra_w=0, extra_h=0):
        u = view.u
        cw = (view.w - (2 * pad + extra_w) * u) / cols
        ch = (view.h - (3 * pad + header + footer + extra_h) * u) / rows
        return max(int(min_cell * u), int(min(max_cell * u, cw, ch)))

    def place(self, view, cw, ch, header=50, footer=26, pad=12, min_w=440):
        u = view.u
        pad, hh, fh = pad * u, header * u, footer * u
        pw = max(cw + 2 * pad, min_w * u)
        ph = ch + hh + fh + 3 * pad
        px = view.x0 + (view.w - pw) / 2
        py = view.y0 + (view.h - ph) / 2
        self.u = u
        self.panel = (px, py, pw, ph)
        self.left = px + (pw - cw) / 2
        self.top = py + ph - 2 * pad - hh
        self.hy = py + ph - pad - hh / 2
        self.fy = py + fh / 2

    def draw_frame(self, c):
        px, py, pw, ph = self.panel
        u = self.u
        c.rect(px - 2 * u, py - 2 * u, pw + 4 * u, ph + 4 * u, C_BORDER)
        c.rect(px, py, pw, ph, C_PANEL)

    def draw_header(self, c, main, sub=None, col=C_WHITE):
        px, _, pw, _ = self.panel
        u = self.u
        if sub:
            c.text(main, px + pw / 2, self.hy + 10 * u, 19 * u, col)
            c.text(sub, px + pw / 2, self.hy - 13 * u, 12 * u, C_TEXT)
        else:
            c.text(main, px + pw / 2, self.hy, 19 * u, col)

    def draw_hint(self, c, text):
        px, _, pw, _ = self.panel
        c.text(text, px + pw / 2, self.fy, 11 * self.u, C_TEXT)

    def banner(self, c, x, y, w, h, text, sub=None):
        u = self.u
        c.rect(x, y, w, h, (0.0, 0.0, 0.0, 0.62))
        if sub:
            c.text(text, x + w / 2, y + h / 2 + 12 * u, 24 * u, C_WHITE)
            c.text(sub, x + w / 2, y + h / 2 - 16 * u, 13 * u, C_TEXT)
        else:
            c.text(text, x + w / 2, y + h / 2, 24 * u, C_WHITE)


# ----------------------------------------------------------------------------
# Runner + operator factory
# ----------------------------------------------------------------------------

_runners = []
_current = None     # the Runner currently running (one overlay game at a time)
_refs = 0


def _redraw_all():
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == 'VIEW_3D':
                area.tag_redraw()


def _draw_cb(runner):
    ctx = bpy.context
    area = ctx.area
    if not runner.running or area is None or area.as_pointer() != runner.area_ptr:
        return
    try:
        view = compute_view(area, ctx.region)
        g = runner.game
        g.layout(view)
        c = Canvas()
        g.draw(c)
        c.flush()
    except Exception:
        traceback.print_exc()
        runner.stop = True


class Runner:
    def __init__(self, gid, name, game_class, help_lines=()):
        self.gid, self.name, self.game_class = gid, name, game_class
        self.help_lines = list(help_lines)
        self.idname = "minigames.%s_play" % gid
        self.op = None
        self.running = False
        self.stop = False
        self.gen = 0
        self.game = None
        self.area = None
        self.area_ptr = 0
        self.handle = None
        self.last = 0.0

    # -- registration
    def register(self):
        self.op = _make_operator(self)
        bpy.utils.register_class(self.op)
        _runners.append(self)
        _acquire()

    def unregister(self):
        self.shutdown()
        bpy.utils.unregister_class(self.op)
        if self in _runners:
            _runners.remove(self)
        _release()

    # -- lifecycle
    def shutdown(self):
        if self.running or self.handle is not None:
            self.stop = True
            self.gen += 1
            self._teardown()

    def _teardown(self):
        global _current
        if self.handle is not None:
            try:
                bpy.types.SpaceView3D.draw_handler_remove(self.handle, 'WINDOW')
            except Exception:
                pass
            self.handle = None
        self.running = False
        self.area = None
        if _current is self:
            _current = None
        try:
            _redraw_all()
        except Exception:
            pass

    # -- sidebar UI
    def draw_ui(self, layout):
        if self.running:
            layout.operator(self.idname, text="Quit Game", icon='X')
        else:
            layout.operator(self.idname, text="Play %s" % self.name, icon='PLAY')
        if self.help_lines:
            col = layout.box().column(align=True)
            col.scale_y = 0.8
            for line in self.help_lines:
                col.label(text=line)


def _make_operator(runner):
    def _remove_timer(self, context):
        t = getattr(self, "_timer", None)
        if t is not None:
            try:
                context.window_manager.event_timer_remove(t)
            except Exception:
                pass
            self._timer = None

    def _finish(self, context):
        self._remove_timer(context)
        runner._teardown()
        return {'FINISHED'}

    def invoke(self, context, event):
        global _current
        if runner.running:                       # toggle
            runner.stop = True
            return {'FINISHED'}
        if _current is not None and _current.running:
            self.report({'WARNING'}, "Quit %s first" % _current.name)
            return {'CANCELLED'}
        area = context.area
        if area is None or area.type != 'VIEW_3D':
            self.report({'WARNING'}, "Run this from a 3D Viewport")
            return {'CANCELLED'}

        runner.gen += 1
        self.gen = runner.gen
        runner.running, runner.stop = True, False
        runner.area, runner.area_ptr = area, area.as_pointer()
        runner.game = runner.game_class()
        runner.last = time.time()
        _current = runner
        runner.handle = bpy.types.SpaceView3D.draw_handler_add(
            _draw_cb, (runner,), 'WINDOW', 'POST_PIXEL')
        wm = context.window_manager
        self._timer = wm.event_timer_add(1 / 30, window=context.window)
        wm.modal_handler_add(self)
        _redraw_all()
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        r = runner
        if self.gen != r.gen:                    # stale (file load / reload)
            self._remove_timer(context)
            return {'CANCELLED'}
        if r.stop:
            return self._finish(context)
        try:
            area = r.area
            win = next(x for x in area.regions if x.type == 'WINDOW')
            ax, ay, aw, ah = area.x, area.y, area.width, area.height
        except (ReferenceError, StopIteration, AttributeError):
            return self._finish(context)

        g = r.game
        try:
            if event.type == 'TIMER':
                now = time.time()
                dt = min(0.25, now - r.last)
                r.last = now
                if g.update(dt):
                    area.tag_redraw()
                return {'PASS_THROUGH'}

            inside = ax <= event.mouse_x < ax + aw and ay <= event.mouse_y < ay + ah
            if not inside:
                if g.move(-1e6, -1e6):
                    area.tag_redraw()
                return {'PASS_THROUGH'}

            g.layout(compute_view(area, win))
            mx, my = event.mouse_x - win.x, event.mouse_y - win.y

            if event.value == 'PRESS' and not (event.ctrl or event.alt or event.oskey):
                if event.type == 'ESC':
                    return self._finish(context)
                if event.type == 'R':
                    g.reset()
                    area.tag_redraw()
                    return {'RUNNING_MODAL'}
                if event.type in g.keys:
                    g.key(event.type, bool(getattr(event, "is_repeat", False)))
                    area.tag_redraw()
                    return {'RUNNING_MODAL'}

            if event.type in {'MOUSEMOVE', 'INBETWEEN_MOUSEMOVE'}:
                if g.move(mx, my):
                    area.tag_redraw()
                return {'PASS_THROUGH'}

            if event.type in {'LEFTMOUSE', 'RIGHTMOUSE'} and g.hit(mx, my):
                if event.value in {'PRESS', 'DOUBLE_CLICK'}:
                    g.click(mx, my, 'LEFT' if event.type == 'LEFTMOUSE' else 'RIGHT')
                    area.tag_redraw()
                return {'RUNNING_MODAL'}
        except Exception:
            traceback.print_exc()
            return self._finish(context)
        return {'PASS_THROUGH'}

    return type("MINIGAMES_OT_%s_play" % runner.gid, (bpy.types.Operator,), {
        "bl_idname": runner.idname,
        "bl_label": "Play %s" % runner.name,
        "bl_description": "Play %s in the 3D Viewport (run again to quit)" % runner.name,
        "invoke": invoke,
        "modal": modal,
        "_remove_timer": _remove_timer,
        "_finish": _finish,
    })


@persistent
def _on_load_pre(*args):
    for r in list(_runners):
        r.shutdown()


def _acquire():
    global _refs
    if _refs == 0:
        bpy.app.handlers.load_pre.append(_on_load_pre)
    _refs += 1


def _release():
    global _refs
    _refs -= 1
    if _refs <= 0:
        _refs = 0
        if _on_load_pre in bpy.app.handlers.load_pre:
            bpy.app.handlers.load_pre.remove(_on_load_pre)
