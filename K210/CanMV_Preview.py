# K210 Codex Display Hub · CanMV single-file preview
# Open this file in CanMV IDE, connect K210 and Run. For permanent use, save it
# as /flash/main.py, reboot the board, then disconnect CanMV IDE before the
# computer bridge opens the same Type-C serial port.
"""320x240 renderer; no hardware imports, identical operations in browser preview."""
import math
try:
    import uzlib as zlib
except ImportError:
    import zlib
try:
    import ujson as json
except ImportError:
    import json
try:
    import ubinascii as binascii
except ImportError:
    import binascii
try:
    import uhashlib as hashlib
except ImportError:
    import hashlib
import os
import time

# Reserve the largest contiguous allocation before fonts fragment the heap.
if __name__ == "__main__":
    import image
    import gc
    gc.collect()
    _screen_image = image.Image(size=(320, 240))

BG = (10, 17, 22)
CARD = (21, 32, 41)
EDGE = (37, 52, 64)
WHITE = (240, 244, 248)
MUTED = (148, 163, 177)
GREEN = (22, 215, 141)
BLUE = (31, 172, 239)
AMBER = (255, 182, 74)
RED = (239, 68, 68)
SETTINGS_PATH = "/flash/k210_codex_settings.json"
BOOT_ID_PATH = "/flash/k210_codex_boot_id.json"
PROGRAM_INFO_PATH = "/flash/k210_codex_program.json"
PROGRAM_VERSION = "v1.3.0"
TOUCH_SCL = 30
TOUCH_SDA = 31
WEATHER_HITBOX = (226, 0, 94, 82)
BACK_HITBOX = (0, 0, 72, 56)
INFO_HITBOX = (266, 0, 54, 52)
CODEX_HITBOX = (12, 57, 296, 38)
CLOCK_SETTINGS_HITBOX = (268, 0, 52, 48)
FLIP_HITBOX = (12, 99, 296, 38)
BLUETOOTH_HITBOX = (12, 141, 296, 38)
BRIGHTNESS_DOWN_HITBOX = (214, 184, 44, 46)
BRIGHTNESS_UP_HITBOX = (262, 184, 46, 46)
RESET_HITBOX = (216, 156, 100, 84)
DEFAULT_BRIGHTNESS = 100
PROGRAM_UPDATE_MAX_BYTES = 512 * 1024
PROGRAM_UPDATE_CHUNK_BYTES = 768
GPIO_UART_TX = 7
GPIO_UART_RX = 6
GPIO_UART_BAUD = 9600  # HC-05 data mode; match the module's AT+UART setting.


