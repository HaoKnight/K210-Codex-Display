import unittest
from unittest.mock import patch, Mock
from test_device_info import ns
from bridge.serial_link import SerialLink
from bridge.app import Bridge

class TapSyncTests(unittest.TestCase):
    def setUp(self):
        self.now=0
        self.module=Mock(STATUS_PRESS=1,STATUS_MOVE=2)
        self.touch=ns['TouchInput'](self.module,None)
        p=patch.dict(ns,ticks_ms=lambda:self.now,ticks_diff=lambda a,b:(a-b+4096)%8192-4096)
        p.start();self.addCleanup(p.stop)
    def poll(self,dt,status,x=239,y=158,circle=(239,158,38),flip=False):
        self.now=(self.now+dt)%8192;self.module.read.return_value=(status,x,y)
        return self.touch.poll_tap(flip,circle)
    def test_press_syncs_once_until_release(self):
        self.assertEqual(self.poll(200,1),'sync')
        self.assertIsNone(self.poll(1000,2))
        self.assertIsNone(self.poll(1000,1))
        self.poll(1,0)
        self.assertEqual(self.poll(200,1),'sync')
    def test_only_inside_seven_day_circle(self):
        for x,y in ((81,158),(80,30),(277,158),(270,190)):
            self.poll(200,0)
            self.assertEqual(self.poll(200,1,x=x,y=y),(x,y))
        self.poll(200,0)
        self.assertEqual(self.poll(200,1,circle=None),(239,158))
    def test_flipped_coordinates(self):
        self.assertEqual(self.poll(200,1,x=80,y=81,flip=True),'sync')
    def test_host_deduplicates_heartbeat_retries(self):
        link=SerialLink('',115200)
        msg={'boot_id':1,'sync_request':1}
        link._accept_device_ui(msg);self.assertTrue(link.sync_requested)
        link.sync_requested=False;link._accept_device_ui(msg)
        self.assertFalse(link.sync_requested)
        link._accept_device_ui(dict(msg,sync_request=2));self.assertTrue(link.sync_requested)
    def test_worker_reuses_full_sync_and_releases_busy_flag(self):
        import threading
        b=Mock();b.lock=threading.Lock()
        Bridge.sync_from_touch(b);b.sync_now.assert_called_once()
        self.assertFalse(b.touch_sync_busy)
        b.sync_now.side_effect=RuntimeError('network failed')
        Bridge.sync_from_touch(b)
        self.assertFalse(b.last_sync['ok']);self.assertFalse(b.touch_sync_busy)
