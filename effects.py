"""Движок эффектов: слои (как в NGENUITY), смешивание, реактивные эффекты по нажатиям."""
import colorsys
import glob
import json
import math
import os
import random
import re
import select
import threading
import time
import uuid

from hyper_ctl import lamp_groups, parse_color, resolve_keys

CONFIG_DIR = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "hyper_ctl")
PROFILE_PATH = os.path.join(CONFIG_DIR, "profile.json")
PRESETS_DIR = os.path.join(CONFIG_DIR, "profiles")

U = 19050  # ширина стандартной клавиши, мкм
FULL_RESEND_SEC = 2.0


def hex_to_rgb(h):
    r, g, b = parse_color(h)
    return r / 255, g / 255, b / 255


def rgb_to_hex(c):
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(v * 255))) for v in c)


def hsv(h, s=1.0, v=1.0):
    return colorsys.hsv_to_rgb(h % 1.0, s, v)


def lerp(a, b, k):
    return tuple(x + (y - x) * k for x, y in zip(a, b))


def palette(colors, k):
    """Плавный цикл по списку цветов, k в [0, 1)."""
    if len(colors) == 1:
        return colors[0]
    k = (k % 1.0) * len(colors)
    i = int(k)
    return lerp(colors[i], colors[(i + 1) % len(colors)], k - i)


def scale(colors, k):
    """Линейная шкала по списку цветов (без зацикливания), k в [0, 1]."""
    if len(colors) == 1:
        return colors[0]
    k = max(0.0, min(1.0, k)) * (len(colors) - 1)
    i = min(int(k), len(colors) - 2)
    return lerp(colors[i], colors[i + 1], k - i)


# ---------- эффекты ----------
# Каждый эффект описывает свои параметры в SCHEMA, по ней UI сам строит форму.

EFFECTS = {}


def effect(cls):
    EFFECTS[cls.ID] = cls
    return cls


class Effect:
    ID = ""
    TITLE = ""
    REACTIVE = False
    SCHEMA = []

    def __init__(self, engine):
        self.engine = engine
        self.cfg = {}

    def p(self, name):
        val = self.cfg.get("params", {}).get(name)
        if val is None:
            val = next(s["default"] for s in self.SCHEMA if s["name"] == name)
        return val

    def colors(self, name="colors"):
        return [hex_to_rgb(c) for c in self.p(name)] or [(1, 1, 1)]

    def on_key(self, lamp_id, t):
        pass

    def render(self, t, dt, ids):
        """-> dict lamp_id -> (r, g, b, alpha), все значения 0..1."""
        return {}


SPEED = {"name": "speed", "label": "Скорость", "type": "range", "min": 0.05, "max": 5, "step": 0.05, "default": 1}
DIRECTION = {"name": "direction", "label": "Направление", "type": "select", "default": "right",
             "options": [["right", "→ вправо"], ["left", "← влево"], ["down", "↓ вниз"], ["up", "↑ вверх"],
                         ["out", "◎ от центра"], ["in", "◉ к центру"], ["diag", "↘ по диагонали"]]}


@effect
class Paint(Effect):
    ID, TITLE = "paint", "Ручная раскраска"
    SCHEMA = [{"name": "colors", "type": "keymap", "default": {}}]

    def render(self, t, dt, ids):
        out = {}
        lamps = self.engine.by_name
        for name, c in self.p("colors").items():
            if name in lamps:
                out[lamps[name]] = (*hex_to_rgb(c), 1.0)
        return out


@effect
class Solid(Effect):
    ID, TITLE = "solid", "Сплошной цвет"
    SCHEMA = [{"name": "color", "label": "Цвет", "type": "color", "default": "#ff0040"}]

    def render(self, t, dt, ids):
        c = hex_to_rgb(self.p("color"))
        return {i: (*c, 1.0) for i in ids}


@effect
class Breathing(Effect):
    ID, TITLE = "breathing", "Дыхание"
    SCHEMA = [{"name": "colors", "label": "Цвета (по очереди)", "type": "colors", "default": ["#ff0040", "#0080ff"]},
              {**SPEED, "default": 0.5},
              {"name": "min", "label": "Минимум яркости", "type": "range", "min": 0, "max": 1, "step": 0.05, "default": 0}]

    def render(self, t, dt, ids):
        phase = t * self.p("speed") * 0.5
        cols = self.colors()
        c = cols[int(phase) % len(cols)]
        lo = self.p("min")
        k = lo + (1 - lo) * (1 - math.cos((phase % 1) * 2 * math.pi)) / 2
        return {i: (*c, k) for i in ids}


