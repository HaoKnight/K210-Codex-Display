"""Check fragmented ACK delivery and channel cleanup across reconnections."""
import sys
import subprocess
import time
import unittest
from unittest.mock import patch

from bridge.bluetooth_serial import BluetoothSerial
from bridge.serial_link import SerialLink


FAKE_HELPER = '''
import base64,json,sys
def emit(message):
    print(json.dumps(message),flush=True)
emit({'type':'ready'})
for line in sys.stdin:
    message=json.loads(line)
    if message['op']=='close':
        emit({'type':'closed'})
        break
    packet=json.loads(base64.b64decode(message['data']))
    ack=json.dumps({'v':1,'type':'ack','seq':packet['seq'],'heartbeat':1}).encode()+b'\\n'
    for data in (ack[:10],ack[10:]):
        emit({'type':'data','data':base64.b64encode(data).decode()})
    emit({'type':'written','id':message['id'],'count':len(base64.b64decode(message['data']))})
'''


class BluetoothStreamTests(unittest.TestCase):
    def test_reconnect_gets_fresh_ack_and_releases_previous_helper(self):
        popen = subprocess.Popen
        processes = []

        def launch(_command, **kwargs):
            process = popen([sys.executable, '-u', '-c', FAKE_HELPER], **kwargs)
            processes.append(process)
            return process

        with patch('bridge.bluetooth_serial.subprocess.Popen', side_effect=launch):
            for seq in (1, 2):
                stream = BluetoothSerial('aa:bb:cc:dd:ee:ff', timeout=2)
                link = SerialLink('fake', 9600)
                link.conn = stream
                try:
                    link.exchange({'v': 1, 'type': 'state', 'seq': seq})
                    deadline = time.monotonic() + 2
                    while not link.info()['connected'] and time.monotonic() < deadline:
                        link.poll()
                        time.sleep(.01)
                    self.assertTrue(link.info()['connected'])
                    self.assertEqual(link.info()['ack_seq'], seq)
                finally:
                    link.close()
                self.assertEqual(stream.process.poll(), 0)
        self.assertEqual(len(processes), 2)

    def test_closed_native_channel_fails_write_instead_of_reporting_success(self):
        popen = subprocess.Popen
        script = "import json,sys;print(json.dumps({'type':'ready'}),flush=True);sys.stdin.readline();print(json.dumps({'type':'error','error':'channel lost'}),flush=True)"
        with patch('bridge.bluetooth_serial.subprocess.Popen',
                   side_effect=lambda _command, **kwargs: popen(
                       [sys.executable, '-u', '-c', script], **kwargs)):
            stream = BluetoothSerial('aa:bb:cc:dd:ee:ff', timeout=2)
            try:
                with self.assertRaisesRegex(ConnectionError, 'channel lost'):
                    stream.write(b'hello\n')
            finally:
                stream.close()


if __name__ == '__main__':
    unittest.main()
