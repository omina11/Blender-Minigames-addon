"""Scopa - the classic Italian card game, you vs the computer
(drawn as a GPU overlay in the 3D Viewport).

40-card Italian deck (Denari, Coppe, Spade, Bastoni; Asso..7, Fante, Cavallo, Re).
Play a card: take a card of the same value, or - if there is none - a group of
table cards that add up to it. Otherwise the card stays on the table.
Clearing the table is a "scopa" (+1). At the end of a round you score:
Carte (most cards), Denari (most coins), Settebello (7 of coins),
Primiera (best prime) and your scope. First to 11 wins the match.
"""

import math
import random

from . import _overlay as ov
from . import _record as _rec

GAME_NAME = "Scopa"
GAME_ICON = 'COPYDOWN'

TARGET = 11                 # points to win the match
AI_DELAY = (0.8, 1.4)       # seconds the computer "thinks"
W, H = 720, 520             # design size (scaled to the viewport)

C_FELT = (0.10, 0.32, 0.22, 1.0)
C_FELT_EDGE = (0.30, 0.18, 0.09, 1.0)
C_CARD = (0.96, 0.93, 0.84, 1.0)
C_CARD_EDGE = (0.20, 0.18, 0.16, 1.0)
C_INK = (0.16, 0.13, 0.10, 1.0)
C_BACK = (0.55, 0.12, 0.14, 1.0)
C_DIM = (0.45, 0.47, 0.52, 1.0)
C_BTN_OFF = (0.17, 0.18, 0.21, 1.0)
C_BTN = (0.20, 0.50, 0.30, 1.0)
C_HOVER = (0.95, 0.96, 0.98, 1.0)

SUITS = ("Denari", "Coppe", "Spade", "Bastoni")
RNAMES = {1: "Asso", 8: "Fante", 9: "Cavallo", 10: "Re"}
FACE = {8: "F", 9: "C", 10: "R"}
PRIME = {7: 21, 6: 18, 1: 16, 5: 15, 4: 14, 3: 13, 2: 12, 8: 10, 9: 10, 10: 10}
SETTEBELLO = 6              # 7 of Denari  (suit 0 * 10 + 7 - 1)


# ----------------------------------------------------------------------------
# Cards and rules (pure Python)
# card = 0..39 ; suit = card // 10 ; value = card % 10 + 1
# ----------------------------------------------------------------------------

def val(c):
    return c % 10 + 1


def suit(c):
    return c // 10


def card_name(c):
    v = val(c)
    return "%s di %s" % (RNAMES.get(v, str(v)), SUITS[suit(c)])


def find_options(card, table):
    """All legal captures for `card`: tuples of table cards (empty list -> must place).

    A card of the same value on the table MUST be taken (no sums allowed then).
    """
    v = val(card)
    eq = [(t,) for t in table if val(t) == v]
    if eq:
        return eq
    items = sorted(table, key=val)
    res = []

    def dfs(start, left, chosen):
        if left == 0:
            res.append(tuple(chosen))
            return
        for i in range(start, len(items)):
            t = items[i]
            tv = val(t)
            if tv > left:
                break
            chosen.append(t)
            dfs(i + 1, left - tv, chosen)
            chosen.pop()
    dfs(0, v, [])
    return res


def primiera(cards):
    best = {}
    for c in cards:
        p = PRIME[val(c)]
        if p > best.get(suit(c), 0):
            best[suit(c)] = p
    return sum(best.values()) if len(best) == 4 else 0


def score_round(cap, scope):
    """Return (rows, points). rows: (label, text_you, text_opp, pts_you, pts_opp)."""
    rows, pts = [], [0, 0]

    def add(label, ta, tb, pa, pb):
        rows.append((label, ta, tb, pa, pb))
        pts[0] += pa
        pts[1] += pb
    a, b = len(cap[0]), len(cap[1])
    add("Carte", str(a), str(b), int(a > b), int(b > a))
    a = sum(1 for c in cap[0] if suit(c) == 0)
    b = sum(1 for c in cap[1] if suit(c) == 0)
    add("Denari", str(a), str(b), int(a > b), int(b > a))
    s0, s1 = SETTEBELLO in cap[0], SETTEBELLO in cap[1]
    add("Settebello", "yes" if s0 else "-", "yes" if s1 else "-", int(s0), int(s1))
    a, b = primiera(cap[0]), primiera(cap[1])
    add("Primiera", str(a), str(b), int(a > b), int(b > a))
    add("Scope", str(scope[0]), str(scope[1]), scope[0], scope[1])
    return rows, pts


