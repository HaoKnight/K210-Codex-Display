"""Incremental frames must match full frames, including digit erasure."""
import unittest
from PIL import Image, ImageDraw
from test_device_info import ns

class Canvas:
    def __init__(self):
        self.image = Image.new('RGB', (320, 240))
        self.draw = ImageDraw.Draw(self.image)
        self.calls = 0
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
                        100 * 1024)
        decoded_bytes = 0
        for bank in ns['SMOOTH_FONTS'].values():
            font = ns['NativeFont'](bank.characters, base64.b64encode(bank.offsets), bank.path)
            self.assertFalse(font.cache)
            for char in font.characters:
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
