"""Solitaire (Klondike) - three difficulties, drawn as a GPU overlay.

Click a card (or the waste pile) to pick it up, then click where it should go:
a tableau column or a foundation. Double-click (or right-click) a card to send it
to its foundation. Click the stock to draw. Build the four foundations from Ace
to King to win.

    Easy    draw 1 card, unlimited redeals, undo + hints
    Medium  draw 3 cards, unlimited redeals, undo + hints
    Hard    draw 3 cards, only 2 redeals, no undo, no hints

Keys: Z undo   H hint   A auto-finish to the foundations   N new deal   M menu.
The best score and the fastest win are saved for each difficulty
(solitaire_easy / medium / hard).
"""

import copy
import math
import random
import traceback

from . import _overlay as ov
from . import _record as _rec

GAME_NAME = "Solitaire"
GAME_ICON = 'FILE_IMAGE'

W, H = 720.0, 480.0
CW, CH = 78.0, 104.0
GAP = 14.0
X0 = 45.0
TOP_Y = 468.0                       # top edge of the first row
TAB_Y = 346.0                       # top edge of the tableau columns

DIFFS = [
    dict(name="Easy", info="Draw 1 card - unlimited redeals - undo and hints", draw=1, redeals=None, undo=True,
         hint=True, mult=1.0, col=(0.45, 0.85, 0.45), recycle=-15),
    dict(name="Medium", info="Draw 3 cards - unlimited redeals - undo and hints", draw=3, redeals=None, undo=True,
         hint=True, mult=1.5, col=(1.0, 0.65, 0.25), recycle=-20),
    dict(name="Hard", info="Draw 3 cards - only 2 redeals - no undo, no hints", draw=3, redeals=2, undo=False,
         hint=False, mult=2.0, col=(0.95, 0.30, 0.30), recycle=-40),
]
DIGITS = {'ONE': 0, 'TWO': 1, 'THREE': 2, 'NUMPAD_1': 0, 'NUMPAD_2': 1, 'NUMPAD_3': 2}

RANKS = ("A", "2", "3", "4", "5", "6", "7", "8", "9", "10", "J", "Q", "K")
RED_COL = (0.82, 0.10, 0.16, 1.0)
BLK_COL = (0.10, 0.11, 0.15, 1.0)
FELT = (0.09, 0.40, 0.24, 1.0)
GOLD = (1.0, 0.82, 0.25, 1.0)


def _fmt(t):
    return "--" if t is None else "%d:%02d" % divmod(int(round(t)), 60)


def _red(suit):
    return suit in (1, 2)           # 0 spades, 1 hearts, 2 diamonds, 3 clubs


def _rounded(x, y, w, h, r, seg=4):
    pts = []
    for cx, cy, a0 in ((x + w - r, y + h - r, 0), (x + r, y + h - r, 90),
                       (x + r, y + r, 180), (x + w - r, y + r, 270)):
        for k in range(seg + 1):
            a = math.radians(a0 + 90.0 * k / seg)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


