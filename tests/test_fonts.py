"""Font tool contracts: Photoshop's font list, font sources, text font reads and font assignment."""

import os
import sys
import tempfile
import unittest
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from owned_copy_fixtures import ContractBridge, IllustratorContractBridge, png_bytes

from cavi_adobe_mcp.illustrator.bridge import IllustratorAuthoring
from cavi_adobe_mcp.photoshop.bridge import PhotoshopAuthoring
from cavi_adobe_mcp.photoshop.fontlist import (
    classify_font_file,
    font_list_location,
    parse_font_list,
    read_font_list,
)

HOME = Path('/Users/test')
APPLICATION = '/Applications/Adobe Photoshop 2027/Adobe Photoshop 2027.app'
LIVETYPE = 'file:///Users/test/Library/Application%20Support/Adobe/CoreSync/plugins/livetype/.r/.4872.otf'


def record(name, file, family='Family', *extra):
    lines = ['%BeginFont', 'Handler:CoreTextHandler', 'FontType:Type1', 'FontName:' + name,
             'FamilyName:' + family, 'StyleName:Regular']
    if file is not None:
        lines.append('OutlineFileName:' + file)
    lines += ['DataFormat:sfntResource', *extra, 'NameArray:0,Mac,65535,' + name, '%EndFont', '']
    return '\n'.join(lines)


FONT_LIST = '\n'.join([
    '%!Adobe-FontList 1.25', '%Locale:0x409', '',
    record('AdobeClean-Bold', 'file:///Applications/Adobe%20Photoshop%202027/Adobe%20Photoshop%202027.app'
           '/Contents/Frameworks/dvaui.framework/Versions/A/Resources/otf/AdobeClean-Bold.otf'
           '#postscript-name=AdobeClean-Bold', 'Adobe Clean'),
    record('Osaka', 'file:///System/Library/AssetsV2/com_apple_MobileAsset_Font8/0b5b.asset/AssetData/Osaka.ttf'
           '#postscript-name=Osaka', 'Osaka'),
    record('SourceSans3-Regular', LIVETYPE + '#postscript-name=SourceSans3-Regular', 'Source Sans 3'),
    record('Zapfino', 'file:///Library/Fonts/Zapfino.ttf#postscript-name=Zapfino', 'Zapfino'),
    record('MyFont-Regular', 'file:///Users/test/Library/Fonts/My%20Font%C3%A9.otf', 'My Font'),
    '%BeginFont\nHandler:CoreTextHandler\nFamilyName:No Name\n%EndFont\n',
    record('NoFile-Regular', None, 'No File'),
    record('Osaka', 'file:///Library/Fonts/Duplicate.ttf', 'Osaka'),
    '%BeginFont\nFontName:Unterminated-Regular\nOutlineFileName:file:///Library/Fonts/Unterminated.ttf\n',
])


