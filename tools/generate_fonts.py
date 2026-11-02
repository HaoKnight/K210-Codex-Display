"""Generate native-size K210 glyphs directly from font outlines; never resize.

Run with Pillow on macOS. --check verifies every embedded glyph without writing.
The manifest records exact fonts, sizes, cell metrics and baseline offsets.
City glyphs use the same 15/24 px metrics as Tianjin in both weather layouts.
"""
import argparse
import base64
import json
import math
from pathlib import Path
import zlib
from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = Path(__file__).with_name('font_manifest.json')
FONT_DIR = Path('/System/Library/Fonts')
RESET_GLYPHS = {
    '12': '发卡重置确认待定时段公开放预告暂无有效消息仅布日期具体时间未等待源由提供预计截止大约距开始小时天分正在生已进入原帖和完整说明请查看网页来源当前模型检查尚陈旧仍按不代表本账户到账最近记录返回，',
    '15': '发卡重置确认待定时段状态公开放预告来源消息检查尚未abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ-._/',
    '18': '重置信息暂无有效消息卡即将发放已确认额度预计记录最近时间未确定日期截止大约状态由源提供等待公布仅，具体距开始小时分进入正在生0123456789/-',
    '20': '重置信息暂无有效消息卡即将发放已确认额度预计Codex 正在执行更新·请勿断电！',
}


def generate():
    manifest = json.loads(MANIFEST.read_text())
    catalog = json.loads((ROOT / 'bridge/web/china-cities.json').read_text())
    city_chars = set(''.join(city['name'] for province in catalog['provinces']
                            for city in province['cities']))
    for size in ('15', '24'):
        for char in sorted(city_chars):
            manifest[size][char] = manifest[size]['天']
    for size, characters in RESET_GLYPHS.items():
        template = (dict(manifest['18']['设'], width=20, height=22) if size == '20'
                    else manifest[size]['天' if size == '12' else '设'])
        for char in characters:
            spec = template
            if size in ('15', '20') and (char.isascii() or char == '·'):
                name = 'Arial.ttf' if size == '15' else 'Arial Bold.ttf'
                font = ImageFont.truetype(str(FONT_DIR / 'Supplemental' / name), int(size))
                spec = dict(font=name, offset_y=-1,
                            width=max(1, math.ceil(font.getlength(char))), height=int(size) + 2)
            manifest[size].setdefault(char, spec)
    tables = {}
    for size, entries in manifest.items():
        tables[size] = {}
        for char, spec in entries.items():
            path = FONT_DIR / spec['font']
            if not path.exists():
                path = FONT_DIR / 'Supplemental' / spec['font']
            font = ImageFont.truetype(str(path), int(size), index=0)
            width, height = spec['width'], spec['height']
            bitmap = Image.new('L', (width, height))
            ImageDraw.Draw(bitmap).text((0, spec['offset_y']), char,
                                       font=font, fill=255)
            packed = bytearray((width * height + 3) // 4)
            pixels = getattr(bitmap, 'get_flattened_data', bitmap.getdata)()
            for i, coverage in enumerate(pixels):
                packed[i // 4] |= min(3, (coverage + 42) // 85) << ((i % 4) * 2)
            tables[size][char] = (width, height, bytes(packed))
    return tables


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    tables = generate()
    manifest = json.loads(MANIFEST.read_text())
    catalog = json.loads((ROOT / 'bridge/web/china-cities.json').read_text())
    city_chars = set(''.join(city['name'] for province in catalog['provinces']
                            for city in province['cities']))
    banks, font_data = [], bytearray()
    for size, entries in tables.items():
        # New UI glyphs stay inline so existing devices need only main.py updated.
        inline = set(RESET_GLYPHS.get(size, '')) - set(manifest[size])
        if size == '15':
            # Existing city glyphs must keep their original font.bin offsets.
            inline.difference_update(city_chars)
            inline.add('次')
        extra = {char: base64.b64encode(zlib.compress(bytes(glyph[:2]) + glyph[2], 9)).decode()
                 for char, glyph in entries.items() if char in inline}
        stored = {char: glyph for char, glyph in entries.items() if char not in extra}
        offsets = bytearray()
        for width, height, pixels in stored.values():
            offsets.extend(len(font_data).to_bytes(3, 'little'))
            font_data.extend(zlib.compress(bytes((width, height)) + pixels, 9))
        offsets.extend(len(font_data).to_bytes(3, 'little'))
        banks.append(repr(size) + ': NativeFont(%r, %r, _FONT_PATH%s)' % (
            ''.join(stored), base64.b64encode(offsets).decode(),
            ', ' + repr(extra) if extra else ''))
    font_path = ROOT / 'K210/font.bin'
    if args.check:
        assert font_path.read_bytes() == font_data, font_path
    else:
        font_path.write_bytes(font_data)
    targets = [ROOT / 'K210/main.py', ROOT / 'K210/CanMV_Preview.py']
    for path in targets:
        source = path.read_text()
        if args.check:
            namespace = {'__name__': 'native_font_check'}
            exec(compile(source, str(path), 'exec'), namespace)
            for size, entries in tables.items():
                font = namespace['SMOOTH_FONTS'][size]
                for char, glyph in entries.items():
                    assert font[char] == glyph, (path, size, char)
        else:
            start = source.index('# BEGIN SMOOTH FONT DATA')
            end = source.index('# END SMOOTH FONT DATA') + len('# END SMOOTH FONT DATA')
            block = ('# BEGIN SMOOTH FONT DATA\n'
                     '# Generated by tools/generate_fonts.py directly at each target size.\n'
                     '# Four-level coverage; no bitmap resizing or supersampling.\n'
                     'SMOOTH_FONTS = {' + ', '.join(banks) + '}\n# END SMOOTH FONT DATA')
            updated = source[:start] + block + source[end:]
            compile(updated, str(path), 'exec')
            path.write_text(updated)
    assert targets[0].read_bytes() == targets[1].read_bytes()
    print(('Verified' if args.check else 'Generated'),
          sum(map(len, tables.values())), 'native glyphs at', ', '.join(tables), 'px')


if __name__ == '__main__':
    main()