def card_weight(c):
    """How much the computer likes to own a card."""
    w = 1.0 + PRIME[val(c)] / 21.0 * 1.5
    if suit(c) == 0:
        w += 1.2
    if c == SETTEBELLO:
        w += 6.0
    return w


# ----------------------------------------------------------------------------
# Game (overlay)
# ----------------------------------------------------------------------------

class Scopa(ov.BaseGame):
    keys = ('ONE', 'TWO', 'THREE', 'NUMPAD_1', 'NUMPAD_2', 'NUMPAD_3',
            'LEFT_ARROW', 'RIGHT_ARROW', 'SPACE', 'RET', 'NUMPAD_ENTER')

    def setup(self):
        self.k = 1.0
        self.base = 0.0
        self.wins = self.losses = 0
        try:
            self.record_mgr = _rec.RecordManager(game_name="scopa")
            rec = self.record_mgr.get_records()
            self.wins = int(rec.get("wins", 0))
            self.losses = int(rec.get("losses", 0))
        except Exception:
            self.record_mgr = None

    def reset(self):
        """New match."""
        self.points = [0, 0]
        self.round_no = 0
        self.dealer = random.randrange(2)
        self.recorded = False
        self.match_won = False
        self.hover = None
        self.buttons = []
        self.rects = []
        self.breakdown = []
        self.round_pts = [0, 0]
        self.timer = 0.0
        self._opt_key = None
        self._opt_val = []
        self._new_round()

    # ------------------------------------------------------------------ flow
    def _new_round(self):
        self.round_no += 1
        if self.round_no > 1:
            self.dealer = 1 - self.dealer
        while True:                                  # 3+ kings on the table: redeal
            self.deck = list(range(40))
            random.shuffle(self.deck)
            self.table = [self.deck.pop() for _ in range(4)]
            if sum(1 for c in self.table if val(c) == 10) < 3:
                break
        self.hands = [[self.deck.pop() for _ in range(3)] for _ in range(2)]
        self._sort_hand()
        self.captured = [[], []]
        self.scope = [0, 0]
        self.last_capturer = None
        self.last_card = None
        self.log = ("New round - %s deals" % ("you" if self.dealer == 0 else "the opponent"),
                    ov.C_TEXT)
        self._set_turn(1 - self.dealer)

    def _sort_hand(self):
        self.hands[0].sort(key=lambda c: (suit(c), val(c)))

    def _set_turn(self, t):
        self.turn = t
        self.sel_card = None
        self.sel_tab = set()
        if t == 0:
            self.state = 'human'
        else:
            self.state = 'ai'
            self.timer = random.uniform(*AI_DELAY)

    def _play(self, p, card, opt):
        """Player p plays `card` taking the table cards in `opt` (empty = place)."""
        self.hands[p].remove(card)
        who = "You" if p == 0 else "Opponent"
        scopa = False
        if opt:
            for t in opt:
                self.table.remove(t)
            self.captured[p] += [card] + list(opt)
            self.last_capturer = p
            last = not self.deck and not self.hands[0] and not self.hands[1]
            scopa = (not self.table) and not last        # no scopa on the very last play
            if scopa:
                self.scope[p] += 1
            txt = "%s: %s takes %s" % (who, card_name(card),
                                       " + ".join(card_name(t) for t in opt))
            if scopa:
                txt += "  -  SCOPA!"
        else:
            self.table.append(card)
            txt = "%s places %s" % (who, card_name(card))
        if p == 1:
            self.last_card = card
        self.log = (txt, ov.C_GOLD if scopa else ov.C_WHITE)

        if not self.hands[0] and not self.hands[1]:
            if self.deck:                                 # deal 3 more each
                for _ in range(3):
                    for q in (0, 1):
                        self.hands[q].append(self.deck.pop())
                self._sort_hand()
            else:
                return self._end_round()
        self._set_turn(1 - p)

    def _end_round(self):
        if self.table and self.last_capturer is not None:
            self.captured[self.last_capturer] += self.table
            self.table = []
        rows, pts = score_round(self.captured, self.scope)
        self.breakdown = rows
        self.round_pts = pts
        self.points[0] += pts[0]
        self.points[1] += pts[1]
        top = max(self.points)
        if top >= TARGET and self.points[0] != self.points[1]:
            self._match_end(self.points[0] > self.points[1])
        else:
            self.state = 'round_over'

    def _match_end(self, won):
        self.state = 'match_over'
        self.match_won = won
        if self.record_mgr and not self.recorded:
            self.recorded = True
            try:
                rec = (self.record_mgr.add_win_record(score=self.points[0])
                       if won else self.record_mgr.add_lose_record())
                self.wins, self.losses = rec["wins"], rec["losses"]
            except Exception:
                pass

    # ------------------------------------------------------------------ computer
    def _ai_move(self):
        hand = self.hands[1]
        t_sum = sum(val(t) for t in self.table)
        last = not self.deck and not self.hands[0] and len(hand) == 1
        best = None
        for card in hand:
            opts = find_options(card, self.table)
            if opts:
                for o in opts:
                    s = 1.0 + sum(card_weight(x) for x in (card,) + o)
                    rest = [t for t in self.table if t not in o]
                    if not rest:
                        if not last:
                            s += 7.0                      # scopa
                    elif sum(val(t) for t in rest) <= 10:
                        s -= 3.0                          # leaves a scopa chance
                    s += random.uniform(-0.4, 0.4)
                    if best is None or s > best[0]:
                        best = (s, card, o)
            else:
                s = -card_weight(card) * 1.3
                if t_sum + val(card) <= 10:
                    s -= 3.5                              # opponent could sweep it
                s += random.uniform(-0.4, 0.4)
                if best is None or s > best[0]:
                    best = (s, card, ())
        self._play(1, best[1], best[2])

    # ------------------------------------------------------------------ input
    def _opts(self):
        if self.sel_card is None:
            return []
        key = (self.sel_card, tuple(self.table))
        if self._opt_key != key:
            self._opt_key = key
            self._opt_val = find_options(self.sel_card, self.table)
        return self._opt_val

    def _cands(self):
        return {t for o in self._opts() for t in o}

    def _select(self, idx):
        if self.state != 'human' or not (0 <= idx < len(self.hands[0])):
            return
        self.sel_card = self.hands[0][idx]
        self.sel_tab = set()

    def _confirm(self):
        if self.state == 'round_over':
            return self._new_round()
        if self.state == 'match_over':
            return self.reset()
        if self.state != 'human' or self.sel_card is None:
            return
        opts = self._opts()
        if not opts:
            self._play(0, self.sel_card, ())
        elif len(opts) == 1:
            self._play(0, self.sel_card, opts[0])
        else:
            fs = frozenset(self.sel_tab)
            for o in opts:
                if frozenset(o) == fs:
                    self._play(0, self.sel_card, o)
                    return

    def _toggle(self, tid):
        if self.sel_card is None or tid not in self._cands():
            return
        opts = self._opts()
        if len(opts) == 1:                           # already highlighted: just take it
            return self._confirm()
        if tid in self.sel_tab:
            self.sel_tab.discard(tid)
        else:
            self.sel_tab.add(tid)
        fs = frozenset(self.sel_tab)
        for o in opts:                               # exact match -> capture
            if frozenset(o) == fs:
                self._play(0, self.sel_card, o)
                return

    def key(self, key, repeat):
        if repeat and key not in ('LEFT_ARROW', 'RIGHT_ARROW'):
            return
        if key in ('SPACE', 'RET', 'NUMPAD_ENTER'):
            self._confirm()
        elif key in ('ONE', 'NUMPAD_1'):
            self._select(0)
        elif key in ('TWO', 'NUMPAD_2'):
            self._select(1)
        elif key in ('THREE', 'NUMPAD_3'):
            self._select(2)
        elif key in ('LEFT_ARROW', 'RIGHT_ARROW') and self.state == 'human':
            hand = self.hands[0]
            if not hand:
                return
            cur = hand.index(self.sel_card) if self.sel_card in hand else -1
            step = -1 if key == 'LEFT_ARROW' else 1
            self._select(max(0, min(len(hand) - 1, cur + step if cur >= 0 else 0)))

    def _pick(self, x, y):
        for bid, bx, by, bw, bh, _l, _f, en, _s in self.buttons:
            if bid and en and bx <= x <= bx + bw and by <= y <= by + bh:
                return ('btn', bid)
        for kind, ident, rx, ry, rw, rh in self.rects:
            if rx <= x <= rx + rw and ry <= y <= ry + rh:
                return (kind, ident)
        return None

    def click(self, x, y, button):
        if button == 'RIGHT':
            if self.state == 'human':
                self.sel_card = None
                self.sel_tab = set()
            return
        hit = self._pick(x, y)
        if hit is None:
            return
        kind, ident = hit
        if kind == 'btn':
            self._confirm()
        elif self.state != 'human':
            return
        elif kind == 'hand':
            card = self.hands[0][ident]
            if card == self.sel_card:
                self._confirm()
            else:
                self._select(ident)
        elif kind == 'tab':
            self._toggle(ident)

    def move(self, x, y):
        h = self._pick(x, y)
        if h != self.hover:
            self.hover = h
            return True
        return False

    def update(self, dt):
        if self.state == 'ai':
            self.timer -= dt
            if self.timer <= 0:
                self._ai_move()
                return True
        return False

    # ------------------------------------------------------------------ layout
    def _x(self, dx):
        return self.left + dx * self.k

    def _y(self, dy):
        return self.base + dy * self.k

    def _table_slots(self, n):
        cols = 8
        res = []
        for i in range(n):
            r, j = divmod(i, cols)
            in_row = min(cols, n - r * cols)
            res.append((360 + (j - (in_row - 1) / 2.0) * 64, 360 - r * 78))
        return res

    def _hand_slot(self, i, n):
        return 360 + (i - (n - 1) / 2.0) * 80, 76

    def layout(self, view):
        u = view.u
        k = min(u, (view.w - 24 * u) / W, (view.h - 112 * u) / H)
        self.k = max(0.45 * u, k)
        self.place(view, W * self.k, H * self.k, min_w=0)
        self.base = self.top - H * self.k
        self._layout_hit()

    def _layout_hit(self):
        k = self.k
        rects, btns = [], []

        def btn(bid, dx, dy, w, h, label, face, enabled=True, size=13):
            btns.append((bid, self._x(dx), self._y(dy), w * k, h * k,
                         label, face, enabled, size * k))
        st = self.state
        if st == 'human':
            for t, (dx, dy) in zip(self.table, self._table_slots(len(self.table))):
                rects.append(('tab', t, self._x(dx) - 24 * k, self._y(dy) - 35 * k,
                              48 * k, 70 * k))
            n = len(self.hands[0])
            for i in range(n):
                dx, dy = self._hand_slot(i, n)
                rects.append(('hand', i, self._x(dx) - 31 * k, self._y(dy) - 45 * k,
                              62 * k, 106 * k))
        if st in ('human', 'ai'):
            opts = self._opts() if st == 'human' else []
            if st != 'human' or self.sel_card is None:
                label, en = "Play  (SPACE)", False
            elif not opts:
                label, en = "Place card  (SPACE)", True
            elif len(opts) == 1:
                label, en = "Take  (SPACE)", True
            else:
                label, en = "Pick the cards to take", False
            btn('play', 250, 4, 220, 24, label, C_BTN, en)
        elif st == 'round_over':
            btn('next', 250, 4, 220, 24, "Next round  (SPACE)", C_BTN, True)
        else:
            btn('new', 250, 4, 220, 24, "New match  (SPACE)", (0.62, 0.46, 0.10, 1.0), True)
        self.rects, self.buttons = rects, btns

    # ------------------------------------------------------------------ drawing
    def _rr(self, c, x, y, w, h, ch, col):
        c.poly([(x + ch, y), (x + w - ch, y), (x + w, y + ch), (x + w, y + h - ch),
                (x + w - ch, y + h), (x + ch, y + h), (x, y + h - ch), (x, y + ch)], col)

    def _tp(self, c, pts, cx, cy, r, ang, col):
        ca, sa = math.cos(ang), math.sin(ang)
        c.poly([(cx + r * (x * ca - y * sa), cy + r * (x * sa + y * ca)) for x, y in pts], col)

    def _tc(self, c, x, y, rad, cx, cy, r, ang, col):
        ca, sa = math.cos(ang), math.sin(ang)
        c.circle(cx + r * (x * ca - y * sa), cy + r * (x * sa + y * ca), r * rad, col, 14)

    def _suit_sym(self, c, s, cx, cy, r):
        if s == 0:                                              # Denari: gold coin
            dark = (0.66, 0.46, 0.06, 1.0)
            c.circle(cx, cy, r * 0.92, (0.55, 0.37, 0.04, 1.0), 20)
            c.circle(cx, cy, r * 0.80, (0.97, 0.77, 0.17, 1.0), 20)
            c.ring(cx, cy, r * 0.50, max(1.0, r * 0.12), dark, 20)
            c.circle(cx, cy, r * 0.16, dark, 10)
        elif s == 1:                                            # Coppe: red cup
            red, dark = (0.78, 0.14, 0.16, 1.0), (0.52, 0.07, 0.10, 1.0)
            self._tp(c, [(-0.75, 0.7), (0.75, 0.7), (0.62, 0.15), (0.3, -0.2),
                         (-0.3, -0.2), (-0.62, 0.15)], cx, cy, r, 0, red)
            self._tp(c, [(-0.82, 0.8), (0.82, 0.8), (0.76, 0.62), (-0.76, 0.62)],
                     cx, cy, r, 0, dark)
            self._tp(c, [(-0.09, -0.2), (0.09, -0.2), (0.09, -0.55), (-0.09, -0.55)],
                     cx, cy, r, 0, red)
            self._tp(c, [(-0.45, -0.8), (0.45, -0.8), (0.3, -0.55), (-0.3, -0.55)],
                     cx, cy, r, 0, dark)
        elif s == 2:                                            # Spade: tilted sword
            a = -0.6
            self._tp(c, [(0, 1.0), (0.14, 0.8), (0.14, -0.05), (-0.14, -0.05),
                         (-0.14, 0.8)], cx, cy, r, a, (0.28, 0.46, 0.80, 1.0))
            self._tp(c, [(-0.42, -0.05), (0.42, -0.05), (0.42, -0.18), (-0.42, -0.18)],
                     cx, cy, r, a, (0.22, 0.22, 0.32, 1.0))
            self._tp(c, [(-0.07, -0.18), (0.07, -0.18), (0.07, -0.62), (-0.07, -0.62)],
                     cx, cy, r, a, (0.45, 0.28, 0.12, 1.0))
            self._tc(c, 0, -0.7, 0.12, cx, cy, r, a, (0.22, 0.22, 0.32, 1.0))
        else:                                                   # Bastoni: green club
            a = 0.55
            body, knob = (0.32, 0.50, 0.14, 1.0), (0.17, 0.34, 0.09, 1.0)
            self._tp(c, [(-0.13, 0.9), (0.13, 0.9), (0.2, -0.85), (-0.2, -0.85)],
                     cx, cy, r, a, body)
            self._tc(c, 0, 0.92, 0.22, cx, cy, r, a, knob)
            self._tc(c, 0, -0.88, 0.20, cx, cy, r, a, knob)
            self._tc(c, 0.26, 0.25, 0.10, cx, cy, r, a, knob)
            self._tc(c, -0.24, -0.2, 0.10, cx, cy, r, a, knob)

    def _card(self, c, cx, cy, w, h, card=None, ring=None):
        """Draw a card centred on pixel (cx, cy); w/h in design units. card=None -> back."""
        k, u = self.k, self.u
        w, h = w * k, h * k
        x0, y0 = cx - w / 2, cy - h / 2
        ch = min(w, h) * 0.10
        if ring is not None:
            t = 3 * u
            self._rr(c, x0 - t, y0 - t, w + 2 * t, h + 2 * t, ch + t, ring)
        self._rr(c, x0 - 1, y0 - 1, w + 2, h + 2, ch, C_CARD_EDGE)
        if card is None:
            self._rr(c, x0, y0, w, h, ch, C_BACK)
            m = w * 0.12
            self._rr(c, x0 + m, y0 + m, w - 2 * m, h - 2 * m, ch * 0.6, ov.shade(C_BACK, 1.5))
            c.poly([(cx, cy + h * 0.26), (cx + w * 0.22, cy),
                    (cx, cy - h * 0.26), (cx - w * 0.22, cy)], ov.shade(C_BACK, 0.7))
            return
        self._rr(c, x0, y0, w, h, ch, C_CARD)
        v = val(card)
        c.text(str(v), cx, cy + h * 0.30, h * 0.27, C_INK)
        self._suit_sym(c, suit(card), cx, cy - h * 0.04, h * 0.22)
        if v in FACE:
            c.text(FACE[v], cx, cy - h * 0.36, h * 0.15, C_INK)

    def _ring_for(self, kind, ident):
        if self.state != 'human':
            return None
        if kind == 'hand':
            if self.hands[0][ident] == self.sel_card:
                return ov.C_GOLD
        else:
            opts = self._opts()
            if ident in self.sel_tab or (len(opts) == 1 and ident in opts[0]):
                return ov.C_GOLD
            if ident in self._cands():
                if self.hover == ('tab', ident):
                    return C_HOVER
                return ov.C_GOOD
            return None
        if self.hover == (kind, ident):
            return C_HOVER
        return None

    def _prompt(self):
        st = self.state
        if st == 'ai':
            return "Opponent is thinking...", ov.C_TEXT
        if st == 'round_over':
            return "Round over - press SPACE", ov.C_TEXT
        if st == 'match_over':
            return ("You won the match!", ov.C_GOLD) if self.match_won else \
                   ("You lost the match", ov.C_BAD)
        if self.sel_card is None:
            return "Your turn - choose a card", ov.C_GOLD
        opts = self._opts()
        if not opts:
            return "No capture - place the card on the table", ov.C_TEXT
        if len(opts) == 1:
            return "Take the highlighted cards", ov.C_GOOD
        return "Choose which cards to take", ov.C_GOOD

    def _draw_summary(self, c):
        k = self.k
        X, Y = self._x, self._y
        c.rect(X(90), Y(162), 540 * k, 246 * k, (0.0, 0.0, 0.0, 0.55))
        c.text("Round %d" % self.round_no, X(360), Y(390), 17 * k, ov.C_GOLD)
        c.text("You", X(400), Y(362), 12 * k, ov.C_TEXT)
        c.text("Opponent", X(520), Y(362), 12 * k, ov.C_TEXT)
        for n, (label, ta, tb, pa, pb) in enumerate(self.breakdown):
            y = Y(334 - n * 28)
            c.text(label, X(130), y, 14 * k, ov.C_WHITE, 'left')
            c.text(ta + ("  +%d" % pa if pa else ""), X(400), y, 14 * k,
                   ov.C_GOOD if pa else ov.C_TEXT)
            c.text(tb + ("  +%d" % pb if pb else ""), X(520), y, 14 * k,
                   ov.C_GOOD if pb else ov.C_TEXT)
        c.text("Round: You +%d   Opponent +%d" % tuple(self.round_pts),
               X(360), Y(197), 14 * k, ov.C_WHITE)
        c.text("Match: You %d - %d Opponent   (first to %d)" %
               (self.points[0], self.points[1], TARGET), X(360), Y(177), 13 * k, ov.C_GOLD)

    def draw(self, c):
        k, u = self.k, self.u
        X, Y = self._x, self._y
        self.draw_frame(c)
        sub = "Round %d  |  You %d - %d Opp  |  to %d  |  Record %dW - %dL" % (
            self.round_no, self.points[0], self.points[1], TARGET, self.wins, self.losses)
        self.draw_header(c, "Scopa", sub, ov.C_GOLD)

        # --- felt
        self._rr(c, X(14), Y(136), 692 * k, 285 * k, 16 * k, C_FELT_EDGE)
        self._rr(c, X(20), Y(142), 680 * k, 273 * k, 12 * k, C_FELT)

        # --- opponent
        n = len(self.hands[1])
        for i in range(n):
            self._card(c, X(360 + (i - (n - 1) / 2.0) * 46), Y(474), 38, 56)
        opp = "Opponent" + ("  (dealer)" if self.dealer == 1 else "")
        c.text(opp, X(30), Y(490), 15 * k, ov.C_WHITE, 'left')
        c.text("Captured: %d   Scope: %d" % (len(self.captured[1]), self.scope[1]),
               X(30), Y(468), 11 * k, ov.C_TEXT, 'left')
        if self.last_card is not None:
            self._card(c, X(650), Y(466), 38, 56, self.last_card)
            c.text("Last played", X(650), Y(500), 10 * k, ov.C_TEXT)
        c.text(self.log[0], X(360), Y(428), 13 * k, self.log[1])

        # --- table
        if self.state in ('human', 'ai'):
            for t, (dx, dy) in zip(self.table, self._table_slots(len(self.table))):
                self._card(c, X(dx), Y(dy), 48, 70, t, self._ring_for('tab', t))
        else:
            self._draw_summary(c)
        txt, col = self._prompt()
        c.text(txt, X(360), Y(154), 14 * k, col)

        # --- you
        hand = self.hands[0]
        for i, card in enumerate(hand):
            dx, dy = self._hand_slot(i, len(hand))
            if card == self.sel_card:
                dy += 14
            self._card(c, X(dx), Y(dy), 62, 90, card, self._ring_for('hand', i))
        me = "You" + ("  (dealer)" if self.dealer == 0 else "")
        c.text(me, X(30), Y(104), 15 * k, ov.C_WHITE, 'left')
        c.text("Captured: %d   Scope: %d" % (len(self.captured[0]), self.scope[0]),
               X(30), Y(82), 11 * k, ov.C_TEXT, 'left')
        if self.deck:
            self._card(c, X(650), Y(76), 40, 58)
        c.text("Deck: %d" % len(self.deck), X(650), Y(40), 11 * k, ov.C_TEXT)

        # --- buttons
        b = max(1.0, 2 * u)
        for bid, x, y, w, h, label, face, en, size in self.buttons:
            f = face if en else C_BTN_OFF
            if en and self.hover == ('btn', bid):
                f = ov.shade(f, 1.25)
            c.bevel(x, y, w, h, b, f)
            c.text(label, x + w / 2, y + h / 2, size, ov.C_WHITE if en else C_DIM)

        self.draw_hint(c, "Click a card, then the table cards to take   "
                          "1-3 / arrows select   R new match   ESC quit")


RUNNER = ov.Runner("scopa", GAME_NAME, Scopa, [
    "Scopa vs the computer (first to %d)" % TARGET,
    "Click a card, then the cards to take",
    "SPACE: confirm / next   Right click: deselect",
    "1-3 or arrows: select a card",
    "R: new match   ESC: quit",
])


def draw_ui(layout, context):
    RUNNER.draw_ui(layout)


def register():
    RUNNER.register()


def unregister():
    RUNNER.unregister()
