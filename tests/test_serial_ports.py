"""Port discovery and Bluetooth connection behavior."""
import sys
import types
import unittest
from unittest.mock import patch

from bridge import bluetooth_devices, serial_link


class SerialPortScanTests(unittest.TestCase):
    def test_ioreg_maps_bluetooth_serial_names_to_addresses(self):
        output = '''+-o IOBluetoothDevice  <class IOBluetoothDevice>
  |   "BD_ADDR" = <112233445566>
  |   "BTTTYName" = "OldHeadphones"
+-o IOBluetoothDevice  <class IOBluetoothDevice>
  |   "BD_ADDR" = <aabbccddeeff>
  |   "BTTTYName" = "MySerialModule"
'''
        self.assertEqual(serial_link._bluetooth_serial_addresses_from_ioreg(output),
                         {'/dev/cu.OldHeadphones': '11:22:33:44:55:66',
                          '/dev/cu.MySerialModule': 'aa:bb:cc:dd:ee:ff'})

    def test_scan_separates_usb_and_bluetooth_candidates(self):
        port = types.SimpleNamespace(device='/dev/cu.wchusbserial123',
                                     description='USB adapter', vid=1234)
        list_ports_module = types.ModuleType('serial.tools.list_ports')
        list_ports_module.comports = lambda: [port]
        tools_module = types.ModuleType('serial.tools')
        tools_module.list_ports = list_ports_module
        serial_module = types.ModuleType('serial')
        serial_module.tools = tools_module
        devices = [{'device': '/dev/cu.MySerialModule',
                    'address': 'aa:bb:cc:dd:ee:ff',
                    'name': 'MySerialModule', 'connected': True}]
        with patch.dict(sys.modules, {'serial': serial_module,
                                      'serial.tools': tools_module,
                                      'serial.tools.list_ports': list_ports_module}), \
             patch.object(bluetooth_devices, 'list_devices', return_value=devices):
            result = serial_link.list_ports()
        self.assertEqual([item['kind'] for item in result], ['usb', 'bluetooth'])
        self.assertEqual(result[1]['address'], 'aa:bb:cc:dd:ee:ff')
        self.assertTrue(result[1]['connected'])

    def test_saved_but_disconnected_bluetooth_devices_are_hidden(self):
        class DeviceClass:
            @staticmethod
            def deviceWithAddressString_(address):
                return types.SimpleNamespace(isConnected=lambda: address.endswith('ff'))

        addresses = {'/dev/cu.Headphones': '11:22:33:44:55:66',
                     '/dev/cu.SerialModule': 'aa:bb:cc:dd:ee:ff'}
        with patch.object(bluetooth_devices.sys, 'platform', 'darwin'), \
             patch.object(bluetooth_devices, '_device_class', return_value=DeviceClass), \
             patch.object(bluetooth_devices, '_serial_addresses', return_value=addresses):
            result = bluetooth_devices.list_devices()
        self.assertEqual([item['device'] for item in result], ['/dev/cu.SerialModule'])

    def test_bluetooth_address_resolves_to_the_same_port_after_disconnect(self):
        port = '/dev/cu.MySerialModule'
        with patch.object(bluetooth_devices, '_serial_addresses',
                          return_value={port: 'aa:bb:cc:dd:ee:ff'}):
            self.assertEqual(bluetooth_devices.serial_port_for_device('AA:BB:CC:DD:EE:FF'),
                             port)

    def test_invalid_bluetooth_address_is_rejected(self):
        with self.assertRaises(ValueError):
            bluetooth_devices.serial_port_for_device('/dev/cu.whatever')


if __name__ == '__main__':
    unittest.main()
