"""Device telemetry tests without hardware imports or flash writes."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bridge.app import Bridge, Canvas, load_config
from bridge.serial_link import SerialLink

ns = {'__name__': 'device_test'}
exec(compile((ROOT/'K210/main.py').read_bytes(), 'main.py', 'exec'), ns)


class Clock:
    host_synced = True
    def now(self): return 1790035800


class DeviceInfoTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = str(Path(self.tmp.name)/'info.json')
        self.now = 0
        self.patches = patch.dict(ns, ticks_ms=lambda: self.now,
                                 ticks_diff=lambda a,b: (a-b+512)%1024-512)
        self.patches.start()
        self.addCleanup(self.patches.stop)
        self.info = ns['DeviceInfo'](self.path)

    def test_tick_wrap_accumulates_without_resetting(self):
        for _ in range(30):
            self.now = (self.now+100)%1024
            self.info.advance()
        self.assertEqual(self.info.snapshot(Clock())['uptime_ms'], 3000)
        self.assertEqual(ns['format_uptime'](8285000), '2h 18m 05s')
        self.assertEqual(ns['format_uptime'](360000000), '100h 00m 00s')

    def test_receive_age_increases_without_packets(self):
        self.assertIsNone(self.info.snapshot(Clock())['rx_age_ms'])
        self.info.receive_state({'q5': 50})
        for _ in range(120):
            self.now = (self.now+100)%1024
            self.info.advance()
        self.assertEqual(self.info.snapshot(Clock())['rx_age_ms'], 12000)
        self.info.receive_state({'q5': 49})
        self.assertEqual(self.info.snapshot(Clock())['rx_age_ms'], 0)
        self.assertEqual(self.info.snapshot(Clock())['uptime_ms'], 12000)

    def test_clock_sync_does_not_reset_uptime(self):
        for _ in range(20):
            self.now=(self.now+100)%1024
            self.info.advance()
        clock=Clock()
        self.assertEqual(self.info.snapshot(clock)['boot_ts'],clock.now()-2)
        clock.host_synced=False
        self.assertIsNone(self.info.snapshot(clock)['boot_ts'])
        self.assertEqual(self.info.snapshot(clock)['uptime_ms'],2000)

    def test_update_timestamp_survives_restart(self):
        self.info.receive_state({'program_version':ns['PROGRAM_VERSION'],'program_updated_at':1790035800})
        second=ns['DeviceInfo'](self.path)
        self.assertEqual(second.updated_at,1790035800)
        with patch('builtins.open',side_effect=AssertionError('unnecessary flash write')):
            second.receive_state({'program_version':ns['PROGRAM_VERSION'],'program_updated_at':1790035800})

    def test_wrong_version_cannot_overwrite_timestamp(self):
        self.info.receive_state({'program_version':'different','program_updated_at':1790035800})
        self.assertIsNone(self.info.updated_at)
        self.assertFalse(Path(self.path).exists())

    def test_total_elapsed_survives_power_off_updates_and_clock_sync(self):
        clock=Clock();clock.host_synced=False
        self.assertIsNone(self.info.snapshot(clock)['total_elapsed_ms'])
        self.assertFalse(Path(self.path).exists())
        clock.host_synced=True
        self.assertEqual(self.info.snapshot(clock)['total_elapsed_ms'],0)
        started_at=clock.now()
        self.info.receive_state({'program_version':ns['PROGRAM_VERSION'],
                                 'program_updated_at':started_at+10})
        with patch.dict(ns,PROGRAM_VERSION='v1.2.1'):
            second=ns['DeviceInfo'](self.path)
            clock.host_synced=False
            self.assertIsNone(second.snapshot(clock)['total_elapsed_ms'])
            clock.host_synced=True;clock.now=lambda:started_at+86400
            with patch('builtins.open',side_effect=AssertionError('unnecessary flash write')):
                state=second.snapshot(clock)
            self.assertEqual(state['total_elapsed_ms'],86400000)
            self.assertEqual(state['uptime_ms'],0)
            self.assertIsNone(state['updated_at'])
        self.assertEqual(json.loads(Path(self.path).read_text())['started_at'],started_at)
        before=Path(self.path).read_bytes()
        with patch.object(ns['os'],'rename',side_effect=OSError('power lost')):
            self.info.receive_state({'program_version':ns['PROGRAM_VERSION'],
                                     'program_updated_at':started_at+20})
        self.assertEqual(Path(self.path).read_bytes(),before)
        self.assertEqual(self.info.updated_at,started_at+10)

    def test_native_fonts_cover_info_text(self):
        for size,chars in [('24','设备信息'),('15','累计程序版本启动时间本次运行最近通信秒前收到待同步0123456789vdhms :.-'),('12','最近更新待同步')]:
            for ch in chars:self.assertIn(ch, ns['SMOOTH_FONTS'][size])

    def test_offline_render_and_long_runtime_fit(self):
        class Capture(ns['EmbeddedDisplay']):
            def text(self,x,y,value,color=ns['WHITE'],scale=1,**kw):
                self.labels.append((str(value),color))
                self.assertion(x >= 0 and x+self.text_width(value,scale)<=320,(x,value))
        display=Capture(Canvas())
        display.labels=[];display.assertion=self.assertTrue
        display.render_info({'version':'v1.2.0','uptime_ms':360000000,
                             'total_elapsed_ms':86400000000,'rx_age_ms':15000}, online=False)
        self.assertNotIn(('连接',ns['MUTED']),display.labels)
        self.assertIn(('累计运行',ns['MUTED']),display.labels)
        self.assertLess(display.labels.index(('本次运行',ns['MUTED'])),
                        display.labels.index(('累计运行',ns['MUTED'])))
        self.assertIn(('24000h 00m 00s',ns['WHITE']),display.labels)
        self.assertIn(('15 秒前',ns['MUTED']),display.labels)

    def test_heartbeat_does_not_reset_data_age(self):
        self.info.receive_state({'type': 'heartbeat'})
        self.assertIsNone(self.info.snapshot(Clock())['rx_age_ms'])
        self.info.receive_state({'type': 'state', 'time_sync': True})
        for _ in range(200):
            self.now = (self.now + 100) % 1024
            self.info.advance()
            self.info.receive_state({'type': 'heartbeat'})
        self.assertEqual(self.info.snapshot(Clock())['rx_age_ms'], 20000)
        self.info.receive_state({'type': 'state', 'time_sync': True})
        self.assertEqual(self.info.snapshot(Clock())['rx_age_ms'], 0)

    def test_only_time_weather_and_quota_sync_reset_age(self):
        packet = dict(type='state', ts=100, seq=1, temp=26, condition='晴',
                      city='天津', q5=50, q7=60, r5=1000, r7=2000)
        self.info.receive_state(packet)
        for changes in ({'page':'settings'}, {'brightness':80}, {'show_codex':True},
                        {'status':'RUNNING'}, {'model':'new'}, {'ts':200,'seq':2}, {}):
            self.now += 100
            packet.update(changes)
            self.info.receive_state(packet)
        self.assertEqual(self.info.snapshot(Clock())['rx_age_ms'],700)
        for changes in ({'temp':27}, {'q5':49}, {'time_sync':True}):
            self.now += 100
            packet.update(changes)
            self.info.receive_state(packet)
            self.assertEqual(self.info.snapshot(Clock())['rx_age_ms'],0)

    def test_old_data_can_still_be_connected(self):
        class Capture(ns['EmbeddedDisplay']):
            def text(self, x, y, value, color=ns['WHITE'], scale=1, **kw):
                self.labels.append((str(value), color))
        display = Capture(Canvas())
        display.labels = []
        display.render_info({'rx_age_ms': 60000}, online=True)
        self.assertIn(['circle',296,26,3,ns['GREEN'],True], display.c.canvas.ops)
        self.assertFalse(any(value in ('已连接','未连接') for value,color in display.labels))
        self.assertIn(('60 秒前', ns['BLUE']), display.labels)

    def test_brightness_defaults_to_full_and_preserves_saved_choice(self):
        settings_path = str(Path(self.tmp.name) / 'settings.json')
        self.assertEqual(ns['load_device_settings'](settings_path)['brightness'], 100)
        self.assertEqual(SerialLink('', 115200).device_brightness, 100)
        ns['save_device_settings'](False, 70, settings_path)
        self.assertEqual(ns['load_device_settings'](settings_path)['brightness'], 70)

    def test_reply_contains_telemetry_and_fits_protocol(self):
        with patch.dict(ns,make_boot_id=lambda:42):receiver=ns['SerialStateReceiver']()
        class UART:
            def write(self,data):self.data=data
        uart=UART()
        receiver.acknowledge(uart,5,False,'info',80,self.info.snapshot(Clock()))
        message=json.loads(uart.data)
        self.assertEqual(message['page'],'info')
        self.assertEqual(message['device_info']['uptime_ms'],0)
        self.assertLess(len(uart.data),2048)


class HostInfoTests(unittest.TestCase):
    def test_host_extrapolates_reported_age_and_uptime(self):
        link=SerialLink('',115200)
        with patch('bridge.serial_link.time.monotonic',return_value=10):
            link._accept_device_ui({'boot_id':2,'device_info':{'version':'v1.2.0','uptime_ms':4000,
                                                             'total_elapsed_ms':86400000,'rx_age_ms':0}})
        with patch('bridge.serial_link.time.monotonic',return_value=12):
            info=link.info()['device_info']
        self.assertEqual(info['uptime_ms'],6000)
        self.assertEqual(info['total_elapsed_ms'],86402000)
        self.assertEqual(info['rx_age_ms'],2000)
        link._accept_device_ui({'boot_id':3})
        self.assertEqual(link.info()['device_info'],{})

    def test_info_navigation_and_timestamp_return(self):
        with tempfile.TemporaryDirectory() as d:
            config=load_config();config.update(source='demo',serial_port='')
            bridge=Bridge(config,Path(d)/'config.json')
            bridge.device_touch(270,25)
            self.assertEqual(bridge.link.device_page,'settings')
            bridge.device_touch(290,25)
            self.assertEqual(bridge.link.device_page,'info')
            bridge.device_touch(25,25)
            self.assertEqual(bridge.link.device_page,'settings')
            bridge.link.device_boot_id=2
            bridge.link.device_info={'version':'v1.2.0'}
            bridge.program_update={'status':'success','device_boot_id':2,'updated_at':1790035800}
            bridge.tick(send=False,poll=False)
            self.assertEqual(bridge.read()['packet']['program_updated_at'],1790035800)
            bridge.link.device_boot_id=3
            bridge.tick(send=False,poll=False)
            self.assertNotIn('program_updated_at',bridge.read()['packet'])


if __name__=='__main__':unittest.main()
