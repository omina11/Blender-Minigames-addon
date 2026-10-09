"""Dice - the Kingdom Come: Deliverance style dice game (Farkle), as a GPU overlay.

Roll six dice, set aside the ones that score, then either roll the rest again
or bank your turn points. If a roll has no scoring dice you bust and lose the
points of the turn. First to the target score wins.

Scoring
    single 1 = 100, single 5 = 50
    three of a kind: 1s = 1000, 2s = 200, 3s = 300, 4s = 400, 5s = 500, 6s = 600
        every extra die of the same face doubles the value (4 of a kind x2, ...)
    straights: 1-2-3-4-5 = 500, 2-3-4-5-6 = 750, 1-2-3-4-5-6 = 1500
    all dice used ("hot dice"): you may roll all six again

Best score: for every CPU level and goal the biggest winning margin is saved
(dice_<level>_<goal>.json).
"""

import math
import random
import traceback
from collections import Counter

from . import _overlay as ov
from . import _record as _rec

GAME_NAME = "Dice"
GAME_ICON = 'MESH_CUBE'

TARGETS = (1000, 1500, 2000, 3000)
LEVELS = ("Easy", "Medium", "Hard")
ROLL_T = 0.60

TRIPLE = {1: 1000, 2: 200, 3: 300, 4: 400, 5: 500, 6: 600}
STRAIGHTS = ((1, 6, 1500), (1, 5, 500), (2, 6, 750))     # (low, high, points)

# chance that a roll of r dice has no scoring dice (computed exactly in the tests)
P_BUST = {1: 0.6667, 2: 0.4444, 3: 0.2778, 4: 0.1574, 5: 0.0772, 6: 0.0309}
AVG_GAIN = {6: 420, 5: 280, 4: 200, 3: 140, 2: 95, 1: 75}   # rough extra points when a roll succeeds
BANK_AT = {1: 300, 2: 350, 3: 450, 4: 700, 5: 1500, 6: 10 ** 9}

C_DIE = (0.96, 0.93, 0.85, 1.0)
C_DIE_DEAD = (0.62, 0.60, 0.56, 1.0)
C_DIE_HELD = (0.74, 0.88, 0.78, 1.0)
C_PIP = (0.12, 0.10, 0.09, 1.0)
C_EDGE = (0.30, 0.26, 0.20, 1.0)
C_SLOT = (0.16, 0.18, 0.22, 1.0)
C_FELT = (0.11, 0.24, 0.18, 1.0)
C_CARD = (0.18, 0.20, 0.25, 1.0)
C_BTN_OK = (0.30, 0.72, 0.42, 1.0)
C_BTN_BANK = (0.88, 0.70, 0.22, 1.0)
C_WOOD = (0.30, 0.18, 0.10, 1.0)
C_WOOD_HI = (0.46, 0.29, 0.16, 1.0)

DIGITS = {}
for _i, _n in enumerate(('ONE', 'TWO', 'THREE', 'FOUR', 'FIVE', 'SIX')):
    DIGITS[_n] = _i
    DIGITS['NUMPAD_%d' % (_i + 1)] = _i

PIPS = {
    1: [(0, 0)],
    2: [(-1, 1), (1, -1)],
    3: [(-1, 1), (0, 0), (1, -1)],
    4: [(-1, 1), (1, 1), (-1, -1), (1, -1)],
    5: [(-1, 1), (1, 1), (0, 0), (-1, -1), (1, -1)],
    6: [(-1, 1), (1, 1), (-1, 0), (1, 0), (-1, -1), (1, -1)],
}


# ----------------------------------------------------------------------------
# Scoring rules (pure Python)
# ----------------------------------------------------------------------------

def _face_points(face, n_new, n_prev):
    """(points, size_of_triple_group) for n_new dice of a face, or None if they don't score.

    n_prev > 0 means a triple (or bigger) of that face was already set aside earlier in
    the turn: extra dice of the same face may join it, doubling its value each time.
    """
    if n_prev >= 3:
        total = n_prev + n_new
        return TRIPLE[face] * 2 ** (total - 3) - TRIPLE[face] * 2 ** (n_prev - 3), total
    if n_new >= 3:
        return TRIPLE[face] * 2 ** (n_new - 3), n_new
    if n_new == 0:
        return 0, 0
    if face == 1:
        return 100 * n_new, 0
    if face == 5:
        return 50 * n_new, 0
    return None


