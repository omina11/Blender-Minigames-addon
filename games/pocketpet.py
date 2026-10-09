"""Pocket Pet - a tamagotchi-style virtual pet (GPU overlay).

Hatch an egg and look after it: feed it, play with it, clean up after it, put it
to bed (lights off) and give it medicine when it is sick. The way you care for it
decides how it grows up. The pet is SAVED between sessions: while the game is
closed time keeps passing (gently - it cannot die while you are away).

Time modes (chosen on the title screen):
    Fast  - one pet day lasts 15 minutes (a whole life in about 3 hours)
    Real  - one pet day lasts 24 hours

Mini games earn coins and happiness: Fruit Catch and Reflex Tap.
Records (pocketpet_life / pocketpet_catch / pocketpet_tap): the best care score of a
pet that lived its whole life, and the best score of each mini game.

Keys: F feed   P play   L lights   C clean   H medicine   S shop   M title
"""

import json
import math
import os
import random
import tempfile
import time
import traceback

from . import _overlay as ov
from . import _record as _rec

GAME_NAME = "Pocket Pet"
GAME_ICON = 'OUTLINER_OB_ARMATURE'

W, H = 640.0, 400.0
LIFESPAN = 12.0                                  # pet days
STAGES = (('egg', 0.0), ('baby', 0.04), ('child', 1.0), ('teen', 3.0), ('adult', 6.0), ('senior', 10.0))
DAY_SECONDS = {'fast': 900.0, 'real': 86400.0}
MAX_OFFLINE_H = 14.0
FORM_NAMES = {'good': "Star", 'ok': "Buddy", 'bad': "Grumpy"}

THEMES = [("Cozy room", 0), ("Space station", 40), ("Garden", 70)]
SHOP = [("Treat", 8, "treat"), ("Medicine", 15, "med"), ("Space station theme", 40, "theme1"), ("Garden theme", 70, "theme2")]


def _state_path():
    try:
        d = _rec.RecordManager(game_name="pocketpet").save_dir
    except Exception:
        d = tempfile.gettempdir()
    return os.path.join(d, "pocketpet_state.json")


def _clamp(v, lo=0.0, hi=100.0):
    return lo if v < lo else hi if v > hi else v


def _new_state(mode='fast', theme_owned=None, coins=0):
    now = time.time()
    return dict(name=random.choice(("Pip", "Momo", "Bibi", "Nori", "Kiko", "Luma", "Taro", "Zuzu")),
                born=now, last=now, age=0.0, hunger=80.0, happy=80.0, energy=90.0, health=100.0,
                poops=0, sick=False, asleep=False, coins=coins, treats=1, meds=1, form='ok',
                care_sum=0.0, care_t=0.0, alive=True, retired=False, mode=mode, theme=0,
                owned=list(theme_owned or [0]), hue=random.random(), poop_t=random.uniform(2, 5),
                snacks=0.0, result=None, born_pet_day=0.0, mini={'catch': 0, 'tap': 0}, hatch_msg=False)


def _lerp(a, b, t):
    return a + (b - a) * t


def _hsv(h, s, v, a=1.0):
    i = int(h * 6) % 6
    f = h * 6 - int(h * 6)
    p, q, t = v * (1 - s), v * (1 - f * s), v * (1 - (1 - f) * s)
    r, g, b = [(v, t, p), (q, v, p), (p, v, t), (p, q, v), (t, p, v), (v, p, q)][i]
    return (r, g, b, a)