@effect
class Spectrum(Effect):
    ID, TITLE = "spectrum", "Смена цветов"
    SCHEMA = [{**SPEED, "default": 0.3},
              {"name": "colors", "label": "Цвета (пусто = радуга)", "type": "colors", "default": []}]

    def render(self, t, dt, ids):
        k = t * self.p("speed") * 0.2
        c = palette(self.colors(), k) if self.p("colors") else hsv(k)
        return {i: (*c, 1.0) for i in ids}


class Directional(Effect):
    def coord(self, lamp):
        e = self.engine
        x, y = (lamp.x - e.cx) / U, (lamp.y - e.cy) / U
        d = self.p("direction")
        return {"right": -x, "left": x, "down": -y, "up": y, "diag": -(x + y) * 0.7,
                "out": -math.hypot(x, y), "in": math.hypot(x, y)}[d]


@effect
class Wave(Directional):
    ID, TITLE = "wave", "Радужная волна"
    SCHEMA = [DIRECTION, SPEED,
              {"name": "scale", "label": "Длина волны (клавиш)", "type": "range", "min": 2, "max": 40, "step": 1, "default": 16},
              {"name": "saturation", "label": "Насыщенность", "type": "range", "min": 0, "max": 1, "step": 0.05, "default": 1}]

    def render(self, t, dt, ids):
        lamps, scale, sat = self.engine.lamp_by_id, self.p("scale"), self.p("saturation")
        shift = t * self.p("speed") * 0.25
        return {i: (*hsv(self.coord(lamps[i]) / scale + shift, sat), 1.0) for i in ids}


@effect
class Gradient(Directional):
    ID, TITLE = "gradient", "Градиент / цветная волна"
    SCHEMA = [{"name": "colors", "label": "Цвета", "type": "colors", "default": ["#ff0080", "#7000ff", "#00c8ff"]},
              DIRECTION, {**SPEED, "min": 0, "default": 0.5},
              {"name": "scale", "label": "Длина (клавиш)", "type": "range", "min": 2, "max": 40, "step": 1, "default": 18}]

    def render(self, t, dt, ids):
        lamps, scale, cols = self.engine.lamp_by_id, self.p("scale"), self.colors()
        shift = t * self.p("speed") * 0.25
        return {i: (*palette(cols, self.coord(lamps[i]) / scale + shift), 1.0) for i in ids}


@effect
class Starlight(Effect):
    ID, TITLE = "starlight", "Звёздное небо"
    SCHEMA = [{"name": "colors", "label": "Цвета (пусто = случайные)", "type": "colors", "default": ["#ffffff", "#80c0ff"]},
              {"name": "density", "label": "Плотность", "type": "range", "min": 0.1, "max": 10, "step": 0.1, "default": 3},
              {"name": "fade", "label": "Длительность вспышки, с", "type": "range", "min": 0.2, "max": 5, "step": 0.1, "default": 1.5}]

    def __init__(self, engine):
        super().__init__(engine)
        self.stars = {}  # lamp_id -> (color, birth)

    def render(self, t, dt, ids):
        fade = self.p("fade")
        if ids and random.random() < self.p("density") * dt * len(ids) / 20:
            c = random.choice(self.colors()) if self.p("colors") else hsv(random.random())
            self.stars[random.choice(ids)] = (c, t)
        out = {}
        for i, (c, birth) in list(self.stars.items()):
            age = (t - birth) / fade
            if age >= 1:
                del self.stars[i]
            else:
                out[i] = (*c, math.sin(age * math.pi))
        return out


@effect
class Rain(Effect):
    ID, TITLE = "rain", "Дождь"
    SCHEMA = [{"name": "colors", "label": "Цвета", "type": "colors", "default": ["#00ff60"]},
              {**SPEED, "default": 1},
              {"name": "density", "label": "Плотность", "type": "range", "min": 0.2, "max": 10, "step": 0.1, "default": 2},
              {"name": "trail", "label": "Хвост (клавиш)", "type": "range", "min": 0.5, "max": 6, "step": 0.5, "default": 2.5}]

    def __init__(self, engine):
        super().__init__(engine)
        self.drops = []  # [x, y, color]

    def render(self, t, dt, ids):
        e, lamps = self.engine, self.engine.lamp_by_id
        if ids and random.random() < self.p("density") * dt * 3:
            self.drops.append([lamps[random.choice(ids)].x, e.min_y - U, random.choice(self.colors())])
        speed, trail = self.p("speed") * 6 * U, self.p("trail") * U
        for d in self.drops:
            d[1] += speed * dt
        self.drops = [d for d in self.drops if d[1] - trail < e.max_y + U]
        out = {}
        for i in ids:
            l = lamps[i]
            best = 0, None
            for x, y, c in self.drops:
                if abs(l.x - x) < U * 0.6 and l.y <= y + U / 2:
                    k = 1 - (y - l.y) / trail
                    if k > best[0]:
                        best = k, c
            if best[1]:
                out[i] = (*best[1], min(1, best[0]))
        return out


