"""Incremental frames must match full frames, including digit erasure."""
import unittest
from PIL import Image, ImageDraw, ImageFilter
from test_device_info import ns

class Canvas:
    def __init__(self):
        self.image = Image.new('RGB', (320, 240))
        self.draw = ImageDraw.Draw(self.image)
        self.calls = 0
        self.filters = []
    def mean(self, size):
        self.filters.append(('mean', size))
        self.image = self.image.filter(ImageFilter.BoxBlur(size))
        self.draw = ImageDraw.Draw(self.image)
    def blend(self, color, alpha=128):
        self.filters.append(('blend', color, alpha))
        self.image = Image.blend(Image.new('RGB', self.image.size, color),
                                 self.image, alpha / 256)
        self.draw = ImageDraw.Draw(self.image)
    def draw_rectangle(self, x, y, w, h, color, fill=False):
        self.calls += 1
        self.draw.rectangle((x,y,x+w-1,y+h-1), fill=color if fill else None,
                            outline=None if fill else color)
    def draw_line(self, x, y, x2, y2, color, thickness=1):
        self.calls += 1
        self.draw.line((x,y,x2,y2), fill=color, width=thickness)
    def draw_circle(self, x, y, r, color, fill=False):
        self.calls += 1
        self.draw.ellipse((x-r,y-r,x+r,y+r), fill=color if fill else None,
                          outline=None if fill else color)

