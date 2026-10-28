#!/usr/bin/env python3
"""hyper_ctl — управление RGB-подсветкой клавиатур с HID LampArray (HyperX Alloy Rise 75 и др.).

Без зависимостей: работает напрямую с /dev/hidrawN через ioctl.
"""
import argparse
import fcntl
import glob
import os
import signal
import struct
import sys
import time

LAMPARRAY_DESC_PREFIX = bytes.fromhex("05590901")  # Usage Page (Lighting), Usage (LampArray)

# Report IDs из дескриптора клавиатуры
RID_ARRAY_ATTRS = 1
RID_LAMP_REQ = 2
RID_LAMP_RESP = 3
RID_MULTI_UPDATE = 4
RID_RANGE_UPDATE = 5
RID_CONTROL = 6

MULTI_MAX = 8
FLAG_UPDATE_COMPLETE = 1
MAX_SANE_LAMPS = 512


def _ioc(direction, nr, size):
    return (direction << 30) | (size << 16) | (ord("H") << 8) | nr


def HIDIOCSFEATURE(size):
    return _ioc(3, 0x06, size)


def HIDIOCGFEATURE(size):
    return _ioc(3, 0x07, size)


# HID Keyboard usage page (0x07) -> имя клавиши
KEY_NAMES = {}
for i, c in enumerate("abcdefghijklmnopqrstuvwxyz"):
    KEY_NAMES[0x04 + i] = c
for i, c in enumerate("1234567890"):
    KEY_NAMES[0x1E + i] = c
for i in range(12):
    KEY_NAMES[0x3A + i] = f"f{i + 1}"
    KEY_NAMES[0x68 + i] = f"f{i + 13}"
KEY_NAMES.update({
    0x28: "enter", 0x29: "esc", 0x2A: "backspace", 0x2B: "tab", 0x2C: "space",
    0x2D: "minus", 0x2E: "equal", 0x2F: "lbracket", 0x30: "rbracket", 0x31: "backslash",
    0x32: "hash", 0x33: "semicolon", 0x34: "quote", 0x35: "grave", 0x36: "comma",
    0x37: "dot", 0x38: "slash", 0x39: "capslock", 0x46: "printscreen", 0x47: "scrolllock",
    0x48: "pause", 0x49: "insert", 0x4A: "home", 0x4B: "pageup", 0x4C: "delete",
    0x4D: "end", 0x4E: "pagedown", 0x4F: "right", 0x50: "left", 0x51: "down", 0x52: "up",
    0x64: "nonus_backslash", 0x65: "menu",
    0xE0: "lctrl", 0xE1: "lshift", 0xE2: "lalt", 0xE3: "lmeta",
    0xE4: "rctrl", 0xE5: "rshift", 0xE6: "ralt", 0xE7: "rmeta",
})

# Группы клавиш для удобства (lbar/rbar/bars/keys вычисляются по раскладке)
GROUPS = {
    "letters": [chr(c) for c in range(ord("a"), ord("z") + 1)],
    "digits": list("1234567890"),
    "fkeys": [f"f{i}" for i in range(1, 13)],
    "arrows": ["up", "down", "left", "right"],
    "wasd": list("wasd"),
    "mods": ["lctrl", "lshift", "lalt", "lmeta", "rctrl", "rshift", "ralt", "rmeta", "fn"],
}


def parse_color(s):
    s = s.strip().lstrip("#")
    if len(s) != 6:
        raise ValueError(f"цвет должен быть в формате RRGGBB: {s!r}")
    return tuple(int(s[i:i + 2], 16) for i in (0, 2, 4))


def find_devices():
    found = []
    for node in sorted(glob.glob("/sys/class/hidraw/hidraw*"), key=lambda p: int(p.rsplit("hidraw", 1)[1])):
        try:
            with open(f"{node}/device/report_descriptor", "rb") as f:
                if f.read(4) == LAMPARRAY_DESC_PREFIX:
                    found.append("/dev/" + os.path.basename(node))
        except OSError:
            pass
    return found


class Lamp:
    __slots__ = ("id", "x", "y", "programmable", "binding", "name")

    def __init__(self, id, x, y, programmable, binding):
        self.id, self.x, self.y = id, x, y
        self.programmable, self.binding = programmable, binding
        self.name = KEY_NAMES.get(binding, f"lamp{id}")


def name_lamps(lamps):
    """Даёт имена лампам без кода клавиши: боковые полосы, Fn, дубли пробела."""
    keys = [l for l in lamps if l.binding in KEY_NAMES]
    if not keys:
        return
    min_x, max_x = min(l.x for l in keys), max(l.x for l in keys)
    bottom_y = max(l.y for l in keys)
    for side, test in (("lbar", lambda l: l.x < min_x), ("rbar", lambda l: l.x > max_x)):
        bar = sorted((l for l in lamps if l.binding not in KEY_NAMES and test(l)), key=lambda l: -l.y)
        for i, l in enumerate(bar, 1):
            l.name = f"{side}{i}"
    for l in lamps:
        if l.name.startswith("lamp") and abs(l.y - bottom_y) < 3000:
            l.name = "fn"
    seen = {}
    for l in lamps:
        n = seen.get(l.name, 0)
        seen[l.name] = n + 1
        if n:
            l.name = f"{l.name}{n + 1}"