@effect
class Fire(Effect):
    ID, TITLE = "fire", "Огонь"
    SCHEMA = [{"name": "colors", "label": "Цвета (снизу вверх)", "type": "colors", "default": ["#ff2000", "#ff8000", "#ffd040"]},
              {**SPEED, "default": 1},
              {"name": "height", "label": "Высота пламени", "type": "range", "min": 0.2, "max": 2, "step": 0.05, "default": 1}]

    def render(self, t, dt, ids):
        e, lamps, cols = self.engine, self.engine.lamp_by_id, self.colors()
        s, h = t * self.p("speed") * 3, self.p("height")
        span = max(e.max_y - e.min_y, 1)
        out = {}
        for i in ids:
            l = lamps[i]
            up = (e.max_y - l.y) / span  # 0 снизу, 1 сверху
            n = (math.sin(l.x / U * 1.7 + s * 1.3) + math.sin(l.x / U * 0.6 - s * 2.1) + math.sin(s * 3.7 + l.x / U)) / 6 + 0.5
            k = max(0.0, min(1.0, n * h * 1.6 - up))
            out[i] = (*scale(cols, k), min(1.0, k * 1.5))
        return out


@effect
class Reactive(Effect):
    ID, TITLE, REACTIVE = "reactive", "Подсветка нажатий", True
    SCHEMA = [{"name": "colors", "label": "Цвета (пусто = случайные)", "type": "colors", "default": ["#ffffff"]},
              {"name": "fade", "label": "Затухание, с", "type": "range", "min": 0.1, "max": 5, "step": 0.1, "default": 0.8}]

    def __init__(self, engine):
        super().__init__(engine)
        self.hits = {}

    def on_key(self, lamp_id, t):
        c = random.choice(self.colors()) if self.p("colors") else hsv(random.random())
        self.hits[lamp_id] = (c, t)

    def render(self, t, dt, ids):
        fade, allowed, out = self.p("fade"), set(ids), {}
        for i, (c, t0) in list(self.hits.items()):
            k = 1 - (t - t0) / fade
            if k <= 0:
                del self.hits[i]
            elif i in allowed:
                out[i] = (*c, k * k)
        return out


@effect
class Ripple(Effect):
    ID, TITLE, REACTIVE = "ripple", "Волна от нажатия", True
    SCHEMA = [{"name": "colors", "label": "Цвета (пусто = радуга)", "type": "colors", "default": ["#00e5ff"]},
              {**SPEED, "default": 1},
              {"name": "width", "label": "Ширина кольца (клавиш)", "type": "range", "min": 0.5, "max": 4, "step": 0.25, "default": 1.25},
              {"name": "radius", "label": "Радиус (клавиш)", "type": "range", "min": 2, "max": 20, "step": 0.5, "default": 8}]

    def __init__(self, engine):
        super().__init__(engine)
        self.rings = []  # (x, y, t0, color)

    def on_key(self, lamp_id, t):
        l = self.engine.lamp_by_id[lamp_id]
        c = random.choice(self.colors()) if self.p("colors") else None
        self.rings.append((l.x, l.y, t, c))

    def render(self, t, dt, ids):
        lamps, speed = self.engine.lamp_by_id, self.p("speed") * 12
        width, radius = self.p("width"), self.p("radius")
        self.rings = [r for r in self.rings if (t - r[2]) * speed < radius + width]
        out = {}
        for i in ids:
            l = lamps[i]
            best = None
            for x, y, t0, c in self.rings:
                r = (t - t0) * speed
                d = math.hypot(l.x - x, l.y - y) / U
                k = (1 - abs(d - r) / width) * (1 - r / (radius + width))
                if k > 0 and (best is None or k > best[3]):
                    best = (*(c or hsv(d / 10 - t * 0.2)), k)
            if best:
                out[i] = best
        return out


