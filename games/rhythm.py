"""Rhythm - a Guitar-Hero-style note highway (GPU overlay).

Hit A S D J K L as the notes reach the line. Everything is original:
the five songs are composed in this file and the backing track and the
instrument sounds are synthesised by the code (nothing copyrighted, no
audio files shipped). Audio uses Blender's built-in `aud` module; if it is
not available the game simply runs silent.

MIDI library: drop .mid / .midi files in the "midi" folder next to this script
and pick "MIDI Library" in the menu (key 6 / M). The game finds the melody,
the rhythm and the drums by itself and builds a chart for every difficulty.
"""

import math
import os
import random
import shutil
import subprocess
import bisect
import struct
import tempfile
import threading
import time
import wave
import zlib
from array import array

try:
    import numpy as np
except Exception:                       # Blender ships numpy; without it the MIDI audio is simpler
    np = None

from . import _overlay as ov
from . import _record as _rec

GAME_NAME = "Rhythm"
GAME_ICON = 'SOUND'

# ----------------------------------------------------------------------------
# Timing / scoring
# ----------------------------------------------------------------------------

# seconds of silence before bar 1 (also baked into the wav)
LEAD = 2.0
# hit windows (perfect, great, good) in seconds, per difficulty - Expert is much stricter
WINDOWS = ((0.050, 0.100, 0.150), (0.050, 0.100, 0.150), (0.050, 0.100, 0.150), (0.034, 0.066, 0.098))
BASE_POINTS = {'PERFECT': 100, 'GREAT': 70, 'GOOD': 40}
METER_GAIN = {'PERFECT': 0.025, 'GREAT': 0.020, 'GOOD': 0.012}
# rock meter per difficulty: (loss on a miss, loss on a wasted key press, gain scale)
METER_CFG = ((0.07, 0.015, 1.0), (0.07, 0.015, 1.0), (0.07, 0.015, 1.0), (0.115, 0.035, 0.70))
DIFFS = ("Easy", "Medium", "Hard", "Expert")
APPROACH = (1.9, 1.7, 1.3, 0.95)    # seconds a note takes to travel the highway
NL = 6  # number of lanes
HALF = NL / 2.0
LANE_KEYS = {'A': 0, 'S': 1, 'D': 2, 'J': 3, 'K': 4, 'L': 5}
LANE_LABEL = ("A", "S", "D", "J", "K", "L")
LANE_COLORS = ((0.30, 0.88, 0.42, 1.0), (0.95, 0.30, 0.30, 1.0), (0.97, 0.85, 0.25, 1.0),
               (0.30, 0.55, 1.00, 1.0), (1.00, 0.60, 0.20, 1.0), (0.70, 0.40, 0.95, 1.0))
# audio/display latency compensation in seconds (persists per session)
OFFSET = [0.0]
BEST = {}                      # (song index, difficulty) -> best score
# long notes (sustains)
HOLD_MIN = 0.45                    # absolute minimum length (s) a MIDI note needs to be a candidate
HOLD_MIN_BY_DIFF = (0.45, 0.50, 0.80, 0.65)     # minimum length per difficulty
HOLD_RATIO = (0.35, 0.30, 0.12, 0.20)           # max share of long notes among the notes of any 8 s window
HOLD_MAX_CONCURRENT = (2, 2, 1, 2)              # long notes that may be held at the same time
HOLD_GRACE = 0.12                  # early-release tolerance (s)
HOLD_POINTS = 50
HARD_OVERLAP_KEEP = 0.25           # Hard: share of short notes kept while a long note is being held
KEYUP_SEEN = [False]               # becomes True as soon as the runner delivers key releases

# ----------------------------------------------------------------------------
# Original songs: 16-step bars, digits are scale degrees of the lead melody (0-9)
# ----------------------------------------------------------------------------

MINOR_PENTA = (0, 3, 5, 7, 10)
MAJOR_PENTA = (0, 2, 4, 7, 9)

SONGS = []


def lead_midi(song, d):
    return song['root'] + song['scale'][d % 5] + 12 * (d // 5)


def midi_freq(m):
    return 440.0 * 2 ** ((m - 69) / 12.0)


class Note:
    # state: 0 pending, 1 hit, 2 missed
    # state 3 = being held; dur > 0 means a long note (seconds)
    __slots__ = ('t', 'lane', 'midi', 'state', 'dur')

    def __init__(self, t, lane, midi, dur=0.0):
        self.t, self.lane, self.midi, self.state, self.dur = t, lane, midi, 0, dur


BUILTIN = 0                       # the 5 built-in synth songs are gone; defaultmidi/*.mid are loaded instead
DEFAULT_MIDI = []                 # indexes in SONGS of the defaultmidi songs
ZIGZAG = (0, 1, 2, 3, 4, 5, 4, 3, 2, 1)


def build_chart(song, diff):
    """Turn a song into notes.
    Easy = quarter notes, Medium = eighths, Hard = every melody note + chords on beats 1 and 3,
    Expert = melody + a chord on every beat + a stream of fill notes (zig-zag across the lanes),
    16th-note bursts at the end of each phrase and triple chords on the downbeats."""
    if song.get('midi'):
        return [Note(t, lane, p, d) for t, lane, p, d in song['charts'][diff]]
    step = 60.0 / (song['bpm'] * 4)
    notes = []
    last = [-9.0] * NL
    fills = 0
    for bar, key in enumerate(song['form']):
        for s, ch in enumerate(song['phrases'][key]):
            t = (bar * 16 + s) * step
            lane = None
            if ch != '.' and not ((diff == 0 and s % 4) or (diff == 1 and s % 2)):
                d = int(ch)
                lane = min(NL - 1, d * NL // 10)
                if t - last[lane] >= 0.09:
                    notes.append(Note(t, lane, lead_midi(song, d)))
                    last[lane] = t
                else:
                    lane = None
            if diff == 2 and lane is not None and s in (0, 8):
                chord = [3]
            elif diff == 3 and lane is not None and s % 2 == 0:
                chord = [3] + ([2, -3] if (s in (0, 8) and bar % 4 == 0) else [])
            else:
                chord = []
            for off in chord:
                l2 = lane + off
                if not 0 <= l2 < NL:
                    l2 = lane - off if 0 <= lane - off < NL else None
                if l2 is not None and l2 != lane and t - last[l2] >= 0.09:
                    notes.append(Note(t, l2, lead_midi(song, min(9, round(l2 * 1.8)))))
                    last[l2] = t
            if diff == 3 and lane is None:         # Expert: fill every free eighth, and every free 16th in the odd bars
                if s % 2 == 0 or bar % 2 == 1:
                    fl = ZIGZAG[fills % len(ZIGZAG)]
                    fills += 1
                    if t - last[fl] >= 0.09:
                        notes.append(Note(t, fl, lead_midi(song, min(9, round(fl * 1.8)))))
                        last[fl] = t
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


SF_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'soundfont')
FS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fluidsynth')
DEFAULT_MIDI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'defaultmidi')


def _find_fluidsynth():
    p = shutil.which('fluidsynth')
    if p:
        return p
    for sub in (('bin', 'fluidsynth.exe'), ('fluidsynth.exe',), ('bin', 'fluidsynth'), ('fluidsynth',)):
        q = os.path.join(FS_DIR, *sub)
        if os.path.isfile(q):
            return q
    return None


