"""The selected transport uses the correct pins, baud and reply path."""
import json
import sys
import tempfile
import types
import unittest
from unittest.mock import Mock, patch

from test_device_info import ns


class FakeUART:
    def __init__(self):
        self.incoming = []
        self.writes = []

    def read(self):
        return self.incoming.pop(0) if self.incoming else None

    def write(self, data):
        self.writes.append(data)
        return len(data)

    def queue(self, packet, split_at=None):
        data = (json.dumps(packet) + '\n').encode()
        if split_at is None:
            self.incoming.append(data)
        else:
            self.incoming.extend((data[:split_at], data[split_at:]))


def state(seq):
    return dict(v=1, type='state', seq=seq, ts=1790035800, offset=480,
                city='天津', condition='晴', status='RUNNING', model='test',
                stale=False, weather_stale=False, demo=False,
                q5=50, q7=80, r5=None, r7=None)


class DualSerialTests(unittest.TestCase):
    def test_switching_transport_discards_incomplete_packet(self):
        usb, bluetooth = FakeUART(), FakeUART()
        receiver = ns['SerialStateReceiver']()
        usb.queue(state(1), split_at=17)
        self.assertIsNone(receiver.poll(usb))
        receiver.buffer = b''
        bluetooth.queue(state(2))
        self.assertEqual(receiver.poll(bluetooth)['seq'], 2)

    def test_replies_go_to_selected_uart(self):
        usb, bluetooth = FakeUART(), FakeUART()
        receiver = ns['SerialStateReceiver']()
        receiver.acknowledge(bluetooth, 7, False)
        self.assertEqual(json.loads(bluetooth.writes[0])['seq'], 7)
        self.assertEqual(usb.writes, [])

    def test_hc05_uses_io7_tx_io6_rx_at_data_mode_baud(self):
        fm = Mock()
        fm.fpioa.UART1_TX = 'uart1_tx'
        fm.fpioa.UART1_RX = 'uart1_rx'
        module = types.SimpleNamespace(fm=fm)
        uart_type = Mock(UART1=1)
        with patch.dict(sys.modules, fpioa_manager=module):
            ns['open_gpio_uart'](uart_type)
        self.assertEqual(fm.register.call_args_list[0].args, (7, 'uart1_tx'))
        self.assertEqual(fm.register.call_args_list[1].args, (6, 'uart1_rx'))
        self.assertEqual(uart_type.call_args.args, (1, 9600, 8, None, 1))
        self.assertEqual(uart_type.call_args.kwargs['timeout'], 0)

    def test_bluetooth_setting_survives_other_settings_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = directory + '/settings.json'
            self.assertFalse(ns['load_device_settings'](path)['bluetooth_enabled'])
            self.assertTrue(ns['save_device_settings'](
                False, 100, path, bluetooth_enabled=True))
            self.assertTrue(ns['save_brightness_setting'](70, path=path))
            self.assertTrue(ns['load_device_settings'](path)['bluetooth_enabled'])
            self.assertEqual(ns['load_device_settings'](path)['brightness'], 70)
            self.assertTrue(ns['save_device_settings'](
                False, 70, path, bluetooth_enabled=False))
            self.assertFalse(ns['load_device_settings'](path)['bluetooth_enabled'])


if __name__ == '__main__':
    unittest.main()