@effect
class Heatmap(Effect):
    ID, TITLE, REACTIVE = "heatmap", "Тепловая карта набора", True
    SCHEMA = [{"name": "colors", "label": "Шкала (холодно → горячо)", "type": "colors",
               "default": ["#0010ff", "#00ffa0", "#ffff00", "#ff0000"]},
              {"name": "decay", "label": "Остывание, с", "type": "range", "min": 2, "max": 120, "step": 1, "default": 20}]

    def __init__(self, engine):
        super().__init__(engine)
        self.heat = {}

    def on_key(self, lamp_id, t):
        self.heat[lamp_id] = min(1.0, self.heat.get(lamp_id, 0) + 0.15)

    def render(self, t, dt, ids):
        k = math.exp(-dt / self.p("decay") * 3)
        cols = self.colors()
        out = {}
        for i in ids:
            h = self.heat.get(i, 0) * k
            self.heat[i] = h
            out[i] = (*scale(cols, h), 1.0)
        return out


# ---------- профиль ----------

def new_layer(effect_id, **kw):
    return {"id": uuid.uuid4().hex[:8], "effect": effect_id, "enabled": True, "keys": ["all"],
            "opacity": 1.0, "blend": "normal", "params": {}, **kw}


def default_profile():
    return {"mode": "custom", "brightness": 1.0, "layers": [
        new_layer("wave"),
        new_layer("paint"),
        new_layer("reactive", blend="add"),
    ]}