def _find_sf2():
    try:
        for f in sorted(os.listdir(SF_DIR)):
            if f.lower().endswith('.sf2'):
                return os.path.join(SF_DIR, f)
    except Exception:
        pass
    return None


def sf2_available():
    return bool(_find_fluidsynth() and _find_sf2())


def render_midi_sf2(song, path):
    """Render the original MIDI with real instruments (FluidSynth + a .sf2 SoundFont).
    The result is padded so that chart time 0 sits LEAD seconds into the wav."""
    exe, sf = _find_fluidsynth(), _find_sf2()
    if not exe or not sf or not song.get('path'):
        raise RuntimeError("fluidsynth or SoundFont missing")
    tmp = path + ".raw.wav"
    flags = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    subprocess.run([exe, '-ni', '-F', tmp, '-r', '44100', '-g', '0.7', sf, song['path']],
                   check=True, timeout=180, creationflags=flags,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    with wave.open(tmp, 'rb') as r:
        ch, sw, sr = r.getnchannels(), r.getsampwidth(), r.getframerate()
        raw = r.readframes(r.getnframes())
    fb = ch * sw
    pad = LEAD - song.get('shift', 0.0)
    if pad >= 0:
        raw = bytes(int(pad * sr) * fb) + raw
    else:
        raw = raw[int(-pad * sr) * fb:]
    with wave.open(path, 'wb') as w:
        w.setnchannels(ch)
        w.setsampwidth(sw)
        w.setframerate(sr)
        w.writeframes(raw)
    try:
        os.remove(tmp)
    except OSError:
        pass


def wav_path(i):
    key = SONGS[i].get('wav_key', str(i))
    sf = "_sf" if SONGS[i].get('midi') and sf2_available() else ""
    return os.path.join(tempfile.gettempdir(), "minigames_rhythm_%s%s_v%d.wav" % (key, sf, WAV_VERSION))


def ensure_wav(i):
    """Render the backing track once per session (blocking; call from a worker thread)."""
    with _WAV_LOCK:
        p = _WAV.get(i)
        if p and os.path.exists(p):
            return p
        p = wav_path(i)
        if SONGS[i].get('midi'):
            done = False
            if sf2_available():
                try:
                    render_midi_sf2(SONGS[i], p)
                    done = True
                except Exception as e:
                    print("[Minigames/Rhythm] SoundFont render failed, using the built-in synth: %r" % (e,))
            if not done:
                render_midi_backing(SONGS[i], p)
        else:
            render_backing(SONGS[i], p)
        _WAV[i] = p
        return p


# ----------------------------------------------------------------------------
# MIDI import: put .mid / .midi files in the "midi" folder next to this script.
# The file is parsed (pure Python), the lead melody is detected and the notes are
# turned into a chart for every difficulty. The backing track is synthesised from
# the whole file (needs numpy for speed - Blender ships it).
# ----------------------------------------------------------------------------

MIDI_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'midi')
MIDI_MAX_SECONDS = 240.0                 # longer files are trimmed
MIDI_CACHE = {}                          # (path, mtime) -> index in SONGS
DRUM_KICK, DRUM_SNARE = (35, 36), (38, 40, 37, 39)
DRUM_HAT = (42, 44, 46, 51, 53, 54, 56, 69, 70, 80, 81)
# difficulty -> (notes per second, minimum gap in seconds, chord chance, use drums, use support voices)
MIDI_LEVELS = ((1.5, 0.38, 0.0, False, False), (2.9, 0.22, 0.0, False, False),
               (5.0, 0.125, 0.18, False, True), (10.0, 0.082, 0.55, True, True))


class MidiError(Exception):
    pass


def _vlq(data, pos):
    v = 0
    for _ in range(4):
        b = data[pos]
        pos += 1
        v = (v << 7) | (b & 0x7F)
        if not b & 0x80:
            break
    return v, pos


def parse_midi(path):
    """Minimal Standard MIDI File reader -> dict(tpq, tempos, notes, programs, end)."""
    with open(path, 'rb') as f:
        data = f.read()
    if data[:4] != b'MThd':
        raise MidiError("not a MIDI file")
    hlen = struct.unpack('>I', data[4:8])[0]
    fmt, ntrk, div = struct.unpack('>HHH', data[8:14])
    tpq = div if not div & 0x8000 and div > 0 else 480
    pos = 8 + hlen
    tempos, notes, programs, end = [], [], {}, 0
    names, vols = {}, {}
    tr = 0
    while pos + 8 <= len(data):
        cid, ln = data[pos:pos + 4], struct.unpack('>I', data[pos + 4:pos + 8])[0]
        body_start, body_end = pos + 8, min(len(data), pos + 8 + ln)
        pos = body_end
        if cid != b'MTrk':
            continue
        p, tick, status = body_start, 0, 0
        active = {}                                       # (ch, pitch) -> (on_tick, vel)
        try:
            while p < body_end:
                dt, p = _vlq(data, p)
                tick += dt
                b = data[p]
                if b & 0x80:
                    status = b
                    p += 1
                if status == 0xFF:
                    mtype = data[p]
                    mlen, p = _vlq(data, p + 1)
                    if mtype == 0x51 and mlen == 3:
                        tempos.append((tick, (data[p] << 16) | (data[p + 1] << 8) | data[p + 2]))
                    elif mtype in (0x03, 0x04) and mlen:
                        names.setdefault(tr, []).append(bytes(data[p:p + mlen]).decode('latin-1', 'ignore').strip())
                    p += mlen
                    if mtype == 0x2F:
                        break
                elif status in (0xF0, 0xF7):
                    mlen, p = _vlq(data, p)
                    p += mlen
                else:
                    hi, ch = status & 0xF0, status & 0x0F
                    if hi in (0xC0, 0xD0):
                        if hi == 0xC0:
                            programs[(tr, ch)] = data[p]
                        p += 1
                    else:
                        a, b2 = data[p], data[p + 1]
                        p += 2
                        if hi == 0x90 and b2 > 0:
                            if (ch, a) in active:                      # retrigger: close the old one
                                t0, v0 = active.pop((ch, a))
                                notes.append((t0, tick, a, v0, ch, tr))
                            active[(ch, a)] = (tick, b2)
                        elif hi == 0x80 or (hi == 0x90 and b2 == 0):
                            if (ch, a) in active:
                                t0, v0 = active.pop((ch, a))
                                notes.append((t0, max(tick, t0 + 1), a, v0, ch, tr))
                        elif hi == 0xB0 and a in (7, 11):                  # channel volume / expression
                            vols.setdefault((tr, ch, a), []).append(b2)
        except IndexError:
            pass                                                   # truncated track: keep what we have
        for (ch, a), (t0, v0) in active.items():
            notes.append((t0, tick, a, v0, ch, tr))
        end = max(end, tick)
        tr += 1
    if not notes:
        raise MidiError("no notes found")
    tempos = sorted(set(tempos)) or [(0, 500000)]
    if tempos[0][0] != 0:
        tempos.insert(0, (0, tempos[0][1]))
    return dict(tpq=tpq, tempos=tempos, notes=notes, programs=programs, end=end, names=names, vols=vols)


