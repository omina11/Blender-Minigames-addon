"""Blackjack - you vs the dealer (drawn as a GPU overlay in the 3D Viewport).

6-deck shoe, dealer stands on all 17, blackjack pays 3:2, double down on any
two cards (also after a split), split up to 4 hands (split aces get one card),
insurance when the dealer shows an Ace.
You start with $1000: go broke and you lose, reach $5000 (or cash out in
profit) and you win. A basic-strategy hint can be switched on with T.
"""

import math
import random

from . import _overlay as ov
from . import _record as _rec

GAME_NAME = "Blackjack"
GAME_ICON = 'MESH_PLANE'

START_BANK = 1000
TARGET = 5000               # "break the bank"
MIN_BET = 10
MAX_BET = 500
CHIPS = (10, 50, 100, 500)
DECKS = 6
CUT = 78                    # reshuffle before a hand when fewer cards are left
W, H = 720, 520             # design size (scaled to the viewport)

C_FELT = (0.06, 0.34, 0.20, 1.0)
C_FELT_EDGE = (0.30, 0.18, 0.09, 1.0)
C_FELT_TXT = (0.42, 0.66, 0.50, 1.0)
C_CARD = (0.97, 0.96, 0.92, 1.0)
C_CARD_EDGE = (0.20, 0.20, 0.24, 1.0)
C_RED = (0.82, 0.12, 0.16, 1.0)
C_BLACK = (0.10, 0.10, 0.13, 1.0)
C_BACK = (0.15, 0.25, 0.60, 1.0)
C_DIM = (0.45, 0.47, 0.52, 1.0)
C_BTN_OFF = (0.17, 0.18, 0.21, 1.0)
C_HIT = (0.20, 0.50, 0.30, 1.0)
C_STAND = (0.58, 0.20, 0.20, 1.0)
C_SPECIAL = (0.62, 0.46, 0.10, 1.0)
C_CHIP = {10: (0.20, 0.45, 0.85, 1.0), 50: (0.80, 0.20, 0.20, 1.0),
          100: (0.13, 0.13, 0.15, 1.0), 500: (0.55, 0.25, 0.70, 1.0)}

# ----------------------------------------------------------------------------
# Cards and rules (pure Python)
# card = 0..51 ; rank = card % 13 (0 = '2' ... 12 = Ace) ; suit = card // 13
# (0 spades, 1 hearts, 2 diamonds, 3 clubs)
# ----------------------------------------------------------------------------

RANKS = ('2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A')


def card_val(c):
    r = c % 13
    return 11 if r == 12 else min(r + 2, 10)


def hand_value(cards):
    """(best total, is_soft)."""
    tot, aces = 0, 0
    for c in cards:
        v = card_val(c)
        tot += v
        if v == 11:
            aces += 1
    while tot > 21 and aces:
        tot -= 10
        aces -= 1
    return tot, aces > 0


def is_natural(cards):
    return len(cards) == 2 and hand_value(cards)[0] == 21


def advice(cards, up, can_double, can_split):
    """Basic strategy (6 decks, dealer stands on 17, double after split).

    up = dealer upcard value (2..11). Returns HIT / STAND / DOUBLE / SPLIT.
    """
    tot, soft = hand_value(cards)
    vals = [card_val(c) for c in cards]
    if can_split and len(cards) == 2 and vals[0] == vals[1]:
        p = vals[0]
        if p in (11, 8):
            return 'SPLIT'
        if p == 9:
            return 'SPLIT' if up in (2, 3, 4, 5, 6, 8, 9) else 'STAND'
        if p == 7 and up <= 7:
            return 'SPLIT'
        if p == 6 and up <= 6:
            return 'SPLIT'
        if p == 4 and up in (5, 6):
            return 'SPLIT'
        if p in (2, 3) and up <= 7:
            return 'SPLIT'
    if soft:
        if tot >= 19:
            return 'STAND'
        if tot == 18:
            if 3 <= up <= 6:
                return 'DOUBLE' if can_double else 'STAND'
            return 'STAND' if up in (2, 7, 8) else 'HIT'
        if tot == 17:
            a = 'DOUBLE' if 3 <= up <= 6 else 'HIT'
        elif tot in (15, 16):
            a = 'DOUBLE' if 4 <= up <= 6 else 'HIT'
        else:
            a = 'DOUBLE' if 5 <= up <= 6 else 'HIT'
    else:
        if tot >= 17:
            return 'STAND'
        if tot >= 13:
            return 'STAND' if up <= 6 else 'HIT'
        if tot == 12:
            return 'STAND' if 4 <= up <= 6 else 'HIT'
        if tot == 11:
            a = 'DOUBLE' if up <= 10 else 'HIT'
        elif tot == 10:
            a = 'DOUBLE' if up <= 9 else 'HIT'
        elif tot == 9:
            a = 'DOUBLE' if 3 <= up <= 6 else 'HIT'
        else:
            a = 'HIT'
    if a == 'DOUBLE' and not can_double:
        a = 'HIT'
    return a