class LampArray:
    """Одно LampArray-устройство (один /dev/hidrawN)."""

    def __init__(self, path):
        self.path = path
        self.fd = os.open(path, os.O_RDWR)
        try:
            self.count, self.width, self.height, self.interval = self._array_attrs()
            self._lamps = None
        except Exception:
            os.close(self.fd)
            raise

    def close(self):
        os.close(self.fd)

    def _get_feature(self, rid, size):
        buf = bytearray(size + 1)
        buf[0] = rid
        fcntl.ioctl(self.fd, HIDIOCGFEATURE(len(buf)), buf)
        return bytes(buf[1:])

    def _set_feature(self, rid, payload):
        buf = bytearray([rid]) + payload
        fcntl.ioctl(self.fd, HIDIOCSFEATURE(len(buf)), buf)

    def _array_attrs(self):
        # первый ответ иногда мусорный — ждём два одинаковых подряд
        prev = None
        for _ in range(5):
            count, w, h, _d, _kind, interval = struct.unpack("<H5I", self._get_feature(RID_ARRAY_ATTRS, 22))
            cur = (count, w, h, interval)
            if cur == prev and 0 < count <= MAX_SANE_LAMPS:
                return cur
            prev = cur
        raise OSError(f"{self.path}: некорректный ответ LampArrayAttributes {prev}")

    @property
    def lamps(self):
        if self._lamps is None:
            lamps = []
            for i in range(self.count):
                self._set_feature(RID_LAMP_REQ, struct.pack("<H", i))
                data = self._get_feature(RID_LAMP_RESP, 28)
                lid, x, y, _z, _lat, _purp = struct.unpack_from("<H5I", data)
                if lid != i:
                    # донгл при подключённом кабеле завышает LampCount и дальше отвечает мусором
                    break
                _r, _g, _b, _i, prog, binding = data[22:28]
                lamps.append(Lamp(lid, x, y, bool(prog), binding))
            if not lamps:
                raise OSError(f"{self.path}: не удалось прочитать раскладку")
            name_lamps(lamps)
            self.count = len(lamps)
            self._lamps = lamps
        return self._lamps

    def set_autonomous(self, enabled):
        self._set_feature(RID_CONTROL, bytes([1 if enabled else 0]))

    def fill(self, rgb):
        self._set_feature(RID_RANGE_UPDATE, struct.pack(
            "<BHH4B", FLAG_UPDATE_COMPLETE, 0, self.count - 1, *rgb, 0xFF))

    def set_colors(self, colors):
        """colors: dict lamp_id -> (r, g, b)."""
        items = list(colors.items())
        for start in range(0, len(items), MULTI_MAX):
            chunk = items[start:start + MULTI_MAX]
            last = start + MULTI_MAX >= len(items)
            ids = [lid for lid, _ in chunk] + [0] * (MULTI_MAX - len(chunk))
            rgbi = b"".join(bytes([*rgb, 0xFF]) for _, rgb in chunk) + bytes(4 * (MULTI_MAX - len(chunk)))
            self._set_feature(RID_MULTI_UPDATE, struct.pack(
                "<BB8H", len(chunk), FLAG_UPDATE_COMPLETE if last else 0, *ids) + rgbi)


class Keyboard:
    """Все подключения клавиатуры сразу (провод + донгл), с автопереподключением.

    Команды уходят на каждое доступное LampArray-устройство: при подключённом
    кабеле донгл их просто игнорирует, а при работе по радио — передаёт.
    """

    RESCAN_SEC = 3

    def __init__(self, path=None):
        self.fixed_path = path
        self.devices = {}
        self.lamps = []
        self.count = 0
        self.interval = 1 / 30
        self._last_scan = 0
        self.rescan(force=True)
        if self.devices and not self.lamps:
            sys.exit(f"Не удалось прочитать раскладку ни с одного устройства: {self.path}")
        if not self.devices:
            found = [path] if path else find_devices()
            if not found:
                sys.exit("LampArray-устройство не найдено")
            sys.exit(f"Нет доступа к {', '.join(found)}. Установи udev-правило 70-hyperx.rules или запусти через sudo.")

    @property
    def path(self):
        return ", ".join(self.devices) or "нет устройства"

    def rescan(self, force=False):
        """Подключает новые устройства. Возвращает True, если появилось новое."""
        now = time.monotonic()
        if not force and now - self._last_scan < self.RESCAN_SEC:
            return False
        self._last_scan = now
        added = False
        for p in [self.fixed_path] if self.fixed_path else find_devices():
            if p in self.devices or not os.access(p, os.R_OK | os.W_OK):
                continue
            try:
                self.devices[p] = LampArray(p)
                added = True
            except OSError:
                pass
        if not self.lamps:
            for dev in self.devices.values():
                try:
                    self.lamps, self.count = dev.lamps, dev.count
                    self.interval = max(dev.interval / 1e6, 1 / 60)
                    break
                except OSError:
                    pass
        return added

    def _each(self, fn):
        for p, dev in list(self.devices.items()):
            try:
                fn(dev)
            except OSError:
                dev.close()
                del self.devices[p]

    def set_autonomous(self, enabled):
        self._each(lambda d: d.set_autonomous(enabled))

    def fill(self, rgb):
        self._each(lambda d: (d.set_autonomous(False), d.fill(rgb)))

    def set_colors(self, colors):
        self._each(lambda d: (d.set_autonomous(False), d.set_colors(colors)))

    def lamp_ids(self, names):
        return resolve_keys(self.lamps, names)


