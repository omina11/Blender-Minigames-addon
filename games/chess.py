"""Chess - you vs the CPU (alpha-beta), or two players (GPU overlay).

Full rules: castling, en passant, promotion (you choose the piece), check,
checkmate, stalemate, 50-move rule, threefold repetition, insufficient material.
The CPU thinks in a background thread so Blender never freezes.
"""

import math
import random
import threading
import time
import traceback

from . import _overlay as ov
from . import _record as _rec

GAME_NAME = "Chess"
GAME_ICON = 'MESH_GRID'

# ----------------------------------------------------------------------------
# Engine: board representation
#   board = list of 64 ints, index = rank * 8 + file (a1 = 0, h8 = 63)
#   0 empty, +1..+6 white P N B R Q K, -1..-6 black
# ----------------------------------------------------------------------------

P_, N_, B_, R_, Q_, K_ = 1, 2, 3, 4, 5, 6
WK, WQ, BK, BQ = 1, 2, 4, 8          # castling right bits

DIRS8 = ((0, 1), (0, -1), (1, 0), (-1, 0), (1, 1), (-1, 1), (1, -1), (-1, -1))

KNIGHT_T, KING_T, RAYS, PAWN_FROM = [], [], [], {1: [], -1: []}
CASTLE_MASK = [0] * 64
CASTLE_MASK[0], CASTLE_MASK[7], CASTLE_MASK[4] = WQ, WK, WK | WQ
CASTLE_MASK[56], CASTLE_MASK[63], CASTLE_MASK[60] = BQ, BK, BK | BQ


def _init_tables():
    for sq in range(64):
        f, r = sq & 7, sq >> 3
        KNIGHT_T.append(tuple(
            (r + dr) * 8 + f + df
            for dr, df in ((1, 2), (2, 1), (2, -1), (1, -2), (-1, -2), (-2, -1), (-2, 1), (-1, 2))
            if 0 <= r + dr < 8 and 0 <= f + df < 8))
        KING_T.append(tuple(
            (r + dr) * 8 + f + df
            for dr in (-1, 0, 1) for df in (-1, 0, 1)
            if (dr or df) and 0 <= r + dr < 8 and 0 <= f + df < 8))
        rays = []
        for df, dr in DIRS8:
            ray, ff, rr = [], f + df, r + dr
            while 0 <= ff < 8 and 0 <= rr < 8:
                ray.append(rr * 8 + ff)
                ff, rr = ff + df, rr + dr
            rays.append(tuple(ray))
        RAYS.append(tuple(rays))
        # squares from which a pawn of the given colour attacks sq
        PAWN_FROM[1].append(tuple((r - 1) * 8 + f + df for df in (-1, 1)
                                  if r - 1 >= 0 and 0 <= f + df < 8))
        PAWN_FROM[-1].append(tuple((r + 1) * 8 + f + df for df in (-1, 1)
                                   if r + 1 < 8 and 0 <= f + df < 8))


_init_tables()


class Pos:
    __slots__ = ('b', 'turn', 'castle', 'ep', 'half', 'full', 'ksq')

    def __init__(self, b, turn, castle, ep, half, full, ksq):
        self.b, self.turn, self.castle, self.ep = b, turn, castle, ep
        self.half, self.full, self.ksq = half, full, ksq


def start_pos():
    b = [0] * 64
    back = (R_, N_, B_, Q_, K_, B_, N_, R_)
    for f in range(8):
        b[f] = back[f]
        b[8 + f] = P_
        b[48 + f] = -P_
        b[56 + f] = -back[f]
    return Pos(b, 1, WK | WQ | BK | BQ, -1, 0, 1, [4, 60])


def pos_key(pos):
    return (tuple(pos.b), pos.turn, pos.castle, pos.ep)


def attacked(b, sq, by):
    """Is square sq attacked by colour `by` (+1 white, -1 black)?"""
    for s in PAWN_FROM[by][sq]:
        if b[s] == by:
            return True
    kn = 2 * by
    for s in KNIGHT_T[sq]:
        if b[s] == kn:
            return True
    kg = 6 * by
    for s in KING_T[sq]:
        if b[s] == kg:
            return True
    rk, qn, bi = 4 * by, 5 * by, 3 * by
    rays = RAYS[sq]
    for i in range(4):
        for s in rays[i]:
            v = b[s]
            if v:
                if v == rk or v == qn:
                    return True
                break
    for i in range(4, 8):
        for s in rays[i]:
            v = b[s]
            if v:
                if v == bi or v == qn:
                    return True
                break
    return False


