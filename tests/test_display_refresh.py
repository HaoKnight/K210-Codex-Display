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
    def packet(self, mode):
        return dict(ts=1790056800, offset=480, city='天津', condition='晴',
                    temp=28, weather_stale=False, show_codex=mode, q5=55,
                    q7=18, r5=1790056863, r7=None, stale=False,
                    status='IDLE', model='test')

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
        info=dict(version='v1.1.0',uptime_ms=99000,rx_age_ms=99000,
                  boot_ts=1790056800,updated_at=1790056800)
        for age,online in ((99000,True),(100000,True),(0,True),(1000,False)):
            info.update(uptime_ms=age,rx_age_ms=age)
            d.render_info(info,online=online,live=True)
            ref=Canvas();ns['EmbeddedDisplay'](ref).render_info(info,online=online)
            self.assertEqual(c.image.tobytes(),ref.image.tobytes())
        d.render_settings(False,100,live=True)
        ref=Canvas();ns['EmbeddedDisplay'](ref).render_settings(False,100)
        self.assertEqual(c.image.tobytes(),ref.image.tobytes())

if __name__=='__main__': unittest.main()