class ClipCanvas:
    """Wraps a Canvas and clips everything to the rectangle (x0, y0, w, h) in screen pixels."""

    def __init__(self, c, x0, y0, w, h):
        self.c, self.x0, self.y0, self.x1, self.y1 = c, x0, y0, x0 + w, y0 + h

    def _clip(self, pts):
        def run(pl, inside, inter):
            out = []
            for i in range(len(pl)):
                a, b = pl[i], pl[(i + 1) % len(pl)]
                ia, ib = inside(a), inside(b)
                if ia != ib:
                    out.append(inter(a, b))
                if ib:
                    out.append(b)
            return out
        X0, X1, Y0, Y1 = self.x0, self.x1, self.y0, self.y1
        ix = lambda xv: (lambda a, b: (xv, a[1] + (b[1] - a[1]) * (xv - a[0]) / ((b[0] - a[0]) or 1e-9)))
        iy = lambda yv: (lambda a, b: (a[0] + (b[0] - a[0]) * (yv - a[1]) / ((b[1] - a[1]) or 1e-9), yv))
        pl = list(pts)
        for inside, inter in ((lambda p: p[0] >= X0, ix(X0)), (lambda p: p[0] <= X1, ix(X1)),
                              (lambda p: p[1] >= Y0, iy(Y0)), (lambda p: p[1] <= Y1, iy(Y1))):
            if not pl:
                return []
            pl = run(pl, inside, inter)
        return pl

    def _inside(self, pts):
        return all(self.x0 <= x <= self.x1 and self.y0 <= y <= self.y1 for x, y in pts)

    def poly(self, pts, col):
        if self._inside(pts):
            self.c.poly(pts, col)
        else:
            cl = self._clip(pts)
            if len(cl) >= 3:
                self.c.poly(cl, col)

    def tri(self, a, b, c_, col):
        self.poly([a, b, c_], col)

    def rect(self, x, y, w, h, col):
        x0, y0 = max(x, self.x0), max(y, self.y0)
        x1, y1 = min(x + w, self.x1), min(y + h, self.y1)
        if x1 > x0 and y1 > y0:
            self.c.rect(x0, y0, x1 - x0, y1 - y0, col)

    def circle(self, cx, cy, r, col, seg=24):
        if cx + r < self.x0 or cx - r > self.x1 or cy + r < self.y0 or cy - r > self.y1:
            return
        if cx - r >= self.x0 and cx + r <= self.x1 and cy - r >= self.y0 and cy + r <= self.y1:
            self.c.circle(cx, cy, r, col, seg)
            return
        n = max(12, seg)
        self.poly([(cx + r * math.cos(2 * math.pi * k / n), cy + r * math.sin(2 * math.pi * k / n)) for k in range(n)], col)

    def ring(self, cx, cy, r, th, col, seg=28):
        ro = r + th / 2
        if cx + ro < self.x0 or cx - ro > self.x1 or cy + ro < self.y0 or cy - ro > self.y1:
            return
        if cx - ro >= self.x0 and cx + ro <= self.x1 and cy - ro >= self.y0 and cy + ro <= self.y1:
            self.c.ring(cx, cy, r, th, col, seg)
            return
        ri, n = max(0.0, r - th / 2), max(16, seg)
        for i in range(n):
            a0, a1 = 2 * math.pi * i / n, 2 * math.pi * (i + 1) / n
            self.poly([(cx + ro * math.cos(a0), cy + ro * math.sin(a0)), (cx + ro * math.cos(a1), cy + ro * math.sin(a1)),
                       (cx + ri * math.cos(a1), cy + ri * math.sin(a1)), (cx + ri * math.cos(a0), cy + ri * math.sin(a0))], col)

    def line(self, p, q, w, col):
        dx, dy = q[0] - p[0], q[1] - p[1]
        ln = math.hypot(dx, dy) or 1.0
        nx, ny = -dy / ln * w / 2, dx / ln * w / 2
        self.poly([(p[0] + nx, p[1] + ny), (p[0] - nx, p[1] - ny), (q[0] - nx, q[1] - ny), (q[0] + nx, q[1] + ny)], col)

    def polyline(self, pts, w, col):
        for a, b in zip(pts, pts[1:]):
            self.line(a, b, w, col)

    def text(self, s, x, y, size, col=(1, 1, 1, 1), align='center'):
        if self.x0 - 40 <= x <= self.x1 + 40 and self.y0 - 10 <= y <= self.y1 + 10:
            self.c.text(s, x, y, size, col, align)


