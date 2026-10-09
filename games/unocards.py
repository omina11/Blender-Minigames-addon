"""GTLP Cards - the classic colour-matching card game against 2-3 CPU players (GPU overlay).

Match the top card by COLOUR or by NUMBER / SYMBOL. Special cards:
    Skip (a circle with a bar)  - the next player loses their turn
    Reverse (two arrows)        - the order of play flips
    +2                          - the next player draws two and is skipped
    Wild (four colours)         - you choose the colour
    Wild +4                     - choose the colour, the next player draws four and is skipped
Can't play? Draw a card (click the deck or press SPACE): if it fits you may play it,
otherwise the turn passes. First to empty their hand wins.

Difficulties: Easy (2 opponents, random CPU), Medium (3 opponents, smarter CPU),
Hard (3 opponents, tactical CPU and you MUST press GTLP (G) before playing your
second-last card or you draw 2). Best score and fastest win saved per difficulty
(gtlp_easy / gtlp_medium / gtlp_hard).
"""

import math
import random
import traceback

from . import _overlay as ov
from . import _record as _rec

GAME_NAME = "GTLP Cards"
GAME_ICON = 'COLOR'

W, H = 720.0, 480.0
CW, CH = 62.0, 92.0
PILE_Y = 204.0                               # bottom of the two centre piles
DRAW_X = W / 2 - 4.0 - CW                     # draw pile (left of centre)
DISC_X = W / 2 + 4.0                          # discard pile (right of centre)
CENTER = (W / 2, PILE_Y + CH / 2)             # centre of the colour ring
RING_R = 90.0                                 # colour ring: contains both piles and their labels

COLORS = {'R': (0.90, 0.20, 0.22, 1.0), 'Y': (0.98, 0.78, 0.15, 1.0), 'G': (0.20, 0.68, 0.34, 1.0), 'B': (0.20, 0.42, 0.88, 1.0)}
ORDER = ('R', 'Y', 'G', 'B')
COLOR_NAME = {'R': "red", 'Y': "yellow", 'G': "green", 'B': "blue"}

DIFFS = [
    dict(name="Easy", info="2 opponents - the CPU plays at random", n=3, level=0, gtlp_call=False, mult=1.0, col=(0.45, 0.85, 0.45)),
    dict(name="Medium", info="3 opponents - the CPU plays with a plan", n=4, level=1, gtlp_call=False, mult=1.5, col=(1.0, 0.65, 0.25)),
    dict(name="Hard", info="3 opponents - tactical CPU, you must call GTLP", n=4, level=2, gtlp_call=True, mult=2.0, col=(0.95, 0.30, 0.30)),
]
DIGITS = {'ONE': 0, 'TWO': 1, 'THREE': 2, 'FOUR': 3, 'NUMPAD_1': 0, 'NUMPAD_2': 1, 'NUMPAD_3': 2, 'NUMPAD_4': 3}
NAMES = ("You", "Ada", "Bob", "Cleo")


def _fmt(t):
    return "--" if t is None else "%d:%02d" % divmod(int(round(t)), 60)


def _rounded(x, y, w, h, r, seg=4):
    pts = []
    for cx, cy, a0 in ((x + w - r, y + h - r, 0), (x + r, y + h - r, 90),
                       (x + r, y + r, 180), (x + w - r, y + r, 270)):
        for k in range(seg + 1):
            a = math.radians(a0 + 90.0 * k / seg)
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def _deck():
    d = []
    for col in ORDER:
        d.append((col, '0'))
        for k in list('123456789') + ['S', 'V', 'D']:
            d += [(col, k), (col, k)]
    d += [('W', 'W')] * 4 + [('W', 'F')] * 4
    return d


def _value(card):
    k = card[1]
    if k.isdigit():
        return int(k)
    return 50 if card[0] == 'W' else 20


