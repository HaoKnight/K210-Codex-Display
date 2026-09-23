"""Deterministic communication timing and protocol compatibility checks."""
import json
import unittest
from unittest.mock import patch
from test_bridge import FakeSerial
from test_device_info import ns
from bridge.serial_link import SerialLink


class HeartbeatTests(unittest.TestCase):
    def setUp(self):
        self.now = 100.0
        timer = patch('bridge.serial_link.time.monotonic', side_effect=lambda: self.now)
        timer.start()
        self.addCleanup(timer.stop)
        self.link = SerialLink('fake', 115200)
        self.link.conn = FakeSerial()
        self.seq = 0

    def send(self, **changes):
        self.seq += 1
        p = dict(v=1, type='state', seq=self.seq, ts=int(self.now), q5=88, page='dashboard')
        p.update(changes)
        return self.link.exchange(p)

    def ack(self, **changes):
        p = dict(v=1, type='ack', seq=self.seq, heartbeat=1)
        p.update(changes)
        self.link._accept_standard_response(p)

    def test_idle_heartbeat_and_preview_keeps_full_state(self):
        self.send(); self.ack()
        for _ in range(9):
            self.now += .5
            self.send()
        self.assertEqual(len(self.link.conn.writes), 1)
        self.now += .5
        preview = self.send()
        self.assertEqual(self.link.conn.writes[-1], dict(v=1, type='heartbeat', seq=self.seq))
        self.assertEqual(preview['q5'], 88)
        self.assertEqual(preview['type'], 'state')

    def test_change_immediate_and_defers_heartbeat(self):
        self.send(); self.ack()
        self.now += 1
        self.send(q5=19); self.ack()
        self.assertEqual(len(self.link.conn.writes), 2)
        self.assertEqual(self.link.conn.writes[-1]['q5'], 19)
        self.now += 4.9
        self.send(q5=19)
        self.assertEqual(len(self.link.conn.writes), 2)

    def test_unacknowledged_state_retries_without_flood(self):
        self.send()
        self.now += .5
        self.send()
        self.assertEqual(len(self.link.conn.writes), 1)
        self.now += 4.5
        self.send()
        self.assertEqual(self.link.conn.writes[-1]['type'], 'state')
        self.assertTrue(self.link.conn.writes[-1]['time_sync'])
        self.ack()
        self.now += 5
        self.send()
        self.assertEqual(self.link.conn.writes[-1]['type'], 'heartbeat')

    def test_unknown_duplicate_ack_and_ui_cannot_keep_host_online(self):
        self.send(); self.ack()
        self.now += 14.9
        self.assertTrue(self.link.info()['connected'])
        self.link._accept_standard_response(dict(v=1,type='ack',seq=999))
        self.ack()
        self.link._accept_standard_response(dict(v=1,type='ui',page='settings'))
        self.now += .1
        self.assertFalse(self.link.info()['connected'])

    def test_ui_echo_and_boot_force_full_sync(self):
        self.send(); self.ack()
        self.link._accept_standard_response(dict(v=1,type='ui',page='dashboard',heartbeat=1))
        # A real event includes flip and telemetry, even when value is unchanged.
        self.link._accept_standard_response(dict(v=1,type='ui',flip=False,heartbeat=1))
        self.send()
        self.assertEqual(len(self.link.conn.writes), 2)
        self.ack(boot_id=42)
        self.send()
        self.assertEqual(self.link.conn.writes[-1]['type'], 'state')
        self.assertTrue(self.link.conn.writes[-1]['time_sync'])

    def test_old_firmware_uses_full_state_keepalive(self):
        self.send(); self.ack(heartbeat=0)
        self.now += 5
        self.send()
        self.assertEqual(self.link.conn.writes[-1]['type'], 'state')

    def test_manual_time_sync_is_immediate(self):
        self.send(); self.ack()
        self.link.request_time_sync()
        self.send()
        self.assertEqual(len(self.link.conn.writes), 2)
        self.assertTrue(self.link.conn.writes[-1]['time_sync'])

    def test_device_accepts_heartbeat_and_times_out(self):
        with patch.dict(ns, make_boot_id=lambda: 1, ticks_ms=lambda: int(self.now*1000),
                        ticks_diff=lambda a,b: a-b):
            receiver = ns['SerialStateReceiver']()
            class UART:
                def read(_): return b'{"v":1,"type":"heartbeat","seq":3}\n'
            self.assertEqual(receiver.poll(UART())['seq'], 3)
            self.now += 14.9
            self.assertTrue(receiver.online())
            self.now += .1
            self.assertFalse(receiver.online())
            for seq in (True,-1,'3',None):
                self.assertFalse(receiver.valid(dict(v=1,type='heartbeat',seq=seq)))


if __name__ == '__main__': unittest.main()