def _score_counts(counts, carry):
    pts, new_carry = 0, dict(carry)
    for face in range(1, 7):
        r = _face_points(face, counts.get(face, 0), carry.get(face, 0))
        if r is None:
            return None
        pts += r[0]
        if r[1] >= 3:
            new_carry[face] = r[1]
    return pts, new_carry


def score_selection(values, carry=None):
    """Points for the chosen dice, or None if any of them doesn't score.

    Returns (points, new_carry) where carry maps face -> size of the triple group
    already set aside this turn.
    """
    carry = carry or {}
    counts = Counter(values)
    best = _score_counts(counts, carry)
    for lo, hi, pts in STRAIGHTS:
        faces = range(lo, hi + 1)
        if all(counts.get(f, 0) >= 1 for f in faces):
            rest = counts.copy()
            for f in faces:
                rest[f] -= 1
            r = _score_counts(rest, carry)
            if r is not None and (best is None or r[0] + pts > best[0]):
                best = (r[0] + pts, r[1])
    return best


def valid_subsets(values, carry=None):
    """Every non-empty subset of dice that scores: [(indices, points, new_carry)]."""
    n = len(values)
    out = []
    for mask in range(1, 1 << n):
        idx = tuple(i for i in range(n) if mask >> i & 1)
        r = score_selection([values[i] for i in idx], carry)
        if r is not None:
            out.append((idx, r[0], r[1]))
    return out


def has_scoring(values, carry=None):
    carry = carry or {}
    counts = Counter(values)
    if counts.get(1) or counts.get(5):
        return True
    return any(n >= 3 for n in counts.values()) or any(counts.get(f, 0) for f in carry)


def _rounded(x, y, w, h, r, seg=4):
    pts = []
    for cx, cy, a0 in ((x + w - r, y + h - r, 0), (x + r, y + h - r, 90),
                       (x + r, y + r, 180), (x + w - r, y + r, 270)):
        for k in range(seg + 1):
            a = math.radians(a0 + 90.0 * k / seg)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


# ----------------------------------------------------------------------------
# Game
# ----------------------------------------------------------------------------