class Hand:
    def __init__(self, bet):
        self.cards = []
        self.bet = bet
        self.done = False
        self.split = False
        self.split_aces = False
        self.result = ""


# ----------------------------------------------------------------------------
# Game (overlay)
# ----------------------------------------------------------------------------

class Blackjack(ov.BaseGame):
    keys = ('H', 'S', 'D', 'P', 'I', 'N', 'C', 'T', 'SPACE', 'RET', 'NUMPAD_ENTER',
            'ONE', 'TWO', 'THREE', 'FOUR', 'BACK_SPACE')

    def setup(self):
        self.k = 1.0
        self.base = 0.0
        self.hint = False                   # basic-strategy tip (survives restarts)
        self.wins = self.losses = 0
        try:
            self.record_mgr = _rec.RecordManager(game_name="blackjack")
            rec = self.record_mgr.get_records()
            self.wins = int(rec.get("wins", 0))
            self.losses = int(rec.get("losses", 0))
        except Exception:
            self.record_mgr = None

    def reset(self):
        """New session."""
        self.bankroll = START_BANK
        self.bet = 50
        self.last_bet = 50
        self.hands = []
        self.dealer = []
        self.hole_hidden = True
        self.active = 0
        self.insurance = 0
        self.round_start = START_BANK
        self.hands_played = 0
        self.hands_won = 0
        self.timer = 0.0
        self.queue = []
        self.recorded = False
        self.match_won = False
        self.hover = None
        self.buttons = []
        self.state = 'betting'
        self.msg = ("Place your bet", ov.C_GOLD)
        self._new_shoe()

    # ------------------------------------------------------------------ shoe
    def _new_shoe(self):
        self.shoe = list(range(52)) * DECKS
        random.shuffle(self.shoe)

    def _draw(self):
        if not self.shoe:
            self._new_shoe()
        return self.shoe.pop()

    # ------------------------------------------------------------------ flow
    def _deal_start(self):
        cap = min(MAX_BET, self.bankroll)
        if not (MIN_BET <= self.bet <= cap):
            return
        if len(self.shoe) < CUT:
            self._new_shoe()
        self.round_start = self.bankroll
        self.bankroll -= self.bet
        self.last_bet = self.bet
        self.hands = [Hand(self.bet)]
        self.dealer = []
        self.hole_hidden = True
        self.insurance = 0
        self.queue = ['p', 'd', 'p', 'd']
        self.state = 'deal'
        self.timer = 0.2
        self.msg = ("", ov.C_TEXT)

    def _after_deal(self):
        h = self.hands[0]
        if card_val(self.dealer[0]) == 11 and self.bankroll >= h.bet // 2:
            self.state = 'insurance'
            self.msg = ("Dealer shows an Ace - insurance for %d?" % (h.bet // 2), ov.C_GOLD)
        else:
            self._check_naturals()

    def _take_insurance(self, take):
        if self.state != 'insurance':
            return
        if take:
            self.insurance = self.hands[0].bet // 2
            self.bankroll -= self.insurance
        self._check_naturals()

    def _check_naturals(self):
        """Dealer peeks for blackjack; player blackjack pays 3:2."""
        h = self.hands[0]
        pn, dn = is_natural(h.cards), is_natural(self.dealer)
        if dn:
            self.hole_hidden = False
            if self.insurance:
                self.bankroll += self.insurance * 3
            if pn:
                self.bankroll += h.bet
                h.result = "PUSH"
            else:
                h.result = "LOSE -%d" % h.bet
            self._to_result("Dealer has blackjack")
        elif pn:
            self.hole_hidden = False
            win = h.bet * 3 // 2
            self.bankroll += h.bet + win
            h.result = "BLACKJACK +%d" % win
            self._to_result("Blackjack!")
        else:
            self._start_player()

    def _start_player(self):
        self.active = 0
        self.state = 'player'
        self._player_msg()

    def _player_msg(self):
        n = len(self.hands)
        self.msg = ("Your move" if n == 1 else "Hand %d of %d" % (self.active + 1, n),
                    ov.C_GOLD)

    def _advance(self):
        for i, h in enumerate(self.hands):
            if not h.done:
                self.active = i
                self.state = 'player'
                self._player_msg()
                return
        self._start_dealer()

    def _start_dealer(self):
        self.state = 'dealer'
        self.hole_hidden = False
        self.timer = 0.8
        self.msg = ("Dealer plays...", ov.C_TEXT)

    def _dealer_draws(self):
        live = any(hand_value(h.cards)[0] <= 21 for h in self.hands)
        return live and hand_value(self.dealer)[0] < 17

    def _settle(self):
        dt = hand_value(self.dealer)[0]
        for h in self.hands:
            pt = hand_value(h.cards)[0]
            if pt > 21:
                h.result = "BUST -%d" % h.bet
            elif dt > 21 or pt > dt:
                h.result = "WIN +%d" % h.bet
                self.bankroll += 2 * h.bet
            elif pt == dt:
                h.result = "PUSH"
                self.bankroll += h.bet
            else:
                h.result = "LOSE -%d" % h.bet
        self._to_result("Dealer busts" if dt > 21 else "")

    def _to_result(self, prefix=""):
        diff = self.bankroll - self.round_start
        self.state = 'result'
        self.hands_played += 1
        if diff > 0:
            self.hands_won += 1
            txt, col = "You win %d" % diff, ov.C_GOOD
        elif diff < 0:
            txt, col = "You lose %d" % -diff, ov.C_BAD
        else:
            txt, col = "Push", ov.C_TEXT
        if prefix:
            txt = prefix + " - " + txt
        self.msg = (txt, col)

    def _next_hand(self):
        if self.bankroll < MIN_BET:
            return self._match_end(False, "You are out of chips")
        if self.bankroll >= TARGET:
            return self._match_end(True, "You broke the bank!")
        self.hands = []
        self.dealer = []
        self.state = 'betting'
        self.bet = min(self.last_bet, self.bankroll, MAX_BET)
        self.msg = ("Place your bet", ov.C_GOLD)
        if len(self.shoe) < CUT:
            self._new_shoe()
            self.msg = ("Shuffling the shoe - place your bet", ov.C_GOLD)

    def _match_end(self, won, text):
        self.state = 'match_over'
        self.match_won = won
        self.msg = (text + "  ($%d)" % self.bankroll, ov.C_GOLD if won else ov.C_BAD)
        if self.record_mgr and not self.recorded:
            self.recorded = True
            try:
                rec = (self.record_mgr.add_win_record(score=self.bankroll)
                       if won else self.record_mgr.add_lose_record())
                self.wins, self.losses = rec["wins"], rec["losses"]
            except Exception:
                pass

    # ------------------------------------------------------------------ actions
    def _can_double(self, h):
        return len(h.cards) == 2 and not h.split_aces and self.bankroll >= h.bet

    def _can_split(self, h):
        return (len(h.cards) == 2 and len(self.hands) < 4 and not h.split_aces
                and card_val(h.cards[0]) == card_val(h.cards[1])
                and self.bankroll >= h.bet)

    def _hit(self):
        h = self.hands[self.active]
        h.cards.append(self._draw())
        if hand_value(h.cards)[0] >= 21:
            h.done = True
            self._advance()

    def _stand(self):
        self.hands[self.active].done = True
        self._advance()

    def _double(self):
        h = self.hands[self.active]
        if not self._can_double(h):
            return
        self.bankroll -= h.bet
        h.bet *= 2
        h.cards.append(self._draw())
        h.done = True
        self._advance()

    def _split(self):
        h = self.hands[self.active]
        if not self._can_split(h):
            return
        self.bankroll -= h.bet
        new = Hand(h.bet)
        new.split = h.split = True
        new.cards.append(h.cards.pop())
        h.cards.append(self._draw())
        new.cards.append(self._draw())
        self.hands.insert(self.active + 1, new)
        if card_val(h.cards[0]) == 11:                 # split aces: one card each
            h.split_aces = new.split_aces = True
            h.done = new.done = True
        else:
            for x in (h, new):
                if hand_value(x.cards)[0] == 21:
                    x.done = True
        self._advance()

    def _add_chip(self, amt):
        if self.state == 'betting':
            self.bet = min(self.bet + amt, MAX_BET, self.bankroll)

    def _do(self, a):
        st = self.state
        if a == 'hint':
            self.hint = not self.hint
            return
        if st == 'betting':
            if a == 'deal':
                self._deal_start()
            elif a == 'clear':
                self.bet = 0
            elif a == 'rebet':
                self.bet = min(self.last_bet, MAX_BET, self.bankroll)
            elif a == 'cash':
                self._match_end(self.bankroll > START_BANK,
                                "You cash out" if self.bankroll > START_BANK
                                else "You leave the table")
            elif a.startswith('chip'):
                self._add_chip(int(a[4:]))
        elif st == 'player':
            h = self.hands[self.active]
            if a == 'hit':
                self._hit()
            elif a == 'stand':
                self._stand()
            elif a == 'double':
                self._double()
            elif a == 'split':
                self._split()
        elif st == 'insurance':
            if a == 'ins':
                self._take_insurance(True)
            elif a == 'noins':
                self._take_insurance(False)
        elif st == 'result' and a == 'next':
            self._next_hand()
        elif st == 'match_over' and a == 'new':
            self.reset()

    # ------------------------------------------------------------------ input
    def key(self, key, repeat):
        if repeat:
            return
        st = self.state
        if key == 'T':
            self._do('hint')
        elif key in ('SPACE', 'RET', 'NUMPAD_ENTER'):
            self._do({'betting': 'deal', 'result': 'next', 'match_over': 'new'}.get(st, ''))
        elif st == 'betting':
            idx = {'ONE': 0, 'TWO': 1, 'THREE': 2, 'FOUR': 3}.get(key)
            if idx is not None:
                self._do('chip%d' % CHIPS[idx])
            elif key == 'BACK_SPACE':
                self._do('clear')
            elif key == 'C':
                self._do('cash')
        elif st == 'player':
            m = {'H': 'hit', 'S': 'stand', 'D': 'double', 'P': 'split'}.get(key)
            if m:
                self._do(m)
        elif st == 'insurance':
            if key == 'I':
                self._do('ins')
            elif key == 'N':
                self._do('noins')

    def _pick(self, x, y):
        for bid, bx, by, bw, bh, _l, _f, en, _s in self.buttons:
            if bid and en and bx <= x <= bx + bw and by <= y <= by + bh:
                return bid
        return None

    def click(self, x, y, button):
        if button != 'LEFT':
            return
        bid = self._pick(x, y)
        if bid:
            self._do(bid)

    def move(self, x, y):
        h = self._pick(x, y)
        if h != self.hover:
            self.hover = h
            return True
        return False

    def update(self, dt):
        if self.state == 'deal':
            self.timer -= dt
            if self.timer <= 0:
                who = self.queue.pop(0)
                (self.hands[0].cards if who == 'p' else self.dealer).append(self._draw())
                self.timer = 0.3
                if not self.queue:
                    self._after_deal()
                return True
        elif self.state == 'dealer':
            self.timer -= dt
            if self.timer <= 0:
                if self._dealer_draws():
                    self.dealer.append(self._draw())
                    self.timer = 0.75
                else:
                    self._settle()
                return True
        return False

    # ------------------------------------------------------------------ layout
    def _x(self, dx):
        return self.left + dx * self.k

    def _y(self, dy):
        return self.base + dy * self.k

    def layout(self, view):
        u = view.u
        k = min(u, (view.w - 24 * u) / W, (view.h - 112 * u) / H)
        self.k = max(0.45 * u, k)
        self.place(view, W * self.k, H * self.k, min_w=0)
        self.base = self.top - H * self.k
        self._layout_buttons()

    def _layout_buttons(self):
        k = self.k
        out = []

        def add(bid, dx, dy, w, h, label, face, enabled=True, size=13):
            out.append((bid, self._x(dx), self._y(dy), w * k, h * k,
                        label, face, enabled, size * k))
        st = self.state
        if st == 'betting':
            cap = min(MAX_BET, self.bankroll)
            add('cash', 40, 8, 120, 34, "Cash out (C)", C_STAND)
            add('rebet', 170, 8, 100, 34, "Rebet", ov.C_CELL, self.bet != min(self.last_bet, cap))
            add('deal', 280, 8, 200, 34, "Deal  (SPACE)", C_HIT, MIN_BET <= self.bet <= cap, 14)
            add('clear', 80, 48, 80, 26, "Clear", ov.C_CELL, self.bet > 0, 12)
            for n, amt in enumerate(CHIPS):
                add('chip%d' % amt, 170 + n * 90, 48, 80, 26, "+%d  (%d)" % (amt, n + 1),
                    ov.C_CELL, self.bet < cap, 12)
        elif st == 'player':
            h = self.hands[self.active]
            add('hit', 70, 8, 110, 34, "Hit  (H)", C_HIT)
            add('stand', 190, 8, 110, 34, "Stand  (S)", C_STAND)
            add('double', 310, 8, 120, 34, "Double  (D)", C_SPECIAL, self._can_double(h))
            add('split', 440, 8, 110, 34, "Split  (P)", C_SPECIAL, self._can_split(h))
        elif st == 'insurance':
            add('ins', 150, 8, 190, 34, "Insurance  (I)", C_SPECIAL)
            add('noins', 350, 8, 190, 34, "No thanks  (N)", ov.C_CELL)
        elif st == 'result':
            add('next', 250, 8, 220, 34, "Next hand  (SPACE)", C_HIT, True, 14)
        elif st == 'match_over':
            add('new', 250, 8, 220, 34, "New session  (SPACE)", C_SPECIAL, True, 14)
        else:
            add(None, 250, 8, 220, 34, "...", C_BTN_OFF, False, 14)
        if st != 'match_over':
            add('hint', 596, 8, 114, 34, "Hint: %s  (T)" % ("on" if self.hint else "off"),
                ov.C_CELL, True, 11)
        self.buttons = out

    # ------------------------------------------------------------------ drawing
    def _rr(self, c, x, y, w, h, ch, col):
        c.poly([(x + ch, y), (x + w - ch, y), (x + w, y + ch), (x + w, y + h - ch),
                (x + w - ch, y + h), (x + ch, y + h), (x, y + h - ch), (x, y + ch)], col)

    def _suit(self, c, s, cx, cy, r, col):
        if s == 2:                                                  # diamond
            c.poly([(cx, cy + 0.58 * r), (cx + 0.40 * r, cy),
                    (cx, cy - 0.58 * r), (cx - 0.40 * r, cy)], col)
        elif s == 1:                                                # heart
            c.circle(cx - 0.235 * r, cy + 0.17 * r, 0.28 * r, col, 14)
            c.circle(cx + 0.235 * r, cy + 0.17 * r, 0.28 * r, col, 14)
            c.tri((cx - 0.49 * r, cy + 0.12 * r), (cx + 0.49 * r, cy + 0.12 * r),
                  (cx, cy - 0.52 * r), col)
        elif s == 0:                                                # spade
            c.circle(cx - 0.235 * r, cy - 0.12 * r, 0.28 * r, col, 14)
            c.circle(cx + 0.235 * r, cy - 0.12 * r, 0.28 * r, col, 14)
            c.tri((cx - 0.49 * r, cy - 0.08 * r), (cx + 0.49 * r, cy - 0.08 * r),
                  (cx, cy + 0.55 * r), col)
            c.tri((cx - 0.13 * r, cy - 0.55 * r), (cx + 0.13 * r, cy - 0.55 * r),
                  (cx, cy - 0.15 * r), col)
        else:                                                       # club
            for dx, dy in ((0, 0.25), (-0.27, -0.10), (0.27, -0.10)):
                c.circle(cx + dx * r, cy + dy * r, 0.25 * r, col, 14)
            c.tri((cx - 0.14 * r, cy - 0.55 * r), (cx + 0.14 * r, cy - 0.55 * r),
                  (cx, cy - 0.05 * r), col)

    def _card(self, c, x0, y0, w, h, strip, card=None):
        """Card with its lower-left corner at pixel (x0, y0); w/h/strip in pixels.

        Cards in a hand overlap, so the rank (text is always drawn on top of the
        geometry) lives in the left `strip` that the next card never covers.
        """
        u = self.u
        ch = min(w, h) * 0.10
        self._rr(c, x0 - 1, y0 - 1, w + 2, h + 2, ch, C_CARD_EDGE)
        if card is None:
            self._rr(c, x0, y0, w, h, ch, C_BACK)
            m = w * 0.12
            self._rr(c, x0 + m, y0 + m, w - 2 * m, h - 2 * m, ch * 0.6,
                     ov.shade(C_BACK, 1.6))
            cx, cy = x0 + w / 2, y0 + h / 2
            c.poly([(cx, cy + h * 0.26), (cx + w * 0.22, cy),
                    (cx, cy - h * 0.26), (cx - w * 0.22, cy)], ov.shade(C_BACK, 0.7))
            return
        self._rr(c, x0, y0, w, h, ch, C_CARD)
        s = card // 13
        col = C_RED if s in (1, 2) else C_BLACK
        sx = x0 + min(strip, w) / 2
        c.text(RANKS[card % 13], sx, y0 + h * 0.83, h * 0.21, col)
        self._suit(c, s, sx, y0 + h * 0.60, h * 0.10, col)
        self._suit(c, s, x0 + w * 0.64, y0 + h * 0.36, h * 0.22, col)

    def _chip(self, c, cx, cy, r, denom):
        col = C_CHIP[denom]
        c.circle(cx, cy, r, ov.shade(col, 0.6), 20)
        c.circle(cx, cy, r * 0.92, col, 20)
        c.ring(cx, cy, r * 0.72, max(1.0, r * 0.10), (0.95, 0.95, 0.95, 1.0), 20)
        c.text(str(denom), cx, cy, r * 0.62, ov.C_WHITE)

    def _hand_cards(self, c, cards, left, cy, cw, chh, step, hide_second=False):
        k = self.k
        x0 = self._x(left)
        y0 = self._y(cy) - chh * k / 2
        for i, card in enumerate(cards):
            back = hide_second and i == 1
            self._card(c, x0 + i * step * k, y0, cw * k, chh * k, step * k,
                       None if back else card)

    def draw(self, c):
        k, u = self.k, self.u
        X, Y = self._x, self._y
        self.draw_frame(c)
        sub = "Bankroll $%d  |  Goal $%d  |  Record %dW - %dL" % (
            self.bankroll, TARGET, self.wins, self.losses)
        self.draw_header(c, "Blackjack", sub, ov.C_GOLD)

        # --- felt
        self._rr(c, X(14), Y(92), 692 * k, 420 * k, 18 * k, C_FELT_EDGE)
        self._rr(c, X(20), Y(98), 680 * k, 408 * k, 14 * k, C_FELT)
        c.text("BLACKJACK PAYS 3 TO 2   -   DEALER STANDS ON 17   -   INSURANCE PAYS 2 TO 1",
               X(360), Y(333), 11 * k, C_FELT_TXT)

        # --- dealer
        dcards = self.dealer
        if dcards:
            n = len(dcards)
            cw, chh, step = 58, 84, 30
            left = 360 - (cw + step * (n - 1)) / 2.0
            self._hand_cards(c, dcards, left, 398, cw, chh, step,
                             hide_second=self.hole_hidden and n >= 2)
            if self.hole_hidden:
                label = "Dealer"
            else:
                dv, soft = hand_value(dcards)
                label = "Dealer  " + ("Blackjack" if is_natural(dcards)
                                      else "Bust" if dv > 21 else str(dv))
            c.text(label, X(360), Y(398 + 42 + 16), 14 * k, ov.C_WHITE)
        if self.insurance:
            c.text("Insurance: %d" % self.insurance, X(360), Y(318), 11 * k, ov.C_GOLD)

        # --- shoe
        self._card(c, X(628), Y(400), 44 * k, 62 * k, 44 * k, None)
        c.text("Shoe: %d" % len(self.shoe), X(650), Y(384), 11 * k, ov.C_TEXT)

        # --- message / hint
        c.text(self.msg[0], X(360), Y(300), 16 * k, self.msg[1])
        if self.hint and self.state == 'player' and self.dealer:
            h = self.hands[self.active]
            tip = advice(h.cards, card_val(self.dealer[0]),
                         self._can_double(h), self._can_split(h))
            c.text("Basic strategy: %s" % tip.capitalize(), X(360), Y(278), 12 * k, ov.C_GOLD)

        # --- player hands
        nh = len(self.hands)
        if nh:
            cw, chh, smax, smin = (58, 84, 30, 22) if nh <= 2 else (48, 70, 24, 18)
            spacing = min(240.0, 680.0 / nh)
            cy = 215
            for i, h in enumerate(self.hands):
                n = len(h.cards)
                step = smax
                if n > 1:
                    step = max(smin, min(smax, (spacing - 14 - cw) / (n - 1)))
                tw = cw + step * (n - 1)
                cx = 360 + (i - (nh - 1) / 2.0) * spacing
                self._hand_cards(c, h.cards, cx - tw / 2, cy, cw, chh, step)
                bottom = cy - chh / 2
                active = self.state == 'player' and i == self.active
                if active:
                    c.rect(X(cx - tw / 2), Y(bottom - 6), tw * k, 3 * k, ov.C_GOLD)
                tot, soft = hand_value(h.cards)
                if is_natural(h.cards) and not h.split:
                    t = "Blackjack"
                elif tot > 21:
                    t = "Bust"
                elif soft and n > 1:
                    t = "Soft %d" % tot
                else:
                    t = str(tot)
                c.text(t, X(cx), Y(bottom - 18), 14 * k, ov.C_GOLD if active else ov.C_WHITE)
                if h.result:
                    r = h.result
                    col = ov.C_GOOD if r.startswith(("WIN", "BLACK")) else \
                        ov.C_TEXT if r == "PUSH" else ov.C_BAD
                    c.text(r, X(cx), Y(bottom - 36), 13 * k, col)
                c.circle(X(cx - 22), Y(bottom - 54), 7 * k, ov.C_GOLD)
                c.text(str(h.bet), X(cx - 10), Y(bottom - 54), 12 * k, ov.C_WHITE, 'left')
        elif self.state == 'betting':
            # chips of the bet being built
            amt, chips = self.bet, []
            for d in reversed(CHIPS):
                while amt >= d:
                    chips.append(d)
                    amt -= d
            sp = 38.0
            for i, d in enumerate(chips):
                self._chip(c, X(360 + (i - (len(chips) - 1) / 2.0) * sp), Y(215), 17 * k, d)
            c.text("Bet: $%d" % self.bet, X(360), Y(160), 20 * k, ov.C_GOLD)

        # --- buttons
        b = max(1.0, 2 * u)
        for bid, x, y, w, h, label, face, en, size in self.buttons:
            f = face if en else C_BTN_OFF
            if en and bid is not None and bid == self.hover:
                f = ov.shade(f, 1.25)
            c.bevel(x, y, w, h, b, f)
            c.text(label, x + w / 2, y + h / 2, size, ov.C_WHITE if en else C_DIM)

        self.draw_hint(c, "H hit   S stand   D double   P split   T hint   "
                          "SPACE deal / next   R new session   ESC quit")


RUNNER = ov.Runner("blackjack", GAME_NAME, Blackjack, [
    "Blackjack vs the dealer ($%d to start, goal $%d)" % (START_BANK, TARGET),
    "Bet with the chip buttons (or 1-4), SPACE deals",
    "H hit   S stand   D double   P split",
    "I insurance   N no insurance   T basic-strategy hint",
    "C cash out   R new session   ESC quit",
])


def draw_ui(layout, context):
    RUNNER.draw_ui(layout)


def register():
    RUNNER.register()


def unregister():
    RUNNER.unregister()