def _tick_seconds(tempos, tpq):
    """Returns a function tick -> seconds that honours tempo changes."""
    segs, acc = [], 0.0
    for i, (tk, us) in enumerate(tempos):
        segs.append((tk, acc, us))
        if i + 1 < len(tempos):
            acc += (tempos[i + 1][0] - tk) * us / 1e6 / tpq

    def f(tick):
        lo = 0
        for k in range(len(segs) - 1, -1, -1):
            if segs[k][0] <= tick:
                lo = k
                break
        tk, base, us = segs[lo]
        return base + (tick - tk) * us / 1e6 / tpq
    return f


def _beat_strength(tick, tpq):
    r = tick % (tpq * 4)
    tol = tpq / 16.0
    if r < tol or r > tpq * 4 - tol:
        return 4.0
    for div, val in ((tpq, 3.0), (tpq / 2.0, 2.0), (tpq / 4.0, 1.0)):
        m = tick % div
        if m < tol or m > div - tol:
            return val
    return 0.4


def _program_group(prog):
    if prog is None:
        return 'tri_pluck'
    if 32 <= prog <= 39:
        return 'bass'
    if prog < 16 or 104 <= prog <= 111:
        return 'tri_pluck'
    if prog < 24 or 80 <= prog <= 87:
        return 'sq_sus'
    if prog < 32 or 56 <= prog <= 63:
        return 'saw_pluck'
    return 'tri_sus'


GM_FAMILY = ("Piano", "Chrom. perc", "Organ", "Guitar", "Bass", "Strings", "Ensemble", "Brass",
             "Reed", "Pipe", "Synth lead", "Synth pad", "Synth fx", "Ethnic", "Percussive", "Sound fx")
LEAD_NAME_POS = ('melod', 'lead', 'vocal', 'voice', 'voce', 'sing', 'canto', 'solo', 'main', 'theme',
                 'tema', 'lyric', 'principal')
LEAD_NAME_NEG = ('bass', 'basso', 'drum', 'batt', 'perc', 'pad', 'string', 'chord', 'accomp', 'rhythm',
                 'ritmo', 'harmon', 'backing', 'comp', 'arp', 'strum', 'choir', 'coro')
LEAD_WIN = 4.0                        # seconds: the lead may change instrument from window to window


def _pitch_fit(p):
    if 60 <= p <= 88:
        return 1.0
    if p > 88:
        return max(0.3, 1.0 - (p - 88) / 15.0)
    return max(0.05, (p - 40) / 20.0)


def _program_bonus(prog):
    if prog is None:
        return 0.0
    if 32 <= prog <= 39:
        return -3.0                    # bass
    if 48 <= prog <= 55 or 88 <= prog <= 103 or prog >= 112:
        return -1.5                    # string ensembles, pads, fx, percussive, sfx
    if 42 <= prog <= 47:
        return -0.8
    if 80 <= prog <= 87 or 64 <= prog <= 79:
        return 1.5                     # synth leads, reeds, pipes
    if 56 <= prog <= 63 or 40 <= prog <= 41 or 27 <= prog <= 30:
        return 1.0                     # brass, violin/viola, electric guitars
    if 24 <= prog <= 31:
        return 0.6
    if 104 <= prog <= 111:
        return 0.7
    if prog <= 15:
        return 0.3
    return 0.0