class GtlpCards(ov.BaseGame):
    keys = ('SPACE', 'G', 'M', 'N', 'ONE', 'TWO', 'THREE', 'FOUR', 'NUMPAD_1', 'NUMPAD_2', 'NUMPAD_3', 'NUMPAD_4')

    # ------------------------------------------------------------------ setup
    def setup(self):
        self.diff = 1
        self.menu = True
        self.menu_hover = -1
        self.record_mgrs = {}
        self.best_cache = {}
        self.sc, self.fx0, self.fy0 = 1.0, 0.0, 0.0
        self.time = 0.0
        self.mouse = None
        self.hover = -1
        self._deal()

    def reset(self):
        if not self.menu:
            self._abandon()
            self._deal()

    def _deal(self):
        self.cfg = DIFFS[self.diff]
        self.n = self.cfg['n']
        deck = _deck()
        random.shuffle(deck)
        self.hands = [[deck.pop() for _ in range(7)] for _ in range(self.n)]
        self.hands[0].sort(key=self._sort_key)
        while True:                                            # the first card must be a number
            first = deck.pop()
            if first[1].isdigit():
                break
            deck.insert(0, first)
        self.pile = deck
        self.disc = [first]
        self.color = first[0]
        self.dir = 1
        self.turn = random.randrange(self.n)
        self.phase = 'play'                    # play | color | over
        self.wait = 1.0
        self.drawn = None                      # index of the card just drawn by you (only it may be played)
        self.pending_wild = None
        self.gtlp_called = False
        self.msg, self.msg_t = "", 0.0
        self.log = []
        self.anims = []
        self.play_t = 0.0
        self.winner = None
        self.recorded = False
        self.final = {}
        self.new_best_score = self.new_best_time = False
        self.flash = [0.0] * 4
        self.say("%s starts" % NAMES[self.turn])

    @staticmethod
    def _sort_key(card):
        return ("RYGBW".index(card[0]), card[1])

    def say(self, text):
        self.msg, self.msg_t = text, 2.2
        self.log.append(text)

    # ---------------------------------------------------------------- records
    def _mgr(self, d):
        if d not in self.record_mgrs:
            try:
                self.record_mgrs[d] = _rec.RecordManager(game_name="gtlp_" + DIFFS[d]['name'].lower())
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
        if self.phase != 'over' and not self.recorded and len(self.disc) > 12:
            self.recorded = True
            m = self._mgr(self.diff)
            if m:
                try:
                    m.add_lose_record()
                except Exception:
                    traceback.print_exc()

    def _finish(self, winner):
        self.winner = winner
        self.phase = 'over'
        won = winner == 0
        pts = 0
        if won:
            pts = int(sum(_value(c) for h in self.hands[1:] for c in h) * self.cfg['mult']
                      + max(0.0, 240.0 - self.play_t) * 2 * self.cfg['mult'])
        bs, bt = self._best(self.diff)
        self.final = dict(won=won, score=pts)
        if won:
            self.new_best_score = pts > bs
            self.new_best_time = bt is None or self.play_t < bt
        if self.recorded:
            return
        self.recorded = True
        m = self._mgr(self.diff)
        if m:
            try:
                if won:
                    m.add_win_record(score=pts, best_time=self.play_t)
                else:
                    m.add_lose_record()
                self.best_cache.pop(self.diff, None)
            except Exception:
                traceback.print_exc()

    # ------------------------------------------------------------------ rules
    def _playable(self, card):
        top = self.disc[-1]
        if card[0] == 'W':
            return True
        return card[0] == self.color or (card[1] == top[1] and top[0] != 'W')

    def _next(self, i, steps=1):
        return (i + self.dir * steps) % self.n

    def _draw_cards(self, p, k):
        got = []
        for _ in range(k):
            if not self.pile:
                if len(self.disc) <= 1:
                    break
                top = self.disc.pop()
                self.pile = self.disc
                random.shuffle(self.pile)
                self.disc = [top]
            c = self.pile.pop()
            self.hands[p].append(c)
            got.append(c)
            if p == 0:
                self.hands[0].sort(key=self._sort_key)
        if got:
            self.anims.append(dict(kind='draw', p=p, t=0.0, n=len(got)))
        return got

    def _apply_card(self, p, card, color):
        """Effects of a card played by p; sets who plays next. Returns text."""
        self.disc.append(card)
        self.color = color if card[0] == 'W' else card[0]
        k = card[1]
        nxt = self._next(p)
        text = ""
        if k == 'S':
            text = "%s is skipped" % NAMES[nxt]
            self.turn = self._next(p, 2)
        elif k == 'V':
            if self.n == 2:
                self.turn = p
            else:
                self.dir *= -1
                self.turn = self._next(p)
            text = "Reverse!"
        elif k == 'D':
            self._draw_cards(nxt, 2)
            text = "%s draws 2" % NAMES[nxt]
            self.turn = self._next(p, 2)
        elif k == 'F':
            self._draw_cards(nxt, 4)
            text = "%s draws 4 - %s" % (NAMES[nxt], COLOR_NAME[self.color])
            self.turn = self._next(p, 2)
        elif k == 'W':
            text = "Colour: %s" % COLOR_NAME[self.color]
            self.turn = self._next(p)
        else:
            self.turn = self._next(p)
        self.anims.append(dict(kind='play', p=p, card=card, t=0.0))
        return text

    def _play(self, p, idx, color=None):
        card = self.hands[p].pop(idx)
        text = self._apply_card(p, card, color or card[0])
        self.drawn = None
        self.flash[p] = 0.5
        self.say(("%s plays %s. %s" % (NAMES[p], self._cname(card), text)).strip())
        if not self.hands[p]:
            self._finish(p)
            return
        if len(self.hands[p]) == 1 and p != 0:
            self.say("%s: GTLP!" % NAMES[p])
        self.wait = 0.9
        self.gtlp_called = False

    @staticmethod
    def _cname(card):
        names = {'S': "Skip", 'V': "Reverse", 'D': "+2", 'W': "Wild", 'F': "Wild +4"}
        k = names.get(card[1], card[1])
        return k if card[0] == 'W' else "%s %s" % (COLOR_NAME[card[0]], k)

    # ------------------------------------------------------------------- AI
    def _best_color(self, hand):
        cnt = {c: 0 for c in ORDER}
        for c in hand:
            if c[0] in cnt:
                cnt[c[0]] += 1
        m = max(cnt.values())
        return random.choice([c for c in ORDER if cnt[c] == m]) if m else random.choice(ORDER)

    def _cpu_choose(self, p):
        hand = self.hands[p]
        ok = [i for i, c in enumerate(hand) if self._playable(c)]
        if not ok:
            return None
        lvl = self.cfg['level']
        if lvl == 0:
            i = random.choice(ok)
            return i, random.choice(ORDER)
        nxt = self._next(p)
        threat = len(self.hands[nxt]) <= 2
        def score(i):
            c = hand[i]
            s = 0.0
            if c[0] == 'W':
                s -= 6.0 if lvl == 2 and len(ok) > 1 and not threat else 1.0
                if c[1] == 'F':
                    s -= 1.5
                    if threat:
                        s += 9.0
                s += 0.5
            else:
                same = sum(1 for x in hand if x[0] == c[0])
                s += 1.0 + 0.3 * same
                if c[1] in ('S', 'D'):
                    s += 3.0 if threat else 1.0
                if c[1] == 'V':
                    s += 2.0 if threat and self.n > 2 else 0.4
                if c[1].isdigit():
                    s += int(c[1]) * 0.12                  # shed the high numbers first
            if len(hand) == 2 and c[0] == 'W':
                s += 2.0                                  # keep it safe: finish with a wild
            return s + random.random() * 0.2
        best = max(ok, key=score)
        rest = [c for j, c in enumerate(hand) if j != best]
        return best, self._best_color(rest if rest else hand)

    def _cpu_turn(self):
        p = self.turn
        ch = self._cpu_choose(p)
        if ch is None:
            got = self._draw_cards(p, 1)
            self.say("%s draws a card" % NAMES[p])
            if got and self._playable(got[0]):
                idx = len(self.hands[p]) - 1
                color = self._best_color(self.hands[p][:-1]) if got[0][0] == 'W' else None
                self.wait = 0.7
                self._play(p, idx, color)
            else:
                self.turn = self._next(p)
                self.wait = 0.8
            return
        self._play(p, ch[0], ch[1])

    # ----------------------------------------------------------------- update
    def update(self, dt):
        self.time += dt
        if self.menu:
            return True
        dt = min(dt, 0.1)
        self.msg_t = max(0.0, self.msg_t - dt)
        for a in self.anims:
            a['t'] += dt
        self.anims = [a for a in self.anims if a['t'] < 0.45]
        self.flash = [max(0.0, f - dt) for f in self.flash]
        if self.phase == 'over':
            return True
        self.play_t += dt
        if self.phase == 'play' and self.turn != 0:
            self.wait -= dt
            if self.wait <= 0:
                self._cpu_turn()
        elif self.phase == 'play' and self.turn == 0:
            self.wait = max(0.0, self.wait - dt)
        return True

    # ------------------------------------------------------------------ input
    def _logical(self, x, y):
        return (x - self.fx0) / self.sc, (y - self.fy0) / self.sc

    def _card_menu(self, i):
        return 130.0, 212.0 - i * 92.0, 460.0, 80.0

    def _menu_idx(self, x, y):
        lx, ly = self._logical(x, y)
        for i in range(len(DIFFS)):
            cx, cy, w, h = self._card_menu(i)
            if cx <= lx <= cx + w and cy <= ly <= cy + h:
                return i
        return -1

    def _hand_geom(self):
        n = len(self.hands[0])
        avail = 560.0
        step = min(54.0, (avail - CW) / (n - 1)) if n > 1 else 0.0
        total = CW + step * (n - 1)
        x0 = W / 2 - total / 2
        return x0, step

    def _hand_hit(self, lx, ly):
        x0, step = self._hand_geom()
        n = len(self.hands[0])
        for i in range(n - 1, -1, -1):
            cx = x0 + i * step
            lift = 16.0 if i == self.hover else 0.0
            if cx <= lx <= cx + (CW if i == n - 1 else max(step, 8.0)) and 8.0 <= ly <= 8.0 + CH + lift:
                return i
        return -1

    def _deck_rect(self):
        return DRAW_X, PILE_Y, CW, CH

    def _gtlp_rect(self):
        return W - 150.0, 150.0, 110.0, 36.0

    def _pick_rects(self):
        return [(W / 2 - 124 + i * 64, 200.0, 56.0, 56.0) for i in range(4)]

    def move(self, x, y):
        if x < -1e5:
            self.mouse = None
            return False
        if self.menu:
            h = self._menu_idx(x, y)
            changed = h != self.menu_hover
            self.menu_hover = h
            return changed
        lx, ly = self._logical(x, y)
        self.mouse = (lx, ly)
        h = self._hand_hit(lx, ly) if self.phase == 'play' else -1
        changed = h != self.hover
        self.hover = h
        return changed

    def _start(self, i):
        self._abandon()
        self.diff = i
        self.menu = False
        self._deal()

    def _human_draw(self):
        if self.turn != 0 or self.phase != 'play':
            return
        if self.drawn is not None:                     # pass after drawing a playable card
            self.drawn = None
            self.turn = self._next(0)
            self.wait = 0.7
            self.say("You pass")
            return
        got = self._draw_cards(0, 1)
        if not got:
            self.turn = self._next(0)
            return
        if self._playable(got[0]):
            self.drawn = self.hands[0].index(got[0])
            self.say("You drew a playable card - play it or pass (SPACE)")
        else:
            self.say("You draw a card - no luck, next player")
            self.turn = self._next(0)
            self.wait = 0.9

    def _human_play(self, idx):
        if self.turn != 0 or self.phase != 'play':
            return
        card = self.hands[0][idx]
        if self.drawn is not None and idx != self.drawn:
            return
        if not self._playable(card):
            self.say("That card doesn't match")
            return
        if len(self.hands[0]) == 2 and self.cfg['gtlp_call'] and not self.gtlp_called:
            self._draw_cards(0, 2)
            self.say("You forgot to call GTLP!  +2 cards")
            self.turn = self._next(0)
            self.wait = 0.9
            self.drawn = None
            return
        if card[0] == 'W':
            self.pending_wild = idx
            self.phase = 'color'
            return
        self._play(0, idx)

    def _pick_color(self, col):
        if self.phase != 'color' or self.pending_wild is None:
            return
        idx, self.pending_wild = self.pending_wild, None
        self.phase = 'play'
        self._play(0, idx, col)

    def click(self, x, y, button):
        if self.menu:
            if button == 'LEFT':
                i = self._menu_idx(x, y)
                if i >= 0:
                    self._start(i)
            return
        lx, ly = self._logical(x, y)
        if self.phase == 'over':
            if button == 'LEFT':
                self._deal()
            return
        if button != 'LEFT':
            return
        if self.phase == 'color':
            for i, (rx, ry, rw, rh) in enumerate(self._pick_rects()):
                if rx <= lx <= rx + rw and ry <= ly <= ry + rh:
                    self._pick_color(ORDER[i])
            return
        dx, dy, dw, dh = self._deck_rect()
        if dx <= lx <= dx + dw and dy <= ly <= dy + dh:
            self._human_draw()
            return
        ux, uy, uw, uh = self._gtlp_rect()
        if self.cfg['gtlp_call'] and ux <= lx <= ux + uw and uy <= ly <= uy + uh:
            self._call_gtlp()
            return
        i = self._hand_hit(lx, ly)
        if i >= 0:
            self._human_play(i)

    def _call_gtlp(self):
        if len(self.hands[0]) == 2 and self.turn == 0:
            self.gtlp_called = True
            self.say("GTLP!")

    def key(self, key, repeat):
        if self.menu:
            i = DIGITS.get(key)
            if i is not None and i < len(DIFFS):
                self._start(i)
            return
        if key == 'M':
            self._abandon()
            self.menu, self.menu_hover = True, -1
        elif key == 'N':
            self._abandon()
            self._deal()
        elif self.phase == 'color':
            i = DIGITS.get(key)
            if i is not None:
                self._pick_color(ORDER[i])
        elif self.phase == 'play':
            if key == 'SPACE':
                self._human_draw()
            elif key == 'G' and self.cfg['gtlp_call']:
                self._call_gtlp()
        elif self.phase == 'over' and key == 'SPACE':
            self._deal()

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

    def _circ(self, c, x, y, r, col, seg=18):
        c.circle(self._X(x), self._Y(y), r * self.sc, col, seg)

    def _line(self, c, p, q, w, col):
        c.line((self._X(p[0]), self._Y(p[1])), (self._X(q[0]), self._Y(q[1])), max(1.0, w * self.sc), col)

    def _poly(self, c, pts, col):
        c.poly([(self._X(x), self._Y(y)) for x, y in pts], col)

    def _rr(self, c, x, y, w, h, r, col):
        self._poly(c, _rounded(x, y, w, h, r), col)

    def _text(self, c, s, x, y, size, col=ov.C_WHITE, align='center'):
        c.text(s, self._X(x), self._Y(y), size * self.sc, col, align)

    def _oval(self, cx, cy, rx, ry, ang, n=20, a0=0.0, a1=2 * math.pi):
        ca, sa = math.cos(ang), math.sin(ang)
        pts = []
        for k in range(n + 1):
            a = a0 + (a1 - a0) * k / n
            x, y = math.cos(a) * rx, math.sin(a) * ry
            pts.append((cx + x * ca - y * sa, cy + x * sa + y * ca))
        return pts

    def _card_face(self, c, card, x, y, w=CW, h=CH, dim=False, glow=None):
        """x, y = bottom-left."""
        col, k = card
        base = COLORS.get(col, (0.12, 0.12, 0.16, 1.0))
        s = w / CW
        if glow:
            self._rr(c, x - 3 * s, y - 3 * s, w + 6 * s, h + 6 * s, 8 * s, glow)
        self._rr(c, x + 2 * s, y - 3 * s, w, h, 7 * s, (0, 0, 0, 0.30))
        self._rr(c, x, y, w, h, 7 * s, (0.97, 0.97, 0.98, 1))
        self._rr(c, x + 3 * s, y + 3 * s, w - 6 * s, h - 6 * s, 5 * s, base)
        cx, cy = x + w / 2, y + h / 2
        ang = math.radians(-24)
        self._poly(c, self._oval(cx, cy, w * 0.40, h * 0.30, ang), (0.97, 0.97, 0.98, 1))
        dark = (base[0] * 0.78, base[1] * 0.78, base[2] * 0.78, 1.0)
        if col == 'W':
            for q in range(4):
                a0, a1 = q * math.pi / 2, (q + 1) * math.pi / 2
                pts = [(cx, cy)] + self._oval(cx, cy, w * 0.34, h * 0.24, ang, 6, a0, a1)
                self._poly(c, pts, COLORS[ORDER[q]])
            if k == 'F':
                self._text(c, "+4", cx, cy, 15 * s, (1, 1, 1, 1))
        elif k.isdigit():
            self._text(c, k, cx, cy, 30 * s, dark)
        elif k == 'S':
            c.ring(self._X(cx), self._Y(cy), 14 * s * self.sc, 5 * s * self.sc, dark, 20)
            self._line(c, (cx - 10 * s, cy - 10 * s), (cx + 10 * s, cy + 10 * s), 5 * s, dark)
        elif k == 'V':
            for sg in (-1, 1):
                self._line(c, (cx - sg * 11 * s, cy - sg * 6 * s), (cx + sg * 8 * s, cy + sg * 6 * s), 4.5 * s, dark)
                tip = (cx + sg * 14 * s, cy + sg * 8 * s)
                self._poly(c, [tip, (tip[0] - sg * 9 * s, tip[1] + 2 * s), (tip[0] - sg * 3 * s, tip[1] - 8 * s)], dark)
        elif k == 'D':
            self._rr(c, cx - 14 * s, cy - 9 * s, 14 * s, 20 * s, 2 * s, dark)
            self._rr(c, cx + 0 * s, cy - 11 * s, 14 * s, 20 * s, 2 * s, ov.shade(dark, 1.25))
            self._text(c, "+2", cx + 2 * s, cy - 24 * s * 0 + 0, 0.01, dark)
        label = {'S': "S", 'V': "R", 'D': "+2", 'W': "W", 'F': "+4"}.get(k, k)
        for (tx, ty) in ((x + 10 * s, y + h - 13 * s), (x + w - 10 * s, y + 13 * s)):
            self._text(c, label, tx, ty, 11 * s, (1, 1, 1, 1))
        if dim:
            self._rr(c, x, y, w, h, 7 * s, (0, 0, 0, 0.42))

    def _card_back(self, c, x, y, w=CW, h=CH):
        s = w / CW
        self._rr(c, x + 2 * s, y - 3 * s, w, h, 7 * s, (0, 0, 0, 0.30))
        self._rr(c, x, y, w, h, 7 * s, (0.97, 0.97, 0.98, 1))
        self._rr(c, x + 3 * s, y + 3 * s, w - 6 * s, h - 6 * s, 5 * s, (0.10, 0.10, 0.14, 1))
        self._poly(c, self._oval(x + w / 2, y + h / 2, w * 0.40, h * 0.28, math.radians(-24)), (0.88, 0.18, 0.2, 1))
        if w > 40:
            self._text(c, "GTLP", x + w / 2, y + h / 2, 13 * s, (1.0, 0.9, 0.2, 1))

    def _seat(self, p):
        n = self.n
        if p == 0:
            return None
        if n == 3:
            return ((170.0, 392.0), (550.0, 392.0))[p - 1]
        return ((80.0, 255.0), (W / 2, 420.0), (W - 80.0, 255.0))[p - 1]

    def _table(self, c):
        self._rect(c, 0, 0, W, H, (0.10, 0.30, 0.22, 1))
        for k in range(8):                                     # concentric glow, kept INSIDE the panel
            self._circ(c, W / 2, H / 2, 232 - k * 28, (1, 1, 1, 0.022), 48)

    def _opponent(self, c, p):
        sx, sy = self._seat(p)
        hand = self.hands[p]
        n = len(hand)
        active = self.turn == p and self.phase == 'play'
        fl = self.flash[p]
        if active:
            self._circ(c, sx, sy, 56 + 3 * math.sin(self.time * 6), (1.0, 0.9, 0.3, 0.16), 28)
        shown = min(n, 9)
        step = 11.0 if shown > 1 else 0
        w, h = 34.0, 50.0
        total = w + step * (shown - 1)
        for i in range(shown):
            self._card_back(c, sx - total / 2 + i * step, sy - 8 - fl * 6, w, h)
        self._rr(c, sx - 45, sy - 44, 90, 22, 10, (0, 0, 0, 0.55))
        self._text(c, "%s  %d" % (NAMES[p], n), sx, sy - 33, 11, (1.0, 0.9, 0.4, 1) if active else ov.C_WHITE)
        if n == 1:
            self._text(c, "GTLP!", sx, sy + 48, 12, (1.0, 0.85, 0.2, 1))

    def _menu(self, c):
        self._table(c)
        self._text(c, "GTLP CARDS", W / 2, 442, 46, (1.0, 0.85 + 0.1 * math.sin(self.time * 2), 0.25, 1))
        for i, card in enumerate((('R', '7'), ('Y', 'S'), ('G', 'D'), ('B', 'V'), ('W', 'F'))):
            self._card_face(c, card, W / 2 - 170 + i * 72, 322 + 4 * math.sin(self.time * 2 + i), 62, 92)
        self._text(c, "Match colours or numbers - empty your hand first", W / 2, 303, 13, (1, 1, 1, 0.75))
        for i, d in enumerate(DIFFS):
            x, y, w, h = self._card_menu(i)
            hov = i == self.menu_hover
            self._rect(c, x + 3, y - 4, w, h, (0, 0, 0, 0.4))
            self._rect(c, x, y, w, h, ov.C_CELL_HOVER if hov else ov.C_CELL)
            self._rect(c, x, y, 9, h, tuple(d['col']) + (1.0,))
            self._text(c, "%d  %s" % (i + 1, d['name']), x + 24, y + h - 24, 20, ov.C_WHITE, 'left')
            self._text(c, d['info'], x + 24, y + 22, 10, ov.C_TEXT, 'left')
            bs, bt = self._best(i)
            if bs:
                self._text(c, "Best score  %d" % bs, x + w - 14, y + h - 26, 13, ov.C_GOLD, 'right')
                self._text(c, "Fastest win  %s" % _fmt(bt), x + w - 14, y + 22, 12, ov.C_GOLD, 'right')
            else:
                self._text(c, "No win yet", x + w - 14, y + h - 26, 12, ov.C_TEXT, 'right')

    def draw(self, c):
        self.draw_frame(c)
        bs, bt = self._best(self.diff)
        if self.menu:
            self.draw_header(c, "GTLP CARDS", "Best score and fastest win saved for every difficulty", ov.C_GOLD)
            self._menu(c)
            self.draw_hint(c, "Click or press 1-3 to pick a difficulty   ESC quit")
            return
        who = "your turn" if self.turn == 0 else "%s is playing" % NAMES[self.turn]
        self.draw_header(c, "GTLP CARDS - %s" % self.cfg['name'],
                         "%s   |   Time %s   |   Best %d (%s)" % (who if self.phase != 'over' else "game over",
                                                                  _fmt(self.play_t), bs, _fmt(bt)),
                         tuple(self.cfg['col']) + (1.0,))
        self._table(c)
        for p in range(1, self.n):
            self._opponent(c, p)
        # centre: colour ring with the two piles inside it (nothing overlaps)
        cc = COLORS[self.color]
        ccx, ccy = CENTER
        self._circ(c, ccx, ccy, RING_R, (cc[0], cc[1], cc[2], 0.22), 40)
        c.ring(self._X(ccx), self._Y(ccy), RING_R * self.sc, 4 * self.sc, (cc[0], cc[1], cc[2], 0.9), 48)
        dx, dy, dw, dh = self._deck_rect()
        for k in range(3):
            self._card_back(c, dx - k * 1.4, dy + k * 1.4)
        self._rr(c, dx + dw / 2 - 15, dy - 25, 30, 16, 7, (0, 0, 0, 0.55))      # pile counter pill
        self._text(c, "%d" % len(self.pile), dx + dw / 2, dy - 17, 10, ov.C_WHITE)
        if self.turn == 0 and self.phase == 'play':
            self._text(c, "DRAW" if self.drawn is None else "PASS", dx + dw / 2, dy + dh + 12, 10, ov.C_GOLD)
        top3 = self.disc[-3:]
        for k, card in enumerate(top3):
            depth = len(top3) - 1 - k                                           # top card = 0
            self._card_face(c, card, DISC_X - depth * 2.5, PILE_Y + depth * 2.0, CW, CH)
        # direction arrows (they travel along the colour ring, outside the cards)
        for sg in (-1, 1):
            a = self.time * 1.2 * self.dir + (0 if sg > 0 else math.pi)
            r = RING_R
            px, py = ccx + math.cos(a) * r, ccy + math.sin(a) * r
            ta = a + math.pi / 2 * self.dir
            self._poly(c, [(px + math.cos(ta) * 8, py + math.sin(ta) * 8),
                           (px + math.cos(ta + 2.5) * 6, py + math.sin(ta + 2.5) * 6),
                           (px + math.cos(ta - 2.5) * 6, py + math.sin(ta - 2.5) * 6)], (1, 1, 1, 0.35))
        # flying cards
        for a in self.anims:
            k = min(1.0, a['t'] / 0.4)
            e = k * k * (3 - 2 * k)
            if a['kind'] == 'play' and a['p'] != 0:
                sx, sy = self._seat(a['p'])
                x = sx + (DISC_X + CW / 2 - sx) * e
                y = sy + (PILE_Y + CH / 2 - sy) * e
                self._card_face(c, a['card'], x - CW / 2 * (0.55 + 0.45 * e), y - CH / 2 * (0.55 + 0.45 * e),
                                CW * (0.55 + 0.45 * e), CH * (0.55 + 0.45 * e))
            elif a['kind'] == 'draw' and a['p'] != 0:
                sx, sy = self._seat(a['p'])
                x = dx + CW / 2 + (sx - dx - CW / 2) * e
                y = dy + CH / 2 + (sy - dy - CH / 2) * e
                self._card_back(c, x - 17, y - 25, 34, 50)
        # your hand
        n = len(self.hands[0])
        x0, step = self._hand_geom()
        for i, card in enumerate(self.hands[0]):
            ok = self.phase == 'play' and self.turn == 0 and self._playable(card) and (self.drawn is None or i == self.drawn)
            hov = i == self.hover and ok
            lift = 16.0 if hov else (6.0 if ok and self.turn == 0 else 0.0)
            glow = (1.0, 0.9, 0.3, 0.9) if (hov or (self.drawn is not None and i == self.drawn)) else None
            self._card_face(c, card, x0 + i * step, 8 + lift, CW, CH, dim=not ok and self.turn == 0 and self.phase in ('play', 'color'), glow=glow)
        self._rr(c, W / 2 - 40, 124, 80, 20, 9, (0, 0, 0, 0.5))
        self._text(c, "You  %d" % n, W / 2, 134, 10.5, (1.0, 0.9, 0.4, 1) if self.turn == 0 and self.phase == 'play' else ov.C_WHITE)
        if self.cfg['gtlp_call'] and self.turn == 0 and n == 2 and self.phase == 'play':
            ux, uy, uw, uh = self._gtlp_rect()
            self._rr(c, ux, uy, uw, uh, 10, (0.95, 0.25 + 0.4 * (1 if self.gtlp_called else abs(math.sin(self.time * 6))), 0.2, 1))
            self._text(c, "GTLP! (G)" if not self.gtlp_called else "GTLP called", ux + uw / 2, uy + uh / 2, 13, (1, 1, 1, 1))
        # toast
        if self.msg_t > 0:
            self._rr(c, W / 2 - 190, 346, 380, 24, 10, (0, 0, 0, 0.55 * min(1.0, self.msg_t * 2)))
            self._text(c, self.msg, W / 2, 358, 12, ov.alpha(ov.C_WHITE, min(1.0, self.msg_t * 2)))
        if self.phase == 'color':
            self._rect(c, 0, 0, W, H, (0, 0, 0, 0.55))
            self._text(c, "CHOOSE A COLOUR", W / 2, 290, 22, ov.C_WHITE)
            for i, (rx, ry, rw, rh) in enumerate(self._pick_rects()):
                hov = self.mouse is not None and rx <= self.mouse[0] <= rx + rw and ry <= self.mouse[1] <= ry + rh
                self._rr(c, rx - (3 if hov else 0), ry - (3 if hov else 0), rw + (6 if hov else 0), rh + (6 if hov else 0), 10, COLORS[ORDER[i]])
                self._text(c, str(i + 1), rx + rw / 2, ry + rh / 2, 20, (1, 1, 1, 0.9))
        if self.phase == 'over':
            f = self.final
            won = f['won']
            self._rect(c, 0, 0, W, H, (0, 0, 0, 0.62))
            self._text(c, "YOU WIN!" if won else "%s WINS" % NAMES[self.winner].upper(), W / 2, 320, 50, ov.C_GOLD if won else ov.C_BAD)
            if won:
                self._text(c, "Score  %d%s" % (f['score'], "   NEW BEST!" if self.new_best_score else ""), W / 2, 266, 24, ov.C_GOLD)
                self._text(c, "Time  %s%s" % (_fmt(self.play_t), "   NEW BEST TIME!" if self.new_best_time else ""), W / 2, 234, 14,
                           ov.C_GOLD if self.new_best_time else ov.C_TEXT)
            else:
                self._text(c, "Cards left: " + "  ".join("%s %d" % (NAMES[i], len(h)) for i, h in enumerate(self.hands)), W / 2, 260, 13, ov.C_TEXT)
            self._text(c, "SPACE / click: new game     M: difficulty", W / 2, 180, 13, ov.C_TEXT)
        if self.cfg['gtlp_call']:
            self.draw_hint(c, "Click a card   deck / SPACE: draw or pass   G: call GTLP   N new   M menu")
        else:
            self.draw_hint(c, "Click a card   deck / SPACE: draw or pass   N new   M menu   R restart")


RUNNER = ov.Runner("unocards", GAME_NAME, GtlpCards, [
    "Click a card to play it (matching colour or number)",
    "No match? Click the deck / SPACE to draw",
    "Skip, Reverse, +2, Wild, Wild +4 work as in GTLP",
    "Hard: press G before your second-last card",
    "Best score + fastest win saved per difficulty",
    "M: difficulty   N: new game   ESC: quit",
])


def draw_ui(layout, context):
    RUNNER.draw_ui(layout)


def register():
    RUNNER.register()


def unregister():
    RUNNER.unregister()
