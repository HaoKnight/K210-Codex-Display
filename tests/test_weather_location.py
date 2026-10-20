import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from bridge.app import Bridge, load_config

class WeatherLocationTests(unittest.TestCase):
    def test_device_city_removes_only_the_city_suffix(self):
        for full, sent in (('天津市', '天津'), ('济宁市', '济宁'),
                           ('石家庄市', '石家庄'), ('石家庄', '石家庄'),
                           ('乌鲁木齐市', '乌鲁木齐'), ('北京', '北京'), ('未知城市', '未知城市')):
            with self.subTest(city=full), tempfile.TemporaryDirectory() as tmp:
                c=load_config();c.update(source='demo',serial_port='')
                c['weather']['city']=full
                b=Bridge(c,Path(tmp)/'config.json')
                with patch.object(b.link,'exchange',side_effect=lambda packet,*args:packet) as exchange:
                    b.tick(send=True,poll=False)
                self.assertEqual(exchange.call_args.args[0]['city'],sent)
                self.assertEqual(b.read()['packet']['city'],sent)
                self.assertEqual(b.read()['weather_city'],full)
                self.assertEqual(b.config_view()['weather']['city'],full)

    def test_manual_english_search_persists_chinese_city(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=load_config();c.update(source='demo',serial_port='')
            c['weather']['auto_location']=False
            b=Bridge(c,Path(tmp)/'config.json')
            with patch('bridge.sources.fetch_json', return_value={'results':[{
                    'name':'济宁','latitude':35.4,'longitude':116.6}]}):
                b.save_config({'weather':{'auto_location':False,'city':'Jining'}})
            self.assertEqual(b.config_view()['weather']['city'],'济宁')
            self.assertEqual(load_config(b.config_path)['weather']['city'],'济宁')
            self.assertEqual(b.read()['packet']['city'],'济宁')

    def test_legacy_english_config_never_appears_on_screen(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=load_config();c.update(source='demo',serial_port='')
            c['weather']['city']='Beijing'
            b=Bridge(c,Path(tmp)/'config.json')
            self.assertEqual(b.config_view()['weather']['city'],'未知城市')
            self.assertEqual(b.read()['packet']['city'],'未知城市')

    def test_auto_ignores_manual_city_and_manual_geocodes(self):
        with tempfile.TemporaryDirectory() as tmp:
            c=load_config();c.update(source='demo',serial_port='')
            b=Bridge(c,Path(tmp)/'config.json')
            with patch('bridge.app.geocode_city',return_value={'city':'天津','latitude':39,'longitude':117}) as geo:
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
                self.assertIn('自动定位失败', b.refresh_weather())
                self.assertEqual(b.weather['city'],'天津')
            with patch('bridge.app.locate_city',return_value={'city':'天津','latitude':39,'longitude':117}),patch('bridge.app.weather_fetch',side_effect=TimeoutError()):
                self.assertIn('天气查询失败：请求超时', b.refresh_weather())
                self.assertEqual(b.weather['city'],'天津')