def gen(pos, caps_only=False):
    """Pseudo-legal moves as (from, to, promotion_piece_type_or_0)."""
    b, color, ep = pos.b, pos.turn, pos.ep
    out = []
    add = out.append
    last = 7 if color == 1 else 0
    for sq in range(64):
        p = b[sq]
        if not p or (p > 0) != (color > 0):
            continue
        ap = p if p > 0 else -p
        if ap == 1:
            fl = sq & 7
            t = sq + 8 * color
            if not b[t]:
                if (t >> 3) == last:
                    for pr in ((5,) if caps_only else (5, 4, 3, 2)):
                        add((sq, t, pr))
                elif not caps_only:
                    add((sq, t, 0))
                    if (sq >> 3) == (1 if color == 1 else 6):
                        t2 = t + 8 * color
                        if not b[t2]:
                            add((sq, t2, 0))
            for df in (-1, 1):
                if 0 <= fl + df < 8:
                    t = sq + 8 * color + df
                    v = b[t]
                    if v and (v > 0) != (color > 0):
                        if (t >> 3) == last:
                            for pr in ((5,) if caps_only else (5, 4, 3, 2)):
                                add((sq, t, pr))
                        else:
                            add((sq, t, 0))
                    elif not v and t == ep:
                        add((sq, t, 0))
        elif ap == 2:
            for t in KNIGHT_T[sq]:
                v = b[t]
                if not v:
                    if not caps_only:
                        add((sq, t, 0))
                elif (v > 0) != (color > 0):
                    add((sq, t, 0))
        elif ap == 6:
            for t in KING_T[sq]:
                v = b[t]
                if not v:
                    if not caps_only:
                        add((sq, t, 0))
                elif (v > 0) != (color > 0):
                    add((sq, t, 0))
            if not caps_only and sq == (4 if color == 1 else 60):
                ks, qs = (WK, WQ) if color == 1 else (BK, BQ)
                opp = -color
                if pos.castle & (ks | qs) and not attacked(b, sq, opp):
                    if (pos.castle & ks and not b[sq + 1] and not b[sq + 2]
                            and b[sq + 3] == 4 * color
                            and not attacked(b, sq + 1, opp) and not attacked(b, sq + 2, opp)):
                        add((sq, sq + 2, 0))
                    if (pos.castle & qs and not b[sq - 1] and not b[sq - 2] and not b[sq - 3]
                            and b[sq - 4] == 4 * color
                            and not attacked(b, sq - 1, opp) and not attacked(b, sq - 2, opp)):
                        add((sq, sq - 2, 0))
        else:
            lo, hi = (4, 8) if ap == 3 else (0, 4) if ap == 4 else (0, 8)
            rays = RAYS[sq]
            for i in range(lo, hi):
                for t in rays[i]:
                    v = b[t]
                    if not v:
                        if not caps_only:
                            add((sq, t, 0))
                        continue
                    if (v > 0) != (color > 0):
                        add((sq, t, 0))
                    break
    return out


def make(pos, mv):
    f, t, pr = mv
    b = pos.b[:]
    piece = b[f]
    color = 1 if piece > 0 else -1
    ap = piece if piece > 0 else -piece
    cap = b[t]
    ep = -1
    half = pos.half + 1
    ksq = pos.ksq
    b[t] = piece
    b[f] = 0
    if cap or ap == 1:
        half = 0
    if ap == 1:
        if not cap and (f & 7) != (t & 7):               # en passant capture
            b[t - 8 * color] = 0
        elif t - f == 16 or f - t == 16:
            ep = (f + t) >> 1
        if pr:
            b[t] = pr * color
    elif ap == 6:
        ksq = [ksq[0], ksq[1]]
        ksq[0 if color == 1 else 1] = t
        if t - f == 2:                                     # castle kingside
            b[f + 3] = 0
            b[f + 1] = R_ * color
        elif f - t == 2:                                   # castle queenside
            b[f - 4] = 0
            b[f - 1] = R_ * color
    castle = pos.castle & ~(CASTLE_MASK[f] | CASTLE_MASK[t])
    return Pos(b, -color, castle, ep, half, pos.full + (1 if color == -1 else 0), ksq)


def legal_moves(pos):
    """List of (move, resulting_pos) for the side to move."""
    ki = 0 if pos.turn == 1 else 1
    opp = -pos.turn
    out = []
    for mv in gen(pos):
        ch = make(pos, mv)
        if not attacked(ch.b, ch.ksq[ki], opp):
            out.append((mv, ch))
    return out


def in_check(pos):
    return attacked(pos.b, pos.ksq[0 if pos.turn == 1 else 1], -pos.turn)


def insufficient(b):
    minors = 0
    for v in b:
        a = v if v > 0 else -v
        if a in (1, 4, 5):
            return False
        if a in (2, 3):
            minors += 1
    return minors <= 1


def sq_name(sq):
    return "abcdefgh"[sq & 7] + str((sq >> 3) + 1)


# ----------------------------------------------------------------------------
# Engine: evaluation + search
# ----------------------------------------------------------------------------

_PAWN = [0, 0, 0, 0, 0, 0, 0, 0, 50, 50, 50, 50, 50, 50, 50, 50, 10, 10, 20, 30, 30, 20, 10, 10,
         5, 5, 10, 25, 25, 10, 5, 5, 0, 0, 0, 20, 20, 0, 0, 0, 5, -5, -10, 0, 0, -10, -5, 5,
         5, 10, 10, -20, -20, 10, 10, 5, 0, 0, 0, 0, 0, 0, 0, 0]
_KNIGHT = [-50, -40, -30, -30, -30, -30, -40, -50, -40, -20, 0, 0, 0, 0, -20, -40,
           -30, 0, 10, 15, 15, 10, 0, -30, -30, 5, 15, 20, 20, 15, 5, -30,
           -30, 0, 15, 20, 20, 15, 0, -30, -30, 5, 10, 15, 15, 10, 5, -30,
           -40, -20, 0, 5, 5, 0, -20, -40, -50, -40, -30, -30, -30, -30, -40, -50]
_BISHOP = [-20, -10, -10, -10, -10, -10, -10, -20, -10, 0, 0, 0, 0, 0, 0, -10,
           -10, 0, 5, 10, 10, 5, 0, -10, -10, 5, 5, 10, 10, 5, 5, -10,
           -10, 0, 10, 10, 10, 10, 0, -10, -10, 10, 10, 10, 10, 10, 10, -10,
           -10, 5, 0, 0, 0, 0, 5, -10, -20, -10, -10, -10, -10, -10, -10, -20]
