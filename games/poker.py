"""Poker - Texas Hold'em: you vs 3 bots (drawn as a GPU overlay in the 3D Viewport).

Two hole cards each, five community cards in the middle (flop / turn / river),
blinds that grow every few hands, side pots, all-in run-outs.
The match ends when you are out of chips or you have busted all the bots.
"""

import math
import random
from itertools import combinations

from . import _overlay as ov
from . import _record as _rec

GAME_NAME = "Poker"
GAME_ICON = 'DUPLICATE'

START_CHIPS = 1000
BLINDS = [(10, 20), (20, 40), (30, 60), (50, 100),
          (75, 150), (100, 200), (150, 300), (200, 400)]
HANDS_PER_LEVEL = 5         # blinds go up every N hands
AI_DELAY = (0.7, 1.3)       # seconds a bot "thinks"
STREET_DELAY = 1.0          # seconds between cards in an all-in run-out
SHOWDOWN_DELAY = 1.2

W, H = 720, 540             # design size of the table (scaled to the viewport)

C_FELT = (0.06, 0.34, 0.20, 1.0)
C_FELT_EDGE = (0.30, 0.18, 0.09, 1.0)
C_FELT_LINE = (0.10, 0.46, 0.28, 1.0)
C_CARD = (0.97, 0.96, 0.92, 1.0)
C_CARD_EDGE = (0.20, 0.20, 0.24, 1.0)
C_RED = (0.82, 0.12, 0.16, 1.0)
C_BLACK = (0.10, 0.10, 0.13, 1.0)
C_BACK = (0.15, 0.25, 0.60, 1.0)
C_PLATE = (0.09, 0.10, 0.13, 0.96)
C_PLATE_OFF = (0.07, 0.07, 0.09, 0.90)
C_DIM = (0.45, 0.47, 0.52, 1.0)
C_BTN_OFF = (0.17, 0.18, 0.21, 1.0)
C_FOLD = (0.58, 0.20, 0.20, 1.0)
C_CALL = (0.20, 0.50, 0.30, 1.0)
C_RAISE = (0.62, 0.46, 0.10, 1.0)

STAGES = ("Pre-flop", "Flop", "Turn", "River")

# seats: 0 = you (bottom), then clockwise. Coordinates in design units (origin bottom-left).
SEATS = [
    {'plate': (360, 95), 'cards': (360, 165), 'bet': (440, 160)},
    {'plate': (85, 340), 'cards': (85, 282), 'bet': (175, 282)},
    {'plate': (360, 500), 'cards': (360, 440), 'bet': (445, 440)},
    {'plate': (635, 340), 'cards': (635, 282), 'bet': (545, 282)},
]

# ----------------------------------------------------------------------------
# Cards and hand evaluation (pure Python)
# card = 0..51 ; rank = card % 13 + 2 (2..14) ; suit = card // 13 (0 S, 1 H, 2 D, 3 C)
# ----------------------------------------------------------------------------

RANK_LABEL = {10: '10', 11: 'J', 12: 'Q', 13: 'K', 14: 'A'}
SING = {2: 'Two', 3: 'Three', 4: 'Four', 5: 'Five', 6: 'Six', 7: 'Seven', 8: 'Eight',
        9: 'Nine', 10: 'Ten', 11: 'Jack', 12: 'Queen', 13: 'King', 14: 'Ace'}
PLURAL = {2: 'Twos', 3: 'Threes', 4: 'Fours', 5: 'Fives', 6: 'Sixes', 7: 'Sevens',
          8: 'Eights', 9: 'Nines', 10: 'Tens', 11: 'Jacks', 12: 'Queens',
          13: 'Kings', 14: 'Aces'}


def rank_label(card):
    r = card % 13 + 2
    return RANK_LABEL.get(r, str(r))