def lamp_groups(lamps):
    by_name = {l.name: l.id for l in lamps}
    groups = {g: [by_name[k] for k in keys if k in by_name] for g, keys in GROUPS.items()}
    groups["all"] = [l.id for l in lamps]
    groups["lbar"] = [l.id for l in lamps if l.name.startswith("lbar")]
    groups["rbar"] = [l.id for l in lamps if l.name.startswith("rbar")]
    groups["bars"] = groups["lbar"] + groups["rbar"]
    groups["keys"] = [l.id for l in lamps if l.id not in groups["bars"]]
    groups["space"] = [l.id for l in lamps if l.name.startswith("space")]
    return groups


def resolve_keys(lamps, names):
    """Имена клавиш/групп/lampN -> список lamp id."""
    by_name = {l.name: l.id for l in lamps}
    groups = lamp_groups(lamps)
    ids = []
    for n in names:
        n = n.lower()
        if n in groups:
            ids += groups[n]
        elif n in by_name:
            ids.append(by_name[n])
        elif n.startswith("lamp") and n[4:].isdigit():
            ids.append(int(n[4:]))
        else:
            raise KeyError(f"Неизвестная клавиша: {n!r} (список: hyper_ctl.py info)")
    return ids


def cmd_info(kb, args):
    dev = next(iter(kb.devices.values()))
    print(f"Устройства: {kb.path}; ламп: {kb.count}, размер {dev.width / 1000:.0f}x{dev.height / 1000:.0f} мм, "
          f"кадр не чаще {kb.interval * 1000:.0f} мс")
    for l in kb.lamps:
        print(f"  {l.id:3d}  {l.name:16s} x={l.x / 1000:6.1f} y={l.y / 1000:5.1f}"
              + ("" if l.programmable else "  (не программируется)"))


def cmd_color(kb, args):
    kb.fill(parse_color(args.color))


def cmd_off(kb, args):
    kb.fill((0, 0, 0))


def cmd_key(kb, args):
    rgb = parse_color(args.color)
    kb.set_colors({lid: rgb for lid in kb.lamp_ids(args.keys)})


def cmd_auto(kb, args):
    kb.set_autonomous(True)


def cmd_run(kb, args):
    from effects import Engine, load_profile
    engine = Engine(kb, load_profile(args.profile))
    print(f"Играю профиль {args.profile or 'по умолчанию'} на {kb.path} (Ctrl+C для выхода)")
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt))
    try:
        engine.run()
    except KeyboardInterrupt:
        pass
    finally:
        kb.set_autonomous(True)


def main():
    p = argparse.ArgumentParser(description="Управление подсветкой HID LampArray клавиатуры")
    p.add_argument("--device", help="путь к /dev/hidrawN (по умолчанию — все найденные)")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("info", help="показать лампы/клавиши").set_defaults(func=cmd_info)

    s = sub.add_parser("color", help="залить все клавиши одним цветом")
    s.add_argument("color", help="RRGGBB, например ff0000")
    s.set_defaults(func=cmd_color)

    s = sub.add_parser("key", help="покрасить отдельные клавиши")
    s.add_argument("color")
    s.add_argument("keys", nargs="+", help=f"имена клавиш, lampN или группы: {', '.join(GROUPS)}, lbar, rbar, bars, all")
    s.set_defaults(func=cmd_key)

    sub.add_parser("off", help="выключить подсветку").set_defaults(func=cmd_off)
    sub.add_parser("auto", help="вернуть встроенные эффекты клавиатуры").set_defaults(func=cmd_auto)

    s = sub.add_parser("run", help="проигрывать профиль с эффектами (без UI)")
    s.add_argument("profile", nargs="?", help="JSON-профиль (по умолчанию ~/.config/hyper_ctl/profile.json)")
    s.set_defaults(func=cmd_run)

    args = p.parse_args()
    try:
        args.func(Keyboard(args.device), args)
    except KeyError as e:
        sys.exit(e.args[0])


if __name__ == "__main__":
    main()
