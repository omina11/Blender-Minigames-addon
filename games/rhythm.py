"""Rhythm - a Guitar-Hero-style note highway (GPU overlay).

Hit A S D J K L as the notes reach the line. Everything is original:
the five songs are composed in this file and the backing track and the
instrument sounds are synthesised by the code (nothing copyrighted, no
audio files shipped). Audio uses Blender's built-in `aud` module; if it is
not available the game simply runs silent.
"""

import math
import os
import random
import tempfile
import threading
import time
import wave
from array import array

from . import _overlay as ov
from . import _record as _rec

GAME_NAME = "Rhythm"
GAME_ICON = 'SOUND'

# ----------------------------------------------------------------------------
# Timing / scoring
# ----------------------------------------------------------------------------

# seconds of silence before bar 1 (also baked into the wav)
LEAD = 2.0
PERFECT_W, GREAT_W, GOOD_W = 0.050, 0.100, 0.150
BASE_POINTS = {'PERFECT': 100, 'GREAT': 70, 'GOOD': 40}
METER_GAIN = {'PERFECT': 0.025, 'GREAT': 0.020, 'GOOD': 0.012}
METER_MISS, METER_GHOST = 0.07, 0.015
DIFFS = ("Easy", "Medium", "Hard", "Expert")
APPROACH = (1.9, 1.7, 1.5, 1.3)     # seconds a note takes to travel the highway
NL = 6  # number of lanes
HALF = NL / 2.0
LANE_KEYS = {'A': 0, 'S': 1, 'D': 2, 'J': 3, 'K': 4, 'L': 5}
LANE_LABEL = ("A", "S", "D", "J", "K", "L")
LANE_COLORS = ((0.30, 0.88, 0.42, 1.0), (0.95, 0.30, 0.30, 1.0), (0.97, 0.85, 0.25, 1.0),
               (0.30, 0.55, 1.00, 1.0), (1.00, 0.60, 0.20, 1.0), (0.70, 0.40, 0.95, 1.0))
# audio/display latency compensation in seconds (persists per session)
OFFSET = [0.0]
BEST = {}                      # (song index, difficulty) -> best score

# ----------------------------------------------------------------------------
# Original songs: 16-step bars, digits are scale degrees of the lead melody (0-9)
# ----------------------------------------------------------------------------

MINOR_PENTA = (0, 3, 5, 7, 10)
MAJOR_PENTA = (0, 2, 4, 7, 9)

SONGS = [
    dict(title="Neon Run", bpm=118, root=57, scale=MINOR_PENTA,
         note="A minor groove",
         phrases={'A': "3...5..7..5.3...", 'B': "5..7..8.7.5..3..", 'C': "8.7.5.7.8.9.8.5.",
                  'D': "7...5.3.5...3.2.", 'E': "3.5.7.5.3.5.7.9.", 'F': "9..7.5..7..5.3..",
                  'G': "5.5.7.5.3.3.5.3.", 'H': "0.2.3.5.7.5.3.2."},
         form="ABADCECFABGDCEHFABADCEHD",
         chords=[(0, (0, 3, 7)), (-4, (0, 4, 7)),
                 (3, (0, 4, 7)), (-2, (0, 4, 7))],
         kick={0, 4, 8, 12}, kick_odd={10}, snare={4, 12}, hat=set(range(0, 16, 2))),
    dict(title="Pixel Sunrise", bpm=104, root=60, scale=MAJOR_PENTA,
         note="Bright and bouncy",
         phrases={'A': "4...5...7...5.4.", 'B': "5.7.8...7.5.4...", 'C': "2.4.5.4.2.4.5.7.",
                  'D': "7...8...9.8.7.5.", 'E': "4.4.5.7...7.5.4.", 'F': "9.8.7.5.4.2.0...",
                  'G': "5...7.5.4...2.4.", 'H': "2.2.4.5.7.5.4.2."},
         form="ABADEBEFGBGDEBHFABADEBHF",
         chords=[(0, (0, 4, 7)), (7, (0, 4, 7)),
                 (9, (0, 3, 7)), (5, (0, 4, 7))],
         kick={0, 6, 8, 14}, kick_odd={3}, snare={4, 12}, hat=set(range(0, 16, 2))),
    dict(title="Circuit Breaker", bpm=140, root=52, scale=MINOR_PENTA,
         note="Fast and busy",
         phrases={'A': "3.35.57.8.7.5.3.", 'B': "5.57.78.9.8.7.5.", 'C': "8.98.75.7.53.2.3",
                  'D': "3335.5.7.7.9.8.7", 'E': "0.23.35.57.79.97", 'F': "9.89.87.8.75.53.",
                  'G': "5.5.5.7.8.8.9.8.", 'H': "7.5.3.5.7.9.8.7."},
         form="ABACDEDFABACGEGHABACDEDH",
         chords=[(0, (0, 3, 7)), (-4, (0, 4, 7)),
                 (-2, (0, 4, 7)), (-5, (0, 3, 7))],
         kick={0, 3, 8, 11}, kick_odd={6, 14}, snare={4, 12}, hat=set(range(0, 16, 2)) | {15}),
    dict(title="Midnight Drive", bpm=92, root=55, scale=MINOR_PENTA,
         note="Laid-back night cruise",
         phrases={'A': "5...3...5.7.5.3.", 'B': "7...8.7.5...3...", 'C': "3.5.7...5.3.2.3.",
                  'D': "8...7.5.7...5.3.", 'E': "5.5.7.8.7.5.3.5.", 'F': "9...8...7.5.7...",
                  'G': "2...3.5.3...2...", 'H': "0.3.5.7.8.7.5.3."},
         form="ABACDEFABGCDEHFABACDEHFD",
         chords=[(0, (0, 3, 7)), (-2, (0, 4, 7)),
                 (-4, (0, 4, 7)), (-5, (0, 3, 7))],
         kick={0, 8}, kick_odd={11}, snare={4, 12}, hat=set(range(0, 16, 2))),
    dict(title="Solar Flare", bpm=128, root=62, scale=MAJOR_PENTA,
         note="Driving major anthem",
         phrases={'A': "4.4.5.7.9.7.5.4.", 'B': "7.7.8.9.8.7.5.4.", 'C': "9.8.7.5.7.8.9...",
                  'D': "5...7...9...8.7.", 'E': "2.4.5.7.9.7.5.4.", 'F': "9.9.8.7.5.4.2.4.",
                  'G': "7.5.4.2.4.5.7.9.", 'H': "8...9...8.7.5.7."},
         form="ABABCDEFABABGDEHCDEFGHFH",
         chords=[(0, (0, 4, 7)), (5, (0, 4, 7)),
                 (7, (0, 4, 7)), (9, (0, 3, 7))],
         kick={0, 4, 8, 12}, kick_odd={14}, snare={4, 12}, hat=set(range(0, 16, 2))),
]