class Solitaire(ov.BaseGame):
    keys = ('Z', 'H', 'A', 'N', 'M', 'ONE', 'TWO', 'THREE', 'NUMPAD_1', 'NUMPAD_2', 'NUMPAD_3')

    # ------------------------------------------------------------------ setup
    def setup(self):
        self.diff = 0
        self.menu = True
        self.menu_hover = -1
        self.record_mgrs = {}
        self.best_cache = {}
        self.sc, self.fx0, self.fy0 = 1.0, 0.0, 0.0
        self.time = 0.0
        self.mouse = None
        self.last_click = (None, -9.0)
        self.won_confetti = []
        self.undo_stack = []
        self._deal()

    def reset(self):
        if self.menu:
            return
        self._abandon()
        self._deal()

    def _deal(self):
        self.cfg = DIFFS[self.diff]
        deck = [(s, r) for s in range(4) for r in range(1, 14)]
        random.shuffle(deck)
        self.tab = []
        for col in range(7):
            pile = []
            for k in range(col + 1):
                pile.append([deck.pop(), k == col])
            self.tab.append(pile)
        self.stock = deck                      # face-down list, draw from the end
        self.waste = []
        self.found = [[], [], [], []]
        self.score = 0
        self.moves = 0
        self.play_t = 0.0
        self.started = False
        self.redeals = 0
        self.sel = None
        self.hint = None
        self.hint_t = 0.0
        self.state = 'play'                    # play | won
        self.undo_stack = []
        self.msg, self.msg_t = "", 0.0
        self.recorded = False
        self.new_best_score = self.new_best_time = False
        self.final = {}
        self.won_confetti = []
        self.flash = []                        # (x, y, t)

    # ---------------------------------------------------------------- records
    def _mgr(self, d):
        if d not in self.record_mgrs:
            try:
                self.record_mgrs[d] = _rec.RecordManager(game_name="solitaire_" + DIFFS[d]['name'].lower())
            except Exception:
                self.record_mgrs[d] = None
        return self.record_mgrs[d]

    def _best(self, d):
        if d not in self.best_cache:
            m = self._mgr(d)
            try:
                r = m.get_records() if m else {}
                self.best_cache[d] = (int(r.get("highest_score", 0) or 0), r.get("best_time"))
            except Exception:
                self.best_cache[d] = (0, None)
        return self.best_cache[d]

    def _abandon(self):
        """A started game that is dropped without winning counts as a loss."""
        if self.state == 'play' and self.moves >= 15 and not self.recorded:
            self.recorded = True
            m = self._mgr(self.diff)
            if m:
                try:
                    m.add_lose_record()
                except Exception:
                    traceback.print_exc()

    def _win(self):
        self.state = 'won'
        bonus = int(max(0.0, 700.0 - self.play_t) * 3 * self.cfg['mult'])
        self.score += bonus
        bs, bt = self._best(self.diff)
        self.final = dict(bonus=bonus, score=self.score)
        self.new_best_score = self.score > bs
        self.new_best_time = bt is None or self.play_t < bt
        rng = random.Random(4)
        self.won_confetti = [[rng.uniform(40, W - 40), rng.uniform(H, H + 300), rng.uniform(-30, 30), rng.uniform(-90, -40),
                              rng.random(), rng.randrange(4)] for _ in range(70)]
        if self.recorded:
            return
        self.recorded = True
        m = self._mgr(self.diff)
        if m:
            try:
                m.add_win_record(score=int(self.score), best_time=self.play_t)
                self.best_cache.pop(self.diff, None)
            except Exception:
                traceback.print_exc()

    # ------------------------------------------------------------------ rules
    def _snapshot(self):
        if self.cfg['undo']:
            self.undo_stack.append((copy.deepcopy(self.tab), list(self.stock), list(self.waste),
                                    [list(f) for f in self.found], self.score, self.redeals))
            if len(self.undo_stack) > 300:
                self.undo_stack.pop(0)

    def _undo(self):
        if not self.cfg['undo'] or not self.undo_stack or self.state != 'play':
            return
        self.tab, self.stock, self.waste, self.found, self.score, self.redeals = self.undo_stack.pop()
        self.score = max(0, self.score - 2)
        self.sel = None

    def _can_tab(self, card, col):
        pile = self.tab[col]
        if not pile:
            return card[1] == 13
        top, up = pile[-1]
        return up and top[1] == card[1] + 1 and _red(top[0]) != _red(card[0])

    def _can_found(self, card):
        f = self.found[card[0]]
        return card[1] == len(f) + 1

    def _bump(self, pts, x, y):
        self.score = max(0, self.score + pts)
        self.flash.append([x, y, 0.0])

    def _start_clock(self):
        self.started = True

    def _move_to_found(self, src):
        card = self._card_of(src)
        if card is None or not self._can_found(card):
            return False
        if src[0] == 'tab' and src[2] != len(self.tab[src[1]]) - 1:
            return False
        self._snapshot()
        self._take(src)
        self.found[card[0]].append(card)
        self._after_take(src, 10)
        self.sel = None
        self.moves += 1
        self._start_clock()
        if sum(len(f) for f in self.found) == 52:
            self._win()
        return True

    def _card_of(self, src):
        if src[0] == 'waste':
            return self.waste[-1] if self.waste else None
        if src[0] == 'found':
            return self.found[src[1]][-1] if self.found[src[1]] else None
        pile = self.tab[src[1]]
        if 0 <= src[2] < len(pile) and pile[src[2]][1]:
            return pile[src[2]][0]
        return None

    def _take(self, src):
        """Remove and return the card(s) of src."""
        if src[0] == 'waste':
            return [self.waste.pop()]
        if src[0] == 'found':
            return [self.found[src[1]].pop()]
        pile = self.tab[src[1]]
        cards = [c for c, _ in pile[src[2]:]]
        del pile[src[2]:]
        return cards

    def _after_take(self, src, pts):
        self._bump(pts, *self._pos_hint(src))
        if src[0] == 'tab' and self.tab[src[1]] and not self.tab[src[1]][-1][1]:
            self.tab[src[1]][-1][1] = True
            self._bump(5, *self._pos_hint(('tab', src[1], len(self.tab[src[1]]) - 1)))

    def _pos_hint(self, src):
        try:
            if src[0] == 'tab':
                p = self._tab_pos(src[1], max(0, min(src[2], len(self.tab[src[1]]) - 1)))
                return p[0] + CW / 2, p[1] - CH / 2
        except Exception:
            pass
        return W / 2, H / 2

    def _try_move(self, src, dst):
        """Move src -> dst ('tab', col) / ('found', suit). Returns True if done."""
        card = self._card_of(src)
        if card is None:
            return False
        if dst[0] == 'found':
            if src[0] != 'found' and dst[1] == card[0]:
                return self._move_to_found(src)
            return False
        col = dst[1]
        if src[0] == 'tab' and src[1] == col:
            return False
        if not self._can_tab(card, col):
            return False
        self._snapshot()
        cards = self._take(src)
        for cd in cards:
            self.tab[col].append([cd, True])
        pts = 5 if src[0] == 'waste' else (-15 if src[0] == 'found' else 0)
        self._after_take(src, pts)
        self.sel = None
        self.moves += 1
        self._start_clock()
        return True

    def _draw_stock(self):
        self._snapshot()
        if self.stock:
            for _ in range(min(self.cfg['draw'], len(self.stock))):
                self.waste.append(self.stock.pop())
        else:
            lim = self.cfg['redeals']
            if not self.waste or (lim is not None and self.redeals >= lim):
                if self.undo_stack:
                    self.undo_stack.pop()
                self.msg, self.msg_t = ("No redeals left" if self.waste else "Stock is empty"), 1.2
                return
            self.stock = self.waste[::-1]
            self.waste = []
            self.redeals += 1
            self.score = max(0, self.score + self.cfg['recycle'])
        self.sel = None
        self.moves += 1
        self._start_clock()

    def _auto_finish(self):
        moved = True
        n = 0
        while moved and self.state == 'play' and n < 60:
            moved = False
            n += 1
            for col in range(7):
                if self.tab[col] and self._move_to_found(('tab', col, len(self.tab[col]) - 1)):
                    moved = True
            if self.waste and self._move_to_found(('waste',)):
                moved = True

    def _find_hint(self):
        for col in range(7):
            if self.tab[col] and self.tab[col][-1][1]:
                c = self.tab[col][-1][0]
                if self._can_found(c):
                    return ('tab', col, len(self.tab[col]) - 1), ('found', c[0])
        if self.waste and self._can_found(self.waste[-1]):
            return ('waste',), ('found', self.waste[-1][0])
        for col in range(7):                                        # reveal a hidden card
            pile = self.tab[col]
            for i, (c, up) in enumerate(pile):
                if up and i > 0 and not pile[i - 1][1]:
                    for d in range(7):
                        if d != col and self._can_tab(c, d):
                            return ('tab', col, i), ('tab', d)
        if self.waste:
            for d in range(7):
                if self._can_tab(self.waste[-1], d):
                    return ('waste',), ('tab', d)
        for col in range(7):
            pile = self.tab[col]
            for i, (c, up) in enumerate(pile):
                if up and not (i == 0 and c[1] == 13):
                    for d in range(7):
                        if d != col and self._can_tab(c, d) and not (c[1] == 13 and i == 0):
                            return ('tab', col, i), ('tab', d)
        if self.stock or self.waste:
            return ('stock',), None
        return None

    # --------------------------------------------------------------- geometry
    def _col_x(self, i):
        return X0 + i * (CW + GAP)

    def _off(self, col):
        pile = self.tab[col]
        down, up = 12.0, 26.0
        need = sum(down if not f else up for _, f in pile[:-1]) if pile else 0.0
        avail = TAB_Y - 10.0 - CH
        k = min(1.0, avail / need) if need > 0 else 1.0
        return down * k, up * k

    def _tab_pos(self, col, idx):
        down, up = self._off(col)
        y = TAB_Y
        for _, f in self.tab[col][:idx]:
            y -= up if f else down
        return self._col_x(col), y            # (left, top)

    def _logical(self, x, y):
        return (x - self.fx0) / self.sc, (y - self.fy0) / self.sc

    def _hit(self, lx, ly):
        """What is under the point: ('stock',), ('waste',), ('found', s), ('tab', col, idx), ('col', col)."""
        if TOP_Y - CH <= ly <= TOP_Y:
            if self._col_x(0) <= lx <= self._col_x(0) + CW:
                return ('stock',)
            wx = self._col_x(1)
            n = min(len(self.waste), self.cfg['draw'])
            if wx <= lx <= wx + CW + max(0, n - 1) * 18 + 4 and self.waste:
                return ('waste',)
            if wx <= lx <= wx + CW and not self.waste:
                return ('wempty',)
            for s in range(4):
                fx = self._col_x(3 + s)
                if fx <= lx <= fx + CW:
                    return ('found', s)
        for col in range(7):
            x = self._col_x(col)
            if not (x <= lx <= x + CW):
                continue
            pile = self.tab[col]
            for i in range(len(pile) - 1, -1, -1):
                px, top = self._tab_pos(col, i)
                bottom = top - CH if i == len(pile) - 1 else top - (self._off(col)[1 if pile[i][1] else 0])
                if bottom <= ly <= top:
                    return ('tab', col, i)
            if not pile and TAB_Y - CH <= ly <= TAB_Y:
                return ('col', col)
            if pile and ly < self._tab_pos(col, len(pile) - 1)[1]:
                pass
        return None

    # ----------------------------------------------------------------- update
    def update(self, dt):
        self.time += dt
        if self.menu:
            return True
        dt = min(dt, 0.1)
        self.msg_t = max(0.0, self.msg_t - dt)
        self.hint_t = max(0.0, self.hint_t - dt)
        for f in self.flash:
            f[2] += dt
        self.flash = [f for f in self.flash if f[2] < 0.5]
        if self.state == 'play' and self.started:
            before = int(self.play_t)
            self.play_t += dt
            if int(self.play_t) != before:
                return True
        if self.state == 'won':
            for p in self.won_confetti:
                p[0] += p[2] * dt
                p[1] += p[3] * dt
                p[3] -= 160.0 * dt
                if p[1] < 20:
                    p[1], p[3] = 20, abs(p[3]) * 0.55
            return True
        return bool(self.flash) or self.hint_t > 0

    # ------------------------------------------------------------------ input
    def _card_menu(self, i):
        return 130.0, 270.0 - i * 92.0, 460.0, 80.0

    def _menu_idx(self, x, y):
        lx, ly = self._logical(x, y)
        for i in range(len(DIFFS)):
            cx, cy, w, h = self._card_menu(i)
            if cx <= lx <= cx + w and cy <= ly <= cy + h:
                return i
        return -1

    def move(self, x, y):
        if x < -1e5:
            self.mouse = None
            return False
        if self.menu:
            h = self._menu_idx(x, y)
            changed = h != self.menu_hover
            self.menu_hover = h
            return changed
        old = self.mouse
        self.mouse = self._logical(x, y)
        return old is None or abs(old[0] - self.mouse[0]) + abs(old[1] - self.mouse[1]) > 0.5

    def _start(self, i):
        self._abandon()
        self.diff = i
        self.menu = False
        self._deal()

    def click(self, x, y, button):
        if self.menu:
            if button == 'LEFT':
                i = self._menu_idx(x, y)
                if i >= 0:
                    self._start(i)
            return
        if self.state == 'won':
            self._deal()
            return
        lx, ly = self._logical(x, y)
        h = self._hit(lx, ly)
        if h is None:
            self.sel = None
            return
        self.hint = None
        if button == 'RIGHT':
            if h[0] in ('waste', 'tab'):
                src = ('waste',) if h[0] == 'waste' else (('tab', h[1], h[2]) if self._card_of(h) else None)
                if src:
                    self._move_to_found(src)
            return
        now = self.time
        double = self.last_click[0] == h and now - self.last_click[1] < 0.38
        self.last_click = (h, now)
        if h[0] == 'stock':
            self._draw_stock()
            return
        if double and h[0] in ('waste', 'tab') and self._card_of(h) is not None:
            if self._move_to_found(h if h[0] == 'waste' else ('tab', h[1], h[2])):
                return
        if self.sel is not None:
            src = self.sel
            dst = None
            if h[0] == 'found':
                dst = ('found', h[1])
            elif h[0] == 'tab':
                dst = ('tab', h[1])
            elif h[0] == 'col':
                dst = ('tab', h[1])
            if dst is not None and self._try_move(src, dst):
                return
            if h[0] in ('waste', 'tab', 'found') and self._card_of(h) is not None and h != src:
                self.sel = h
            else:
                self.sel = None
            return
        if h[0] in ('waste', 'tab', 'found') and self._card_of(h) is not None:
            self.sel = h
        elif h[0] == 'found' and self.found[h[1]]:
            self.sel = h

    def key(self, key, repeat):
        if self.menu:
            i = DIGITS.get(key)
            if i is not None:
                self._start(i)
            return
        if key == 'M':
            self._abandon()
            self.menu, self.menu_hover = True, -1
        elif key == 'N':
            self._abandon()
            self._deal()
        elif self.state != 'play':
            return
        elif key == 'Z':
            if self.cfg['undo']:
                self._undo()
            else:
                self.msg, self.msg_t = "No undo on Hard", 1.0
        elif key == 'A':
            self._auto_finish()
        elif key == 'H':
            if not self.cfg['hint']:
                self.msg, self.msg_t = "No hints on Hard", 1.0
            else:
                self.hint, self.hint_t = self._find_hint(), 2.0
                self.score = max(0, self.score - 5)
                if self.hint is None:
                    self.msg, self.msg_t = "No moves left - try a new deal (N)", 1.6

    # ----------------------------------------------------------------- layout
    def layout(self, view):
        cs = self.fit_cell(view, 9, 6, max_cell=100, min_cell=36)
        self.place(view, 9 * cs, 6 * cs)
        self.sc = cs / 80.0
        self.fx0 = self.left
        self.fy0 = self.top - 6 * cs

    # ---------------------------------------------------------------- drawing
    def _X(self, v):
        return self.fx0 + v * self.sc

    def _Y(self, v):
        return self.fy0 + v * self.sc

    def _rect(self, c, x, y, w, h, col):
        c.rect(self._X(x), self._Y(y), w * self.sc, h * self.sc, col)

    def _circ(self, c, x, y, r, col, seg=14):
        c.circle(self._X(x), self._Y(y), r * self.sc, col, seg)

    def _rr(self, c, x, y, w, h, r, col):
        c.poly([(self._X(px), self._Y(py)) for px, py in _rounded(x, y, w, h, r)], col)

    def _text(self, c, s, x, y, size, col=ov.C_WHITE, align='center'):
        c.text(s, self._X(x), self._Y(y), size * self.sc, col, align)

    def _suit(self, c, suit, cx, cy, r, col=None):
        col = col or (RED_COL if _red(suit) else BLK_COL)
        P = lambda x, y: (self._X(x), self._Y(y))
        if suit == 1:                                           # heart
            c.poly([P(cx - 1.04 * r, cy + 0.12 * r), P(cx + 1.04 * r, cy + 0.12 * r), P(cx, cy - 1.0 * r)], col)
            self._circ(c, cx - 0.5 * r, cy + 0.42 * r, 0.58 * r, col, 12)
            self._circ(c, cx + 0.5 * r, cy + 0.42 * r, 0.58 * r, col, 12)
        elif suit == 2:                                         # diamond
            c.poly([P(cx, cy + 1.1 * r), P(cx + 0.8 * r, cy), P(cx, cy - 1.1 * r), P(cx - 0.8 * r, cy)], col)
        elif suit == 0:                                         # spade
            c.poly([P(cx - 1.04 * r, cy - 0.1 * r), P(cx + 1.04 * r, cy - 0.1 * r), P(cx, cy + 1.05 * r)], col)
            self._circ(c, cx - 0.5 * r, cy - 0.4 * r, 0.58 * r, col, 12)
            self._circ(c, cx + 0.5 * r, cy - 0.4 * r, 0.58 * r, col, 12)
            c.poly([P(cx - 0.2 * r, cy - 0.3 * r), P(cx + 0.2 * r, cy - 0.3 * r), P(cx + 0.5 * r, cy - 1.15 * r),
                    P(cx - 0.5 * r, cy - 1.15 * r)], col)
        else:                                                   # club
            self._circ(c, cx, cy + 0.52 * r, 0.52 * r, col, 12)
            self._circ(c, cx - 0.6 * r, cy - 0.22 * r, 0.52 * r, col, 12)
            self._circ(c, cx + 0.6 * r, cy - 0.22 * r, 0.52 * r, col, 12)
            c.poly([P(cx - 0.18 * r, cy - 0.1 * r), P(cx + 0.18 * r, cy - 0.1 * r), P(cx + 0.5 * r, cy - 1.15 * r),
                    P(cx - 0.5 * r, cy - 1.15 * r)], col)

    def _face_down(self, c, x, top):
        self._rr(c, x + 2, top - CH - 3, CW, CH, 6, (0, 0, 0, 0.30))
        self._rr(c, x, top - CH, CW, CH, 6, (0.93, 0.93, 0.96, 1))
        self._rr(c, x + 3.5, top - CH + 3.5, CW - 7, CH - 7, 4, (0.17, 0.28, 0.62, 1))
        for i in range(-3, 4):
            for j in range(-4, 5):
                if (i + j) % 2 == 0:
                    cx, cy = x + CW / 2 + i * 9.5, top - CH / 2 + j * 9.5
                    if abs(i) * 9.5 < CW / 2 - 8 and abs(j) * 9.5 < CH / 2 - 8:
                        c.poly([(self._X(cx), self._Y(cy + 4)), (self._X(cx + 4), self._Y(cy)), (self._X(cx), self._Y(cy - 4)),
                                (self._X(cx - 4), self._Y(cy))], (0.30, 0.45, 0.85, 0.8))
        self._rr(c, x + 3.5, top - CH + 3.5, CW - 7, 2.4, 1, (1, 1, 1, 0.12))

    def _face_up(self, c, x, top, card, selected=False, hint=False, hover=False, partial=False):
        s, r = card
        col = RED_COL if _red(s) else BLK_COL
        self._rr(c, x + 2, top - CH - 3, CW, CH, 6, (0, 0, 0, 0.30))
        if selected:
            self._rr(c, x - 3, top - CH - 3, CW + 6, CH + 6, 8, GOLD)
        elif hint:
            self._rr(c, x - 3, top - CH - 3, CW + 6, CH + 6, 8, (0.4, 0.9, 1.0, 0.9))
        self._rr(c, x, top - CH, CW, CH, 6, (0.78, 0.78, 0.82, 1))
        self._rr(c, x + 0.8, top - CH + 0.8, CW - 1.6, CH - 1.6, 5.4, (0.99, 0.98, 0.95, 1) if not hover else (1, 1, 0.9, 1))
        self._text(c, RANKS[r - 1], x + 11, top - 13, 15 if r != 10 else 13, col)
        self._suit(c, s, x + 11, top - 29, 5.0)
        if not partial:
            self._text(c, RANKS[r - 1], x + CW - 11, top - CH + 13, 15 if r != 10 else 13, col)
            self._suit(c, s, x + CW - 11, top - CH + 29, 5.0)
            if r > 10 or r == 1:
                self._suit(c, s, x + CW / 2, top - CH / 2, 16.0 if r == 1 else 13.0)
                if r > 10:
                    self._rr(c, x + CW / 2 - 17, top - CH / 2 - 26, 34, 52, 3, (col[0], col[1], col[2], 0.10))
            else:
                pips = {2: [(0, 1), (0, -1)], 3: [(0, 1), (0, 0), (0, -1)], 4: [(-1, 1), (1, 1), (-1, -1), (1, -1)],
                        5: [(-1, 1), (1, 1), (0, 0), (-1, -1), (1, -1)], 6: [(-1, 1), (1, 1), (-1, 0), (1, 0), (-1, -1), (1, -1)],
                        7: [(-1, 1), (1, 1), (0, 0.5), (-1, 0), (1, 0), (-1, -1), (1, -1)],
                        8: [(-1, 1), (1, 1), (0, 0.5), (-1, 0), (1, 0), (0, -0.5), (-1, -1), (1, -1)],
                        9: [(-1, 1.2), (1, 1.2), (-1, 0.4), (1, 0.4), (0, 0), (-1, -0.4), (1, -0.4), (-1, -1.2), (1, -1.2)],
                        10: [(-1, 1.3), (1, 1.3), (0, 0.9), (-1, 0.45), (1, 0.45), (-1, -0.45), (1, -0.45), (0, -0.9), (-1, -1.3), (1, -1.3)]}[r]
                for px, py in pips:
                    self._suit(c, s, x + CW / 2 + px * 14, top - CH / 2 + py * 17, 6.2)

    def _placeholder(self, c, x, top, label=None, suit=None):
        self._rr(c, x, top - CH, CW, CH, 6, (0, 0, 0, 0.18))
        self._rr(c, x + 2, top - CH + 2, CW - 4, CH - 4, 5, (FELT[0] * 1.05, FELT[1] * 1.12, FELT[2] * 1.05, 1))
        if suit is not None:
            self._suit(c, suit, x + CW / 2, top - CH / 2, 15.0, (1, 1, 1, 0.20))
        if label:
            self._text(c, label, x + CW / 2, top - CH / 2, 18, (1, 1, 1, 0.25))

    def _table(self, c):
        self._rect(c, 0, 0, W, H, FELT)
        for k in range(8):
            self._rect(c, 0, k * 60, W, 30, (0, 0, 0, 0.035))
        for k in range(7):
            self._circ(c, W / 2, H / 2, 420 - k * 50, (1, 1, 1, 0.018), 40)
        self._rect(c, 0, 0, W, 5, (0, 0, 0, 0.25))
        self._rect(c, 0, H - 5, W, 5, (0, 0, 0, 0.25))

    def _menu(self, c):
        self._table(c)
        self._text(c, "SOLITAIRE", W / 2, 420, 46, GOLD)
        for i, s in enumerate((0, 1, 3, 2)):
            self._face_up(c, W / 2 - 170 + i * 90, 395 - 0, (s, 1 + i * 3), False)
        self._text(c, "Klondike - pick a difficulty", W / 2, 300, 13, (1, 1, 1, 0.7))
        for i, d in enumerate(DIFFS):
            x, y, w, h = self._card_menu(i)
            y -= 40
            hov = i == self.menu_hover
            self._rect(c, x + 3, y - 4, w, h, (0, 0, 0, 0.4))
            self._rect(c, x, y, w, h, ov.C_CELL_HOVER if hov else ov.C_CELL)
            self._rect(c, x, y, 9, h, tuple(d['col']) + (1.0,))
            self._text(c, "%d  %s" % (i + 1, d['name']), x + 24, y + h - 24, 20, ov.C_WHITE, 'left')
            self._text(c, d['info'], x + 24, y + 22, 10.5, ov.C_TEXT, 'left')
            bs, bt = self._best(i)
            if bs:
                self._text(c, "Best score  %d" % bs, x + w - 14, y + h - 26, 13, ov.C_GOLD, 'right')
                self._text(c, "Fastest win  %s" % _fmt(bt), x + w - 14, y + 22, 12, ov.C_GOLD, 'right')
            else:
                self._text(c, "No win yet", x + w - 14, y + h - 26, 12, ov.C_TEXT, 'right')

    # ---- header / hint that shrink to fit the panel width
    def _fit_text(self, c, text, x, y, size, col):
        px, _, pw, _ = self.panel
        est = len(text) * 0.56 * size
        if est > pw * 0.94:
            size = size * pw * 0.94 / est
        c.text(text, x, y, size, col)

    def _fit_header(self, c, main, sub=None, col=ov.C_WHITE):
        px, _, pw, _ = self.panel
        u = self.u
        if sub:
            self._fit_text(c, main, px + pw / 2, self.hy + 10 * u, 19 * u, col)
            self._fit_text(c, sub, px + pw / 2, self.hy - 13 * u, 12 * u, ov.C_TEXT)
        else:
            self._fit_text(c, main, px + pw / 2, self.hy, 19 * u, col)

    def _fit_hint(self, c, text):
        px, _, pw, _ = self.panel
        self._fit_text(c, text, px + pw / 2, self.fy, 11 * self.u, ov.C_TEXT)

    def draw(self, c):
        raw = c
        self.draw_frame(raw)
        c = ClipCanvas(raw, self.fx0, self.fy0, W * self.sc, H * self.sc)
        bs, bt = self._best(self.diff)
        if self.menu:
            self._fit_header(raw, "SOLITAIRE", "Best score and fastest win saved for every difficulty", ov.C_GOLD)
            self._menu(c)
            self._fit_hint(raw, "Click or press 1-3 to pick a difficulty   ESC quit")
            return
        left = ""
        if self.cfg['redeals'] is not None:
            left = "   |   Redeals left %d" % (self.cfg['redeals'] - self.redeals)
        self._fit_header(raw, "SOLITAIRE - %s" % self.cfg['name'],
                         "Score %d   |   Moves %d   |   Time %s   |   Best %d (%s)%s" % (
                             self.score, self.moves, _fmt(self.play_t), bs, _fmt(bt), left),
                         tuple(self.cfg['col']) + (1.0,))
        self._table(c)
        hs, hd = (self.hint if self.hint_t > 0 and self.hint else (None, None))
        # stock
        sx = self._col_x(0)
        if self.stock:
            for k in range(min(3, 1 + len(self.stock) // 10)):
                self._face_down(c, sx - k * 1.2, TOP_Y + k * 1.2)
            self._text(c, "%d" % len(self.stock), sx + CW / 2, TOP_Y - CH - 9, 9, (1, 1, 1, 0.55))
        else:
            self._placeholder(c, sx, TOP_Y, "O" if self.waste else None)
        if hs and hs[0] == 'stock':
            self._rr(c, sx - 3, TOP_Y - CH - 3, CW + 6, CH + 6, 8, (0.4, 0.9, 1.0, 0.45))
        # waste
        wx = self._col_x(1)
        self._placeholder(c, wx, TOP_Y)
        n = min(len(self.waste), self.cfg['draw'])
        for k in range(n):
            card = self.waste[len(self.waste) - n + k]
            top_card = k == n - 1
            self._face_up(c, wx + k * 18, TOP_Y, card, selected=(self.sel == ('waste',) and top_card),
                          hint=(hs == ('waste',) and top_card), partial=not top_card)
        # foundations
        for s in range(4):
            fx = self._col_x(3 + s)
            self._placeholder(c, fx, TOP_Y, None, s)
            if self.found[s]:
                self._face_up(c, fx, TOP_Y, self.found[s][-1], selected=(self.sel == ('found', s)),
                              hint=(hd == ('found', s)))
            elif hd == ('found', s):
                self._rr(c, fx - 3, TOP_Y - CH - 3, CW + 6, CH + 6, 8, (0.4, 0.9, 1.0, 0.6))
        # tableau
        for col in range(7):
            x = self._col_x(col)
            pile = self.tab[col]
            if not pile:
                self._placeholder(c, x, TAB_Y, "K")
                if hd == ('tab', col):
                    self._rr(c, x - 3, TAB_Y - CH - 3, CW + 6, CH + 6, 8, (0.4, 0.9, 1.0, 0.6))
            for i, (card, up) in enumerate(pile):
                _, top = self._tab_pos(col, i)
                last = i == len(pile) - 1
                if not up:
                    self._face_down(c, x, top)
                else:
                    sel = self.sel is not None and self.sel[0] == 'tab' and self.sel[1] == col and i >= self.sel[2]
                    hint = hs is not None and hs[0] == 'tab' and hs[1] == col and i >= hs[2] or \
                        (hd == ('tab', col) and last)
                    self._face_up(c, x, top, card, selected=sel, hint=bool(hint), partial=not last)
        for fx_, fy_, t in self.flash:
            k = t / 0.5
            self._text(c, "+", fx_, fy_ + 20 * k, 14, (1, 0.9, 0.4, 1 - k))
        if self.msg_t > 0:
            self._text(c, self.msg, W / 2, 20, 14, ov.alpha(GOLD, min(1.0, self.msg_t * 2)))
        if self.state == 'won':
            self._rect(c, 0, 0, W, H, (0, 0, 0, 0.45))
            for x, y, vx, vy, ph, s in self.won_confetti:
                self._suit(c, s, x, y, 9.0, (RED_COL if _red(s) else (0.95, 0.95, 1.0, 1.0)))
            f = self.final
            self._text(c, "YOU WIN!", W / 2, 330, 56, GOLD)
            self._text(c, "Time bonus  +%d" % f['bonus'], W / 2, 280, 14, ov.C_TEXT)
            self._text(c, "SCORE  %d%s" % (f['score'], "   NEW BEST!" if self.new_best_score else ""), W / 2, 244, 26, GOLD)
            self._text(c, "Time  %s%s" % (_fmt(self.play_t), "   NEW BEST TIME!" if self.new_best_time else ""), W / 2, 210, 15,
                       GOLD if self.new_best_time else ov.C_TEXT)
            self._text(c, "Click: new deal     M: change difficulty", W / 2, 160, 13, ov.C_TEXT)
        self._fit_hint(raw, "Click a card then a target   dbl-click / right-click: foundation   Z undo  H hint  A auto  N new  M menu")


RUNNER = ov.Runner("solitaire", GAME_NAME, Solitaire, [
    "Click a card, then click where it goes",
    "Double-click / right-click: send to foundation",
    "Click the stock to draw (Easy 1, Medium/Hard 3)",
    "Z: undo   H: hint   A: auto-finish   N: new deal",
    "Win = all 52 cards on the foundations",
    "Best score + fastest win saved per difficulty",
])


def draw_ui(layout, context):
    RUNNER.draw_ui(layout)


def register():
    RUNNER.register()


def unregister():
    RUNNER.unregister()