class PocketPet(ov.BaseGame):
    keys = ('F', 'P', 'L', 'C', 'H', 'S', 'M', 'SPACE', 'LEFT_ARROW', 'RIGHT_ARROW', 'ONE', 'TWO', 'THREE')

    # ------------------------------------------------------------------ setup
    def setup(self):
        self.sc, self.fx0, self.fy0 = 1.0, 0.0, 0.0
        self.time = 0.0
        self.path = _state_path()
        self.s = self._load()
        self.view = 'title'                 # title | home | menu | catch | tap
        self.popup = None                   # None | 'feed' | 'play' | 'shop'
        self.hover = None
        self.mouse = None
        self.msg, self.msg_t = "", 0.0
        self.confirm_new = 0.0
        self.autosave_t = 0.0
        self.anim = dict(x=250.0, tx=250.0, face=1, wait=1.0, blink=2.0, bounce=0.0, eat=0.0, play=0.0, happy_fx=0.0,
                         food=None, poof=0.0, evolve=0.0)
        self.hearts = []
        self.mgr_cache = {}
        self.mini = None
        self._offline_note = ""
        self._apply_offline()

    def reset(self):
        self.view = 'title'
        self.popup = None
        self.mini = None

    def close(self):
        self.save()

    # ------------------------------------------------------------ persistence
    def _load(self):
        try:
            with open(self.path, 'r') as f:
                st = json.load(f)
            base = _new_state(st.get('mode', 'fast'))
            base.update(st)
            base['mini'] = dict({'catch': 0, 'tap': 0}, **st.get('mini', {}))
            return base
        except Exception:
            return None

    def save(self):
        if self.s is None:
            return
        self.s['last'] = time.time()
        try:
            with open(self.path, 'w') as f:
                json.dump(self.s, f)
        except Exception:
            pass

    def _apply_offline(self):
        s = self.s
        if not s or not s['alive'] or s['retired']:
            return
        away = max(0.0, time.time() - s['last'])
        if away < 20:
            return
        hours = min(MAX_OFFLINE_H, away / DAY_SECONDS[s['mode']] * 24.0) * 0.6
        if hours <= 0.02:
            return
        was = (s['hunger'], s['happy'], s['health'])
        self._simulate(hours, offline=True)
        self._offline_note = "Welcome back! You were away %s" % self._ago(away)
        s['hunger'] = max(s['hunger'], 12.0)
        s['happy'] = max(s['happy'], 12.0)
        s['health'] = max(s['health'], 40.0)
        s['energy'] = max(s['energy'], 20.0)

    @staticmethod
    def _ago(sec):
        if sec < 3600:
            return "%d min" % (sec // 60)
        if sec < 86400:
            return "%.1f h" % (sec / 3600)
        return "%.1f days" % (sec / 86400)

    # ---------------------------------------------------------------- records
    def _mgr(self, key):
        if key not in self.mgr_cache:
            try:
                self.mgr_cache[key] = _rec.RecordManager(game_name="pocketpet_" + key)
            except Exception:
                self.mgr_cache[key] = None
        return self.mgr_cache[key]

    def _best(self, key):
        m = self._mgr(key)
        try:
            return int(m.get_records().get("highest_score", 0) or 0) if m else 0
        except Exception:
            return 0

    def _record_win(self, key, score):
        m = self._mgr(key)
        if m:
            try:
                m.add_win_record(score=int(score))
            except Exception:
                traceback.print_exc()

    def _record_loss(self, key):
        m = self._mgr(key)
        if m:
            try:
                m.add_lose_record()
            except Exception:
                traceback.print_exc()

    # -------------------------------------------------------------- simulation
    def stage(self):
        age = self.s['age']
        name = STAGES[0][0]
        for n, a in STAGES:
            if age >= a:
                name = n
        return name

    def _say(self, text, t=2.4):
        self.msg, self.msg_t = text, t

    def _simulate(self, hours, offline=False):
        s = self.s
        if not s['alive'] or s['retired']:
            return
        steps = max(1, int(hours / 0.25))
        h = hours / steps
        for _ in range(steps):
            self._tick(h, offline)
            if not s['alive'] or s['retired']:
                break

    def _tick(self, h, offline):
        s = self.s
        old_stage = self.stage()
        s['age'] += h / 24.0
        st = self.stage()
        if st != old_stage and not offline:
            self.anim['evolve'] = 1.2
            if old_stage == 'egg':
                self._say("%s hatched!" % s['name'], 3.0)
            else:
                self._say("%s grew into a %s!" % (s['name'], st), 3.0)
        if st == 'egg':
            return
        if old_stage == 'child' and st == 'teen':
            avg = s['care_sum'] / max(0.01, s['care_t'])
            s['form'] = 'good' if avg >= 0.72 else 'ok' if avg >= 0.50 else 'bad'
        mult = 1.35 if st == 'baby' else 1.0
        awake = not s['asleep']
        s['hunger'] -= (3.4 if awake else 1.0) * mult * h
        s['happy'] -= ((2.4 if awake else 0.4) + s['poops'] * 1.4 + (2.0 if s['hunger'] < 25 else 0.0)) * h
        if awake:
            s['energy'] -= 4.2 * h
        else:
            s['energy'] += 14.0 * h
        if awake:
            s['poop_t'] -= h
            if s['poop_t'] <= 0 and s['poops'] < 4:
                s['poops'] += 1
                s['poop_t'] = random.uniform(3.5, 6.5)
        s['snacks'] = max(0.0, s['snacks'] - 0.7 * h)
        risk = 0.0
        if s['poops'] >= 3:
            risk += 0.16
        if s['hunger'] < 8:
            risk += 0.12
        if s['snacks'] > 5:
            risk += 0.12
        if not s['sick'] and random.random() < risk * h:
            s['sick'] = True
            if not offline:
                self._say("%s feels sick..." % s['name'])
        if s['sick']:
            s['health'] -= 5.0 * h
            s['happy'] -= 3.0 * h
        if s['hunger'] <= 0:
            s['health'] -= 8.0 * h
        if s['energy'] <= 0 and awake:
            s['happy'] -= 5.0 * h
            s['health'] -= 2.0 * h
        if not s['sick'] and s['hunger'] > 40 and s['energy'] > 30:
            s['health'] += 1.5 * h
        for k in ('hunger', 'happy', 'energy', 'health'):
            s[k] = _clamp(s[k])
        if s['asleep'] and s['energy'] >= 99.0:
            s['asleep'] = False
            if not offline:
                self._say("%s woke up" % s['name'])
        s['care_sum'] += (s['hunger'] + s['happy'] + s['health']) / 300.0 * h
        s['care_t'] += h
        if s['health'] <= 0:
            s['alive'] = False
            s['result'] = 'died'
            self._record_loss('life')
            self.view = 'home'
        elif s['age'] >= LIFESPAN:
            s['retired'] = True
            avg = s['care_sum'] / max(0.01, s['care_t'])
            s['result'] = int(avg * 1000 + len(s['owned']) * 20 + s['mini']['catch'] + s['mini']['tap'])
            self._record_win('life', s['result'])

    # ---------------------------------------------------------------- actions
    def _need_awake(self):
        if self.s['asleep']:
            self._say("Shh... %s is sleeping" % self.s['name'])
            return False
        return True

    def _ready(self):
        s = self.s
        if not s or not s['alive'] or s['retired']:
            return False
        if self.stage() == 'egg':
            self._say("The egg is warm - wait for it to hatch")
            return False
        return True

    def do_meal(self):
        s = self.s
        if not self._ready() or not self._need_awake():
            return
        if s['hunger'] > 92:
            self._say("%s isn't hungry" % s['name'])
            return
        s['hunger'] = _clamp(s['hunger'] + 35)
        s['happy'] = _clamp(s['happy'] + 3)
        self.anim.update(eat=1.4, food='meal')
        self.popup = None
        self._say("Yum!", 1.2)

    def do_snack(self):
        s = self.s
        if not self._ready() or not self._need_awake():
            return
        s['hunger'] = _clamp(s['hunger'] + 12)
        s['happy'] = _clamp(s['happy'] + 10)
        s['snacks'] += 1.4
        self.anim.update(eat=1.0, food='snack')
        self.popup = None
        self._say("Tasty snack!" if s['snacks'] < 5 else "Careful - too many snacks...", 1.4)

    def do_treat(self):
        s = self.s
        if not self._ready() or not self._need_awake():
            return
        if s['treats'] <= 0:
            self._say("No treats left - buy one in the shop")
            return
        s['treats'] -= 1
        s['happy'] = _clamp(s['happy'] + 30)
        s['hunger'] = _clamp(s['hunger'] + 8)
        self.anim.update(eat=1.2, food='treat', happy_fx=1.6)
        self.popup = None
        self._say("A special treat!", 1.4)

    def do_clean(self):
        s = self.s
        if not self._ready():
            return
        if s['poops'] <= 0:
            self._say("Everything is clean")
            return
        s['poops'] = 0
        s['happy'] = _clamp(s['happy'] + 4)
        self.anim['poof'] = 0.7
        self._say("Sparkling clean!", 1.2)

    def do_medicine(self):
        s = self.s
        if not self._ready():
            return
        if not s['sick']:
            self._say("%s is not sick" % s['name'])
            return
        if s['meds'] <= 0:
            self._say("No medicine - buy some in the shop")
            return
        s['meds'] -= 1
        s['sick'] = False
        s['health'] = _clamp(s['health'] + 20)
        self.anim['happy_fx'] = 1.2
        self._say("Feeling better!", 1.6)

    def do_sleep(self):
        s = self.s
        if not self._ready():
            return
        if not s['asleep'] and s['energy'] > 92:
            self._say("%s isn't tired" % s['name'])
            return
        s['asleep'] = not s['asleep']
        self._say("Lights out - good night" if s['asleep'] else "Good morning!", 1.4)

    def start_mini(self, which):
        s = self.s
        if not self._ready() or not self._need_awake():
            return
        if s['energy'] < 15:
            self._say("%s is too tired to play" % s['name'])
            return
        self.popup = None
        self.view = which
        if which == 'catch':
            self.mini = dict(t=30.0, score=0, x=W / 2, items=[], spawn=0.4, over=False, lives=3, shake=0.0, fx=[])
        else:
            self.mini = dict(t=25.0, score=0, targets=[], spawn=0.3, over=False, miss=0, hits=0, fx=[])

    def end_mini(self):
        s, m = self.s, self.mini
        key = self.view
        score = m['score']
        coins = score // 2 if key == 'catch' else score
        s['coins'] += coins
        s['happy'] = _clamp(s['happy'] + min(35, 8 + score))
        s['energy'] = _clamp(s['energy'] - 12)
        s['hunger'] = _clamp(s['hunger'] - 6)
        new = score > s['mini'][key]
        s['mini'][key] = max(s['mini'][key], score)
        if score > 0:
            self._record_win(key, score)
        m['result'] = dict(score=score, coins=coins, new=new)
        m['over'] = True
        self.save()

    def buy(self, item):
        s = self.s
        name, price, kind = SHOP[item]
        if kind.startswith('theme'):
            idx = int(kind[-1])
            if idx in s['owned']:
                s['theme'] = idx
                self._say("Theme changed")
                return
        if s['coins'] < price:
            self._say("Not enough coins (%d needed)" % price)
            return
        s['coins'] -= price
        if kind == 'treat':
            s['treats'] += 1
        elif kind == 'med':
            s['meds'] += 1
        else:
            idx = int(kind[-1])
            s['owned'].append(idx)
            s['theme'] = idx
        self._say("Bought: %s" % name, 1.4)
        self.save()

    def new_pet(self):
        old = self.s
        self.s = _new_state(old['mode'] if old else 'fast', old['owned'] if old else [0], old['coins'] if old else 0)
        if old:
            self.s['mini'] = old['mini']
            self.s['theme'] = old['theme']
        self.view = 'home'
        self.popup = None
        self.anim.update(x=W / 2 - 40, tx=W / 2 - 40)
        self._say("A new egg! Keep it warm", 3.0)
        self.save()

    # ----------------------------------------------------------------- update
    def update(self, dt):
        self.time += dt
        dt = min(dt, 0.1)
        self.msg_t = max(0.0, self.msg_t - dt)
        self.confirm_new = max(0.0, self.confirm_new - dt)
        a = self.anim
        for k in ('eat', 'happy_fx', 'poof', 'evolve'):
            a[k] = max(0.0, a[k] - dt)
        self.hearts = [[x, y + 26 * dt, t + dt] for x, y, t in self.hearts if t < 1.4]
        if self.view in ('home', 'menu') and self.s and self.s['alive'] and not self.s['retired']:
            hours = dt * 24.0 / DAY_SECONDS[self.s['mode']]
            self._simulate(hours)
            self.autosave_t += dt
            if self.autosave_t > 15:
                self.autosave_t = 0
                self.save()
        if self.view in ('home',) or self.view == 'title':
            self._wander(dt)
        if self.view == 'catch':
            self._upd_catch(dt)
        elif self.view == 'tap':
            self._upd_tap(dt)
        return True

    def _wander(self, dt):
        a, s = self.anim, self.s
        a['blink'] -= dt
        if a['blink'] < -0.14:
            a['blink'] = random.uniform(2.0, 5.0)
        if not s or s['asleep'] or not s['alive']:
            return
        a['wait'] -= dt
        if a['wait'] <= 0:
            a['tx'] = random.uniform(130, 440) if self.stage() != 'egg' else 250
            a['wait'] = random.uniform(2.0, 5.0)
        dx = a['tx'] - a['x']
        sp = 36.0 if not s['sick'] else 14.0
        if abs(dx) > 2 and a['eat'] <= 0 and self.stage() != 'egg':
            a['x'] += math.copysign(min(abs(dx), sp * dt), dx)
            a['face'] = 1 if dx > 0 else -1
            a['bounce'] += dt * 9
        else:
            a['bounce'] += dt * 2.5
        if a['happy_fx'] > 0 and random.random() < 0.25:
            self.hearts.append([a['x'] + random.uniform(-20, 20), 130 + random.uniform(0, 30), 0.0])

    # ---- mini games
    def _upd_catch(self, dt):
        m = self.mini
        if m['over']:
            return
        m['t'] -= dt
        m['shake'] = max(0.0, m['shake'] - dt)
        if self.mouse:
            m['x'] += (_clamp(self.mouse[0], 50, W - 50) - m['x']) * min(1.0, 14 * dt)
        m['spawn'] -= dt
        if m['spawn'] <= 0:
            m['spawn'] = max(0.22, 0.65 - (30 - m['t']) * 0.012)
            kind = random.choices(['apple', 'gold', 'bomb'], [70, 8, 22])[0]
            m['items'].append(dict(x=random.uniform(40, W - 40), y=H + 10, v=random.uniform(130, 200) + (30 - m['t']) * 3, kind=kind, r=random.random()))
        for it in m['items']:
            it['y'] -= it['v'] * dt
        keep = []
        for it in m['items']:
            if abs(it['y'] - 62) < 16 and abs(it['x'] - m['x']) < 44:
                if it['kind'] == 'bomb':
                    m['lives'] -= 1
                    m['shake'] = 0.4
                    m['fx'].append([it['x'], it['y'], 0.0, 'boom'])
                else:
                    m['score'] += 3 if it['kind'] == 'gold' else 1
                    m['fx'].append([it['x'], it['y'], 0.0, 'plus'])
                continue
            if it['y'] > 20:
                keep.append(it)
        m['items'] = keep
        for f in m['fx']:
            f[2] += dt
        m['fx'] = [f for f in m['fx'] if f[2] < 0.5]
        if m['t'] <= 0 or m['lives'] <= 0:
            m['t'] = max(0.0, m['t'])
            self.end_mini()

    def _upd_tap(self, dt):
        m = self.mini
        if m['over']:
            return
        m['t'] -= dt
        m['spawn'] -= dt
        if m['spawn'] <= 0 and len(m['targets']) < 4:
            m['spawn'] = max(0.35, 0.8 - (25 - m['t']) * 0.015)
            m['targets'].append(dict(x=random.uniform(70, W - 70), y=random.uniform(110, H - 80), t=0.0,
                                     life=max(0.7, 1.4 - (25 - m['t']) * 0.02), hue=random.random()))
        for tg in m['targets']:
            tg['t'] += dt
        for tg in list(m['targets']):
            if tg['t'] > tg['life']:
                m['targets'].remove(tg)
                m['miss'] += 1
        for f in m['fx']:
            f[2] += dt
        m['fx'] = [f for f in m['fx'] if f[2] < 0.5]
        if m['t'] <= 0:
            m['t'] = 0.0
            self.end_mini()

    # ------------------------------------------------------------------ input
    def _logical(self, x, y):
        return (x - self.fx0) / self.sc, (y - self.fy0) / self.sc

    BTN = [("feed", "FEED", 'F'), ("play", "PLAY", 'P'), ("sleep", "LIGHTS", 'L'),
           ("clean", "CLEAN", 'C'), ("med", "HEAL", 'H'), ("shop", "SHOP", 'S')]

    def _btn_rect(self, i):
        return 14.0 + i * 74.0, 8.0, 68.0, 46.0

    def _popup_items(self):
        s = self.s
        if self.popup == 'feed':
            return [("Meal  (+35 food)", self.do_meal), ("Snack  (+happy, careful!)", self.do_snack),
                    ("Treat x%d  (big happiness)" % s['treats'], self.do_treat)]
        if self.popup == 'play':
            return [("Fruit Catch   best %d" % s['mini']['catch'], lambda: self.start_mini('catch')),
                    ("Reflex Tap   best %d" % s['mini']['tap'], lambda: self.start_mini('tap'))]
        if self.popup == 'shop':
            out = []
            for i, (n, p, k) in enumerate(SHOP):
                tag = ""
                if k.startswith('theme'):
                    tag = "  (owned - click to use)" if int(k[-1]) in s['owned'] else ""
                out.append(("%s  -  %d coins%s" % (n, p, tag), (lambda i=i: self.buy(i))))
            return out
        return []

    def _popup_rect(self, i):
        return 190.0, 296.0 - i * 40.0 - 0, 260.0, 34.0

    def _title_btns(self):
        return [("continue", 200.0, 190.0, 240.0, 44.0), ("new", 200.0, 138.0, 240.0, 44.0), ("mode", 200.0, 86.0, 240.0, 44.0)]

    def move(self, x, y):
        if x < -1e5:
            self.mouse = None
            return False
        self.mouse = self._logical(x, y)
        mx, my = self.mouse
        h = None
        if self.view == 'title':
            for name, bx, by, bw, bh in self._title_btns():
                if bx <= mx <= bx + bw and by <= my <= by + bh:
                    h = name
        elif self.view == 'home':
            if self.popup:
                for i in range(len(self._popup_items())):
                    bx, by, bw, bh = self._popup_rect(i)
                    if bx <= mx <= bx + bw and by <= my <= by + bh:
                        h = ('pop', i)
            for i in range(len(self.BTN)):
                bx, by, bw, bh = self._btn_rect(i)
                if bx <= mx <= bx + bw and by <= my <= by + bh:
                    h = ('btn', i)
        changed = h != self.hover
        self.hover = h
        return changed or self.view in ('catch', 'tap')

    def _press_btn(self, name):
        if self.popup == name or (name in ('feed', 'play', 'shop') and self.popup):
            self.popup = None if self.popup == name else name
            return
        if name in ('feed', 'play', 'shop'):
            if name != 'shop' and not self._ready():
                return
            self.popup = name
        elif name == 'sleep':
            self.popup = None
            self.do_sleep()
        elif name == 'clean':
            self.do_clean()
        elif name == 'med':
            self.do_medicine()

    def click(self, x, y, button):
        mx, my = self._logical(x, y)
        self.mouse = (mx, my)
        if self.view == 'title':
            for name, bx, by, bw, bh in self._title_btns():
                if bx <= mx <= bx + bw and by <= my <= by + bh:
                    self._title_click(name)
            return
        if self.view == 'catch' or self.view == 'tap':
            m = self.mini
            if m['over']:
                self.view = 'home'
                self.mini = None
                return
            if self.view == 'tap':
                for tg in list(m['targets']):
                    r = 26.0 * (1.0 - 0.35 * tg['t'] / tg['life'])
                    if (mx - tg['x']) ** 2 + (my - tg['y']) ** 2 <= (r + 6) ** 2:
                        m['targets'].remove(tg)
                        pts = 2 if tg['t'] < tg['life'] * 0.4 else 1
                        m['score'] += pts
                        m['hits'] += 1
                        m['fx'].append([tg['x'], tg['y'], 0.0, '+%d' % pts])
                        return
                m['miss'] += 1
            return
        if self.popup:
            for i, (label, fn) in enumerate(self._popup_items()):
                bx, by, bw, bh = self._popup_rect(i)
                if bx <= mx <= bx + bw and by <= my <= by + bh:
                    fn()
                    return
        for i, (name, _, _) in enumerate(self.BTN):
            bx, by, bw, bh = self._btn_rect(i)
            if bx <= mx <= bx + bw and by <= my <= by + bh:
                self._press_btn(name)
                return
        self.popup = None
        # petting: click the pet
        s = self.s
        a = self.anim
        if s and s['alive'] and not s['asleep'] and not s['retired'] and abs(mx - a['x']) < 40 and 100 < my < 230:
            s['happy'] = _clamp(s['happy'] + 1.5)
            self.hearts.append([a['x'] + random.uniform(-14, 14), 150, 0.0])
        # clicking a poop cleans it
        if s and s['poops'] > 0:
            for i in range(s['poops']):
                px = 120 + i * 90
                if abs(mx - px) < 18 and 96 < my < 130:
                    s['poops'] -= 1
                    self.anim['poof'] = 0.5

    def _title_click(self, name):
        s = self.s
        if name == 'continue':
            if s and (s['alive'] or s['retired'] or s['result']):
                self.view = 'home'
            else:
                self.new_pet()
        elif name == 'new':
            if s and s['alive'] and not s['retired'] and self.confirm_new <= 0:
                self.confirm_new = 2.5
                self._say("Click again to abandon %s and hatch a new egg" % s['name'], 2.5)
            else:
                self.new_pet()
        elif name == 'mode':
            if self.s is None:
                self.s = _new_state('fast')
            self.s['mode'] = 'real' if self.s['mode'] == 'fast' else 'fast'
            self.save()

    def key(self, key, repeat):
        if key == 'M':
            if self.view in ('catch', 'tap'):
                self.mini = None
            self.view = 'title'
            self.popup = None
            self.save()
            return
        if self.view == 'title':
            if key == 'SPACE':
                self._title_click('continue')
            return
        if self.view in ('catch', 'tap'):
            m = self.mini
            if self.view == 'catch' and not m['over']:
                if key == 'LEFT_ARROW':
                    m['x'] = max(50, m['x'] - 40)
                elif key == 'RIGHT_ARROW':
                    m['x'] = min(W - 50, m['x'] + 40)
            if key == 'SPACE' and m['over']:
                self.view = 'home'
                self.mini = None
            return
        if self.s is None:
            return
        if key == 'F':
            self._press_btn('feed')
        elif key == 'P':
            self._press_btn('play')
        elif key == 'L':
            self._press_btn('sleep')
        elif key == 'C':
            self._press_btn('clean')
        elif key == 'H':
            self._press_btn('med')
        elif key == 'S':
            self._press_btn('shop')
        elif key in ('ONE', 'TWO', 'THREE') and self.popup:
            items = self._popup_items()
            i = {'ONE': 0, 'TWO': 1, 'THREE': 2}[key]
            if i < len(items):
                items[i][1]()

    # ----------------------------------------------------------------- layout
    def layout(self, view):
        cs = self.fit_cell(view, 8, 5, max_cell=100, min_cell=36)
        self.place(view, 8 * cs, 5 * cs)
        self.sc = cs / 80.0
        self.fx0 = self.left
        self.fy0 = self.top - 5 * cs

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

    def _oval(self, cx, cy, rx, ry, n=22, a0=0.0, a1=2 * math.pi, rot=0.0):
        ca, sa = math.cos(rot), math.sin(rot)
        pts = []
        for k in range(n + 1):
            a = a0 + (a1 - a0) * k / n
            x, y = math.cos(a) * rx, math.sin(a) * ry
            pts.append((cx + x * ca - y * sa, cy + x * sa + y * ca))
        return pts

    def _rr(self, c, x, y, w, h, r, col, seg=5):
        pts = []
        for cx, cy, a0 in ((x + w - r, y + h - r, 0), (x + r, y + h - r, 90), (x + r, y + r, 180), (x + w - r, y + r, 270)):
            for k in range(seg + 1):
                a = math.radians(a0 + 90.0 * k / seg)
                pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
        self._poly(c, pts, col)

    def _text(self, c, s, x, y, size, col=ov.C_WHITE, align='center'):
        c.text(s, self._X(x), self._Y(y), size * self.sc, col, align)

    # ---- room
    def _room(self, c):
        s = self.s
        theme = s['theme'] if s else 0
        pal = [((0.95, 0.82, 0.68), (0.80, 0.55, 0.38), (0.55, 0.36, 0.24)),
               ((0.16, 0.18, 0.30), (0.26, 0.30, 0.46), (0.12, 0.13, 0.22)),
               ((0.72, 0.90, 0.72), (0.40, 0.70, 0.36), (0.30, 0.52, 0.26))][theme]
        wall, floor, edge = pal
        asleep = bool(s and s['asleep'])
        self._rect(c, 0, 66, W, 334, wall + (1.0,))
        for i in range(10):
            self._rect(c, 0, 66 + i * 33, W, 17, (1, 1, 1, 0.03))
        self._rect(c, 0, 66, W, 98, floor + (1.0,))
        for i in range(14):
            self._rect(c, i * 48 - 4, 66, 2, 98, (0, 0, 0, 0.10))
        self._rect(c, 0, 160, W, 6, edge + (1.0,))
        # window with sky by pet clock
        hour = ((s['age'] * 24.0) % 24.0) if s else 12.0
        night = hour < 6 or hour > 19
        sky = (0.10, 0.12, 0.28, 1) if night else (0.55, 0.80, 0.98, 1)
        self._rect(c, 468, 236, 130, 110, edge + (1.0,))
        self._rect(c, 474, 242, 118, 98, sky)
        if night:
            for k in range(7):
                self._rect(c, 480 + (k * 37) % 104, 250 + (k * 53) % 80, 2, 2, (1, 1, 1, 0.8))
            self._circ(c, 560, 318, 12, (1.0, 0.95, 0.7, 1))
        else:
            self._circ(c, 500, 316, 12, (1.0, 0.9, 0.35, 1))
            self._poly(c, [(520, 262), (560, 262), (566, 270), (512, 270)], (1, 1, 1, 0.9))
        self._rect(c, 530, 242, 3, 98, edge + (1.0,))
        self._rect(c, 474, 290, 118, 3, edge + (1.0,))
        if theme == 1:
            for k in range(14):
                self._circ(c, 40 + (k * 83) % 400, 200 + (k * 61) % 150, 1.6, (1, 1, 1, 0.7), 6)
            self._circ(c, 90, 310, 26, (0.45, 0.35, 0.65, 1))
            c.ring(self._X(90), self._Y(310), 38 * self.sc, 3 * self.sc, (0.8, 0.7, 1, 0.8), 28)
        elif theme == 2:
            for k in range(5):
                self._circ(c, 60 + k * 100, 190 + (k % 2) * 20, 18, (0.30, 0.6, 0.28, 1))
            for k in range(6):
                self._circ(c, 70 + k * 92, 175, 6, (1.0, 0.5 + 0.1 * k, 0.6, 1))
        else:
            self._rect(c, 70, 240, 90, 66, (0.78, 0.55, 0.35, 1))       # picture frame
            self._rect(c, 76, 246, 78, 54, (0.55, 0.78, 0.95, 1))
            self._poly(c, [(76, 246), (110, 288), (154, 246)], (0.35, 0.65, 0.4, 1))
        # furniture: lamp
        self._rect(c, 40, 66, 8, 90, (0.35, 0.28, 0.25, 1))
        self._poly(c, [(26, 156), (62, 156), (54, 184), (34, 184)], (1.0, 0.85, 0.45, 1) if not asleep else (0.5, 0.45, 0.35, 1))
        # plant
        self._poly(c, [(560, 66), (592, 66), (588, 98), (564, 98)], (0.7, 0.4, 0.28, 1))
        for k in range(5):
            self._line(c, (576, 98), (576 + (k - 2) * 12, 130 + (k % 2) * 8), 5, (0.25, 0.6, 0.3, 1))
        self._rect(c, 0, 64, W, 3, (0, 0, 0, 0.3))

    # ---- pet drawing
    def _pet_colors(self):
        s = self.s
        h = s['hue']
        form = s['form']
        sat = 0.55 if form != 'bad' else 0.40
        val = 0.95 if form == 'good' else 0.85 if form == 'ok' else 0.66
        body = _hsv(h, sat, val)
        belly = _hsv(h, max(0.05, sat - 0.4), min(1.0, val + 0.1))
        dark = _hsv(h, sat + 0.15, val * 0.55)
        if self.stage() == 'senior':
            body = tuple(_lerp(body[i], 0.85, 0.30) for i in range(3)) + (1.0,)
        return body, belly, dark

    def _mood(self):
        s = self.s
        if not s['alive']:
            return 'dead'
        if s['asleep']:
            return 'sleep'
        if self.anim['eat'] > 0:
            return 'eat'
        if s['sick']:
            return 'sick'
        if s['hunger'] < 25:
            return 'hungry'
        if s['happy'] < 28 or s['poops'] >= 3:
            return 'sad'
        if s['happy'] > 75 or self.anim['happy_fx'] > 0:
            return 'happy'
        return 'ok'

    def _draw_pet(self, c, x, y_ground, scale=1.0, show_fx=True):
        s, a = self.s, self.anim
        st = self.stage()
        body, belly, dark = self._pet_colors()
        mood = self._mood()
        t = a['bounce']
        walking = abs(a['tx'] - a['x']) > 2 and not s['asleep'] and a['eat'] <= 0
        hop = abs(math.sin(t)) * 7 if walking else math.sin(t) * 1.6
        if mood == 'happy' and not walking:
            hop = abs(math.sin(self.time * 5)) * 9
        sq = 1.0 + (0.06 * math.sin(t * 2) if not walking else 0.10 * math.cos(t * 2))
        size = {'egg': 30, 'baby': 26, 'child': 32, 'teen': 38, 'adult': 44, 'senior': 44}[st] * scale
        fx = a['face']
        if mood == 'dead':
            hop = 0
        cx, cy = x, y_ground + size * 0.9 + hop
        evolve = a['evolve']
        if evolve > 0:
            size *= 1.0 + 0.25 * math.sin(evolve * 14) * evolve
        # shadow
        self._poly(c, self._oval(x, y_ground - 2, size * 0.9, size * 0.18, 18), (0, 0, 0, 0.22 - min(0.12, hop * 0.01)))
        if st == 'egg':
            wob = math.sin(self.time * 6) * 0.08 * (1 if self.s['age'] > 0.03 else 0.0)
            pts = self._oval(cx, cy + 4, size * 0.78, size * 1.05, 22, rot=wob)
            self._poly(c, pts, (0.30, 0.25, 0.28, 1))
            self._poly(c, self._oval(cx, cy + 4, size * 0.72, size * 0.99, 22, rot=wob), (0.97, 0.95, 0.88, 1))
            for k in range(5):
                ang = k * 1.26 + 0.4
                self._circ(c, cx + math.cos(ang) * size * 0.4, cy + 4 + math.sin(ang) * size * 0.55, size * 0.13,
                           _hsv(s['hue'], 0.45, 0.95), 8)
            if s['age'] > 0.025:
                self._line(c, (cx - size * 0.3, cy + size * 0.5), (cx - size * 0.1, cy + size * 0.2), 2, (0.3, 0.25, 0.25, 1))
            return
        # ears / antenna / spikes by form (behind the body)
        form = s['form']
        if st in ('child', 'teen', 'adult', 'senior'):
            if form == 'good' or st == 'child':
                for sg in (-1, 1):
                    self._poly(c, [(cx + sg * size * 0.42, cy + size * 0.55), (cx + sg * size * 0.78, cy + size * 1.25),
                                   (cx + sg * size * 0.12, cy + size * 0.86)], dark)
                    self._poly(c, [(cx + sg * size * 0.42, cy + size * 0.62), (cx + sg * size * 0.66, cy + size * 1.07),
                                   (cx + sg * size * 0.22, cy + size * 0.82)], belly)
            elif form == 'ok':
                self._line(c, (cx, cy + size * 0.8), (cx + math.sin(self.time * 3) * 4, cy + size * 1.4), 3, dark)
                self._circ(c, cx + math.sin(self.time * 3) * 4, cy + size * 1.45, size * 0.12, (1.0, 0.85, 0.3, 1), 8)
            else:
                for k in range(-2, 3):
                    self._poly(c, [(cx + k * size * 0.3 - size * 0.12, cy + size * 0.72), (cx + k * size * 0.3, cy + size * 1.2 - abs(k) * 5),
                                   (cx + k * size * 0.3 + size * 0.12, cy + size * 0.72)], dark)
        # tail
        if st in ('teen', 'adult', 'senior'):
            tx = cx - fx * size * 0.85
            self._line(c, (tx, cy - size * 0.2), (tx - fx * size * 0.35, cy + size * 0.1 + math.sin(self.time * 5) * 5), 5, dark)
            self._circ(c, tx - fx * size * 0.35, cy + size * 0.1 + math.sin(self.time * 5) * 5, size * 0.13, body, 8)
        # feet
        if st != 'baby':
            for sg in (-1, 1):
                fy = y_ground + 2 + (abs(math.sin(t + (0 if sg > 0 else 1.6))) * 4 if walking else 0)
                self._poly(c, self._oval(cx + sg * size * 0.38, fy + 4, size * 0.26, size * 0.14, 10), dark)
        # body
        if mood == 'dead':
            body = tuple(0.8 for _ in range(3)) + (0.6,)
            belly = (0.9, 0.9, 0.95, 0.6)
        bw, bh = size * (1.0 - 0.05 * (sq - 1)), size * 0.95 * sq
        self._poly(c, self._oval(cx, cy, bw * 1.04, bh * 1.04, 28), dark)
        self._poly(c, self._oval(cx, cy, bw, bh, 28), body)
        self._poly(c, self._oval(cx, cy - bh * 0.18, bw * 0.62, bh * 0.58, 22), belly)
        self._poly(c, self._oval(cx - bw * 0.35, cy + bh * 0.45, bw * 0.2, bh * 0.1, 10, rot=0.6), (1, 1, 1, 0.45))
        if st in ('teen', 'adult', 'senior'):                       # arms
            for sg in (-1, 1):
                ax = cx + sg * bw * 0.95
                ay = cy - bh * 0.05 + (math.sin(self.time * 6 + sg) * 6 if mood in ('happy', 'eat') else 0)
                self._line(c, (cx + sg * bw * 0.8, cy + bh * 0.05), (ax + sg * 4, ay - 6), 6, dark)
                self._circ(c, ax + sg * 4, ay - 6, size * 0.12, body, 8)
        if form == 'good' and st in ('adult', 'senior'):
            self._poly(c, [(cx, cy + bh * 0.55), (cx + 5, cy + bh * 0.42), (cx + 12, cy + bh * 0.42), (cx + 6, cy + bh * 0.3),
                           (cx + 9, cy + bh * 0.18), (cx, cy + bh * 0.26), (cx - 9, cy + bh * 0.18), (cx - 6, cy + bh * 0.3),
                           (cx - 12, cy + bh * 0.42), (cx - 5, cy + bh * 0.42)], (1.0, 0.85, 0.25, 1))
        # face
        ex = bw * 0.38
        ey = cy + bh * 0.22
        blink = a['blink'] < 0
        for sg in (-1, 1):
            exx = cx + sg * ex + fx * bw * 0.05
            if mood in ('sleep', 'dead') or blink:
                if mood == 'dead':
                    self._line(c, (exx - 4, ey - 4), (exx + 4, ey + 4), 2, (0.1, 0.1, 0.15, 1))
                    self._line(c, (exx - 4, ey + 4), (exx + 4, ey - 4), 2, (0.1, 0.1, 0.15, 1))
                else:
                    self._line(c, (exx - 5, ey), (exx + 5, ey), 2.2, (0.1, 0.1, 0.15, 1))
            elif mood in ('happy', 'eat'):
                pts = self._oval(exx, ey - 2, 5.5, 5.0, 8, math.pi * 0.05, math.pi * 0.95)
                for p, q in zip(pts, pts[1:]):
                    self._line(c, p, q, 2.4, (0.1, 0.1, 0.15, 1))
            else:
                r = size * 0.17
                self._circ(c, exx, ey, r + 1.4, (0.1, 0.1, 0.15, 1), 12)
                self._circ(c, exx, ey, r, (1, 1, 1, 1), 12)
                look = 0.0 if mood != 'sad' else -0.4
                self._circ(c, exx + fx * r * 0.28, ey + look, r * 0.58, (0.1, 0.1, 0.15, 1), 10)
                self._circ(c, exx + fx * r * 0.2 - 1, ey + 2, r * 0.18, (1, 1, 1, 1), 6)
                if mood in ('sad', 'sick', 'hungry'):
                    self._line(c, (exx - sg * 6, ey + r + 3), (exx + sg * 4, ey + r + 1 + (3 if sg > 0 else 0)), 2, dark)
        mx, my = cx + fx * bw * 0.05, cy - bh * 0.12
        if mood == 'happy':
            pts = self._oval(mx, my + 2, 8, 6, 10, math.pi * 1.1, math.pi * 1.9)
            for p, q in zip(pts, pts[1:]):
                self._line(c, p, q, 2.4, (0.1, 0.1, 0.15, 1))
            self._circ(c, cx - bw * 0.62, my + 3, 4.5, (1, 0.5, 0.55, 0.45), 8)
            self._circ(c, cx + bw * 0.62, my + 3, 4.5, (1, 0.5, 0.55, 0.45), 8)
        elif mood == 'eat':
            self._poly(c, self._oval(mx, my, 6, 4 + 3 * abs(math.sin(self.time * 16)), 10), (0.45, 0.1, 0.15, 1))
        elif mood in ('sad', 'sick', 'hungry'):
            pts = self._oval(mx, my - 4, 7, 5, 10, math.pi * 0.1, math.pi * 0.9)
            for p, q in zip(pts, pts[1:]):
                self._line(c, p, q, 2.2, (0.1, 0.1, 0.15, 1))
        elif mood == 'sleep':
            self._circ(c, mx, my, 2.6, (0.1, 0.1, 0.15, 1), 8)
        else:
            self._line(c, (mx - 5, my), (mx + 5, my), 2.2, (0.1, 0.1, 0.15, 1))
        if not show_fx:
            return
        # status effects
        if mood == 'sleep':
            for k in range(3):
                ph = (self.time * 0.8 + k * 0.33) % 1.0
                self._text(c, "Z", cx + bw * 0.7 + ph * 26, cy + bh + ph * 34, 10 + k * 3, (1, 1, 1, 1 - ph))
        if mood == 'sick':
            self._rr(c, cx + bw * 0.8, cy + bh * 0.3, 10, 22, 4, (0.95, 0.95, 0.98, 1))
            self._circ(c, cx + bw * 0.8 + 5, cy + bh * 0.3 + 3, 6, (0.9, 0.2, 0.25, 1), 10)
            self._text(c, "+", cx - bw * 0.8, cy + bh * 0.9, 18, (0.4, 0.9, 0.4, 0.8 + 0.2 * math.sin(self.time * 6)))
        if mood == 'hungry':
            self._text(c, "?", cx + bw * 0.8, cy + bh + 6, 20, (1, 0.8, 0.3, 1))
        if s['poops'] >= 2 and mood != 'sleep':
            for k in range(3):
                self._circ(c, cx - bw * 0.9 + k * 14, cy + bh * 0.9 + math.sin(self.time * 7 + k) * 5, 2.4, (0.4, 0.5, 0.3, 0.9), 6)
        if mood == 'eat' and a['food']:
            fk = a['food']
            fx_ = cx + fx * (bw * 1.1 - (1.4 - a['eat']) * 20)
            fy_ = cy - 4
            if fk == 'meal':
                self._poly(c, self._oval(fx_, fy_, 11, 7, 10, math.pi, 2 * math.pi), (0.95, 0.95, 0.98, 1))
                self._circ(c, fx_, fy_ + 4, 8, (0.85, 0.55, 0.25, 1), 10)
            elif fk == 'snack':
                self._circ(c, fx_, fy_, 7, (0.6, 0.38, 0.2, 1), 10)
                for k in range(3):
                    self._circ(c, fx_ + k * 3 - 3, fy_ + k * 2 - 2, 1.4, (0.25, 0.14, 0.08, 1), 5)
            else:
                self._circ(c, fx_, fy_, 8, (1.0, 0.45, 0.55, 1), 10)
                self._poly(c, [(fx_ - 7, fy_ + 6), (fx_ + 7, fy_ + 6), (fx_, fy_ - 12)], (1.0, 0.8, 0.3, 1))

    def _poops(self, c):
        s = self.s
        if not s or s['asleep'] and False:
            return
        for i in range(s['poops']):
            px = 120 + i * 90
            py = 100
            wob = math.sin(self.time * 3 + i) * 1.0
            self._poly(c, self._oval(px, py - 6, 18, 5, 12), (0, 0, 0, 0.2))
            for k, (rx, ry, yy) in enumerate(((16, 8, 0), (12, 7, 9), (8, 6, 17))):
                self._poly(c, self._oval(px + wob * k * 0.3, py + yy, rx, ry, 14), (0.42, 0.26, 0.14, 1))
                self._poly(c, self._oval(px + wob * k * 0.3 - 2, py + yy + 2, rx * 0.6, ry * 0.5, 10), (0.58, 0.38, 0.22, 1))
            self._circ(c, px - 3, py + 17, 1.4, (1, 1, 1, 1), 6)
            self._circ(c, px + 3, py + 17, 1.4, (1, 1, 1, 1), 6)
            if int(self.time * 3 + i) % 2 == 0:
                self._text(c, "~", px + 18, py + 28, 10, (0.5, 0.7, 0.3, 0.9))

    # ---- HUD
    def _bar(self, c, x, y, w, label, val, col, icon):
        self._rr(c, x, y, w, 15, 6, (0, 0, 0, 0.45))
        fillc = col if val > 25 else (0.95, 0.3, 0.3, 1.0)
        if val > 0:
            self._rr(c, x + 1.5, y + 1.5, max(8.0, (w - 3) * val / 100.0), 12, 5, fillc)
        self._rr(c, x + 3, y + 8, w - 6, 3, 1.5, (1, 1, 1, 0.18))
        ix, iy = x - 12, y + 7.5
        if icon == 'food':
            self._circ(c, ix, iy - 1, 5, (0.95, 0.3, 0.3, 1), 10)
            self._line(c, (ix, iy + 4), (ix + 2, iy + 8), 1.6, (0.3, 0.6, 0.3, 1))
        elif icon == 'happy':
            self._circ(c, ix, iy, 6, (1.0, 0.85, 0.2, 1), 12)
            pts = self._oval(ix, iy - 1, 3, 3, 8, math.pi * 1.1, math.pi * 1.9)
            for p, q in zip(pts, pts[1:]):
                self._line(c, p, q, 1.2, (0.2, 0.15, 0.1, 1))
        elif icon == 'energy':
            self._poly(c, [(ix + 1, iy + 7), (ix - 4, iy - 1), (ix, iy - 1), (ix - 1, iy - 7), (ix + 4, iy + 1), (ix, iy + 1)], (1.0, 0.9, 0.3, 1))
        else:
            self._rect(c, ix - 5, iy - 1.5, 10, 3, (0.95, 0.35, 0.4, 1))
            self._rect(c, ix - 1.5, iy - 5, 3, 10, (0.95, 0.35, 0.4, 1))

    def _hud(self, c):
        s = self.s
        x0 = 36.0
        self._bar(c, x0, 380, 130, "Food", s['hunger'], (0.95, 0.65, 0.25, 1), 'food')
        self._bar(c, x0, 360, 130, "Mood", s['happy'], (1.0, 0.85, 0.25, 1), 'happy')
        self._bar(c, 200, 380, 130, "Energy", s['energy'], (0.4, 0.75, 1.0, 1), 'energy')
        self._bar(c, 200, 360, 130, "Health", s['health'], (0.4, 0.9, 0.5, 1), 'health')
        # info card
        st = self.stage()
        self._rr(c, 372, 338, 258, 54, 8, (0, 0, 0, 0.45))
        self._text(c, "%s - %s%s" % (s['name'], st.capitalize(), (" (%s)" % FORM_NAMES[s['form']]) if st in ('teen', 'adult', 'senior') else ""),
                   384, 378, 12, ov.C_WHITE, 'left')
        self._text(c, "Age %.1f / %d days   |   %s" % (s['age'], LIFESPAN, "Fast" if s['mode'] == 'fast' else "Real"),
                   384, 362, 9.5, ov.C_TEXT, 'left')
        self._circ(c, 390, 346, 5.5, (1.0, 0.82, 0.2, 1), 10)
        self._text(c, "%d coins    treats %d    meds %d" % (s['coins'], s['treats'], s['meds']), 400, 346, 9.5, ov.C_GOLD, 'left')
        # buttons
        for i, (name, label, key) in enumerate(self.BTN):
            x, y, w, h = self._btn_rect(i)
            hov = self.hover == ('btn', i)
            on = (name == 'sleep' and s['asleep']) or self.popup == name
            alert = (name == 'clean' and s['poops'] > 0 and not s['asleep']) or (name == 'med' and s['sick']) or \
                (name == 'feed' and s['hunger'] < 30 and not s['asleep']) or (name == 'sleep' and s['energy'] < 25 and not s['asleep'])
            base = (0.95, 0.75, 0.25, 1) if on else ov.C_CELL_HOVER if hov else ov.C_CELL
            self._rr(c, x + 2, y - 3, w, h, 8, (0, 0, 0, 0.35))
            self._rr(c, x, y, w, h, 8, base)
            if alert and int(self.time * 3) % 2 == 0:
                self._rr(c, x - 2, y - 2, w + 4, h + 4, 10, (1.0, 0.35, 0.3, 0.55))
                self._rr(c, x, y, w, h, 8, base)
            ix, iy = x + w / 2, y + h - 17
            self._icon(c, name, ix, iy, s)
            self._text(c, label, x + w / 2, y + 9, 9.5, ov.C_DARK if on else ov.C_WHITE)

    def _icon(self, c, name, x, y, s):
        w = (1, 1, 1, 1)
        if name == 'feed':
            self._poly(c, self._oval(x, y - 1, 10, 6, 10, math.pi, 2 * math.pi), w)
            self._rect(c, x - 10, y - 2, 20, 2, (0.8, 0.8, 0.85, 1))
            self._circ(c, x, y + 3, 4, (0.95, 0.5, 0.25, 1), 8)
        elif name == 'play':
            self._circ(c, x, y, 8, (0.95, 0.3, 0.35, 1), 12)
            self._poly(c, self._oval(x, y, 8, 3, 10), (1, 1, 1, 0.9))
        elif name == 'sleep':
            self._circ(c, x, y, 8, (1.0, 0.95, 0.5, 1) if not s['asleep'] else (0.7, 0.8, 1, 1), 12)
            self._circ(c, x + 4, y + 3, 7, (0.30, 0.30, 0.38, 1) if not s['asleep'] else (0.95, 0.75, 0.25, 1), 12)
        elif name == 'clean':
            self._poly(c, [(x - 6, y - 6), (x + 6, y - 6), (x + 3, y + 7), (x - 3, y + 7)], (0.5, 0.8, 1, 1))
            for k in range(3):
                self._circ(c, x - 8 + k * 8, y + 8 + (k % 2) * 2, 2, (1, 1, 1, 0.9), 6)
        elif name == 'med':
            self._rect(c, x - 8, y - 3, 16, 6, (0.95, 0.3, 0.35, 1))
            self._rect(c, x - 3, y - 8, 6, 16, (0.95, 0.3, 0.35, 1))
            self._rect(c, x - 8, y - 1, 16, 2, (1, 1, 1, 0.3))
        else:
            self._circ(c, x, y, 8.5, (1.0, 0.82, 0.2, 1), 14)
            self._circ(c, x, y, 5.5, (0.85, 0.62, 0.1, 1), 12)
            self._text(c, "$", x, y, 9, (1, 0.95, 0.7, 1))

    def _popup_draw(self, c):
        items = self._popup_items()
        title = {'feed': "FEED", 'play': "PLAY", 'shop': "SHOP"}[self.popup]
        n = len(items)
        self._rr(c, 176, 296 - (n - 1) * 40 - 22, 288, 40 * n + 52, 10, (0, 0, 0, 0.62))
        self._text(c, title, 320, 296 + 24, 12, ov.C_GOLD)
        for i, (label, fn) in enumerate(items):
            x, y, w, h = self._popup_rect(i)
            hov = self.hover == ('pop', i)
            self._rr(c, x, y, w, h, 7, ov.C_CELL_HOVER if hov else ov.C_CELL)
            self._text(c, "%d  %s" % (i + 1, label), x + 12, y + h / 2, 10.5, ov.C_WHITE, 'left')

    # ---- screens
    def _title(self, c):
        s = self.s
        self._rect(c, 0, 0, W, H, (0.12, 0.10, 0.22, 1))
        for k in range(9):
            self._rect(c, 0, k * 46, W, 22, (1, 1, 1, 0.014))
        for k in range(30):
            self._circ(c, (k * 97) % 640, 120 + (k * 53) % 280, 1.4 + (k % 3) * 0.5, (1, 1, 1, 0.3 + 0.3 * math.sin(self.time * 2 + k)), 6)
        self._text(c, "POCKET PET", W / 2, 350, 46, (1.0, 0.7 + 0.2 * math.sin(self.time * 2), 0.85, 1))
        self._text(c, "Hatch it. Feed it. Love it.", W / 2, 316, 13, ov.C_TEXT)
        # little preview of the pet
        if s and s['alive'] and not s['retired']:
            self.anim['x'] = 330
            self._draw_pet(c, 90, 238, 1.0, False)
        for name, bx, by, bw, bh in self._title_btns():
            hov = self.hover == name
            self._rr(c, bx + 3, by - 4, bw, bh, 10, (0, 0, 0, 0.4))
            self._rr(c, bx, by, bw, bh, 10, ov.C_CELL_HOVER if hov else ov.C_CELL)
            if name == 'continue':
                if s and s['alive'] and not s['retired']:
                    label = "CONTINUE  -  %s, %s (%.1f d)" % (s['name'], self.stage(), s['age'])
                elif s and s['retired']:
                    label = "SEE %s'S RESULT" % s['name']
                elif s and s['result'] == 'died':
                    label = "%s passed away..." % s['name']
                else:
                    label = "HATCH AN EGG"
            elif name == 'new':
                label = "NEW EGG (abandon current pet)" if (s and s['alive'] and not s['retired']) else "NEW EGG"
            else:
                label = "TIME: %s" % ("FAST (1 day = 15 min)" if (not s or s['mode'] == 'fast') else "REAL (1 day = 24 h)")
            self._text(c, label, bx + bw / 2, by + bh / 2, 11, ov.C_WHITE)
        lb, cb, tb = self._best('life'), self._best('catch'), self._best('tap')
        self._text(c, "Best care score %d   |   Fruit Catch %d   |   Reflex Tap %d" % (lb, cb, tb), W / 2, 56, 11, ov.C_GOLD)

    def _home(self, c):
        s = self.s
        self._room(c)
        self._poops(c)
        a = self.anim
        if s['alive'] or True:
            self._draw_pet(c, a['x'], 98)
        for x, y, t in self.hearts:
            self._circ(c, x - 3, y + 2, 3.4 * (1 - t / 1.4) + 1, (1.0, 0.35, 0.5, 1 - t / 1.4), 8)
            self._circ(c, x + 3, y + 2, 3.4 * (1 - t / 1.4) + 1, (1.0, 0.35, 0.5, 1 - t / 1.4), 8)
        if a['poof'] > 0:
            for k in range(8):
                ang = k * 0.785 + a['poof']
                rr_ = (0.7 - a['poof']) * 70
                self._circ(c, 120 + math.cos(ang) * rr_ + 150, 110 + math.sin(ang) * rr_ * 0.5, 4 * a['poof'] + 1, (1, 1, 1, a['poof']), 8)
        if s['asleep']:
            self._rect(c, 0, 66, W, 334, (0.02, 0.03, 0.12, 0.55))
        if not s['alive']:
            self._ghost(c)
        if s['retired']:
            self._rect(c, 0, 66, W, 334, (0, 0, 0, 0.5))
            self._text(c, "%s lived a full life!" % s['name'], W / 2, 280, 28, ov.C_GOLD)
            self._text(c, "Care score  %d" % (s['result'] or 0), W / 2, 240, 20, ov.C_WHITE)
            self._text(c, "Saved to the records.   M: title screen  ->  NEW EGG", W / 2, 200, 12, ov.C_TEXT)
        elif not s['alive']:
            self._text(c, "%s has passed away..." % s['name'], W / 2, 280, 24, ov.C_BAD)
            self._text(c, "M: title screen  ->  NEW EGG", W / 2, 244, 12, ov.C_TEXT)
        self._hud(c)
        if self.popup:
            self._popup_draw(c)
        if self.msg_t > 0:
            self._rr(c, W / 2 - 200, 306 if not self.popup else 360 - 0, 400, 24, 10, (0, 0, 0, 0.55 * min(1.0, self.msg_t * 2)))
            self._text(c, self.msg, W / 2, 318 if not self.popup else 372, 11.5, ov.alpha(ov.C_WHITE, min(1.0, self.msg_t * 2)))
        elif self._offline_note:
            self._rr(c, W / 2 - 200, 306, 400, 24, 10, (0, 0, 0, 0.55))
            self._text(c, self._offline_note, W / 2, 318, 11.5, ov.C_WHITE)

    def _ghost(self, c):
        x, y = self.anim['x'], 170 + math.sin(self.time * 2) * 6 + (self.time % 8) * 0
        self._poly(c, self._oval(x, y + 40, 26, 30, 16, 0, math.pi), (1, 1, 1, 0.55))
        self._rect(c, x - 26, y + 6, 52, 34, (1, 1, 1, 0.55))
        for k in range(4):
            self._circ(c, x - 19.5 + k * 13, y + 6, 6.5, (1, 1, 1, 0.55), 8)
        for sg in (-1, 1):
            self._circ(c, x + sg * 9, y + 32, 3.5, (0.1, 0.1, 0.15, 0.9), 8)

    def _catch(self, c):
        m = self.mini
        self._rect(c, 0, 0, W, H, (0.55, 0.82, 0.98, 1))
        self._circ(c, 560, 330, 30, (1.0, 0.92, 0.45, 1))
        self._rect(c, 0, 0, W, 54, (0.35, 0.62, 0.30, 1))
        self._rect(c, 0, 50, W, 5, (0.28, 0.5, 0.24, 1))
        sx = random.uniform(-3, 3) * m['shake'] * 8
        for it in m['items']:
            x, y = it['x'], it['y']
            if it['kind'] == 'apple':
                self._circ(c, x, y, 11, (0.85, 0.15, 0.2, 1))
                self._circ(c, x - 3, y + 3, 3, (1, 1, 1, 0.5), 6)
                self._line(c, (x, y + 10), (x + 3, y + 17), 2.5, (0.4, 0.28, 0.15, 1))
                self._poly(c, [(x + 3, y + 14), (x + 12, y + 17), (x + 5, y + 10)], (0.3, 0.7, 0.3, 1))
            elif it['kind'] == 'gold':
                self._circ(c, x, y, 12, (1.0, 0.8, 0.15, 1))
                self._circ(c, x, y, 8, (1.0, 0.92, 0.45, 1))
                self._text(c, "3", x, y, 10, (0.6, 0.4, 0, 1))
            else:
                self._circ(c, x, y, 11, (0.12, 0.12, 0.16, 1))
                self._line(c, (x + 6, y + 9), (x + 12, y + 16), 2.5, (0.6, 0.5, 0.3, 1))
                self._circ(c, x + 13, y + 17, 3 + math.sin(self.time * 30), (1.0, 0.7, 0.2, 1), 6)
                self._circ(c, x - 3, y + 3, 2.5, (1, 1, 1, 0.4), 6)
        bx = m['x'] + sx
        self._poly(c, [(bx - 44, 82), (bx + 44, 82), (bx + 34, 50), (bx - 34, 50)], (0.55, 0.35, 0.2, 1))
        self._poly(c, [(bx - 40, 80), (bx + 40, 80), (bx + 31, 52), (bx - 31, 52)], (0.75, 0.52, 0.30, 1))
        for k in range(-3, 4):
            self._line(c, (bx + k * 11, 80), (bx + k * 9, 52), 1.5, (0.5, 0.3, 0.15, 1))
        self.anim['x'] = bx
        self.anim['tx'] = bx
        self._draw_pet(c, bx, 78, 0.55, False)
        for x, y, t, kind in m['fx']:
            self._text(c, "+1" if kind == 'plus' else "OUCH", x, y + 16 + 20 * t, 12, (1, 1, 0.4, 1 - 2 * t) if kind == 'plus' else (1, 0.4, 0.3, 1 - 2 * t))
        self._text(c, "SCORE %d" % m['score'], 20, 380, 16, ov.C_DARK, 'left')
        self._text(c, "TIME %d" % math.ceil(m['t']), W - 20, 380, 16, ov.C_DARK, 'right')
        for i in range(3):
            self._circ(c, W / 2 - 20 + i * 20, 382, 7, (0.9, 0.2, 0.3, 1) if i < m['lives'] else (0, 0, 0, 0.25), 10)
        self._text(c, "Catch the fruit, avoid the bombs (move the mouse)", W / 2, 360, 11, ov.C_DARK)

    def _tap(self, c):
        m = self.mini
        self._rect(c, 0, 0, W, H, (0.12, 0.13, 0.24, 1))
        for k in range(8):
            self._rect(c, 0, k * 50, W, 24, (1, 1, 1, 0.02))
        for tg in m['targets']:
            k = tg['t'] / tg['life']
            r = 26.0 * (1.0 - 0.35 * k)
            col = _hsv(tg['hue'], 0.6, 1.0)
            self._circ(c, tg['x'], tg['y'], r + 6, (col[0], col[1], col[2], 0.25), 22)
            self._circ(c, tg['x'], tg['y'], r, col, 22)
            self._circ(c, tg['x'], tg['y'], r * 0.55, (1, 1, 1, 0.85), 18)
            c.ring(self._X(tg['x']), self._Y(tg['y']), (r + 10 * (1 - k)) * self.sc, 2 * self.sc, (1, 1, 1, 0.6), 22)
        for x, y, t, txt in m['fx']:
            self._text(c, txt, x, y + 20 * t * 2, 16, (1, 1, 0.4, 1 - 2 * t))
        self._text(c, "SCORE %d" % m['score'], 20, 380, 16, ov.C_GOLD, 'left')
        self._text(c, "TIME %d" % math.ceil(m['t']), W - 20, 380, 16, ov.C_WHITE, 'right')
        self._text(c, "Hit the glowing targets - fast hits count double", W / 2, 24, 11, ov.C_TEXT)

    def _mini_result(self, c):
        m = self.mini
        r = m['result']
        self._rect(c, 0, 0, W, H, (0, 0, 0, 0.65))
        self._text(c, "TIME'S UP!" if self.view == 'tap' or m.get('lives', 1) > 0 else "BOOM!", W / 2, 300, 36, ov.C_GOLD)
        self._text(c, "Score  %d%s" % (r['score'], "   NEW BEST!" if r['new'] else ""), W / 2, 256, 24, ov.C_WHITE)
        self._text(c, "+%d coins    pet is happier" % r['coins'], W / 2, 224, 14, ov.C_GOLD)
        self._text(c, "Click or SPACE to go back", W / 2, 170, 12, ov.C_TEXT)

    def draw(self, c):
        self.draw_frame(c)
        s = self.s
        if self.view == 'title':
            self.draw_header(c, "POCKET PET", "A virtual pet that lives while you play - and while you're away", ov.C_GOLD)
            self._title(c)
            if self.msg_t > 0:
                self._text(c, self.msg, W / 2, 28, 11, ov.C_WHITE)
            self.draw_hint(c, "Click a button   SPACE continue   ESC quit")
            return
        if self.view == 'catch':
            self.draw_header(c, "FRUIT CATCH", "Best %d" % self.s['mini']['catch'], ov.C_GOLD)
            self._catch(c)
            if self.mini['over']:
                self._mini_result(c)
            self.draw_hint(c, "Mouse or arrow keys: move the basket   M: title")
            return
        if self.view == 'tap':
            self.draw_header(c, "REFLEX TAP", "Best %d" % self.s['mini']['tap'], ov.C_GOLD)
            self._tap(c)
            if self.mini['over']:
                self._mini_result(c)
            self.draw_hint(c, "Click the targets before they shrink   M: title")
            return
        self.draw_header(c, "POCKET PET - %s" % s['name'],
                         "%s%s   |   Best care score %d" % (self.stage().capitalize(), "  (asleep)" if s['asleep'] else "", self._best('life')),
                         ov.C_GOLD)
        self._home(c)
        self.draw_hint(c, "F feed  P play  L lights  C clean  H heal  S shop   click the pet to pet it   M title")


class _PetRunner(ov.Runner):
    """Saves the pet when the game is quit."""

    def _teardown(self):
        g = self.game
        if g is not None and hasattr(g, 'close'):
            try:
                g.close()
            except Exception:
                pass
        super()._teardown()


RUNNER = _PetRunner("pocketpet", GAME_NAME, PocketPet, [
    "Feed, play, clean, put to bed, heal",
    "Click the pet to pet it; click poops to clean",
    "Mini games: Fruit Catch, Reflex Tap",
    "The pet is saved and keeps living while you're away",
    "Records: best care score + mini game scores",
    "M: title screen   ESC: quit (auto-saves)",
])


def draw_ui(layout, context):
    RUNNER.draw_ui(layout)


def register():
    RUNNER.register()


def unregister():
    RUNNER.unregister()