class RefreshTests(unittest.TestCase):
    def test_device_update_progress_uses_local_incremental_drawing_and_restores_home(self):
        canvas = Canvas(); display = ns['EmbeddedDisplay'](canvas)
        for char in '正在执行更新·请勿断电！':
            self.assertIn(char, ns['SMOOTH_FONTS']['20'])
        display.render_program_update(0)
        header = canvas.image.crop((0, 0, 320, 132)).tobytes()
        for percent in (1, 9, 37, 75, 99, 100):
            canvas.calls = 0
            display.render_program_update(percent)
            self.assertLess(canvas.calls, 250)
            self.assertEqual(canvas.image.crop((0, 0, 320, 132)).tobytes(), header)
            reference = Canvas(); ns['EmbeddedDisplay'](reference).render_program_update(percent, live=False)
            self.assertEqual(canvas.image.tobytes(), reference.image.tobytes())
            canvas.calls = 0; display.render_program_update(percent)
            self.assertEqual(canvas.calls, 0)
        packet = self.packet(True)
        display.render_live(packet, packet['ts'])
        reference = Canvas(); ns['EmbeddedDisplay'](reference).render(packet, packet['ts'])
        self.assertEqual(canvas.image.tobytes(), reference.image.tobytes())

    def test_update_frosts_the_current_screen_once_and_handles_missing_filters(self):
        from unittest.mock import patch
        canvas = Canvas(); display = ns['EmbeddedDisplay'](canvas)
        packet = self.packet(True)
        display.render_live(packet, packet['ts'])
        before = canvas.image.crop((0, 0, 320, 48)).tobytes()
        display.render_program_update(0)
        blurred = canvas.image.crop((0, 0, 320, 48)).tobytes()
        self.assertNotEqual(before, blurred)
        self.assertEqual(canvas.filters, [('mean', 5), ('mean', 5),
                                         ('blend', ns['BG'], 64)])
        for percent in (1, 47, 99, 100):
            display.render_program_update(percent)
            self.assertEqual(canvas.image.crop((0, 0, 320, 48)).tobytes(), blurred)
        self.assertEqual(len(canvas.filters), 3)
        display.render_live(packet, packet['ts'])
        reference = Canvas(); ns['EmbeddedDisplay'](reference).render(packet, packet['ts'])
        self.assertEqual(canvas.image.tobytes(), reference.image.tobytes())
        for error in (AttributeError('older firmware'), MemoryError('no filter buffer')):
            with patch.object(canvas, 'mean', side_effect=error):
                display.render_program_update(47)
                self.assertEqual(canvas.image.getpixel((0, 0)), ns['BG'])
            display.render_live(packet, packet['ts'])

    def test_preview_reloads_when_only_the_font_file_changes(self):
        from pathlib import Path
        import tempfile
        from bridge.live_renderer import LiveRenderer
        root = Path(__file__).resolve().parents[1] / 'K210'
        with tempfile.TemporaryDirectory() as tmp:
            preview = Path(tmp) / 'main.py'
            preview.write_bytes((root / 'main.py').read_bytes())
            font = Path(tmp) / 'font.bin'
            font.write_bytes((root / 'font.bin').read_bytes())
            renderer = LiveRenderer(preview)
            previous = renderer.display_type()
            self.assertIs(renderer.display_type(), previous)
            # Trailing padding preserves every indexed glyph but changes the asset.
            font.write_bytes(font.read_bytes() + b'\0')
            self.assertIsNot(renderer.display_type(), previous)
            self.assertIsNone(renderer.error)

    def test_native_fonts_load_only_requested_glyphs_and_limit_memory(self):
        import base64
        from pathlib import Path
        self.assertLess((Path(__file__).resolve().parents[1] / 'K210/main.py').stat().st_size,
                        112 * 1024)
        decoded_bytes = 0
        for bank in ns['SMOOTH_FONTS'].values():
            font = ns['NativeFont'](bank.characters, base64.b64encode(bank.offsets), bank.path,
                                    extra=bank.extra)
            self.assertFalse(font.cache)
            for char in font.characters + ''.join(font.extra):
                font[char]
                self.assertLessEqual(len(font.cache), 48)
            decoded_bytes += sum(len(glyph[2]) for glyph in font.cache.values())
            self.assertIsNone(font.get('🦄'))
        self.assertLess(decoded_bytes, 40000)

    def test_all_city_choices_use_tianjin_weather_font_in_both_layouts(self):
        from bridge.cities import city_catalog
        from pathlib import Path
        self.assertLessEqual((Path(__file__).resolve().parents[1] / 'K210/main.py').stat().st_size,
                             512 * 1024)
        for size in ('15', '24'):
            table = ns['SMOOTH_FONTS'][size]
            for province in city_catalog()['provinces']:
                for city in province['cities']:
                    for char in city['name']:
                        self.assertIn(char, table)
                        self.assertEqual(table[char][:2], table['天'][:2])
                        self.assertTrue(any(table[char][2]), char)
        class Capture(ns['EmbeddedDisplay']):
            def text(self, x, y, value, *args, **kwargs):
                if value == self.city:
                    self.city_scale = kwargs.get('scale', 1)
                return super().text(x, y, value, *args, **kwargs)
        for mode in (True, False):
            for city in ('天津', '济宁', '石家', '乌鲁', '攀枝', '鄂尔', '香港', '台北'):
                display = Capture(Canvas()); display.city = city
                display.render(dict(self.packet(mode), city=city))
                self.assertEqual(display.city_scale, 1.25 if mode else 2)

    def packet(self, mode):
        return dict(ts=1790056800, offset=480, city='天津', condition='晴',
                    temp=28, weather_stale=False, show_codex=mode, q5=55,
                    q7=18, r5=1790056863, r7=None, stale=False,
                    status='IDLE', model='test')

    def test_long_model_scrolls_to_both_ends_without_overwriting_status(self):
        p = dict(self.packet(True), model='gpt-6.1-sol-extra-long', status='RUNNING')
        canvas = Canvas()
        display = ns['EmbeddedDisplay'](canvas)
        display.render_live(p, p['ts'])
        left = canvas.image.crop((0, 214, 214, 240)).tobytes()
        right = canvas.image.crop((312, 214, 320, 240)).tobytes()
        images = set()
        width = display.text_width(p['model'], 1.25)
        self.assertGreater(width, 98)
        # Includes both pauses, movement in each direction, and erasure on change.
        duration = (width + 24) * 2 // 8 + 2
        for seconds in range(duration):
            display.render_live(p, p['ts'] + seconds)
            if seconds % 5 == 0:
                reference = Canvas()
                ns['EmbeddedDisplay'](reference).render(p, p['ts'] + seconds)
                self.assertEqual(canvas.image.tobytes(), reference.image.tobytes())
            self.assertEqual(canvas.image.crop((0, 214, 214, 240)).tobytes(), left)
            self.assertEqual(canvas.image.crop((312, 214, 320, 240)).tobytes(), right)
            images.add(canvas.image.crop((214, 214, 312, 240)).tobytes())
        self.assertGreater(len(images), 4)
        for offset in (0, width - 98):
            reference = Canvas()
            reference.draw_rectangle(214, 214, 98, 26, ns['CARD'], fill=True)
            text = ns['EmbeddedDisplay'](reference)
            text.text(214 - offset, text.centered_text_y(p['model'], 227, 1.25),
                      p['model'], scale=1.25, max_width=width, clip=(214, 312))
            self.assertIn(reference.image.crop((214, 214, 312, 240)).tobytes(), images)
        display.render_live(dict(p, model='gpt-6-sol'), p['ts'] + duration)
        reference = Canvas()
        ns['EmbeddedDisplay'](reference).render(dict(p, model='gpt-6-sol'), p['ts'] + duration)
        self.assertEqual(canvas.image.tobytes(), reference.image.tobytes())

    def test_full_city_names_are_never_clipped_in_either_weather_layout(self):
        from bridge.app import Canvas as OpsCanvas
        from bridge.cities import city_catalog
        class Capture(ns['EmbeddedDisplay']):
            def text(self, x, y, value, *args, **kwargs):
                value = str(value)
                if value and value in self.city and (y < 42 if self.mode else y >= 184):
                    scale = kwargs.get('scale', 1)
                    width = self.text_width(value, scale)
                    self.labels.append(value)
                    self.bounds.append((x, y, width, kwargs.get('max_width', 320), scale))
        for mode in (True, False):
            display = Capture(OpsCanvas()); display.mode = mode
            for province in city_catalog()['provinces']:
                for city in province['cities']:
                    name = city['name'].removesuffix('市')
                    display.city = name; display.labels = []; display.bounds = []
                    display.render(dict(self.packet(mode), city=name))
                    self.assertEqual(''.join(display.labels), name, (mode, name))
                    for x, y, width, limit, scale in display.bounds:
                        self.assertLessEqual(width, limit, name)
                        self.assertGreaterEqual(x, 226 if mode else 0, name)
                        self.assertLessEqual(x + width, 320, name)
                        self.assertLessEqual(y + (17 if scale == 1.25 else 27), 80 if mode else 240, name)

    def test_incremental_matches_full_for_every_second_and_rollover(self):
        for mode in (False, True):
            p=self.packet(mode)
            canvas=Canvas(); display=ns['EmbeddedDisplay'](canvas)
            for second in range(65):
                now=p['ts']+second
                display.render_live(p, now)
                expected=Canvas(); ns['EmbeddedDisplay'](expected).render(p, now)
                self.assertEqual(canvas.image.tobytes(), expected.image.tobytes(),
                                 (mode,second))

    def test_seconds_avoid_expensive_static_drawing(self):
        for mode in (False,True):
            p=self.packet(mode); c=Canvas(); d=ns['EmbeddedDisplay'](c)
            d.render_live(p,p['ts']); full=c.calls;c.calls=0
            d.render_live(dict(p,seq=2,time_sync=False),p['ts']+1)
            self.assertLess(c.calls,full//5)

    def test_changes_and_return_from_settings_rebuild_frame(self):
        for mode in (False,True):
            p=self.packet(mode); c=Canvas(); d=ns['EmbeddedDisplay'](c)
            d.render_live(p,p['ts'])
            for changes,online in (({'temp':9},True),({'q5':10},True),({},False),
                                   ({'show_codex':not mode},True)):
                p.update(changes)
                d.render_live(p,p['ts']+1,online=online)
                expected=Canvas();ns['EmbeddedDisplay'](expected).render(p,p['ts']+1,online)
                self.assertEqual(c.image.tobytes(),expected.image.tobytes())
            d.render_settings(False,70)
            d.set_brightness(70)
            d.render_live(p,p['ts']+1,force=True)
            expected=Canvas(); ref=ns['EmbeddedDisplay'](expected);ref.set_brightness(70)
            ref.render(p,p['ts']+1)
            self.assertEqual(c.image.tobytes(),expected.image.tobytes())

    def test_local_changes_never_clear_whole_page(self):
        from bridge.app import Canvas as OpsCanvas
        for mode in (False, True):
            p=self.packet(mode); c=OpsCanvas();d=ns['EmbeddedDisplay'](c)
            d.render_live(p,p['ts'])
            for dt,changes,online in ((60,{},True),(61,{'temp':-8},True),
                    (62,{'condition':'多云','weather_stale':True},True),
                    (63,{'q5':9,'status':'RUNNING'},True),(64,{},False)):
                c.ops=[];p.update(changes);d.render_live(p,p['ts']+dt,online)
                self.assertFalse(any(op[:5]==['rect',0,0,320,240] for op in c.ops))

    def test_settings_and_info_incremental_match_full(self):
        c=Canvas();d=ns['EmbeddedDisplay'](c)
        d.render_settings(False,100,live=True)
        c.calls=0;d.render_settings(False,100,live=True)
        self.assertEqual(c.calls,0)
        for flip,codex,bluetooth in ((False,False,False),(True,False,True),
                                     (True,True,True),(False,True,False)):
            d.render_settings(flip,100,show_codex=codex,
                              bluetooth_enabled=bluetooth,live=True)
            ref=Canvas();ns['EmbeddedDisplay'](ref).render_settings(
                flip,100,show_codex=codex,bluetooth_enabled=bluetooth)
            self.assertEqual(c.image.tobytes(),ref.image.tobytes())
        info=dict(version='v1.2.0',uptime_ms=99000,rx_age_ms=99000,
                  boot_ts=1790056800,updated_at=1790056800)
        for age,online in ((99000,True),(100000,True),(0,True),(1000,False)):
            info.update(uptime_ms=age,total_elapsed_ms=86400000+age,rx_age_ms=age)
            d.render_info(info,online=online,live=True)
            ref=Canvas();ns['EmbeddedDisplay'](ref).render_info(info,online=online)
            self.assertEqual(c.image.tobytes(),ref.image.tobytes())
        d.render_settings(False,100,live=True)
        ref=Canvas();ns['EmbeddedDisplay'](ref).render_settings(False,100)
        self.assertEqual(c.image.tobytes(),ref.image.tobytes())

    def test_settings_indicator_follows_link_not_touch_readiness(self):
        c=Canvas();d=ns['EmbeddedDisplay'](c)
        d.render_settings(False,live=True,online=True)
        for online in (False,True):
            c.calls=0
            d.render_settings(False,touch_ready=not online,live=True,online=online)
            self.assertEqual(c.image.getpixel((313,7)),ns['GREEN'] if online else ns['RED'])
            self.assertEqual(c.calls,1)
            ref=Canvas();ns['EmbeddedDisplay'](ref).render_settings(False,online=online)
            self.assertEqual(c.image.tobytes(),ref.image.tobytes())

if __name__=='__main__': unittest.main()