_ROOK = [0, 0, 0, 0, 0, 0, 0, 0, 5, 10, 10, 10, 10, 10, 10, 5, -5, 0, 0, 0, 0, 0, 0, -5,
         -5, 0, 0, 0, 0, 0, 0, -5, -5, 0, 0, 0, 0, 0, 0, -5, -5, 0, 0, 0, 0, 0, 0, -5,
         -5, 0, 0, 0, 0, 0, 0, -5, 0, 0, 0, 5, 5, 0, 0, 0]
_QUEEN = [-20, -10, -10, -5, -5, -10, -10, -20, -10, 0, 0, 0, 0, 0, 0, -10,
          -10, 0, 5, 5, 5, 5, 0, -10, -5, 0, 5, 5, 5, 5, 0, -5,
          0, 0, 5, 5, 5, 5, 0, -5, -10, 5, 5, 5, 5, 5, 0, -10,
          -10, 0, 5, 0, 0, 0, 0, -10, -20, -10, -10, -5, -5, -10, -10, -20]
_KING_MID = [-30, -40, -40, -50, -50, -40, -40, -30, -30, -40, -40, -50, -50, -40, -40, -30,
             -30, -40, -40, -50, -50, -40, -40, -30, -30, -40, -40, -50, -50, -40, -40, -30,
             -20, -30, -30, -40, -40, -30, -30, -20, -10, -20, -20, -20, -20, -20, -20, -10,
             20, 20, 0, 0, 0, 0, 20, 20, 20, 30, 10, 0, 0, 10, 30, 20]
_KING_END = [-50, -40, -30, -20, -20, -30, -40, -50, -30, -20, -10, 0, 0, -10, -20, -30,
             -30, -10, 20, 30, 30, 20, -10, -30, -30, -10, 30, 40, 40, 30, -10, -30,
             -30, -10, 30, 40, 40, 30, -10, -30, -30, -10, 20, 30, 30, 20, -10, -30,
             -30, -30, 0, 0, 0, 0, -30, -30, -50, -30, -30, -30, -30, -30, -30, -50]


def _mk(tab):
    """Tables are written from white's view (top row = rank 8)."""
    white = [tab[((7 - (s >> 3)) << 3) | (s & 7)] for s in range(64)]
    black = [tab[s] for s in range(64)]
    return white, black


VAL = {1: 100, 2: 320, 3: 330, 4: 500, 5: 900}
PST = {1: _mk(_PAWN), 2: _mk(_KNIGHT), 3: _mk(_BISHOP), 4: _mk(_ROOK), 5: _mk(_QUEEN)}
K_MID, K_END = _mk(_KING_MID), _mk(_KING_END)

MATE = 100000
INF = 10 ** 9


def evaluate(pos):
    """Score in centipawns from the point of view of the side to move."""
    b = pos.b
    score = npw = npb = 0
    for sq in range(64):
        p = b[sq]
        if not p:
            continue
        if p > 0:
            if p == 6:
                continue
            score += VAL[p] + PST[p][0][sq]
            if p != 1:
                npw += VAL[p]
        else:
            a = -p
            if a == 6:
                continue
            score -= VAL[a] + PST[a][1][sq]
            if a != 1:
                npb += VAL[a]
    kt = K_END if (npw <= 1300 and npb <= 1300) else K_MID
    score += kt[0][pos.ksq[0]] - kt[1][pos.ksq[1]]
    return score if pos.turn == 1 else -score


def order_moves(pos, moves):
    b = pos.b

    def key(mv):
        f, t, pr = mv
        v = b[t]
        s = 0
        if v:
            s = 100 + 10 * (v if v > 0 else -v) - (b[f] if b[f] > 0 else -b[f])
        elif (b[f] == 1 or b[f] == -1) and (f & 7) != (t & 7):
            s = 109
        if pr:
            s += 80 + pr
        return -s
    moves.sort(key=key)
    return moves


class _Timeout(Exception):
    pass


LEVELS = ["Easy", "Medium", "Hard", "Expert"]
LEVEL_INFO = ["Beginner - often blunders", "Casual - sees 2 moves ahead",
              "Strong - searches ~1 s per move", "Expert - searches ~3.5 s per move"]
LEVEL_CFG = [
    {'depth': 1, 'noise': 150, 'time': None},   # random among near-equal moves
    {'depth': 2, 'noise': 15, 'time': None},
    {'depth': 4, 'noise': 0, 'time': 1.0},
    {'depth': 8, 'noise': 0, 'time': 3.5},
]