def eval5(cards):
    """Score of 5 cards: (category, tiebreakers) - higher tuple wins."""
    ranks = sorted((c % 13 + 2 for c in cards), reverse=True)
    flush = len({c // 13 for c in cards}) == 1
    uniq = sorted(set(ranks), reverse=True)
    straight = 0
    if len(uniq) == 5:
        if uniq[0] - uniq[4] == 4:
            straight = uniq[0]
        elif uniq == [14, 5, 4, 3, 2]:
            straight = 5
    counts = {}
    for r in ranks:
        counts[r] = counts.get(r, 0) + 1
    groups = sorted(counts.items(), key=lambda kv: (kv[1], kv[0]), reverse=True)
    pattern = [g[1] for g in groups]
    tb = tuple(g[0] for g in groups)
    if straight and flush:
        return (8, (straight,))
    if pattern[0] == 4:
        return (7, tb)
    if pattern == [3, 2]:
        return (6, tb)
    if flush:
        return (5, tb)
    if straight:
        return (4, (straight,))
    if pattern[0] == 3:
        return (3, tb)
    if pattern[:2] == [2, 2]:
        return (2, tb)
    if pattern[0] == 2:
        return (1, tb)
    return (0, tb)


def best_score(cards):
    return max(eval5(c) for c in combinations(cards, 5))


def best_hand(cards):
    """(score, the 5 cards that make it) for 5-7 cards."""
    best = None
    for combo in combinations(cards, 5):
        sc = eval5(combo)
        if best is None or sc > best[0]:
            best = (sc, combo)
    return best


def describe(score):
    cat, tb = score
    if cat == 8:
        return "Royal Flush" if tb[0] == 14 else "Straight Flush, %s high" % SING[tb[0]]
    if cat == 7:
        return "Four of a Kind, %s" % PLURAL[tb[0]]
    if cat == 6:
        return "Full House, %s full of %s" % (PLURAL[tb[0]], PLURAL[tb[1]])
    if cat == 5:
        return "Flush, %s high" % SING[tb[0]]
    if cat == 4:
        return "Straight, %s high" % SING[tb[0]]
    if cat == 3:
        return "Three of a Kind, %s" % PLURAL[tb[0]]
    if cat == 2:
        return "Two Pair, %s and %s" % (PLURAL[tb[0]], PLURAL[tb[1]])
    if cat == 1:
        return "Pair of %s" % PLURAL[tb[0]]
    return "High card %s" % SING[tb[0]]


# ----------------------------------------------------------------------------
# Bot brain
# ----------------------------------------------------------------------------

def chen(hole):
    """Chen formula: quick pre-flop hand score (0..20)."""
    r1, r2 = sorted((c % 13 + 2 for c in hole), reverse=True)
    suited = hole[0] // 13 == hole[1] // 13

    def pts(r):
        return {14: 10, 13: 8, 12: 7, 11: 6}.get(r, r / 2.0)
    sc = pts(r1)
    if r1 == r2:
        sc = max(5, sc * 2)
    else:
        if suited:
            sc += 2
        gap = r1 - r2 - 1
        sc -= {0: 0, 1: 1, 2: 2, 3: 4}.get(gap, 5)
        if gap <= 1 and r1 < 12:
            sc += 1
    return max(0, min(20, sc))


def equity(hole, board, n_opp, sims):
    """Monte Carlo win probability against n_opp random hands."""
    known = set(hole) | set(board)
    deck = [c for c in range(52) if c not in known]
    need = 5 - len(board)
    score = 0.0
    for _ in range(sims):
        draw = random.sample(deck, need + 2 * n_opp)
        full = board + draw[:need]
        mine = best_score(hole + full)
        top = None
        for j in range(n_opp):
            o = draw[need + 2 * j: need + 2 * j + 2]
            sc = best_score(o + full)
            if top is None or sc > top:
                top = sc
        if top is None or mine > top:
            score += 1
        elif mine == top:
            score += 0.5
    return score / sims


# ----------------------------------------------------------------------------
# Players
# ----------------------------------------------------------------------------

class Player:
    def __init__(self, name, aggr=0.5, tight=0.5, human=False):
        self.name, self.aggr, self.tight, self.human = name, aggr, tight, human
        self.chips = START_CHIPS
        self.out = False
        self.clear_hand()

    def clear_hand(self):
        self.hole = []
        self.bet = 0            # chips put in during the current betting round
        self.total = 0          # chips put in during the whole hand
        self.folded = False
        self.allin = False
        self.acted = False
        self.act_txt = ""
        self.score = None
        self.won = 0


# ----------------------------------------------------------------------------
# Game (overlay)
# ----------------------------------------------------------------------------

class Poker(ov.BaseGame):
    keys = ('F', 'C', 'B', 'A', 'UP_ARROW', 'DOWN_ARROW', 'SPACE', 'RET', 'NUMPAD_ENTER')

    def setup(self):
        self.k = 1.0
        self.base = 0.0
        self.wins = self.losses = 0
        try:
            self.record_mgr = _rec.RecordManager(game_name="poker")
            rec = self.record_mgr.get_records()
            self.wins = int(rec.get("wins", 0))
            self.losses = int(rec.get("losses", 0))
        except Exception:
            self.record_mgr = None

    def reset(self):
        """New match."""
        self.players = [Player("You", human=True),
                        Player("Marco", aggr=0.75, tight=0.35),
                        Player("Giulia", aggr=0.45, tight=0.75),
                        Player("Luca", aggr=0.60, tight=0.15)]
        self.dealer = random.randrange(len(self.players))
        self.hand_no = 0
        self.biggest = 0            # biggest pot you won in this match
        self.recorded = False
        self.buttons = []
        self.hover = None
        self.raise_to = 0
        self.timer = 0.0
        self.state = 'ai'
        self.new_hand()

    # ------------------------------------------------------------------ helpers
    def pot(self):
        return sum(p.total for p in self.players)

    def _next_alive(self, i):
        n = len(self.players)
        for s in range(1, n + 1):
            j = (i + s) % n
            if not self.players[j].out:
                return j
        return i

    def _commit(self, p, amt):
        amt = max(0, min(int(amt), p.chips))
        p.chips -= amt
        p.bet += amt
        p.total += amt
        if p.chips == 0:
            p.allin = True
        return amt

    def _bounds(self):
        p = self.players[0]
        to_call = max(0, self.current_bet - p.bet)
        max_to = p.bet + p.chips
        min_to = min(self.current_bet + self.min_raise, max_to)
        can_raise = p.chips > to_call
        return to_call, min_to, max_to, can_raise

    def _clamp_raise(self, reset=False):
        _, min_to, max_to, _ = self._bounds()
        if reset:
            self.raise_to = min_to
        self.raise_to = int(max(min_to, min(max_to, self.raise_to)))

    # ------------------------------------------------------------------ hand flow
    def new_hand(self):
        ps = self.players
        self.hand_no += 1
        self.dealer = self._next_alive(self.dealer)
        lvl = min(len(BLINDS) - 1, (self.hand_no - 1) // HANDS_PER_LEVEL)
        self.sb_amt, self.bb_amt = BLINDS[lvl]
        self.deck = list(range(52))
        random.shuffle(self.deck)
        self.board = []
        self.stage = 0
        self.showdown = False
        self.reveal = False
        self.win_cards = set()
        self.msg = []
        for p in ps:
            p.clear_hand()
            if not p.out:
                p.hole = [self.deck.pop(), self.deck.pop()]
        alive = [i for i, p in enumerate(ps) if not p.out]
        self.sb_i = self.dealer if len(alive) == 2 else self._next_alive(self.dealer)
        self.bb_i = self._next_alive(self.sb_i)
        self._commit(ps[self.sb_i], self.sb_amt)
        self._commit(ps[self.bb_i], self.bb_amt)
        self.current_bet = max(p.bet for p in ps)
        self.min_raise = self.bb_amt
        self.raise_count = 0
        self.turn = self.bb_i
        self._advance()

    def _set_turn(self, j):
        self.turn = j
        p = self.players[j]
        if p.human:
            self.state = 'human'
            self._clamp_raise(reset=True)
        else:
            self.state = 'ai'
            fast = self.players[0].folded or self.players[0].out
            self.timer = random.uniform(*AI_DELAY) * (0.35 if fast else 1.0)

    def _advance(self):
        """Give the action to the next player, or close the betting round."""
        ps = self.players
        live = [p for p in ps if not p.out and not p.folded]
        if len(live) == 1:
            return self._finish_uncontested()
        able = [p for p in live if not p.allin]
        if len(able) <= 1 and all(p.bet >= self.current_bet for p in able):
            return self._end_round()
        n = len(ps)
        for s in range(1, n + 1):
            j = (self.turn + s) % n
            p = ps[j]
            if p.out or p.folded or p.allin:
                continue
            if not p.acted or p.bet < self.current_bet:
                return self._set_turn(j)
        self._end_round()

    def _end_round(self):
        ps = self.players
        for p in ps:
            p.bet = 0
            p.acted = False
            if not p.folded and not p.allin:
                p.act_txt = ""
        self.current_bet = 0
        self.min_raise = self.bb_amt
        self.raise_count = 0
        if self.stage == 3:
            return self._showdown()
        self._deal_street()
        able = [p for p in ps if not p.out and not p.folded and not p.allin]
        if len(able) <= 1:                      # everybody is all-in: run it out
            self.state = 'runout'
            self.reveal = True
            self.timer = STREET_DELAY
        else:
            self.turn = self.dealer
            self._advance()

    def _deal_street(self):
        self.stage += 1
        for _ in range(3 if self.stage == 1 else 1):
            self.board.append(self.deck.pop())

    def _runout_step(self):
        if self.stage >= 3:
            self._showdown()
        else:
            self._deal_street()
            self.timer = STREET_DELAY if self.stage < 3 else SHOWDOWN_DELAY

    # ------------------------------------------------------------------ actions
    def _fold(self, i):
        p = self.players[i]
        p.folded = True
        p.acted = True
        p.act_txt = "Fold"
        self.turn = i
        self._advance()

    def _call(self, i):
        p = self.players[i]
        to_call = max(0, self.current_bet - p.bet)
        self._commit(p, to_call)
        p.acted = True
        if to_call == 0:
            p.act_txt = "Check"
        elif p.allin:
            p.act_txt = "All-in %d" % p.bet
        else:
            p.act_txt = "Call %d" % p.bet
        self.turn = i
        self._advance()

    def _raise(self, i, to):
        p = self.players[i]
        to = int(min(to, p.bet + p.chips))
        if to <= self.current_bet:
            return self._call(i)
        was_bet = self.current_bet == 0
        self._commit(p, to - p.bet)
        full = to - self.current_bet
        if full >= self.min_raise:
            self.min_raise = full
        self.current_bet = to
        self.raise_count += 1
        for q in self.players:                  # everybody else gets to answer
            if q is not p and not q.out and not q.folded and not q.allin:
                q.acted = False
        p.acted = True
        if p.allin:
            p.act_txt = "All-in %d" % p.bet
        elif was_bet:
            p.act_txt = "Bet %d" % p.bet
        else:
            p.act_txt = "Raise %d" % p.bet
        self.turn = i
        self._advance()

    def _ai_act(self):
        i = self.turn
        p = self.players[i]
        to_call = max(0, self.current_bet - p.bet)
        pot = self.pot()
        n_opp = sum(1 for j, q in enumerate(self.players)
                    if j != i and not q.out and not q.folded)
        if not self.board:
            s = 0.12 + 0.62 * chen(p.hole) / 20.0
        else:
            s = equity(p.hole, self.board, max(1, min(n_opp, 3)), 60)
        s += random.uniform(-0.04, 0.04)

        max_to = p.bet + p.chips
        min_to = min(self.current_bet + self.min_raise, max_to)
        can_raise = p.chips > to_call and self.raise_count < 4

        def size(frac):
            to = self.current_bet + int(frac * (pot + to_call))
            to = int(round(max(min_to, to) / 5.0) * 5)
            return min(max(to, min_to), max_to)

        if to_call == 0:
            if can_raise and s > 0.48 + 0.12 * p.tight and \
                    random.random() < 0.45 + 0.5 * p.aggr:
                self._raise(i, size(random.uniform(0.45, 0.9 if s < 0.75 else 1.1)))
            elif can_raise and random.random() < 0.07 * p.aggr:
                self._raise(i, size(0.6))           # occasional bluff
            else:
                self._call(i)                       # check
        else:
            odds = to_call / float(pot + to_call)
            need = odds + 0.03 + 0.07 * p.tight
            if s < need:
                self._fold(i)
            elif can_raise and s > 0.60 + 0.10 * p.tight and random.random() < p.aggr:
                if s > 0.85 and random.random() < 0.25:
                    self._raise(i, max_to)
                else:
                    self._raise(i, size(random.uniform(0.6, 1.0)))
            else:
                self._call(i)

    # ------------------------------------------------------------------ end of hand
    def _finish_uncontested(self):
        ps = self.players
        i = next(i for i, p in enumerate(ps) if not p.out and not p.folded)
        total = self.pot()
        who = "You win" if i == 0 else "%s wins" % ps[i].name
        self.showdown = False
        self.msg = [("%s %d - everyone folded" % (who, total),
                     ov.C_GOOD if i == 0 else ov.C_WHITE)]
        self._hand_end({i: total})

    def _showdown(self):
        ps = self.players
        n = len(ps)
        self.showdown = True
        live = [i for i, p in enumerate(ps) if not p.out and not p.folded]
        combos = {}
        for i in live:
            sc, combo = best_hand(ps[i].hole + self.board)
            ps[i].score = sc
            ps[i].act_txt = describe(sc)
            combos[i] = combo

        contrib = [p.total for p in ps]
        won = {}
        prev = 0
        winners = live
        for lvl in sorted({ps[i].total for i in live}):      # main pot + side pots
            part = sum(min(c, lvl) - min(c, prev) for c in contrib)
            elig = [i for i in live if ps[i].total >= lvl]
            top = max(ps[i].score for i in elig)
            winners = [i for i in elig if ps[i].score == top]
            winners.sort(key=lambda i: (i - self.dealer - 1) % n)
            share, rem = divmod(part, len(winners))
            for k, i in enumerate(winners):
                won[i] = won.get(i, 0) + share + (1 if k < rem else 0)
            prev = lvl
        extra = sum(contrib) - sum(won.values())
        if extra > 0 and winners:
            won[winners[0]] += extra

        self.msg = []
        for i in sorted(won, key=lambda i: -won[i])[:3]:
            who = "You win" if i == 0 else "%s wins" % ps[i].name
            self.msg.append(("%s %d - %s" % (who, won[i], describe(ps[i].score)),
                             ov.C_GOOD if i == 0 else ov.C_WHITE))
            self.win_cards |= set(combos[i])
        self._hand_end(won)

    def _hand_end(self, won):
        ps = self.players
        for i, a in won.items():
            ps[i].chips += a
            ps[i].won = a
        self.biggest = max(self.biggest, won.get(0, 0))
        for p in ps:
            if p.chips <= 0 and not p.out:
                p.out = True
                p.act_txt = "Out"
        if ps[0].out:
            self._match_end(False)
        elif all(p.out for p in ps[1:]):
            self._match_end(True)
        else:
            self.state = 'hand_over'

    def _match_end(self, won):
        self.state = 'match_over'
        if won:
            head = ("You won the match!", ov.C_GOLD)
        else:
            head = ("You are out of chips", ov.C_BAD)
        self.msg = [head] + self.msg[:1]
        if self.record_mgr and not self.recorded:
            self.recorded = True
            try:
                rec = (self.record_mgr.add_win_record(score=self.biggest)
                       if won else self.record_mgr.add_lose_record())
                self.wins, self.losses = rec["wins"], rec["losses"]
            except Exception:
                pass

    # ------------------------------------------------------------------ input
    def _do(self, a):
        st = self.state
        if a == 'next' and st == 'hand_over':
            self.new_hand()
            return
        if a == 'new' and st == 'match_over':
            self.reset()
            return
        if st != 'human':
            return
        to_call, min_to, max_to, can_raise = self._bounds()
        if a == 'fold':
            if to_call > 0:
                self._fold(0)
        elif a == 'call':
            self._call(0)
        elif a == 'raise':
            if can_raise:
                self._clamp_raise()
                self._raise(0, self.raise_to)
        elif can_raise:
            pot = self.pot()
            if a == 'plus':
                self.raise_to += self.bb_amt
            elif a == 'minus':
                self.raise_to -= self.bb_amt
            elif a == 'min':
                self.raise_to = min_to
            elif a == 'half':
                self.raise_to = self.current_bet + int(0.5 * (pot + to_call))
            elif a == 'pot':
                self.raise_to = self.current_bet + pot + to_call
            elif a == 'allin':
                self.raise_to = max_to
            self._clamp_raise()

    def key(self, key, repeat):
        if key in ('UP_ARROW', 'DOWN_ARROW'):
            self._do('plus' if key == 'UP_ARROW' else 'minus')
            return
        if repeat:
            return
        mapping = {'F': 'fold', 'C': 'call', 'B': 'raise', 'A': 'allin'}
        if key in mapping:
            self._do(mapping[key])
        elif key in ('SPACE', 'RET', 'NUMPAD_ENTER'):
            self._do('next' if self.state == 'hand_over' else 'new')

    def _button_at(self, x, y):
        for bid, bx, by, bw, bh, _l, _f, en, _s in self.buttons:
            if bid and en and bx <= x <= bx + bw and by <= y <= by + bh:
                return bid
        return None

    def click(self, x, y, button):
        if button != 'LEFT':
            return
        bid = self._button_at(x, y)
        if bid:
            self._do(bid)

    def move(self, x, y):
        h = self._button_at(x, y)
        if h != self.hover:
            self.hover = h
            return True
        return False

    def update(self, dt):
        if self.state == 'ai':
            self.timer -= dt
            if self.timer <= 0:
                self._ai_act()
                return True
        elif self.state == 'runout':
            self.timer -= dt
            if self.timer <= 0:
                self._runout_step()
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
        if st == 'hand_over':
            add('next', 250, 8, 220, 36, "Next hand  (SPACE)", C_CALL, size=14)
        elif st == 'match_over':
            add('new', 250, 8, 220, 36, "New match  (SPACE)", C_RAISE, size=14)
        else:
            mine = st == 'human'
            p = self.players[0]
            to_call, min_to, max_to, can_raise = self._bounds()
            self._clamp_raise()
            call_txt = "Check  (C)" if to_call == 0 else "Call %d  (C)" % min(to_call, p.chips)
            if not can_raise:
                raise_txt = "Raise"
            elif self.raise_to >= max_to:
                raise_txt = "All-in %d  (B)" % max_to
            elif self.current_bet == 0:
                raise_txt = "Bet %d  (B)" % self.raise_to
            else:
                raise_txt = "Raise to %d  (B)" % self.raise_to
            r = mine and can_raise
            add('fold', 130, 6, 110, 34, "Fold  (F)", C_FOLD, mine and to_call > 0)
            add('call', 250, 6, 150, 34, call_txt, C_CALL, mine)
            add('raise', 410, 6, 180, 34, raise_txt, C_RAISE, r)
            add('minus', 170, 46, 30, 24, "-", ov.C_CELL, r, 15)
            add(None, 204, 46, 92, 24, str(self.raise_to) if can_raise else "-",
                ov.C_DARK, False, 13)
            add('plus', 300, 46, 30, 24, "+", ov.C_CELL, r, 15)
            add('min', 338, 46, 44, 24, "Min", ov.C_CELL, r, 11)
            add('half', 386, 46, 54, 24, "1/2 Pot", ov.C_CELL, r, 11)
            add('pot', 444, 46, 44, 24, "Pot", ov.C_CELL, r, 11)
            add('allin', 492, 46, 58, 24, "All-in", ov.C_CELL, r, 11)
        self.buttons = out

    # ------------------------------------------------------------------ drawing
    def _rr(self, c, x, y, w, h, ch, col):
        """Rectangle with chamfered corners."""
        c.poly([(x + ch, y), (x + w - ch, y), (x + w, y + ch), (x + w, y + h - ch),
                (x + w - ch, y + h), (x + ch, y + h), (x, y + h - ch), (x, y + ch)], col)

    def _ellipse(self, cx, cy, rx, ry, seg=72):
        return [(self._x(cx + rx * math.cos(2 * math.pi * i / seg)),
                 self._y(cy + ry * math.sin(2 * math.pi * i / seg))) for i in range(seg)]

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

    def _card(self, c, cx, cy, w, h, card=None, hi=False, dim=False):
        """Draw a card centred on pixel (cx, cy); w/h in design units. card=None -> back."""
        k, u = self.k, self.u
        w, h = w * k, h * k
        x0, y0 = cx - w / 2, cy - h / 2
        ch = min(w, h) * 0.12
        if hi:
            t = 3 * u
            self._rr(c, x0 - t, y0 - t, w + 2 * t, h + 2 * t, ch + t, ov.C_GOLD)
        self._rr(c, x0 - 1, y0 - 1, w + 2, h + 2, ch, C_CARD_EDGE)
        if card is None:
            self._rr(c, x0, y0, w, h, ch, C_BACK)
            m = w * 0.12
            self._rr(c, x0 + m, y0 + m, w - 2 * m, h - 2 * m, ch * 0.6, ov.shade(C_BACK, 1.6))
            c.poly([(cx, cy + h * 0.28), (cx + w * 0.22, cy),
                    (cx, cy - h * 0.28), (cx - w * 0.22, cy)], ov.shade(C_BACK, 0.7))
            return
        self._rr(c, x0, y0, w, h, ch, ov.shade(C_CARD, 0.62) if dim else C_CARD)
        s = card // 13
        col = C_RED if s in (1, 2) else C_BLACK
        c.text(rank_label(card), cx, cy + h * 0.22, h * 0.34, col)
        self._suit(c, s, cx, cy - h * 0.20, h * 0.30, col)

    def _draw_table(self, c):
        k = self.k
        c.poly(self._ellipse(360, 285, 355, 210), C_FELT_EDGE)
        c.poly(self._ellipse(360, 285, 345, 200), C_FELT)
        ring = self._ellipse(360, 285, 330, 186)
        c.polyline(ring + [ring[0]], max(1.5, 2 * k), C_FELT_LINE)

    def _draw_seat(self, c, i):
        k, u = self.k, self.u
        p = self.players[i]
        seat = SEATS[i]
        playing = self.state in ('human', 'ai')
        over = self.state in ('hand_over', 'match_over')

        # --- cards
        px, py = self._x(seat['cards'][0]), self._y(seat['cards'][1])
        if p.hole and not p.out and (i == 0 or not p.folded):
            face_up = i == 0 or ((self.showdown or self.reveal) and not p.folded)
            if i == 0:
                w, h, off = 54, 76, 31
            else:
                w, h, off = 36, 52, 21
            for n, card in enumerate(p.hole):
                dx = (-off if n == 0 else off) * k
                hi = over and self.showdown and card in self.win_cards
                self._card(c, px + dx, py, w, h, card if face_up else None,
                           hi=hi, dim=(i == 0 and p.folded))

        # --- bet in front of the player
        if p.bet > 0:
            bx, by = self._x(seat['bet'][0]), self._y(seat['bet'][1])
            c.circle(bx, by, 8 * k, ov.C_GOLD)
            c.ring(bx, by, 5.5 * k, max(1.0, 1.5 * k), (0.55, 0.40, 0.05, 1.0))
            c.text(str(p.bet), bx + 14 * k, by, 13 * k, ov.C_WHITE, 'left')

        # --- name plate
        w, h = 150 * k, 46 * k
        x0 = self._x(seat['plate'][0]) - w / 2
        y0 = self._y(seat['plate'][1]) - h / 2
        active = playing and self.turn == i
        winner = over and p.won > 0
        edge = ov.C_GOLD if active else ov.C_GOOD if winner else ov.C_BORDER
        off = p.out or p.folded
        th = max(1.5, 2 * u)
        c.rect(x0 - th, y0 - th, w + 2 * th, h + 2 * th, edge)
        c.rect(x0, y0, w, h, C_PLATE_OFF if off else C_PLATE)

        tags = []
        if not p.out:
            if i == self.dealer:
                tags.append("D")
            if i == self.sb_i:
                tags.append("SB")
            if i == self.bb_i:
                tags.append("BB")
        name = p.name + ("  (%s)" % "/".join(tags) if tags else "")
        cx = x0 + w / 2
        c.text(name, cx, y0 + h * 0.76, 13 * k, ov.C_TEXT if off else ov.C_WHITE)
        if p.out:
            c.text("Out", cx, y0 + h * 0.40, 12 * k, C_DIM)
        else:
            chips = "$%d" % p.chips + ("  ALL-IN" if p.allin and p.chips == 0 and not over else "")
            c.text(chips, cx, y0 + h * 0.46, 12 * k, C_DIM if off else ov.C_GOLD)
            if p.act_txt:
                if winner:
                    col = ov.C_GOOD
                elif p.folded:
                    col = C_DIM
                else:
                    col = ov.C_TEXT
                c.text(p.act_txt, cx, y0 + h * 0.17, 10 * k, col)

    def _status(self):
        if self.state == 'human':
            to_call = self._bounds()[0]
            return [("Your turn - check or bet" if to_call == 0
                     else "Your turn - %d to call" % to_call, ov.C_GOLD)]
        if self.state == 'ai':
            return [("%s is thinking..." % self.players[self.turn].name, ov.C_TEXT)]
        if self.state == 'runout':
            return [("All-in - dealing the board...", ov.C_TEXT)]
        return self.msg

    def draw(self, c):
        k, u = self.k, self.u
        self.draw_frame(c)
        sub = "Hand %d  |  Blinds %d/%d  |  Record %dW - %dL" % (
            self.hand_no, self.sb_amt, self.bb_amt, self.wins, self.losses)
        self.draw_header(c, "Texas Hold'em", sub, ov.C_GOLD)
        self._draw_table(c)

        # --- community cards
        over = self.state in ('hand_over', 'match_over')
        for n in range(5):
            cx, cy = self._x(360 + (n - 2) * 56), self._y(305)
            if n < len(self.board):
                card = self.board[n]
                self._card(c, cx, cy, 48, 68, card,
                           hi=over and self.showdown and card in self.win_cards)
            else:
                w, h = 48 * k, 68 * k
                self._rr(c, cx - w / 2, cy - h / 2, w, h, 5 * k, ov.alpha(C_FELT_LINE, 0.8))
                self._rr(c, cx - w / 2 + 2 * u, cy - h / 2 + 2 * u, w - 4 * u, h - 4 * u,
                         4 * k, ov.alpha(C_FELT, 1.0))

        # --- pot / street / status
        if not over:
            c.text("Pot: %d" % self.pot(), self._x(360), self._y(362), 17 * k, ov.C_GOLD)
            c.text(STAGES[self.stage], self._x(360), self._y(381), 11 * k, ov.C_TEXT)
        else:
            c.text("Hand over", self._x(360), self._y(362), 15 * k, ov.C_TEXT)
        for n, (txt, col) in enumerate(self._status()[:3]):
            c.text(txt, self._x(360), self._y(250 - 17 * n), (15 if n == 0 else 13) * k, col)

        # --- seats
        for i in range(len(self.players)):
            self._draw_seat(c, i)

        # --- what you are holding
        me = self.players[0]
        if len(self.board) >= 3 and me.hole and not me.folded and not me.out:
            sc, _ = best_hand(me.hole + self.board)
            c.text(describe(sc), self._x(292), self._y(165), 12 * k, ov.C_TEXT, 'right')

        # --- buttons
        b = max(1.0, 2 * u)
        for bid, x, y, w, h, label, face, en, size in self.buttons:
            f = face if en else C_BTN_OFF
            if en and bid is not None and bid == self.hover:
                f = ov.shade(f, 1.25)
            c.bevel(x, y, w, h, b, f)
            c.text(label, x + w / 2, y + h / 2, size, ov.C_WHITE if en else C_DIM)

        self.draw_hint(c, "F fold   C check/call   B bet/raise   Up/Down amount   "
                          "R new match   ESC quit")


RUNNER = ov.Runner("poker", GAME_NAME, Poker, [
    "Texas Hold'em vs 3 bots ($1000 each)",
    "F: fold   C: check / call",
    "Up / Down: raise amount   B: bet / raise",
    "A: all-in amount   SPACE: next hand",
    "R: new match   ESC: quit",
])


def draw_ui(layout, context):
    RUNNER.draw_ui(layout)


def register():
    RUNNER.register()


def unregister():
    RUNNER.unregister()