class Dice(ov.BaseGame):
    keys = tuple(DIGITS) + ('SPACE', 'RET', 'NUMPAD_ENTER', 'B', 'S', 'D', 'T', 'M')

    def setup(self):
        self.mode = 'CPU'            # CPU | PVP
        self.level = 1               # CPU strength (index in LEVELS)
        self.target_i = 2
        self.wins = [0, 0]
        self.starter = 0
        self.record_mgrs = {}        # (level, target index) -> RecordManager
        self.best_cache = {}         # (level, target index) -> best winning margin

    # ---- records (best winning margin per CPU level and goal)
    def _mgr(self, key):
        if key not in self.record_mgrs:
            try:
                self.record_mgrs[key] = _rec.RecordManager(
                    game_name="dice_%s_%d" % (LEVELS[key[0]].lower(), TARGETS[key[1]]))
            except Exception:
                self.record_mgrs[key] = None
        return self.record_mgrs[key]

    def _best_score(self, key=None):
        key = key or (self.level, self.target_i)
        if key not in self.best_cache:
            m = self._mgr(key)
            try:
                self.best_cache[key] = int(m.get_records().get("highest_score", 0) or 0) if m else 0
            except Exception:
                self.best_cache[key] = 0
        return self.best_cache[key]

    def _record_end(self):
        """vs CPU only: a win saves the winning margin, a loss counts as a defeat."""
        if self.mode != 'CPU':
            return
        key = (self.level, self.target_i)
        m = self._mgr(key)
        self.margin = self.scores[0] - self.scores[1]
        if not m:
            return
        try:
            if self.winner == 0:
                self.new_best = self.margin > self._best_score(key)
                m.add_win_record(score=max(1, self.margin))
                self.best_cache.pop(key, None)
            else:
                m.add_lose_record()
        except Exception:
            traceback.print_exc()

    def reset(self):
        self.scores = [0, 0]
        self.turn = self.starter
        self.winner = -1
        self.margin = 0
        self.new_best = False
        self.cs = 50
        self.bw = self.bh = 0.0
        self.hover = None
        self.anim_t = 0.0
        self._begin_turn()

    # ---- turn flow
    def _cpu_turn(self):
        return self.mode == 'CPU' and self.turn == 1

    def _begin_turn(self):
        self.turn_score = 0
        self.held = []
        self.carry = {}
        self.n_free = 6
        self.roll = []
        self.sel = set()
        self.scorable = set()
        self.note = ''
        self.lost = 0
        self.cpu_plan = None
        self.timer = 0.8
        self.state = 'cpu_wait' if self._cpu_turn() else 'idle'

    def _next_player(self):
        self.turn = 1 - self.turn
        self._begin_turn()

    def _roll(self):
        self.roll = [random.randint(1, 6) for _ in range(self.n_free)]
        self.sel = set()
        self.scorable = set()
        self.anim_t = 0.0
        self.state = 'rolling'

    def _after_roll(self):
        subs = valid_subsets(self.roll, self.carry)
        if not subs:
            self.lost = self.turn_score
            self.state, self.timer = 'bust', 1.7
            return
        self.scorable = {i for idx, _, _ in subs for i in idx}
        if self._cpu_turn():
            self.state, self.timer = 'cpu_think', 0.9
        else:
            self.state = 'choose'

    def _sel_info(self):
        if not self.sel:
            return None
        return score_selection([self.roll[i] for i in sorted(self.sel)], self.carry)

    def _continue(self):
        info = self._sel_info()
        if info is None:
            return
        pts, carry = info
        vals = [self.roll[i] for i in sorted(self.sel)]
        self.held += vals
        self.turn_score += pts
        self.carry = carry
        self.n_free -= len(vals)
        hot = self.n_free == 0
        if hot:
            self.held, self.carry, self.n_free = [], {}, 6
        self._roll()
        self.note = "Hot dice! All six are rolled again." if hot else ''

    def _bank(self):
        info = self._sel_info()
        if info is None:
            return
        self.scores[self.turn] += self.turn_score + info[0]
        if self.scores[self.turn] >= TARGETS[self.target_i]:
            self.winner = self.turn
            self.state = 'over'
            self.wins[self.turn] += 1
            self.starter = 1 - self.starter
            self._record_end()
            return
        self._next_player()

    # ---- CPU brain
    def ai_decide(self):
        """Pick which dice to keep and whether to bank. Returns (indices, 'bank' | 'roll')."""
        subs = valid_subsets(self.roll, self.carry)
        me, opp = self.scores[self.turn], self.scores[1 - self.turn]
        goal = TARGETS[self.target_i]
        best = None
        for idx, pts, _ in subs:
            rem = self.n_free - len(idx)
            if rem == 0:
                rem = 6
            total = self.turn_score + pts
            if me + total >= goal:
                value, action = 10 ** 9, 'bank'
            elif self.level == 0:
                value = pts + 10 * len(idx)               # keeps every scoring die
                reckless = rem <= 2 and total < 600 and random.random() < 0.4
                action = 'bank' if (total >= 300 and not reckless) else 'roll'
            elif self.level == 1:
                value = pts + 35 * rem
                action = 'bank' if total >= BANK_AT[rem] else 'roll'
            else:
                ev_roll = (1 - P_BUST[rem]) * (total + AVG_GAIN[rem])
                ev_bank = total * (0.5 if (opp >= goal - 500 and me + total < goal) else 1.0)
                value, action = (ev_roll, 'roll') if ev_roll > ev_bank else (ev_bank, 'bank')
            if best is None or value > best[0]:
                best = (value, idx, action)
        return best[1], best[2]

    # ---- update
    def update(self, dt):
        if self.state == 'rolling':
            self.anim_t += dt
            if self.anim_t >= ROLL_T:
                self._after_roll()
            return True
        if self.state in ('bust', 'cpu_wait', 'cpu_think', 'cpu_show'):
            self.timer -= dt
            if self.timer <= 0:
                if self.state == 'bust':
                    self._next_player()
                elif self.state == 'cpu_wait':
                    self._roll()
                elif self.state == 'cpu_think':
                    idx, act = self.ai_decide()
                    self.sel, self.cpu_plan = set(idx), act
                    self.state, self.timer = 'cpu_show', 1.0
                else:
                    if self.cpu_plan == 'bank':
                        self._bank()
                    else:
                        self._continue()
            return True
        return False

    # ---- input
    def _human_can_act(self):
        return self.state in ('idle', 'choose')

    def _primary(self):
        if self.state == 'idle':
            self._roll()
        elif self.state == 'choose':
            self._continue()

    def _toggle(self, i):
        if self.state == 'choose' and 0 <= i < len(self.roll):
            self.sel.symmetric_difference_update({i})
            self.note = ''

    def key(self, key, repeat):
        if self.state == 'over' and key in ('SPACE', 'RET', 'NUMPAD_ENTER'):
            self.reset()
            return
        if key in DIGITS:
            self._toggle(DIGITS[key])
        elif key in ('SPACE', 'RET', 'NUMPAD_ENTER'):
            self._primary()
        elif key == 'B':
            if self.state == 'choose':
                self._bank()
        elif key == 'S':
            if self.state == 'choose':
                subs = valid_subsets(self.roll, self.carry)
                if subs:
                    self.sel = set(max(subs, key=lambda s: (s[1], -len(s[0])))[0])
        elif key == 'D':
            self.level = (self.level + 1) % len(LEVELS)
            self.reset()
        elif key == 'T':
            self.target_i = (self.target_i + 1) % len(TARGETS)
            self.reset()
        elif key == 'M':
            self.mode = 'PVP' if self.mode == 'CPU' else 'CPU'
            self.wins = [0, 0]
            self.reset()

    def layout(self, view):
        self.cs = self.fit_cell(view, 8, 6.2, max_cell=74, min_cell=26)
        self.bw, self.bh = 8 * self.cs, 6.2 * self.cs
        self.place(view, self.bw, self.bh)

    def _die_center(self, i, n):
        cs = self.cs
        return self.left + self.bw / 2 + (i - (n - 1) / 2) * 1.2 * cs, self.top - 2.85 * cs

    def _buttons(self):
        cs = self.cs
        y = self.top - 5.9 * cs
        x0 = self.left + self.bw / 2
        return {'primary': (x0 - 3.2 * cs, y, 3.0 * cs, 0.9 * cs),
                'bank': (x0 + 0.2 * cs, y, 3.0 * cs, 0.9 * cs)}

    def _target_at(self, x, y):
        for name, (bx, by, bw, bh) in self._buttons().items():
            if bx <= x <= bx + bw and by <= y <= by + bh:
                return (name, 0)
        if self.state == 'choose':
            n = len(self.roll)
            for i in range(n):
                cx, cy = self._die_center(i, n)
                if abs(x - cx) <= 0.55 * self.cs and abs(y - cy) <= 0.6 * self.cs:
                    return ('die', i)
        return None

    def move(self, x, y):
        h = self._target_at(x, y)
        changed = h != self.hover
        self.hover = h
        return changed

    def click(self, x, y, button):
        if self.state == 'over':
            self.reset()
            return
        if button != 'LEFT':
            return
        t = self._target_at(x, y)
        if t is None:
            return
        kind, i = t
        if kind == 'die':
            self._toggle(i)
        elif kind == 'primary':
            self._primary()
        elif kind == 'bank' and self.state == 'choose':
            self._bank()

    # ---- drawing helpers
    def _rr(self, c, x, y, w, h, r, col, seg=6):
        c.poly(_rounded(x, y, w, h, r, seg), col)

    def _die(self, c, cx, cy, size, face, body, lift=0.0):
        r = size * 0.20
        base_y = cy
        cy += lift
        # soft shadow on the felt (grows and fades while the die is lifted)
        sh = 0.05 + 0.5 * lift / max(size, 1.0)
        self._rr(c, cx - size / 2 + size * 0.06, base_y - size / 2 - size * 0.07 - lift * 0.4,
                 size, size, r, (0.0, 0.0, 0.0, max(0.12, 0.34 - sh)))
        # dark rim, body, lit face
        self._rr(c, cx - size / 2 - 1.5, cy - size / 2 - 1.5, size + 3, size + 3, r + 1.5, C_EDGE)
        self._rr(c, cx - size / 2, cy - size / 2, size, size, r, ov.shade(body, 0.86))
        inset = size * 0.045
        self._rr(c, cx - size / 2 + inset, cy - size / 2 + inset * 1.8,
                 size - 2 * inset, size - 2.8 * inset, r * 0.9, body)
        # bevel: bright top edge, shaded bottom edge
        self._rr(c, cx - size * 0.36, cy + size * 0.33, size * 0.72, size * 0.09, size * 0.04,
                 (1.0, 1.0, 1.0, 0.45), 3)
        self._rr(c, cx - size * 0.36, cy - size * 0.42, size * 0.72, size * 0.06, size * 0.03,
                 (0.0, 0.0, 0.0, 0.10), 3)
        # engraved pips: dark hole with a lit lower rim
        pr = size * 0.09
        for px, py in PIPS[face]:
            x, y = cx + px * size * 0.25, cy + py * size * 0.25
            c.circle(x, y - pr * 0.18, pr * 1.18, (1.0, 1.0, 1.0, 0.35), 14)
            c.circle(x, y, pr, C_PIP, 14)
            c.circle(x - pr * 0.25, y + pr * 0.28, pr * 0.32, (0.45, 0.42, 0.40, 1.0), 8)

    def _table(self, c):
        cs, L, T, bw, bh = self.cs, self.left, self.top, self.bw, self.bh
        rim = 0.16 * cs
        c.rect(L, T - bh, bw, bh, C_WOOD)
        c.rect(L, T - bh + bh - 0.05 * cs, bw, 0.05 * cs, C_WOOD_HI)            # lit top edge of the rim
        c.rect(L + rim, T - bh + rim, bw - 2 * rim, bh - 2 * rim, ov.shade(C_FELT, 0.75))
        c.rect(L + rim + 0.05 * cs, T - bh + rim + 0.05 * cs, bw - 2 * rim - 0.1 * cs,
               bh - 2 * rim - 0.1 * cs, C_FELT)
        cx, cy = L + bw / 2, T - bh * 0.46                                       # table light
        for k in range(6):
            c.circle(cx, cy, cs * (4.6 - 0.65 * k), (1.0, 1.0, 0.8, 0.018), 40)
        c.rect(L + rim + 0.05 * cs, T - bh + rim + 0.05 * cs, bw - 2 * rim - 0.1 * cs, 0.05 * cs,
               (0.0, 0.0, 0.0, 0.18))

    def _names(self):
        return ("You", "CPU") if self.mode == 'CPU' else ("Player 1", "Player 2")

    def _status(self):
        names = self._names()
        who = names[self.turn]
        st = self.state
        if st == 'over':
            if self.mode == 'CPU':
                return ("You win!", ov.C_GOLD) if self.winner == 0 else ("CPU wins", ov.C_BAD)
            return "%s wins!" % names[self.winner], ov.C_GOLD
        if st == 'bust':
            return "Farkle! No scoring dice", ov.C_BAD
        if st == 'rolling':
            return "%s rolls..." % who, ov.C_WHITE
        if st == 'idle':
            return "%s: roll the dice" % ("Your turn" if self.mode == 'CPU' else who), ov.C_WHITE
        if st == 'choose':
            return "%s: pick the scoring dice" % ("Your turn" if self.mode == 'CPU' else who), ov.C_WHITE
        return "CPU is playing...", ov.C_WHITE

    # ---- drawing
    def draw(self, c):
        cs, u = self.cs, self.u
        L, T, bw = self.left, self.top, self.bw
        self.draw_frame(c)

        main, col = self._status()
        goal = TARGETS[self.target_i]
        if self.state == 'bust':
            sub = "You lose the %d points of this turn" % self.lost if self.lost else "Nothing to lose this time"
            if self._cpu_turn():
                sub = sub.replace("You lose", "CPU loses")
        elif self.note:
            sub = self.note
        elif self.state == 'choose':
            info = self._sel_info()
            if info:
                sub = "Selection: +%d   |   roll again or bank" % info[0]
            elif self.sel:
                sub = "Some of the selected dice don't score"
            else:
                sub = "Click dice (or 1-6) - only scoring dice can be kept"
        else:
            sub = "Goal %d   |   %s" % (goal, "vs CPU (%s)   |   Best margin %d" % (
                LEVELS[self.level], self._best_score()) if self.mode == 'CPU' else "2 players")
        self.draw_header(c, main, sub, col)

        # felt table
        self._table(c)

        # score cards
        names = self._names()
        for p in (0, 1):
            x = L + 0.15 * cs if p == 0 else L + bw - 3.15 * cs
            y = T - 1.15 * cs
            active = (self.turn == p and self.state != 'over') or (self.state == 'over' and self.winner == p)
            self._rr(c, x + 3, y - 4, 3.0 * cs, 1.0 * cs, 0.14 * cs, (0.0, 0.0, 0.0, 0.30))
            if active:
                self._rr(c, x - 3, y - 3, 3.0 * cs + 6, 1.0 * cs + 6, 0.16 * cs, ov.alpha(ov.C_GOLD, 0.35))
                self._rr(c, x - 2, y - 2, 3.0 * cs + 4, 1.0 * cs + 4, 0.15 * cs, ov.C_GOLD)
            self._rr(c, x, y, 3.0 * cs, 1.0 * cs, 0.13 * cs, C_CARD)
            self._rr(c, x + 0.08 * cs, y + 0.9 * cs, 2.84 * cs, 0.06 * cs, 0.03 * cs, (1.0, 1.0, 1.0, 0.10), 3)
            c.text(names[p], x + 1.5 * cs, y + 0.76 * cs, cs * 0.26, ov.C_TEXT)
            c.text(str(self.scores[p]), x + 1.5 * cs, y + 0.38 * cs, cs * 0.50, ov.C_WHITE)
        c.text("GOAL", L + bw / 2, T - 0.40 * cs, cs * 0.22, ov.C_TEXT)
        c.text(str(goal), L + bw / 2, T - 0.72 * cs, cs * 0.42, ov.C_GOLD)

        # turn info
        info = self._sel_info() if self.state == 'choose' else None
        txt = "Turn points: %d" % self.turn_score
        if info:
            txt += "  (+%d)" % info[0]
        c.text(txt, L + bw / 2, T - 1.62 * cs, cs * 0.32, ov.C_WHITE)

        # dice
        n = len(self.roll) if self.roll else self.n_free
        for i in range(n):
            cx, cy = self._die_center(i, n)
            if not self.roll:                                     # not rolled yet
                self._rr(c, cx - 0.47 * cs, cy - 0.49 * cs, 0.94 * cs, 0.94 * cs, 0.18 * cs, (0.0, 0.0, 0.0, 0.35))
                self._rr(c, cx - 0.45 * cs, cy - 0.45 * cs, 0.9 * cs, 0.9 * cs, 0.16 * cs, C_SLOT)
                continue
            if self.state == 'rolling':
                face = ((i * 5 + int(self.anim_t * 22) * 3 + i * i) % 6) + 1
                lift = 0.10 * cs * abs(math.sin(self.anim_t * 14 + i))
                self._die(c, cx, cy, 0.9 * cs, face, C_DIE, lift)
                continue
            selected = i in self.sel
            body = C_DIE if i in self.scorable else C_DIE_DEAD
            hov = self.hover == ('die', i) and self.state == 'choose'
            lift = 0.10 * cs if selected else (0.04 * cs if hov else 0.0)
            if selected:
                self._rr(c, cx - 0.56 * cs, cy - 0.56 * cs + lift, 1.12 * cs, 1.12 * cs, 0.24 * cs,
                         ov.alpha(ov.C_GOLD, 0.30))
                self._rr(c, cx - 0.5 * cs, cy - 0.5 * cs + lift, 1.0 * cs, 1.0 * cs, 0.2 * cs, ov.C_GOLD)
            self._die(c, cx, cy, 0.9 * cs, self.roll[i], body, lift)
            if self.state == 'choose':
                c.text(str(i + 1), cx, cy - 0.66 * cs, cs * 0.20, ov.C_TEXT)

        # set-aside dice
        c.text("SET ASIDE", L + bw / 2, T - 3.78 * cs, cs * 0.20, ov.C_TEXT)
        m = len(self.held)
        for i, v in enumerate(self.held):
            cx = L + bw / 2 + (i - (m - 1) / 2) * 0.72 * cs
            self._die(c, cx, T - 4.35 * cs, 0.56 * cs, v, C_DIE_HELD)

        # buttons
        info = self._sel_info() if self.state == 'choose' else None
        for name, (bx, by, bwid, bh) in self._buttons().items():
            if name == 'primary':
                if self.state == 'idle':
                    label, ok, base = "Roll", True, C_BTN_OK
                else:
                    label, ok, base = "Score & Roll", bool(info) and self.state == 'choose', C_BTN_OK
            else:
                total = self.turn_score + (info[0] if info else 0)
                label = "Bank %d" % total if info else "Bank"
                ok, base = bool(info) and self.state == 'choose', C_BTN_BANK
            hov = self.hover == (name, 0) and ok
            bg = ov.shade(base, 1.15 if hov else 1.0) if ok else (0.25, 0.27, 0.31, 1.0)
            self._rr(c, bx + 2, by - 3, bwid, bh, 0.18 * cs, (0.0, 0.0, 0.0, 0.30))
            self._rr(c, bx, by, bwid, bh, 0.18 * cs, ov.shade(bg, 0.78))
            self._rr(c, bx, by + 0.07 * cs, bwid, bh - 0.07 * cs, 0.17 * cs, bg)
            if ok:
                self._rr(c, bx + 0.12 * cs, by + bh - 0.2 * cs, bwid - 0.24 * cs, 0.08 * cs, 0.04 * cs,
                         (1.0, 1.0, 1.0, 0.30), 3)
            c.text(label, bx + bwid / 2, by + bh / 2, cs * 0.36, ov.C_DARK if ok else (0.55, 0.57, 0.62, 1.0))

        if self.state == 'over':
            if self.mode == 'CPU' and self.winner == 0:
                note = "Won by %d%s   (best %d)   -   click or SPACE for a new game" % (
                    self.margin, "  NEW BEST!" if self.new_best else "", self._best_score())
            else:
                note = "Click or press SPACE for a new game"
            self.banner(c, L, T - self.bh, bw, self.bh, main, note)
            self.draw_hint(c, "Won: %s %d - %s %d   |   R / SPACE new game" % (names[0], self.wins[0], names[1], self.wins[1]))
        else:
            self.draw_hint(c, "Click / 1-6 select  SPACE roll  B bank  S auto  T goal  D level  M mode  R new")


RUNNER = ov.Runner("dice", GAME_NAME, Dice, [
    "Roll six dice, set aside scoring dice (click or keys 1-6)",
    "SPACE: roll / Score & Roll   B: bank   S: auto-select",
    "1 = 100, 5 = 50, three of a kind = 100 x face (1s = 1000)",
    "More of a kind doubles; straights 500 / 750 / 1500",
    "No scoring dice = Farkle: you lose the turn's points",
    "T: goal   D: CPU level   M: vs CPU / 2 players   R: new",
])


def draw_ui(layout, context):
    RUNNER.draw_ui(layout)


def register():
    RUNNER.register()


def unregister():
    RUNNER.unregister()