class Search:
    def __init__(self):
        self.cancel = False
        self.timed = False
        self.deadline = 0.0
        self.nodes = 0

    def _tick(self):
        self.nodes += 1
        if not (self.nodes & 511):
            if self.cancel:
                raise _Timeout()
            if self.timed and time.time() > self.deadline:
                raise _Timeout()
            time.sleep(0.0005)          # let the UI thread breathe

    def negamax(self, pos, depth, alpha, beta, ply):
        self._tick()
        if pos.half >= 100:
            return 0
        turn = pos.turn
        ki = 0 if turn == 1 else 1
        chk = attacked(pos.b, pos.ksq[ki], -turn)
        if chk and ply < 10:
            depth += 1
        if depth <= 0:
            return self.quiesce(pos, alpha, beta, 0)
        best = -INF
        legal = 0
        for mv in order_moves(pos, gen(pos)):
            ch = make(pos, mv)
            if attacked(ch.b, ch.ksq[ki], -turn):
                continue
            legal += 1
            sc = -self.negamax(ch, depth - 1, -beta, -alpha, ply + 1)
            if sc > best:
                best = sc
                if sc > alpha:
                    alpha = sc
                    if alpha >= beta:
                        break
        if legal == 0:
            return -(MATE - ply) if chk else 0
        return best

    def quiesce(self, pos, alpha, beta, qd):
        self._tick()
        stand = evaluate(pos)
        if stand >= beta or qd >= 6:
            return stand
        if stand > alpha:
            alpha = stand
        best = stand
        turn = pos.turn
        ki = 0 if turn == 1 else 1
        for mv in order_moves(pos, gen(pos, True)):
            ch = make(pos, mv)
            if attacked(ch.b, ch.ksq[ki], -turn):
                continue
            sc = -self.quiesce(ch, -beta, -alpha, qd + 1)
            if sc > best:
                best = sc
                if sc > alpha:
                    alpha = sc
                    if alpha >= beta:
                        break
        return best

    @staticmethod
    def _rep_pen(ch, rep):
        return 40 * rep.get(pos_key(ch), 0)

    def root(self, pos, level, rep):
        """Best move for the side to move (None if cancelled / no moves)."""
        legal = legal_moves(pos)
        legal = [(mv, ch) for mv, ch in legal]
        if not legal:
            return None
        if len(legal) == 1:
            return legal[0][0]
        random.shuffle(legal)
        cfg = LEVEL_CFG[level]
        try:
            if cfg['time'] is None:
                return self._root_noisy(legal, cfg, rep)
            return self._root_deepening(legal, cfg, rep)
        except _Timeout:
            return None

    def _root_noisy(self, legal, cfg, rep):
        scored = []
        for mv, ch in legal:
            sc = -self.negamax(ch, cfg['depth'] - 1, -INF, INF, 1) - self._rep_pen(ch, rep)
            scored.append((sc, mv))
        best = max(s for s, _ in scored)
        return random.choice([mv for s, mv in scored if s >= best - cfg['noise']])

    def _root_deepening(self, legal, cfg, rep):
        self.deadline = time.time() + cfg['time']
        order = legal
        best = legal[0][0]
        for d in range(1, cfg['depth'] + 1):
            self.timed = d >= 2
            alpha, cur_best, cur = -INF, None, []
            try:
                for mv, ch in order:
                    sc = -self.negamax(ch, d - 1, -INF, -alpha, 1) - self._rep_pen(ch, rep)
                    cur.append((sc, mv, ch))
                    if sc > alpha:
                        alpha, cur_best = sc, mv
            except _Timeout:
                if self.cancel:
                    return None
                if cur_best is not None:
                    best = cur_best
                break
            best = cur_best
            cur.sort(key=lambda x: -x[0])
            order = [(mv, ch) for _, mv, ch in cur]
            if alpha > MATE - 100:
                break
        return best


# ----------------------------------------------------------------------------
# Piece shapes (unit square, y up). ('p', pts) convex polygon, ('c', x, y, r)
# circle, ('l', p, q, w) / ('e', x, y, r) details drawn in the outline colour.
# ----------------------------------------------------------------------------