class FontListParserTests(unittest.TestCase):
    def test_records_map_postscript_names_to_decoded_files(self):
        fonts = parse_font_list(FONT_LIST)
        self.assertEqual(fonts['SourceSans3-Regular'],
                         '/Users/test/Library/Application Support/Adobe/CoreSync/plugins/livetype/.r/.4872.otf')
        self.assertEqual(fonts['Osaka'],
                         '/System/Library/AssetsV2/com_apple_MobileAsset_Font8/0b5b.asset/AssetData/Osaka.ttf')
        self.assertTrue(fonts['AdobeClean-Bold'].endswith('/otf/AdobeClean-Bold.otf'))
        self.assertEqual(fonts['MyFont-Regular'], '/Users/test/Library/Fonts/My Fonté.otf')
        self.assertEqual(fonts['Zapfino'], '/Library/Fonts/Zapfino.ttf')

    def test_incomplete_records_never_name_a_file(self):
        fonts = parse_font_list(FONT_LIST)
        self.assertIsNone(fonts['NoFile-Regular'])
        self.assertNotIn('Unterminated-Regular', fonts)
        self.assertEqual(len(fonts), 6)

    def test_unusable_file_urls_are_not_paths(self):
        for url in ('https://example.com/a.otf', 'file://host/a.otf', 'file:///a%00b.otf',
                    'file:///a%FFb.otf', 'file:///a%0Ab.otf'):
            with self.subTest(url=url):
                self.assertIsNone(parse_font_list('%!Adobe-FontList 1.25\n' + record('F', url))['F'])

    def test_oversized_or_foreign_input_is_refused(self):
        with self.assertRaises(ValueError):
            parse_font_list(FONT_LIST, limit=100)
        for text in ('', 'not a font list\n%BeginFont\nFontName:A\n%EndFont\n'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_font_list(text)
        with self.assertRaises(TypeError):
            parse_font_list(None)

    def test_the_first_record_of_a_name_wins(self):
        self.assertEqual(parse_font_list(FONT_LIST)['Osaka'].split('/')[1], 'System')


class FontSourceTests(unittest.TestCase):
    def source(self, path):
        return classify_font_file(path, home=HOME, application=APPLICATION)

    def test_each_location_has_one_source(self):
        fonts = parse_font_list(FONT_LIST)
        self.assertEqual({name: self.source(path) for name, path in fonts.items()}, {
            'AdobeClean-Bold': 'application', 'Osaka': 'system', 'SourceSans3-Regular': 'adobe_fonts',
            'Zapfino': 'local', 'MyFont-Regular': 'local', 'NoFile-Regular': 'unknown'})

    def test_both_livetype_folders_are_adobe_fonts(self):
        base = '/Users/test/Library/Application Support/Adobe/CoreSync/plugins/livetype/'
        self.assertEqual(self.source(base + '.w/1.otf'), 'adobe_fonts')
        self.assertEqual(self.source(base + '.r/1.otf'), 'adobe_fonts')
        self.assertEqual(self.source(base + 'other/1.otf'), 'unknown')
        self.assertEqual(self.source(base + '.rx/1.otf'), 'unknown')

    def test_lookalike_and_traversing_paths_are_unknown(self):
        base = '/Users/test/Library/Application Support/Adobe/CoreSync/plugins/livetype/.r/'
        for path in (None, '', 'relative/a.otf', '/System/Library/../../tmp/a.otf',
                     base + '../../a.otf', '/System/LibraryX/a.otf', '/Volumes/Disk/a.otf',
                     '/Users/other/Library/Fonts/a.otf', APPLICATION + 'X/a.otf'):
            with self.subTest(path=path):
                self.assertEqual(self.source(path), 'unknown')

    def test_application_bundle_follows_the_resolved_application(self):
        path = '/Applications/Other/Other.app/Contents/a.otf'
        self.assertEqual(classify_font_file(path, home=HOME, application='/Applications/Other/Other.app'),
                         'application')
        self.assertEqual(self.source(path), 'unknown')

    def test_font_list_belongs_to_the_resolved_application(self):
        expected = HOME / 'Library/Application Support/Adobe/Adobe Photoshop 2027/CT Font Cache/AdobeFnt_OSFonts.lst'
        self.assertEqual(font_list_location(APPLICATION, HOME), expected)
        self.assertEqual(font_list_location('/Applications/Adobe Photoshop 2026/Adobe Photoshop.app', HOME),
                         HOME / 'Library/Application Support/Adobe/Adobe Photoshop 2026/CT Font Cache/AdobeFnt_OSFonts.lst')
        self.assertIsNone(font_list_location('/fixture/app', HOME))


class FontListFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def test_absent_list_is_none(self):
        self.assertIsNone(read_font_list(self.root / 'missing.lst'))

    def test_list_is_read_with_its_modification_time(self):
        path = self.root / 'list.lst'
        path.write_text(FONT_LIST)
        os.utime(path, (0, 1_700_000_000))
        fonts, modified = read_font_list(path)
        self.assertEqual(modified, '2023-11-14T22:13:20Z')
        self.assertIn('Osaka', fonts)

    def test_symlinks_invalid_text_and_directories_are_refused(self):
        target = self.root / 'real.lst'
        target.write_text(FONT_LIST)
        link = self.root / 'link.lst'
        link.symlink_to(target)
        bad = self.root / 'bad.lst'
        bad.write_bytes(b'%!Adobe-FontList 1.25\n\xff\xfe\n')
        for path in (link, bad, self.root):
            with self.subTest(path=path.name), self.assertRaises(ValueError):
                read_font_list(path)


class FontBridge(ContractBridge):
    """Photoshop host whose application and home folder are fixtures."""
    home = HOME

    def status(self):
        return {**super().status(), 'application': APPLICATION}

    def _home(self):
        return self.home


NATIVE_FONTS = [
    {'name': 'Adobe Clean Bold', 'family': 'Adobe Clean', 'style': 'Bold', 'postscript_name': 'AdobeClean-Bold'},
    {'name': 'Source Sans 3', 'family': 'Source Sans 3', 'style': 'Regular', 'postscript_name': 'SourceSans3-Regular'},
    {'name': 'Osaka', 'family': 'Osaka', 'style': 'Regular', 'postscript_name': 'Osaka'},
    {'name': 'Mystery', 'family': 'Mystery', 'style': 'Regular', 'postscript_name': 'Mystery-Regular'},
]


class PhotoshopFontTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.home = self.root / 'home'
        self.bridge = FontBridge(self.root)
        self.bridge.home = self.home
        self.bridge.fonts_receipt = NATIVE_FONTS
        (self.root / 'source.png').write_bytes(png_bytes())
        self.listing = (self.home / 'Library/Application Support/Adobe/Adobe Photoshop 2027/CT Font Cache'
                        / 'AdobeFnt_OSFonts.lst')

    def write_list(self):
        self.listing.parent.mkdir(parents=True)
        self.listing.write_text(FONT_LIST.replace('/Users/test', str(self.home)))

    def test_fonts_carry_source_and_file_from_the_font_list(self):
        self.write_list()
        result = self.bridge.fonts()
        by_name = {font['postscript_name']: font for font in result['fonts']}
        self.assertEqual({name: font['source'] for name, font in by_name.items()}, {
            'AdobeClean-Bold': 'application', 'SourceSans3-Regular': 'adobe_fonts',
            'Osaka': 'system', 'Mystery-Regular': 'unknown'})
        self.assertTrue(by_name['SourceSans3-Regular']['file'].endswith('/livetype/.r/.4872.otf'))
        self.assertIsNone(by_name['Mystery-Regular']['file'])
        self.assertEqual(result['font_list']['path'], str(self.listing))
        self.assertRegex(result['font_list']['modified'], r'^\d{4}-\d\d-\d\dT[\d:.]+Z$')
        self.assertEqual(by_name['Osaka']['family'], 'Osaka')
        self.assertIn('runtime', result)

    def test_absent_font_list_reports_unknown_sources_and_no_list(self):
        result = self.bridge.fonts()
        self.assertIsNone(result['font_list'])
        self.assertEqual({font['source'] for font in result['fonts']}, {'unknown'})
        self.assertEqual({font['file'] for font in result['fonts']}, {None})

    def test_family_filter_is_exact_and_case_insensitive(self):
        self.write_list()
        self.assertEqual([font['postscript_name'] for font in self.bridge.fonts('source sans 3')['fonts']],
                         ['SourceSans3-Regular'])
        self.assertEqual(self.bridge.fonts('Source Sans')['fonts'], [])
        for family in ('', 3, 'a\nb', 'x' * 129):
            with self.subTest(family=family), self.assertRaises(ValueError):
                self.bridge.fonts(family)

    def test_oversized_font_list_is_refused(self):
        self.write_list()
        self.listing.write_text('%!Adobe-FontList 1.25\n' + 'x' * (64 * 1024 * 1024))
        with self.assertRaises(ValueError):
            self.bridge.fonts()

    def test_text_fonts_lists_runs_and_missing_fonts(self):
        session = self.bridge.open_copy('source.png', 'working.psd')['session_id']
        self.bridge.text_layers = [
            {'path': [0], 'name': 'Title', 'native_id': 7, 'runs': [
                {'from': 0, 'to': 3, 'postscript_name': 'Missing-Bold', 'available': False},
                {'from': 3, 'to': 9, 'postscript_name': 'Osaka', 'available': True}]},
            {'path': [1], 'name': 'Body', 'native_id': 8, 'runs': [
                {'from': 0, 'to': 4, 'postscript_name': 'Missing-Bold', 'available': False},
                {'from': 4, 'to': 8, 'postscript_name': 'Another-Light', 'available': False}]}]
        result = self.bridge.text_fonts(session)
        self.assertEqual(self.bridge.requests[-1]['action'], 'text_fonts')
        self.assertEqual(result['missing_fonts'], ['Another-Light', 'Missing-Bold'])
        self.assertEqual([layer['name'] for layer in result['layers']], ['Title', 'Body'])
        self.assertIsNone(result['pending_operation'])

    def test_text_fonts_refuses_an_unbounded_native_receipt(self):
        session = self.bridge.open_copy('source.png', 'working.psd')['session_id']
        run = {'from': 0, 'to': 1, 'postscript_name': 'Osaka', 'available': True}
        self.bridge.text_layers = [{'path': [0], 'name': 'Wide', 'native_id': 1, 'runs': [run] * 4097}]
        with self.assertRaises(ValueError):
            self.bridge.text_fonts(session)
        self.bridge.text_layers = [{'path': [0], 'name': 'L', 'native_id': 1, 'runs': []}] * 2049
        with self.assertRaises(ValueError):
            self.bridge.text_fonts(session)

    def test_text_fonts_refuses_a_bad_session_before_dispatch(self):
        count = len(self.bridge.requests)
        for session in ('nope', None, '0' * 31):
            with self.subTest(session=session), self.assertRaises(ValueError):
                self.bridge.text_fonts(session)
        self.assertEqual(len(self.bridge.requests), count)

    def test_set_text_font_sends_the_exact_target_and_returns_the_changed_layer(self):
        session = self.bridge.open_copy('source.png', 'working.psd')['session_id']
        self.bridge.set_text_font(session, [0, 2], 'Title', 'Osaka')
        request = self.bridge.requests[-1]
        self.assertEqual((request['action'], request['layer_path'], request['expected_name'], request['font']),
                         ('set_text_font', [0, 2], 'Title', 'Osaka'))

    def test_set_text_font_validates_before_dispatch(self):
        session = self.bridge.open_copy('source.png', 'working.psd')['session_id']
        count = len(self.bridge.requests)
        for path, name, font in (([], 'T', 'F'), ([True], 'T', 'F'), (None, 'T', 'F'), ([0], '', 'F'),
                                 ([0], 'T', ''), ([0], 'T', 'A\nB'), ([0], 'T', 'x' * 129), ([0], 'T', None)):
            with self.subTest(path=path, name=name, font=font), self.assertRaises(ValueError):
                self.bridge.set_text_font(session, path, name, font)
        with self.assertRaises(ValueError):
            self.bridge.set_text_font('nope', [0], 'T', 'F')
        self.assertEqual(len(self.bridge.requests), count)


class IllustratorFontTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.bridge = IllustratorContractBridge(self.root)
        (self.root / 'source.png').write_bytes(png_bytes())
        self.session = self.bridge.open_copy('source.png', 'working.ai')['session_id']

    def test_set_text_font_sends_the_exact_target(self):
        self.bridge.set_text_font(self.session, [1], 'Layer 2', 3, 'ArialMT')
        request = self.bridge.requests[-1]
        self.assertEqual((request['action'], request['layer_path'], request['expected_name'],
                          request['frame_index'], request['font']),
                         ('set_text_font', [1], 'Layer 2', 3, 'ArialMT'))

    def test_set_text_font_validates_before_dispatch(self):
        count = len(self.bridge.requests)
        for path, name, frame, font in (([], 'L', 0, 'F'), ([0], '', 0, 'F'), ([0], 'L', -1, 'F'),
                                        ([0], 'L', True, 'F'), ([0], 'L', 1.0, 'F'), ([0], 'L', 10001, 'F'),
                                        ([0], 'L', None, 'F'), ([0], 'L', 0, ''), ([0], 'L', 0, 'A\tB')):
            with self.subTest(path=path, name=name, frame=frame, font=font), self.assertRaises(ValueError):
                self.bridge.set_text_font(self.session, path, name, frame, font)
        with self.assertRaises(ValueError):
            self.bridge.set_text_font('nope', [0], 'L', 0, 'F')
        self.assertEqual(len(self.bridge.requests), count)

    def test_fonts_keep_the_native_receipt_without_a_source(self):
        self.bridge.fonts_receipt = [{'name': 'Arial', 'family': 'Arial', 'style': 'Regular', 'postscript_name': 'ArialMT'}]
        result = self.bridge.fonts()
        self.assertEqual(result['fonts'], self.bridge.fonts_receipt)
        self.assertNotIn('font_list', result)


class AssembledFontScriptTests(unittest.TestCase):
    def script(self, host):
        with tempfile.TemporaryDirectory() as folder:
            return host(Path(folder).resolve())._script({'action': 'inspect', 'operation_id': 'a' * 32})

    def test_photoshop_script_reads_runs_and_checks_every_run_after_assignment(self):
        script = self.script(PhotoshopAuthoring)
        for text in ('text_fonts: caviTextFonts', 'set_text_font: {', 'textStyleRange', 'fontPostScriptName',
                     'fontAvailable', 'caviRequireRunFont(item, r.font)', 'caviRequireRunFont(c.item, c.r.font)'):
            with self.subTest(text=text):
                self.assertIn(text, script)
        self.assertEqual(script.count('Exact PostScript font is unavailable; use fonts discovery'), 1)

    def test_illustrator_script_reads_the_font_back_after_assignment(self):
        script = self.script(IllustratorAuthoring)
        self.assertIn('set_text_font: {', script)
        self.assertEqual(script.count('attributes.textFont.name !== c.r.font'), 1)
        self.assertEqual(script.count('characterAttributes.textFont.name !== r.font'), 1)
        for text in ('textStyleRange', 'fontAvailable', 'text_fonts'):
            with self.subTest(text=text):
                self.assertNotIn(text, script)


class FontCatalogTests(unittest.IsolatedAsyncioTestCase):
    def session(self, module, workspace):
        parameters = StdioServerParameters(command=sys.executable, args=['-m', module],
                                           env={**os.environ, 'CAVI_ADOBE_WORKSPACE': str(workspace)})
        return stdio_client(parameters)

    async def test_photoshop_font_tools_in_the_catalog(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder) / 'workspace'
            workspace.mkdir()
            async with self.session('cavi_adobe_mcp.photoshop.server', workspace) as (reader, writer), \
                    ClientSession(reader, writer) as client:
                await client.initialize()
                tools = {tool.name: tool for tool in (await client.list_tools()).tools}
                self.assertEqual(len(tools), 21)
                fonts = tools['photoshop_fonts']
                self.assertTrue(fonts.annotations.read_only_hint)
                self.assertEqual(fonts.input_schema.get('required', []), [])
                self.assertEqual(set(fonts.input_schema['properties']), {'family'})
                self.assertIn('Creative Cloud', fonts.description)
                self.assertIn('adobe_fonts', fonts.description)
                properties = fonts.output_schema['properties']
                self.assertIn('font_list', properties)
                font = properties['fonts']['items']['properties']
                self.assertEqual(set(font['source']['enum']),
                                 {'adobe_fonts', 'system', 'local', 'application', 'unknown'})
                self.assertEqual(font['file']['type'], ['string', 'null'])
                self.assertEqual(set(properties['font_list']['properties']), {'path', 'modified'})
                run = tools['photoshop_text_fonts'].output_schema['properties']['layers']['items'][
                    'properties']['runs']['items']['properties']
                self.assertEqual(set(run), {'from', 'to', 'postscript_name', 'available'})
                text_fonts = tools['photoshop_text_fonts']
                self.assertEqual(text_fonts.input_schema['required'], ['session_id'])
                self.assertTrue(text_fonts.annotations.read_only_hint)
                self.assertFalse(text_fonts.annotations.open_world_hint)
                self.assertIn('missing_fonts', text_fonts.output_schema['properties'])
                setter = tools['photoshop_set_text_font']
                self.assertEqual(set(setter.input_schema['required']),
                                 {'session_id', 'layer_path', 'expected_name', 'font'})
                self.assertFalse(setter.annotations.read_only_hint)
                self.assertTrue(setter.annotations.destructive_hint)
                self.assertFalse(setter.annotations.open_world_hint)
                self.assertIn('PostScript', setter.description)
                for name, arguments, message in (
                        ('photoshop_text_fonts', {'session_id': 'nope'}, 'Invalid Adobe session id'),
                        ('photoshop_set_text_font', {'session_id': 'nope', 'layer_path': [0],
                                                     'expected_name': 'T', 'font': 'Osaka'},
                         'Invalid Adobe session id'),
                        ('photoshop_set_text_font', {'session_id': '0' * 32, 'layer_path': [0],
                                                     'expected_name': 'T', 'font': ''},
                         'printable characters'),
                        ('photoshop_set_text_font', {'session_id': '0' * 32, 'layer_path': [0],
                                                     'expected_name': 'T', 'font': 'A\nB'},
                         'printable characters'),
                        ('photoshop_set_text_font', {'session_id': '0' * 32, 'layer_path': [True],
                                                     'expected_name': 'T', 'font': 'Osaka'}, None),
                        ('photoshop_set_text_font', {'session_id': '0' * 32, 'layer_path': [],
                                                     'expected_name': 'T', 'font': 'Osaka'}, None),
                        ('photoshop_fonts', {'family': ''}, None)):
                    with self.subTest(tool=name, arguments=arguments):
                        rejected = await client.call_tool(name, arguments)
                        self.assertTrue(rejected.is_error)
                        self.assertEqual(rejected.structured_content['error']['outcome'], 'rejected')
                        if message:
                            self.assertIn(message, rejected.content[0].text)
                self.assertFalse((workspace / '.adobe-authoring').exists())

    async def test_illustrator_font_tools_in_the_catalog(self):
        with tempfile.TemporaryDirectory() as folder:
            workspace = Path(folder) / 'workspace'
            workspace.mkdir()
            async with self.session('cavi_adobe_mcp.illustrator.server', workspace) as (reader, writer), \
                    ClientSession(reader, writer) as client:
                await client.initialize()
                tools = {tool.name: tool for tool in (await client.list_tools()).tools}
                self.assertEqual(len(tools), 15)
                fonts = tools['illustrator_fonts']
                self.assertEqual(set(fonts.input_schema['properties']), set())
                self.assertEqual(set(fonts.output_schema['properties']['fonts']['items']['properties']),
                                 {'name', 'family', 'style', 'postscript_name'})
                self.assertNotIn('font_list', fonts.output_schema['properties'])
                setter = tools['illustrator_set_text_font']
                self.assertEqual(set(setter.input_schema['required']),
                                 {'session_id', 'layer_path', 'expected_name', 'frame_index', 'font'})
                self.assertEqual(setter.input_schema['properties']['frame_index']['minimum'], 0)
                self.assertFalse(setter.annotations.read_only_hint)
                self.assertTrue(setter.annotations.destructive_hint)
                self.assertFalse(setter.annotations.open_world_hint)
                self.assertIn('illustrator_fonts', setter.description)
                for arguments, message in (
                        ({'session_id': 'nope', 'layer_path': [0], 'expected_name': 'L', 'frame_index': 0,
                          'font': 'ArialMT'}, 'Invalid Adobe session id'),
                        ({'session_id': '0' * 32, 'layer_path': [0], 'expected_name': 'L', 'frame_index': 0,
                          'font': 'A\x01B'}, 'printable characters'),
                        ({'session_id': '0' * 32, 'layer_path': [0], 'expected_name': 'L', 'frame_index': -1,
                          'font': 'ArialMT'}, None),
                        ({'session_id': '0' * 32, 'layer_path': [0], 'expected_name': 'L', 'frame_index': True,
                          'font': 'ArialMT'}, None),
                        ({'session_id': '0' * 32, 'layer_path': [], 'expected_name': 'L', 'frame_index': 0,
                          'font': 'ArialMT'}, None)):
                    with self.subTest(arguments=arguments):
                        rejected = await client.call_tool('illustrator_set_text_font', arguments)
                        self.assertTrue(rejected.is_error)
                        self.assertEqual(rejected.structured_content['error']['outcome'], 'rejected')
                        if message:
                            self.assertIn(message, rejected.content[0].text)
                self.assertFalse((workspace / '.adobe-authoring').exists())


if __name__ == '__main__':
    unittest.main()
