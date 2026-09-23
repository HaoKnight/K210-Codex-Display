import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from bridge.app import Bridge, load_config

class WeatherLocationTests(unittest.TestCase):
    def test_auto_ignores_manual_city_and_manual_geocodes(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=load_config();c.update(source='demo',serial_port='')
            b=Bridge(c,Path(tmp)/'config.json')
            with patch('bridge.app.geocode_city',return_value=(39,117)) as geo:
                b.save_config({'weather':{'auto_location':True,'city':'无效输入','enabled':False}})
                self.assertTrue(b.config['weather']['enabled'])
                self.assertNotEqual(b.config['weather']['city'],'无效输入')
                geo.assert_not_called()
                b.save_config({'weather':{'auto_location':False,'city':'天津'}})
                geo.assert_called_once_with('天津')
    def test_auto_weather_uses_location_and_keeps_previous_on_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=load_config();c.update(source='logs',serial_port='')
            c['weather']['auto_location']=True
            b=Bridge(c,Path(tmp)/'config.json')
            with patch('bridge.app.locate_city',return_value={'city':'天津','latitude':39,'longitude':117}),patch('bridge.app.weather_fetch',return_value={'temp':22,'condition':'晴','at':1}) as fetch:
                self.assertIsNone(b.refresh_weather())
                self.assertEqual(fetch.call_args.args[0]['latitude'],39)
                self.assertEqual(b.weather['city'],'天津')
            with patch('bridge.app.locate_city',side_effect=ValueError('failed')):
                self.assertIsNotNone(b.refresh_weather())
                self.assertEqual(b.weather['city'],'天津')