def _median(v, default):
    if not v:
        return default
    v = sorted(v)
    return v[len(v) // 2]


def score_voices(groups, m, mel):
    """Score every (track, channel) voice by how much it behaves like THE melody.
    Uses: track / instrument name, GM program, monophony, register of its top line, how much of the song
    it covers, its (log) note count, velocity x channel volume x expression relative to the other voices,
    pitch variety and how repetitive it is (ostinatos / arpeggios score low).
    Returns (scores, labels)."""
    wins_all = set(int(n['t'] // LEAD_WIN) for n in mel)
    ngroups = {}
    for (tr, ch) in groups:
        ngroups[tr] = ngroups.get(tr, 0) + 1
    max_n = max(len(ns) for ns in groups.values())
    loud, parts = {}, {}
    for key, ns in groups.items():
        tr, ch = key
        ns.sort(key=lambda n: n['t'])
        tops, c0 = [], -9.0
        for n in ns:
            if n['t'] - c0 < 0.035:
                if n['pitch'] > tops[-1]:
                    tops[-1] = n['pitch']
            else:
                tops.append(n['pitch'])
                c0 = n['t']
        poly = len(ns) / float(len(tops))
        grams = [tuple(tops[i:i + 4]) for i in range(len(tops) - 3)]
        r4 = len(set(grams)) / float(len(grams)) if grams else 0.0
        vel = sum(n['vel'] for n in ns) / float(len(ns)) / 127.0
        v7 = _median(m.get('vols', {}).get((tr, ch, 7)), 100) / 127.0
        v11 = _median(m.get('vols', {}).get((tr, ch, 11)), 127) / 127.0
        loud[key] = vel * min(1.0, v7) * min(1.0, v11)
        parts[key] = (1.0 / poly, _pitch_fit(sum(tops) / float(len(tops))),
                      len(set(int(n['t'] // LEAD_WIN) for n in ns)) / float(max(1, len(wins_all))),
                      math.log(1 + len(ns)) / math.log(1 + max_n),
                      min(1.0, len(set(tops)) / 10.0), r4)
    max_loud = max(max(loud.values()), 1e-6)
    scores, labels = {}, {}
    for key, ns in groups.items():
        tr, ch = key
        mono, pf, cover, cnt_f, var, r4 = parts[key]
        prog = m['programs'].get(key)
        nm = ' '.join(m.get('names', {}).get(tr, [])).lower() if ngroups[tr] == 1 else ''
        name_b = (3.0 if any(k in nm for k in LEAD_NAME_POS) else 0.0) - \
                 (3.0 if any(k in nm for k in LEAD_NAME_NEG) else 0.0)
        sc = (2.5 * mono + 2.0 * pf + 1.5 * cover + 1.0 * cnt_f + 2.0 * (loud[key] / max_loud) +
              1.5 * var + 1.0 * r4 + _program_bonus(prog) + name_b)
        if len(ns) < 8:
            sc -= 3.0
        scores[key] = sc
        if nm:
            labels[key] = m['names'][tr][0][:14]
        elif prog is not None:
            labels[key] = GM_FAMILY[min(15, prog // 8)]
        else:
            labels[key] = "Ch%d" % (ch + 1)
    return scores, labels


def analyze_midi(path):
    """Parse a MIDI file and build a playable song dict (charts for all difficulties)."""
    m = parse_midi(path)
    tpq = m['tpq']
    to_sec = _tick_seconds(m['tempos'], tpq)
    # snap onsets to a 16th grid when they are close to it (hand-played files get a steadier chart)
    grid = tpq / 4.0
    snap = tpq / 12.0

    def snapped(tk):
        g = round(tk / grid) * grid
        return int(g) if abs(g - tk) <= snap else tk
    mel, drums = [], []
    for on, off, pitch, vel, ch, tr in m['notes']:
        if vel < 12:
            continue
        ton = snapped(on)
        s0 = to_sec(ton)
        if s0 > MIDI_MAX_SECONDS + 30:
            continue
        s1 = max(s0 + 0.05, to_sec(off))
        item = dict(tick=ton, t=s0, off=s1, pitch=pitch, vel=vel, ch=ch, tr=tr)
        (drums if ch == 9 else mel).append(item)
    if not mel:
        raise MidiError("only drums in this file")
    # ---- pick the lead voice(s): score every (track, channel) voice, then follow the best one
    #      window by window (if it is silent somewhere, a close runner-up takes over there)
    groups = {}
    for n in mel:
        groups.setdefault((n['tr'], n['ch']), []).append(n)
    scores, labels = score_voices(groups, m, mel)
    ranked = sorted(groups, key=lambda k: -scores[k])
    best_g = ranked[0]
    top = ranked[:4]
    by_win = {k: {} for k in top}
    for k in top:
        for n in groups[k]:
            by_win[k].setdefault(int(n['t'] // LEAD_WIN), []).append(n)
    all_w = sorted(set(w for k in top for w in by_win[k]))
    lead_all = []
    for w in all_w:
        pick = best_g
        if len(by_win[best_g].get(w, ())) < 2:
            for k in top[1:]:
                if scores[k] >= scores[best_g] - 2.0 and len(by_win[k].get(w, ())) >= 3:
                    pick = k
                    break
        lead_all += by_win[pick].get(w, [])
    lead_all.sort(key=lambda n: n['t'])
    lead_ids_all = set(id(n) for n in lead_all)
    others = [n for n in mel if id(n) not in lead_ids_all]
    lead_label = labels[best_g]
    print("[Minigames/Rhythm] %s: lead voice = %s (track %d, ch %d); ranking: %s" % (
        os.path.basename(path), lead_label, best_g[0], best_g[1],
        ", ".join("%s %.1f" % (labels[k], scores[k]) for k in ranked[:4])))
    # skyline: simultaneous lead onsets -> keep the highest, the rest become support
    lead, support = [], list(others)
    for n in lead_all:
        if lead and n['t'] - lead[-1]['t'] < 0.035:
            if n['pitch'] > lead[-1]['pitch']:
                support.append(lead[-1])
                lead[-1] = n
            else:
                support.append(n)
        else:
            lead.append(n)
    support.sort(key=lambda n: n['t'])
    lead = [n for n in lead if n['t'] <= MIDI_MAX_SECONDS + 30]
    first = lead[0]['t']
    shift = max(0.0, first - 1.5)
    # ---- pitch quantiles -> lanes
    pitches = sorted(set(n['pitch'] for n in lead))      # distinct pitches: every pitch gets its own share of lanes
    cuts = [pitches[min(len(pitches) - 1, int(len(pitches) * k / NL))] for k in range(1, NL)]

    def lane_of(p):
        return sum(1 for c_ in cuts if p >= c_) if cuts else 0
    # ---- candidates with a "musical importance" score
    cands = []
    for n in lead:
        dur = min(1.0, n['off'] - n['t'])
        cands.append(dict(t=n['t'] - shift, tick=n['tick'], kind='lead', pitch=n['pitch'], len=n['off'] - n['t'],
                          score=_beat_strength(n['tick'], tpq) + dur * 1.6 + n['vel'] / 127.0 + 1.5))
    lead_times = [c_['t'] for c_ in cands]
    def near_lead(t, win):
        i = bisect.bisect_left(lead_times, t - win)
        return i < len(lead_times) and lead_times[i] <= t + win
    for n in support:
        t = n['t'] - shift
        if t < -0.1 or near_lead(t, 0.05):
            continue
        cands.append(dict(t=t, tick=n['tick'], kind='sup', pitch=n['pitch'], len=n['off'] - n['t'],
                          score=_beat_strength(n['tick'], tpq) + min(1.0, n['off'] - n['t']) + n['vel'] / 127.0 - 0.5))
    dr = []
    for d in drums:
        pit = d['pitch']
        kind = 'kick' if pit in DRUM_KICK else 'snare' if pit in DRUM_SNARE else 'hat' if pit in DRUM_HAT else 'tom'
        dr.append(dict(t=d['t'] - shift, tick=d['tick'], kind=kind, pitch=pit, vel=d['vel']))
        if kind in ('kick', 'snare', 'tom', 'hat') and not near_lead(d['t'] - shift, 0.05):
            cands.append(dict(t=d['t'] - shift, tick=d['tick'], kind=kind, pitch=pit,
                              score=_beat_strength(d['tick'], tpq) + {'kick': 2.0, 'snare': 2.0, 'tom': 0.5, 'hat': -0.6}[kind] - 0.8))
    cands = [c_ for c_ in cands if 0.0 <= c_['t'] <= MIDI_MAX_SECONDS]
    duration = min(MIDI_MAX_SECONDS, max([c_['t'] for c_ in cands] + [1.0]) + 1.0)
    # ---- one chart per difficulty
    charts = []
    for diff, (dens, gap, chord_p, use_dr, use_sup) in enumerate(MIDI_LEVELS):
        pool = [c_ for c_ in cands if c_['kind'] == 'lead' or (use_sup and c_['kind'] == 'sup') or
                (use_dr and c_['kind'] in ('kick', 'snare', 'tom', 'hat'))]
        if diff <= 1:                                              # easy levels: strong beats of the melody only
            pool = [c_ for c_ in pool if c_['kind'] == 'lead']
        rng = random.Random(zlib.crc32(os.path.basename(path).encode()) + diff)
        win = 2.0
        chosen = []
        t0 = 0.0
        pool.sort(key=lambda c_: c_['t'])
        idx = 0
        while t0 < duration and idx < len(pool):
            seg = []
            while idx < len(pool) and pool[idx]['t'] < t0 + win:
                seg.append(pool[idx])
                idx += 1
            quota = max(1, int(round(dens * win)))
            seg.sort(key=lambda c_: -c_['score'])
            take = []
            for c_ in seg:
                if len(take) >= quota:
                    break
                if all(abs(c_['t'] - o['t']) >= gap for o in take) and \
                        all(abs(c_['t'] - o['t']) >= gap for o in chosen[-4:]):
                    take.append(c_)
            chosen += sorted(take, key=lambda c_: c_['t'])
            t0 += win
        chosen.sort(key=lambda c_: c_['t'])
        events, last = [], [-9.0] * NL
        kd = [0, 0, 0]
        prev = None                                              # (pitch, lane, time) of the last lead note

        def put(t, lane, pitch, ln=0.0):
            lane = max(0, min(NL - 1, lane))
            if t - last[lane] < 0.085:
                for alt in (lane - 1, lane + 1, lane - 2, lane + 2):
                    if 0 <= alt < NL and t - last[alt] >= 0.085:
                        lane = alt
                        break
                else:
                    return None
            last[lane] = t
            p_ = pitch
            while p_ < 48:
                p_ += 12
            while p_ > 84:
                p_ -= 12
            events.append((t, lane, p_, ln if ln >= HOLD_MIN else 0.0))
            return lane
        for c_ in chosen:
            if c_['kind'] in ('lead', 'sup'):
                lane = lane_of(c_['pitch'])
                if c_['kind'] == 'lead' and prev is not None and c_['t'] - prev[2] < 1.0:
                    pp, pl = prev[0], prev[1]
                    pit = c_['pitch']
                    if pit == pp:                                  # same pitch -> same lane
                        lane = pl
                    elif pit > pp and lane <= pl:                  # higher pitch must not sit on the same / a lower lane
                        lane = pl + 1 if pl < NL - 1 else pl - 1
                    elif pit < pp and lane >= pl:                  # lower pitch must not sit on the same / a higher lane
                        lane = pl - 1 if pl > 0 else pl + 1
            elif c_['kind'] == 'kick':
                lane = (0, NL - 1)[kd[0] % 2]
                kd[0] += 1
            elif c_['kind'] == 'snare':
                lane = (2, 3)[kd[1] % 2]
                kd[1] += 1
            else:
                lane = (1, 4)[kd[2] % 2]
                kd[2] += 1
            got = put(c_['t'], lane, c_['pitch'], c_.get('len', 0.0) if c_['kind'] in ('lead', 'sup') else 0.0)
            if c_['kind'] == 'lead' and got is not None:
                prev = (c_['pitch'], got, c_['t'])
            if got is None or chord_p <= 0 or c_['kind'] != 'lead' or c_['score'] < 6.0:
                continue
            if rng.random() < chord_p:                                  # a second note from another voice / interval
                mates = [s for s in cands if s['kind'] == 'sup' and abs(s['t'] - c_['t']) < 0.06]
                lane2 = lane_of(mates[0]['pitch']) if mates else (got + 3) % NL
                if lane2 == got or abs(lane2 - got) < 2:
                    lane2 = got + 3 if got + 3 < NL else got - 3
                put(c_['t'], lane2, mates[0]['pitch'] if mates else c_['pitch'] - 7)
                if diff == 3 and c_['score'] >= 8.0 and rng.random() < 0.5:     # triple chord on the big downbeats
                    for lane3 in (got - 2, got + 2, 0, NL - 1):
                        if 0 <= lane3 < NL and lane3 not in (got, lane2) and abs(lane3 - lane2) >= 1:
                            put(c_['t'], lane3, c_['pitch'] - 12)
                            break
        events.sort()
        events = _balance_holds(_trim_holds(events), diff)
        if diff == 2:                                              # Hard: long notes rarely overlap short ones
            events = _thin_overlaps(events, rng)
        charts.append(events)
    # tempo for the beat lines
    us = m['tempos'][0][1]
    bpm = int(round(60e6 / us)) if us else 120
    base = os.path.splitext(os.path.basename(path))[0]
    slug = ''.join(ch_ if ch_.isalnum() else '_' for ch_ in base.lower())[:36].strip('_') or 'song'
    title = base.replace('_', ' ')[:30]
    notes_for_audio = [dict(t=n['t'] - shift, off=n['off'] - shift, pitch=n['pitch'], vel=n['vel'],
                            group=_program_group(m['programs'].get((n['tr'], n['ch']))), lead=False) for n in mel
                       if 0.0 <= n['t'] - shift <= duration + 1]
    lead_ids = set((round(n['t'], 4), n['pitch']) for n in lead)
    for na, n in zip(notes_for_audio, [n for n in mel if 0.0 <= n['t'] - shift <= duration + 1]):
        na['lead'] = (round(n['t'], 4), n['pitch']) in lead_ids
    return dict(title=title, bpm=bpm, note="MIDI - %d notes, %s - lead: %s" % (len(m['notes']), _fmt_len(duration), lead_label),
                midi=True, charts=charts, duration=duration, rec_name="midi_" + slug,
                wav_key="midi_%s_%d" % (slug, int(os.path.getmtime(path)) % 100000),
                audio_notes=notes_for_audio, audio_drums=[d for d in dr if 0.0 <= d['t'] <= duration + 1],
                path=path, shift=shift, lead_label=lead_label,
                form="", phrases={})


def _trim_holds(events):
    """Long notes must end before the next note on the same lane (+ a small gap)."""
    out = []
    for i, (t, l, p, d) in enumerate(events):
        if d > 0:
            for e in events[i + 1:]:
                if e[1] == l:
                    d = min(d, e[0] - 0.15 - t)
                    break
            d = min(d, 4.0)
            if d < HOLD_MIN:
                d = 0.0
        out.append((t, l, p, d))
    return out


def _balance_holds(events, diff):
    """Decide which candidate long notes really stay long: they must reach the per-difficulty minimum
    length, the longest (= most clearly sustained) are kept first, no 8 s window may contain more than
    HOLD_RATIO of long notes and only HOLD_MAX_CONCURRENT of them may be held at the same time."""
    ev = [list(e) for e in events]
    for e in ev:
        if 0 < e[3] < HOLD_MIN_BY_DIFF[diff]:
            e[3] = 0.0
    total, count = {}, {}
    for e in ev:
        w = int(e[0] // 8.0)
        total[w] = total.get(w, 0) + 1
    cand = sorted((i for i, e in enumerate(ev) if e[3] > 0), key=lambda i: -ev[i][3])
    keep = []
    for i in cand:
        t0, d = ev[i][0], ev[i][3]
        w = int(t0 // 8.0)
        if count.get(w, 0) + 1 > max(1, int(round(HOLD_RATIO[diff] * total[w]))):
            continue
        conc = sum(1 for j in keep if ev[j][0] < t0 + d and t0 < ev[j][0] + ev[j][3])
        if conc >= HOLD_MAX_CONCURRENT[diff]:
            continue
        keep.append(i)
        count[w] = count.get(w, 0) + 1
    keep = set(keep)
    for i in cand:
        if i not in keep:
            ev[i][3] = 0.0
    return [tuple(e) for e in ev]


def _thin_overlaps(events, rng):
    """Drop most short notes that fall inside a long note (other lanes, or chord mates at its start)."""
    spans = [(t, t + d) for t, l, p, d in events if d > 0]
    if not spans:
        return events
    out = []
    for t, l, p, d in events:
        if d == 0 and any(s0 - 0.02 <= t <= s1 for s0, s1 in spans) and rng.random() >= HARD_OVERLAP_KEEP:
            continue
        out.append((t, l, p, d))
    return out


def _fmt_len(sec):
    return "%d:%02d" % divmod(int(sec), 60)


def list_midi_files():
    try:
        os.makedirs(MIDI_DIR, exist_ok=True)
        names = sorted(f for f in os.listdir(MIDI_DIR) if f.lower().endswith(('.mid', '.midi')))
    except Exception:
        return []
    return [(os.path.splitext(f)[0], os.path.join(MIDI_DIR, f)) for f in names]


def load_default_midi_songs():
    """Load the .mid / .midi files from the 'defaultmidi' folder as the built-in songs.
    The title comes from the file name. They are always available and cannot be removed."""
    global BUILTIN, DEFAULT_MIDI
    try:
        os.makedirs(DEFAULT_MIDI_DIR, exist_ok=True)
        names = sorted(f for f in os.listdir(DEFAULT_MIDI_DIR)
                       if f.lower().endswith(('.mid', '.midi')))
    except Exception:
        names = []
    DEFAULT_MIDI = []
    for f in names:
        path = os.path.join(DEFAULT_MIDI_DIR, f)
        try:
            SONGS.append(analyze_midi(path))
            DEFAULT_MIDI.append(len(SONGS) - 1)
        except Exception as e:
            print("[Minigames/Rhythm] could not load default MIDI %s: %r" % (f, e))
    BUILTIN = len(DEFAULT_MIDI)
    return BUILTIN


def render_midi_backing(song, path):
    """Synthesise the whole MIDI (all voices + drums) to a wav. Needs numpy for decent speed."""
    dur = song['duration']
    total = int((LEAD + dur + 2.0) * SR)
    if np is None:
        notes = [n for n in song['audio_notes'] if n['group'] == 'bass' or n['lead']][:1500]
        buf = [0.0] * total
        for n in notes:
            w = _tone(midi_freq(n['pitch']), min(0.6, n['off'] - n['t']), 'tri', 0.2 * n['vel'] / 127.0)
            o = int((LEAD + n['t']) * SR)
            for i in range(min(len(w), total - o)):
                buf[o + i] += w[i]
        peak = max(max(buf), -min(buf), 1e-6)
        pcm = array('h', (int(max(-1.0, min(1.0, v * 0.85 / peak)) * 32767) for v in buf))
    else:
        buf = np.zeros(total, dtype=np.float32)
        cache = {}

        def wave_for(group, pitch, length, vol):
            key = (group, pitch, int(length * 50))
            w = cache.get(key)
            if w is None:
                n_ = max(8, int(length * SR))
                t = np.arange(n_, dtype=np.float32) / SR
                ph = (midi_freq(pitch) * t) % 1.0
                if group.startswith('tri'):
                    base = 4.0 * np.abs(ph - 0.5) - 1.0
                elif group.startswith('saw'):
                    base = 2.0 * ph - 1.0
                else:
                    base = np.where(ph < 0.5, 0.55, -0.55).astype(np.float32)
                atk = np.minimum(1.0, t / 0.006)
                rel = np.minimum(1.0, (length - t) / 0.04)
                if group.endswith('pluck'):
                    env = atk * rel * (0.35 + 0.65 * np.exp(-5.0 * t))
                else:
                    env = atk * rel
                w = (base * env).astype(np.float32)
                cache[key] = w
            return w * vol

        def add(w, t0):
            o = int((LEAD + t0) * SR)
            if o < 0 or o >= total:
                return
            k = min(len(w), total - o)
            buf[o:o + k] += w[:k]
        for n in song['audio_notes']:
            length = max(0.06, min(1.6, n['off'] - n['t']))
            g = n['group']
            vol = (0.30 if n['lead'] else 0.17 if g != 'bass' else 0.26) * n['vel'] / 127.0
            pitch = n['pitch'] if g != 'bass' else n['pitch']
            add(wave_for(g, pitch, length, vol), n['t'])
        k_, s_, h_ = (np.array(_kick(), dtype=np.float32), np.array(_snare(), dtype=np.float32),
                      np.array(_hat(), dtype=np.float32))
        for d in song['audio_drums']:
            v = d['vel'] / 127.0
            if d['kind'] == 'kick':
                add(k_ * 0.8 * v, d['t'])
            elif d['kind'] == 'snare':
                add(s_ * 0.7 * v, d['t'])
            elif d['kind'] == 'hat':
                add(h_ * 0.5 * v, d['t'])
            else:
                add(s_ * 0.45 * v, d['t'])
        peak = max(float(np.max(np.abs(buf))), 1e-6)
        pcm = array('h')
        pcm.frombytes((np.clip(buf * (0.85 / peak), -1.0, 1.0) * 32767).astype('<i2').tobytes())
    with wave.open(path, 'wb') as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(SR)
        wf.writeframes(pcm.tobytes())
    return total / SR


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

W, H = 420.0, 490.0                 # logical field = 6 x 7 cells of 70 (matches layout())
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
                                'ONE', 'TWO', 'THREE', 'FOUR', 'FIVE', 'SIX', 'M', 'BACK_SPACE'))

    def setup(self):
        load_default_midi_songs()
        self.song_i = 0
        self.diff = 1
        self.token = 0
        self.closed = False
        self.record_mgrs = {}  # (song, diff) -> RecordManager, created on demand
        self.midi_files = []
        self.midi_sel = 0
        self.midi_msg = ""
        self.midi_count = len(list_midi_files())

    # ---- MIDI library
    def _open_midi(self):
        self.midi_files = list_midi_files()
        self.midi_count = len(self.midi_files)
        self.midi_msg = ""
        self.midi_sel = 0
        if self.song_i >= BUILTIN:
            for k, (name, path) in enumerate(self.midi_files):
                if SONGS[self.song_i].get('title') == name.replace('_', ' ')[:30]:
                    self.midi_sel = k
        self.state = 'midi'
        self.menu_hover = None

    def _load_midi(self, k):
        if not 0 <= k < len(self.midi_files):
            return
        name, path = self.midi_files[k]
        try:
            key = (path, int(os.path.getmtime(path)))
            if key in MIDI_CACHE:
                self.song_i = MIDI_CACHE[key]
            else:
                song = analyze_midi(path)
                SONGS.append(song)
                MIDI_CACHE[key] = len(SONGS) - 1
                self.song_i = len(SONGS) - 1
            self.state = 'menu'
            self.menu_hover = None
        except Exception as e:
            self.midi_msg = "Can't use this file: %s" % (e,)
            print("[Minigames/Rhythm] MIDI import failed for %s: %r" % (path, e))

    def _midi_rect(self, k):
        return 30.0, 408.0 - k * 46.0, 360.0, 40.0

    def _midi_visible(self):
        first = max(0, min(self.midi_sel - 7, max(0, len(self.midi_files) - 8)))
        return first, list(range(first, min(len(self.midi_files), first + 8)))

    def _mgr(self, song_i, diff):
        """One record file per song and difficulty: rhythm_<song>_<diff>.json"""
        k = (song_i, diff)
        if k not in self.record_mgrs:
            try:
                name = "rhythm_%s_%s" % (
                    SONGS[song_i].get('rec_name') or SONGS[song_i]['title'].lower().replace(' ', '_'), DIFFS[diff].lower())
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
        self.midi_count = len(list_midi_files())
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
        self.holding = {}
        self.hold_seen = [0.0] * NL
        self.hold_acc = 0.0
        self.last_down = -1

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
        self.holding = {}                 # lane -> Note currently held down
        self.hold_seen = [0.0] * NL       # last wall time we saw the key down (press / repeat)
        self.hold_acc = 0.0
        self.last_down = -1               # lane of the most recently pressed key (only that one auto-repeats)
        self.stars = 0
        self.new_best = False
        self.tvis = APPROACH[self.diff]
        self.win = WINDOWS[self.diff]
        self.meter_cfg = METER_CFG[self.diff]
        self.end_t = max((n.t + n.dur for n in self.notes), default=0.0) + 1.3
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
        while p < len(notes) and notes[p].t + self.win[2] < t:
            notes[p].state = 2
            self._miss(notes[p])
            p += 1
        self.ptr[lane] = p

    def _miss(self, note):
        self.counts['MISS'] += 1
        self.combo = 0
        self.meter = max(0.0, self.meter - self.meter_cfg[0])
        self.popups.append(["MISS", ov.C_BAD, time.perf_counter(), note.lane])
        AUDIO.buzz()

    def _press(self, lane):
        t = self.now()
        self.flash[lane] = time.perf_counter()
        self._resolve_misses(lane, t)
        notes, p = self.lane_notes[lane], self.ptr[lane]
        if p < len(notes) and abs(notes[p].t - t) <= self.win[2]:
            n = notes[p]
            d = abs(n.t - t)
            j = 'PERFECT' if d <= self.win[0] else 'GREAT' if d <= self.win[1] else 'GOOD'
            n.state = 1
            self.ptr[lane] += 1
            self.score += BASE_POINTS[j] * self._mult()
            self.counts[j] += 1
            self.combo += 1
            self.max_combo = max(self.max_combo, self.combo)
            self.meter = min(1.0, self.meter + METER_GAIN[j] * self.meter_cfg[2])
            col = ov.C_GOLD if j == 'PERFECT' else ov.C_GOOD if j == 'GREAT' else ov.C_TEXT
            self.popups.append([j, col, time.perf_counter(), lane])
            self.bursts.append([lane, time.perf_counter()])
            AUDIO.tone(n.midi)
            if n.dur > 0:                                             # long note: keep the key down
                n.state = 3
                self.holding[lane] = n
                self.hold_seen[lane] = time.perf_counter()
        else:                                                         # nothing to hit: a wasted press
            self.ghosts += 1
            self.meter = max(0.0, self.meter - self.meter_cfg[1])
        if self.meter <= 0.0:
            self._fail()

    def _hold_done(self, lane):
        n = self.holding.pop(lane, None)
        if n is None:
            return
        n.state = 1
        self.score += HOLD_POINTS * self._mult()
        self.popups.append(["HOLD", ov.C_GOLD, time.perf_counter(), lane])
        self.bursts.append([lane, time.perf_counter()])

    def _hold_drop(self, lane):
        n = self.holding.pop(lane, None)
        if n is None:
            return
        n.state = 2                       # released too early: the combo is kept, only a small meter loss
        self.counts['MISS'] += 1
        self.meter = max(0.0, self.meter - self.meter_cfg[0] * 0.5)
        self.popups.append(["DROP", ov.C_BAD, time.perf_counter(), lane])
        AUDIO.buzz()

    def _release(self, lane):
        n = self.holding.get(lane)
        if n is None:
            return
        self._hold_drop(lane)             # a real key release always ends the long note

    def key_up(self, key):
        """Called by the runner when a key is released (needed for long notes)."""
        KEYUP_SEEN[0] = True
        if key in LANE_KEYS and self.state == 'play':
            self._release(LANE_KEYS[key])

    def _fail(self):
        AUDIO.stop()
        self.holding = {}
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
        pc = time.perf_counter()
        for lane, n in list(self.holding.items()):
            if t >= n.t + n.dur:
                self._hold_done(lane)
            elif not KEYUP_SEEN[0] and lane == self.last_down and pc - self.hold_seen[lane] > 0.7:
                # no key-up events from the runner: only the LAST pressed key auto-repeats, so only
                # that one can be judged released; a held key is never dropped because of another press
                self._hold_drop(lane)
            else:
                self.flash[lane] = pc
                self.hold_acc += dt * 60.0 * self._mult()
                k = int(self.hold_acc)
                self.hold_acc -= k
                self.score += k
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
            if st == 'play':
                lane = LANE_KEYS[key]
                self.last_down = lane
                if repeat:
                    self.hold_seen[lane] = time.perf_counter()
                else:
                    self._press(lane)
            return
        if key == 'SPACE':
            if st in ('menu', 'results', 'failed', 'midi'):
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
                self.hold_seen = [time.perf_counter()] * NL
                self.state = 'play'
                AUDIO.resume()
        elif st == 'midi':
            if key == 'UP_ARROW':
                self.midi_sel = max(0, self.midi_sel - 1)
            elif key == 'DOWN_ARROW':
                self.midi_sel = min(max(0, len(self.midi_files) - 1), self.midi_sel + 1)
            elif key in ('RET', 'NUMPAD_ENTER'):
                self._load_midi(self.midi_sel)
            elif key in ('M', 'BACK_SPACE'):
                self.state = 'menu'
        elif st == 'menu':
            if key == 'UP_ARROW':
                self.song_i = (self.song_i - 1) % BUILTIN if self.song_i < BUILTIN else BUILTIN - 1
            elif key == 'DOWN_ARROW':
                self.song_i = (self.song_i + 1) % BUILTIN if self.song_i < BUILTIN else 0
            elif key in ('SIX', 'M'):
                self._open_midi()
            elif key == 'LEFT_ARROW':
                self.diff = (self.diff - 1) % len(DIFFS)
            elif key == 'RIGHT_ARROW':
                self.diff = (self.diff + 1) % len(DIFFS)
            elif key in ('ONE', 'TWO', 'THREE', 'FOUR', 'FIVE'):
                self.song_i = {'ONE': 0, 'TWO': 1, 'THREE': 2, 'FOUR': 3, 'FIVE': 4}[key]
                if self.song_i >= BUILTIN:
                    self.song_i = 0
            elif key in ('RET', 'NUMPAD_ENTER'):
                self._start_song()
        elif st in ('results', 'failed') and key in ('RET', 'NUMPAD_ENTER'):
            self._to_menu()

    # ---- menu geometry (logical coordinates)
    def _song_rect(self, i):
        return 30.0, 398.0 - i * 44.0, 360.0, 40.0

    def _diff_rect(self, i):
        return 30.0 + i * 92.0, 130.0, 84.0, 36.0

    def _start_rect(self):
        return 110.0, 76.0, 200.0, 40.0

    def _to_logical(self, x, y):
        return (x - self.fx0) / self.sc, (y - self.fy0) / self.sc

    @staticmethod
    def _inside(r, lx, ly):
        return r[0] <= lx <= r[0] + r[2] and r[1] <= ly <= r[1] + r[3]

    def _menu_target(self, x, y):
        lx, ly = self._to_logical(x, y)
        for i in range(BUILTIN + 1):
            if self._inside(self._song_rect(i), lx, ly):
                return ('song', i)
        for i in range(len(DIFFS)):
            if self._inside(self._diff_rect(i), lx, ly):
                return ('diff', i)
        if self._inside(self._start_rect(), lx, ly):
            return ('start', 0)
        return None

    def move(self, x, y):
        if self.state == 'midi':
            lx, ly = self._to_logical(x, y)
            h = None
            first, rows = self._midi_visible()
            for k in rows:
                if self._inside(self._midi_rect(k - first), lx, ly):
                    h = ('midi', k)
            if self._inside((140.0, 44.0, 140.0, 34.0), lx, ly):
                h = ('back', 0)
            changed = h != self.menu_hover
            self.menu_hover = h
            return changed
        if self.state != 'menu':
            return False
        h = self._menu_target(x, y)
        changed = h != self.menu_hover
        self.menu_hover = h
        return changed

    def click(self, x, y, button):
        if button != 'LEFT':
            return
        if self.state == 'midi':
            lx, ly = self._to_logical(x, y)
            first, rows = self._midi_visible()
            for k in rows:
                if self._inside(self._midi_rect(k - first), lx, ly):
                    self.midi_sel = k
                    self._load_midi(k)
                    return
            if self._inside((140.0, 44.0, 140.0, 34.0), lx, ly):
                self.state = 'menu'
            return
        if self.state == 'menu':
            t = self._menu_target(x, y)
            if t is None:
                return
            kind, i = t
            if kind == 'song':
                if i == BUILTIN:
                    self._open_midi()
                    return
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
        elif st == 'midi':
            self._draw_midi(c)
        elif st in ('play', 'paused'):
            self._draw_play(c)
        elif st == 'failed':
            self._draw_play(c)
            self.banner(c, self.fx0, self.fy0, W * self.sc, H * self.sc, "Song failed",
                        "Click or SPACE: menu   R: retry")
        elif st == 'results':
            self._draw_results(c)

    def _tail(self, c, lane, u0, u1, col, active):
        if u1 <= u0:
            return
        s0, s1 = _persp(u0), _persp(u1)
        y0, y1 = _y_of(u0), _y_of(u1)
        x0 = CX + (lane - (NL - 1) / 2.0) * LW * s0
        x1 = CX + (lane - (NL - 1) / 2.0) * LW * s1
        w0, w1 = 0.16 * LW * s0, 0.16 * LW * s1
        k = 1.0 if active else 0.55
        self._poly(c, [(x0 - w0, y0), (x0 + w0, y0), (x1 + w1, y1), (x1 - w1, y1)],
                   (col[0] * k, col[1] * k, col[2] * k, 1.0))

    def _draw_menu(self, c):
        self.draw_header(
            c, "Rhythm", "Pick a song, then press ENTER", ov.C_GOLD)
        self._text(c, "Hit A  S  D  J  K  L  on the beat",
                   W / 2, 468, 15, ov.C_WHITE)
        self._text(c, "Default MIDI songs - or play your own MIDI files",
                   W / 2, 450, 11, ov.C_TEXT)
        for i, s in enumerate(SONGS[:BUILTIN] + [None]):
            x, y, w, h = self._song_rect(i)
            if s is None:                                          # the MIDI library card
                hov = self.menu_hover == ('song', i)
                c.rect(self._X(x), self._Y(y), w * self.sc, h * self.sc, ov.C_CELL_HOVER if hov else ov.C_CELL)
                self._text(c, "MIDI Library", x + 14, y + 30, 17, ov.C_WHITE, 'left')
                self._text(c, "%d file(s) in the 'midi' folder - click to browse" % self.midi_count,
                           x + 14, y + 10, 10.5, ov.C_TEXT, 'left')
                continue
            sel = i == self.song_i
            hov = self.menu_hover == ('song', i)
            if sel:
                c.rect(self._X(x) - 2, self._Y(y) - 2, w *
                       self.sc + 4, h * self.sc + 4, ov.C_GOLD)
            c.rect(self._X(x), self._Y(y), w * self.sc, h * self.sc,
                   ov.C_CELL_HOVER if hov else ov.C_CELL)
            self._text(c, s['title'], x + 14, y + 30, 17, ov.C_WHITE, 'left')
            self._text(c, "%s" %
                       s['note'], x + 14, y + 10, 10.5, ov.C_TEXT, 'left')
            best = self._best(i, self.diff)
            self._text(c, "Best %d" % best if best else "No score yet", x + w - 14, y + 10, 11,
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
            c, "Up/Down song  Left/Right level  ENTER start  6 / M: MIDI library  ESC quit")

    def _draw_midi(self, c):
        self.draw_header(c, "MIDI Library", "Pick a file - the game finds the melody and rhythm for you", ov.C_GOLD)
        files = self.midi_files
        if not files:
            self._text(c, "No MIDI files found", W / 2, 360, 20, ov.C_WHITE)
            self._text(c, "Put .mid / .midi files in this folder:", W / 2, 320, 12, ov.C_TEXT)
            path = MIDI_DIR if len(MIDI_DIR) < 46 else "..." + MIDI_DIR[-43:]
            self._text(c, path, W / 2, 298, 10, ov.C_GOLD)
            self._text(c, "then come back to this screen (6 / M).", W / 2, 270, 12, ov.C_TEXT)
        first, rows = self._midi_visible()
        for k in rows:
            x, y, w, h = self._midi_rect(k - first)
            sel = k == self.midi_sel
            hov = self.menu_hover == ('midi', k)
            if sel:
                c.rect(self._X(x) - 2, self._Y(y) - 2, w * self.sc + 4, h * self.sc + 4, ov.C_GOLD)
            c.rect(self._X(x), self._Y(y), w * self.sc, h * self.sc, ov.C_CELL_HOVER if hov else ov.C_CELL)
            name = files[k][0].replace('_', ' ')
            self._text(c, name if len(name) <= 30 else name[:28] + "..", x + 12, y + 20, 14, ov.C_WHITE, 'left')
            key_ = None
            for sg_i, sg in enumerate(SONGS):
                if sg.get('midi') and sg['title'] == name[:30]:
                    key_ = sg_i
            if key_ is not None:
                best = self._best(key_, self.diff)
                self._text(c, "Best %d" % best if best else "loaded", x + w - 12, y + 20, 11,
                           ov.C_GOLD if best else ov.C_TEXT, 'right')
        if len(files) > 8:
            self._text(c, "%d - %d of %d  (Up / Down)" % (first + 1, rows[-1] + 1, len(files)), W / 2, 74, 10, ov.C_TEXT)
        if self.midi_msg:
            self._text(c, self.midi_msg[:52], W / 2, 100, 11, ov.C_BAD)
        hov = self.menu_hover == ('back', 0)
        c.rect(self._X(140), self._Y(44), 140 * self.sc, 34 * self.sc, ov.C_CELL_HOVER if hov else ov.C_CELL)
        self._text(c, "BACK", 210, 61, 15, ov.C_WHITE)
        self.draw_hint(c, "Click / ENTER: use this file   Up/Down: choose   M / BACKSPACE: back")

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
                if n.dur > 0:                                      # long note: tail behind the head
                    self._tail(c, lane, max(u, ub), min(1.0, (n.t + n.dur - t) / tv), col, False)
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

        for lane, n in self.holding.items():                      # held notes: bright tail to the line
            self._tail(c, lane, ub, min(1.0, (n.t + n.dur - t) / tv), LANE_COLORS[lane], True)

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
        self._poly(c, [(0, 0), (W, 0), (W, 4), (0, 4)],
                   (0.12, 0.13, 0.17, 1.0))
        self._poly(c, [(0, 0), (W * prog, 0),
                   (W * prog, 4), (0, 4)], LANE_COLORS[2])

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
    "MIDI: drop .mid files in the 'midi' folder, then 6 / M",
    "Long notes: keep the key held until the tail ends",
    "R: restart song   ESC: quit",
])


load_default_midi_songs()


def draw_ui(layout, context):
    RUNNER.draw_ui(layout)


def register():
    RUNNER.register()


def unregister():
    RUNNER.unregister()