def lead_midi(song, d):
    return song['root'] + song['scale'][d % 5] + 12 * (d // 5)


def midi_freq(m):
    return 440.0 * 2 ** ((m - 69) / 12.0)


class Note:
    # state: 0 pending, 1 hit, 2 missed
    __slots__ = ('t', 'lane', 'midi', 'state')

    def __init__(self, t, lane, midi):
        self.t, self.lane, self.midi, self.state = t, lane, midi, 0


def build_chart(song, diff):
    """Turn the melody into notes. Easy = quarter notes, Medium = eighths, Hard = all + chords, Expert = more chords."""
    step = 60.0 / (song['bpm'] * 4)
    notes = []
    last = [-9.0] * NL
    for bar, key in enumerate(song['form']):
        for s, ch in enumerate(song['phrases'][key]):
            if ch == '.' or (diff == 0 and s % 4) or (diff == 1 and s % 2):
                continue
            d = int(ch)
            lane = min(NL - 1, d * NL // 10)
            t = (bar * 16 + s) * step
            if t - last[lane] < 0.09:
                continue
            notes.append(Note(t, lane, lead_midi(song, d)))
            last[lane] = t
            if (diff == 2 and s == 0 and bar % 2 == 0) or (diff == 3 and s in (0, 8)):
                l2 = lane + 3 if lane + 3 <= NL - 1 else lane - 3
                notes.append(Note(t, l2, lead_midi(song, min(9, round(l2 * 1.8)))))
                last[l2] = t
    notes.sort(key=lambda n: (n.t, n.lane))
    return notes


# ----------------------------------------------------------------------------
# Procedural audio: backing track rendered to a wav (pure Python) + live tones
# ----------------------------------------------------------------------------

SR = 22050
WAV_VERSION = 1


def _tone(freq, dur, kind, vol):
    n = int(dur * SR)
    out = [0.0] * n
    for i in range(n):
        p = (freq * i / SR) % 1.0
        w = 4.0 * abs(p - 0.5) - \
            1.0 if kind == 'tri' else (0.6 if p < 0.5 else -0.6)
        env = min(1.0, i / (0.004 * SR)) * math.exp(-3.2 * i / n)
        out[i] = w * env * vol
    return out


def _kick():
    n = int(0.22 * SR)
    out, ph = [0.0] * n, 0.0
    for i in range(n):
        t = i / SR
        ph += 2 * math.pi * (45 + 110 * math.exp(-t * 28)) / SR
        out[i] = math.sin(ph) * math.exp(-t * 9) * 0.9
    return out


def _snare():
    rng = random.Random(1)
    n = int(0.18 * SR)
    return [(rng.uniform(-1, 1) * math.exp(-i / SR * 22) * 0.45 +
             math.sin(2 * math.pi * 185 * i / SR) * math.exp(-i / SR * 30) * 0.35) for i in range(n)]


def _hat():
    rng = random.Random(2)
    n = int(0.05 * SR)
    prev, out = 0.0, []
    for i in range(n):
        v = rng.uniform(-1, 1)
        out.append((v - prev) * math.exp(-i / SR * 90) * 0.25)
        prev = v
    return out


def render_backing(song, path):
    """Synthesise bass + drums + a soft arpeggio. Returns the duration in seconds."""
    step = 60.0 / (song['bpm'] * 4)
    n_steps = len(song['form']) * 16
    total = int((LEAD + n_steps * step + 2.0) * SR)
    buf = [0.0] * total
    cache = {}

    def get(key, make):
        if key not in cache:
            cache[key] = make()
        return cache[key]

    def add(w, t, gain=1.0):
        o = int((LEAD + t) * SR)
        for i in range(min(len(w), total - o)):
            buf[o + i] += w[i] * gain

    kick, snare, hat = _kick(), _snare(), _hat()
    for st in range(n_steps):
        bar, s = divmod(st, 16)
        t = st * step
        off, ivs = song['chords'][bar % len(song['chords'])]
        kicks = song['kick'] | (song['kick_odd'] if bar % 2 else set())
        if s in kicks:
            add(kick, t, 0.8)
        if s in song['snare']:
            add(snare, t, 0.7)
        if s in song['hat']:
            add(hat, t, 0.6 if s % 4 == 0 else 0.4)
        if s % 2 == 0:                                              # bass, eighth notes
            m = song['root'] - 24 + off + (12 if s in (6, 14) else 0)
            add(get(('b', m), lambda m=m: _tone(
                midi_freq(m), step * 1.9, 'sq', 0.30)), t)
        a = ivs[(st % 4) % len(ivs)] + (12 if st %
                                        8 >= 4 else 0)   # quiet arpeggio texture
        m = song['root'] + off + a
        add(get(('a', m), lambda m=m: _tone(midi_freq(m), step * 1.6, 'tri', 0.10)), t)

    peak = max(max(buf), -min(buf), 1e-6)
    g = 0.85 / peak
    pcm = array('h', (int(max(-1.0, min(1.0, v * g)) * 32767) for v in buf))
    with wave.open(path, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(pcm.tobytes())
    return total / SR


_WAV = {}           # song index -> path of the rendered backing track
_WAV_LOCK = threading.Lock()


def wav_path(i):
    return os.path.join(tempfile.gettempdir(), "minigames_rhythm_%d_v%d.wav" % (i, WAV_VERSION))


def ensure_wav(i):
    """Render the backing track once per session (blocking; call from a worker thread)."""
    with _WAV_LOCK:
        p = _WAV.get(i)
        if p and os.path.exists(p):
            return p
        p = wav_path(i)
        render_backing(SONGS[i], p)
        _WAV[i] = p
        return p


class _Audio:
    """Thin, defensive wrapper around Blender's `aud`. Any failure just means silence."""

    def __init__(self):
        self.state = None            # None = not tried, True / False afterwards
        self.aud = self.dev = self.handle = None
        self.cache = {}
        self.warned = False

    def available(self):
        if self.state is None:
            try:
                import aud
                self.aud, self.dev, self.state = aud, aud.Device(), True
            except Exception:
                self.state = False
        return self.state

    def _warn(self, e):
        if not self.warned:
            print("[Minigames/Rhythm] audio problem, continuing: %r" % (e,))
            self.warned = True

    def play_file(self, path):
        try:
            try:
                snd = self.aud.Sound(path)
            except Exception:
                snd = self.aud.Sound.file(path)
            self.handle = self.dev.play(snd)
            self.handle.volume = 0.85
            return True
        except Exception as e:
            self._warn(e)
            self.handle = None
            return False

    def _call(self, name):
        try:
            if self.handle is not None:
                getattr(self.handle, name)()
        except Exception as e:
            self._warn(e)

    def stop(self):
        self._call('stop')
        self.handle = None

    def pause(self):
        self._call('pause')

    def resume(self):
        self._call('resume')

    def tone(self, midi):
        if not self.available():
            return
        try:
            snd = self.cache.get(midi)
            if snd is None:
                snd = (self.aud.Sound.triangle(midi_freq(midi)).limit(0, 0.35)
                       .fadeout(0.12, 0.2).volume(0.30))
                self.cache[midi] = snd
            self.dev.play(snd)
        except Exception as e:
            self._warn(e)

    def buzz(self):
        if not self.available():
            return
        try:
            snd = self.cache.get('buzz')
            if snd is None:
                snd = self.aud.Sound.sawtooth(82.0).limit(
                    0, 0.12).fadeout(0.04, 0.08).volume(0.16)
                self.cache['buzz'] = snd
            self.dev.play(snd)
        except Exception as e:
            self._warn(e)


AUDIO = _Audio()

# ----------------------------------------------------------------------------
# Game
# ----------------------------------------------------------------------------

W, H = 420.0, 560.0
LW = 52.0                         # lane width at the hit line
CX = 205.0                        # highway centre x
Y_HIT, Y_FAR = 105.0, 490.0
PERSP = 2.0
S_FAR = 1.0 / (1.0 + PERSP)
U_BOTTOM = -0.05                  # how far below the hit line the highway is drawn

C_FIELD = (0.04, 0.04, 0.07, 1.0)
C_ROAD = (0.08, 0.09, 0.14, 1.0)


def _persp(u):
    return 1.0 / (1.0 + PERSP * u)


def _y_of(u):
    return Y_HIT + (Y_FAR - Y_HIT) * (1.0 - _persp(u)) / (1.0 - S_FAR)


def _ellipse(cx, cy, rx, ry, n=16):
    return [(cx + rx * math.cos(2 * math.pi * k / n), cy + ry * math.sin(2 * math.pi * k / n))
            for k in range(n)]


class Rhythm(ov.BaseGame):
    keys = (tuple(LANE_KEYS) + ('SPACE', 'P', 'COMMA', 'PERIOD', 'UP_ARROW', 'DOWN_ARROW',
                                'LEFT_ARROW', 'RIGHT_ARROW', 'RET', 'NUMPAD_ENTER',
                                'ONE', 'TWO', 'THREE', 'FOUR', 'FIVE'))

    def setup(self):
        self.song_i = 0
        self.diff = 1
        self.token = 0
        self.closed = False
        self.record_mgrs = {}  # (song, diff) -> RecordManager, created on demand

    def _mgr(self, song_i, diff):
        """One record file per song and difficulty: rhythm_<song>_<diff>.json"""
        k = (song_i, diff)
        if k not in self.record_mgrs:
            try:
                name = "rhythm_%s_%s" % (
                    SONGS[song_i]['title'].lower().replace(' ', '_'), DIFFS[diff].lower())
                self.record_mgrs[k] = _rec.RecordManager(game_name=name)
            except Exception:
                self.record_mgrs[k] = None
        return self.record_mgrs[k]

    def _best(self, song_i, diff):
        """Highest saved score for this song + difficulty (cached)."""
        k = (song_i, diff)
        if k not in BEST:
            m = self._mgr(song_i, diff)
            try:
                BEST[k] = int(m.get_records().get("highest_score", 0)) if m else 0
            except Exception:
                BEST[k] = 0
        return BEST[k]

    def reset(self):
        cur = getattr(self, 'state', 'menu')
        if cur in ('play', 'paused', 'failed', 'results', 'loading') and hasattr(self, 'song'):
            self._start_song()               # R restarts the current song
        else:
            self._to_menu()
        self.sc = 1.0
        self.fx0 = self.fy0 = 0.0
        self.menu_hover = None

    # ---- lifecycle
    def close(self):
        """Called by the runner when the game is quit: stop any audio."""
        self.closed = True
        self.token += 1
        AUDIO.stop()

    def _to_menu(self):
        self.token += 1
        AUDIO.stop()
        self.state = 'menu'
        self.notes = []
        self.lane_notes = [[] for _ in range(NL)]
        self.ptr = [0] * NL
        self.popups = []
        self.bursts = []
        self.flash = [-9.0] * NL
        self.t0 = 0.0
        self.pause_t = 0.0
        self.menu_hover = None

    def _start_song(self):
        self.token += 1
        AUDIO.stop()
        self.song = SONGS[self.song_i]
        self.notes = build_chart(self.song, self.diff)
        self.lane_notes = [[n for n in self.notes if n.lane == l]
                           for l in range(NL)]
        self.ptr = [0] * NL
        self.score = 0
        self.combo = 0
        self.max_combo = 0
        self.meter = 0.5
        self.counts = {'PERFECT': 0, 'GREAT': 0, 'GOOD': 0, 'MISS': 0}
        self.ghosts = 0
        self.popups = []              # [text, colour, wall_time, lane]
        self.bursts = []              # [lane, wall_time]
        self.flash = [-9.0] * NL
        self.pause_t = 0.0
        self.stars = 0
        self.new_best = False
        self.tvis = APPROACH[self.diff]
        self.end_t = (self.notes[-1].t if self.notes else 0.0) + 1.3
        if AUDIO.available():
            self.state = 'loading'
            tok = self.token
            idx = self.song_i

            def job():
                try:
                    ensure_wav(idx)
                except Exception as e:
                    print(
                        "[Minigames/Rhythm] could not render backing track: %r" % (e,))
                self._wav_done = tok
            self._wav_done = -1
            threading.Thread(target=job, daemon=True).start()
        else:
            self._begin_play(None)

    def _begin_play(self, path):
        self.t0 = time.perf_counter()
        if path and AUDIO.available():
            AUDIO.play_file(path)
        self.state = 'play'

    # ---- clock
    def now(self):
        """Song time in seconds (negative during the lead-in), compensated by the offset."""
        base = self.pause_t if self.state == 'paused' else time.perf_counter() - \
            self.t0
        return base - LEAD - OFFSET[0]

    # ---- judging
    def _mult(self):
        return 1 + min(3, self.combo // 10)

    def _resolve_misses(self, lane, t):
        notes, p = self.lane_notes[lane], self.ptr[lane]
        while p < len(notes) and notes[p].t + GOOD_W < t:
            notes[p].state = 2
            self._miss(notes[p])
            p += 1
        self.ptr[lane] = p

    def _miss(self, note):
        self.counts['MISS'] += 1
        self.combo = 0
        self.meter = max(0.0, self.meter - METER_MISS)
        self.popups.append(["MISS", ov.C_BAD, time.perf_counter(), note.lane])
        AUDIO.buzz()

    def _press(self, lane):
        t = self.now()
        self.flash[lane] = time.perf_counter()
        self._resolve_misses(lane, t)
        notes, p = self.lane_notes[lane], self.ptr[lane]
        if p < len(notes) and abs(notes[p].t - t) <= GOOD_W:
            n = notes[p]
            d = abs(n.t - t)
            j = 'PERFECT' if d <= PERFECT_W else 'GREAT' if d <= GREAT_W else 'GOOD'
            n.state = 1
            self.ptr[lane] += 1
            self.score += BASE_POINTS[j] * self._mult()
            self.counts[j] += 1
            self.combo += 1
            self.max_combo = max(self.max_combo, self.combo)
            self.meter = min(1.0, self.meter + METER_GAIN[j])
            col = ov.C_GOLD if j == 'PERFECT' else ov.C_GOOD if j == 'GREAT' else ov.C_TEXT
            self.popups.append([j, col, time.perf_counter(), lane])
            self.bursts.append([lane, time.perf_counter()])
            AUDIO.tone(n.midi)
        else:                                                         # nothing to hit: a wasted press
            self.ghosts += 1
            self.meter = max(0.0, self.meter - METER_GHOST)
        if self.meter <= 0.0:
            self._fail()

    def _fail(self):
        AUDIO.stop()
        self.state = 'failed'
        m = self._mgr(self.song_i, self.diff)
        if m:
            m.add_lose_record()

    def _finish(self):
        total = len(self.notes)
        acc = 0.0 if not total else (self.counts['PERFECT'] + 0.7 * self.counts['GREAT'] +
                                     0.4 * self.counts['GOOD']) / total
        self.accuracy = acc
        self.stars = 5 if acc >= 0.95 else 4 if acc >= 0.85 else 3 if acc >= 0.70 else 2 if acc >= 0.50 else 1
        key = (self.song_i, self.diff)
        self.new_best = self.score > self._best(*key)
        if self.new_best:
            BEST[key] = self.score
        self.state = 'results'
        m = self._mgr(self.song_i, self.diff)
        if m:
            m.add_win_record(score=self.score)

    # ---- update
    def update(self, dt):
        if self.state == 'loading':
            if getattr(self, '_wav_done', -1) == self.token:
                self._begin_play(_WAV.get(self.song_i))
            return True
        if self.state != 'play':
            return False
        t = self.now()
        for lane in range(NL):
            self._resolve_misses(lane, t)
        if self.meter <= 0.0:
            self._fail()
        elif t > self.end_t:
            self._finish()
        now = time.perf_counter()
        self.popups = [p for p in self.popups if now - p[2] < 0.6]
        self.bursts = [b for b in self.bursts if now - b[1] < 0.25]
        return True

    # ---- input
    def key(self, key, repeat):
        st = self.state
        if key in LANE_KEYS:
            if st == 'play' and not repeat:
                self._press(LANE_KEYS[key])
            return
        if key == 'SPACE':
            if st in ('menu', 'results', 'failed'):
                key = 'RET'
            else:
                return
        if key == 'COMMA':
            OFFSET[0] = max(-0.5, OFFSET[0] - 0.01)
        elif key == 'PERIOD':
            OFFSET[0] = min(0.5, OFFSET[0] + 0.01)
        elif key == 'P':
            if st == 'play':
                self.pause_t = time.perf_counter() - self.t0
                self.state = 'paused'
                AUDIO.pause()
            elif st == 'paused':
                self.t0 = time.perf_counter() - self.pause_t
                self.state = 'play'
                AUDIO.resume()
        elif st == 'menu':
            if key == 'UP_ARROW':
                self.song_i = (self.song_i - 1) % len(SONGS)
            elif key == 'DOWN_ARROW':
                self.song_i = (self.song_i + 1) % len(SONGS)
            elif key == 'LEFT_ARROW':
                self.diff = (self.diff - 1) % len(DIFFS)
            elif key == 'RIGHT_ARROW':
                self.diff = (self.diff + 1) % len(DIFFS)
            elif key in ('ONE', 'TWO', 'THREE', 'FOUR', 'FIVE'):
                self.song_i = {'ONE': 0, 'TWO': 1, 'THREE': 2, 'FOUR': 3, 'FIVE': 4}[key]
            elif key in ('RET', 'NUMPAD_ENTER'):
                self._start_song()
        elif st in ('results', 'failed') and key in ('RET', 'NUMPAD_ENTER'):
            self._to_menu()

    # ---- menu geometry (logical coordinates)
    def _song_rect(self, i):
        return 30.0, 432.0 - i * 58.0, 360.0, 52.0

    def _diff_rect(self, i):
        return 30.0 + i * 92.0, 136.0, 84.0, 38.0

    def _start_rect(self):
        return 110.0, 74.0, 200.0, 44.0

    def _to_logical(self, x, y):
        return (x - self.fx0) / self.sc, (y - self.fy0) / self.sc

    @staticmethod
    def _inside(r, lx, ly):
        return r[0] <= lx <= r[0] + r[2] and r[1] <= ly <= r[1] + r[3]

    def _menu_target(self, x, y):
        lx, ly = self._to_logical(x, y)
        for i in range(len(SONGS)):
            if self._inside(self._song_rect(i), lx, ly):
                return ('song', i)
        for i in range(len(DIFFS)):
            if self._inside(self._diff_rect(i), lx, ly):
                return ('diff', i)
        if self._inside(self._start_rect(), lx, ly):
            return ('start', 0)
        return None

    def move(self, x, y):
        if self.state != 'menu':
            return False
        h = self._menu_target(x, y)
        changed = h != self.menu_hover
        self.menu_hover = h
        return changed

    def click(self, x, y, button):
        if button != 'LEFT':
            return
        if self.state == 'menu':
            t = self._menu_target(x, y)
            if t is None:
                return
            kind, i = t
            if kind == 'song':
                if self.song_i == i:
                    self._start_song()
                self.song_i = i
            elif kind == 'diff':
                self.diff = i
            else:
                self._start_song()
        elif self.state in ('results', 'failed'):
            self._to_menu()
        elif self.state == 'paused':
            self.key('P', False)

    def layout(self, view):
        cs = self.fit_cell(view, 6, 7, max_cell=80, min_cell=30)
        self.place(view, 6 * cs, 7 * cs)
        self.sc = cs / 70.0
        self.fx0 = self.left
        self.fy0 = self.top - 7 * cs

    # ---- drawing
    def _X(self, v):
        return self.fx0 + v * self.sc

    def _Y(self, v):
        return self.fy0 + v * self.sc

    def _poly(self, c, pts, col):
        c.poly([(self._X(x), self._Y(y)) for x, y in pts], col)

    def _ell(self, c, cx, cy, rx, ry, col):
        self._poly(c, _ellipse(cx, cy, rx, ry), col)

    def _text(self, c, s, x, y, size, col=ov.C_TEXT, align='center'):
        c.text(s, self._X(x), self._Y(y), size * self.sc, col, align)

    def draw(self, c):
        self.draw_frame(c)
        c.rect(self.fx0, self.fy0, W * self.sc, H * self.sc, C_FIELD)
        st = self.state
        if st == 'menu':
            self._draw_menu(c)
        elif st == 'loading':
            self.draw_header(
                c, "Rhythm", "Composing the backing track...", ov.C_WHITE)
            self._text(c, "Loading...", W / 2, H / 2, 24, ov.C_WHITE)
            self.draw_hint(
                c, "One moment - the music is generated by the code")
        elif st in ('play', 'paused'):
            self._draw_play(c)
        elif st == 'failed':
            self._draw_play(c)
            self.banner(c, self.fx0, self.fy0, W * self.sc, H * self.sc, "Song failed",
                        "Click or SPACE: menu   R: retry")
        elif st == 'results':
            self._draw_results(c)

    def _draw_menu(self, c):
        self.draw_header(
            c, "Rhythm", "Pick a song, then press ENTER", ov.C_GOLD)
        self._text(c, "Hit A  S  D  J  K  L  on the beat",
                   W / 2, 520, 15, ov.C_WHITE)
        self._text(c, "Original songs and sounds, made by code",
                   W / 2, 498, 11, ov.C_TEXT)
        for i, s in enumerate(SONGS):
            x, y, w, h = self._song_rect(i)
            sel = i == self.song_i
            hov = self.menu_hover == ('song', i)
            if sel:
                c.rect(self._X(x) - 2, self._Y(y) - 2, w *
                       self.sc + 4, h * self.sc + 4, ov.C_GOLD)
            c.rect(self._X(x), self._Y(y), w * self.sc, h * self.sc,
                   ov.C_CELL_HOVER if hov else ov.C_CELL)
            self._text(c, s['title'], x + 14, y + 31, 17, ov.C_WHITE, 'left')
            self._text(c, "%d BPM  -  %s" %
                       (s['bpm'], s['note']), x + 14, y + 11, 11, ov.C_TEXT, 'left')
            best = self._best(i, self.diff)
            self._text(c, "Best %d" % best if best else "No score yet", x + w - 14, y + 11, 11,
                       ov.C_GOLD if best else ov.C_TEXT, 'right')
        for i, name in enumerate(DIFFS):
            x, y, w, h = self._diff_rect(i)
            sel = i == self.diff
            hov = self.menu_hover == ('diff', i)
            col = (0.30, 0.72, 0.42, 1.0) if sel else (
                ov.C_CELL_HOVER if hov else ov.C_CELL)
            c.rect(self._X(x), self._Y(y), w * self.sc, h * self.sc, col)
            self._text(c, name, x + w / 2, y + h / 2, 14,
                       ov.C_DARK if sel else ov.C_TEXT)
        x, y, w, h = self._start_rect()
        hov = self.menu_hover == ('start', 0)
        c.rect(self._X(x), self._Y(y), w * self.sc, h * self.sc,
               ov.shade(LANE_COLORS[2], 1.1 if hov else 0.95))
        self._text(c, "START", x + w / 2, y + h / 2, 20, ov.C_DARK)
        self._text(c, "Audio offset %+d ms  ( , and . to adjust )" % round(OFFSET[0] * 1000),
                   W / 2, 38, 11, ov.C_TEXT)
        self.draw_hint(
            c, "Up/Down song  Left/Right level  ENTER start  1-5 quick pick  ESC quit")

    def _draw_play(self, c):
        sc = self.sc
        now = time.perf_counter()
        t = self.now()
        tv = self.tvis
        diff_name = DIFFS[self.diff]
        self.draw_header(c, self.song['title'], "Score %d   Combo %d   x%d   |   %s" % (
            self.score, self.combo, self._mult(), diff_name), ov.C_WHITE)

        # highway
        ub, ut = U_BOTTOM, 1.0
        sb, stp = _persp(ub), _persp(ut)
        yb, yt = _y_of(ub), _y_of(ut)
        hwb, hwt = HALF * LW * sb, HALF * LW * stp
        self._poly(c, [(CX - hwb, yb), (CX + hwb, yb),
                   (CX + hwt, yt), (CX - hwt, yt)], C_ROAD)
        for k in range(NL + 1):
            off = k - HALF
            c.line((self._X(CX + off * LW * sb), self._Y(yb)), (self._X(CX + off * LW * stp), self._Y(yt)),
                   1.4 * sc + 0.4, (0.35, 0.38, 0.50, 0.55))
        beat = 60.0 / self.song['bpm']
        b = max(0, int((t - 0.2) / beat))
        while b * beat - t < tv:
            u = (b * beat - t) / tv
            if u >= ub:
                s = _persp(u)
                y = _y_of(u)
                bar = b % 4 == 0
                c.line((self._X(CX - HALF * LW * s), self._Y(y)), (self._X(CX + HALF * LW * s), self._Y(y)),
                       (2.2 if bar else 1.2) * sc * s + 0.4, (1.0, 1.0, 1.0, 0.30 if bar else 0.12))
            b += 1

        # notes
        for lane in range(NL):
            col = LANE_COLORS[lane]
            for n in self.lane_notes[lane][self.ptr[lane]:]:
                u = (n.t - t) / tv
                if u > 1.0:
                    break
                if u < ub:
                    continue
                s = _persp(u)
                x, y = CX + (lane - (NL - 1) / 2.0) * LW * s, _y_of(u)
                rx = 0.44 * LW * s
                self._ell(c, x, y, rx * 1.18, rx *
                          0.72, (0.05, 0.05, 0.08, 1.0))
                self._ell(c, x, y, rx, rx * 0.58, col)
                self._ell(c, x, y + rx * 0.08, rx * 0.5,
                          rx * 0.22, (1.0, 1.0, 1.0, 0.55))

        # frets and hit effects
        for lane in range(NL):
            col = LANE_COLORS[lane]
            x = CX + (lane - (NL - 1) / 2.0) * LW
            lit = max(0.0, 1.0 - (now - self.flash[lane]) / 0.14)
            self._ell(c, x, Y_HIT, 0.50 * LW, 0.50 * LW * 0.58, col)
            inner = tuple(0.07 + (col[i] - 0.07) *
                          lit for i in range(3)) + (1.0,)
            self._ell(c, x, Y_HIT, 0.42 * LW, 0.42 * LW * 0.58, inner)
            self._text(c, LANE_LABEL[lane], x, Y_HIT - 38, 13, col)
        for lane, bt in self.bursts:
            p = (now - bt) / 0.25
            x = CX + (lane - (NL - 1) / 2.0) * LW
            r = 0.5 * LW * (1.0 + 0.9 * p)
            pts = _ellipse(self._X(x), self._Y(Y_HIT),
                           r * sc, r * sc * 0.58, 20)
            c.polyline(pts + [pts[0]], 2.5 * sc,
                       (*LANE_COLORS[lane][:3], 1.0 - p))
        for text, col, ct, lane in self.popups:
            p = (now - ct) / 0.6
            self._text(c, text, CX + (lane - (NL - 1) / 2.0) * LW * 0.5,
                       Y_HIT + 38 + 26 * p, 15, (*col[:3], 1.0 - p))

        # rock meter + progress
        mx, my0, mh = W - 28.0, 140.0, 300.0
        self._poly(c, [(mx - 9, my0), (mx + 9, my0), (mx + 9,
                   my0 + mh), (mx - 9, my0 + mh)], (0.12, 0.13, 0.17, 1.0))
        m = max(0.0, min(1.0, self.meter))
        mc = ov.C_GOOD if m > 0.5 else ov.C_GOLD if m > 0.25 else ov.C_BAD
        self._poly(c, [(mx - 7, my0 + 2), (mx + 7, my0 + 2), (mx + 7, my0 + 2 + (mh - 4) * m),
                       (mx - 7, my0 + 2 + (mh - 4) * m)], mc)
        self._text(c, "ROCK", mx, my0 - 14, 10, ov.C_TEXT)
        prog = max(0.0, min(1.0, (t) / max(0.1, self.end_t)))
        self._poly(c, [(0, H - 4), (W, H - 4), (W, H), (0, H)],
                   (0.12, 0.13, 0.17, 1.0))
        self._poly(c, [(0, H - 4), (W * prog, H - 4),
                   (W * prog, H), (0, H)], LANE_COLORS[2])

        # combo / multiplier below the highway
        self._text(c, "COMBO %d   x%d" % (self.combo, self._mult()), CX, 22, 16,
                   ov.C_GOLD if self.combo >= 10 else ov.C_WHITE)
        if t < 0:
            self._text(c, "Get ready...  %d" %
                       (math.ceil(-t)), CX, 300, 26, ov.C_WHITE)
        if self.state == 'paused':
            self.banner(c, self.fx0, self.fy0, W * sc, H * sc, "Paused",
                        "P: resume   R: restart   ESC: quit   offset %+d ms" % round(OFFSET[0] * 1000))
        else:
            self.draw_hint(
                c, "A S D J K L hit   P pause   , . calibrate audio   R restart   ESC quit")

    def _draw_results(self, c):
        self.draw_header(c, "Song complete!", "%s  -  %s" %
                         (self.song['title'], DIFFS[self.diff]), ov.C_GOLD)
        stars = "*" * self.stars + "-" * (5 - self.stars)
        self._text(c, stars, W / 2, 470, 34, ov.C_GOLD)
        self._text(c, "%d" % self.score, W / 2, 415, 40, ov.C_WHITE)
        self._text(c, "NEW BEST!" if self.new_best else "Best %d" % self._best(self.song_i, self.diff),
                   W / 2, 380, 14, ov.C_GOOD if self.new_best else ov.C_TEXT)
        rows = [("Perfect", self.counts['PERFECT'], ov.C_GOLD), ("Great", self.counts['GREAT'], ov.C_GOOD),
                ("Good", self.counts['GOOD'], ov.C_TEXT), ("Missed", self.counts['MISS'], ov.C_BAD)]
        for i, (name, n, col) in enumerate(rows):
            y = 320 - i * 30
            self._text(c, name, 110, y, 16, col, 'left')
            self._text(c, str(n), 310, y, 16, ov.C_WHITE, 'right')
        self._text(c, "Accuracy %.1f%%   Max combo %d" % (
            self.accuracy * 100, self.max_combo), W / 2, 180, 15, ov.C_WHITE)
        self._text(c, "Click or SPACE: back to the menu",
                   W / 2, 120, 13, ov.C_TEXT)
        self.draw_hint(c, "R: play again   ESC: quit")


class _RhythmRunner(ov.Runner):
    """Runner that stops the music when the game is quit or Blender reloads."""

    def _teardown(self):
        g = self.game
        if g is not None and hasattr(g, 'close'):
            try:
                g.close()
            except Exception:
                pass
        super()._teardown()


RUNNER = _RhythmRunner("rhythm", GAME_NAME, Rhythm, [
    "Menu: arrows / click to pick song & level, ENTER to start",
    "Hit A S D J K L when notes reach the line",
    "Combo raises the multiplier (x1 to x4)",
    "Don't let the ROCK meter hit zero",
    "P: pause   , and . : calibrate audio latency",
    "R: restart song   ESC: quit",
])


def draw_ui(layout, context):
    RUNNER.draw_ui(layout)


def register():
    RUNNER.register()


def unregister():
    RUNNER.unregister()
