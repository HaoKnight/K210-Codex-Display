"""Clock mode routing, persistent choice and serial synchronization."""
import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import patch
from test_device_info import ns
from bridge.app import Bridge, Canvas, load_config
from bridge.serial_link import SerialLink

class ClockModeTests(unittest.TestCase):
 def test_saved_choice_survives_brightness_and_flip_changes(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=str(Path(tmp)/'settings.json')
   self.assertTrue(ns['load_device_settings'](path)['show_codex'])
   self.assertTrue(ns['save_device_settings'](False,100,path,show_codex=True))
   ns['save_flip_setting'](True,path)
   ns['save_brightness_setting'](70,True,path)
   self.assertEqual(ns['load_device_settings'](path),dict(flip=True,brightness=70,show_codex=True))
   ns['save_device_settings'](True,70,path,show_codex=False)
   self.assertFalse(ns['load_device_settings'](path)['show_codex'])

 def test_old_settings_get_codex_default(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/'settings.json';p.write_text('{"v":1,"flip":true,"brightness":100}')
   self.assertEqual(ns['load_device_settings'](str(p)),dict(flip=True,brightness=100,show_codex=True))

 def test_protocol_reports_saved_mode(self):
  with patch.dict(ns,make_boot_id=lambda:1): r=ns['SerialStateReceiver']()
  class UART:
   def write(self,data):self.data=data
  uart=UART();r.show_codex=True;r.acknowledge(uart,1,False)
  link=SerialLink('',115200);link._accept_device_ui(json.loads(uart.data))
  self.assertTrue(link.ui_state()['show_codex'])
  self.assertTrue(link.info()['device_show_codex'])
  with self.assertRaises(ValueError):link.set_device_ui(show_codex='false')

 def test_clock_contains_no_quota_or_codex_and_seconds_advance(self):
  class Capture(ns['EmbeddedDisplay']):
   def text(self,x,y,value,*args,**kw):self.labels.append(str(value));return super().text(x,y,value,*args,**kw)
  p=dict(ts=1790056800,offset=480,city='天津',condition='晴',temp=28,weather_stale=False,show_codex=False)
  a=Canvas();d=Capture(a);d.labels=[];d.render(p)
  self.assertFalse(any('Codex' in label or '剩余' in label for label in d.labels))
  b=Canvas()
  e=ns['EmbeddedDisplay'](b);e.render(p,now=p['ts']+1)
  self.assertNotEqual(a.ops,b.ops)
  for text in ('2026-09-22','周二','天津','显示 Codex 额度','100%'):
   self.assertTrue(all(c in ns['SMOOTH_FONTS']['15'] for c in text),text)

 def test_flip_clock_large_glyphs_and_panel_geometry(self):
  for ch in '0123456789':
   self.assertIn(ch, ns['SMOOTH_FONTS']['124'])
   self.assertIn(ch, ns['SMOOTH_FONTS']['22'])
  p=dict(ts=1790056800,offset=480,city='天津',condition='晴',temp=-10,weather_stale=False,show_codex=False)
  c=Canvas();display=ns['EmbeddedDisplay'](c);display.render(p)
  for x in (16,168):
   self.assertIn(['rect',x,110,136,1,(0,0,0),True],c.ops)
  self.assertIn(['rect',0,0,320,240,(0,0,0),True],c.ops)
  self.assertLess(display.text_width('59',124/12),136)
  self.assertLessEqual(300-display.text_width('59',22/12),300)

 def test_preview_can_toggle_and_return_to_both_home_modes(self):
  with tempfile.TemporaryDirectory() as tmp:
   c=load_config();c.update(source='demo',serial_port='')
   b=Bridge(c,Path(tmp)/'config.json')
   self.assertTrue(b.link.device_show_codex)
   b.link.set_device_ui(show_codex=False)
   b.tick(send=False,poll=False)
   clock_frame=b.read()['frame']
   b.device_touch(295,24)
   self.assertEqual(b.link.device_page,'settings')
   b.device_touch(270,83)
   self.assertTrue(b.link.device_show_codex)
   b.device_touch(25,25)
   self.assertEqual(b.link.device_page,'dashboard')
   self.assertTrue(b.read()['packet']['show_codex'])
   self.assertNotEqual(clock_frame,b.read()['frame'])
   b.device_touch(270,25);b.device_touch(270,83);b.device_touch(25,25)
   self.assertFalse(b.read()['packet']['show_codex'])
   self.assertFalse(b.read()['errors'])

if __name__=='__main__':unittest.main()
