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
PROGRAM_VERSION = "v1.2.0"
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


class Display:
    def __init__(self, canvas, font_path):
        self.c = DimmableCanvas(canvas)
        with open(font_path) as f:
            self.font = json.load(f)

    def set_brightness(self, value):
        self.c.set_brightness(value)

    def text(self, x, y, value, color=WHITE, scale=1, max_width=320,
             crisp=False, background=None):
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
                        if level:
                            self.c.draw_rectangle(x + col, y + row, end - col, 1,
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
                        self.c.draw_rectangle(x + int(col * scale), y + int(row * scale),
                                              int(end * scale) - int(col * scale),
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

    def _begin_frame(self, page, background, live=False):
        self.frame_changed = False
        identity = (page, self.c.brightness)
        if not live or getattr(self, "_page_identity", None) != identity:
            self._regions = {}
            self._page_identity = identity
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
        if self._dirty("status", (p["status"], p.get("model"), online)):
            self.rect(4, 214, 312, 26, CARD)
            status = p["status"] if online else "OFFLINE"
            label = "Codex状态:"
            label_x = 12
            self.text(label_x, 217, label, WHITE, scale=1.5)
            status_scale = 20 / 12
            # Fixed central slot: short and long states share the same center.
            status_left, status_right = 117, 229
            status_width = self.text_width(status, status_scale)
            status_x = status_left + (status_right - status_left - status_width) // 2
            model_space = 308 - (status_right + 5)
            model = str(p.get("model") or "--")
            if self.text_width(model) > model_space:
                suffix = "..."
                while model and self.text_width(model + suffix) > model_space:
                    model = model[:-1]
                model += suffix
            model_x = 308 - self.text_width(model)
            self.text(model_x, 221, model, MUTED, max_width=model_space)
            self.text(status_x, 216, status,
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
            self.text(24, 190, "设备亮度", WHITE, scale=1.25)
            self.text(125, 190, "%d%%" % brightness, BLUE, scale=1.25)
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
        if char in self.extra:
            return self.extra[char]
        if char in self.cache:
            return self.cache[char]
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
SMOOTH_FONTS = {'12': NativeFont(' %-/0123456789:?dhm一三上二云五京余倒六剩北南同周四圳型多天安小州广庆度待态成据数日旧时晴最未杭模步武汉津活海深状知置苏西计跃近郑都重阴雨雪雷雾额itguynK_OLervT.ljzZCBAxQDPSYbHERNpoMGWfwaFkUqJVXsIc更新秒前', 'AAAADAAANQAARAAAWAAAdgAAkgAAsQAAzwAA7gAADAEAKgEASAEAZwEAhQEAlQEAswEA0gEA8QEAFAIAJgIAQgIAXAIAcwIAmgIAwQIA7AIAGQMARQMAcQMAngMAwQMA7gMAFgQAQQQAZQQAiQQAtwQA5AQADQUAOwUAYwUAjgUAqwUA1gUABAYAMgYAYAYAjwYAvQYA7AYABQcAIQcARwcAdQcAnwcAyAcA9gcAJAgATQgAeAgApggA0wgAAQkALwkAXAkAiAkAtgkA4AkACwoANAoAXQoAiwoAuQoA5goAFAsAQQsAbwsAmwsAxgsA8wsAIgwAUAwAZQwAeQwAmAwAtAwA0wwA7wwAFA0AJA0ARw0AXw0Aew0AlA0Arw0Axw0A1w0A6g0A/w0AGg4AOQ4AXA4AfQ4AoQ4AvQ4A4w4ABQ8AIA8AQQ8AXQ8AfA8Amw8AtA8A1w8A/A8AGhAANhAAXRAAgRAAqxAAvhAA3xAA+xAAFREANBEAUREAcBEAjBEArREAzhEA6hEA/REAGRIARBIAchIAnxIAxxIA', _FONT_PATH), '18': NativeFont(' :?Cdeox态状屏幕翻转设备亮度：D7余5剩H01234689-周一二三四五六日', 'xxIA0xIA7BIAGxMAUhMAhRMArhMA2RMAAhQAVBQAqhQAABUAURUApxUAABYAVxYApRYA7BYAPxcAWBcAjxcAvhcADxgAPxgAkxgAuxgA3BgA9hgAHxkARxkAbBkAkxkAvBkA5RkA9RkARRoAWhoAdRoAlhoA2RoAHhsAYRsAjhsA', _FONT_PATH), '24': NativeFont(' %-0123456789:?ACDEFGHIKLNORTUW余剩周设置备信息天津南京晴多云阴雨雪雷雾未知°七万三上丘东个中丰临丹为主丽义乌乐九乡乳二五井亚交亭亳什仁介仓仙令仪们任份伊休会伦佛作佳侗依侯保傈傣僳儋元充光克公六兰共关兴兵内冈冲冶冷凉凌凤凭凯则利力勒匀包化北区十华博卫原厦县双口古句可台合吉同名吐吕启吴员呼和咸哈唐商喀善嘉嘴四回园固国图土地圳坊坝垣城埠基堰塔壁壮夏大太头夷奎如姚威娄嫩子孜孝孟宁安宏定宜宝宣宫家容宾宿密富察寿封射尔尚尼尾屏屯山岑岗岛岩岭岳峡峨峪峰崃崇崖嵊川州巢左巩巴市布常平广庄庆庐库应底店度康廉廊延建开张弥当彝彦彬彭彰征徐德志忠忻怀怒恩惠感慈成房扎扬扶承投抚拉招指掖揭攀敦文斗斯新方施族无日旧旬昆昌明星春昭晋普景暨曲朔朝木本朴来杨杭松林果枝枣柯柳树栖栗株根格桂桃桐桥桦梁梅梧棱楚楞榆樟横武毕民水永汉汕汝江池汨汾沁沂沅沈沙沧河油治泉泊波泰泸泽洛洪洮洱洲流济浏浦浩浮海涟涿淄淖淮深清渠温渭港湖湘湛湾源溧溪滁滋滕满滦滨漠漯漳潍潜潭潮澄澎澳濮灯灵烟焦煌照熟版牙牡特犁狮玉玛环珠珲理琼瑞瓦瓯甘田甸界番登白百皇皋益盐监盖盘盟眉石碑磐祁神祥票禄福禹秦穆穴竹简米红纳绍绥绵罗羌老耒聊肇肥胡胶腾自舒舞舟色节芒芜芝芦芬花苏苗英茂茫荆荔荣荥莆莞莱莲菏萍营萨葛葫蒙蓥藏虎蚌蛟衡衢襄西讷许诸调贝贡贵贺资赣赤车辉辛边辽达迁运远连迪通遂遵邑邓邛邡邢那邮邯邳邵邹郑郭郴郸都鄂酒醴里重金钟钢钦铁铜银错锡锦镇长门间阆阜防阳阿陆陇陵隆随雄雅集霍霞霸青靖鞍韩音韶顶项顺颇额饶首香马驻骅高鲁鲜鸡鸭鹤鹰麻黄黑黔鼎齐龙', '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', _FONT_PATH), '36': NativeFont(' -0123456789:?C', 'rRkBuhkBzhkBJBoBWBoBxBoBOhsBkBsB/xsBfBwBxBwBLx0Bpx0Bux0BJB4BpR4B', _FONT_PATH), '15': NativeFont('?南京天津晴多云阴雨雪雷雾未知DFTRUEKONALWGI连接已程序版本启动时间持续运行最近通信秒前收到待同步0123456789vdhms :.-显示额度屏幕翻转设备亮Coex周一二三四五六日%外蓝牙累计七万上丘东个中丰临丹为主丽义乌乐九乡乳井亚交亭亳什仁介仓仙令仪们任份伊休会伦余佛作佳侗依侯保傈傣僳儋元充光克公兰共关兴兵内冈冲冶冷凉凌凤凭凯则利力勒匀包化北区十华博卫原厦县双口古句可台合吉名吐吕吴员呼和咸哈唐商喀善嘉嘴回园固国图土地圳坊坝垣城埠基堰塔壁壮夏大太头夷奎如姚威娄嫩子孜孝孟宁安宏定宜宝宣宫家容宾宿密富察寿封射尔尚尼尾屯山岑岗岛岩岭岳峡峨峪峰崃崇崖嵊川州巢左巩巴市布常平广庄庆庐库应底店康廉廊延建开张弥当彝彦彬彭彰征徐德志忠忻怀怒恩惠感慈成房扎扬扶承投抚拉招指掖揭攀敦文斗斯新方施族无旧旬昆昌明星春昭晋普景暨曲朔朝木朴来杨杭松林果枝枣柯柳树栖栗株根格桂桃桐桥桦梁梅梧棱楚楞榆樟横武毕民水永汉汕汝江池汨汾沁沂沅沈沙沧河油治泉泊波泰泸泽洛洪洮洱洲流济浏浦浩浮海涟涿淄淖淮深清渠温渭港湖湘湛湾源溧溪滁滋滕满滦滨漠漯漳潍潜潭潮澄澎澳濮灯灵烟焦煌照熟牡特犁狮玉玛环珠珲理琼瑞瓦瓯甘田甸界番登白百皇皋益盐监盖盘盟眉石碑磐祁神祥票禄福禹秦穆穴竹简米红纳绍绥绵罗羌老耒聊肇肥胡胶腾自舒舞舟色节芒芜芝芦芬花苏苗英茂茫荆荔荣荥莆莞莱莲菏萍营萨葛葫蒙蓥藏虎蚌蛟衡衢襄西讷许诸调贝贡贵贺资赣赤车辉辛边辽达迁远迪遂遵邑邓邛邡邢那邮邯邳邵邹郑郭郴郸都鄂酒醴里重金钟钢钦铁铜银错锡锦镇长门阆阜防阳阿陆陇陵隆随雄雅集霍霞霸青靖鞍韩音韶顶项顺颇饶首香马驻骅高鲁鲜鸡鸭鹤鹰麻黄黑黔鼎齐龙', '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', _FONT_PATH, {'次': (15, 17, b'\x00\xc0\x01\x00\x064\x00\x00\x07\nP@\xc3\xff\x1f\x80%\x05\x03@\x87\x91\x00\x04`\x04\x00\x03,\x00`@\n\x00\x0c`\t\x80\x02\n\n \xb4\x00\x1e@\x06\x00\t\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00')}), '34': NativeFont('0123456789-?', 'O70BqL0B7r0BWb4BzL4BM78Bp78BHcABd8AB7MABYMEBe8EB3MEB', _FONT_PATH), '20': NativeFont('?ADEFGIKLNORTUW', '3MEBEMIBVsIBi8IBxsIB48IBJsMBO8MBgMMBl8MB2sMBEsQBU8QBa8QBl8QB8cQB', _FONT_PATH), '124': NativeFont('0123456789?', '8cQBwcUBJMYBKscBV8gBFskB8MkB8coBeMsBjMwBic0BbM4B', _FONT_PATH), '22': NativeFont('0123456789?', 'bM4Bis4Bo84B0s4B/s4BKs8BVc8BgM8Brc8B0s8B/c8BJ9AB', _FONT_PATH), '26': NativeFont('0123456789-°C?', 'J9ABbNABlNAB3tABLdEBdNEBw9EBEtIBUtIBodIB8NIBBdMBItMBfdMBwdMB', _FONT_PATH), '19': NativeFont('0123456789-?周一二三四五六日', 'wdMB9dMBGtQBS9QBf9QBsdQB5dQBGdUBR9UBe9UBr9UBwdUB8dUBStYBZNYBf9YBoNYB7NYBNtcBeNcBqtcB', _FONT_PATH)}
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
              "show_codex": True, "bluetooth_enabled": False}
    try:
        with open(path, "r") as settings_file:
            settings = json.load(settings_file)
        if settings.get("v") == 1 and isinstance(settings.get("flip"), bool):
            result["flip"] = settings["flip"]
        if isinstance(settings.get("show_codex"), bool):
            result["show_codex"] = settings["show_codex"]
        if isinstance(settings.get("bluetooth_enabled"), bool):
            result["bluetooth_enabled"] = settings["bluetooth_enabled"]
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
                         bluetooth_enabled=None):
    try:
        if show_codex is None or bluetooth_enabled is None:
            previous = load_device_settings(path)
            if show_codex is None:
                show_codex = previous["show_codex"]
            if bluetooth_enabled is None:
                bluetooth_enabled = previous["bluetooth_enabled"]
        settings = {"v": 1, "flip": bool(flip), "show_codex": bool(show_codex),
                    "bluetooth_enabled": bool(bluetooth_enabled),
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
            if (tap[0]-sync_circle[0])**2 + (tap[1]-sync_circle[1])**2 < sync_circle[2]**2:
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
        return all(key in packet for key in required)

    def poll(self, uart):
        latest = None
        data = uart.read()
        for packet in self.feed(data):
            latest = packet
            self.last_receive_ticks = ticks_ms()
        return latest

    def _reply(self, uart, kind, flip, page, brightness, info, seq=None):
        message = {"v": 1, "type": kind, "flip": flip, "page": page,
                   "brightness": brightness, "program_update": self.program_update,
                   "boot_id": self.boot_id,
                   "heartbeat": 1, "show_codex": self.show_codex,
                   "bluetooth_enabled": self.bluetooth_enabled,
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
                time.sleep_ms(1)
                continue
            updater.abort()
            last_frame = None
        if updater is not None and updater.reset_at is not None:
            if updater.restart_due():
                machine.reset()
            time.sleep_ms(1)
            continue
        if packet is not None:
            received_page = packet.get("page", page)
            if received_page not in ("dashboard", "settings", "info"):
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
        now_second = display.local_clock.now()
        if last_frame is None or now_second != last_frame:
            if page == "info":
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