def _build_prims():
    foot = ('p', [(-0.32, -0.42), (0.32, -0.42), (0.28, -0.30), (-0.28, -0.30)])
    pr = {}
    pr[1] = [foot,
             ('p', [(-0.20, -0.30), (0.20, -0.30), (0.09, 0.05), (-0.09, 0.05)]),
             ('p', [(-0.15, 0.02), (0.15, 0.02), (0.15, 0.10), (-0.15, 0.10)]),
             ('c', 0.0, 0.21, 0.14)]
    pr[4] = [foot,
             ('p', [(-0.22, -0.30), (0.22, -0.30), (0.17, 0.12), (-0.17, 0.12)]),
             ('p', [(-0.27, 0.12), (0.27, 0.12), (0.27, 0.22), (-0.27, 0.22)]),
             ('p', [(-0.27, 0.22), (-0.14, 0.22), (-0.14, 0.34), (-0.27, 0.34)]),
             ('p', [(-0.065, 0.22), (0.065, 0.22), (0.065, 0.34), (-0.065, 0.34)]),
             ('p', [(0.14, 0.22), (0.27, 0.22), (0.27, 0.34), (0.14, 0.34)])]
    pr[2] = [foot,
             ('p', [(-0.28, -0.30), (0.28, -0.30), (0.25, -0.24), (-0.25, -0.24)]),
             ('p', [(-0.22, -0.25), (0.24, -0.25), (0.22, 0.00), (0.10, 0.24),
                    (-0.04, 0.30), (-0.18, 0.10)]),
             ('p', [(-0.02, 0.30), (-0.16, 0.26), (-0.34, 0.04), (-0.31, -0.04),
                    (-0.17, 0.00), (0.00, 0.12)]),
             ('p', [(0.00, 0.28), (0.05, 0.45), (0.14, 0.27)]),
             ('p', [(0.08, 0.26), (0.17, 0.40), (0.20, 0.20)]),
             ('l', (0.12, 0.17), (0.21, 0.14), 0.024),
             ('l', (0.14, 0.05), (0.23, 0.01), 0.024),
             ('l', (0.16, -0.08), (0.24, -0.12), 0.024),
             ('l', (-0.33, -0.01), (-0.21, 0.01), 0.014),
             ('e', -0.30, 0.025, 0.018),
             ('e', -0.08, 0.19, 0.032)]
    pr[3] = [foot,
             ('p', [(-0.20, -0.30), (0.20, -0.30), (0.08, 0.10), (-0.08, 0.10)]),
             ('p', [(0.0, 0.38), (-0.14, 0.22), (-0.10, 0.08), (0.10, 0.08), (0.14, 0.22)]),
             ('c', 0.0, 0.42, 0.05),
             ('l', (-0.03, 0.17), (0.07, 0.29), 0.03)]
    q = [foot,
         ('p', [(-0.28, -0.30), (0.28, -0.30), (0.25, -0.24), (-0.25, -0.24)]),
         ('p', [(-0.22, -0.25), (0.22, -0.25), (0.12, -0.02), (-0.12, -0.02)]),
         ('p', [(-0.12, -0.02), (0.12, -0.02), (0.09, 0.12), (-0.09, 0.12)]),
         ('p', [(-0.17, 0.11), (0.17, 0.11), (0.17, 0.17), (-0.17, 0.17)]),
         ('p', [(-0.12, 0.17), (0.12, 0.17), (0.25, 0.29), (-0.25, 0.29)]),
         ('l', (-0.17, 0.14), (0.17, 0.14), 0.012),
         ('e', 0.0, 0.22, 0.022)]
    for x, tip in ((-0.22, 0.34), (-0.11, 0.38), (0.0, 0.42), (0.11, 0.38), (0.22, 0.34)):
        q.append(('p', [(x - 0.055, 0.28), (x + 0.055, 0.28), (x, tip - 0.03)]))
        q.append(('c', x, tip, 0.038))
    pr[5] = q
    pr[6] = [foot,
             ('p', [(-0.28, -0.30), (0.28, -0.30), (0.25, -0.24), (-0.25, -0.24)]),
             ('p', [(-0.22, -0.25), (0.22, -0.25), (0.13, 0.02), (-0.13, 0.02)]),
             ('p', [(-0.13, 0.02), (0.13, 0.02), (0.10, 0.12), (-0.10, 0.12)]),
             ('p', [(-0.19, 0.11), (0.19, 0.11), (0.19, 0.17), (-0.19, 0.17)]),
             ('p', [(-0.13, 0.17), (0.13, 0.17), (0.23, 0.31), (-0.23, 0.31)]),
             ('p', [(-0.23, 0.30), (0.23, 0.30), (0.23, 0.35), (-0.23, 0.35)]),
             ('p', [(-0.035, 0.35), (0.035, 0.35), (0.035, 0.47), (-0.035, 0.47)]),
             ('p', [(-0.10, 0.395), (0.10, 0.395), (0.10, 0.445), (-0.10, 0.445)]),
             ('l', (-0.19, 0.14), (0.19, 0.14), 0.012),
             ('e', 0.0, 0.25, 0.026),
             ('e', -0.11, 0.24, 0.016),
             ('e', 0.11, 0.24, 0.016)]
    return pr


PRIMS = _build_prims()

C_LIGHT = (0.74, 0.77, 0.84, 1.0)
C_DARKSQ = (0.36, 0.43, 0.57, 1.0)
P_WHITE = (0.97, 0.96, 0.92, 1.0)
P_WHITE_EDGE = (0.10, 0.10, 0.12, 1.0)
P_BLACK = (0.13, 0.14, 0.18, 1.0)
P_BLACK_EDGE = (0.80, 0.82, 0.88, 1.0)
C_SEL = (0.35, 0.85, 0.45, 0.55)
C_LAST = (0.95, 0.85, 0.25, 0.38)
C_CHECK = (0.95, 0.22, 0.22, 0.70)
C_HOVER = (1.0, 1.0, 1.0, 0.14)
C_HINT = (0.10, 0.12, 0.16, 0.45)
C_BLACK_TXT = (0.70, 0.76, 0.90, 1.0)


def draw_piece(c, kind, white, cx, cy, size):
    fill, edge = (P_WHITE, P_WHITE_EDGE) if white else (P_BLACK, P_BLACK_EDGE)
    y0 = -0.05
    for outline in (True, False):
        sc = 1.12 if outline else 1.0
        col = edge if outline else fill
        for pr in PRIMS[kind]:
            t = pr[0]
            if t == 'p':
                pts = [(cx + x * sc * size, cy + ((y - y0) * sc + y0) * size) for x, y in pr[1]]
                c.poly(pts, col)
            elif t == 'c':
                c.circle(cx + pr[1] * sc * size, cy + ((pr[2] - y0) * sc + y0) * size,
                         pr[3] * sc * size, col, 16)
            elif not outline and t == 'l':
                p, q = pr[1], pr[2]
                c.line((cx + p[0] * size, cy + p[1] * size),
                       (cx + q[0] * size, cy + q[1] * size), pr[3] * size, edge)
            elif not outline and t == 'e':
                c.circle(cx + pr[1] * size, cy + pr[2] * size, pr[3] * size, edge, 8)


# ----------------------------------------------------------------------------
# Game (overlay)
# ----------------------------------------------------------------------------

PROMO_CHOICES = (5, 4, 3, 2)      # queen, rook, bishop, knight