class DimmableCanvas:
    """Apply software brightness when the K210 LCD API has no BL driver."""
    def __init__(self, canvas):
        self.canvas = canvas
        self.brightness = 100
        self.color_cache = {}

    def set_brightness(self, value):
        self.brightness = max(20, min(100, int(value)))
        self.color_cache = {}

    def color(self, value):
        if self.brightness >= 100:
            return value
        scaled = self.color_cache.get(value)
        if scaled is None:
            scaled = tuple(channel * self.brightness // 100 for channel in value)
            self.color_cache[value] = scaled
        return scaled

    def draw_rectangle(self, x, y, w, h, color, fill=False):
        return self.canvas.draw_rectangle(x, y, w, h,
                                          color=self.color(color), fill=fill)

    def draw_line(self, x, y, x2, y2, color, thickness=1):
        return self.canvas.draw_line(x, y, x2, y2,
                                    color=self.color(color), thickness=thickness)

    def draw_circle(self, x, y, radius, color, fill=False):
        return self.canvas.draw_circle(x, y, radius,
                                      color=self.color(color), fill=fill)


def valid_reset_key(value):
    return isinstance(value, str) and (value == "" or len(value) == 32
                                      and all(ch in "0123456789abcdef" for ch in value))


def valid_reset(reset):
    if reset is None:
        return True
    if (not isinstance(reset, dict) or reset.get("type") not in ("reset_credit", "direct_reset")
            or reset.get("status") not in ("announced", "confirmed")
            or reset.get("precision") not in (None, "exact", "approximate", "deadline", "date", "window")
            or not isinstance(reset.get("stale"), bool)
            or not valid_reset_key(reset.get("key", ""))
            or not isinstance(reset.get("seen", False), bool)):
        return False
    for key in ("from", "through", "confirmed_at", "checked_at", "expires_at"):
        value = reset.get(key)
        if value is not None and (type(value) is not int or not 0 < value <= 9999999999):
            return False
    if reset.get("expires_at") is None:
        return False
    start, end = reset.get("from"), reset.get("through")
    return (start is None and end is None and reset.get("precision") is None
            or start is not None and end is not None and start <= end and reset.get("precision") is not None)


class Display:
    def __init__(self, canvas, font_path):
        self.c = DimmableCanvas(canvas)
        with open(font_path) as f:
            self.font = json.load(f)

    def set_brightness(self, value):
        self.c.set_brightness(value)

    def text(self, x, y, value, color=WHITE, scale=1, max_width=320,
             crisp=False, background=None, clip=None):
        # Native-size glyphs, four coverage levels; integer-only LCD drawing.
        table = SMOOTH_FONTS.get(str(int(12 * scale)))
        if table is not None:
            start = x
            # Text sits on one of the two flat surfaces in this layout.
            if background is None:
                background = (0, 0, 0) if getattr(self, "clock_only", False) else (CARD if y >= 83 else BG)
            palette = [tuple((background[i] * (3 - a) + color[i] * a) // 3
                             for i in range(3)) for a in range(4)]
            for char in str(value):
                width, height, pixels = table.get(char, table["?"])
                if x + width > start + max_width:
                    break
                for row in range(height):
                    col = 0
                    while col < width:
                        index = row * width + col
                        level = (pixels[index // 4] >> ((index % 4) * 2)) & 3
                        if crisp:
                            level = 3 if level else 0
                        end = col + 1
                        while end < width:
                            next_index = row * width + end
                            next_level = (pixels[next_index // 4] >> ((next_index % 4) * 2)) & 3
                            if crisp:
                                next_level = 3 if next_level else 0
                            if next_level != level:
                                break
                            end += 1
                        left = max(x + col, clip[0]) if clip else x + col
                        right = min(x + end, clip[1]) if clip else x + end
                        if level and right > left:
                            self.c.draw_rectangle(left, y + row, right - left, 1,
                                                  color=palette[level], fill=True)
                        col = end
                x += width + 1
            return
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
                        left, right = x + int(col * scale), x + int(end * scale)
                        if clip:
                            left, right = max(left, clip[0]), min(right, clip[1])
                        if right > left:
                            self.c.draw_rectangle(left, y + int(row * scale), right - left,
                                                  int((row + 1) * scale) - int(row * scale),
                                                  color=color, fill=True)
                        col = end
                    else:
                        col += 1
            x += int((width + 1) * scale)

    def text_width(self, value, scale=1):
        table = SMOOTH_FONTS.get(str(int(12 * scale)))
        if table is not None:
            return sum(table.get(ch, table["?"])[0] + 1 for ch in str(value))
        return sum(int((self.font.get(ch, self.font["?"])[0] + 1) * scale) for ch in str(value))

    def centered_text_y(self, value, center_y, scale=1):
        """Center visible glyph pixels, excluding different font top bearings."""
        table = SMOOTH_FONTS.get(str(int(12 * scale)))
        if table is None:
            return center_y - int(12 * scale) // 2
        top, bottom = None, None
        for char in str(value):
            width, height, pixels = table.get(char, table["?"])
            for row in range(height):
                for col in range(width):
                    index = row * width + col
                    if (pixels[index // 4] >> ((index % 4) * 2)) & 3:
                        top = row if top is None else min(top, row)
                        bottom = row if bottom is None else max(bottom, row)
                        break
        return center_y if top is None else center_y - (top + bottom + 1) // 2

    def text_lines(self, value, scale, max_width):
        lines, line = [], ""
        for char in str(value):
            if line and self.text_width(line + char, scale) > max_width:
                lines.append(line)
                line = ""
            line += char
        return lines + [line]

    def rect(self, x, y, w, h, color, fill=True):
        self.c.draw_rectangle(x, y, w, h, color=color, fill=fill)

    def temperature_unit(self, x, y):
        """Draw a compact degree-C unit without depending on font glyphs."""
        self.c.draw_circle(x, y, 2, color=WHITE, fill=False)
        self.rect(x + 6, y + 1, 6, 2, WHITE)
        self.rect(x + 5, y + 3, 2, 7, WHITE)
        self.rect(x + 6, y + 10, 6, 2, WHITE)

    def ring(self, x, y, value, color, remaining_text=None):
        label_color = WHITE
        if value is not None and color in (GREEN, BLUE):
            if value <= 10:
                color = label_color = RED
            elif value <= 20:
                color = label_color = AMBER
        # Signed-distance coverage: smooth inner/outer edges and rounded caps.
        # Draw directly into the existing framebuffer; no extra image/cache.
        radius, half_width = 42, 4
        tau = 2 * math.pi
        fraction = 0 if value is None else max(0, min(100, value)) / 100.0
        sweep = fraction * tau
        end_x, end_y = radius * math.sin(sweep), -radius * math.cos(sweep)
        track_palette = [tuple((CARD[i] * (8 - a) + EDGE[i] * a) // 8
                               for i in range(3)) for a in range(9)]
        extent = int(radius + half_width + 1)
        inner_squared = (radius - half_width - 1) ** 2
        outer_squared = (radius + half_width + 1) ** 2
        for dy in range(-extent, extent + 1):
            for dx in range(-extent, extent + 1):
                squared = dx * dx + dy * dy
                if squared < inner_squared or squared > outer_squared:
                    continue
                radial = abs(math.sqrt(squared) - radius)
                coverage = max(0, min(1, half_width + 0.5 - radial))
                if coverage <= 0:
                    continue
                ink = track_palette[int(coverage * 8 + 0.5)]
                if fraction > 0:
                    angle = math.atan2(dx, -dy)
                    if angle < 0:
                        angle += tau
                    distance = radial
                    if fraction < 1 and angle > sweep:
                        start_distance = math.sqrt(dx * dx + (dy + radius) * (dy + radius))
                        end_distance = math.sqrt((dx - end_x) * (dx - end_x) +
                                                 (dy - end_y) * (dy - end_y))
                        distance = min(start_distance, end_distance)
                    active = max(0, min(1, half_width + 0.5 - distance))
                    # Coverage outside the arc blends into its gray track;
                    # radial edges blend into the card background only once.
                    mix = min(8, int(active / coverage * 8 + 0.5))
                    level = int(coverage * 8 + 0.5)
                    ink = tuple((ink[i] * (8 - mix) +
                                 ((CARD[i] * (8 - level) + color[i] * level) // 8) * mix) // 8
                                for i in range(3))
                self.c.draw_rectangle(x + dx, y + dy, 1, 1, color=ink, fill=True)
        label = "--" if value is None else str(int(round(value)))
        number_scale = 34 / 12
        width = self.text_width(label, number_scale)
        percent_width = self.text_width("%") if value is not None else 0
        total = width + percent_width
        start_x = x - total // 2 + 2
        self.text(start_x, y - 22, label, label_color, scale=number_scale)
        if value is not None:
            self.text(start_x + width, y - 5, "%", label_color)
        if remaining_text:
            remaining_width = self.text_width(remaining_text)
            self.text(x - remaining_width // 2, y + 16, remaining_text, MUTED)

    def clock(self, hour, minute, second):
        patterns = ("abcdef", "bc", "abdeg", "abcdg", "bcfg", "acdfg", "acdefg", "abc", "abcdefg", "abcdfg")
        segments = {"a": (5, 0, 15, 5), "b": (20, 5, 5, 19), "c": (20, 29, 5, 19),
                    "d": (5, 48, 15, 5), "e": (0, 29, 5, 19), "f": (0, 5, 5, 19), "g": (5, 24, 15, 5)}
        for idx, digit in enumerate("%02d%02d%02d" % (hour, minute, second)):
            x = 4 + (idx // 2) * 70 + (idx % 2) * 28
            for name in patterns[int(digit)]:
                dx, dy, w, h = segments[name]
                self.rect(x + dx, 2 + dy, w, h, WHITE)
        for colon_x in (62, 132):
            self.rect(colon_x, 17, 5, 5, WHITE)
            self.rect(colon_x, 39, 5, 5, WHITE)
        return (4 + 197) // 2

    def weather_icon(self, x, y, condition):
        """Tiny weather icon made from LCD primitives; no bitmap allocation."""
        name = str(condition)

        def sun(cx, cy):
            self.c.draw_circle(cx, cy, 4, color=AMBER, fill=True)
            self.rect(cx - 1, cy - 7, 2, 2, AMBER)
            self.rect(cx - 1, cy + 6, 2, 2, AMBER)
            self.rect(cx - 7, cy - 1, 2, 2, AMBER)
            self.rect(cx + 6, cy - 1, 2, 2, AMBER)

        def cloud(cx, cy, color=WHITE):
            self.c.draw_circle(cx + 5, cy + 7, 4, color=color, fill=True)
            self.c.draw_circle(cx + 10, cy + 4, 5, color=color, fill=True)
            self.c.draw_circle(cx + 15, cy + 7, 4, color=color, fill=True)
            self.rect(cx + 4, cy + 7, 13, 5, color)

        if "雷" in name:
            cloud(x, y, MUTED)
            self.rect(x + 10, y + 12, 3, 4, AMBER)
            self.rect(x + 8, y + 15, 3, 3, AMBER)
        elif "雨" in name:
            cloud(x, y, MUTED)
            for dx in (6, 11, 16):
                self.rect(x + dx, y + 14, 2, 4, BLUE)
        elif "雪" in name:
            cloud(x, y, MUTED)
            for dx in (7, 14):
                self.rect(x + dx, y + 14, 2, 2, WHITE)
        elif "晴" in name and "云" not in name:
            sun(x + 10, y + 8)
        elif "云" in name:
            sun(x + 5, y + 4)
            cloud(x + 2, y + 2)
        else:
            cloud(x + 2, y + 2, MUTED)

    def remaining(self, reset, now):
        if reset is None:
            return "--"
        seconds = max(0, int(reset - now))
        if not seconds:
            return "待同步"
        # Codex reset windows are at most seven days. A larger difference
        # means the board still has its 2000-era standby clock after a reboot.
        if seconds > 8 * 24 * 60 * 60:
            return "待同步"
        mins = (seconds + 59) // 60
        if mins >= 1440:
            return "%dd %dh" % (mins // 1440, (mins % 1440) // 60)
        return "%dh %dm" % (mins // 60, mins % 60)

    def reset_message(self, p, now):
        event = p.get("reset")
        if not event or not valid_reset(event) or now > event["expires_at"]:
            return None
        credit = event["type"] == "reset_credit"
        confirmed = event["status"] == "confirmed"
        prefix = "发卡" if credit else "重置"
        checked = event.get("checked_at")
        stale = event["stale"] or not checked or now - checked > 1800
        color = MUTED if stale else GREEN if confirmed else AMBER
        title = ("重置卡已确认发放" if confirmed else "重置卡即将发放") if credit else (
            "额度重置已确认" if confirmed else "Codex 额度预计重置")
        if confirmed:
            badge = prefix + "确认"
            timing = "确认记录 " + info_time(event["confirmed_at"], p["offset"]) if event.get("confirmed_at") else "最近已确认"
            relative = "确认状态由消息源提供"
        elif event.get("from") is None:
            badge, timing, relative = prefix + "待定", "时间未确定", "等待消息源公布时间"
        else:
            start, end, precision = event["from"], event["through"], event["precision"]
            if precision == "date":
                date = calendar(start + p["offset"] * 60)
                last = calendar(end + p["offset"] * 60)
                day = "%02d/%02d" % (date[1], date[2])
                timing = "预计日期 " + day
                if date[:3] != last[:3]:
                    timing += "-%02d/%02d" % (last[1], last[2])
                badge, relative = prefix + day, "仅公布日期，具体时间未确定"
            else:
                target = end if precision == "deadline" else start
                minutes = int(math.ceil(max(0, target - now) / 60))
                hours = minutes // 60
                duration = ("%dd" % (hours // 24) if hours >= 2400 else
                            "%dd%02dh" % (hours // 24, hours % 24) if hours >= 24 else
                            "%dh%02dm" % (hours, minutes % 60) if hours else "%dm" % minutes)
                full = "%d小时%02d分" % (hours, minutes % 60) if hours else "%d分" % minutes
                badge = prefix + duration
                timing = ("预计截止 " if precision == "deadline" else
                          "预计大约 " if precision == "approximate" else "预计 ") + info_time(target, p["offset"])
                if precision in ("window", "approximate") and start != end:
                    last = calendar(end + p["offset"] * 60)
                    end_text = ("%02d:%02d" % (last[3], last[4]) if
                                calendar(start + p["offset"] * 60)[:3] == last[:3] else info_time(end, p["offset"]))
                    timing = "预计 " + info_time(start, p["offset"]) + "-" + end_text
                relative = ("距预计截止 " if precision == "deadline" else "距预计开始 ") + full
                if precision != "deadline" and now >= start:
                    badge = prefix + "时段"
                    relative = "预计正在发生" if precision == "exact" else "已进入预计发放时段"
        return {"badge": badge, "timing": timing, "relative": relative,
                "title": title, "color": color, "stale": stale}

    def render_reset(self, p, now=None, online=True, live=False):
        self.clock_only = False
        now = p["ts"] if now is None else now
        self._begin_frame("reset", BG, live)
        if self._dirty("header", True):
            self.settings_card(12, 8, 36, 36, 9)
            self.c.draw_line(33, 18, 24, 26, color=WHITE, thickness=3)
            self.c.draw_line(24, 26, 33, 34, color=WHITE, thickness=3)
            self.text((320 - self.text_width("重置信息", 20 / 12)) // 2,
                      self.centered_text_y("重置信息", 26, 20 / 12),
                      "重置信息", scale=20 / 12, max_width=100)
            self.rect(0, 52, 320, 1, EDGE)
        if self._dirty("link", online):
            self.c.draw_circle(296, 26, 3, color=GREEN if online else RED, fill=True)
        message = self.reset_message(p, now)
        event = p.get("reset") or {}
        identity = (message, event.get("checked_at"))
        if not self._dirty("message", identity):
            return
        self.rect(0, 54, 320, 186, BG)
        self.settings_card(12, 64, 296, 144, 9)
        if message:
            ink = message["color"]
            label = ("公开确认" if event["status"] == "confirmed" else
                     "发放预告" if event["type"] == "reset_credit" else "重置预告")
            rows = ((label, 82, 1.25, ink), (message["title"], 111, 20 / 12, WHITE),
                    (message["timing"], 145, 1.5, WHITE),
                    (message["relative"], 181, 1.5, WHITE))
        else:
            rows = (("暂无有效重置消息", 136, 20 / 12, WHITE),)
        for value, center_y, scale, color in rows:
            self.text((320 - self.text_width(value, scale)) // 2,
                      self.centered_text_y(value, center_y, scale), value, color,
                      scale=scale, max_width=272, background=CARD)
        footer = "来源 AIhot 公开消息"
        self.text(12, self.centered_text_y(footer, 229, 1.25), footer,
                  scale=1.25, max_width=188, background=BG)
        checked = event.get("checked_at")
        stamp = calendar(checked + p["offset"] * 60) if checked else None
        check = "检查 %02d:%02d" % (stamp[3], stamp[4]) if stamp else "尚未检查"
        self.text(308 - self.text_width(check, 1.25), self.centered_text_y(check, 229, 1.25),
                  check, scale=1.25, max_width=100, background=BG)

    def render_program_update(self, percent, live=True):
        self.clock_only = False
        percent = max(0, min(100, int(percent)))
        self._begin_frame("program_update", None, live)
        if self._dirty("header", True):
            try:
                # Blur the existing framebuffer once; never delay each upload chunk.
                self.c.canvas.mean(5)
                self.c.canvas.mean(5)
                self.c.canvas.blend(self.c.color(BG), alpha=64)
            except Exception:
                # A visual effect must not interrupt a firmware write on older builds.
                self.rect(0, 0, 320, 240, BG)
            for label, center_y, color in (("正在执行更新···", 64, WHITE),
                                            ("请勿断电！！！", 104, AMBER)):
                self.text((320 - self.text_width(label, 20 / 12)) // 2,
                          self.centered_text_y(label, center_y, 20 / 12), label,
                          color, scale=20 / 12, max_width=288, background=BG)
        if self._dirty("progress", percent):
            self.rect(30, 140, 260, 48, BG)
            self.rect(30, 140, 260, 16, EDGE)
            self.rect(32, 142, 256, 12, CARD)
            if percent:
                self.rect(32, 142, 256 * percent // 100, 12, AMBER)
            caption = "%d%%" % percent
            self.text((320 - self.text_width(caption, 1.25)) // 2,
                      self.centered_text_y(caption, 176, 1.25), caption,
                      scale=1.25, max_width=100, background=BG)

    def _begin_frame(self, page, background, live=False):
        self.frame_changed = False
        identity = (page, self.c.brightness)
        if not live or getattr(self, "_page_identity", None) != identity:
            self._regions = {}
            self._page_identity = identity
            if background is not None:
                self.rect(0, 0, 320, 240, background)
            self.frame_changed = True

    def _dirty(self, name, value):
        if name in self._regions and self._regions[name] == value:
            return False
        self._regions[name] = value
        self.frame_changed = True
        return True

    def render_live(self, p, now, online=True, force=False):
        self.render(p, now, online, live=True)

    def render(self, p, now=None, online=True, live=False):
        self.clock_only = False
        if p.get("show_codex", True) is False:
            return self.render_clock(p, now, online, live=live)
        now = p["ts"] if now is None else now
        # CPython uses 1970; firmware passes a corrected calendar helper below.
        stamp = calendar(now + p["offset"] * 60)
        self._begin_frame("codex", BG, live)
        if self._dirty("time", (bool(p["ts"]), stamp)):
            self.rect(0, 0, 224, 80, BG)
            if p["ts"]:
                time_center = self.clock(stamp[3], stamp[4], stamp[5])
                date_text = "%04d-%02d-%02d" % stamp[:3]
                weekday_text = "周" + "一二三四五六日"[stamp[6]]
                date_width = self.text_width(date_text)
                weekday_width = self.text_width(weekday_text)
                row_gap = 12
                row_x = time_center - (date_width + row_gap + weekday_width) // 2
                self.text(row_x, 62, date_text, MUTED)
                self.text(row_x + date_width + row_gap, 62, weekday_text, MUTED)
            else:
                self.text(12, 8, "--:--", scale=3)
                self.text(11, 58, "待同步", MUTED)
        if self._dirty("weather", tuple(p.get(k) for k in ("city", "condition", "temp", "weather_stale"))):
            self.rect(225, 0, 95, 80, BG)
            self.rect(225, 5, 1, 70, EDGE)
            weather_center_x = 273
            city_text = str(p["city"])
            condition_text = str(p["condition"])
            weather_scale = 1.25
            weather_table = SMOOTH_FONTS.get("15", {})
            for char in city_text + condition_text:
                if char not in weather_table:
                    weather_scale = 1
                    break
            city_width = self.text_width(city_text, weather_scale)
            condition_width = self.text_width(condition_text, weather_scale)
            separator_gap = 10 if weather_scale > 1 else 14
            weather_width = city_width + separator_gap + condition_width
            weather_x = weather_center_x - weather_width // 2
            # Keep location and condition on one centered row. Draw the separator
            # as a circle because some CanMV fonts do not contain the middle dot.
            weather_y = 16 if weather_scale > 1 else 18
            stacked = weather_width > 90
            if stacked:
                lines = self.text_lines(city_text, weather_scale, 64)
                city_y = 16 if len(lines) == 1 else 3
                for index, line in enumerate(lines):
                    self.text(weather_center_x - self.text_width(line, weather_scale) // 2,
                              city_y + index * 18, line, scale=weather_scale, max_width=64)
                row_y = 58 if len(lines) >= 3 else 45
                temp = "--" if p["temp"] is None else str(int(round(p["temp"])))
                temp_width = self.text_width(temp, 1.5)
                row_x = weather_center_x - (condition_width + 6 + temp_width + 18) // 2
                self.text(row_x, row_y + 2, condition_text, scale=weather_scale,
                          max_width=condition_width)
                temp_x = row_x + condition_width + 6
                self.text(temp_x, row_y, temp, scale=1.5, max_width=temp_width)
                self.temperature_unit(temp_x + temp_width + 3, row_y + 3)
            else:
                self.text(weather_x, weather_y, city_text,
                          scale=weather_scale, max_width=city_width)
                separator_x = weather_x + city_width + separator_gap // 2
                self.c.draw_circle(separator_x, 24, 1, color=MUTED, fill=True)
                condition_x = weather_x + city_width + separator_gap
                self.text(condition_x, weather_y, condition_text,
                          scale=weather_scale, max_width=condition_width)
            if not stacked and p["temp"] is None:
                temp = "--"
                temp_width = self.text_width(temp, 2)
                row_width = temp_width + 9 + 22
                temp_x = weather_center_x - row_width // 2
                self.text(temp_x, 42, temp, scale=2, max_width=48)
                icon_x = temp_x + temp_width + 9
            elif not stacked:
                temp = str(int(round(p["temp"])))
                temp_scale = 1.5 if len(temp) >= 3 else 2
                temp_y = 45 if temp_scale == 1.5 else 42
                temp_width = self.text_width(temp, temp_scale)
                row_width = temp_width + 44
                temp_x = weather_center_x - row_width // 2
                self.text(temp_x, temp_y, temp, scale=temp_scale, max_width=48)
                degree_x = temp_x + temp_width + 3
                self.temperature_unit(degree_x, 46)
                icon_x = degree_x + 19
            if not stacked:
                self.weather_icon(icon_x, 46, p["condition"])
            self.text(228 if stacked else 294, 3 if stacked else 67,
                      "旧" if p["weather_stale"] else "", AMBER)
        if self._dirty("link", (online, p["city"], p["condition"], p["temp"], p["weather_stale"])):
            self.c.draw_circle(311, 8, 3, color=GREEN if online else RED, fill=True)
        for x, title, q, r, color in ((4, "5H 剩余", "q5", "r5", GREEN),
                                      (162, "7D 剩余", "q7", "r7", BLUE)):
            if self._dirty(q, (p[q], p["stale"], online, self.remaining(p[r], now), p[r] is not None and now >= p[r])):
                self.rect(x, 83, 154, 126, CARD)
                title_width = self.text_width(title, 1.5)
                self.text(x + (154 - title_width) // 2, 88, title, scale=1.5, max_width=154)
                expired = p[r] is not None and now >= p[r]
                reset_text = self.remaining(p[r], now)
                self.ring(x + 77, 158, p[q],
                          MUTED if p["stale"] or expired or not online else color,
                          reset_text)
                if p["stale"]:
                    self.text(x + 137, 117, "旧", AMBER)
        reset = self.reset_message(p, now)
        if reset and p["reset"]["status"] == "confirmed" and p["reset"].get("seen", False):
            reset = None
        caption = reset["badge"] if reset else str(p.get("model") or "--")
        scale, space = 1.25, 98
        caption_width = self.text_width(caption, scale)
        overflow = max(0, caption_width - space) if not reset else 0
        scroll = 0
        if overflow:
            travel = overflow + 24
            step = int(now) * 8 % (travel * 2)
            scroll = min(overflow, max(0, min(step, travel * 2 - step) - 12))
        if self._dirty("status", (p["status"], caption, online, reset, scroll)):
            self.rect(4, 214, 312, 26, CARD)
            status = p["status"] if online else "OFFLINE"
            label = "Codex状态:"
            label_x = 12
            self.text(label_x, self.centered_text_y(label, 227, 1.25), label, WHITE, scale=1.25)
            status_scale = 20 / 12
            # Fixed central slot: short and long states share the same center.
            status_left, status_right = 104, 208
            if self.text_width(status, status_scale) > status_right - status_left:
                status_scale = 1.25
            status_width = self.text_width(status, status_scale)
            status_x = status_left + (status_right - status_left - status_width) // 2
            if reset:
                fill = (36, 48, 58) if reset["stale"] else (21, 60, 50) if p["reset"]["status"] == "confirmed" else (52, 43, 28)
                self.rounded_rect(212, 216, 102, 22, 4, fill)
                color = reset["color"]
            else:
                fill, color = CARD, WHITE
            self.text(214 - scroll if overflow else 263 - caption_width // 2,
                      self.centered_text_y(caption, 227, scale), caption, color,
                      scale=scale, max_width=caption_width, background=fill, clip=(214, 312))
            self.text(status_x, self.centered_text_y(status, 227, status_scale), status,
                      GREEN if status == "DONE" else BLUE if status == "RUNNING"
                      else MUTED if status == "IDLE" else AMBER,
                      scale=status_scale, max_width=308 - status_x)

    def rounded_rect(self, x, y, w, h, radius, color):
        """Rounded fill using scanlines supported by CanMV and the web canvas."""
        radius = min(radius, w // 2, h // 2)
        for row in range(h):
            dy = max(radius - row - 0.5, row + 0.5 - (h - radius), 0)
            inset = int(radius - math.sqrt(max(0, radius * radius - dy * dy)) + 0.5)
            self.rect(x + inset, y + row, w - 2 * inset, 1, color)

    def settings_card(self, x, y, w, h, radius=12):
        self.rounded_rect(x, y, w, h, radius, EDGE)
        self.rounded_rect(x + 1, y + 1, w - 2, h - 2, radius - 1, CARD)

    def render_clock(self, p, now=None, online=True, live=False):
        """Reference-matched flip-card clock, with live time and weather."""
        self.clock_only = True
        now = p["ts"] if now is None else now
        stamp = calendar(now + p["offset"] * 60)
        black, tile, ink = (0, 0, 0), (36, 43, 46), (245, 244, 239)
        self._begin_frame("clock", black, live)
        if self._dirty("date", stamp[:3]):
            self.rect(0, 0, 296, 38, black)
            date = "%04d-%02d-%02d" % stamp[:3]
            weekday = "周" + "一二三四五六日"[stamp[6]]
            dw, ww = self.text_width(date, 19/12), self.text_width(weekday, 19/12)
            x = (320-dw-ww-12)//2
            self.text(x, 9, date, ink, scale=19/12)
            self.text(x+dw+12, 9, weekday, ink, scale=19/12)
        # The whole top-right hit area opens settings; the dot shows link health.
        if self._dirty("link", online):
            self.c.draw_circle(306, 17, 3, color=GREEN if online else RED, fill=True)
        for left, value in ((16, stamp[3]), (168, stamp[4])):
            if self._dirty(left, value):
                self.rounded_rect(left, 42, 136, 135, 8, tile)
                label = "%02d" % value
                width = self.text_width(label, 124/12)
                number_x = left+(136-width)//2 - (7 if left == 168 else 0)
                self.text(number_x, 65, label, ink, scale=124/12,
                          background=tile)
                self.rect(left, 110, 136, 1, black)
        if self._dirty("colon", True):
            self.c.draw_circle(159, 100, 4, color=ink, fill=True)
            self.c.draw_circle(159, 124, 4, color=ink, fill=True)
        if self._dirty("seconds", (stamp[4], stamp[5])):
            self.rect(284, 139, 18, 17, tile)
            seconds = "%02d" % stamp[5]
            self.text(302-self.text_width(seconds, 22/12), 139, seconds, ink,
                      scale=22/12, background=tile)
        if self._dirty("weather", tuple(p.get(k) for k in ("city", "condition", "temp", "weather_stale"))):
            self.rect(0, 184, 320, 56, black)
            # Same single weather row and larger sun as in the supplied reference.
            condition = str(p["condition"])
            if condition == "晴":
                for angle in range(0, 360, 45):
                    rad = angle * math.pi / 180
                    self.c.draw_line(45+int(15*math.cos(rad)), 213+int(15*math.sin(rad)),
                                     45+int(19*math.cos(rad)), 213+int(19*math.sin(rad)),
                                     color=(255, 185, 0), thickness=3)
                self.c.draw_circle(45, 213, 10, color=(255, 185, 0), fill=True)
            else:
                self.weather_icon(34, 206, condition)
            city = str(p["city"])
            scale = 2 if all(ch in SMOOTH_FONTS["24"] for ch in city+condition) else 1
            temp = "--" if p["temp"] is None else str(int(round(p["temp"])))
            value = temp + "°C"
            temp_scale = 26/12 if self.text_width(value, 26/12) <= 80 else 2
            temp_x = 302 - self.text_width(value, temp_scale)
            separator_x = 78 + max(55, self.text_width(city, scale)) + 6
            condition_x = separator_x + 10
            if condition_x + self.text_width(condition, scale) + 6 <= temp_x:
                self.text(78, self.centered_text_y(city, 213, scale), city, ink,
                          scale=scale, max_width=self.text_width(city, scale))
                self.c.draw_circle(separator_x, 213, 1, color=ink, fill=True)
                self.text(condition_x, self.centered_text_y(condition, 213, scale),
                          condition, ink, scale=scale, max_width=self.text_width(condition, scale))
                temp_y = self.centered_text_y(value, 213, temp_scale)
            else:
                if self.text_width(city, scale) > 300:
                    scale = 1.25
                lines = self.text_lines(city, scale, 150)
                for index, line in enumerate(lines):
                    self.text(66, 185 + index * 27, line, ink, scale=scale, max_width=150)
                self.text(230, 187, condition, ink, scale=1.25, max_width=72)
                temp_y = 214
            self.text(temp_x, temp_y, value, ink, scale=temp_scale)
            if p.get("weather_stale"):
                self.text(303, 185, "旧", AMBER)

    def render_settings(self, flipped, brightness=DEFAULT_BRIGHTNESS,
                        touch_ready=True, show_codex=True,
                        bluetooth_enabled=False, live=False, online=True):
        self.clock_only = False
        self._begin_frame("settings", BG, live)
        if self._dirty("header", True):
            self.settings_card(12, 8, 36, 36, 9)
            self.c.draw_line(33, 18, 24, 26, color=WHITE, thickness=3)
            self.c.draw_line(24, 26, 33, 34, color=WHITE, thickness=3)
            self.text((320-self.text_width("设置", 2))//2, 13, "设置", WHITE, scale=2)
            self.settings_card(272, 8, 36, 36, 9)
            self.c.draw_circle(290, 26, 10, color=BLUE, fill=False)
            self.c.draw_circle(290, 21, 1, color=BLUE, fill=True)
            self.rect(289, 25, 3, 8, BLUE)
            self.rect(0, 52, 320, 1, EDGE)
        if self._dirty("link", online):
            self.c.draw_circle(313, 7, 2, color=GREEN if online else RED, fill=True)
        for y, label, enabled in ((57, "显示 Codex 额度", show_codex),
                                  (99, "屏幕翻转", flipped),
                                  (141, "外接蓝牙", bluetooth_enabled)):
            if self._dirty(y, enabled):
                self.settings_card(12, y, 296, 38, 9)
                self.text(24, y+10, label, WHITE, scale=1.25)
                if y == 141:
                    self.text(132, y+11, "9600", BLUE, scale=1.25)
                self.rounded_rect(242, y+5, 52, 28, 14, GREEN if enabled else EDGE)
                self.c.draw_circle(280 if enabled else 256, y+19, 10, color=WHITE, fill=True)
        if self._dirty("brightness", brightness):
            self.settings_card(12, 183, 296, 47, 9)
            self.text(24, self.centered_text_y("设备亮度", 206, 1.25),
                      "设备亮度", WHITE, scale=1.25)
            level = "%d%%" % brightness
            self.text(125, self.centered_text_y(level, 206, 1.25), level, BLUE, scale=1.25)
            self.settings_card(218, 190, 36, 32, 8)
            self.settings_card(266, 190, 36, 32, 8)
            self.c.draw_line(229, 206, 243, 206,
                             color=MUTED if brightness == 20 else WHITE, thickness=3)
            ink = MUTED if brightness == 100 else BLUE
            self.c.draw_line(277, 206, 291, 206, color=ink, thickness=3)
            self.c.draw_line(284, 199, 284, 213, color=ink, thickness=3)


    def render_info(self, info, offset=480, online=True, live=False):
        """Device-owned telemetry, rendered identically in the web preview."""
        self.clock_only = False
        self._begin_frame("info", BG, live)
        if self._dirty("header", True):
            self.settings_card(12, 8, 36, 36, 9)
            self.c.draw_line(33, 18, 24, 26, color=WHITE, thickness=3)
            self.c.draw_line(24, 26, 33, 34, color=WHITE, thickness=3)
            title = "设备信息"
            self.text((320 - self.text_width(title, 2)) // 2, 13, title, WHITE, scale=2)
            # Connection health includes heartbeats; data age is independent.
            self.rect(0, 52, 320, 1, EDGE)
            self.settings_card(12, 64, 296, 150)
        if self._dirty("link", online):
            self.c.draw_circle(296, 26, 3, color=GREEN if online else RED, fill=True)
        age = info.get("rx_age_ms")
        rows = (("程序版本", info.get("version") or "--", WHITE),
                ("启动时间", info_time(info.get("boot_ts"), offset), WHITE),
                ("本次运行", format_uptime(info.get("uptime_ms")), WHITE),
                ("累计运行", "待同步" if info.get("total_elapsed_ms") is None
                 else format_uptime(info["total_elapsed_ms"]), WHITE),
                ("最近通信", "未收到" if age is None else "%d 秒前" % (age // 1000),
                 BLUE if online else MUTED))
        for index, (label, value, color) in enumerate(rows):
            if not self._dirty(index, (value, color)):
                continue
            y = 75 + index * 27
            self.rect(25, y, 269, 23, CARD)
            self.text(25, y, label, MUTED, scale=1.25)
            # Long runtimes/connection gaps use the native 12 px table.
            scale = 1.25 if self.text_width(value, 1.25) <= 180 else 1
            self.text(294 - self.text_width(value, scale), y, value, color, scale=scale)
            if index < 4:
                self.rect(25, y + 23, 269, 1, EDGE)
        if self._dirty("updated", info_time(info.get("updated_at"), offset)):
            self.rect(12, 222, 296, 16, CARD)
            self.text(23, 223, "最近更新 " + info_time(info.get("updated_at"), offset), MUTED)


def format_uptime(milliseconds):
    if milliseconds is None:
        return "--"
    seconds = max(0, int(milliseconds) // 1000)
    return "%dh %02dm %02ds" % (seconds // 3600, seconds // 60 % 60, seconds % 60)


def info_time(timestamp, offset):
    if not timestamp:
        return "待同步"
    stamp = calendar(timestamp + offset * 60)
    return "%02d-%02d %02d:%02d" % (stamp[1], stamp[2], stamp[3], stamp[4])


def calendar(unix_seconds):
    # MaixPy/CanMV ports may use 2000 instead of 1970 as their epoch.
    convert = getattr(time, "gmtime", time.localtime)
    epoch = convert(0)[0]
    # Older CanMV time functions reject float seconds, unlike CPython.
    return convert(int(unix_seconds) - (946684800 if epoch == 2000 else 0))


EMBEDDED_FONT = {}


_FONT_PATH = globals().get("__file__", "")
if "/" in _FONT_PATH:
    _FONT_PATH = _FONT_PATH.rsplit("/", 1)[0] + "/font.bin"
elif __name__ == "__main__":
    _FONT_PATH = "/sd/font.bin" if "sd" in os.listdir("/") else "/flash/font.bin"
else:
    _FONT_PATH = "K210/font.bin"


class NativeFont:
    """Read compressed glyphs from storage and cache only visible text."""
    def __init__(self, characters, offsets, path, extra=None):
        self.characters = characters
        self.offsets = binascii.a2b_base64(offsets)
        self.path = path
        self.extra = extra or {}
        self.cache = {}

    def __contains__(self, char):
        return char in self.extra or char in self.characters

    def _offset(self, index):
        index *= 3
        return (self.offsets[index] | self.offsets[index + 1] << 8
                | self.offsets[index + 2] << 16)

    def get(self, char, default=None):
        if char in self.cache:
            return self.cache[char]
        if char in self.extra:
            data = zlib.decompress(binascii.a2b_base64(self.extra[char]))
        else:
            # ponytail: linear lookup over at most 681 characters; index if profiling warrants it.
            index = self.characters.find(char)
            if index < 0:
                return default
            start, end = self._offset(index), self._offset(index + 1)
            with open(self.path, "rb") as font:
                font.seek(start)
                data = zlib.decompress(font.read(end - start))
        glyph = (data[0], data[1], data[2:])
        if len(self.cache) >= 48:
            self.cache.clear()
        self.cache[char] = glyph
        return glyph

    def __getitem__(self, char):
        glyph = self.get(char)
        if glyph is None:
            raise KeyError(char)
        return glyph


# BEGIN SMOOTH FONT DATA
# Generated by tools/generate_fonts.py directly at each target size.
# Four-level coverage; no bitmap resizing or supersampling.
SMOOTH_FONTS = {'12': NativeFont(' %-/0123456789:?dhm一三上二云五京余倒六剩北南同周四圳型多天安小州广庆度待态成据数日旧时晴最未杭模步武汉津活海深状知置苏西计跃近郑都重阴雨雪雷雾额itguynK_OLervT.ljzZCBAxQDPSYbHERNpoMGWfwaFkUqJVXsIc更新秒前', 'AAAADAAANQAARAAAWAAAdgAAkgAAsQAAzwAA7gAADAEAKgEASAEAZwEAhQEAlQEAswEA0gEA8QEAFAIAJgIAQgIAXAIAcwIAmgIAwQIA7AIAGQMARQMAcQMAngMAwQMA7gMAFgQAQQQAZQQAiQQAtwQA5AQADQUAOwUAYwUAjgUAqwUA1gUABAYAMgYAYAYAjwYAvQYA7AYABQcAIQcARwcAdQcAnwcAyAcA9gcAJAgATQgAeAgApggA0wgAAQkALwkAXAkAiAkAtgkA4AkACwoANAoAXQoAiwoAuQoA5goAFAsAQQsAbwsAmwsAxgsA8wsAIgwAUAwAZQwAeQwAmAwAtAwA0wwA7wwAFA0AJA0ARw0AXw0Aew0AlA0Arw0Axw0A1w0A6g0A/w0AGg4AOQ4AXA4AfQ4AoQ4AvQ4A4w4ABQ8AIA8AQQ8AXQ8AfA8Amw8AtA8A1w8A/A8AGhAANhAAXRAAgRAAqxAAvhAA3xAA+xAAFREANBEAUREAcBEAjBEArREAzhEA6hEA/REAGRIARBIAchIAnxIAxxIA', _FONT_PATH, {'发': 'eNrj4XUQYQiIYFLgYP3y356BlYHhP38DI2sCC5PEAgaWSLEJjCoMYAAArmIHVg==', '卡': 'eNrj4WXgZGDgYGD4ww0k//6vB7EzGBg8mBg4OEFsIAIDAHiBBJA=', '确': 'eNrj4WVoYPhxXT5AhVXhSf4P5SCNT/UKSkFA9OF7vYJUgACnBgMYAAAdhQvT', '认': 'eNrj4VVgYGhoAGIQsm1gUGhgVAhgVFBiXSDCUcCpwsAkwAAGAI0yBbs=', '定': 'eNrj4WXgYGAQYfjx356DQeHCf24GFYYDKgwBX+QXqDCEaDNwrKhnAAMAzI0JSw==', '段': 'eNrj4WVgYPjyiFskgOOLMoeISo3ID/kvyZwiASwr25gkJkiJSKQwgAEA47IJsA==', '公': 'eNrj4WUAgr8MDY6MExiYFEQ4JTg0ONgEGJoYGhyYHqxiD2DkYAADAIhsBjw=', '开': 'eNrj4WVgYPjyX57BiRGC/v6vB5IOjowNDowJDowqDowMYAAAwXMH/A==', '放': 'eNrj4Z3QwNDQwPD3fb2CgohCAMeDbg6FDg4FVzYVV3aPaZKuqgkMYAAAAsoKIA==', '预': 'eNrj4WVgYPj7297BkXHJB/1/6aoBaaoBiaoBCaoBE0QTEiRUJBIYwAAADjcLDw==', '告': 'eNrj4XVQYWhQYXjwn19FhYFFheHPf3sGBoYL/7kDGFgv/GcPYOBkAAMA5j8KGA==', '暂': 'eNrj4Z3AIPJnvmhCIEPIxfof8Rwr1DgcBDke/Od/8J9bgQHEYGBgYWBgAAAt3Q1D', '无': 'eNrj4WVgYPjyn5uBgwGC/vzXZ8hkYEhicEgSCEhIkLigzwAGAKzuB/4=', '有': 'eNrj4WWQYGDgYPjzv56BicHhP/8CRo6I//wOjBwOYLKBkYsBDADGkAh+', '效': 'eNrj4Q1wYGxoYPh3v14hUEREjYNzKodCE+cFRvYJTlwqU1VYJQIYwAAA4/cJHQ==', '消': 'eNrj4WVoYEhpVXHo4GD5a6/CqsDw196BVWHCX3sFVgUgl4NVhwEMAM1mCDQ=', '息': 'eNrj4WVgZbjwnzuAgQNIwhkBoiwzAlQ6GVMd/jMxgAEAEjIL3A==', '仅': 'eNrj4W1gZGj4Kx8gImIgwqGhwKGawKrQwARCE9gUJCUSmBIYwAAAqIUG4g==', '布': 'eNrj4WWQYGDgYPj7fz+DEEODIsOD//IzFUQaoEiKQYEBAgDSawi1', '期': 'eNrj4VXgYPhTX6/gEfjBP1DBIwRM1v/dH5jQGiAyI4ElQYMBDAAh2AyS', '具': 'eNrj4WVgYLjwnz2AgRVIwhl//9c7ODIuYOCSYFBhAAMAH9kL/w==', '体': 'eNrj4Z3QwJDQwKDQwLDiv73mAibFGUwKrRwJTRIL/uYrgMUhAAAE2wsJ', '间': 'eNrj4VVgYGj4oS/CoBLyXzXEEYz+Q0mgoASDJAMYAADbiAkY', '等': 'eNrj4Q1oYPhw336GItuT//wMIgx//tczMDD/+K/vwMjEwMbEMIGJAQwAJ3cLPQ==', '源': 'eNrj4RVhYAj4V8/gxMj6TV9CSUHgmz4QKQgxiSiJeXAlnZzA5ODAAAQAynYJOw==', '由': 'eNrj4WVQYWAQYfjyX14EDOAMKBsoBwYAsWwH3A==', '提': 'eNrj4VVgYFD4If/3l7wCh4jCT/kt/+o1WxgUOvQV5jAoPGLMccxiAAMACpwLhg==', '供': 'eNrj4Z3AwJAgwaYgwZbx314TzFjwf7+CCKMCB6cCm0QCkwIDGAAArRsHFQ==', '截': 'eNrj4WVoYvyyXoKhieHv//qAJoYv60V/5nMqOHF9kGf/oM+uMCGCQUGFgYEBADeoDUQ=', '止': 'eNrj4WVQYACiAAj6oB8AZysw/P1fzwADAItbBuQ=', '大': 'eNrj4WUQYYCgH//tGTQYQCiSgaGNsYGRPYVBVoJBhQEMAHRJBQE=', '约': 'eNrj4W1oYAgIYFB4Zh+hpjBFQiGgQSGlTTGEQYmBVWEFowILgxQDGAAAz2oH5g==', '距': 'eNrj4WVgYPjxv55DiQGIfnySb1EQOaEu0vFBvkOB4YYag+Tf/QxgAAD/YQu6', '始': 'eNrj4Q1oYExoYFBQYPkrJqHwzz5ENMDji/wUEZEFoiIq3+Q5RUQYwAAA5IgJmg==', '分': 'eNrj4WUAgh8MDY6MDQxMCgycL/5rszCJMjAxARFQROUBIwMYAACTegY7', '正': 'eNrj4WVgYPjyX55BgQGIGoDog3wDhKHA8Pd/PQMMAADHKQj2', '在': 'eNrj4WXgYGDgZPjx357BiaGhkWHCX/2OBoYGCPq/v4EBCgDqbQsm', '生': 'eNrj4WVQYZigwhCgwvDhv7yKCgOHCsOV/9xAQQj6+7+eAQwAuacIrQ==', '已': 'eNrj4WVgYPjxn52BgRWIJBhYgWwJEINBgkFAgkHjy39uBjAAAJd/Btg=', '进': 'eNrj4WVQYZIQYUr4b88gwmQrwrTgf72CCJMCB5MCJ9MORkbXVXUMYAAAnZIHRQ==', '入': 'eNrj4WVgYDigz8BgwMCgwsAQwcDQycDQxOjAyDSBgVWFQYKDQYEBDABgQgP9', '原': 'eNrj4WVgYPjw315BgSHhv7wCEDFJgBiBjCJBnDMCFDgyGCAAAOWJCVU=', '帖': 'eNrj4U1oYASiv82MmU31mU2Mmd/0M8UUgChFRCHhi36CiAIDGAAAEUoLLg==', '和': 'eNrj4WVgYPgiz+DQVO/QFPBnf4BDW0DDrICESQEeYBEHJgYIAAAR/wvq', '完': 'eNrj4WXgYACiH//lORhELvxnYmAAsRmCGICoYaKCxgF9BjAAAMfRCS8=', '整': 'eNrj4XVwZPxbz+jQWP9jPcuPeM4ETi6PiSpf/us3fOFuUGH4+7+eAQwAP3AOlA==', '说': 'eNrj4WVh4UhRYW0QYLL7K6/AKgJCQIYC0wJFpgKJIAc2OwYwAACVRga0', '明': 'eNrj4WVgYPjxWF9CSUXikf4PZRUIA0iC2FIqDJxSDGAAANdfCIc=', '请': 'eNrj4WVpYFD4Zx/QwMDwR972f73CH3kFDhEgmfBXvoBDRIBDkgEMAAE6Cow=', '查': 'eNrj4WUQYQCiP//tHcIYM0Qyr/xnD2BgvfCfHYgYGEDiDGAAAPnEDA0=', '看': 'eNrj4WVgYAhYKsXAwfDgPz8DK8Of//UODBwRU/kd/vM7MHIASQagHAMDANSKCc8=', '网': 'eNrj4WVgYPjyXz9EQnVKqtqUJjWRZtWQZrUpoWpLNDRFGEQkGOQYwAAA68MJQw==', '页': 'eNrj4WVgYPjx356Bg+HBf+4EAY4EETDi4EiYyTmBiTODQYUBDADPqghL', '来': 'eNrj4WUQYfjyXx5IThBhcwhl/PPfnmEVQ0MoU4qIJIeIAFAcAgDQ3Agg', '当': 'eNrj4WWQYFEQkZkgwuQwleHHf3kGBhEg+gBmwLggAAC4RwiL', '检': 'eNrj4VVoYFCYwKigwPaXS8LgV/QWBgaNVgnNDg6FEE4FBlaFf/UMYAAAx84Ixg==', '尚': 'eNrj4WUQYRARkZgQwuQgwvDjv3zEf9EIRzD6LyrBICLBIMUABgDAPAhQ', '陈': 'eNrj4WWYwPA3gaH1v33oAgbVGQxTOxha/+rPnMEq2inh2qTAuoABAgAdBwvC', '仍': 'eNrj4W1gZGj4yx+gwFGgwJGhwJKhUJugoJAgopDAoZDAqZDAJM0ABgC+DgeK', '按': 'eNrj4RVoYFBoYFT4V//XLUEhUGDBv3oLBQ4FCQ6FCUwKS6Q0pBQYwAAA83QJuw==', '不': 'eNrj4WVgYPjzv55BhYFBgoEhirFBlK1ERIJDRIFBhAGEgOJgAACN+ATu', '代': 'eNrj4W1QYGhQYEtI4CgI0MxYyhjZxhDQwBDgwBjA4JTAkJnAoMIABgDHQgg+', '表': 'eNrj4WUQYQCiH//1v/znBzL+/q9nSJOY0MQU4cTkICXZwCjAAAYA9ZwKKw==', '本': 'eNrj4WUQYYCgP//tGTIZGKYxNIQwJYhwzfivxSAiAJICAwCaxgbc', '账': 'eNrj4WUQYPijJBGhxKqRxKShxKDxz15DCchgWqDIKZEkyZlawAAGAJ1kBu0=', '户': 'eNrj4WUQYWBIYDjwX7+BQQGCgOwJDAoBDAwKDCBJCAAAvpMHfg==', '到': 'eNrj4WVgMPhTr9jAoaSgobQiTcmBQcmBSemHvpKDkMISLQURBl0GMAAAv9IH2g==', '记': 'eNrj4WVgYBD5K7+AQcKBQcKWQULhh7wCh4gCB0MCl8AGiQSFL/oMYAAAmZQHLw==', '录': 'eNrj4WVgYPjwn5uBgeXCf/a//+sZVFgWqHA6pDEuSGGTUFFhkGKAAAD1PgmJ', '返': 'eNrj4WVgYBD5I53AwVD7R19BQ0QkgmNPK1tAK7tCmsYSQQGOlVoMYAAAwO0Ilg==', '回': 'eNrj4WVgYPjyX16EQUTkv6hIkwgU/RcBikDEGcAAANBjCQM=', '，': 'eNrj4WVAAxoMDAoMDCLowgALiQB2'}), '18': NativeFont(' :?Cdeox态状屏幕翻转设备亮度：D7余5剩H01234689-周一二三四五六日', 'xxIA0xIA7BIAGxMAUhMAhRMArhMA2RMAAhQAVBQAqhQAABUAURUApxUAABYAVxYApRYA7BYAPxcAWBcAjxcAvhcADxgAPxgAkxgAuxgA3BgA9hgAHxkARxkAbBkAkxkAvBkA5RkA9RkARRoAWhoAdRoAlhoA2RoAHhsAYRsAjhsA', _FONT_PATH, {'重': 'eNoTEmEAgb//9zMxMHACWRMYGP78//+fGchgbPj/X59BYoIDEzLDHqSGEaz47///7CDGgf8geQwAAC6aGYY=', '置': 'eNoTEmGAgQf//9czcHGxsYEZDAUMDD/+///PyKDCwODw/788gwnDAUZUxhegtAMzg8wBoDp7BgwAAGeRG9o=', '信': 'eNoTEmHgkmBg2MDAxeDA3MDIEPH/f/0FRgYGBjkg3tL4f3+DJJDRAGQwSIAYICmJP/+5Gw4yTGCQ4GHgbDgIkgIxHBnQAAAfxxR4', '息': 'eNoTEmFoYGJgYOAB4v//5RkMGBoYEQxmBgmGD///IzFYGBkYWh86MCVImCxwaGZ01JCQYJB1dvhfz4AJAOAlEtk=', '暂': 'eNoTEmFgY5jCUMCwnu3Hf2MGhh4mZoYEjQ//nf7rOzMJaBioMEzIcmLakSWrwtAwyYHRQZRBhOHH//9MDYwMKlgYKAAA3AIYjQ==', '无': 'eNoTEmGAgQv//+szMJgAWQ7MDFAGE0PD////+RkaVIE8HyYGhgkqQIY7kCEBZCQ4MJlMYDRpcGJi+MfNgAEAvZUNMQ==', '有': 'eNoTEmFgYGNgYDAB4gYmBof////bM0wAchjYgfgHkPOAkYG5QYrBwKHr/39mDQkGA4YGoAhYCsyAiEwAMaQYUAAAiDwQ4Q==', '效': 'eNoTEnFgamBkWMAgw8DAcYDhwv96TgYGhgf/2SWY2NkuTFRJYGB7wcSswTDTwIGDy4epwahBkuEB0wcGBl0GboYS7hVMFw4u3NDAfoWpgoWBjYGRARkAACM8FHs=', '消': 'eNoTEmFgOMDIwMXCI9Fw/eAFxgATH3YGhkMSDUx//ss/OAiUM+FhkGE48P8/IwOIcRIoUgCUcjgMZEgARS4cADLYeRxkFS4w7GBAAQDy8Bh2', '卡': 'eNoTEmE4wMTAwCABxA2MQOLHfyYIAypy4P////YQkQigwoaHQJZECUhKFshwYERSDDIHGQAA9ZENbA==', '即': 'eNoTEmEAAdYUDpEH/4//Z+RK4JFYwHCsgfFfPZQBEgFKARkMQBGBA0AGJ5Bx4FADY1d1D/cD7rMMDuwMPEBDDjCgAgDh1hdk', '将': 'eNoTEmEAgQsMDpIMnevDHCcxsEgUeB68yDC3QIXNgZNTlWECgwMjAyeDBMOMf//rJ4AY0wsaGDU4ZSQaJjo0QaQmMFxhZOBt4GJAAQDRrRJb', '发': 'eNoTEmEAAYEFGgwM7GzcDCoGBxgbHJkYGP7+/6/vcAAkxwnEH/7bMzRwNzAy3GXkZNhSUsJwgekuowN3AzeDiMNtNoYJ8he0GaQYJjAyIAMAnr4S7w==', '放': 'eNoTEmFga2BkWMDAy8Ags4DhwP96dgYHpi//mVUamNgcmGQTGL7Is7E7NC00YFCR8GRqOHhQg0GG5wXjgQMM3AxcPFsYTJ4+qDxw6Rq7HgsDlwMjAzIAAE8NFic=', '已': 'eNoTEmGAgQ////MDqQMgNg+YsQDE+Pf/P0MCiMHG0ABiMAAZDHAGL1DNFoa///8zMWAAAO3zDnQ=', '确': 'eNoTEmFgaGBkYGDgYvjy/0M9Q0GDI5MDMxcPg8aD//oXAnncnP8fTzB5kfDnPzPPsQSTAwk8bsw804CM+r9AkWUMJgcC2B2YGTQYVBgaGD6ADEIAAGUOGng=', '认': 'eNoTEmEAAQkGXgYH9gsMDBcYeRkYWC6AxDgZ/jIBGSogKaYFjECGGpBhwMag4jqJwcGMR4bhxwIH5gPOzAsYOPgYdBgSGBwYGZABAGi/DMA=', '额': 'eNoTEmFgYmBgWADEDDw//rf/r09gkGBiY2vQTFBgmHDh6//GLPbJEgx6Bp2MFbmTJXYwmHY6/uVPlpjQYNLOyOmRLnbh/8GLDJwR7CYMDJIMjAzIAADRoxfY', '预': 'eNoTEmEAggP/0xlUGDT+/2eaAKQvsDcwMiy48J+bwYV3woX/91U4GSb7TmIwuARkMIMYFzSAjN6JDAYMZxkZmLcsYNB7wqgR0MDEwMiADADwJhcT', '计': 'eNoTEmFgaGBi4GKQAFINjAwLgAwGFiCDAcj4+/j/f3YVIMMBJIXKWARkLGEC6uIGMjRAuhgOgHQhAQDF/Q1w', '记': 'eNoTEmEAATYgvnDofz3DBhCbhSEBLPiHCciQ+PCfvYGRGcgwYGAAMhgYJAyBDEsgY4cBg8oBJvYJTBwX/nMzYAAAAc0Oxw==', '录': 'eNoTEmEAgR///zMBKQkgbmBk+P9fHsg4AJLgafj//799AAOvAkPuhQY2Bl9eNgaHWxwMDLK+3Ay6FxrYKxh4LzgyLGVgYbjAxoAKAPzlEy0=', '最': 'eNoTEmEAgS///zM6MDFIoDJAMgf+//9vfwAoxPBn/X/uAw5KCQx/9FmZDjg0STDEaC5h2BHWJMfAkMItx+Ag5sCAAgCDsxmv', '近': 'eNoTEmEAAVaGKWwHHPdnMZioAHkOTAwN9UCGg9P//8w8KhcYEhyYeB3qgQyGJkZeBgWeCwwNCxl4GXJZLzAUrAyNbGRcskqKAQMAAJGOEj8=', '时': 'eNoTEmFgYJBxYGA8wPDDnoGnwZH5AIOECYjx+789mPHfGSQiCxRxPgxkLACpOYhQLKPAwAM04ikQP2BjQAEAHCMTyg==', '间': 'eNoTEmlgZGBgKAFiBt7/+xlYGRg4ExgYJjC0/ZfnTJBomMjQxigBZaCI/Pg/kYENppiNwYGrgIGhhgEFAAC3OhJN', '未': 'eNoTEmEAgwkgghPCaPj/3x4u4vD//399hgmZQM50HgYGjU4mhgsTNjA4sHPqMshOcODewcB5wZkRrJiPARUAAD8DEFk=', '定': 'eNoTEmFwYGJgYCgAYgZOBob////LGzAwNDAyMzBIAHnyDAwToFITQAyuv/sZCoAMB3agyJuJDAwLnnAxNLAvWWUnwYAOAEisEOw=', '期': 'eNoTEmEAARmDH/YNjM2OTv//S6qAGEw/7FdoARn/mSQMJFQa/gNFwAygyKH//6VUGJoOODKpaPCoXGBY5uDE7sCuKcCgsoCRARkAAA2GGFQ=', '截': 'eNoTEmEQcWBgcGCew3Th//kFQAYPw5////8zJzDyMDAsb2BkkGCR4Lrwv7/QgZ9NhenGf28pBh62DwwH/nNzARlfgIz7mkw8Duz2DgwiQNXIAAAMPBhK', '止': 'eNoTEmEAgwNMQEIGiBsYGRhYJRgYCkAM5h//GQzADKAIdsbf////MzNgBQAmkwug', '大': 'eNoTEmFg4GJgYFgAxMgMh////+tDRD4AsYMakJBgZmC4MAHIYNdgYNBl4GbYwnCA/Qkjw4cmJqAkAwYAAGGcDB8=', '约': 'eNoTEmHgDGBgWMDAxeDAXMDAIOHJzDBhwY//TuGXGTW2yHE3ME0wYdRgYF7QwCTBICPxYJdDM2MUQ4EEAwPDAUYGSQaZKesYDjhKMQTwAUXyGFAAAMUwEhk=', '由': 'eNoTEmFg4GJgYFgAxCDGv///uRcwcE1g4FrAwAlnPPj/fz+qCJgBUcwwAWwCJgAA+IoTdA==', '源': 'eNoTEmEAAbZ//+0vJDkwMiRwyTAwLPj7v4HhFgNPyQKeAwyyp/7zM4AYIJGGpL//GWTcDRknGOgUM7QtMpAxiWV2PryIwYCBheEBM9AkVgY4AAAFUhgp', '提': 'eNoTEmlgYmBg0Pjyn7uBiTnhwv/P/9lBDAYNAwa2hqD/+xleAOUvXP//v0mfQYZhBtMhRgYNkz/8DU6HgYwVMgwNk14wMkiw56Xu0HBYBTIKAQCJtxqa', '供': 'eNoTEmFgY2BgKCi4wODAxMbJIJMwgeHCj///HfmBjDcMbJwOPEDGASCD4c7///UHGBgZGHgWLGA44MCswcAjw8B+oIThAqMPE4MEAwYAAG9CFHE=', '等': 'eNoTEmFgamBksBDgFbnw//t/ptONixg0TFhkGBgWOjA0/P+vz8CQwMCQwco2pTmMwcGSgYGBneH////8DCJAhgN7AQPDBUZ2BgaWEqDMFyYGFAAAZioTtg==', '待': 'eNoTEmHgOMDIsICBh8GB+QADA++f//wWDEBGAFCE4db//0wqDAwMF5gYZB20gcIzPv3/z64hcoChgYmdh0HjwEEggwXIYHgCZBzgZkABADS/FNY=', '公': 'eNoTEmEAgz/8DAwyDYwMFxhUGBi4GNgZTAQuMBxg5NZg4DNh4K44wHjBiZGXgYWhYAIDQwOTDAMDrwM7w5bQXwwN67M0gfodmBgwAACu1w1/', '布': 'eNoTEmFg4GJgYCgAYgcmhob////XM0wAchjamRgYJDQYGB78/8/dwK0xwWF2AxOnDqfGBIYJQAYDlMEFZNQxMDQ4AXVoMKACAK0jEUg=', '仅': 'eNoTEmGQYGBguMAIJE79389gkMDA1cDEWcCgO4GB+QETj0mDWkMjkweTigyDCsNyBgemCexAhg6Q8QDIaLjM6ODEVcGQIceg16AGFGRABgBsKBB3', '，': 'eNoTEmEgBD6ACH4gbgAxOIFYgYEoAABkzwHP', '具': 'eNoTEmEAgQSGKQwM///zMxQwHMDGYGfgYfjw/z+YceD////2YF0NzLIMDtwOUg5yQEoDyGdkQAYAZeAUeQ==', '体': 'eNoTEmFgW8DAUMDAxuDAlMDAIAFkXPjx/z8Tf4MawwuGTLYLPAXTGI44JMkw8Mi4MR1YkLCBwYedTfbA3P/57D4MbAIHGIC6eIC6DjAAzUEBACdnFFc=', '距': 'eNoTEmEAgf/ZDKoJC/7/Z2I7xsCQkMDDwABksH7I/rOfYcEBBs4CLp4JDI/PM3AacAFFFh9gYDDwAqoBijD8EONhaGD887+eAQMAAPEQGBQ=', '开': 'eNoTEmGAgQv//9czMLBrMDAkNDAxMLDBGECRP////2dmYAaKGABFHJiADAkg4wKDBoMDO5ChC2QkMIAUIwMAEKIOmQ==', '始': 'eNoTEnFga2BkMGGQZXBgWsDAIOHgzPDHXsKEgWcyA9eELf//M7kFsZokaDAwMBx++J+7hJdrAsPeBQycDtxAxopFDJwXEv7td2AHikgwsAYwoAAAIAkVOg==', '小': 'eNoTEmFg4GNgYFgAxAycQDwBylgwoYCBgZ2Tl8FkQgNTAyNnCQPPBAauBQycBxyZJzBYyADVuMMVMywF4gdsDKgAACc5CyM=', '分': 'eNoTEmEAgy/8DAwWDYwMBxhVGBj4GJgZChgWMDQwM8gwyDE4sFf8/3+fiZHzAgPDBAZOBga2CQwMCkBGA+MCBgc2BnYGrgZtBkwAAK0EDJk=', '进': 'eNoTEmFgYGRkYLowgWEBAycng8aECQwMn/7/Z2QAMv4wAkUkgIyGn///M0ssADIY2YAiJkDGQSZOBl3OCQwlkgwMDQyrVtkxYAAAZQcUvA==', '入': 'eNoTEmGAg3/8QOIAiCULxAuYgIQxEKuwAUUnAhlcMgwMJg5MDAcYC4AcoCINhgbmLQwMDxyBSiUYMAAAuPwI7w==', '正': 'eNoTEmGAgQv//+9nYDjACGTKMEAYrEBGwoH/7AxsYAZQBIPx5////81iDA6WDBgAAC6xEG0=', '在': 'eNoTEmFwYGJgYNAA4gOMDAz////XZ1gA5DAwcwJFLzAwPGDgZWg49X8/wywWXgYNTqDIBKAIA5zx8/9/drAGLgY0AACIaxF+', '生': 'eNoTEmFg4GVgELjAwMDAzcnAYDKBgaFBDMj48/8/+wUGIIMdKKIBYnz5/5+JAcRgAIqgMA78///fngEDAAD8sg9Y', '/': 'eNoTEmEAAQdmIKEBxAcYgQQvEC8ACbMDsQkQNzABCRkgvgAS5QLiApgmHZgoJgAAG1IFYg=='}), '24': NativeFont(' %-0123456789:?ACDEFGHIKLNORTUW余剩周设置备信息天津南京晴多云阴雨雪雷雾未知°七万三上丘东个中丰临丹为主丽义乌乐九乡乳二五井亚交亭亳什仁介仓仙令仪们任份伊休会伦佛作佳侗依侯保傈傣僳儋元充光克公六兰共关兴兵内冈冲冶冷凉凌凤凭凯则利力勒匀包化北区十华博卫原厦县双口古句可台合吉同名吐吕启吴员呼和咸哈唐商喀善嘉嘴四回园固国图土地圳坊坝垣城埠基堰塔壁壮夏大太头夷奎如姚威娄嫩子孜孝孟宁安宏定宜宝宣宫家容宾宿密富察寿封射尔尚尼尾屏屯山岑岗岛岩岭岳峡峨峪峰崃崇崖嵊川州巢左巩巴市布常平广庄庆庐库应底店度康廉廊延建开张弥当彝彦彬彭彰征徐德志忠忻怀怒恩惠感慈成房扎扬扶承投抚拉招指掖揭攀敦文斗斯新方施族无日旧旬昆昌明星春昭晋普景暨曲朔朝木本朴来杨杭松林果枝枣柯柳树栖栗株根格桂桃桐桥桦梁梅梧棱楚楞榆樟横武毕民水永汉汕汝江池汨汾沁沂沅沈沙沧河油治泉泊波泰泸泽洛洪洮洱洲流济浏浦浩浮海涟涿淄淖淮深清渠温渭港湖湘湛湾源溧溪滁滋滕满滦滨漠漯漳潍潜潭潮澄澎澳濮灯灵烟焦煌照熟版牙牡特犁狮玉玛环珠珲理琼瑞瓦瓯甘田甸界番登白百皇皋益盐监盖盘盟眉石碑磐祁神祥票禄福禹秦穆穴竹简米红纳绍绥绵罗羌老耒聊肇肥胡胶腾自舒舞舟色节芒芜芝芦芬花苏苗英茂茫荆荔荣荥莆莞莱莲菏萍营萨葛葫蒙蓥藏虎蚌蛟衡衢襄西讷许诸调贝贡贵贺资赣赤车辉辛边辽达迁运远连迪通遂遵邑邓邛邡邢那邮邯邳邵邹郑郭郴郸都鄂酒醴里重金钟钢钦铁铜银错锡锦镇长门间阆阜防阳阿陆陇陵隆随雄雅集霍霞霸青靖鞍韩音韶顶项顺颇额饶首香马驻骅高鲁鲜鸡鸭鹤鹰麻黄黑黔鼎齐龙', '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', _FONT_PATH), '36': NativeFont(' -0123456789:?C', 'rRkBuhkBzhkBJBoBWBoBxBoBOhsBkBsB/xsBfBwBxBwBLx0Bpx0Bux0BJB4BpR4B', _FONT_PATH), '15': NativeFont('?南京天津晴多云阴雨雪雷雾未知DFTRUEKONALWGI连接已程序版本启动时间持续运行最近通信秒前收到待同步0123456789vdhms :.-显示额度屏幕翻转设备亮Coex周一二三四五六日%外蓝牙累计七万上丘东个中丰临丹为主丽义乌乐九乡乳井亚交亭亳什仁介仓仙令仪们任份伊休会伦余佛作佳侗依侯保傈傣僳儋元充光克公兰共关兴兵内冈冲冶冷凉凌凤凭凯则利力勒匀包化北区十华博卫原厦县双口古句可台合吉名吐吕吴员呼和咸哈唐商喀善嘉嘴回园固国图土地圳坊坝垣城埠基堰塔壁壮夏大太头夷奎如姚威娄嫩子孜孝孟宁安宏定宜宝宣宫家容宾宿密富察寿封射尔尚尼尾屯山岑岗岛岩岭岳峡峨峪峰崃崇崖嵊川州巢左巩巴市布常平广庄庆庐库应底店康廉廊延建开张弥当彝彦彬彭彰征徐德志忠忻怀怒恩惠感慈成房扎扬扶承投抚拉招指掖揭攀敦文斗斯新方施族无旧旬昆昌明星春昭晋普景暨曲朔朝木朴来杨杭松林果枝枣柯柳树栖栗株根格桂桃桐桥桦梁梅梧棱楚楞榆樟横武毕民水永汉汕汝江池汨汾沁沂沅沈沙沧河油治泉泊波泰泸泽洛洪洮洱洲流济浏浦浩浮海涟涿淄淖淮深清渠温渭港湖湘湛湾源溧溪滁滋滕满滦滨漠漯漳潍潜潭潮澄澎澳濮灯灵烟焦煌照熟牡特犁狮玉玛环珠珲理琼瑞瓦瓯甘田甸界番登白百皇皋益盐监盖盘盟眉石碑磐祁神祥票禄福禹秦穆穴竹简米红纳绍绥绵罗羌老耒聊肇肥胡胶腾自舒舞舟色节芒芜芝芦芬花苏苗英茂茫荆荔荣荥莆莞莱莲菏萍营萨葛葫蒙蓥藏虎蚌蛟衡衢襄西讷许诸调贝贡贵贺资赣赤车辉辛边辽达迁远迪遂遵邑邓邛邡邢那邮邯邳邵邹郑郭郴郸都鄂酒醴里重金钟钢钦铁铜银错锡锦镇长门阆阜防阳阿陆陇陵隆随雄雅集霍霞霸青靖鞍韩音韶顶项顺颇饶首香马驻骅高鲁鲜鸡鸭鹤鹰麻黄黑黔鼎齐龙', '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', _FONT_PATH, {'次': 'eNrjF2Q4wMjAZsLAwM4V4HD4v3yDKiuzQ/tEBpYEFgZmHYYEBy4GngTOBiYuLoUtDHIObAycDKgAAIfICTA=', '发': 'eNrjF3RoFGVIMJFl4ODkcGhiZHjw/z8/AycDQ8N/eYYMBiYHJQUGDQW2BqYdjAxPJko5sDmwMaACANaqC6g=', '卡': 'eNrjF2QwYGBg4AHi//wMB4AUkPv//39mBmYGhgMSQK4kUFaDgYGZAyYLVowAAEk4B7o=', '置': 'eNrjF2QAgj//9zM0Byoc+P+fm2ECA8O///+Z/vznZ2BmYAYKMXz4b8/AA4T//zP/+Q8URgUAXw8WRA==', '确': 'eNrjF2Rg4GD41/2fQUJFgsHZmWlBw3/+v+kcTW0//zsktikEzFDk+PL8P3Mrb4NDI6MBg4IDNwMqAADJFRHL', '认': 'eNrjF5zAwMOwgIGZQeEAAwODAcNfINfAgZ2BZwITA/MMhgMcQQwbkgwYdDlVHC4xaDBwACEqAABzvgkp', '段': 'eNrjF2SQ/M+wp9WAgVmB4wADB9OH+Y0MPCIPmJn/2h+oj+QyYGhimOutNoG9gfkAQ6a0wQIGTgZUAABQKQ6K', '状': 'eNrjF2Rg5mE4wJDcYHjA5ACPAYvA9//8BxjYGUwmMDbwzmCQYA52OJRQwGDAacJwh1GDQZyBhQEVAAAU0QyL', '态': 'eNrjF2SYwMDAoMHQ8P//f4YJIgwMEhIMDaoSDLIaGgsYWSxYOhlZIhoXOgQxLDBQaVBk+KvPgAYA9vQMkA==', '放': 'eNrjF2xglGA4wMB84P98BgbmP/UHGpiYP8RPYuCZrMJwyIBtgsINBgkOHQbnJq6ECk1dlkZGTgZUAAByUQ6a', '预': 'eNrjF2QAgv/f/zNPcGBy4ExguND43/1/divjjMgkBrVpEg2NqmwJCa2MEjM0GdhUOK9MYOKVkGBABgCufBBj', '告': 'eNrjF2RoYGS4oMDAIMHB4PD/P3dBAwMDgwLDgf///wMZDA1AKoHBgEGCgYfh33/mBsYDDGgAAE54Dzs=', '消': 'eNrjF1RgkGEo4WxmEjilwsBgyKTx5T+3CacDk8P//QwTGFQc1Bg4Df7+Z/JkmnAByBVRaWBnQAUACRMNog==', '息': 'eNrjF2RQYGB48N+egYOBh+Hff+YGhgPIXIZWBoaeiRMckgSWGEg4JLP8rWdAAwBwHA/Y', '检': 'eNrjFzzAwMGg4MDMwKHA9EXfjWlCggN7xa96pjwGhgliIhw9CR5MigppDBwSHAzMDowHfvznZ0AFANkYDEo=', '查': 'eNrjF2Q4wMDAYMDQ8P//foaEdAaHYwsY5QwmOP/4z83AxMAMFGZQAEr/+M8PVAakIDQSAAC0RBG9', 'a': 'eNrjEGSAgyfyMhUMBRPq9pTylsjW/lgGEwcAckQHGQ==', 'b': 'eNrjEGQAAhkwXCNdYyGzQWYBEBbUWNyRZoACAG6HBmQ=', 'c': 'eNrjEGSAgwfSGiayDLxAKMugYfJAGiYOADYbAxw=', 'f': 'eNpjEWRgeKKh8V8DAhiAAAAn2QNh', 'g': 'eNrjEGSAgwflGhW8BRCoUfGgnKFAxuIJNwMDAGx8B68=', 'i': 'eNpjFmQ4wMhwvFAGiBgYGAAczwN+', 'j': 'eNpjFmQ4wMhwvFAGiK7rMAAAH6wEgQ==', 'k': 'eNrjEGQAAhkI1JHhvsP0hymHXYZLRkbGggEKAEihA94=', 'l': 'eNpjFmQ4UChzHIwYGBgAIrcECg==', 'n': 'eNrjEGSAgznyNRYyBQgIEwcAUtgE0Q==', 'p': 'eNrjEGSAgznSNRYyG2QWAGFBjcUaaRkGEGRgAABj7wYk', 'q': 'eNrjEGSAgwflGhW8JbwFIKhR8aCcoQAMGQBq1wdQ', 'r': 'eNpjFWQAgw3nhdllCg4wsstA+AApQgMg', 't': 'eNpjEWRgENDQ+KcBBl8YgAAAH10DWA==', 'u': 'eNrjEGSAA5kCOCzRqHhSDhMHAFOiBWE=', 'w': 'eNrjFmRABg1O7KseHDRdYyJjzjXrjqO01QF37gcXGJFUAADdtQly', 'y': 'eNrjEGSAA74CWRUdCRPeAq4F7BeYDzAdYJzAUAMUBwBLoQXh', 'z': 'eNrjEGSAg7/2DLIM3A1MDxhKgIx/9TBxAFFjBO4=', 'B': 'eNrjEmQAgT/1B5hkdRoOMWnoTDjwX17nwmEmEx2Hw0y6f/YzIAAAJr0Ltg==', 'H': 'eNrjFmQAgQamBQsYNDQYuLgampgWPPivD2NCRBkQAAD50QjP', 'J': 'eNrjEGQAARlUyCkjy/eFjQEKACR1AkQ=', 'M': 'eNrjFWQAgwPsDez6DHJvGCoL2G4cVDVgPz5BRqatsEDl8MGLXOzs8jIyFgUMKAAAkj8MEA==', 'P': 'eNrjEmQAgT/7DzJZ6DgcZyrRmXD4P7cOwwEmBiiBBAAC9wkM', 'Q': 'eNrjEWQAA4d9jAsc2S0YeGUYdGQZNHgZLGQZLIBsiwTZBRe5HfbpMTCIMMAAAMSmByo=', 'S': 'eNrjEmQAgQn5DuyyOg1NTAxf2Bh+cjNcYGYokXVoZteckM+AAADKxggD', 'V': 'eNrjEmQAAS6GWwwFOg3NzDolFxi5uS4UMMQwN2gxfGFk4GdAAADCDAd1', 'X': 'eNrjEmQAAR0Hd3adBwsYcpgd5Bk+MDToM8Syb3jQyKTD58DNAAcAxlgH6w==', 'Y': 'eNrjEmQAAT6G00wWJRcY+bgPaDF8YWTgZljAwMAFIRAAAKB1Bcs=', 'Z': 'eNrjFGQAggP/9RlKGAoYFjA8YLjAeICxgamB2YGd4d9/JgYYAADX7AlK', '_': 'eNrjEGTAC/7/Z2AAAAqQAhg=', '/': 'eNpjEWRgODAhocBARYKHk42dAQgAJAcCxA=='}), '34': NativeFont('0123456789-?', 'O70BqL0B7r0BWb4BzL4BM78Bp78BHcABd8AB7MABYMEBe8EB3MEB', _FONT_PATH), '20': NativeFont('?ADEFGIKLNORTUW', '3MEBEMIBVsIBi8IBxsIB48IBJsMBO8MBgMMBl8MB2sMBEsQBU8QBa8QBl8QB8cQB', _FONT_PATH, {'重': 'eNoTEWOAgqeMB/7/X83IwKDBwPDj////+mAWkHBgdAByGR2YNRqQWXAuWB1Q739mmA64AZgAAGRzHjI=', '置': 'eNoTEWOAgwv///9nvtDgyAQlQFwGBhkGhi9AljwDAydQDZDFwMDOMAHE2o+b9QOoTp8BCwAAAzgg4A==', '信': 'eNoTEWOQWMDAwCDbwMTAwAWEDsxAXsPn///rL4BYD0DEFub//9kzQCwHCAHkOqBzmRnYoASI6wxmMTKgAwAHshOv', '息': 'eNoTEWNgYGMAAnYgdmACEv/+/2dg4GK4gMzCyVWRcGA4YLKlgWmByQFGrgIThgAdDROGBSYsD/7nM2ADAAd1GZM=', '暂': 'eNoTEWOQYAACXoYALgauA6tZf/w/z8jQUNTAyDChoOG//of/pw8yChQcOMDIsPXWAcYvVQVAVoEKkBBgcGBw+P//P6MDM0MDlEBw4SwGVAAANIUh5w==', '无': 'eNoTEWOAgwP///9nZGDgBbERBCeU+AKU5WZguAbiXgZih0sgLReBxIMLQKIERMhdYNCYwH6BUfcHY8N/bgUGTAAAl+QUrA==', '有': 'eNoTEWNgkGAAAlkQwcXA8OX////2DA1MQN4BRiCxACQOFGRisAWKOlgDiQcfgdwSAyBLAEQwfADJglnIxBMmBhOGJQxoAADDlBfa', '效': 'eNoTEWNgY+BkYOBl4GNg0GDgYgACdoa///OZgYyG//YOkw8wsh9ovMTAvIBhtyOzhUNqM1NIA1M7Y8NlhpMMDHkMaxgYLBhKGBjWMuxhcGhnOsX0gPGyE3cNw04GGxYGCQYBBlQAAFsCF0g=', '消': 'eNoTEWNgYGBkcGBg4OK40MTEJscgx+7GzsAie42RgYHFjSHF4f9//gdOzAy8Dt5AgsEBTADFGCJArFgQ4QXiugNZDSB1FxyBxAYH5gA+AQfmA9wMqAAAVVASqg==', '卡': 'eNoTEWOAAHZU4v9/RjSxH////9dnYGADccNAxDUmIMHGByIqQMQFoA42BwaoErA2bgZ0AABvDglz', '即': 'eNoTEWOAgwv/5f/LX2CQYZNBIv7Lo3CRxBiARAMjkHBgBxH8YXwXdupeY38gbcLGsAEoCzKSnQENAADKXBdR', '将': 'eNoTEWMAAx0QIcEQwMcgsT+L1USCgUlmgQQXF+8BSRkedgdJlRYmoGwDI5A4wOAgBSQO3Pz/336CBMcBhgIJjQMMGhILDjCISDQcYAArARIvQYZmMqABAF9RFBw=', '发': 'eNoTEWOAAA0WBgZZHV0GBi6ZLQwM7LwXGByYuBgYDvz//1/ewYEZqKKBiQHMZ2J4wHiBgaGEeQNQB58FgwO3Bh/DBaYP7Aw7GJ6wMwg46NUyMvxkeiDPADSNkwEVAAC8lRTM', '放': 'eNoTEWMAA04GLgYGWQZ2BgYTB2aGH//nMzEwsB/4b8/A/oCBmYG9BCj4f7oTMwOzuxMTkGgGEszHGR2YmG8xNDAxr2U44MhswXDBgXkvQ8GUpuvsOju+MuqxMGgwiDCgAgCmKxUt', '已': 'eNoTEWOAgwWsDEsZPvz/vx/MQSIegIgFYBZIdgGcuwBFJSuI4LrAyODA3gBUx8iABQAAkYIWxA==', '确': 'eNoTEWOAAjYgdmBm+PD/8H92Bs4FDYwMbBYLGBiYV/6XbmAyUFG58N9QReXLISCx5NDH//oNIBaU+Ark/ldlALIkgUQTL4MKAwO3gyYDA1ODPAMqAADyWh1a', '认': 'eNoTEWMAAQcmB3YGB24GdgYGHRChAiIYIAQzwx92EIsdKM/A7MAHJBokgcQBVQYG5wkGDAyXExcwMFSbHGB0kJdpYG5g52LgdmhkYtBlmMDAwMKACgBjfAwE', '额': 'eNoTEWPgZAACGRCh8eC//Y//8ioMKqwSKgwa7ECiYdXx//ILGg4FyFRcP1Agw7DngIGMw6zjBjJPGE8ayFz5f9hA5gDjYRUg0RzBcoCRuZfpwH9ndq4DjM8ZNRwYUhgEGFABAGbvH3g=', '度': 'eNoTEWOAAA0QUcLAcOH////yF0CcCw7MJgwHHJhUGA78+/+f/4AjiOUwSY/hAEj2wJf//0FKChguMHBpMExg0OFjWMDwgZmhgGFnNIPJL+6f8ipZDA6SDKgAANICHLI=', '预': 'eNoTEWOAgx////7XZ1jAIMPAoMHAw3CBj4GLwYH7y39uBgkTBq6//80luRi6zHi5GG6Z8HAxcEEIXjjBxcrA5XCdkYHrghP3A/Y6Bp0AoH4BBlQAAN34EqQ=', '计': 'eNoTEWMAgQkMDFwMB5iABAM3iJABESwgAiTx5/j///Ys7CAuVoIRSFxnAhLVQK6DPIhgh+lFAwBC4gh7', 'C': 'eNrjE2NAgAn7GRv+72f491//C/ePZn0GqT8MDAf4GRhgFCNDA1AQKMcOVAJSCdTAgAIAhMYVtg==', 'o': 'eNrjEWPABhz2MT74z/2lSf8Pg90ehtq9DLVAEsgGigDFgbLIigHI/Q9f', 'd': 'eNrjEWNAADs4asi3+/Df/keT/R8G+z0gZAckgWygCFAcKIukiwEAA6AQqQ==', 'e': 'eNrjFmPAAv4xffjPXPflEPePL//r//6P12c4wM3wwN3+w3/2B/nICgHXoRC9', 'x': 'eNrjFmPABF+Y63884v7zl+GvPMM/Jod6hg/2DP/0L5yX/3OxX/8BN5JKALf5D+E=', ' ': 'eNpjE2MgBAAD4QAd', '正': 'eNoTEWOAgwP///9nZ2AoALFRCUkQwQsmPvxnhrJwEH+ApthnMDI4aDJgAQDwhxCs', '在': 'eNoTEWNg4GQAAi4Qwc7A8OD////6DA2MQN4FkFABAzcDgwZIRhZIOMgDiQdx//9z75BhZGcQkAFJoBF3/v+vB7IYGCAEKgAAyBUPJg==', '执': 'eNoTEWMAA74GJgYGLhTiz/9//xmBrEYo4dVwkJFhFZBw4D51kPEL95cDjDpcFw4BZTecBhImR4CE7AFBBi/uA4YPHjE1yDNMYMAAAOxRGfY=', '行': 'eNoTEWOQYAACXRDB/fc/ewMTkLEExFNJAJEaIIIXRLT//19/gJ3BgGELiNAAEQy4iQY7ECHFgAYANfwMGw==', '更': 'eNoTEWOAgw////+XZ2AwAbGBhAOQy+TAbNIAJbBwGVhVgGplNYDEE1kg4ZAPJC48i2ZYUsfwU38LKwODKAMqAAA2rRmH', '新': 'eNoTEWMAAhYGLoYJ8gwaDvkMP/7XMzMcYHBmZnBwYmJmYDjMwAwU0/9fL6Ih2szEoMIAJb78jweyshyamByWNzUxXVA91shUonK3gUlGZUEDE4OGBZBQkQQSqAAA9REZgQ==', '·': 'eNpjF2NAA3sY9A9wM2AAAClIAdQ=', '请': 'eNoTEWMAgQkMDNwMB5gY2BgYuv//52fgBLEY/v1nB5JsDCsO/f9v78IMZDswgQmghAMTFwM7EksJxNIGshq4gcQBJq4AoF6uC0wMqAAAJPYVHg==', '勿': 'eNoTEWNIYACCAhBhAiL+/P8vz8DLy8vDwM7Jy9PAxMbJ84CBmYs3AMjkZTjAwM7LsMGBmZdB4wAjrwP3AwbeB0wlDLwBDLIMvAwN7AxcDA8cp7EzMDjsZ2TAAgD62hBO', '断': 'eNoTEWMAgwUMDmwhC2fsZ1NZ3MPIoLKsC0isbAYSCxoYGZb8n/9fX2VFw0FGlRXNQGJlF5BYPOcgY8qiKQcZlyw8AOQuOHCAUSXgwgHGH/9XH2AUYSg5wMjAoAIiUAAAHxAjbQ==', '电': 'eNoTEWOAAG4Qwc7AIAAiPvz/v5/BgIF9ARJBUCwAbAAniOBlYOByAJr48z8TAyoAANHZFDY=', '！': 'eNoTEWNAAhqkECJwAk0vPgAAk2YCCw=='}), '124': NativeFont('0123456789?', '8cQBwcUBJMYBKscBV8gBFskB8MkB8coBeMsBjMwBic0BbM4B', _FONT_PATH), '22': NativeFont('0123456789?', 'bM4Bis4Bo84B0s4B/s4BKs8BVc8BgM8Brc8B0s8B/c8BJ9AB', _FONT_PATH), '26': NativeFont('0123456789-°C?', 'J9ABbNABlNAB3tABLdEBdNEBw9EBEtIBUtIBodIB8NIBBdMBItMBfdMBwdMB', _FONT_PATH), '19': NativeFont('0123456789-?周一二三四五六日', 'wdMB9dMBGtQBS9QBf9QBsdQB5dQBGdUBR9UBe9UBr9UBwdUB8dUBStYBZNYBf9YBoNYB7NYBNtcBeNcBqtcB', _FONT_PATH)}
# END SMOOTH FONT DATA

class EmbeddedDisplay(Display):
    def __init__(self, canvas):
        self.c = DimmableCanvas(canvas)
        self.font = EMBEDDED_FONT
        self.local_clock = SyncedClock()

    def render(self, packet, now=None, online=True, live=False):
        if now is None:
            self.local_clock.accept(packet)
            now = self.local_clock.now()
        return Display.render(self, packet, now=now, online=online, live=live)


class SyncedClock:
    """Keep time locally and only reset the base when a sync packet requests it."""
    def __init__(self):
        self.base_ts = None
        self.base_ticks = 0
        self.host_synced = False

    def sync(self, unix_seconds, trusted=True):
        self.base_ts = int(unix_seconds)
        self.base_ticks = ticks_ms()
        if trusted:
            self.host_synced = True

    def accept(self, packet):
        # The first valid host packet after every boot must establish the
        # clock even if the host has not yet noticed the reset. Later packets
        # only adjust it when the regular two-hour sync flag is present.
        if not self.host_synced or packet.get("time_sync"):
            self.sync(packet["ts"])

    def now(self):
        if self.base_ts is None:
            return 0
        return self.base_ts + ticks_diff(ticks_ms(), self.base_ticks) // 1000


def ticks_ms():
    native = getattr(time, "ticks_ms", None)
    return native() if native else int(time.monotonic() * 1000)


def ticks_diff(new, old):
    native = getattr(time, "ticks_diff", None)
    return native(new, old) if native else new - old


def load_device_settings(path=SETTINGS_PATH):
    """Read locally persisted screen settings from K210 flash."""
    result = {"flip": False, "brightness": DEFAULT_BRIGHTNESS,
              "show_codex": True, "bluetooth_enabled": False, "reset_seen": ""}
    try:
        with open(path, "r") as settings_file:
            settings = json.load(settings_file)
        if settings.get("v") == 1 and isinstance(settings.get("flip"), bool):
            result["flip"] = settings["flip"]
        if isinstance(settings.get("show_codex"), bool):
            result["show_codex"] = settings["show_codex"]
        if isinstance(settings.get("bluetooth_enabled"), bool):
            result["bluetooth_enabled"] = settings["bluetooth_enabled"]
        if valid_reset_key(settings.get("reset_seen", "")):
            result["reset_seen"] = settings.get("reset_seen", "")
        brightness = settings.get("brightness")
        if (isinstance(brightness, int) and not isinstance(brightness, bool)
                and 20 <= brightness <= 100):
            result["brightness"] = brightness
    except (OSError, ValueError, TypeError, AttributeError):
        pass
    return result


def load_flip_setting(path=SETTINGS_PATH):
    return load_device_settings(path)["flip"]


def load_brightness_setting(path=SETTINGS_PATH):
    return load_device_settings(path)["brightness"]


def save_device_settings(flip, brightness, path=SETTINGS_PATH, show_codex=None,
                         bluetooth_enabled=None, reset_seen=None):
    try:
        if show_codex is None or bluetooth_enabled is None or reset_seen is None:
            previous = load_device_settings(path)
            if show_codex is None:
                show_codex = previous["show_codex"]
            if bluetooth_enabled is None:
                bluetooth_enabled = previous["bluetooth_enabled"]
            if reset_seen is None:
                reset_seen = previous["reset_seen"]
        if not valid_reset_key(reset_seen):
            return False
        settings = {"v": 1, "flip": bool(flip), "show_codex": bool(show_codex),
                    "bluetooth_enabled": bool(bluetooth_enabled), "reset_seen": reset_seen,
                    "brightness": max(20, min(100, int(brightness)))}
        with open(path, "w") as settings_file:
            settings_file.write(json.dumps(settings))
        return True
    except (OSError, ValueError, TypeError):
        return False


def save_flip_setting(value, path=SETTINGS_PATH):
    """Persist a direction change; writes occur only when the value changes."""
    current = load_device_settings(path)
    return save_device_settings(value, current["brightness"], path)


def save_brightness_setting(value, flip=False, path=SETTINGS_PATH):
    return save_device_settings(flip, value, path)


def apply_backlight(lcd_module, brightness):
    """Try the firmware hook; older CanMV K210 builds implement it as a no-op."""
    setter = getattr(lcd_module, "set_backlight", None)
    if setter is None:
        return False
    try:
        setter(max(20, min(100, int(brightness))))
        return True
    except Exception:
        return False


def point_in_rect(x, y, rect):
    left, top, width, height = rect
    return left <= x < left + width and top <= y < top + height


def map_touch_point(x, y, flipped):
    """Map the NS2009's normal panel coordinates into the visible UI."""
    x, y = int(x), int(y)
    if flipped:
        x, y = 319 - x, 239 - y
    return max(0, min(319, x)), max(0, min(239, y))


class TouchInput:
    """Small edge-triggered wrapper around CanMV's touchscreen module."""
    def __init__(self, module, i2c):
        self.module = module
        self.i2c = i2c  # Keep the bus alive for firmwares that do not retain it.
        self.down = False
        self.last_tap_ticks = 0

    def poll_tap(self, flipped, sync_circle=None):
        try:
            status, x, y = self.module.read()
        except Exception:
            self.down = False
            return None
        pressed = status in (self.module.STATUS_PRESS, self.module.STATUS_MOVE)
        tap = None
        now = ticks_ms()
        if pressed and not self.down and ticks_diff(now, self.last_tap_ticks) >= 180:
            tap = map_touch_point(x, y, flipped)
            self.last_tap_ticks = now
        if tap is not None and sync_circle is not None:
            # The reset entry takes priority where it overlaps the sync circle.
            if (not point_in_rect(tap[0], tap[1], RESET_HITBOX)
                    and (tap[0]-sync_circle[0])**2 + (tap[1]-sync_circle[1])**2 < sync_circle[2]**2):
                tap = "sync"
        self.down = pressed
        return tap


def init_touch():
    """Initialize the CanMV/NS2009 touch controller without blocking startup."""
    try:
        import touchscreen
        from machine import I2C
        i2c = I2C(I2C.I2C0, freq=400000, scl=TOUCH_SCL, sda=TOUCH_SDA)
        touchscreen.init(i2c)
        print("Touchscreen ready on I2C0: SCL=%d SDA=%d" %
              (TOUCH_SCL, TOUCH_SDA))
        return TouchInput(touchscreen, i2c)
    except Exception as exc:
        print("Touchscreen unavailable:", exc)
        return None


def make_boot_id(path=BOOT_ID_PATH):
    """Persist a boot counter: some K210 PRNGs repeat after every reset."""
    try:
        previous = 0
        try:
            with open(path) as boot_file:
                previous = json.loads(boot_file.read())
            if not isinstance(previous, int) or isinstance(previous, bool):
                previous = 0
        except (OSError, ValueError, TypeError):
            previous = 0
        current = (previous + 1) & 0x3fffffff
        with open(path, "w") as boot_file:
            boot_file.write(json.dumps(current))
        return current
    except OSError:
        pass
    try:
        import urandom
        return int(urandom.getrandbits(30))
    except Exception:
        try:
            raw = os.urandom(4)
            return int(binascii.hexlify(raw).decode("ascii"), 16) & 0x3fffffff
        except Exception:
            return (ticks_ms() ^ id(object())) & 0x3fffffff


class DeviceInfo:
    """Accumulate short tick deltas so uptime and receive age survive tick wrap."""
    def __init__(self, path=PROGRAM_INFO_PATH):
        self.path = path
        self.last_tick = ticks_ms()
        self.uptime_ms = 0
        self.last_state_ms = None
        self.last_sync_data = None
        self.updated_at = None
        self.started_at = None
        try:
            with open(path) as stream:
                saved = json.loads(stream.read())
            value = saved.get("started_at")
            if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                self.started_at = value
            if saved.get("version") == PROGRAM_VERSION:
                value = saved.get("updated_at")
                if isinstance(value, int) and not isinstance(value, bool) and value > 0:
                    self.updated_at = value
        except (OSError, ValueError, TypeError, AttributeError):
            pass

    def save_info(self, started_at, updated_at):
        # Replace atomically so a power loss cannot erase the saved start time.
        try:
            with open(self.path + ".tmp", "w") as stream:
                stream.write(json.dumps({"version": PROGRAM_VERSION,
                                         "started_at": started_at, "updated_at": updated_at}))
            os.rename(self.path + ".tmp", self.path)
            return True
        except OSError:
            return False

    def advance(self):
        now = ticks_ms()
        self.uptime_ms += max(0, ticks_diff(now, self.last_tick))
        self.last_tick = now

    def receive_state(self, packet):
        if packet.get("type") == "heartbeat":
            return
        self.advance()
        # Transport timestamps, UI echoes and Codex activity are not data syncs.
        fields = ("offset", "city", "temp", "condition", "weather_stale",
                  "q5", "q7", "r5", "r7", "stale")
        data = {key: packet[key] for key in fields if key in packet}
        if packet.get("time_sync") or (data and data != self.last_sync_data):
            self.last_state_ms = self.uptime_ms
        if data:
            self.last_sync_data = data
        value = packet.get("program_updated_at")
        if (packet.get("program_version") == PROGRAM_VERSION
                and isinstance(value, int) and not isinstance(value, bool)
                and value > 0 and value != self.updated_at):
            if self.save_info(self.started_at, value):
                self.updated_at = value

    def snapshot(self, clock):
        self.advance()
        now = clock.now() if clock.host_synced else None
        if now is not None and self.started_at is None:
            started_at = now - self.uptime_ms // 1000
            if started_at > 0 and self.save_info(started_at, self.updated_at):
                self.started_at = started_at
        return {"version": PROGRAM_VERSION, "uptime_ms": self.uptime_ms,
                "total_elapsed_ms": (max(0, now - self.started_at) * 1000
                                     if now is not None and self.started_at is not None else None),
                "rx_age_ms": None if self.last_state_ms is None else self.uptime_ms - self.last_state_ms,
                "boot_ts": now - self.uptime_ms // 1000 if now is not None else None,
                "updated_at": self.updated_at}


class SerialStateReceiver:
    """Receive bounded newline-delimited state packets and acknowledge them."""
    def __init__(self, max_line=2048):
        self.buffer = b""
        self.max_line = max_line
        self.last_receive_ticks = None
        self.boot_id = make_boot_id()
        self.show_codex = True
        self.sync_request = 0
        self.program_update = 1
        self.bluetooth_enabled = False
        self.reset_seen = ""

    def feed(self, data):
        if not data:
            return []
        self.buffer += data
        packets = []
        while True:
            newline = self.buffer.find(b"\n")
            if newline < 0:
                if len(self.buffer) > self.max_line:
                    self.buffer = b""
                break
            line, self.buffer = self.buffer[:newline], self.buffer[newline + 1:]
            if not line or len(line) > self.max_line:
                continue
            try:
                packet = json.loads(line.decode("utf-8"))
            except (ValueError, TypeError, UnicodeError):
                continue
            if self.valid(packet):
                packets.append(packet)
        return packets

    @staticmethod
    def valid(packet):
        if not isinstance(packet, dict):
            return False
        if packet.get("v") != 1:
            return False
        packet_type = packet.get("type")
        if packet_type == "update_begin":
            size, digest = packet.get("size"), packet.get("sha256")
            return (isinstance(size, int) and not isinstance(size, bool)
                    and 0 < size <= PROGRAM_UPDATE_MAX_BYTES
                    and isinstance(digest, str) and len(digest) == 64
                    and packet.get("target", "auto") in ("auto", "flash", "sd"))
        if packet_type == "update_chunk":
            return (isinstance(packet.get("index"), int)
                    and packet["index"] >= 0
                    and isinstance(packet.get("data"), str)
                    and len(packet["data"]) <= 1100)
        if packet_type in ("update_end", "update_abort"):
            return True
        if packet_type == "heartbeat":
            seq = packet.get("seq")
            return isinstance(seq, int) and not isinstance(seq, bool) and seq >= 0
        if packet_type != "state":
            return False
        if not isinstance(packet.get("seq"), int) or packet["seq"] < 0:
            return False
        required = ("ts", "offset", "city", "condition", "status", "model",
                    "stale", "weather_stale", "demo", "q5", "q7", "r5", "r7")
        return all(key in packet for key in required) and valid_reset(packet.get("reset"))

    def poll(self, uart):
        latest = None
        data = uart.read()
        for packet in self.feed(data):
            latest = packet
            self.last_receive_ticks = ticks_ms()
        return latest

    def view_reset(self, state, page, now):
        event = state.get("reset")
        if (not event or not valid_reset(event) or event["status"] != "confirmed"
                or not event.get("key") or now > event["expires_at"]):
            return False
        changed = (page == "reset" or event.get("seen", False)) and self.reset_seen != event["key"]
        if changed:
            self.reset_seen = event["key"]
        if self.reset_seen == event["key"]:
            event["seen"] = True
        return changed

    def _reply(self, uart, kind, flip, page, brightness, info, seq=None):
        message = {"v": 1, "type": kind, "flip": flip, "page": page,
                   "brightness": brightness, "program_update": self.program_update,
                   "boot_id": self.boot_id,
                   "heartbeat": 1, "show_codex": self.show_codex,
                   "bluetooth_enabled": self.bluetooth_enabled,
                   "reset_seen": self.reset_seen,
                   "sync_request": self.sync_request}
        if seq is not None:
            message["seq"] = seq
        if info is not None:
            message["device_info"] = info
        uart.write((json.dumps(message) + "\n").encode("utf-8"))

    def acknowledge(self, uart, seq, current_flip, page="dashboard",
                    brightness=DEFAULT_BRIGHTNESS, info=None):
        self._reply(uart, "ack", current_flip, page, brightness, info, seq)

    def report_ui(self, uart, current_flip, page,
                  brightness=DEFAULT_BRIGHTNESS, info=None):
        self._reply(uart, "ui", current_flip, page, brightness, info)

    def online(self):
        return (self.last_receive_ticks is not None and
                ticks_diff(ticks_ms(), self.last_receive_ticks) < 15000)



def open_gpio_uart(UART):
    """Map P7/P6 to independent UART1 for an HC-05 in data mode."""
    from fpioa_manager import fm
    fm.register(GPIO_UART_TX, fm.fpioa.UART1_TX, force=True)
    fm.register(GPIO_UART_RX, fm.fpioa.UART1_RX, force=True)
    return UART(UART.UART1, GPIO_UART_BAUD, 8, None, 1,
                timeout=0, read_buf_len=4096)


def run_device():
    device_info = DeviceInfo()
    import lcd
    import image
    import gc
    import machine
    from machine import UART

    gc.collect()
    print("Free heap before LCD:", getattr(gc, "mem_free", lambda: -1)())

    print("[1/3] Initializing LCD...")
    lcd.init()
    base_rotation = 1 if lcd.width() == 240 and lcd.height() == 320 else 0
    local_settings = load_device_settings()
    flipped = local_settings["flip"]
    brightness = local_settings["brightness"]
    show_codex = local_settings["show_codex"]
    bluetooth_enabled = local_settings["bluetooth_enabled"]
    lcd.rotation((base_rotation + 2) % 4 if flipped else base_rotation)
    print("LCD:", lcd.width(), lcd.height())
    if lcd.width() != 320 or lcd.height() != 240:
        raise ValueError("Expected 320x240 LCD; check vendor LCD initialization")

    print("[2/3] Creating image and opening selected serial port...")
    gc.collect()
    img = globals().get("_screen_image")
    if img is None:
        img = image.Image(size=(320, 240))
    display = EmbeddedDisplay(img)
    display.set_brightness(brightness)
    apply_backlight(lcd, brightness)
    uart = UART.repl_uart()
    uart.init(115200, 8, None, 1, timeout=0, read_buf_len=4096)
    gpio_uart = None
    if bluetooth_enabled:
        try:
            gpio_uart = open_gpio_uart(UART)
            print("P7 TX / P6 RX UART1 ready at", GPIO_UART_BAUD, "baud")
        except Exception as exc:
            print("P7/P6 UART unavailable, using USB:", exc)
            bluetooth_enabled = False
            save_device_settings(flipped, brightness, show_codex=show_codex,
                                 bluetooth_enabled=False)
    active_uart = gpio_uart if bluetooth_enabled else uart
    receiver = SerialStateReceiver()
    receiver.show_codex = show_codex
    receiver.bluetooth_enabled = bluetooth_enabled
    receiver.reset_seen = local_settings["reset_seen"]
    try:
        from program_update import ProgramUpdater
        updater = ProgramUpdater()
    except ImportError:
        updater = None
        receiver.program_update = 0
        print("program_update.py is missing; display remains available")
    touch = init_touch()
    # Show a useful standby screen until the first live packet arrives.
    state = {"ts": 946656000, "offset": 480, "city": "南京",
             "temp": 26, "condition": "多云", "q5": None, "q7": None,
             "r5": None, "r7": None, "status": "UNKNOWN", "flip": flipped,
             "model": "--", "stale": True, "weather_stale": False,
             "demo": True, "show_codex": show_codex}
    print("[3/3] Waiting for K210 Codex Display Hub packets at",
          GPIO_UART_BAUD if bluetooth_enabled else 115200, "baud.")
    gc.collect()
    print("Free heap before drawing:", getattr(gc, "mem_free", lambda: -1)())
    display.local_clock.sync(state["ts"], trusted=False)
    page = "dashboard"
    # The device's flash setting wins on first connection. Once the host echoes
    # it back, later host-side changes are accepted normally.
    awaiting_flip_echo = True
    awaiting_ui_echo = True
    last_frame = None
    pending_ui_ticks = None
    upload_activity = None
    last_update_draw = None
    while True:
        device_info.advance()
        packet_uart = active_uart
        packet = receiver.poll(packet_uart)
        if packet is not None:
            if packet.get("type") == "heartbeat":
                receiver.acknowledge(
                    packet_uart, packet["seq"], flipped, page, brightness,
                    device_info.snapshot(display.local_clock))
                packet = None
        if packet is not None and updater is not None and updater.handle(packet_uart, packet):
            upload_activity = ticks_ms()
            last_frame = None
            packet = None
        # Give the stop-and-wait upload priority over expensive LCD rendering.
        # An interrupted upload must not leave the touchscreen frozen forever.
        if updater is not None and updater.output is not None:
            if ticks_diff(ticks_ms(), upload_activity) < 15000:
                if last_update_draw is None or ticks_diff(ticks_ms(), last_update_draw) >= 250:
                    percent = updater.received * 100 // max(1, updater.expected_size)
                    display.render_program_update(percent)
                    if display.frame_changed:
                        lcd.display(img)
                    last_update_draw = ticks_ms()
                time.sleep_ms(1)
                continue
            updater.abort()
            last_frame = None
        if updater is not None and updater.reset_at is not None:
            display.render_program_update(100)
            if display.frame_changed:
                lcd.display(img)
            if updater.restart_due():
                machine.reset()
            time.sleep_ms(1)
            continue
        last_update_draw = None
        if packet is not None:
            received_page = packet.get("page", page)
            if received_page not in ("dashboard", "settings", "info", "reset"):
                received_page = page
            received_brightness = packet.get("brightness", brightness)
            if (not isinstance(received_brightness, int)
                    or isinstance(received_brightness, bool)
                    or not 20 <= received_brightness <= 100):
                received_brightness = brightness
            received_codex = packet.get("show_codex", show_codex)
            if not isinstance(received_codex, bool):
                received_codex = show_codex
            if awaiting_ui_echo:
                if (received_page == page and received_brightness == brightness
                        and received_codex == show_codex):
                    awaiting_ui_echo = False
                    pending_ui_ticks = None
            else:
                if received_codex != show_codex:
                    show_codex = received_codex
                    receiver.show_codex = show_codex
                    save_device_settings(flipped, brightness, show_codex=show_codex)
                    last_frame = None
                if received_page != page:
                    page = received_page
                    last_frame = None
                if received_brightness != brightness:
                    brightness = received_brightness
                    save_device_settings(flipped, brightness)
                    display.set_brightness(brightness)
                    apply_backlight(lcd, brightness)
                    last_frame = None
            received_flip = bool(packet.get("flip", flipped))
            if awaiting_flip_echo:
                packet["flip"] = flipped
                if received_flip == flipped:
                    awaiting_flip_echo = False
            else:
                packet["flip"] = received_flip
                if received_flip != flipped:
                    if save_flip_setting(received_flip):
                        print("Saved screen flip:", received_flip)
                    else:
                        print("Warning: could not save screen flip")
            packet["show_codex"] = show_codex
            state = packet
            display.local_clock.accept(packet)
            device_info.receive_state(packet)
        want_flip = bool(state.get("flip", flipped))
        if want_flip != flipped:
            # Turning the panel keeps the renderer identical on both sides.
            flipped = want_flip
            lcd.rotation((base_rotation + 2) % 4 if flipped else base_rotation)
            last_frame = None
        sync_circle = (239, 158, 38) if page == "dashboard" and show_codex else None
        tap = touch.poll_tap(flipped, sync_circle) if touch is not None else None
        if tap == "sync":
            receiver.sync_request += 1
            receiver.report_ui(active_uart, flipped, page, brightness)
            tap = None
        ui_changed = False
        if tap is not None:
            x, y = tap
            if page == "dashboard" and point_in_rect(
                    x, y, WEATHER_HITBOX if show_codex else CLOCK_SETTINGS_HITBOX):
                page = "settings"
                last_frame = None
                ui_changed = True
            elif page == "settings" and point_in_rect(x, y, INFO_HITBOX):
                page = "info"
                last_frame = None
                ui_changed = True
            elif page == "info" and point_in_rect(x, y, BACK_HITBOX):
                page = "settings"
                last_frame = None
                ui_changed = True
            elif page == "reset" and point_in_rect(x, y, BACK_HITBOX):
                page = "dashboard"
                last_frame = None
                ui_changed = True
            elif page == "dashboard" and show_codex and point_in_rect(x, y, RESET_HITBOX):
                page = "reset"
                last_frame = None
                ui_changed = True
            elif page == "settings" and point_in_rect(x, y, BACK_HITBOX):
                page = "dashboard"
                last_frame = None
                ui_changed = True
            elif page == "settings" and point_in_rect(x, y, CODEX_HITBOX):
                show_codex = not show_codex
                state["show_codex"] = show_codex
                receiver.show_codex = show_codex
                save_device_settings(flipped, brightness, show_codex=show_codex)
                last_frame = None
                ui_changed = True
            elif page == "settings" and point_in_rect(x, y, FLIP_HITBOX):
                flipped = not flipped
                state["flip"] = flipped
                awaiting_flip_echo = True
                if save_device_settings(flipped, brightness):
                    print("Saved screen flip from touch:", flipped)
                else:
                    print("Warning: could not save touch screen flip")
                lcd.rotation((base_rotation + 2) % 4 if flipped else base_rotation)
                last_frame = None
                ui_changed = True
            elif page == "settings" and point_in_rect(x, y, BLUETOOTH_HITBOX):
                if not bluetooth_enabled and gpio_uart is None:
                    try:
                        gpio_uart = open_gpio_uart(UART)
                    except Exception as exc:
                        print("P7/P6 UART unavailable:", exc)
                if bluetooth_enabled or gpio_uart is not None:
                    bluetooth_enabled = not bluetooth_enabled
                    receiver.bluetooth_enabled = bluetooth_enabled
                    receiver.report_ui(active_uart, flipped, page, brightness,
                                       device_info.snapshot(display.local_clock))
                    active_uart = gpio_uart if bluetooth_enabled else uart
                    receiver.buffer = b""
                    receiver.last_receive_ticks = None
                    save_device_settings(flipped, brightness, show_codex=show_codex,
                                         bluetooth_enabled=bluetooth_enabled)
                    last_frame = None
                    ui_changed = True
            elif page == "settings" and point_in_rect(
                    x, y, BRIGHTNESS_DOWN_HITBOX):
                brightness = max(20, brightness - 10)
                save_device_settings(flipped, brightness)
                display.set_brightness(brightness)
                apply_backlight(lcd, brightness)
                last_frame = None
                ui_changed = True
            elif page == "settings" and point_in_rect(
                    x, y, BRIGHTNESS_UP_HITBOX):
                brightness = min(100, brightness + 10)
                save_device_settings(flipped, brightness)
                display.set_brightness(brightness)
                apply_backlight(lcd, brightness)
                last_frame = None
                ui_changed = True
        now_second = display.local_clock.now()
        if receiver.view_reset(state, page, now_second):
            save_device_settings(flipped, brightness, reset_seen=receiver.reset_seen)
            last_frame = None
            ui_changed = True
        if ui_changed:
            awaiting_ui_echo = True
        if ui_changed or (pending_ui_ticks is not None and
                          ticks_diff(ticks_ms(), pending_ui_ticks) >= 1000):
            pending_ui_ticks = ticks_ms()
            receiver.report_ui(active_uart, flipped, page, brightness,
                               device_info.snapshot(display.local_clock))
        if packet is not None:
            receiver.acknowledge(packet_uart, packet["seq"], flipped, page, brightness,
                                 device_info.snapshot(display.local_clock))
        if last_frame is None or now_second != last_frame:
            if page == "reset":
                display.render_reset(state, now=now_second, online=receiver.online(), live=True)
            elif page == "info":
                current_info = device_info.snapshot(display.local_clock)
                display.render_info(current_info, state["offset"], online=receiver.online(), live=True)
            elif page == "settings":
                display.render_settings(flipped, brightness,
                                        touch_ready=touch is not None, show_codex=show_codex,
                                        bluetooth_enabled=bluetooth_enabled, live=True,
                                        online=receiver.online())
            else:
                display.render_live(state, now=now_second,
                                    online=receiver.online(), force=last_frame is None)
            if display.frame_changed:
                lcd.display(img)
            last_frame = now_second
        if updater is not None and updater.restart_due():
            time.sleep_ms(100)
            machine.reset()
        time.sleep_ms(20)


if __name__ == "__main__":
    run_device()
