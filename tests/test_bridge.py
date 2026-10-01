"""Regression checks use temporary configuration and fake serial; no device writes."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from http.server import ThreadingHTTPServer
from urllib.request import urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from bridge.app import Bridge, handler_for, load_config
from bridge.serial_link import SerialLink
from bridge.sources import LogObserver, quota_error_message
from bridge.live_renderer import LiveRenderer


class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        config = load_config()
        config.update(source='demo', serial_port='')
        # These configuration tests exercise manually selected cities.
        config['weather']['auto_location'] = False
        self.bridge = Bridge(config, Path(self.tmp.name) / 'config.json')

    def pending_update(self):
        self.bridge._set_program_update('restarting', 100, 'waiting',
            confirm_after=time.time()-1, confirm_deadline=time.monotonic()+45,
            previous_boot_id=42)

    def test_unchanged_city_saves_offline(self):
        with patch('bridge.app.geocode_city', side_effect=OSError('offline')) as geo:
            self.bridge.save_config({'utc_offset_minutes': 60,
                'weather': {'enabled': True, 'city': self.bridge.config['weather']['city']}})
        geo.assert_not_called()
        self.assertEqual(json.loads(self.bridge.config_path.read_text())['utc_offset_minutes'], 60)

    def test_changed_city_failure_keeps_configuration(self):
        before = self.bridge.config_view()
        with patch('bridge.app.geocode_city', side_effect=OSError('offline')):
            with self.assertRaises(OSError):
                self.bridge.save_config({'weather': {'city': '杭州'}})
        self.assertEqual(self.bridge.config_view(), before)
        self.assertFalse(self.bridge.config_path.exists())

    def test_changed_city_geocodes_once(self):
        with patch('bridge.app.geocode_city', return_value=(30.27, 120.15)) as geo:
            self.bridge.save_config({'weather': {'city': '杭州'}})
        geo.assert_called_once_with('杭州')
        self.assertEqual(self.bridge.config['weather']['latitude'], 30.27)

    def test_invalid_city_never_calls_network(self):
        with patch('bridge.app.geocode_city') as geo:
            with self.assertRaises(ValueError):
                self.bridge.save_config({'weather': {'city': ''}})
        geo.assert_not_called()

    def test_update_requires_changed_boot(self):
        self.pending_update()
        info = {'connected': True, 'ack_at': time.time(), 'device_boot_id': 42}
        self.assertFalse(self.bridge._confirm_program_update(info))
        self.assertEqual(self.bridge.read()['program_update']['status'], 'restarting')
        info['device_boot_id'] = 43
        self.assertTrue(self.bridge._confirm_program_update(info))
        self.assertEqual(self.bridge.read()['program_update']['status'], 'success')

    def test_missing_boot_cannot_confirm(self):
        self.pending_update()
        self.assertFalse(self.bridge._confirm_program_update({'connected': True, 'ack_at': time.time()}))

    def test_update_timeout_and_retry(self):
        self.pending_update()
        self.bridge.program_update['confirm_deadline'] = time.monotonic()-1
        self.bridge._confirm_program_update({'connected': False})
        self.assertEqual(self.bridge.read()['program_update']['status'], 'failed')
        with patch.object(self.bridge, '_update_device_program', return_value={'ok': True}):
            self.assertEqual(self.bridge.update_device_program(), {'ok': True})

    def test_duplicate_update_keeps_pending_result(self):
        self.pending_update()
        with self.assertRaises(RuntimeError):
            self.bridge.update_device_program()
        self.assertEqual(self.bridge.read()['program_update']['status'], 'restarting')

    def test_frame_version_omits_unchanged_frame_and_isolates_state(self):
        first = self.bridge.read()
        cached = self.bridge.read(frame_version=first['frame_version'])
        self.assertNotIn('frame', cached)
        cached['packet']['q5'] = -1
        self.assertNotEqual(self.bridge.read()['packet']['q5'], -1)
        self.assertLess(len(json.dumps(cached)), len(json.dumps(first))/20)
        self.assertNotIn('frame', self.bridge.read(include_frame=False))

    def test_frame_invalidates_on_page_change(self):
        before = self.bridge.read()['frame_version']
        self.bridge.link.set_device_ui(page='settings')
        self.bridge.tick(send=False, poll=False)
        self.assertIn('frame', self.bridge.read(frame_version=before))

    def test_http_frame_protocol_and_legacy_client(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(self.bridge))
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            url = 'http://127.0.0.1:%d/api/state' % server.server_port
            with urlopen(url) as r: full = json.load(r)
            with urlopen(url+'?frame_version='+full['frame_version']) as r: small = json.load(r)
            self.assertIn('frame', full)
            self.assertNotIn('frame', small)
        finally:
            server.shutdown()
            server.server_close()
            worker.join()

    def test_sync_partial_failure_reported(self):
        self.bridge.config['source'] = 'hybrid'
        with patch.object(self.bridge, 'refresh_quota', return_value='额度模拟失败'), \
             patch.object(self.bridge, 'refresh_weather', return_value=None), \
             patch.object(self.bridge.observer, 'poll'):
            state = self.bridge.sync_now()
        self.assertFalse(state['sync']['ok'])
        self.assertEqual(state['sync']['results']['quota']['status'], 'failed')
        self.assertEqual(state['sync']['results']['weather']['status'], 'ok')
        self.assertEqual(state['sync']['results']['clock']['status'], 'skipped')

    def test_sync_demo_is_skipped(self):
        state = self.bridge.sync_now()
        self.assertTrue(state['sync']['ok'])
        self.assertTrue(all(r['status']=='skipped' for r in state['sync']['results'].values()))

    def test_bluetooth_reconnect_same_port_releases_old_stream_first(self):
        port = '/dev/cu.SerialModule'
        self.bridge.config.update(serial_port=port, baudrate=9600)
        old = SerialLink(port, 9600)
        self.bridge.link = old
        old.conn = FakeSerial()

        def connect(address):
            self.assertEqual(address, 'aa:bb:cc:dd:ee:ff')
            self.assertIsNone(old.conn)
            self.assertEqual(self.bridge.config['serial_port'], '')
            return port

        with patch('bridge.bluetooth_devices.serial_port_for_device', side_effect=connect), \
             patch.object(self.bridge, 'reconnect_serial',
                          return_value={'serial': {'error': None}}) as handshake:
            self.assertEqual(self.bridge.connect_bluetooth('aa:bb:cc:dd:ee:ff'), port)
        self.assertIsNot(self.bridge.link, old)
        self.assertEqual(self.bridge.config['baudrate'], 9600)
        handshake.assert_called_once_with()

    def test_invalid_bluetooth_address_does_not_close_current_stream(self):
        old = self.bridge.link
        with self.assertRaises(ValueError):
            self.bridge.connect_bluetooth('invalid')
        self.assertIs(self.bridge.link, old)


class FakeSerial:
    def __init__(self, messages=()):
        self.messages = list(messages)
        self.writes = []
    def read(self, _):
        return (json.dumps(self.messages.pop(0)).encode()+b'\n') if self.messages else b''
    def write(self, data):
        self.writes.append(json.loads(data))
        return len(data)
    def close(self):
        pass


class SerialTests(unittest.TestCase):
    def test_clock_retries_until_acknowledged(self):
        link = SerialLink('fake', 115200)
        link.conn = FakeSerial()
        link.last_time_sync = time.monotonic()
        link.request_time_sync()
        with patch('bridge.serial_link.time.monotonic', return_value=100):
            link.exchange({'seq': 1})
        with patch('bridge.serial_link.time.monotonic', return_value=105):
            link.exchange({'seq': 2})
            self.assertEqual(len(link.conn.writes), 2)
            self.assertTrue(all(p['time_sync'] for p in link.conn.writes))
            link._accept_standard_response({'v': 1, 'type': 'ack', 'seq': 2})
        with patch('bridge.serial_link.time.monotonic', return_value=110):
            link.exchange({'seq': 3})
        self.assertFalse(link.conn.writes[-1]['time_sync'])
        self.assertIsNotNone(link.last_time_sync_at)

    def test_chunk_error_without_next_is_not_hidden(self):
        link = SerialLink('fake', 115200)
        link.conn = FakeSerial([{'v': 1, 'type': 'update_ack', 'stage': 'chunk',
                                 'ok': False, 'error': 'no space'}])
        with self.assertRaisesRegex(RuntimeError, 'no space'):
            link._wait_update_ack('chunk', expected_next=2, timeout=.05)

    def test_end_error_is_not_hidden(self):
        link = SerialLink('fake', 115200)
        link.conn = FakeSerial([{'v': 1, 'type': 'update_ack', 'stage': 'end',
                                 'ok': False, 'error': 'checksum mismatch'}])
        with self.assertRaisesRegex(RuntimeError, 'checksum mismatch'):
            link._wait_update_ack('complete', timeout=.05)

    def test_reconnect_resets_ack_sequence(self):
        link = SerialLink('fake', 115200)
        link.last_ack_seq = 20
        link.close()
        link.sent[1] = (time.monotonic(), False)
        link._accept_standard_response({'v': 1, 'type': 'ack', 'seq': 1})
        self.assertEqual(link.last_ack_seq, 1)
        self.assertTrue(link.info()['connected'])


class SourceTests(unittest.TestCase):
    def test_running_task_wins_over_recent_completion(self):
        observer = LogObserver('/unused')
        now = time.time()
        observer.activity = {'running': {'at': now-5, 'status': 'RUNNING', 'model': 'a'},
                             'done': {'at': now, 'status': 'DONE', 'model': 'b'}}
        state = observer.current_activity(300)
        self.assertEqual((state['status'], state['running_count'], state['model']), ('RUNNING', 1, 'a'))
        observer.activity['running']['at'] = now-301
        self.assertEqual(observer.current_activity(300)['status'], 'DONE')

    def test_error_message_does_not_expose_raw_details(self):
        self.assertNotIn('SECRET', quota_error_message(RuntimeError('SECRET')))
        self.assertIn('超时', quota_error_message(TimeoutError()))

    def test_renderer_retains_last_valid_version(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'renderer.py'
            path.write_text('class EmbeddedDisplay: pass')
            renderer = LiveRenderer(path)
            valid = renderer.display_type()
            with patch.object(Path, 'read_bytes', side_effect=AssertionError('cache miss')):
                self.assertIs(renderer.display_type(), valid)
            path.write_text('syntax error !')
            self.assertIs(renderer.display_type(), valid)
            self.assertIsNotNone(renderer.error)
            path.write_text('class EmbeddedDisplay: pass\n')
            self.assertIsNotNone(renderer.display_type())
            self.assertIsNone(renderer.error)


if __name__ == '__main__':
    unittest.main()