class Chess(ov.BaseGame):
    keys = ('M', 'D', 'C', 'F', 'U', 'ONE', 'TWO', 'THREE', 'FOUR')

    def setup(self):
        self.mode = 'CPU'            # CPU | PVP
        self.level = 1               # index in LEVELS
        self.human = 1               # colour the player controls vs the CPU
        self.user_flip = False
        self.token = 0
        self.searcher = None
        self.result = None
        self.thinking = False
        self.menu = True             # difficulty menu is shown before the first game
        self.menu_hover = -1
        self.record_mgrs = {}        # level -> RecordManager (chess_<level>.json)
        self.best_cache = {}         # level -> best winning time in seconds (or None)

    def reset(self):
        self._cancel()
        self.pos = start_pos()
        self.hist = []               # (position_before, move)
        self.last = None
        self.sel = -1
        self.targets = {}
        self.promo = None            # (from, to) waiting for a piece choice
        self.promo_hover = -1
        self.hover = -1
        self.t_think = 0.0
        self.cs = 60
        self.t0 = time.time()        # game clock for the best-time record
        self.undone = False          # undo used -> win does not count for best time
        self.recorded = False
        self._refresh()

    # ---- records (one file per difficulty: chess_easy.json ...)
    def _mgr(self, level):
        if level not in self.record_mgrs:
            try:
                self.record_mgrs[level] = _rec.RecordManager(
                    game_name="chess_" + LEVELS[level].lower())
            except Exception:
                self.record_mgrs[level] = None
        return self.record_mgrs[level]

    def _best_time(self, level):
        if level not in self.best_cache:
            m = self._mgr(level)
            try:
                self.best_cache[level] = m.get_records().get("best_time") if m else None
            except Exception:
                self.best_cache[level] = None
        return self.best_cache[level]

    def _record_end(self):
        """Called once when a vs-CPU game ends: win (with time) / loss / draw."""
        if self.mode != 'CPU' or self.status == 'play' or self.recorded:
            return
        self.recorded = True
        m = self._mgr(self.level)
        if not m:
            return
        try:
            if self.status == 'mate':
                if -self.pos.turn == self.human:
                    t = None if self.undone else time.time() - self.t0
                    try:
                        m.add_win_record(score=1, best_time=t)
                    except TypeError:        # _record.py without best_time support
                        m.add_win_record(score=1)
                    self.best_cache.pop(self.level, None)
                else:
                    m.add_lose_record()
            else:
                m.add_draw_record()
        except Exception:
            traceback.print_exc()

    @staticmethod
    def _fmt(t):
        return "--" if t is None else "%d:%02d" % divmod(int(round(t)), 60)

    # ---- difficulty menu
    def _menu_rect(self, i):
        cs = self.cs
        return (self.left + 2 * cs, self.top - (1.5 + 1.1 * i + 0.8) * cs, 4 * cs, 0.8 * cs)

    def _menu_idx(self, x, y):
        for i in range(len(LEVELS) + 1):
            bx, by, bw, bh = self._menu_rect(i)
            if bx <= x <= bx + bw and by <= y <= by + bh:
                return i
        return -1

    def _start_game(self, i):
        if i >= len(LEVELS):
            self.mode = 'PVP'
        else:
            self.mode, self.level = 'CPU', i
        self.user_flip = False
        self.menu = False
        self.reset()

    # ---- state
    def _cancel(self):
        self.token += 1
        if self.searcher is not None:
            self.searcher.cancel = True
        self.thinking = False
        self.result = None

    def _refresh(self):
        pos = self.pos
        self.legal = legal_moves(pos)
        self.check = in_check(pos)
        self.rep = {}
        for p, _ in self.hist:
            k = pos_key(p)
            self.rep[k] = self.rep.get(k, 0) + 1
        k = pos_key(pos)
        self.rep[k] = self.rep.get(k, 0) + 1
        if not self.legal:
            self.status = 'mate' if self.check else 'stalemate'
        elif pos.half >= 100:
            self.status = 'draw50'
        elif self.rep[k] >= 3:
            self.status = 'rep'
        elif insufficient(pos.b):
            self.status = 'material'
        else:
            self.status = 'play'

    def _apply(self, mv):
        for m, ch in self.legal:
            if m == mv:
                self.hist.append((self.pos, mv))
                self.pos, self.last = ch, mv
                break
        else:
            return
        self.sel, self.targets, self.promo = -1, {}, None
        self._refresh()
        self._record_end()

    def _human_turn(self):
        return self.status == 'play' and (self.mode == 'PVP' or self.pos.turn == self.human)

    def _flipped(self):
        return self.user_flip ^ (self.mode == 'CPU' and self.human == -1)

    def undo(self):
        if not self.hist:
            return
        self._cancel()
        self.undone = True
        while self.hist:
            self.pos, _ = self.hist.pop()
            if self.mode == 'PVP' or self.pos.turn == self.human:
                break
        self.last = self.hist[-1][1] if self.hist else None
        self.sel, self.targets, self.promo = -1, {}, None
        self._refresh()

    # ---- CPU
    def _start_think(self):
        self.token += 1
        tok = self.token
        s = Search()
        self.searcher = s
        self.thinking = True
        self.t_think = 0.0
        self.result = None
        pos, level, rep = self.pos, self.level, dict(self.rep)

        def run():
            mv = None
            try:
                mv = s.root(pos, level, rep)
            except Exception:
                traceback.print_exc()
            self.result = (tok, mv)

        threading.Thread(target=run, daemon=True).start()

    def update(self, dt):
        if self.menu:
            return False
        if self.mode == 'CPU' and self.status == 'play' and self.pos.turn != self.human:
            if not self.thinking:
                self._start_think()
                return True
            self.t_think += dt
            r = self.result
            if r is not None and r[0] == self.token and self.t_think >= 0.35:
                self.thinking = False
                self.result = None
                if r[1] is not None:
                    self._apply(r[1])
                return True
        return False

    # ---- input
    def layout(self, view):
        self.cs = self.fit_cell(view, 8, 8, max_cell=80, min_cell=24)
        self.place(view, 8 * self.cs, 8 * self.cs)

    def _sq_of(self, col, row):
        if not self._flipped():
            return (7 - row) * 8 + col
        return row * 8 + (7 - col)

    def _cell_of(self, sq):
        f, r = sq & 7, sq >> 3
        if not self._flipped():
            return f, 7 - r
        return 7 - f, r

    def _idx(self, x, y):
        cr = ov.cell_at(x, y, self.left, self.top, self.cs, 8, 8)
        return -1 if cr is None else self._sq_of(cr[0], cr[1])

    def _promo_rect(self):
        cs = self.cs
        return self.left + 2 * cs, self.top - 4.5 * cs      # x0, y0 of a 4 x 1 strip

    def _promo_idx(self, x, y):
        px0, py0 = self._promo_rect()
        if py0 <= y <= py0 + self.cs and px0 <= x < px0 + 4 * self.cs:
            return int((x - px0) // self.cs)
        return -1

    def move(self, x, y):
        if self.menu:
            h = self._menu_idx(x, y)
            changed = h != self.menu_hover
            self.menu_hover = h
            return changed
        if self.promo:
            h = self._promo_idx(x, y)
            changed = h != self.promo_hover
            self.promo_hover = h
            return changed
        h = self._idx(x, y)
        changed = h != self.hover
        self.hover = h
        return changed

    def _select(self, sq):
        self.sel = sq
        self.targets = {}
        for mv, _ in self.legal:
            if mv[0] == sq:
                self.targets.setdefault(mv[1], []).append(mv)

    def click(self, x, y, button):
        if self.menu:
            if button == 'LEFT':
                i = self._menu_idx(x, y)
                if i >= 0:
                    self._start_game(i)
            return
        if button == 'RIGHT':
            self.sel, self.targets, self.promo = -1, {}, None
            return
        if self.status != 'play':
            self.reset()
            return
        if self.promo:
            i = self._promo_idx(x, y)
            if i >= 0:
                self._apply((self.promo[0], self.promo[1], PROMO_CHOICES[i]))
            else:
                self.promo = None
            return
        if not self._human_turn():
            return
        sq = self._idx(x, y)
        if sq < 0:
            return
        if self.sel >= 0 and sq in self.targets:
            mvs = self.targets[sq]
            if len(mvs) > 1:                       # promotion: ask which piece
                self.promo = (self.sel, sq)
                self.promo_hover = -1
            else:
                self._apply(mvs[0])
            return
        p = self.pos.b[sq]
        if p and (p > 0) == (self.pos.turn > 0) and sq != self.sel:
            self._select(sq)
        else:
            self.sel, self.targets = -1, {}

    def key(self, key, repeat):
        if self.menu:
            idx = {'ONE': 0, 'TWO': 1, 'THREE': 2, 'FOUR': 3, 'M': 4}.get(key)
            if idx is not None:
                self._start_game(idx)
            return
        if key == 'M':
            self.mode = 'PVP' if self.mode == 'CPU' else 'CPU'
            self.user_flip = False
            self.reset()
        elif key == 'D':                 # back to the difficulty menu
            self._cancel()
            self.menu, self.menu_hover = True, -1
        elif key == 'C':
            if self.mode == 'CPU':
                self.human = -self.human
                self.user_flip = False
                self.reset()
        elif key == 'F':
            self.user_flip = not self.user_flip
        elif key == 'U':
            self.undo()

    # ---- drawing
    def _status_text(self):
        st, pos, cpu = self.status, self.pos, self.mode == 'CPU'
        side = "White" if pos.turn == 1 else "Black"
        if st == 'mate':
            win = -pos.turn
            if cpu:
                return (("Checkmate - you win!", ov.C_GOLD) if win == self.human
                        else ("Checkmate - CPU wins", ov.C_BAD))
            return ("Checkmate - %s wins" % ("White" if win == 1 else "Black"), ov.C_GOLD)
        if st == 'stalemate':
            return "Stalemate - draw", ov.C_WHITE
        if st == 'draw50':
            return "Draw (50-move rule)", ov.C_WHITE
        if st == 'rep':
            return "Draw (repetition)", ov.C_WHITE
        if st == 'material':
            return "Draw (insufficient material)", ov.C_WHITE
        col = ov.C_WHITE if pos.turn == 1 else C_BLACK_TXT
        if self.check:
            col = ov.C_BAD
        if cpu:
            if pos.turn == self.human:
                main = "Your turn (%s)" % side
            else:
                main = "CPU is thinking..."
        else:
            main = "%s to move" % side
        if self.check:
            main += " - Check!"
        return main, col

    def draw(self, c):
        cs, u = self.cs, self.u
        self.draw_frame(c)
        main, col = self._status_text()
        cpu = self.mode == 'CPU'
        sub = ("vs CPU (%s)   |   You: %s" % (LEVELS[self.level], "White" if self.human == 1 else "Black")
               if cpu else "2 players")
        if cpu:
            sub += "   |   Best win: %s" % self._fmt(self._best_time(self.level))
        if self.last:
            sub += "   |   Last: %s-%s" % (sq_name(self.last[0]), sq_name(self.last[1]))
        if self.menu:
            main, col, sub = "Chess", ov.C_GOLD, "Choose a difficulty to start"
        self.draw_header(c, main, sub, col)

        b = self.pos.b
        king_sq = self.pos.ksq[0 if self.pos.turn == 1 else 1] if self.check else -1
        for row in range(8):
            for colm in range(8):
                sq = self._sq_of(colm, row)
                x, y = self.left + colm * cs, self.top - (row + 1) * cs
                light = ((sq >> 3) + (sq & 7)) % 2 == 1
                c.rect(x, y, cs, cs, C_LIGHT if light else C_DARKSQ)
                if self.last and sq in (self.last[0], self.last[1]):
                    c.rect(x, y, cs, cs, C_LAST)
                if sq == king_sq:
                    c.rect(x, y, cs, cs, C_CHECK)
                if sq == self.sel:
                    c.rect(x, y, cs, cs, C_SEL)
                elif sq == self.hover and self._human_turn() and not self.promo and (
                        sq in self.targets or (b[sq] and (b[sq] > 0) == (self.pos.turn > 0))):
                    c.rect(x, y, cs, cs, C_HOVER)
                lab = C_DARKSQ if light else C_LIGHT
                if colm == 0:
                    c.text(str((sq >> 3) + 1), x + cs * 0.11, y + cs * 0.87, cs * 0.17, lab, 'left')
                if row == 7:
                    c.text("abcdefgh"[sq & 7], x + cs * 0.90, y + cs * 0.12, cs * 0.17, lab, 'right')

        for sq in range(64):
            p = b[sq]
            if p:
                colm, row = self._cell_of(sq)
                x, y = self.left + colm * cs, self.top - (row + 1) * cs
                draw_piece(c, abs(p), p > 0, x + cs / 2, y + cs / 2, cs * 0.92)

        if self.sel >= 0:
            sel_pawn = abs(b[self.sel]) == 1
            for t in self.targets:
                colm, row = self._cell_of(t)
                cx = self.left + colm * cs + cs / 2
                cy = self.top - (row + 1) * cs + cs / 2
                if b[t] or (sel_pawn and (t & 7) != (self.sel & 7)):
                    c.ring(cx, cy, cs * 0.40, cs * 0.09, C_HINT)
                else:
                    c.circle(cx, cy, cs * 0.14, C_HINT, 16)

        bx, by, bw = self.left, self.top - 8 * cs, 8 * cs
        if self.menu:
            c.rect(bx, by, bw, bw, (0.0, 0.0, 0.0, 0.70))
            c.text("Select difficulty", self.left + 4 * cs, self.top - 0.8 * cs, cs * 0.42, ov.C_WHITE)
            for i in range(len(LEVELS) + 1):
                x0, y0, w0, h0 = self._menu_rect(i)
                c.rect(x0, y0, w0, h0, ov.C_CELL_HOVER if i == self.menu_hover else ov.C_CELL)
                if i < len(LEVELS):
                    c.text("%d  %s" % (i + 1, LEVELS[i]), x0 + 0.25 * cs, y0 + h0 * 0.64,
                           cs * 0.30, ov.C_WHITE, 'left')
                    c.text(LEVEL_INFO[i], x0 + 0.25 * cs, y0 + h0 * 0.28, cs * 0.17, ov.C_TEXT, 'left')
                    bt = self._best_time(i)
                    c.text("Best %s" % self._fmt(bt) if bt is not None else "No win yet",
                           x0 + w0 - 0.25 * cs, y0 + h0 * 0.5, cs * 0.20,
                           ov.C_GOLD if bt is not None else ov.C_TEXT, 'right')
                else:
                    c.text("M  2 players", x0 + 0.25 * cs, y0 + h0 * 0.5, cs * 0.30, ov.C_WHITE, 'left')
            self.draw_hint(c, "Click or press 1-4 to pick a difficulty   M two players   ESC quit")
        elif self.promo:
            c.rect(bx, by, bw, bw, (0.0, 0.0, 0.0, 0.55))
            px0, py0 = self._promo_rect()
            c.rect(px0 - 3 * u, py0 - 3 * u, 4 * cs + 6 * u, cs + 6 * u, ov.C_BORDER)
            white = self.pos.turn == 1
            for i, kind in enumerate(PROMO_CHOICES):
                x = px0 + i * cs
                c.rect(x, py0, cs, cs, ov.C_CELL_HOVER if i == self.promo_hover else ov.C_CELL)
                draw_piece(c, kind, white, x + cs / 2, py0 + cs / 2, cs * 0.92)
            self.draw_hint(c, "Choose the promotion piece   (right click cancels)")
        elif self.status != 'play':
            self.banner(c, bx, by, bw, bw, main, "Click the board or press R for a new game")
            self.draw_hint(c, "U undo   R new game   ESC quit")
        else:
            self.draw_hint(c, "Click: select / move   M mode  D menu  C side  F flip  U undo  R new")


RUNNER = ov.Runner("chess", GAME_NAME, Chess, [
    "LMB: select a piece, then its target",
    "RMB: deselect",
    "M: vs CPU / 2 players",
    "D: difficulty menu (easy / medium / hard / expert)",
    "C: play as white / black",
    "F: flip board   U: undo",
    "R: new game   ESC: quit",
])


def draw_ui(layout, context):
    RUNNER.draw_ui(layout)


def register():
    RUNNER.register()


def unregister():
    RUNNER.unregister()