def load_profile(path=None):
    try:
        with open(path or PROFILE_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return default_profile()


def save_profile(profile, path=None):
    path = path or PROFILE_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(profile, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def effects_catalog():
    return [{"id": c.ID, "title": c.TITLE, "reactive": c.REACTIVE, "schema": c.SCHEMA} for c in EFFECTS.values()]


# ---------- чтение нажатий ----------

class KeyListener(threading.Thread):
    """Читает нажатия напрямую с hidraw клавиатуры (не перехватывая ввод у системы)."""

    def __init__(self, on_press):
        super().__init__(daemon=True)
        self.on_press = on_press
        self.fds = {}  # path -> (fd, kind, bitmap_rid)
        self.state = {}

    @staticmethod
    def _usb_root(hidraw):
        # .../1-3/1-3:1.3/0003:03F0:0EB8.002A -> .../1-3
        return os.path.dirname(os.path.dirname(os.path.realpath(f"/sys/class/hidraw/{hidraw}/device")))

    def _scan(self):
        roots = set()
        for node in glob.glob("/sys/class/hidraw/hidraw*"):
            try:
                with open(f"{node}/device/report_descriptor", "rb") as f:
                    if f.read(4) == bytes.fromhex("05590901"):
                        roots.add(self._usb_root(os.path.basename(node)))
            except OSError:
                pass
        for node in glob.glob("/sys/class/hidraw/hidraw*"):
            name = os.path.basename(node)
            path = "/dev/" + name
            if path in self.fds or self._usb_root(name) not in roots:
                continue
            try:
                with open(f"{node}/device/report_descriptor", "rb") as f:
                    desc = f.read()
            except OSError:
                continue
            m = re.search(rb"\x85(.)\x05\x07\x15\x00\x25\x01\x19\x04", desc, re.S)
            if desc.startswith(bytes.fromhex("05010906")) and b"\x85" not in desc:
                kind, rid = "boot", None
            elif m:
                kind, rid = "bitmap", m.group(1)[0]
            else:
                continue
            try:
                self.fds[path] = (os.open(path, os.O_RDONLY | os.O_NONBLOCK), kind, rid)
            except OSError:
                pass

    def _keys(self, data, kind, rid):
        if kind == "boot" and len(data) >= 8:
            keys = {0xE0 + b for b in range(8) if data[0] >> b & 1}
            return keys | {k for k in data[2:8] if k > 3}
        if kind == "bitmap" and data and data[0] == rid:
            return {0x04 + i for i in range((len(data) - 1) * 8) if data[1 + i // 8] >> (i % 8) & 1}
        return None

    def run(self):
        last_scan = 0
        while True:
            if time.monotonic() - last_scan > 3:
                self._scan()
                last_scan = time.monotonic()
            if not self.fds:
                time.sleep(1)
                continue
            by_fd = {v[0]: (p, v) for p, v in self.fds.items()}
            ready, _, _ = select.select(list(by_fd), [], [], 1.0)
            for fd in ready:
                path, (_, kind, rid) = by_fd[fd]
                try:
                    data = os.read(fd, 64)
                except BlockingIOError:
                    continue
                except OSError:
                    os.close(fd)
                    del self.fds[path]
                    continue
                keys = self._keys(data, kind, rid)
                if keys is None:
                    continue
                prev = self.state.get(path, set())
                self.state[path] = keys
                for k in keys - prev:
                    self.on_press(k)


# ---------- движок ----------

class Engine:
    def __init__(self, kb, profile, listen_keys=True):
        self.kb = kb
        self.lock = threading.Lock()
        self.profile = profile
        self.instances = {}
        self.events = []
        self.frame = {}
        self.sent = {}
        self.last_full = 0
        self._layout()
        if listen_keys:
            self.listener = KeyListener(self._on_usage)
            self.listener.start()

    def _layout(self):
        lamps = self.kb.lamps
        self.lamp_by_id = {l.id: l for l in lamps}
        self.by_name = {l.name: l.id for l in lamps}
        self.by_usage = {}
        for l in lamps:
            if l.binding:
                self.by_usage.setdefault(l.binding, []).append(l.id)
        self.groups = lamp_groups(lamps)
        self.cx = (min(l.x for l in lamps) + max(l.x for l in lamps)) / 2
        self.cy = (min(l.y for l in lamps) + max(l.y for l in lamps)) / 2
        self.min_y, self.max_y = min(l.y for l in lamps), max(l.y for l in lamps)

    def _on_usage(self, usage):
        t = time.monotonic()
        with self.lock:
            for lid in self.by_usage.get(usage, []):
                self.events.append((lid, t))

    def set_profile(self, profile):
        with self.lock:
            self.profile = profile

    def _ids(self, keys):
        try:
            return resolve_keys(self.kb.lamps, keys)
        except KeyError:
            return []

    def render(self, t, dt):
        with self.lock:
            profile, events, self.events = self.profile, self.events, []
            layers = [l for l in profile.get("layers", []) if l.get("enabled", True) and l.get("effect") in EFFECTS]
            live = set()
            for cfg in layers:
                inst = self.instances.get(cfg["id"])
                if not inst or inst.ID != cfg["effect"]:
                    inst = self.instances[cfg["id"]] = EFFECTS[cfg["effect"]](self)
                inst.cfg = cfg
                live.add(cfg["id"])
            for k in set(self.instances) - live:
                del self.instances[k]
        out = {i: [0.0, 0.0, 0.0] for i in self.lamp_by_id}
        for cfg in layers:
            inst = self.instances[cfg["id"]]
            for lid, et in events:
                inst.on_key(lid, et)
            op, blend = float(cfg.get("opacity", 1)), cfg.get("blend", "normal")
            for i, (r, g, b, a) in inst.render(t, dt, self._ids(cfg.get("keys", ["all"]))).items():
                if i not in out:
                    continue
                a *= op
                o = out[i]
                if blend == "add":
                    o[0], o[1], o[2] = min(1, o[0] + r * a), min(1, o[1] + g * a), min(1, o[2] + b * a)
                elif blend == "multiply":
                    o[0], o[1], o[2] = (o[0] * (1 - a + r * a), o[1] * (1 - a + g * a), o[2] * (1 - a + b * a))
                else:
                    o[0], o[1], o[2] = (o[0] + (r - o[0]) * a, o[1] + (g - o[1]) * a, o[2] + (b - o[2]) * a)
        br = float(profile.get("brightness", 1))
        return {i: tuple(max(0, min(255, round(v * br * 255))) for v in c) for i, c in out.items()}

    def step(self, t, dt):
        if self.kb.rescan():
            self.last_full = 0
        if self.profile.get("mode") == "hardware":
            if self.sent is not None:
                self.kb.set_autonomous(True)
                self.sent = None
            return
        frame = self.render(t, dt)
        self.frame = frame
        now = time.monotonic()
        if self.sent is None or now - self.last_full > FULL_RESEND_SEC:
            changed, self.last_full = frame, now
        else:
            changed = {i: c for i, c in frame.items() if self.sent.get(i) != c}
        if changed:
            self.kb.set_colors(changed)
        self.sent = frame

    def run(self, stop=None):
        prev = time.monotonic()
        while not (stop and stop.is_set()):
            t = time.monotonic()
            self.step(t, min(t - prev, 0.2))
            prev = t
            time.sleep(max(0.0, self.kb.interval - (time.monotonic() - t)))
