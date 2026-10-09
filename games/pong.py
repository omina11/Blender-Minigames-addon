"""Pong - you vs the CPU, or two players (drawn as a GPU overlay)."""

import math
import random

from . import _overlay as ov
from . import _record as _rec

GAME_NAME = "Pong"
GAME_ICON = 'MESH_UVSPHERE'

# Logical field (y points up). Everything is scaled to the panel when drawing.
W, H = 600.0, 400.0
PW, PH = 10.0, 70.0      # paddle width / height
MARGIN = 24.0            # paddle distance from the wall
R = 7.0                  # ball radius
BASE_SPEED = 300.0
MAX_SPEED = 720.0
PADDLE_SPEED = 620.0
NUDGE = 50.0             # paddle move per key press
MAX_ANGLE = math.radians(55)
WIN = 7

C_P1 = (0.45, 0.70, 1.00, 1.0)
C_P2 = (1.00, 0.62, 0.30, 1.0)
C_FIELD = (0.06, 0.07, 0.09, 1.0)
C_NET = (0.60, 0.64, 0.72, 0.35)

LX = MARGIN              # left paddle x (its left edge)
RX = W - MARGIN - PW     # right paddle x


class Pong(ov.BaseGame):
    keys = ('W', 'S', 'UP_ARROW', 'DOWN_ARROW', 'SPACE', 'M', 'D')

    def setup(self):
        self.mode = 'CPU'  # CPU | PVP
        self.hard = True
        try:
            self.record_mgr = _rec.RecordManager(game_name="pong")
        except Exception:
            self.record_mgr = None

    def reset(self):
        self.score = [0, 0]
        self.state = 'ready'  # ready | serve | play | over
        self.p = [H / 2, H / 2]    # paddle centres
        self.tgt = [H / 2, H / 2]  # where the paddles are heading
        self.ball = [W / 2, H / 2]
        self.vel = [0.0, 0.0]
        self.speed = BASE_SPEED
        self.timer = 0.0
        self.winner = 0
        self.next_dir = random.choice((-1, 1))
        self.cpu_err = 0.0
        self.sc = 1.0
        self.fy0 = 0.0

    # ---- records
    def _record_match(self):
        if not self.record_mgr:
            return
        margin = abs(self.score[0] - self.score[1])
        if self.mode == 'CPU':
            if self.winner == 0:
                self.record_mgr.add_win_record(score=margin)
            else:
                self.record_mgr.add_lose_record()
        else:
            # PVP: ogni vittoria conta come "vittoria"
            self.record_mgr.add_win_record(score=margin)

    # ---- logic
    def _clamp(self, v):
        return max(PH / 2, min(H - PH / 2, v))

    def _new_cpu_err(self):
        self.cpu_err = random.uniform(-1, 1) * \
            PH * (0.35 if self.hard else 0.65)

    def _serve(self):
        a = math.radians(random.uniform(-25, 25))
        self.vel = [self.next_dir * self.speed *
                    math.cos(a), self.speed * math.sin(a)]
        self.state = 'play'
        self._new_cpu_err()

    def _point(self, scorer):
        self.score[scorer] += 1
        self.ball = [W / 2, H / 2]
        self.vel = [0.0, 0.0]
        self.speed = BASE_SPEED
        if self.score[scorer] >= WIN:
            self.state, self.winner = 'over', scorer
            self._record_match()
        else:
            self.state, self.timer = 'serve', 0.9
            self.next_dir = -1 if scorer == 1 else 1  # towards the one who lost

    def _bounce(self, side, offset):
        """Hit a paddle: new direction depends on where the ball touched it."""
        self.speed = min(MAX_SPEED, self.speed * 1.06)
        a = max(-1.0, min(1.0, offset)) * MAX_ANGLE
        d = 1 if side == 0 else -1
        self.vel = [d * self.speed * math.cos(a), self.speed * math.sin(a)]
        if side == 0:
            self._new_cpu_err()

    def _step_ball(self, dt):
        x, y = self.ball
        vx, vy = self.vel
        n = max(1, int(self.speed * dt / 4.0) + 1)  # sub-steps: no tunnelling
        h = dt / n
        for _ in range(n):
            x += self.vel[0] * h
            y += self.vel[1] * h
            if y < R:
                y, self.vel[1] = R, abs(self.vel[1])
            elif y > H - R:
                y, self.vel[1] = H - R, -abs(self.vel[1])
            reach = PH / 2 + R * 0.8
            if self.vel[0] < 0 and x - R <= LX + PW and x + R >= LX and abs(y - self.p[0]) <= reach:
                x = LX + PW + R
                self._bounce(0, (y - self.p[0]) / reach)
            elif self.vel[0] > 0 and x + R >= RX and x - R <= RX + PW and abs(y - self.p[1]) <= reach:
                x = RX - R
                self._bounce(1, (y - self.p[1]) / reach)
            if x < -2 * R:
                self.ball = [x, y]
                self._point(1)
                return
            if x > W + 2 * R:
                self.ball = [x, y]
                self._point(0)
                return
        self.ball = [x, y]

    def _start(self):
        if self.state == 'ready':
            self._serve()
        elif self.state == 'over':
            self.reset()

    def update(self, dt):
        dt = min(dt, 0.05)
        # CPU paddle
        if self.mode == 'CPU':
            if self.state == 'play' and self.vel[0] > 0:
                self.tgt[1] = self.ball[1] + self.cpu_err
            else:
                self.tgt[1] = H / 2
        moved = False
        for k in (0, 1):
            speed = PADDLE_SPEED
            if k == 1 and self.mode == 'CPU':
                speed = 340.0 if self.hard else 210.0
            t = self._clamp(self.tgt[k])
            diff = t - self.p[k]
            step = speed * dt
            new = t if abs(
                diff) <= step else self.p[k] + math.copysign(step, diff)
            if new != self.p[k]:
                self.p[k] = new
                moved = True
        if self.state == 'serve':
            self.timer -= dt
            if self.timer <= 0:
                self._serve()
            return True
        if self.state == 'play':
            self._step_ball(dt)
            return True
        return moved

    # ---- input
    def layout(self, view):
        cs = self.fit_cell(view, 3, 2, max_cell=200, min_cell=40)
        self.place(view, 3 * cs, 2 * cs)
        self.sc = 3 * cs / W
        self.fy0 = self.top - 2 * cs

    def move(self, x, y):
        if self.mode == 'CPU' and self.hit(x, y):
            self.tgt[0] = self._clamp((y - self.fy0) / self.sc)
        return False

    def click(self, x, y, button):
        if button == 'LEFT':
            self._start()

    def key(self, key, repeat):
        if key == 'M':
            self.mode = 'PVP' if self.mode == 'CPU' else 'CPU'
            self.reset()
        elif key == 'D':
            self.hard = not self.hard
        elif key == 'SPACE':
            self._start()
        else:
            d = 1 if key in ('W', 'UP_ARROW') else -1
            who = 0 if (key in ('W', 'S') or self.mode == 'CPU') else 1
            self.tgt[who] = self._clamp(self.tgt[who] + d * NUDGE)

    # ---- drawing
    def draw(self, c):
        sc, left, fy0 = self.sc, self.left, self.fy0
        cpu = self.mode == 'CPU'

        def X(v):
            return left + v * sc

        def Y(v):
            return fy0 + v * sc

        self.draw_frame(c)

        sub = "%s | first to %d" % (
            ("vs CPU (%s)" % ("hard" if self.hard else "easy")) if cpu else "2 players", WIN)
        if self.record_mgr:
            r = self.record_mgr.get_records()
            sub += " | Totale W %d L %d" % (r['wins'], r['losses'])
        if self.state == 'over':
            if cpu:
                main, col = ("You win!", C_P1) if self.winner == 0 else (
                    "CPU wins", C_P2)
            else:
                main, col = "Player %d wins!" % (
                    self.winner + 1), (C_P1 if self.winner == 0 else C_P2)
        else:
            main, col = "Pong", ov.C_WHITE
        self.draw_header(c, main, sub, col)

        c.rect(left, fy0, W * sc, H * sc, C_FIELD)
        for y in range(0, int(H), 28):  # centre line
            c.rect(X(W / 2) - 1.5 * sc, Y(y + 7), 3 * sc, 14 * sc, C_NET)

        c.text(str(self.score[0]), X(W / 2 - 70),
               Y(H - 55), 56 * sc, alpha_col(C_P1))
        c.text(str(self.score[1]), X(W / 2 + 70),
               Y(H - 55), 56 * sc, alpha_col(C_P2))

        c.rect(X(LX), Y(self.p[0] - PH / 2), PW * sc, PH * sc, C_P1)
        c.rect(X(RX), Y(self.p[1] - PH / 2), PW * sc, PH * sc, C_P2)
        c.circle(X(self.ball[0]), Y(self.ball[1]), R * sc, ov.C_WHITE, 16)

        bw, bh = W * sc, H * sc
        if self.state == 'ready':
            self.banner(c, left, fy0, bw, bh, "Ready?",
                        "Click or press SPACE to serve")
        elif self.state == 'over':
            self.banner(c, left, fy0, bw, bh, main,
                        "Click or press R for a new match")

        if cpu:
            self.draw_hint(
                c, "Mouse / W S: move   M mode   D level   R restart   ESC quit")
        else:
            self.draw_hint(
                c, "P1: W S   P2: Up Down   M mode   R restart   ESC quit")


def alpha_col(col):
    return ov.alpha(col, 0.55)


RUNNER = ov.Runner("pong", GAME_NAME, Pong, [
    "Mouse or W/S: move your paddle",
    "2 players: W/S and Up/Down",
    "SPACE / LMB: serve",
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
