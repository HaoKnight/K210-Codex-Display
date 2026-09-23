"""320x240 renderer; no hardware imports, identical operations in browser preview."""
import math
try:
    import ujson as json
except ImportError:
    import json
import time

BG = (10, 17, 22)
CARD = (21, 32, 41)
EDGE = (37, 52, 64)
WHITE = (240, 244, 248)
MUTED = (148, 163, 177)
GREEN = (22, 215, 141)
BLUE = (31, 172, 239)
AMBER = (255, 182, 74)
RED = (239, 68, 68)


class Display:
    def __init__(self, canvas, font_path):
        self.c = canvas
        with open(font_path) as f:
            self.font = json.load(f)

    def text(self, x, y, value, color=WHITE, scale=1, max_width=320):
        # Bitmap glyphs are drawn as horizontal runs, supported by every CanMV.
        start = x
        for char in str(value):
            glyph = self.font.get(char, self.font["?"])
            width, rows = glyph
            if x + width * scale > start + max_width:
                break
            for row, bits in enumerate(rows):
                col = 0
                while col < width:
                    if bits & (1 << col):
                        end = col + 1
                        while end < width and bits & (1 << end):
                            end += 1
                        self.c.draw_rectangle(x + col * scale, y + row * scale,
                                              (end - col) * scale, scale, color=color, fill=True)
                        col = end
                    else:
                        col += 1
            x += (width + 1) * scale

    def text_width(self, value, scale=1):
        return sum((self.font.get(ch, self.font["?"])[0] + 1) * scale
                   for ch in str(value))

    def rect(self, x, y, w, h, color, fill=True):
        self.c.draw_rectangle(x, y, w, h, color=color, fill=fill)

    def temperature_unit(self, x, y):
        """Draw a compact degree-C unit without depending on font glyphs."""
        self.c.draw_circle(x, y, 2, color=WHITE, fill=False)
        self.rect(x + 6, y + 1, 6, 2, WHITE)
        self.rect(x + 5, y + 3, 2, 7, WHITE)
        self.rect(x + 6, y + 10, 6, 2, WHITE)

    def ring(self, x, y, value, color):
        for degree in range(0, 360, 5):
            a, b = math.radians(degree - 90), math.radians(degree - 85)
            ink = color if value is not None and degree < value * 3.6 else EDGE
            self.c.draw_line(int(x + 27 * math.cos(a)), int(y + 27 * math.sin(a)),
                             int(x + 27 * math.cos(b)), int(y + 27 * math.sin(b)),
                             color=ink, thickness=5)
        label = "--" if value is None else str(int(round(value))) + "%"
        width = sum(self.font.get(ch, self.font["?"])[0] + 1 for ch in label)
        scale = 2 if width <= 27 else 1
        self.text(x - width * scale // 2, y - 6 * scale, label, scale=scale)

    def clock(self, hour, minute):
        patterns = ("abcdef", "bc", "abdeg", "abcdg", "bcfg", "acdfg", "acdefg", "abc", "abcdefg", "abcdfg")
        segments = {"a": (4, 0, 17, 4), "b": (21, 4, 4, 16), "c": (21, 24, 4, 16),
                    "d": (4, 40, 17, 4), "e": (0, 24, 4, 16), "f": (0, 4, 4, 16), "g": (4, 20, 17, 4)}
        for idx, digit in enumerate("%02d%02d" % (hour, minute)):
            x = 12 + idx * 31 + (12 if idx >= 2 else 0)
            for name in patterns[int(digit)]:
                dx, dy, w, h = segments[name]
                self.rect(x + dx, 5 + dy, w, h, WHITE)
        self.rect(75, 18, 4, 4, WHITE)
        self.rect(75, 34, 4, 4, WHITE)

    def remaining(self, reset, now):
        if reset is None:
            return "--"
        seconds = max(0, int(reset - now))
        if not seconds:
            return "待同步"
        mins = (seconds + 59) // 60
        if mins >= 1440:
            return "%dd %dh" % (mins // 1440, (mins % 1440) // 60)
        return "%dh %dm" % (mins // 60, mins % 60)

    def render(self, p, now=None, online=True):
        now = p["ts"] if now is None else now
        # CPython uses 1970; firmware passes a corrected calendar helper below.
        stamp = calendar(now + p["offset"] * 60)
        self.rect(0, 0, 320, 240, BG)
        if p["ts"]:
            self.clock(stamp[3], stamp[4])
            self.text(11, 52, "%04d-%02d-%02d" % stamp[:3], MUTED)
            self.text(11, 67, "周" + "一二三四五六日"[stamp[6]], MUTED)
        else:
            self.text(12, 8, "--:--", scale=3)
            self.text(11, 58, "待同步", MUTED)
        self.rect(175, 9, 1, 66, EDGE)
        weather_center_x = 248
        city_text = str(p["city"])
        condition_text = str(p["condition"])
        weather_scale = 1.25
        city_width = min(self.text_width(city_text, weather_scale), 48)
        condition_width = min(self.text_width(condition_text, weather_scale), 48)
        separator_gap = 12
        weather_width = city_width + separator_gap + condition_width
        weather_x = weather_center_x - weather_width // 2
        self.text(weather_x, 14, city_text,
                  scale=weather_scale, max_width=city_width)
        separator_x = weather_x + city_width + separator_gap // 2
        self.c.draw_circle(separator_x, 24, 1, color=MUTED, fill=True)
        condition_x = weather_x + city_width + separator_gap
        self.text(condition_x, 14, condition_text,
                  scale=weather_scale, max_width=condition_width)
        temp = "--" if p["temp"] is None else str(int(round(p["temp"])))
        temp_width = self.text_width(temp, 2)
        row_width = temp_width + (15 if p["temp"] is not None else 0)
        temp_x = weather_center_x - row_width // 2
        self.text(temp_x, 42, temp, scale=2, max_width=75)
        if p["temp"] is not None:
            self.temperature_unit(temp_x + temp_width + 3, 46)
        self.text(287, 67, "旧" if p["weather_stale"] else "", AMBER)
        self.c.draw_circle(311, 8, 3, color=GREEN if online else RED, fill=True)
        for x, title, q, r, color in ((7, "5小时剩余额度", "q5", "r5", GREEN),
                                      (164, "7D 剩余", "q7", "r7", BLUE)):
            self.rect(x, 87, 149, 90, CARD)
            self.rect(x, 87, 149, 90, EDGE, False)
            self.text(x + 9, 94, title, max_width=137)
            expired = p[r] is not None and now >= p[r]
            self.ring(x + 38, 139, p[q], MUTED if p["stale"] or expired or not online else color)
            self.text(x + 75, 117, "重置倒计时", MUTED, max_width=68)
            self.text(x + 75, 135, self.remaining(p[r], now), max_width=68)
            self.text(x + 75, 153, "待同步" if expired else "数据旧" if p["stale"] else "", AMBER)
        self.rect(7, 184, 306, 40, CARD)
        self.text(15, 188, "Codex状态", MUTED)
        status = p["status"] if online else "OFFLINE"
        self.text(15, 206, status,
                  GREEN if status == "DONE" else BLUE if status == "RUNNING"
                  else MUTED if status == "IDLE" else AMBER,
                  max_width=135)
        self.rect(163, 191, 1, 27, EDGE)
        self.text(174, 188, "最近活跃模型", MUTED)
        self.text(174, 206, p["model"], max_width=131)
        self.text(9, 226, "K210 x Codex", MUTED)
        self.text(204, 226, "DEMO" if p["demo"] else "USB SERIAL", AMBER if p["demo"] else MUTED)


def calendar(unix_seconds):
    # MaixPy/CanMV ports may use 2000 instead of 1970 as their epoch.
    convert = getattr(time, "gmtime", time.localtime)
    epoch = convert(0)[0]
    # Older CanMV time functions reject float seconds, unlike CPython.
    return convert(int(unix_seconds) - (946684800 if epoch == 2000 else 0))
